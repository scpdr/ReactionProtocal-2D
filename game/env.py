# -*- coding: utf-8 -*-
"""AI 训练/评测环境（纯标准库，依赖 server.Game）。

包含：
- DuoEnv：1v1 双人受控（保留给自博弈/对照）
- BotDuelEnv：1v1 对 FSM 机器人（基础评测）
- TeamDuelEnv：2v2 带队友（红方：受控 AI + 队友机器人；蓝方：两名 FSM），
  用于训练“救队友”行为。

动作（6 个离散头）：
    move 0..8
    aim  0..2=视野内第 1/2/3 近敌人；3..4=视野内第 1/2 近队友；5=保持
    fire/reload/switch(0..7)/indicator

观测 53 维：自身14 + 武器one-hot8 + 敌人3×6 + 队友2×6 + 时间1。
"""
import json
import math
import os
import random
import time

import server as S

DT = 1.0 / S.TICK
AIM_TARGETS = 6
WEAPON_IDS = ["knife", "pistol", "rifle", "sniper", "shotgun", "lmg", "limewater", "vitc"]
MOVE_DIRS = [None,
             (0, -1), (1, -1), (1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1)]
ACTION_KEYS = ["move", "aim", "fire", "reload", "switch", "indicator"]
ACTION_SIZES = [9, AIM_TARGETS, 2, 2, 8, 2]
OBS_DIM = 56
MAX_ALLY = 2
MAX_ENEMY = 3


def dir_to_octant(angle):
    idx = (int(round(angle / (math.pi / 4))) + 3) % 8
    return 8 if idx == 0 else idx


def angle_to_aim(angle):
    return 0


def _visible(game, p):
    enemies, allies = [], []
    for q in game.players.values():
        if q["id"] == p["id"] or not q["alive"]:
            continue
        d = math.hypot(q["x"] - p["x"], q["y"] - p["y"])
        if not game._can_see(p["x"], p["y"], q["x"], q["y"]):
            continue
        if q["team"] == p["team"]:
            allies.append((d, q))
        else:
            enemies.append((d, q))
    enemies.sort(key=lambda x: x[0])
    allies.sort(key=lambda x: x[0])
    return [q for _, q in enemies], [q for _, q in allies]


def channel_reward(ev):
    """命中后的化学奖励塑形：鼓励同向补伤、氧化破甲，惩罚反向中和。"""
    ch = ev.get("ch")
    ph = ev.get("ph")
    buf = ev.get("buf")
    if ch == "acid":
        if ph is not None and ph > 8.5:
            return -0.05
        if buf is not None and buf > 60.0:
            return -0.06
        if ph is not None and ph < 6.0:
            return 0.05
    elif ch == "base":
        if ph is not None and ph > 8.5:
            return 0.05
        if ph is not None and ph < 5.5:
            return -0.05
    elif ch == "oxidizing":
        if buf is not None and buf > 60.0:
            return 0.08
        if buf is not None and buf < 25.0:
            return -0.04
    return 0.0


