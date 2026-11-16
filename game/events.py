# -*- coding: utf-8 -*-
"""Reaction Protocol · 随机事件系统（独立模块，便于继续加事件）

设计目标
--------
1. **独立**：事件只通过 server.Game 暴露的少量方法与对局交互，不直接改对局内部结构；
2. **可扩展**：加一个新事件 = 在文件末尾写一次 `register_event(...)`，不用动 server.py；
3. **可关闭**：主机面板/环境变量可以关掉，比赛演示时按需开启。

事件回调签名
------------
    on_start(game, now)           事件开始
    on_tick(game, now, dt)        事件进行中每帧（可选）
    on_end(game, now)             事件结束（必须把对局恢复原状）

game 侧可用的接口（server.Game 实现）：
    game.set_all_weapons(w)       所有人强制使用某武器（None = 恢复自由选择）
    game.set_all_buff(key, val)   给所有人加/改 buff：speed / dmg / inf_ammo
    game.clear_buffs()            清空所有事件 buff
    game.spawn_boss()             召唤超级 AI（Boss）
    game.despawn_boss()           移除 Boss
    game.announce(title, desc)    广播一条事件播报
    game.players                  当前玩家字典（只读遍历即可）

新增事件示例（放到文件末尾）：
    def _start_double_speed(game, now):
        game.set_all_buff("speed", 1.8)

    def _end_double_speed(game, now):
        game.clear_buffs()

    register_event("speed_demo", "神速力·演示", "所有人移速 ×1.8", 20.0, 1.0,
                   on_start=_start_double_speed, on_end=_end_double_speed)
"""
import math
import random

# ---------------------------------------------------------------- 注册表
EVENTS = []
EVENTS_BY_ID = {}


class EventDef(object):
    def __init__(self, eid, name, desc, duration, weight, on_start, on_end, on_tick,
                 min_players, kind):
        self.id = eid
        self.name = name
        self.desc = desc
        self.duration = float(duration)
        self.weight = float(weight)
        self.on_start = on_start
        self.on_end = on_end
        self.on_tick = on_tick
        self.min_players = int(min_players)
        self.kind = kind


def register_event(eid, name, desc, duration, weight=1.0, on_start=None, on_end=None,
                   on_tick=None, min_players=1, kind="buff"):
    """注册一个随机事件（重复 id 会覆盖，方便热改）。"""
    ev = EventDef(eid, name, desc, duration, weight, on_start, on_end, on_tick,
                  min_players, kind)
    if eid in EVENTS_BY_ID:
        EVENTS[EVENTS.index(EVENTS_BY_ID[eid])] = ev
    else:
        EVENTS.append(ev)
    EVENTS_BY_ID[eid] = ev
    return ev


# ---------------------------------------------------------------- 事件管理器
class RandomEvents(object):
    """按时间随机触发事件；同一时刻只跑一个，最近两轮不重复。"""

    def __init__(self, enabled=True, interval=(70.0, 110.0), first_delay=45.0):
        self.enabled = bool(enabled)
        self.interval = interval
        self.first_delay = first_delay
        self.current = None          # {"id","name","desc","end","kind"}
        self.next_at = None
        self.history = []            # 最近事件 id
        self.count = 0

    # ---- 外部调用 ----
    def update(self, game, now, dt):
        if not self.enabled:
            return
        if self.next_at is None:
            self.next_at = now + self.first_delay
        cur = self.current
        if cur is not None:
            ev = EVENTS_BY_ID.get(cur["id"])
            if ev is not None and ev.on_tick is not None:
                try:
                    ev.on_tick(game, now, dt)
                except Exception as e:                     # 事件出错不能拖垮主循环
                    print("  [事件] %s tick 异常: %s" % (cur["id"], e))
            if now >= cur["end"]:
                self._finish(game, now, "时间到")
            return
        if now < self.next_at:
            return
        self._start_random(game, now)

    def force_start(self, game, now, eid=None, duration=None):
        """主机面板/测试可以直接指定事件（eid=None 走随机）。"""
        if eid is not None and eid in EVENTS_BY_ID:
            self._start(game, now, EVENTS_BY_ID[eid], duration)
        else:
            self._start_random(game, now)

    def force_end(self, game, now, reason="手动结束"):
        if self.current is not None:
            self._finish(game, now, reason)

    def snapshot(self):
        cur = self.current
        if cur is None:
            return None
        import time as _t
        return {"id": cur["id"], "name": cur["name"], "desc": cur["desc"],
                "kind": cur.get("kind", "buff"),
                "left": round(max(0.0, cur["end"] - _t.time()), 1),
                "extra": cur.get("extra") or {}}

    # ---- 内部 ----
    def _start_random(self, game, now):
        alive = sum(1 for p in game.players.values() if not p.get("dummy"))
        pool = [e for e in EVENTS
                if e.min_players <= alive and e.id not in self.history[-2:]]
        if not pool:
            pool = [e for e in EVENTS if e.min_players <= alive]
        if not pool:
            self.next_at = now + 20.0
            return
        total = sum(e.weight for e in pool)
        pick = random.uniform(0, total)
        acc = 0.0
        chosen = pool[-1]
        for e in pool:
            acc += e.weight
            if pick <= acc:
                chosen = e
                break
        self._start(game, now, chosen)

    def _start(self, game, now, ev, duration=None):
        dur = float(duration if duration else ev.duration)
        self.current = {"id": ev.id, "name": ev.name, "desc": ev.desc,
                        "end": now + dur, "kind": ev.kind, "start": now,
                        "extra": {}}
        self.count += 1
        try:
            if ev.on_start is not None:
                ev.on_start(game, now)
        except Exception as e:
            print("  [事件] %s start 异常: %s" % (ev.id, e))
        game.announce("随机事件 · " + ev.name, ev.desc)
        print("  [事件] 开始：%s（%s，%.0fs）" % (ev.name, ev.desc, dur))

    def _finish(self, game, now, reason):
        cur = self.current
        if cur is None:
            return
        ev = EVENTS_BY_ID.get(cur["id"])
        if ev is not None and ev.on_end is not None:
            try:
                ev.on_end(game, now)
            except Exception as e:
                print("  [事件] %s end 异常: %s" % (cur["id"], e))
        self.history.append(cur["id"])
        self.history = self.history[-4:]
        self.current = None
        self.next_at = now + random.uniform(*self.interval)
        print("  [事件] 结束：%s（%s）" % (ev.name if ev else cur["id"], reason))


