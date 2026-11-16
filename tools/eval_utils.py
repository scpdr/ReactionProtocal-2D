# -*- coding: utf-8 -*-
"""评测工具：受控策略 vs 游戏内置 FSM 机器人。"""
import random

import torch

from game.env import TeamDuelEnv, ACTION_KEYS, ACTION_SIZES


def evaluate(model, episodes=20, difficulty="normal", max_steps=900, seed=100,
             device="cpu", linear=False, verbose=False):
    model.eval()
    model.to(device)
    env = TeamDuelEnv(difficulty=difficulty, max_steps=max_steps, seed=seed,
                      linear=linear)
    wins = 0
    kills_sum = deaths_sum = 0
    sniper_steps = total_steps = 0
    results = []
    for ep in range(episodes):
        obs = env.reset()
        done = False
        info = {}
        while not done:
            if env.game.players[env.agent_id]["weapon"] == "sniper":
                sniper_steps += 1
            total_steps += 1
            with torch.no_grad():
                acts, _, _, _ = model.act(obs, deterministic=True)
            obs, r, done, info = env.step(dict(zip(ACTION_KEYS, acts)))
        my = info["red_kills"]
        bot = info["blue_kills"]
        kills_sum += my
        deaths_sum += bot
        wins += 1 if my > bot else 0
        results.append((my, bot))
    env.close()
    metrics = {"winrate": wins / float(episodes), "kills": kills_sum / float(episodes),
               "deaths": deaths_sum / float(episodes), "episodes": episodes,
               "sniper_frac": sniper_steps / float(max(1, total_steps)),
               "results": results}
    if verbose:
        print("eval 2v2 vs FSM(%s): winrate=%.2f K/D=%.2f/%.2f sniper=%.1f%%" %
              (difficulty, metrics["winrate"], metrics["kills"], metrics["deaths"],
               metrics["sniper_frac"] * 100))
    return metrics


def evaluate_fn(act_fn, episodes=20, difficulty="normal", max_steps=900, seed=100,
                linear=False, verbose=False):
    env = TeamDuelEnv(difficulty=difficulty, max_steps=max_steps, seed=seed,
                      linear=linear)
    wins = kills_sum = deaths_sum = 0
    for _ in range(episodes):
        obs = env.reset()
        done = False
        info = {}
        while not done:
            acts = act_fn(obs)
            obs, r, done, info = env.step(dict(zip(ACTION_KEYS, acts)))
        my = info["red_kills"]
        bot = info["blue_kills"]
        kills_sum += my
        deaths_sum += bot
        wins += 1 if my > bot else 0
    env.close()
    m = {"winrate": wins / float(episodes), "kills": kills_sum / float(episodes),
         "deaths": deaths_sum / float(episodes), "episodes": episodes}
    if verbose:
        print("eval: winrate=%.2f K/D=%.2f/%.2f" %
              (m["winrate"], m["kills"], m["deaths"]))
    return m


def random_actions(obs, rng=None):
    rng = rng or random
    return [rng.randrange(n) for n in ACTION_SIZES]