def encode_obs(game, pid, step_i, max_steps):
    """53 维观测：自身/武器/敌人/队友/时间。"""
    me = game.players[pid]
    W, H = S.WORLD_W, S.WORLD_H
    mag = S.WEAPONS[me["weapon"]].get("mag", 0) or 1
    vec = [
        me["x"] / W, me["y"] / H,
        math.sin(me["angle"]), math.cos(me["angle"]),
        1.0 if me["alive"] else 0.0,
        me["hp"] / 100.0,
        me["chem"].ring / 100.0,
        ((me["chem"].ph or 7.0) - 7.0) / 4.0,
        me["chem"].l_mmol / 100.0,
        me["chem"].d_mmol / 100.0,
        1.0 if me["reloading"] else 0.0,
        me["ammo"][me["weapon"]] / float(mag),
        me["ind_charges"] / 2.0,
        me["reserve"].get(me["weapon"], 0) / float(max(1, S.WEAPON_RESERVE.get(me["weapon"], 1))),
    ]
    onehot = [0.0] * len(WEAPON_IDS)
    if me["weapon"] in WEAPON_IDS:
        onehot[WEAPON_IDS.index(me["weapon"])] = 1.0
    vec += onehot

    # 敌人（最多 3）
    enemies = []
    for q in game.players.values():
        if q["id"] == pid or q["team"] == me["team"] or not q["alive"]:
            continue
        d = math.hypot(q["x"] - me["x"], q["y"] - me["y"])
        vis = 1.0 if game._can_see(me["x"], me["y"], q["x"], q["y"]) else 0.0
        enemies.append((d, q, vis))
    enemies.sort(key=lambda x: x[0])
    for i in range(MAX_ENEMY):
        if i < len(enemies):
            d, q, vis = enemies[i]
            vec += [(q["x"] - me["x"]) / 2000.0, (q["y"] - me["y"]) / 2000.0,
                    d / 2000.0, (q["chem"].ph or 7.0) / 14.0,
                    q["chem"].ring / 100.0, vis]
        else:
            vec += [0.0] * 6

    # 队友（最多 2）：AI 救援需要知道队友 pH/缓冲
    allies = []
    for q in game.players.values():
        if q["id"] == pid or q["team"] != me["team"] or not q["alive"]:
            continue
        d = math.hypot(q["x"] - me["x"], q["y"] - me["y"])
        vis = 1.0 if game._can_see(me["x"], me["y"], q["x"], q["y"]) else 0.0
        allies.append((d, q, vis))
    allies.sort(key=lambda x: x[0])
    for i in range(MAX_ALLY):
        if i < len(allies):
            d, q, vis = allies[i]
            vec += [(q["x"] - me["x"]) / 2000.0, (q["y"] - me["y"]) / 2000.0,
                    d / 2000.0, (q["chem"].ph or 7.0) / 14.0,
                    q["chem"].ring / 100.0, vis]
        else:
            vec += [0.0] * 6

    # 最近的弹药补给点（低弹量决策用）
    if S.AMMO_PADS:
        pad = min(S.AMMO_PADS, key=lambda a: (a[0] - me["x"]) ** 2 + (a[1] - me["y"]) ** 2)
        d = math.hypot(pad[0] - me["x"], pad[1] - me["y"])
        vec += [(pad[0] - me["x"]) / 2000.0, (pad[1] - me["y"]) / 2000.0, d / 2000.0]
    else:
        vec += [0.0] * 3

    vec.append(max(0.0, 1.0 - step_i / float(max_steps)))
    assert len(vec) == OBS_DIM, (len(vec), OBS_DIM)
    return vec


class TelemetryWriter:
    def __init__(self, path=None):
        self.f = None
        if path:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            self.f = open(path, "a", encoding="utf-8")

    def write(self, t, step, agents, actions, events):
        if self.f is None:
            return
        rec = {"t": round(t, 3), "step": step, "agents": agents,
               "actions": actions, "events": events}
        self.f.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")

    def close(self):
        if self.f:
            self.f.close()
            self.f = None


