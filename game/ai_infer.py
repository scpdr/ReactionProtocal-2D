# -*- coding: utf-8 -*-
"""纯 Python 策略推理（部署端零依赖，不需要 torch）。

读取 tools/export_weights.py 导出的 JSON，前向计算 MLP + 6 个动作头，返回 argmax 动作。
"""
import json
import math


class AIPolicy:
    def __init__(self, path):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self.data = data
        self.obs_dim = data["obs_dim"]
        self.hidden = data["hidden"]
        self.action_keys = data["action_keys"]
        self.action_sizes = data["action_sizes"]
        self.trunk = data["trunk"]
        self.heads = [data["heads"][k] for k in self.action_keys]

    @staticmethod
    def _linear(x, layer):
        w = layer["w"]
        b = layer["b"]
        out = []
        for j in range(len(w)):
            row = w[j]
            s = b[j]
            for i in range(len(row)):
                s += row[i] * x[i]
            out.append(s)
        return out

    @staticmethod
    def _relu(x):
        return [v if v > 0.0 else 0.0 for v in x]

    def logits(self, obs):
        h = self._relu(self._linear(obs, self.trunk[0]))
        h = self._relu(self._linear(h, self.trunk[1]))
        return [self._linear(h, head) for head in self.heads]

    def act(self, obs):
        """返回动作 list（顺序同 action_keys）。"""
        logits = self.logits(obs)
        return [max(range(len(lg)), key=lambda i: lg[i]) for lg in logits]

    def act_logits(self, obs):
        return self.act(obs), self.logits(obs)


def benchmark(policy, n=2000):
    import random
    import time
    rng = random.Random(0)
    obs = [rng.uniform(-1, 1) for _ in range(policy.obs_dim)]
    # 预热
    for _ in range(20):
        policy.act(obs)
    t = time.perf_counter()
    for _ in range(n):
        policy.act(obs)
    return (time.perf_counter() - t) / n * 1000.0
