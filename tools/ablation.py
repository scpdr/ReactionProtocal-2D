# -*- coding: utf-8 -*-
"""规则消融：同一策略在“化学承重”与“线性血条”两种伤害模型下的胜率/行为。

注意：这是行为层消融（同一 policy 跨模式评估），不是重新训练一个线性血条 AI；
结论需与“训练分布不同”的限制一起写进报告。
"""
import argparse
import collections
import csv
import math
import os
import random
import sys

import torch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from game.env import TeamDuelEnv, ACTION_KEYS, WEAPON_IDS  # noqa: E402
from game.policy import PolicyNet  # noqa: E402
from tools.teacher import teacher_action  # noqa: E402


def entropy(counter):
    n = sum(counter.values())
    if n <= 0:
        return 0.0
    return -sum((c / n) * math.log(c / n + 1e-9) for c in counter.values())


def run(model, linear, episodes, difficulty, seed):
    env = TeamDuelEnv(difficulty=difficulty, seed=seed, linear=linear)
    wins = kills = deaths = 0
    steps = fire = 0
    sw = collections.Counter()
    hits = collections.Counter()
    for _ in range(episodes):
        obs = env.reset()
        done = False
        info = {}
        while not done:
            if model is None:
                act = teacher_action(env.game, env.agent_id, None)
                cur = env.game.players[env.agent_id]["weapon"]
                swv = WEAPON_IDS.index(cur) if act["switch"] is None else act["switch"]
                acts = [act["move"], act["aim"], act["fire"], act["reload"],
                        swv, act["indicator"]]
            else:
                acts, _, _, _ = model.act(obs, deterministic=True)
            for k, a in zip(ACTION_KEYS, acts):
                if k == "switch":
                    sw[a] += 1
                if k == "fire":
                    fire += a
            steps += 1
            obs, r, done, info = env.step(dict(zip(ACTION_KEYS, acts)))
            for ev in info["events"]:
                if ev.get("t") == "hit" and ev.get("for") == info.get("agent_id"):
                    hits[ev.get("ch")] += 1
        my = info["red_kills"]
        bot = info["blue_kills"]
        wins += my > bot
        kills += my
        deaths += bot
    env.close()
    weapon_names = ["knife", "pistol", "rifle", "sniper", "shotgun", "lmg",
                    "limewater", "vitc"]
    top = [weapon_names[i] for i, _ in sw.most_common(2)]
    return {"winrate": wins / episodes, "kills": kills / episodes,
            "deaths": deaths / episodes, "fire_rate": fire / max(1, steps),
            "switch_entropy": entropy(sw), "top_weapons": "|".join(top),
            "acid_hits": hits.get("acid", 0), "base_hits": hits.get("base", 0),
            "oxidizing_hits": hits.get("oxidizing", 0), "steps": steps}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--episodes", type=int, default=30)
    ap.add_argument("--difficulty", default="normal")
    ap.add_argument("--out", default=os.path.join(ROOT, "logs", "ablation.csv"))
    args = ap.parse_args()
    models = {"PPO": os.path.join(ROOT, "models", "ppo_quota.pt"),
              "BC": os.path.join(ROOT, "models", "bc_ammo.pt")}
    loaded = {}
    for name, path in models.items():
        if not os.path.exists(path):
            continue
        ck = torch.load(path, map_location="cpu", weights_only=False)
        m = PolicyNet(obs_dim=ck["config"]["obs_dim"], hidden=ck["config"]["hidden"])
        m.load_state_dict(ck["state_dict"])
        m.eval()
        loaded[name] = m
    rows = []
    for name, m in loaded.items():
        for linear in (False, True):
            r = run(m, linear, args.episodes, args.difficulty, 2026)
            r.update({"model": name, "damage_mode": "linear" if linear else "chem"})
            rows.append(r)
            print("%-5s %-6s winrate %.2f K/D %.2f/%.2f fire %.2f entropy %.2f weapons %s ch %d/%d/%d" %
                  (name, r["damage_mode"], r["winrate"], r["kills"], r["deaths"],
                   r["fire_rate"], r["switch_entropy"], r["top_weapons"],
                   r["acid_hits"], r["base_hits"], r["oxidizing_hits"]))
    for linear in (False, True):
        r = run(None, linear, args.episodes, args.difficulty, 2026)
        r.update({"model": "Teacher", "damage_mode": "linear" if linear else "chem"})
        rows.append(r)
        print("%-5s %-6s winrate %.2f K/D %.2f/%.2f fire %.2f entropy %.2f weapons %s ch %d/%d/%d" %
              ("Teacher", r["damage_mode"], r["winrate"], r["kills"], r["deaths"],
               r["fire_rate"], r["switch_entropy"], r["top_weapons"],
               r["acid_hits"], r["base_hits"], r["oxidizing_hits"]))
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    fields = ["model", "damage_mode", "winrate", "kills", "deaths", "fire_rate",
              "switch_entropy", "top_weapons", "acid_hits", "base_hits",
              "oxidizing_hits", "steps"]
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print("saved", args.out)


if __name__ == "__main__":
    main()
