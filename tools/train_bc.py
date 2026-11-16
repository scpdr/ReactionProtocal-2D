# -*- coding: utf-8 -*-
"""行为克隆：用规则教师采集演示，训练 PolicyNet。

用法：
    python tools/train_bc.py --steps 30000 --epochs 12 --out models/bc.pt
"""
import argparse
import os
import random
import sys
import time

import torch
import torch.nn.functional as F

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from game.env import TeamDuelEnv, WEAPON_IDS, ACTION_SIZES  # noqa: E402
from game.policy import PolicyNet  # noqa: E402
from tools.teacher import teacher_action  # noqa: E402
from tools.eval_utils import evaluate  # noqa: E402

ACTION_KEYS = ["move", "aim", "fire", "reload", "switch", "indicator"]


def canonical(act, p):
    """teacher 的 switch=None 表示保持当前武器；BC 标签转成当前槽位。"""
    sw = act.get("switch")
    if sw is None:
        sw = WEAPON_IDS.index(p["weapon"]) if p["weapon"] in WEAPON_IDS else 0
    return [int(act["move"]), int(act["aim"]), int(act["fire"]),
            int(act["reload"]), int(sw), int(act["indicator"])]


def collect(steps=30000, seed=0, epsilon=0.05, verbose=True, difficulty="normal",
            linear=False):
    """采集“教师 vs FSM 机器人”的演示（与评测分布一致）。"""
    env = TeamDuelEnv(difficulty=difficulty, max_steps=900, seed=seed, linear=linear)
    rng = random.Random(seed)
    obs_buf, act_buf = [], []
    ep = 0
    t0 = time.time()
    while len(obs_buf) < steps:
        obs = env.reset()
        ep += 1
        done = False
        while not done:
            ta = teacher_action(env.game, env.agent_id, rng)
            if rng.random() < epsilon:       # 少量探索
                ta = {k: rng.choice(range(n)) if k != "switch"
                      else rng.choice([None] + list(range(8)))
                      for k, n in zip(ACTION_KEYS, ACTION_SIZES)}
            obs_buf.append(list(obs))
            act_buf.append(canonical(ta, env.game.players[env.agent_id]))
            obs, r, done, info = env.step(ta)
    env.close()
    if verbose:
        print("collected %d steps, %d episodes, %.1fs" %
              (len(obs_buf), ep, time.time() - t0))
    return (torch.tensor(obs_buf, dtype=torch.float32),
            torch.tensor(act_buf, dtype=torch.long))


def train_bc(obs, acts, epochs=12, batch=512, lr=1e-3, seed=0, verbose=True,
             weighted=False, device="cpu"):
    torch.manual_seed(seed)
    # 救援样本极稀疏（aim>=3 表示瞄队友）：过采样，避免 BC 完全学不会救人
    support = acts[:, 1] >= 3
    n_sup = int(support.sum())
    if n_sup > 0:
        rep = 20
        obs = torch.cat([obs, obs[support].repeat(rep, 1)], 0)
        acts = torch.cat([acts, acts[support].repeat(rep, 1)], 0)
        if verbose:
            print("rescue samples %d -> %d" % (n_sup, (rep + 1) * n_sup))
    n = len(obs)
    idx = torch.randperm(n)
    n_val = max(1, int(n * 0.05))
    val_idx, tr_idx = idx[:n_val], idx[n_val:]
    model = PolicyNet().to(device)
    opt = torch.optim.Adam(model.parameters(), lr=lr)
    # fire/reload/indicator 类别极不平衡：给少数类加权，避免模型“永远不补刀/不开火”
    weights = []
    for h, n_cls in enumerate(ACTION_SIZES):
        if weighted and h in (2, 3, 5):
            cnt = torch.bincount(acts[:, h], minlength=n_cls).float()
            cw = len(acts) / (n_cls * torch.clamp(cnt, min=1.0))
            weights.append(torch.clamp(cw, max=15.0).to(device))
        else:
            weights.append(None)
    best_val = 1e9
    best_state = None
    for ep in range(1, epochs + 1):
        model.train()
        perm = tr_idx[torch.randperm(len(tr_idx))]
        total, nb = 0.0, 0
        for i in range(0, len(perm), batch):
            b = perm[i:i + batch]
            xb, ab = obs[b].to(device), acts[b].to(device)
            logits, _ = model(xb)
            loss = 0.0
            for h, lg in enumerate(logits):
                loss = loss + F.cross_entropy(lg, ab[:, h], weight=weights[h])
            opt.zero_grad()
            loss.backward()
            opt.step()
            total += float(loss.item())
            nb += 1
        model.eval()
        with torch.no_grad():
            logits, _ = model(obs[val_idx].to(device))
            vloss = sum(float(F.cross_entropy(lg, acts[val_idx, h].to(device)).item())
                        for h, lg in enumerate(logits))
        if verbose:
            accs = []
            with torch.no_grad():
                for h, lg in enumerate(logits):
                    accs.append(float((lg.argmax(-1) ==
                                       acts[val_idx, h].to(device)).float().mean()))
            print("epoch %2d  train_loss %.3f  val_loss %.3f  acc %s" %
                  (ep, total / max(1, nb), vloss,
                   " ".join("%.2f" % a for a in accs)))
        if vloss < best_val:
            best_val = vloss
            best_state = {k: v.detach().cpu().clone()
                          for k, v in model.state_dict().items()}
    if best_state is not None:
        model.load_state_dict(best_state)
    return model


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--steps", type=int, default=30000)
    ap.add_argument("--epochs", type=int, default=12)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--device", default="auto")
    ap.add_argument("--linear", action="store_true", help="消融：线性血条")
    ap.add_argument("--out", default=os.path.join(ROOT, "models", "bc.pt"))
    args = ap.parse_args()
    device = ("cuda" if torch.cuda.is_available() else "cpu") \
        if args.device == "auto" else args.device
    print("device:", device)
    obs, acts = collect(steps=args.steps, seed=args.seed, linear=args.linear)
    model = train_bc(obs, acts, epochs=args.epochs, seed=args.seed, device=device)
    m = evaluate(model, episodes=20, difficulty="normal", seed=123, device=device,
                 linear=args.linear)
    print("BC vs FSM: winrate=%.2f kills=%.2f deaths=%.2f" %
          (m["winrate"], m["kills"], m["deaths"]))
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    torch.save({"state_dict": {k: v.cpu() for k, v in model.state_dict().items()},
                "config": {"obs_dim": model.obs_dim, "hidden": model.hidden},
                "metrics": m}, args.out)
    print("saved", args.out)


if __name__ == "__main__":
    main()
