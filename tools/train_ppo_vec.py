# -*- coding: utf-8 -*-
"""向量化 PPO（多环境并行 + GPU 批量推理），用于长训练。

单个 env 只有 30Hz 状态，PPO 主要瓶颈在环境步进；并行 N 个 BotDuelEnv 后，
把 N 条观测拼成 batch 送 GPU 前向，GPU 才真正有收益。

用法：
    python tools/train_ppo_vec.py --envs 24 --steps 512 --updates 40 --device cuda
"""
import argparse
import os
import sys
import time

import torch
import torch.nn.functional as F

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from game.env import TeamDuelEnv, ACTION_KEYS, ACTION_SIZES  # noqa: E402
from game.policy import PolicyNet  # noqa: E402
from tools.eval_utils import evaluate  # noqa: E402

GAMMA = 0.99
LAM = 0.95
CLIP = 0.2
ENT_COEF = 0.01
VF_COEF = 0.5


def load_model(path, device):
    ck = torch.load(path, map_location="cpu", weights_only=False)
    m = PolicyNet(obs_dim=ck.get("config", {}).get("obs_dim", 41),
                  hidden=ck.get("config", {}).get("hidden", 128))
    m.load_state_dict(ck["state_dict"])
    return m.to(device)


def rollout(model, envs, steps, device, obs_list):
    """返回 transitions 与 GAE 所需数据（按 env 分别计算）。"""
    n = len(envs)
    data = [[] for _ in range(n)]
    with torch.no_grad():
        for _ in range(steps):
            x = torch.tensor(obs_list, dtype=torch.float32, device=device)
            acts_t, logp_t, _, val_t = model.act_batch(x)
            acts_np = [a.tolist() for a in acts_t]
            logp_np = logp_t.tolist()
            val_np = val_t.tolist()
            for i, env in enumerate(envs):
                a = [acts_np[h][i] for h in range(len(ACTION_KEYS))]
                old_obs = obs_list[i]
                nobs, r, done, info = env.step(dict(zip(ACTION_KEYS, a)))
                data[i].append((old_obs, a, logp_np[i], val_np[i], float(r), bool(done)))
                if done:
                    obs_list[i] = env.reset()
                else:
                    obs_list[i] = nobs
    # 引导值：未结束的 env 用当前 obs 的 value
    with torch.no_grad():
        x = torch.tensor(obs_list, dtype=torch.float32, device=device)
        _, _, _, boot_vals = model.act_batch(x)
        boot = boot_vals.tolist()
    obs_b, act_b, logp_b, val_b, rew_b, done_b, adv_b, ret_b = [], [], [], [], [], [], [], []
    for i in range(n):
        seq = data[i]
        vals = [t[3] for t in seq]
        adv = [0.0] * len(seq)
        last = 0.0
        for j in reversed(range(len(seq))):
            r, d = seq[j][4], seq[j][5]
            if d:
                delta = r - vals[j]
                last = delta
            else:
                nxt = vals[j + 1] if j + 1 < len(seq) else boot[i]
                delta = r + GAMMA * nxt - vals[j]
                last = delta + GAMMA * LAM * last
            adv[j] = last
        for j, t in enumerate(seq):
            obs_b.append(t[0]); act_b.append(t[1]); logp_b.append(t[2])
            val_b.append(t[3]); rew_b.append(t[4]); done_b.append(t[5])
            adv_b.append(adv[j]); ret_b.append(adv[j] + vals[j])
    return (torch.tensor(obs_b, dtype=torch.float32),
            torch.tensor(act_b, dtype=torch.long),
            torch.tensor(logp_b, dtype=torch.float32),
            torch.tensor(adv_b, dtype=torch.float32),
            torch.tensor(ret_b, dtype=torch.float32))


def update(model, opt, obs, acts, old_logp, adv, returns, device,
           epochs=4, batch=1024):
    n = len(obs)
    adv = (adv - adv.mean()) / (adv.std() + 1e-8)
    total = 0.0
    nb = 0
    for _ in range(epochs):
        perm = torch.randperm(n)
        for i in range(0, n, batch):
            idx = perm[i:i + batch]
            xb = obs[idx].to(device, non_blocking=True)
            ab = [acts[idx, h].to(device, non_blocking=True) for h in range(acts.shape[1])]
            old = old_logp[idx].to(device, non_blocking=True)
            a = adv[idx].to(device, non_blocking=True)
            ret = returns[idx].to(device, non_blocking=True)
            logp, ent, value, _ = model.evaluate_actions(xb, ab)
            ratio = torch.exp(logp - old)
            surr1 = ratio * a
            surr2 = torch.clamp(ratio, 1 - CLIP, 1 + CLIP) * a
            loss = -torch.min(surr1, surr2).mean() + VF_COEF * F.mse_loss(value, ret) \
                - ENT_COEF * ent.mean()
            opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 0.5)
            opt.step()
            total += float(loss.item())
            nb += 1
    return total / max(1, nb)


