# -*- coding: utf-8 -*-
"""试剂表加载与 TTK 计算（纯标准库）。"""
import json
import os

from .chem import ACID_EQUIV_MMOL, CHANNELS

__all__ = ["load_weapons", "ttk_seconds", "old_ttk_seconds", "weapon_by_id"]

_DEFAULT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "weapons.json")


def load_weapons(path=None):
    with open(path or _DEFAULT, "r", encoding="utf-8") as f:
        data = json.load(f)
    data["by_id"] = {w["id"]: w for w in data["weapons"]}
    return data


def weapon_by_id(data, wid):
    return data["by_id"][wid]


def ttk_seconds(w):
    """新体系击杀所需秒数（连续伤害模型，与设计文档一致）。

    酸/碱：需 51 mmol 当量；物理/氧化：需 100 mmol 缓冲剥离。
    单发当量 >= 需求 -> 0（一击必杀）。
    """
    channel = w["channel"]
    if channel in ("reducing",):
        return None
    need = ACID_EQUIV_MMOL if channel in ("acid", "base") else 100.0
    per = float(w["per_shot_mmol"])
    interval = float(w["interval"])
    if per >= need:
        return 0.0
    return need / (per / interval)


def old_ttk_seconds(old):
    """旧版 100 HP 下的击杀秒数。"""
    dmg = float(old["dmg"])
    interval = float(old["interval"])
    if dmg >= 100.0:
        return 0.0
    return 100.0 / (dmg / interval)
