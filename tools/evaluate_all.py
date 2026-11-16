# -*- coding: utf-8 -*-
"""统一评测：BC / DAgger / PPO / 随机策略 vs FSM（easy/normal/hard），输出 CSV。"""
import argparse
import csv
import os
import random
import sys

import torch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from game.policy import PolicyNet  # noqa: E402
from tools.eval_utils import evaluate, evaluate_fn, random_actions  # noqa: E402


def load(path):
    ck = torch.load(path, map_location="cpu", weights_only=False)
    m = PolicyNet(obs_dim=ck.get("config", {}).get("obs_dim", 41),
                  hidden=ck.get("config", {}).get("hidden", 128))
    m.load_state_dict(ck["state_dict"])
    m.eval()
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--episodes", type=int, default=50)
    ap.add_argument("--out", default=os.path.join(ROOT, "logs", "eval_ai.csv"))
    args = ap.parse_args()
    models = [("BC", os.path.join(ROOT, "models", "bc_ammo.pt")),
              ("DAgger", os.path.join(ROOT, "models", "bc_ammo.pt")),
              ("PPO", os.path.join(ROOT, "models", "ppo_quota.pt"))]
    rows = []
    for name, path in models:
        if not os.path.exists(path):
            continue
        m = load(path)
        for diff in ("easy", "normal", "hard"):
            r = evaluate(m, episodes=args.episodes, difficulty=diff, seed=2026)
            rows.append({"model": name, "difficulty": diff,
                         "winrate": round(r["winrate"], 3),
                         "sniper_frac": round(r["sniper_frac"], 3),
                         "kills": round(r["kills"], 2), "deaths": round(r["deaths"], 2),
                         "episodes": r["episodes"]})
            print("%-7s %-6s winrate %.2f K/D %.2f/%.2f sniper %.1f%%" %
                  (name, diff, r["winrate"], r["kills"], r["deaths"], r["sniper_frac"] * 100))
    rng = random.Random(1)
    r = evaluate_fn(lambda o: random_actions(o, rng), episodes=max(10, args.episodes // 2),
                    difficulty="normal", seed=2026)
    rows.append({"model": "Random", "difficulty": "normal",
                 "winrate": round(r["winrate"], 3), "sniper_frac": 0.0,
                 "kills": round(r["kills"], 2),
                 "deaths": round(r["deaths"], 2), "episodes": r["episodes"]})
    print("Random  normal winrate %.2f K/D %.2f/%.2f" %
          (r["winrate"], r["kills"], r["deaths"]))
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["model", "difficulty", "winrate",
                                          "sniper_frac", "kills", "deaths", "episodes"])
        w.writeheader()
        w.writerows(rows)
    print("saved", args.out)


if __name__ == "__main__":
    main()