class BaseEnv:
    def __init__(self, max_steps=900, telemetry=None, seed=None):
        self.max_steps = int(max_steps)
        self.telemetry = TelemetryWriter(telemetry)
        self._t = 0.0
        self._step_i = 0
        self._done = False
        self._seed = seed
        if seed is not None:
            random.seed(seed)
        self.game = None

    @property
    def done(self):
        return self._done

    def _apply_action(self, pid, act):
        g = self.game
        p = g.players.get(pid)
        if p is None or not p["alive"]:
            return
        move = int(act.get("move", 0))
        if 0 < move < len(MOVE_DIRS):
            dx, dy = MOVE_DIRS[move]
            p["in"] = [int(dy < 0), int(dx > 0), int(dy > 0), int(dx < 0)]
        else:
            p["in"] = [0, 0, 0, 0]
        aim = int(act.get("aim", 5)) % AIM_TARGETS
        vis_e, vis_a = _visible(g, p)
        target = None
        if 0 <= aim < 3 and aim < len(vis_e):
            target = vis_e[aim]
        elif 3 <= aim < 5 and (aim - 3) < len(vis_a):
            target = vis_a[aim - 3]
        if target is not None:
            p["ax"], p["ay"] = target["x"], target["y"]
        else:
            p["ax"] = p["x"] + math.cos(p["angle"]) * 400.0
            p["ay"] = p["y"] + math.sin(p["angle"]) * 400.0
        p["shoot"] = bool(act.get("fire", 0))
        if act.get("reload"):
            g._start_reload(p, self._t)
        sw = act.get("switch")
        if sw is not None and 0 <= int(sw) < len(WEAPON_IDS):
            want = WEAPON_IDS[int(sw)]
            if want == "sniper" and not S.ai_sniper_available(p, self._t):
                pass                       # AI 狙击时间配额不足
            else:
                p["weapon"] = want
                p["reloading"] = False
        if act.get("indicator"):
            g.use_indicator(p, self._t)

    def _obs(self, pid):
        return encode_obs(self.game, pid, self._step_i, self.max_steps)

    def _agent_snapshot(self, pid):
        p = self.game.players[pid]
        return {"x": round(p["x"], 1), "y": round(p["y"], 1),
                "ph": p["chem"].snapshot()["ph"], "ring": p["chem"].snapshot()["ring"],
                "weapon": p["weapon"], "kills": p["kills"]}

    def close(self):
        self.telemetry.close()


class DuoEnv(BaseEnv):
    """1v1：红/蓝两名受控玩家。"""

    def __init__(self, bots=0, bot_diff="normal", max_steps=900,
                 telemetry=None, seed=None, linear=False, counter=True):
        BaseEnv.__init__(self, max_steps=max_steps, telemetry=telemetry, seed=seed)
        self.bots = int(bots)
        self.bot_diff = bot_diff
        self.linear = bool(linear)
        self.counter = bool(counter)
        self.agent_ids = []
        self._last_actions = None

    def reset(self):
        S.CHEM = S.ChemModel()
        S.CHEM.cfg.LINEAR_HP = self.linear
        S.CHEM.cfg.COUNTER_RULES = self.counter and not self.linear
        S.CHEM.cfg.RING_IS_RESERVE = self.counter and not self.linear
        self.game = S.Game()
        if self.bots:
            self.game.set_bots(self.bots, self.bot_diff)
        a = self.game.add_player("AGENT_RED", "red")
        b = self.game.add_player("AGENT_BLUE", "blue")
        self.agent_ids = [a["id"], b["id"]]
        for pid in self.agent_ids:
            self.game.players[pid]["ai_aim"] = True
        self._t = time.time()
        self._step_i = 0
        self._done = False
        return [self._obs(pid) for pid in self.agent_ids]

    def step(self, actions):
        g = self.game
        self._last_actions = actions
        before_score = {pid: g.players[pid]["score"] for pid in self.agent_ids}
        before_deaths = {pid: g.players[pid]["deaths"] for pid in self.agent_ids}
        for pid, act in zip(self.agent_ids, actions):
            self._apply_action(pid, act)
        self._t += DT
        self._step_i += 1
        g.update(self._t, DT)
        events = g.events
        g.events = []
        rewards, won = [], [0.0, 0.0]
        for pid in self.agent_ids:
            p = g.players[pid]
            r = (p["score"] - before_score[pid]) * 0.01
            r -= (p["deaths"] - before_deaths[pid]) * 1.0
            for ev in events:
                if ev.get("t") == "kill" and ev.get("kid") == pid:
                    r += 1.0
                elif ev.get("t") in ("neutralize", "restore") and ev.get("pid") == pid:
                    r += 0.8
                elif ev.get("t") == "hit" and ev.get("for") == pid:
                    r += channel_reward(ev)
            rewards.append(r)
        kills = {pid: g.players[pid]["kills"] for pid in self.agent_ids}
        if max(kills.values()) >= 5 or self._step_i >= self.max_steps:
            self._done = True
            if kills[self.agent_ids[0]] > kills[self.agent_ids[1]]:
                won = [1.0, -1.0]
            elif kills[self.agent_ids[1]] > kills[self.agent_ids[0]]:
                won = [-1.0, 1.0]
            rewards = [r + w * 5.0 for r, w in zip(rewards, won)]
        self._log(events)
        return [self._obs(pid) for pid in self.agent_ids], rewards, self._done, \
            {"kills": kills, "events": events[-40:], "won": won}

    def _log(self, events):
        if not self.telemetry:
            return
        agents = {str(pid): self._agent_snapshot(pid) for pid in self.agent_ids}
        self.telemetry.write(self._t, self._step_i, agents,
                             self._last_actions, events[-40:])