# ================================================================ 具体事件
# —— 加新事件只需在下面追加 register_event(...) ——

def _weapon_all(w):
    def start(game, now):
        game.clear_buffs()          # 先清旧 buff，再强制武器（否则 forced_w 会被 clear 掉）
        game.set_all_weapons(w)

    def end(game, now):
        game.set_all_weapons(None)
        game.clear_buffs()
    return start, end


_s, _e = _weapon_all("shotgun")
register_event("all_shotgun", "喷淋器狂欢", "所有人只能用 H₂O₂ 喷淋器（氧化通道，直接剥缓冲）",
               25.0, 1.0, on_start=_s, on_end=_e, kind="weapon")
_s, _e = _weapon_all("knife")
register_event("all_knife", "搅拌棒大乱斗", "所有人只能用搅拌棒（近战，移速 +18%）",
               20.0, 0.9, on_start=_s, on_end=_e, kind="weapon")
_s, _e = _weapon_all("sniper")
register_event("all_sniper", "高氯酸狙击战", "所有人发一把 HClO₄ 狙击（满缓冲一发酸中毒）",
               22.0, 0.7, on_start=_s, on_end=_e, kind="weapon")


def _speed_start(game, now):
    game.clear_buffs()
    game.set_all_buff("speed", 1.7)


def _buff_end(game, now):
    game.clear_buffs()


register_event("super_speed", "神速力", "所有人移动速度 ×1.7，拉枪与走位都更快",
               20.0, 1.0, on_start=_speed_start, on_end=_buff_end, kind="buff")


def _ammo_start(game, now):
    game.clear_buffs()
    game.set_all_buff("inf_ammo", 1)


register_event("infinite_ammo", "无限弹药", "不用换弹、备弹不消耗，火力全开",
               20.0, 0.8, on_start=_ammo_start, on_end=_buff_end, kind="buff")


def _dmg_start(game, now):
    game.clear_buffs()
    game.set_all_buff("dmg", 1.5)


register_event("overcharge", "过载", "所有人伤害 ×1.5（化学平衡被打破）",
               18.0, 0.9, on_start=_dmg_start, on_end=_buff_end, kind="buff")


# ---------------- Boss 战：超级 AI ----------------
BOSS_CFG = {
    "name": "液氮守卫 BOSS",
    "shield": 2600.0,        # 额外护盾（先扣护盾，再扣缓冲条）
    "hp_mul": 4.5,           # 缓冲环上限倍率（血条更厚）
    "speed": 245.0,          # 移动速度（玩家 230；Boss 比玩家更快，压迫感更强）
    "dmg_mul": 1.8,          # 伤害倍率
    "fire_rate_mul": 0.60,   # 开火间隔倍率（越小越快）
    "abilities": {
        "smoke": 6.5,        # 每隔几秒在目标脚下放烟
        "aoe": 11.0,         # 每隔几秒开 AOE
        "indicator": 8.0,    # 每隔几秒指示剂
    },
}


