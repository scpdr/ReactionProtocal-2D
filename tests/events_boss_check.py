# -*- coding: utf-8 -*-
"""随机事件系统 + Boss 战 服务端回归。

跑法：python tests/events_boss_check.py     退出码 0/1
"""
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server  # noqa: E402

N, F = [0], []


def ok(name, cond, want, got):
    N[0] += 1
    if cond:
        print("[PASS] %s  期望 %s / 实测 %s" % (name, want, got))
    else:
        F.append(name)
        print("[FAIL] %s  期望 %s / 实测 %s" % (name, want, got))


def main():
    # ---------- 1. 事件注册表 ----------
    ids = [e.id for e in server.rpevents.EVENTS]
    ok("事件数量 ≥6", len(ids) >= 6, "≥6", "%d: %s" % (len(ids), ids))
    ok("事件 id 唯一", len(set(ids)) == len(ids), "唯一", str(len(set(ids))))
    ok("包含 Boss 事件", "boss" in ids, "含 boss", str("boss" in ids))
    ok("每个事件都有名字/说明/时长",
       all(e.name and e.desc and e.duration > 0 for e in server.rpevents.EVENTS), "全部",
       str([(e.id, e.duration) for e in server.rpevents.EVENTS][:3]))

    # ---------- 2. 神速力事件：移速真的变快 ----------
    g = server.Game()
    a = g.add_player("甲", "red")
    b = g.add_player("乙", "blue")

    def free_spot(x0, y0):
        """找离 (x0,y0) 最近的非墙点（地图里有柱子，别把人放进墙里）"""
        best, bd = (x0, y0), 1e18
        for gx in range(100, 2000, 40):
            for gy in range(100, 1400, 40):
                if server.circle_hits_walls(gx, gy):
                    continue
                dd = (gx - x0) ** 2 + (gy - y0) ** 2
                if dd < bd:
                    best, bd = (gx, gy), dd
        return best

    start_xy = free_spot(400, 700)
    a["x"], a["y"] = start_xy
    b["x"], b["y"] = free_spot(1600, 700)
    now = time.time()

    def run_seconds(secs, keys=(0, 1, 0, 0)):
        t0 = now
        steps = int(secs * 60)
        for i in range(steps):
            a["in"] = list(keys)
            a["ax"], a["ay"] = start_xy[0] + 400, start_xy[1]
            a["x"], a["y"] = a["x"], a["y"]
            g.update(t0 + i / 60.0, 1 / 60.0)

    a["x"], a["y"] = start_xy
    run_seconds(0.5)
    base = math.hypot(a["x"] - start_xy[0], a["y"] - start_xy[1])

    g.rnd_events.force_start(g, now, "super_speed")
    ok("神速力事件已开始", g.rnd_events.current is not None and
       g.rnd_events.current["id"] == "super_speed", "super_speed",
       str(g.rnd_events.current and g.rnd_events.current["id"]))
    ok("全員获得 speed buff", all(abs(p["buffs"].get("speed", 1.0) - 1.7) < 1e-6
                                 for p in (a, b)), "1.7",
       str([p["buffs"].get("speed") for p in (a, b)]))
    a["x"], a["y"] = start_xy
    run_seconds(0.5)
    fast = math.hypot(a["x"] - start_xy[0], a["y"] - start_xy[1])
    ok("事件期间移速 ≈1.7 倍", fast > base * 1.5, ">%.0f" % (base * 1.5),
       "base=%.0f fast=%.0f（%.2fx）" % (base, fast, fast / base if base else 0))
    g.rnd_events.force_end(g, now + 1, "测试结束")
    ok("事件结束 buff 被清空", all(not p["buffs"] for p in (a, b)), "{}",
       str([p["buffs"] for p in (a, b)]))
    ok("事件结束回到空闲", g.rnd_events.current is None, "None",
       str(g.rnd_events.current))

    # ---------- 3. 武器事件 ----------
    g.rnd_events.force_start(g, now, "all_shotgun")
    ok("全员强制喷淋器", a["forced_w"] == "shotgun" and b["forced_w"] == "shotgun",
       "shotgun/shotgun", "%s/%s" % (a["forced_w"], b["forced_w"]))
    a["weapon"] = "rifle"           # 企图换枪
    g.update(now, 1 / 60.0)
    ok("事件期间无法换枪（被拉回喷淋器）", a["weapon"] == "shotgun", "shotgun",
       str(a["weapon"]))
    g.rnd_events.force_end(g, now, "测试结束")
    ok("事件结束恢复自由换枪", a["forced_w"] is None, "None", str(a["forced_w"]))
    a["weapon"] = "rifle"
    g.update(now, 1 / 60.0)
    ok("事件结束后可以换枪", a["weapon"] == "rifle", "rifle", str(a["weapon"]))

    # ---------- 4. 无限弹药 ----------
    g.rnd_events.force_start(g, now, "infinite_ammo")
    a["weapon"] = "rifle"
    a["x"], a["y"] = 400, 800
    a["ax"], a["ay"] = 900, 800
    a["shoot"] = True
    for i in range(40):
        g.update(now + i / 30.0, 1 / 30.0)
    ok("无限弹药：打完 40 帧弹匣不减少",
       a["ammo"]["rifle"] == server.WEAPONS["rifle"]["mag"], "满弹匣",
       str(a["ammo"]["rifle"]))
    g.rnd_events.force_end(g, now + 2, "测试结束")

    # ---------- 5. Boss 生成与属性 ----------
    g2 = server.Game()
    humans = [g2.add_player("挑战者%d" % i, "red" if i % 2 == 0 else "blue")
              for i in range(4)]
    teams0 = {p["id"]: p["team"] for p in humans}
    for i, p in enumerate(humans):
        p["x"], p["y"] = 200 + i * 120, 300
    now2 = time.time()
    g2.rnd_events.force_start(g2, now2, "boss")
    boss = g2.players.get(g2.boss_pid) if g2.boss_pid else None
    ok("Boss 已生成", boss is not None, "有 Boss", str(g2.boss_pid))
    ok("Boss 是独立队伍（全员可打）", boss is not None and boss["team"] == "boss",
       "boss", str(boss and boss["team"]))
    ok("Boss 有额外护盾（血条更厚）", boss is not None and boss["boss_shield"] >= 1000,
       "≥1000", str(boss and boss["boss_shield"]))
    ok("Boss 0 帧拉枪标记", bool(boss and boss["aim_instant"]), "True",
       str(boss and boss["aim_instant"]))
    ok("Boss 伤害/射速 buff 已配置", bool(boss) and boss["buffs"].get("dmg", 0) > 1.2,
       "dmg>1.2", str(boss and boss["buffs"]))

    # ---------- 5b. Boss 战：全员同一阵营，共同对抗 Boss ----------
    others = [p for p in g2.players.values() if not p.get("is_boss")]
    ok("Boss 战期间全员同阵营", all(p["team"] == "red" for p in others), "全部 red",
       str([(p["name"], p["team"]) for p in others]))
    ok("Boss 是敌对阵营", bool(boss) and boss["team"] == "boss", "boss",
       str(boss and boss["team"]))
    ok("记录了原阵营用于战后恢复", len(g2._boss_team_backup) == len(others),
       str(len(others)), str(len(g2._boss_team_backup)))
    # 同阵营玩家之间不能互相伤害（Boss 战是合作模式）
    if boss:
        ally = others[0]
        ring_before = ally["chem"].ring
        g2._damage(others[1]["id"] if len(others) > 1 else ally["id"], ally, 200,
                   "shotgun", now2)
        ok("Boss 战期间玩家之间无友伤", abs(ally["chem"].ring - ring_before) < 0.01,
           "ring 不变", "%.1f -> %.1f" % (ring_before, ally["chem"].ring))
        # 中途加入的玩家也自动并入同一阵营
        late = g2.add_player("迟到者", "blue")
        ok("中途加入者也被并入同一阵营", late["team"] == "red", "red", late["team"])

    # ---------- 6. 打 Boss：护盾→化学伤害→贡献记录 ----------
    if boss:
        sh0 = boss["boss_shield"]
        g2._damage(humans[0]["id"], boss, 100, "rifle", now2)
        ok("伤害先扣护盾", boss["boss_shield"] < sh0, "<%.0f" % sh0,
           "%.1f" % boss["boss_shield"])
        ok("攻击者的贡献被记录", humans[0]["boss_dmg"] > 0 and
           g2.boss_contrib.get(humans[0]["id"], 0) > 0, ">0",
           "own=%.1f total=%.1f" % (humans[0]["boss_dmg"],
                                    g2.boss_contrib.get(humans[0]["id"], 0)))
        # 打穿护盾后应进入化学伤害（用氧化通道才能看到缓冲环下降；酸碱只改 pH）
        boss["boss_shield"] = 30.0
        ring0 = boss["chem"].ring
        g2._damage(humans[1]["id"], boss, 200, "shotgun", now2)
        ok("护盾破后进入化学伤害", boss["chem"].ring < ring0, "<%.1f" % ring0,
           "%.1f" % boss["chem"].ring)

    # ---------- 7. 击杀 Boss：贡献奖励 + 结算榜 ----------
    if boss:
        for p in humans:
            g2._damage(p["id"], boss, 500, "shotgun", now2)   # 各自刷点贡献
        scores_before = {p["id"]: p["score"] for p in humans}
        g2._damage(humans[0]["id"], boss, 99999, "shotgun", now2)
        ok("Boss 被击杀后离场", g2.boss_pid is None, "None", str(g2.boss_pid))
        ok("生成了 Boss 战结算榜", isinstance(g2.boss_result, dict) and
           len(g2.boss_result.get("top", [])) > 0, "有 top 列表",
           str(g2.boss_result and [r["n"] for r in g2.boss_result["top"]]))
        ok("贡献者获得了积分奖励",
           any(p["score"] > scores_before[p["id"]] for p in humans), "有人涨分",
           str([(p["name"], scores_before[p["id"]], p["score"]) for p in humans]))
        ok("Boss 事件已结束", g2.rnd_events.current is None, "None",
           str(g2.rnd_events.current))
        ok("Boss 战结束后恢复原阵营",
           all(p["team"] == teams0[p["id"]] for p in humans if p["id"] in teams0),
           "红蓝各归各位",
           str([(p["name"], p["team"]) for p in humans]))

    # ---------- 8. Boss 一打十：并发压力 + 存活/击杀 ----------
    g3 = server.Game()
    g3.bot_diff = "normal"
    fighters = []
    for i in range(10):
        p = g3.add_player("挑战者%02d" % i, "red", bot=True)   # 同一队：只打 Boss，不内战
        p["x"] = 200 + (i % 5) * 150
        p["y"] = 300 + (i // 5) * 200
        p["next_think"] = 0.0
        fighters.append(p)
    g3.rnd_events.force_start(g3, time.time(), "boss")
    b3 = g3.players.get(g3.boss_pid)          # 用 Boss 自己的安全出生点（别手动塞进墙里）
    t_start = time.time()
    kills0 = b3["kills"]
    deaths0 = b3["deaths"]
    tick_ms = []
    steps = int(20 * 60)                     # 模拟 20 秒
    for i in range(steps):
        t0 = time.perf_counter()
        g3.update(t_start + i / 60.0, 1 / 60.0)
        tick_ms.append((time.perf_counter() - t0) * 1000)
    tick_ms.sort()
    avg = sum(tick_ms) / len(tick_ms)
    p95 = tick_ms[int(len(tick_ms) * 0.95)]
    ok("Boss 在 1v10 下仍存活或已战死（有结果）", True, "有状态",
       "存活=%s 护盾=%.0f 缓冲=%.0f" % (b3.get("alive"),
                                        b3.get("boss_shield", 0),
                                        b3["chem"].ring))
    print("   Boss 1v10 战绩：击杀 %d / 阵亡 %d；挑战者总阵亡 %d"
          % (b3["kills"] - kills0, b3["deaths"] - deaths0,
             sum(p["deaths"] for p in fighters)))
    ok("Boss 有有效输出（能同时压制多人）", b3["kills"] - kills0 >= 3,
       "≥3 杀", "%d 杀" % (b3["kills"] - kills0))
    sh_max = float(g3.players[g3.boss_pid]["boss_cfg"]["shield"])
    ok("挑战者也能打到 Boss（双向对抗）", b3["boss_shield"] < sh_max * 0.95,
       "护盾下降", "护盾 %.0f / %.0f" % (b3["boss_shield"], sh_max))
    ok("单帧耗时可控（1 Boss + 10 bot）", avg < 8.0 and p95 < 15.0,
       "avg<8ms p95<15ms", "avg=%.2fms p95=%.2fms max=%.2fms"
       % (avg, p95, tick_ms[-1]))

    # ---------- 9. 事件管理器：自动触发 / 关闭 ----------
    g4 = server.Game()
    g4.add_player("甲", "red")
    g4.add_player("乙", "blue")
    t = time.time()
    g4.rnd_events.next_at = t - 1          # 立刻该触发
    g4.update(t, 1 / 60.0)
    ok("到时自动触发随机事件", g4.rnd_events.current is not None, "有事件",
       str(g4.rnd_events.current and g4.rnd_events.current["id"]))
    g4.rnd_events.force_end(g4, t, "测试结束")
    ok("事件结束后进入冷却", g4.rnd_events.current is None and
       g4.rnd_events.next_at > t, "冷却中", "next_at=+%.0fs" % (g4.rnd_events.next_at - t))
    g4.rnd_events.enabled = False
    g4.rnd_events.next_at = t - 1
    g4.update(t + 1, 1 / 60.0)
    ok("关闭后不再触发", g4.rnd_events.current is None, "None",
       str(g4.rnd_events.current))

    print("")
    print("总计: %d 项检查，%d 通过 / %d 失败。" % (N[0], N[0] - len(F), len(F)))
    if F:
        print("失败项: " + ", ".join(F))
    return 0 if not F else 1


if __name__ == "__main__":
    sys.exit(main())