class BotDuelEnv(BaseEnv):
    """1v1：红方受控，蓝方 FSM（基础评测）。"""

    def __init__(self, difficulty="normal", max_steps=900, telemetry=None, seed=None,
                 linear=False, counter=True):
        BaseEnv.__init__(self, max_steps=max_steps, telemetry=telemetry, seed=seed)
        self.difficulty = difficulty
        self.linear = bool(linear)
        self.counter = bool(counter)
        self.agent_id = None
        self.bot_id = None

    def reset(self):
        S.CHEM = S.ChemModel()
        S.CHEM.cfg.LINEAR_HP = self.linear
        S.CHEM.cfg.COUNTER_RULES = self.counter and not self.linear
        S.CHEM.cfg.RING_IS_RESERVE = self.counter and not self.linear
        self.game = S.Game()
        self.game.bot_diff = self.difficulty
        self.agent_id = self.game.add_player("AGENT", "red")["id"]
        self.bot_id = self.game.add_player("FSM", "blue", bot=True)["id"]
        self.game.players[self.agent_id]["ai_aim"] = True
        self._t = time.time()
        self._step_i = 0
        self._done = False
        return self._obs(self.agent_id)

    def step(self, action):
        g = self.game
        p = g.players[self.agent_id]
        before_score = p["score"]
        before_deaths = p["deaths"]
        self._apply_action(self.agent_id, action)
        self._t += DT
        self._step_i += 1
        g.update(self._t, DT)
        events = g.events
        g.events = []
        r = (p["score"] - before_score) * 0.01 - (p["deaths"] - before_deaths) * 1.0
        for ev in events:
            if ev.get("t") == "kill" and ev.get("kid") == self.agent_id:
                r += 1.0
            elif ev.get("t") in ("neutralize", "restore") and ev.get("pid") == self.agent_id:
                r += 0.8
            elif ev.get("t") == "hit" and ev.get("for") == self.agent_id:
                r += channel_reward(ev)
        kills = {self.agent_id: p["kills"], self.bot_id: g.players[self.bot_id]["kills"]}
        if max(kills.values()) >= 5 or self._step_i >= self.max_steps:
            self._done = True
            if kills[self.agent_id] > kills[self.bot_id]:
                r += 5.0
            elif kills[self.agent_id] < kills[self.bot_id]:
                r -= 5.0
        if self.telemetry:
            agents = {str(pid): self._agent_snapshot(pid)
                      for pid in (self.agent_id, self.bot_id)}
            self.telemetry.write(self._t, self._step_i, agents, action, events[-40:])
        return self._obs(self.agent_id), r, self._done, \
            {"kills": kills, "events": events[-40:],
             "agent_id": self.agent_id, "bot_id": self.bot_id}


