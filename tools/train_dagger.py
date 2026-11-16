# -*- coding: utf-8 -*-
"""DAgger：用当前策略跑环境，把访问到的状态交给教师重新标注，再聚合训练。

BC 只在教师的“完美轨迹”上训练，策略一偏离就会 compounding error；
DAgger 把策略自己走出来的状态补上教师标签，是让 BC 真正能打的关键一步。

用法：
    python tools/train_dagger.py --init-steps 30000 --dagger-steps 15000 --iters 3
"""
import argparse
import os
import random
import sys
import time

import torch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from game.env import TeamDuelEnv, ACTION_KEYS  # noqa: E402
from game.policy import PolicyNet  # noqa: E402
from tools.teacher import teacher_action  # noqa: E402
from tools.train_bc import collect, train_bc, canonical  # noqa: E402
from tools.eval_utils import evaluate  # noqa: E402


def cat_data(a, b):
    return torch.cat([a[0], b[0]], 0), torch.cat([a[1], b[1]], 0)


def policy_collect(model, steps=15000, seed=0, difficulty="normal"):
    """用当前策略 vs FSM 走，教师给访问状态打标签。"""
    env = TeamDuelEnv(difficulty=difficulty, max_steps=900, seed=seed)
    rng = random.Random(seed)
    obs_buf, act_buf = [], []
    ep = 0
    with torch.no_grad():
        while len(obs_buf) < steps:
            obs = env.reset()
            ep += 1
            done = False
            while not done:
                a, _, _, _ = model.act(obs, deterministic=False)
                acts = dict(zip(ACTION_KEYS, a))
                obs_buf.append(list(obs))
                act_buf.append(canonical(teacher_action(env.game, env.agent_id, rng),
                                         env.game.players[env.agent_id]))
                obs, r, done, info = env.step(acts)
    env.close()
    print("  policy states %d (episodes %d)" % (len(obs_buf), ep))
    return (torch.tensor(obs_buf, dtype=torch.float32),
            torch.tensor(act_buf, dtype=torch.long))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--init-steps", type=int, default=30000)
    ap.add_argument("--dagger-steps", type=int, default=15000)
    ap.add_argument("--iters", type=int, default=3)
    ap.add_argument("--epochs", type=int, default=10)
    ap.add_argument("--out", default=os.path.join(ROOT, "models", "dagger.pt"))
    args = ap.parse_args()
    t0 = time.time()
    print("== 初始教师数据 ==")
    data = collect(steps=args.init_steps, seed=1, epsilon=0.05)
    model = train_bc(*data, epochs=args.epochs, seed=1, weighted=False)
    best = evaluate(model, episodes=20, difficulty="normal", seed=123, verbose=True)
    best_state = {k: v.clone() for k, v in model.state_dict().items()}
    print("初始 BC: winrate=%.2f" % best["winrate"])

    for it in range(1, args.iters + 1):
        print("== DAgger 迭代 %d ==" % it)
        pol = policy_collect(model, steps=args.dagger_steps, seed=10 + it)
        data = cat_data(data, pol)
        print("  聚合样本:", len(data[0]))
        model = train_bc(*data, epochs=max(4, args.epochs - 2), seed=1 + it, weighted=False)
        m = evaluate(model, episodes=20, difficulty="normal", seed=123, verbose=True)
        print("  迭代 %d: winrate=%.2f kills=%.2f deaths=%.2f (best %.2f)" %
              (it, m["winrate"], m["kills"], m["deaths"], best["winrate"]))
        if m["winrate"] > best["winrate"] or (m["winrate"] == best["winrate"] and
                                              m["kills"] > best["kills"]):
            best = m
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
    model.load_state_dict(best_state)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    torch.save({"state_dict": model.state_dict(),
                "config": {"obs_dim": model.obs_dim, "hidden": model.hidden},
                "metrics": best}, args.out)
    print("best winrate %.2f, saved %s (%.1fs)" % (best["winrate"], args.out, time.time() - t0))


if __name__ == "__main__":
    main()