def eval_cpu(model, episodes, difficulty, seed, train_device):
    """单步推理在 GPU 上反而慢（每步同步），评测放 CPU，训练留在 GPU。"""
    was_cuda = next(model.parameters()).is_cuda
    if was_cuda:
        model.to("cpu")
    m = evaluate(model, episodes=episodes, difficulty=difficulty, seed=seed,
                 device="cpu")
    if was_cuda:
        model.to(train_device)
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--init", default=os.path.join(ROOT, "models", "ppo.pt"))
    ap.add_argument("--envs", type=int, default=24)
    ap.add_argument("--steps", type=int, default=512)
    ap.add_argument("--updates", type=int, default=40)
    ap.add_argument("--epochs", type=int, default=4)
    ap.add_argument("--batch", type=int, default=1024)
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--difficulty", default="normal")
    ap.add_argument("--device", default="auto")
    ap.add_argument("--eval-every", type=int, default=10)
    ap.add_argument("--eval-episodes", type=int, default=10)
    ap.add_argument("--out", default=os.path.join(ROOT, "models", "ppo_long.pt"))
    args = ap.parse_args()
    device = ("cuda" if torch.cuda.is_available() else "cpu") \
        if args.device == "auto" else args.device
    print("device:", device, "envs:", args.envs, "updates:", args.updates)
    torch.manual_seed(0)
    model = load_model(args.init, device)
    opt = torch.optim.Adam(model.parameters(), lr=args.lr)
    envs = [TeamDuelEnv(difficulty=args.difficulty, max_steps=900, seed=100 + i)
            for i in range(args.envs)]
    obs_list = [env.reset() for env in envs]
    best = eval_cpu(model, args.eval_episodes, args.difficulty, 2026, device)
    best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
    print("init winrate %.2f K/D %.2f/%.2f" %
          (best["winrate"], best["kills"], best["deaths"]))
    log_path = os.path.join(ROOT, "logs", "train_ppo_vec.csv")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("update,steps,loss,winrate,kills,deaths\n")
    total_steps = 0
    for u in range(1, args.updates + 1):
        t0 = time.time()
        obs, acts, old_logp, adv, returns = rollout(
            model, envs, args.steps, device, obs_list)
        total_steps += len(obs)
        loss = update(model, opt, obs, acts, old_logp, adv, returns, device,
                      epochs=args.epochs, batch=args.batch)
        if u % args.eval_every == 0 or u == args.updates:
            m = eval_cpu(model, args.eval_episodes, args.difficulty, 2026, device)
            print("update %d  steps %d  loss %.3f  winrate %.2f  K/D %.2f/%.2f  (%.0fs)" %
                  (u, total_steps, loss, m["winrate"], m["kills"], m["deaths"],
                   time.time() - t0))
            with open(log_path, "a", encoding="utf-8") as f:
                f.write("%d,%d,%.4f,%.2f,%.2f,%.2f\n" %
                        (u, total_steps, loss, m["winrate"], m["kills"], m["deaths"]))
            if m["winrate"] > best["winrate"] or (m["winrate"] == best["winrate"] and
                                                  m["kills"] > best["kills"]):
                best = m
                best_state = {k: v.detach().cpu().clone()
                              for k, v in model.state_dict().items()}
        else:
            print("update %d  steps %d  loss %.3f  (%.0fs)" %
                  (u, total_steps, loss, time.time() - t0))
    for env in envs:
        env.close()
    model.load_state_dict(best_state)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    torch.save({"state_dict": {k: v.cpu() for k, v in model.state_dict().items()},
                "config": {"obs_dim": model.obs_dim, "hidden": model.hidden},
                "metrics": best}, args.out)
    print("best winrate %.2f, saved %s" % (best["winrate"], args.out))


if __name__ == "__main__":
    main()