def _boss_start(game, now):
    game.clear_buffs()
    game.spawn_boss(BOSS_CFG)


def _boss_end(game, now):
    game.despawn_boss()


def _boss_tick(game, now, dt):
    game.boss_tick(now, dt)


register_event("boss", "Boss 战：液氮守卫", "超级 AI 登场：厚血 + 0 帧拉枪 + 道具连发，全员合力击杀",
               90.0, 0.9, on_start=_boss_start, on_end=_boss_end, on_tick=_boss_tick,
               min_players=2, kind="boss")

# ================================================================ 小游戏派对
# 参考《Minecraft》小游戏派对：每局一个短平快的玩法，全部走 server.Game 的公共接口。
POTATO_FUSE = 3.8          # 山芋在手里能拿几秒
POTATO_R = 210.0           # 爆炸半径
POTATO_PASS_R = 86.0       # 贴到多近可以传给别人
LAVA_SAFE_R = 330.0        # 岩浆地板的安全圈半径
LAVA_DPS = 9.0             # 圈外每秒掉多少（走氧化通道，直接剥缓冲）


def _party_alive(game):
    return [p for p in game.players.values()
            if p.get("alive") and not p.get("dummy") and not p.get("tut")]


def _party_wh(game):
    try:
        w, h = game.world_size()
        return float(w), float(h)
    except Exception:
        return 2400.0, 1600.0


def _potato_pick(game, extra):
    """随机选一个持有者，并把山芋重新点火。"""
    alive = _party_alive(game)
    if not alive:
        extra["car"] = None
        extra["fuse"] = 0.0
        return None
    p = random.choice(alive)
    extra["car"] = p["id"]
    extra["fuse"] = POTATO_FUSE
    extra["cool"] = 1.0
    extra["x"] = round(p["x"], 1)
    extra["y"] = round(p["y"], 1)
    game.events.append({"t": "potato", "pid": p["id"], "car": p["id"],
                        "x": extra["x"], "y": extra["y"], "r": POTATO_R})
    return p


def _potato_start(game, now):
    game.clear_buffs()
    extra = {"car": None, "fuse": 0.0, "cool": 0.0, "x": 0.0, "y": 0.0,
             "r": POTATO_R, "fuse_max": POTATO_FUSE}
    game.rnd_events.current["extra"] = extra
    p = _potato_pick(game, extra)
    if p is not None:
        game.announce("烫手山芋", "山芋在 %s 手上！贴住别人可以把山芋传出去" % p["name"])


def _potato_tick(game, now, dt):
    extra = game.rnd_events.current.get("extra") or {}
    car = game.players.get(extra.get("car"))
    if car is None or not car.get("alive"):
        _potato_pick(game, extra)
        return
    extra["cool"] = max(0.0, float(extra.get("cool", 0.0)) - dt)
    extra["fuse"] = float(extra.get("fuse", 0.0)) - dt
    extra["x"] = round(car["x"], 1)
    extra["y"] = round(car["y"], 1)
    if extra["cool"] <= 0.0:
        best = None
        bd = 1e9
        for q in _party_alive(game):
            if q is car:
                continue
            d = math.hypot(q["x"] - car["x"], q["y"] - car["y"])
            if d <= POTATO_PASS_R and d < bd:
                best, bd = q, d
        if best is not None:
            extra["car"] = best["id"]
            extra["cool"] = 1.2
            game.events.append({"t": "potato_pass", "from": car["id"], "pid": best["id"],
                                "x": round(best["x"], 1), "y": round(best["y"], 1), "r": POTATO_R})
            game.feed_line("烫手山芋", car["team"], "传给了 " + best["name"], best["team"],
                           "potato", now, 3.0)
    if extra["fuse"] <= 0.0:
        game.events.append({"t": "potato_boom", "pid": car["id"],
                            "x": round(car["x"], 1), "y": round(car["y"], 1), "r": POTATO_R})
        game.feed_line("烫手山芋", car["team"], "炸了！", car["team"], "potato", now, 4.0)
        for q in list(game.players.values()):
            if not q.get("alive") or q.get("dummy") or q.get("tut"):
                continue
            if math.hypot(q["x"] - car["x"], q["y"] - car["y"]) > POTATO_R:
                continue
            if q is car:
                game._damage(None, q, 95.0, "potato", now)
            else:
                game._damage(car["id"], q, 70.0, "potato", now)
        _potato_pick(game, extra)


def _potato_end(game, now):
    extra = (game.rnd_events.current or {}).get("extra") or {}
    extra["car"] = None
    game.announce("烫手山芋结束", "山芋被收走了，喘口气")


