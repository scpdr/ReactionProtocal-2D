# -*- coding: utf-8 -*-
"""AI 训练环境冒烟测试：随机动作 rollout + 观测维度 + 遥测写入。"""
import os
import random
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from game.env import DuoEnv, OBS_DIM, ACTION_SIZES  # noqa: E402


class TestEnv(unittest.TestCase):
    def _random_act(self, rng):
        return {
            "move": rng.randint(0, ACTION_SIZES[0] - 1),
            "aim": rng.randint(0, ACTION_SIZES[1] - 1),
            "fire": rng.randint(0, 1),
            "reload": 1 if rng.random() < 0.05 else 0,
            "switch": rng.randint(0, ACTION_SIZES[4] - 1),
            "indicator": 1 if rng.random() < 0.03 else 0,
        }

    def test_random_rollout(self):
        env = DuoEnv(bots=2, max_steps=180, seed=7)
        obs = env.reset()
        self.assertEqual(len(obs), 2)
        self.assertEqual(len(obs[0]), OBS_DIM)
        rng = random.Random(7)
        total = 0.0
        for _ in range(180):
            obs, rew, done, info = env.step([self._random_act(rng), self._random_act(rng)])
            self.assertEqual(len(obs), 2)
            self.assertEqual(len(rew), 2)
            total += sum(rew)
            if done:
                break
        env.close()
        print("random rollout ok, total reward", round(total, 2))

    def test_telemetry_file(self):
        path = os.path.join(tempfile.gettempdir(), "huazhan_env_telemetry.jsonl")
        if os.path.exists(path):
            os.remove(path)
        env = DuoEnv(bots=1, max_steps=5, seed=3, telemetry=path)
        env.reset()
        rng = random.Random(3)
        for _ in range(5):
            obs, rew, done, info = env.step([self._random_act(rng), self._random_act(rng)])
            if done:
                break
        env.close()
        self.assertTrue(os.path.exists(path))
        lines = open(path, encoding="utf-8").read().strip().split("\n")
        self.assertGreaterEqual(len(lines), 1)
        print("telemetry lines", len(lines))
        os.remove(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
