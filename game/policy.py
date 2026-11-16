# -*- coding: utf-8 -*-
"""训练用策略网络（PyTorch，仅训练侧依赖；部署用 ai_infer.py 的纯 Python 前向）。

共享 MLP + 6 个离散动作头：
    move(9) / aim(8) / fire(2) / reload(2) / switch(8) / indicator(2)
"""
import torch
import torch.nn as nn
import torch.nn.functional as F

ACTION_KEYS = ["move", "aim", "fire", "reload", "switch", "indicator"]
ACTION_SIZES = [9, 6, 2, 2, 8, 2]
OBS_DIM = 56


class PolicyNet(nn.Module):
    def __init__(self, obs_dim=OBS_DIM, hidden=128):
        super().__init__()
        self.obs_dim = obs_dim
        self.hidden = hidden
        self.trunk = nn.Sequential(
            nn.Linear(obs_dim, hidden), nn.ReLU(),
            nn.Linear(hidden, hidden), nn.ReLU(),
        )
        self.heads = nn.ModuleList([nn.Linear(hidden, n) for n in ACTION_SIZES])
        self.value = nn.Linear(hidden, 1)

    def forward(self, x):
        h = self.trunk(x)
        logits = [head(h) for head in self.heads]
        value = self.value(h).squeeze(-1)
        return logits, value

    @torch.no_grad()
    def act(self, obs, deterministic=False):
        dev = next(self.parameters()).device
        x = torch.as_tensor(obs, dtype=torch.float32, device=dev).unsqueeze(0)
        logits, value = self.forward(x)
        actions, logp, ent = [], 0.0, 0.0
        for lg in logits:
            dist = torch.distributions.Categorical(logits=lg)
            a = lg.argmax(-1) if deterministic else dist.sample()
            actions.append(int(a.item()))
            logp = logp + dist.log_prob(a)
            ent = ent + dist.entropy()
        return actions, float(logp.item()), float(ent.item()), float(value.item())

    def act_batch(self, obs_batch):
        """obs_batch: [B, obs_dim] tensor -> list of [B] action tensors + logp/ent/value."""
        logits, value = self.forward(obs_batch)
        acts, logps, ents = [], 0.0, 0.0
        for lg in logits:
            dist = torch.distributions.Categorical(logits=lg)
            a = dist.sample()
            acts.append(a)
            logps = logps + dist.log_prob(a)
            ents = ents + dist.entropy()
        return acts, logps, ents, value

    def evaluate_actions(self, obs_batch, actions_batch):
        """actions_batch: list of [B] long tensors -> logp, entropy, value, logits list."""
        logits, value = self.forward(obs_batch)
        logps, ents = 0.0, 0.0
        for lg, a in zip(logits, actions_batch):
            dist = torch.distributions.Categorical(logits=lg)
            logps = logps + dist.log_prob(a)
            ents = ents + dist.entropy()
        return logps, ents, value, logits
