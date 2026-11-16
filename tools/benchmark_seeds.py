# -*- coding: utf-8 -*-
"""多随机种子基准：报告用的胜率均值 ± 95% CI、狙击时间占比。

用法（正式报告建议 200 局）：
  python tools/benchmark_seeds.py --model models/ppo_quota.pt --seeds 1,2,3 --episodes 70
"""
import argparse
import csv
import math
import os
import sys

import torch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from game.policy import PolicyNet  # noqa: E402
from tools.eval_utils import evaluate  # noqa: E402


def load(path):
    ck = torch.load(path, map_location="cpu", weights_only=False)
    m = PolicyNet(obs_dim=ck["config"]["obs_dim"], hidden=ck["config"]["hidden"])
    m.load_state_dict(ck["state_dict"])
    m.eval()
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=os.path.join(ROOT, "models", "ppo_quota.pt"))
    ap.add_argument("--seeds", default="1,2,3")
    ap.add_argument("--episodes", type=int, default=70)
    ap.add_argument("--difficulty", default="normal")
    ap.add_argument("--out", default=os.path.join(ROOT, "logs", "benchmark_seeds.csv"))
    args = ap.parse_args()
    model = load(args.model)
    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
    games = []
    sniper = []
    per_seed = []
    for seed in seeds:
        m = evaluate(model, episodes=args.episodes, difficulty=args.difficulty, seed=seed)
        per_seed.append(m)
        games.extend(1 if a > b else 0 for a, b in m["results"])
        sniper.append(m["sniper_frac"])
        print("seed %d: win %.3f sniper %.3f" % (seed, m["winrate"], m["sniper_frac"]))
    n = len(games)
    mean = sum(games) / float(n)
    sd = math.sqrt(sum((g - mean) ** 2 for g in games) / max(1, n - 1))
    ci = 1.96 * sd / math.sqrt(n)
    sniper_mean = sum(sniper) / len(sniper)
    print("total %d games: win %.3f ± %.3f (95%% CI), sniper %.1f%%" %
          (n, mean, ci, sniper_mean * 100))
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["seed", "episodes", "winrate", "sniper_frac"])
        for seed, m in zip(seeds, per_seed):
            w.writerow([seed, m["episodes"], round(m["winrate"], 4), round(m["sniper_frac"], 4)])
        w.writerow(["ALL", n, round(mean, 4), round(sniper_mean, 4)])
        w.writerow(["CI95", "", round(ci, 4), ""])
    print("saved", args.out)


if __name__ == "__main__":
    main()
