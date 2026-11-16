# -*- coding: utf-8 -*-
"""救援行为评测：2v2 带队友，统计“中和/维C救援”次数、队友存活率与胜率。"""
import argparse
import csv
import os
import random
import sys

import torch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from game.env import TeamDuelEnv, ACTION_KEYS, ACTION_SIZES  # noqa: E402
from game.policy import PolicyNet  # noqa: E402
from tools.teacher import teacher_action  # noqa: E402


def load(path):
    ck = torch.load(path, map_location="cpu", weights_only=False)
    m = PolicyNet(obs_dim=ck["config"]["obs_dim"], hidden=ck["config"]["hidden"])
    m.load_state_dict(ck["state_dict"])
    m.eval()
    return m


def run(model, episodes=50, seed=2026, difficulty="normal"):
    env = TeamDuelEnv(difficulty=difficulty, seed=seed)
    wins = rescue = survive = kills = deaths = supply = 0
    for _ in range(episodes):
        obs = env.reset()
        done = False
        info = {}
        while not done:
            if model is None:
                ta = teacher_action(env.game, env.agent_id, None)
                acts = [ta["move"], ta["aim"], ta["fire"], ta["reload"],
                        ta["switch"] if ta["switch"] is not None else 7, ta["indicator"]]
            else:
                acts, _, _, _ = model.act(obs, deterministic=True)
            obs, r, done, info = env.step(dict(zip(ACTION_KEYS, acts)))
            for ev in info["events"]:
                if ev.get("t") in ("neutralize", "restore") and ev.get("pid") == info["agent_id"]:
                    rescue += 1
                elif ev.get("t") == "supply" and ev.get("pid") == info["agent_id"]:
                    supply += 1
        kills += info["red_kills"]
        deaths += info["blue_kills"]
        survive += 1 if info["ally_alive"] else 0
        wins += 1 if info["red_kills"] > info["blue_kills"] else 0
    env.close()
    return {"winrate": wins / episodes, "rescue": rescue / episodes,
            "supply": supply / episodes,
            "ally_survival": survive / episodes,
            "kills": kills / episodes, "deaths": deaths / episodes}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--episodes", type=int, default=50)
    ap.add_argument("--out", default=os.path.join(ROOT, "logs", "rescue_eval.csv"))
    args = ap.parse_args()
    models = [("BC-ammo", os.path.join(ROOT, "models", "bc_ammo.pt")),
              ("PPO-quota", os.path.join(ROOT, "models", "ppo_quota.pt"))]
    rows = []
    for name, path in models:
        if not os.path.exists(path):
            continue
        r = run(load(path), episodes=args.episodes)
        r["model"] = name
        rows.append(r)
        print("%-8s win %.2f rescue %.2f supply %.2f ally %.2f" %
              (name, r["winrate"], r["rescue"], r["supply"], r["ally_survival"]))
    tr = run(None, episodes=args.episodes)
    tr["model"] = "Teacher"
    rows.append(tr)
    print("%-8s win %.2f rescue %.2f supply %.2f ally %.2f" %
          ("Teacher", tr["winrate"], tr["rescue"], tr["supply"], tr["ally_survival"]))
    # random
    env = TeamDuelEnv(seed=2026)
    rng = random.Random(1)
    wins = rescue = survive = 0
    for _ in range(args.episodes):
        obs = env.reset(); done = False; info = {}
        while not done:
            acts = [rng.randrange(n) for n in ACTION_SIZES]
            obs, r, done, info = env.step(dict(zip(ACTION_KEYS, acts)))
            rescue += sum(1 for ev in info["events"]
                          if ev.get("t") in ("neutralize", "restore")
                          and ev.get("pid") == info["agent_id"])
        wins += info["red_kills"] > info["blue_kills"]
        survive += info["ally_alive"]
    env.close()
    rr = {"model": "Random", "winrate": wins / args.episodes,
          "rescue": rescue / args.episodes, "supply": 0.0,
          "ally_survival": survive / args.episodes,
          "kills": 0, "deaths": 0}
    rows.append(rr)
    print("%-8s win %.2f rescue %.2f ally_survive %.2f" %
          ("Random", rr["winrate"], rr["rescue"], rr["ally_survival"]))
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    fields = ["model", "winrate", "rescue", "supply", "ally_survival", "kills", "deaths"]
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print("saved", args.out)


if __name__ == "__main__":
    main()
