# -*- coding: utf-8 -*-
"""把 PyTorch 策略导出成纯 Python 推理用的 JSON 权重。

用法：
    python tools/export_weights.py --ckpt models/ppo.pt --out data/ai_weights.json
"""
import argparse
import json
import os
import sys

import torch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from game.policy import ACTION_KEYS, ACTION_SIZES, OBS_DIM  # noqa: E402


def layer_json(weight, bias, nd=5):
    return {"w": [[round(float(x), nd) for x in row] for row in weight.tolist()],
            "b": [round(float(x), nd) for x in bias.tolist()]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", default=os.path.join(ROOT, "models", "ppo.pt"))
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "ai_weights.json"))
    args = ap.parse_args()
    ck = torch.load(args.ckpt, map_location="cpu", weights_only=False)
    sd = ck["state_dict"]
    cfg = ck.get("config", {})
    out = {
        "format": "huazhan-policy-v1",
        "obs_dim": int(cfg.get("obs_dim", OBS_DIM)),
        "hidden": int(cfg.get("hidden", 128)),
        "action_keys": ACTION_KEYS,
        "action_sizes": ACTION_SIZES,
        "trunk": [
            layer_json(sd["trunk.0.weight"], sd["trunk.0.bias"]),
            layer_json(sd["trunk.2.weight"], sd["trunk.2.bias"]),
        ],
        "heads": {
            k: layer_json(sd["heads.%d.weight" % i], sd["heads.%d.bias" % i])
            for i, k in enumerate(ACTION_KEYS)
        },
        "value": layer_json(sd["value.weight"], sd["value.bias"]),
        "metrics": ck.get("metrics", {}),
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print("saved %s (%.0f KB)" % (args.out, os.path.getsize(args.out) / 1024.0))


if __name__ == "__main__":
    main()
