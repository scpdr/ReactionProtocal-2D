# -*- coding: utf-8 -*-
"""AI 部署侧测试：JSON 推理与 torch 一致性、推理耗时、服务端 AI 机器人可运行。"""
import os
import random
import sys
import time
import unittest

import torch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from game.policy import PolicyNet  # noqa: E402
from game.ai_infer import AIPolicy, benchmark  # noqa: E402

WEIGHTS = os.path.join(ROOT, "data", "ai_weights.json")
CKPT = os.path.join(ROOT, "models", "ppo_quota.pt")


class TestAI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.exists(WEIGHTS):
            raise unittest.SkipTest("缺少 data/ai_weights.json")
        cls.policy = AIPolicy(WEIGHTS)
        cls.model = None
        if os.path.exists(CKPT):
            ck = torch.load(CKPT, map_location="cpu", weights_only=False)
            m = PolicyNet(obs_dim=ck["config"]["obs_dim"], hidden=ck["config"]["hidden"])
            m.load_state_dict(ck["state_dict"])
            m.eval()
            cls.model = m

    def test_parity(self):
        if self.model is None:
            self.skipTest("缺少 torch checkpoint")
        rng = random.Random(7)
        mismatch = 0
        for _ in range(100):
            obs = [rng.uniform(-2, 2) for _ in range(self.policy.obs_dim)]
            with torch.no_grad():
                logits, _ = self.model(torch.tensor(obs, dtype=torch.float32).unsqueeze(0))
                ta = [int(lg.argmax(-1)) for lg in logits]
            pa = self.policy.act(obs)
            mismatch += sum(1 for a, b in zip(ta, pa) if a != b)
        self.assertEqual(mismatch, 0, "纯 Python 与 torch argmax 必须完全一致")

    def test_speed(self):
        ms = benchmark(self.policy, 1000)
        print("pure-python inference %.3f ms" % ms)
        self.assertLess(ms, 1.5, "推理需在 1.5ms/次以内")

    def test_aim_turn_limit(self):
        """AI 瞄准不能 0 帧拉枪：单帧转角不超过 AI_TURN_RATE*dt。"""
        import math
        import server
        g = server.Game()
        p = g.add_player("AI", "red")
        p["ai_aim"] = True
        p["shoot"] = False
        p["angle"] = 0.0
        p["ax"], p["ay"] = p["x"], p["y"] + 100.0   # 目标正南（90°）
        g.update(0.0, 0.1)
        self.assertLessEqual(abs(p["angle"]), server.AI_TURN_RATE * 0.1 + 1e-6)

    def test_server_ai_bot(self):
        import server
        old = server.AI_POLICY
        server.AI_POLICY = self.policy
        try:
            g = server.Game()
            g.set_bots(2, "normal")
            start = {p["id"]: (p["x"], p["y"]) for p in g.players.values()}
            t = 100.0
            for _ in range(120):
                t += 1 / 30.0
                g.update(t, 1 / 30.0)
            moved = sum(1 for pid, p in g.players.items()
                        if pid in start and (abs(p["x"] - start[pid][0]) +
                                             abs(p["y"] - start[pid][1])) > 5)
            self.assertGreaterEqual(moved, 1, "AI 机器人应移动")
        finally:
            server.AI_POLICY = old


if __name__ == "__main__":
    unittest.main(verbosity=2)