# ---------------- 岩浆地板：只有安全圈里不受伤 ----------------
def _lava_start(game, now):
    game.clear_buffs()
    w, h = _party_wh(game)
    cx = w * 0.5 + random.uniform(-160.0, 160.0)
    cy = h * 0.5 + random.uniform(-120.0, 120.0)
    r = min(LAVA_SAFE_R, min(w, h) * 0.28)
    extra = {"x": round(cx, 1), "y": round(cy, 1), "r": round(r, 1),
             "acc": 0.0, "dps": LAVA_DPS}
    game.rnd_events.current["extra"] = extra
    game.announce("岩浆地板", "地板开始沸腾！只有中央安全圈里不受伤")


def _lava_tick(game, now, dt):
    extra = game.rnd_events.current.get("extra") or {}
    acc = float(extra.get("acc", 0.0)) + dt
    if acc < 0.5:
        extra["acc"] = acc
        return
    step = acc
    extra["acc"] = 0.0
    cx, cy, r = extra["x"], extra["y"], extra["r"]
    for p in list(game.players.values()):
        if not p.get("alive") or p.get("dummy") or p.get("tut") or p.get("is_boss"):
            continue
        if math.hypot(p["x"] - cx, p["y"] - cy) <= r:
            p["lava_safe"] = True
            continue
        p["lava_safe"] = False
        game._damage(None, p, LAVA_DPS * step, "lava", now)


def _lava_end(game, now):
    for p in game.players.values():
        p.pop("lava_safe", None)
    game.announce("岩浆地板结束", "地板冷却了")


# ---------------- 抢椅子：中央椅子占点得分 ----------------
def _chairs_start(game, now):
    game.clear_buffs()
    w, h = _party_wh(game)
    cx, cy = w * 0.5, h * 0.5
    spots = [(cx - 260, cy), (cx, cy - 210), (cx, cy + 210)]
    extra = {"chairs": [[round(x, 1), round(y, 1), None] for x, y in spots],
             "acc": 0.0, "win": 0, "winner": ""}
    game.rnd_events.current["extra"] = extra
    for p in game.players.values():
        p["chair_pts"] = 0
    game.announce("抢椅子", "中央出现 3 把椅子：站上去每 2 秒加 120 分")


def _chairs_tick(game, now, dt):
    extra = game.rnd_events.current.get("extra") or {}
    chairs = extra.get("chairs") or []
    if not chairs:
        return
    extra["acc"] = float(extra.get("acc", 0.0)) + dt
    occ = [None] * len(chairs)
    for p in _party_alive(game):
        best = None
        bd = 62.0
        for i, c in enumerate(chairs):
            d = math.hypot(p["x"] - c[0], p["y"] - c[1])
            if d <= bd:
                best, bd = i, d
        if best is not None:
            occ[best] = p["id"]
    for i, c in enumerate(chairs):
        c[2] = occ[i]
    if extra["acc"] >= 2.0:
        extra["acc"] = 0.0
        for i, c in enumerate(chairs):
            owner = c[2]
            if owner is None:
                continue
            p = game.players.get(owner)
            if p is None or not p.get("alive"):
                continue
            p["chair_pts"] = int(p.get("chair_pts", 0)) + 120
            game._add_score(p, 120)
            game.events.append({"t": "chair", "pid": p["id"], "x": c[0], "y": c[1], "v": 120})
            if p["chair_pts"] >= 600 and not extra.get("winner"):
                extra["winner"] = p["name"]
                extra["win"] = p["id"]


def _chairs_end(game, now):
    extra = (game.rnd_events.current or {}).get("extra") or {}
    winner = extra.get("winner") or ""
    if winner:
        game.announce("抢椅子结束", "%s 抢到了最多的椅子" % winner)
    else:
        game.announce("抢椅子结束", "椅子没人坐满，加时再见")


register_event("hot_potato", "烫手山芋", "山芋在一个人手上，贴住别人可以传出去，炸了就扣一大条缓冲",
               26.0, 1.0, on_start=_potato_start, on_end=_potato_end,
               on_tick=_potato_tick, min_players=2, kind="party")
register_event("lava_floor", "岩浆地板", "地板沸腾，只有中央安全圈里不受伤，逼所有人挤在一起",
               24.0, 0.9, on_start=_lava_start, on_end=_lava_end,
               on_tick=_lava_tick, min_players=2, kind="party")
register_event("musical_chairs", "抢椅子", "中央 3 把椅子，站上去每 2 秒加 120 分",
               30.0, 0.8, on_start=_chairs_start, on_end=_chairs_end,
               on_tick=_chairs_tick, min_players=2, kind="party")

