# -*- coding: utf-8 -*-
"""规则教师：为行为克隆提供演示动作（也用作 PPO 的固定对手）。

不是最终 AI，只是“会瞄准、会走位、会按距离换枪、会救队友”的脚本；
训练目标是把这类战术行为先克隆下来，再用 PPO 做小幅改进。
"""
import math
import random

import server as S
from game.env import WEAPON_IDS, dir_to_octant


def _units(g, p):
    enemies, allies = [], []
    for q in g.players.values():
        if q["id"] == p["id"] or not q["alive"]:
            continue
        d = math.hypot(q["x"] - p["x"], q["y"] - p["y"])
        vis = g._can_see(p["x"], p["y"], q["x"], q["y"])
        if q["team"] == p["team"]:
            allies.append((d, q, vis))
        else:
            enemies.append((d, q, vis))
    enemies.sort(key=lambda x: x[0])
    allies.sort(key=lambda x: x[0])
    return enemies, allies


def _support_target(p, allies):
    """找一个 pH 偏离安全区、值得中和的队友（只考虑有视线的）。"""
    best = None
    for d, q, vis in allies:
        if not vis or d > 700:
            continue
        ph = q["chem"].ph if q["chem"].ph is not None else 7.0
        if ph < 6.2 or ph > 7.8:
            if best is None or d < best[0]:
                best = (d, q, vis, ph)
    return best


def teacher_action(g, pid, rng=None):
    """返回一个 6 元动作 dict（aim 0..2 敌人、3..4 队友、5 保持）。"""
    rng = rng or random
    p = g.players[pid]
    idle = {"move": 0, "aim": 5, "fire": 0, "reload": 0,
            "switch": None, "indicator": 0}
    if not p["alive"]:
        return idle
    # 低弹量：先去最近的补给点，不在空弹时硬打
    wdef = S.WEAPONS.get(p["weapon"], {})
    total_ammo = p["ammo"][p["weapon"]] + p.get("reserve", {}).get(p["weapon"], 0)
    if total_ammo <= max(3, 0.12 * (wdef.get("mag", 1) + S.WEAPON_RESERVE.get(p["weapon"], 0))):
        if S.AMMO_PADS:
            pad = min(S.AMMO_PADS, key=lambda a: (a[0] - p["x"]) ** 2 + (a[1] - p["y"]) ** 2)
            ang = math.atan2(pad[1] - p["y"], pad[0] - p["x"])
            return {"move": dir_to_octant(ang), "aim": 5, "fire": 0, "reload": 0,
                    "switch": None, "indicator": 0}
    enemies, allies = _units(g, p)
    sup = _support_target(p, allies)
    if sup is not None:
        d, ally, visible, ph = sup
        vis_allies = [a for a in allies if a[2]]
        try:
            aim = 3 + [a[1]["id"] for a in vis_allies].index(ally["id"])
        except ValueError:
            aim = 5
        ang = math.atan2(ally["y"] - p["y"], ally["x"] - p["x"])
        if d > 280:
            mv = dir_to_octant(ang)
        else:
            mv = dir_to_octant(ang + math.pi / 2)
        want = "lmg" if ph < 6.2 else "rifle"
        switch = WEAPON_IDS.index(want) if p["weapon"] != want else None
        fire = 1 if (visible and d < 650 and p["ammo"][p["weapon"]] > 0) else 0
        return {"move": mv, "aim": aim, "fire": fire, "reload": 0,
                "switch": switch, "indicator": 0}

    if not enemies:
        return idle
    vis = [e for e in enemies if e[2]]
    d, target, visible = (vis[0] if vis else enemies[0])
    aim = 5
    if vis:
        aim = min(2, [e[1]["id"] for e in vis].index(target["id"]))
    ang = math.atan2(target["y"] - p["y"], target["x"] - p["x"])

    # 走位：远距离接近、近距离后撤、中距离侧移
    if d > 420:
        mv = dir_to_octant(ang)
    elif d < 150:
        mv = dir_to_octant(ang + math.pi)
    else:
        mv = dir_to_octant(ang + math.pi / 2)

    # 试剂选择：先看对方化学状态（同向补伤/氧化破甲），再看距离
    ph = target["chem"].ph if target["chem"].ph is not None else 7.0
    ring = target["chem"].ring
    if ph > 8.5 and d < 900:
        want = "lmg"
    elif ph < 5.5 and d < 1200:
        want = "sniper" if d > 700 else "rifle"
    elif ring > 60 and d < 500:
        want = "shotgun"
    elif d < 130:
        want = "shotgun"
    elif d < 450:
        want = "rifle"
    elif d > 1100:
        want = "sniper"
    else:
        want = "rifle"
    if ring < 20 and d < 700:
        want = "rifle"
    if want == "sniper" and p.get("ai_snip_used", 0.0) >= S.AI_SNIPER_WINDOW * S.ai_sniper_max_frac(p):
        want = "rifle"        # 遵守 AI 狙击时间配额（按难度）
    switch = WEAPON_IDS.index(want) if p["weapon"] != want else None

    fire = 1 if (visible and d < 1200 and p["ammo"][p["weapon"]] > 0) else 0
    wdef = S.WEAPONS.get(p["weapon"], {})
    ammo_ratio = p["ammo"][p["weapon"]] / float(wdef.get("mag", 1) or 1)
    reload = 1 if (not p["reloading"] and ammo_ratio <= 0.05 and d > 300) else 0
    indicator = 1 if (p["ind_charges"] > 0 and p["ind_cd"] <= 0 and rng.random() < 0.01) else 0
    return {"move": mv, "aim": aim, "fire": fire, "reload": reload,
            "switch": switch, "indicator": indicator}
