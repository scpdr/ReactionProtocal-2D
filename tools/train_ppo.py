# -*- coding: utf-8 -*-
"""短 PPO：从 BC/DAgger 权重出发，在 1v1 对 FSM 机器人上做小幅策略改进。

用法：
    python tools/train_ppo.py --init models/dagger.pt --updates 3 --steps 16384
"""
import argparse
import os
import sys
import time

import torch
import torch.nn.functional as F

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from game.env import BotDuelEnv, ACTION_KEYS, ACTION_SIZES  # noqa: E402
from game.policy import PolicyNet  # noqa: E402
from tools.eval_utils import evaluate  # noqa: E402

GAMMA = 0.99
LAM = 0.95
CLIP = 0.2
ENT_COEF = 0.01
VF_COEF = 0.5


def load_model(path, device="cpu"):
    ck = torch.load(path, map_location=device, weights_only=False)
    m = PolicyNet(obs_dim=ck.get("config", {}).get("obs_dim", 41),
                  hidden=ck.get("config", {}).get("hidden", 128))
    m.load_state_dict(ck["state_dict"])
    return m


def collect(model, env, steps, seed=0):
    obs_buf, act_buf, logp_buf, val_buf, rew_buf, done_buf = [], [], [], [], [], []
    obs = env.reset()
    ep_rew = 0.0
    with torch.no_grad():
        while len(obs_buf) < steps:
            acts, logp, _, value = model.act(obs, deterministic=False)
            obs_buf.append(obs)
            act_buf.append(acts)
            logp_buf.append(logp)
            val_buf.append(value)
            nobs, r, done, info = env.step(dict(zip(ACTION_KEYS, acts)))
            rew_buf.append(r)
            done_buf.append(done)
            ep_rew += r
            obs = nobs
            if done:
                obs = env.reset()
    return (torch.tensor(obs_buf, dtype=torch.float32),
            torch.tensor(act_buf, dtype=torch.long),
            torch.tensor(logp_buf, dtype=torch.float32),
            torch.tensor(val_buf, dtype=torch.float32),
            torch.tensor(rew_buf, dtype=torch.float32),
            torch.tensor(done_buf, dtype=torch.float32))


def gae(rewards, values, dones, gamma=GAMMA, lam=LAM):
    n = len(rewards)
    adv = torch.zeros(n)
    last = 0.0
    for i in reversed(range(n)):
        if dones[i]:
            next_v = 0.0
            delta = rewards[i] - values[i]
            last = delta
        else:
            next_v = values[i + 1] if i + 1 < n else 0.0
            delta = rewards[i] + gamma * next_v - values[i]
            last = delta + gamma * lam * last
        adv[i] = last
    returns = adv + values
    return adv, returns


def update(model, opt, obs, acts, old_logp, adv, returns, epochs=6, batch=256):
    n = len(obs)
    adv = (adv - adv.mean()) / (adv.std() + 1e-8)
    total_loss = 0.0
    nb = 0
    for _ in range(epochs):
        perm = torch.randperm(n)
        for i in range(0, n, batch):
            idx = perm[i:i + batch]
            a_b = [acts[idx, h] for h in range(acts.shape[1])]
            logp, ent, value, _ = model.evaluate_actions(obs[idx], a_b)
            ratio = torch.exp(logp - old_logp[idx])
            surr1 = ratio * adv[idx]
            surr2 = torch.clamp(ratio, 1 - CLIP, 1 + CLIP) * adv[idx]
            policy_loss = -torch.min(surr1, surr2).mean()
            value_loss = F.mse_loss(value, returns[idx])
            loss = policy_loss + VF_COEF * value_loss - ENT_COEF * ent.mean()
            opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 0.5)
            opt.step()
            total_loss += float(loss.item())
            nb += 1
    return total_loss / max(1, nb)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--init", default=os.path.join(ROOT, "models", "dagger.pt"))
    ap.add_argument("--updates", type=int, default=3)
    ap.add_argument("--steps", type=int, default=16384)
    ap.add_argument("--epochs", type=int, default=6)
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--difficulty", default="normal")
    ap.add_argument("--out", default=os.path.join(ROOT, "models", "ppo.pt"))
    args = ap.parse_args()
    torch.manual_seed(0)
    model = load_model(args.init)
    opt = torch.optim.Adam(model.parameters(), lr=args.lr)
    env = BotDuelEnv(difficulty=args.difficulty, max_steps=900, seed=7)
    best = evaluate(model, episodes=20, difficulty=args.difficulty, seed=123, verbose=True)
    best_state = {k: v.clone() for k, v in model.state_dict().items()}
    print("init winrate", best["winrate"])
    log_path = os.path.join(ROOT, "logs", "train_ppo.csv")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("update,steps,loss,winrate,kills,deaths\n")
    total_steps = 0
    for u in range(1, args.updates + 1):
        t0 = time.time()
        obs, acts, old_logp, values, rewards, dones = collect(model, env, args.steps)
        total_steps += args.steps
        adv, returns = gae(rewards, values, dones)
        loss = update(model, opt, obs, acts, old_logp, adv, returns, epochs=args.epochs)
        m = evaluate(model, episodes=20, difficulty=args.difficulty, seed=123, verbose=False)
        print("update %d  steps %d  loss %.3f  winrate %.2f  K/D %.2f/%.2f  (%.0fs)" %
              (u, total_steps, loss, m["winrate"], m["kills"], m["deaths"], time.time() - t0))
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("%d,%d,%.4f,%.2f,%.2f,%.2f\n" %
                    (u, total_steps, loss, m["winrate"], m["kills"], m["deaths"]))
        if m["winrate"] > best["winrate"] or (m["winrate"] == best["winrate"] and
                                              m["kills"] > best["kills"]):
            best = m
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
    env.close()
    model.load_state_dict(best_state)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    torch.save({"state_dict": model.state_dict(),
                "config": {"obs_dim": model.obs_dim, "hidden": model.hidden},
                "metrics": best}, args.out)
    print("best winrate %.2f, saved %s" % (best["winrate"], args.out))


if __name__ == "__main__":
    main()