class TeamDuelEnv(BaseEnv):
    """2v2：红方受控 AI + 红方 FSM 队友，对两名蓝方 FSM。用于训练救援。"""

    def __init__(self, difficulty="normal", max_steps=900, telemetry=None, seed=None,
                 linear=False, counter=True):
        BaseEnv.__init__(self, max_steps=max_steps, telemetry=telemetry, seed=seed)
        self.difficulty = difficulty
        self.linear = bool(linear)
        self.counter = bool(counter)
        self.agent_id = None
        self.ally_id = None

    def reset(self):
        S.CHEM = S.ChemModel()
        S.CHEM.cfg.LINEAR_HP = self.linear
        S.CHEM.cfg.COUNTER_RULES = self.counter and not self.linear
        S.CHEM.cfg.RING_IS_RESERVE = self.counter and not self.linear
        self.game = S.Game()
        self.game.bot_diff = self.difficulty
        self.agent_id = self.game.add_player("AGENT", "red")["id"]
        self.ally_id = self.game.add_player("ALLY", "red", bot=True)["id"]
        self.game.add_player("E1", "blue", bot=True)
        self.game.add_player("E2", "blue", bot=True)
        self.game.players[self.agent_id]["ai_aim"] = True
        self.game.players[self.agent_id]["ai_sniper_frac"] = S.AI_SNIPER_FRAC_BY_DIFF.get(
            self.difficulty, S.AI_SNIPER_MAX_FRAC)
        # 课程：队友放在附近并预设负荷，保证 AI 能看到/练习救援
        ally = self.game.players[self.ally_id]
        ax, ay = self.game.players[self.agent_id]["x"], self.game.players[self.agent_id]["y"]
        if not S.circle_hits_walls(ax + 90, ay):
            ally["x"], ally["y"] = ax + 90, ay
        if random.random() < 0.6:
            if random.random() < 0.5:
                ally["chem"].acid = random.uniform(0.035, 0.045)
            else:
                ally["chem"].base = random.uniform(0.035, 0.045)
            ally["chem"]._invalidate()
            ally["chem"]._refresh()
            ally["hp"] = round(ally["chem"].ring, 1)
        self._t = time.time()
        self._step_i = 0
        self._done = False
        self._ally_alive = True
        return self._obs(self.agent_id)

    def step(self, action):
        g = self.game
        p = g.players[self.agent_id]
        before_score = p["score"]
        before_deaths = p["deaths"]
        before_ally = g.players[self.ally_id]["alive"]
        self._apply_action(self.agent_id, action)
        self._t += DT
        self._step_i += 1
        g.update(self._t, DT)
        events = g.events
        g.events = []
        r = (p["score"] - before_score) * 0.01 - (p["deaths"] - before_deaths) * 1.0
        for ev in events:
            if ev.get("t") == "kill" and ev.get("kid") == self.agent_id:
                r += 1.0
            elif ev.get("t") in ("neutralize", "restore") and ev.get("pid") == self.agent_id:
                r += 2.0       # 救援：重点鼓励
            elif ev.get("t") == "hit" and ev.get("for") == self.agent_id:
                r += channel_reward(ev)
            elif ev.get("t") == "supply" and ev.get("pid") == self.agent_id:
                r += 0.4       # 低弹量时主动补给
        ally = g.players[self.ally_id]
        if before_ally and not ally["alive"]:
            r -= 1.0                 # 队友阵亡
        red_kills = p["kills"] + ally["kills"]
        blue_kills = sum(q["kills"] for q in g.players.values() if q["team"] == "blue")
        if red_kills >= 8 or blue_kills >= 8 or self._step_i >= self.max_steps:
            self._done = True
            if ally["alive"]:
                r += 2.0             # 队友活到结束：救援/保护奖励
            if red_kills > blue_kills:
                r += 5.0
            elif red_kills < blue_kills:
                r -= 5.0
        if self.telemetry:
            agents = {str(pid): self._agent_snapshot(pid)
                      for pid in [self.agent_id, self.ally_id] +
                      [q["id"] for q in g.players.values() if q["team"] == "blue" and q["bot"]]}
            self.telemetry.write(self._t, self._step_i, agents, action, events[-40:])
        return self._obs(self.agent_id), r, self._done, \
            {"red_kills": red_kills, "blue_kills": blue_kills,
             "agent_id": self.agent_id, "ally_id": self.ally_id,
             "ally_alive": ally["alive"], "events": events[-40:]}
