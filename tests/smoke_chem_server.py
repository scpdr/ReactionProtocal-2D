# -*- coding: utf-8 -*-
"""服务端化学接入冒烟测试（进程内，直接驱动 Game，不依赖网络）。

覆盖：
- 快照包含 ph / buf
- 狙击一发（旧伤害 110 → 55 mmol H+）击穿满缓冲并触发死亡（酸中毒）
- 击杀事件带 cause 与 ph
- 步枪单发后缓冲环下降约 22 个百分点，且 pH 轻微下降
- 脱战 4s 后代偿回满
- 重生后化学状态复位
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server  # noqa: E402


def main():
    # 本测试只验证化学/数值：关掉随机事件，避免"全员强制武器"之类的 buff 干扰断言
    server.EVENTS_ENABLED = False
    g = server.Game()
    a = g.add_player("测试A", "red")
    b = g.add_player("测试B", "blue")
    a["x"], a["y"] = 300, 800
    b["x"], b["y"] = 300, 700

    snap = g.snapshot(0.0)
    me = [p for p in snap["ps"] if p["id"] == a["id"]][0]
    assert "ph" in me and "buf" in me, "快照缺少 ph/buf"
    assert abs(me["buf"] - 100.0) < 0.5 and abs(me["ph"] - 7.2) < 0.01
    print("初始快照: ph=%.2f buf=%.1f" % (me["ph"], me["buf"]))

    # 1) 步枪单发：酸碱只改 pH，不直接扣缓冲环
    g._damage(b["id"], a, 22, "rifle", 0.0)
    s = a["chem"].snapshot()
    print("步枪 1 发: ph=%.2f buf=%.1f" % (s["ph"], s["ring"]))
    assert 99.0 < s["ring"] <= 100.0
    assert 7.05 < s["ph"] < 7.10

    # 2) 脱战代偿：4s 后 1.5s 内应回满
    a["chem"].last_damage_t = 0.0
    a["chem"].tick_regen(4.0, 1.5)
    s = a["chem"].snapshot()
    print("代偿后: ph=%.2f buf=%.1f" % (s["ph"], s["ring"]))
    assert abs(s["ring"] - 100.0) < 1.5 and abs(s["ph"] - 7.2) < 0.03

    # 3) 狙击两发：满缓冲保护下第一发不致死，第二发击穿
    g._damage(b["id"], a, 110, "sniper", 1.0)
    g._damage(b["id"], a, 110, "sniper", 1.5)
    assert not a["chem"].alive, "狙击两发应击穿满缓冲"
    assert a["chem"].death_cause == "acidosis"
    kill = [e for e in g.events if e["t"] == "kill"]
    assert kill, "缺少击杀事件"
    print("狙击击杀: cause=%s ph=%.2f" % (kill[0]["cause"], kill[0]["ph"]))
    assert kill[0]["cause"] == "acidosis" and kill[0]["ph"] <= 3.0

    # 4) 快照中死亡状态
    snap = g.snapshot(1.0)
    me = [p for p in snap["ps"] if p["id"] == a["id"]][0]
    assert me["al"] is False

    # 5) 重生复位
    a["respawn_at"] = 0.0
    g.update(time.time(), 0.033)
    s = a["chem"].snapshot()
    print("重生后: alive=%s ph=%.2f buf=%.1f" % (a["alive"], s["ph"], s["ring"]))
    assert a["alive"] and abs(s["ring"] - 100.0) < 1.0

    # 6) 氟泄漏（150 旧伤害 → 150 mmol 缓冲剥离）应击杀，死因为缓冲体系被剥离
    g2 = server.Game()
    c = g2.add_player("C", "red")
    d = g2.add_player("D", "blue")
    g2._damage(d["id"], c, 150, "nuke", 2.0)
    assert not c["chem"].alive and c["chem"].death_cause == "buffer_destroyed"
    print("氟泄漏击杀: cause=%s" % c["chem"].death_cause)

    # 7) 机器人会换枪（多化学通道）
    # 7) 机器人换枪：用启发式 AI 测（训练策略不一定换枪，那属于策略行为）
    _ai_backup = server.AI_POLICY
    server.AI_POLICY = None
    g3 = server.Game()
    b1 = g3.add_player("B1", "red", bot=True)
    b2 = g3.add_player("B2", "blue", bot=True)
    b1["x"], b1["y"] = 300, 800
    b2["x"], b2["y"] = 420, 800
    seen = set()
    t = 10.0
    for _ in range(900):
        g3.update(t, 0.033)
        t += 0.033
        seen.add(b1["weapon"])
    print("机器人用枪:", sorted(seen))
    assert len(seen) >= 2, "机器人应当会切换多种武器"
    server.AI_POLICY = _ai_backup          # 恢复训练策略（后续用例若需要）

    # 8) 中和救援：酸蚀队友被碱中和；错误方向无效
    g4 = server.Game()
    a4 = g4.add_player("A4", "red")
    b4 = g4.add_player("B4", "red")
    b4["chem"].apply_damage(115, "base", 0.0)       # ~40 mmol 碱负荷，环不因酸碱直接下降
    assert b4["chem"].ph > 7.2
    g4._damage(a4["id"], b4, 110, "rifle", 1.0)    # 队友用酸中和：消耗缓冲对
    assert b4["chem"].alive, "中和救援不应致死"
    assert 45.0 < b4["chem"].ring < 70.0, "中和会消耗缓冲对，环应下降但不归零"
    neu = [e for e in g4.events if e["t"] == "neutralize"]
    assert neu and neu[0]["mmol"] > 0
    print("中和救援: %s mmol, pH=%s, 缓冲=%.0f%%" %
          (neu[0]["mmol"], neu[0]["ph"], b4["chem"].ring))
    b4["chem"].apply_damage(60, "acid", 2.0)       # 现在是酸负荷
    ring_before = b4["chem"].ring
    g4._damage(a4["id"], b4, 110, "rifle", 3.0)    # 同向酸：不应“治疗”
    assert abs(b4["chem"].ring - ring_before) < 0.5

    # 6) 参数面板 B：热调 C_T0，快照回传、状态重置
    assert "cfg" in snap and abs(snap["cfg"]["ct0"] - 0.1) < 1e-6
    assert g.set_chem_param("ct0", 0.05)
    assert any(e["t"] == "cfg" for e in g.events)
    snap = g.snapshot(3.0)
    assert abs(snap["cfg"]["ct0"] - 0.05) < 1e-6, snap["cfg"]
    me = [p for p in snap["ps"] if p["id"] == a["id"]][0]
    assert abs(me["buf"] - 100.0) < 0.5
    # 非法参数应被拒绝
    assert not g.set_chem_param("hack", 1.0)
    # 恢复默认，避免影响后续用例（CHEM 是全局单例）
    assert g.set_chem_param("ct0", 0.1)
    print("参数面板 B: C_T0 0.100 -> %.3f mol，状态已重置" % snap["cfg"]["ct0"])

    # 9) 弹道快照带通道/武器码（客户端定制特效）
    g5 = server.Game()
    s1 = g5.add_player("S1", "red")
    s2 = g5.add_player("S2", "blue")
    s1["x"], s1["y"] = 400, 800
    s2["x"], s2["y"] = 900, 800
    s1["weapon"] = "rifle"
    s1["angle"] = 0.0
    s1["last_shot"] = -10.0
    g5._try_shoot(s1, 0.0)
    bs = g5.snapshot(0.0)["bs"]
    assert bs and len(bs[0]) == 6, "弹道快照应含 [x,y,dx,dy,通道,武器]"
    print("弹道快照: ch=%d weapon=%d" % (bs[0][4], bs[0][5]))

    # 10) 高价值目标：达到连杀阈值时必须有全局事件 + 播报（客户端再加优先级保护）
    g6 = server.Game()
    k6 = g6.add_player("K6", "red")
    v6 = g6.add_player("V6", "blue")
    k6["streak"] = server.HVT_STREAK - 1
    g6._damage(k6["id"], v6, 300, "sniper", 0.0)
    assert any(e["t"] == "hvt" for e in g6.events), "缺少 HVT 全局事件"
    assert any(f.get("w") == "hvt" for f in g6.feed), "缺少 HVT 播报"
    print("高价值目标: 全局事件 + 播报已生成")

    # 11) 8 试剂槽：石灰水 AOE / 维C 支援 / 指示剂
    g7 = server.Game()
    r7 = g7.add_player("R7", "red")
    b7 = g7.add_player("B7", "blue")
    r7["x"], r7["y"] = 400, 800
    b7["x"], b7["y"] = 800, 800
    assert "limewater" in server.WEAPONS and "vitc" in server.WEAPONS
    assert r7["ammo"]["limewater"] == 2 and r7["ammo"]["vitc"] == 3
    # 石灰水：朝敌人投掷，AOE 命中后 pH 上升（酸碱只改 pH，不直接扣环）
    r7["weapon"] = "limewater"
    r7["angle"] = 0.0
    r7["last_shot"] = -10.0
    ph_before = b7["chem"].ph
    g7._try_shoot(r7, 0.0)
    t = 0.0
    for _ in range(40):
        t += 0.033
        g7._update_bullets(t, 0.033)
        if b7["chem"].ph > ph_before + 0.02:
            break
    assert b7["chem"].ph > ph_before + 0.02, "石灰水应提高 pH"
    assert any(e["t"] == "boom" for e in g7.events)
    print("石灰水 AOE: pH %.2f -> %.2f" % (ph_before, b7["chem"].ph))

    # 维C：修复队友被剥离的缓冲
    g8 = server.Game()
    r8 = g8.add_player("R8", "red")
    a8 = g8.add_player("A8", "red")
    r8["x"], r8["y"] = 400, 800
    a8["x"], a8["y"] = 700, 800
    a8["chem"].apply_damage(40, "oxidizing", 0.0)   # 剥离 40 mmol
    ring0 = a8["chem"].ring
    r8["weapon"] = "vitc"
    r8["angle"] = 0.0
    r8["last_shot"] = -10.0
    g8._try_shoot(r8, 0.0)
    t = 0.0
    for _ in range(40):
        t += 0.033
        g8._update_bullets(t, 0.033)
        if a8["chem"].ring > ring0 + 1:
            break
    assert a8["chem"].ring > ring0 + 1, "维C 应修复队友缓冲"
    assert any(e["t"] == "restore" for e in g8.events)
    print("维C 支援: 缓冲 %.0f%% -> %.0f%%" % (ring0, a8["chem"].ring))

    # 指示剂：标记准星前方敌人并显示 pH/缓冲
    g9 = server.Game()
    r9 = g9.add_player("R9", "red")
    e9 = g9.add_player("E9", "blue")
    r9["x"], r9["y"] = 400, 800
    e9["x"], e9["y"] = 760, 800
    r9["angle"] = 0.0
    assert g9.use_indicator(r9, 0.0)
    assert e9["ind_until"] > 0 and r9["ind_charges"] == 1
    assert any(e["t"] == "indicator" for e in g9.events)
    print("指示剂: 已标记 %s，剩余 %d 次" % (e9["name"], r9["ind_charges"]))

    # 12) 弹药补给点 + 备弹快照 + 换图保留战绩
    g10 = server.Game()
    p10 = g10.add_player("P10", "red")
    p10["reserve"]["rifle"] = 0
    p10["ammo"]["rifle"] = 0
    pad = server.AMMO_PADS[0]
    p10["x"], p10["y"] = pad[0], pad[1]
    g10.update(time.time() + 1.0, 0.033)
    assert p10["reserve"]["rifle"] == server.WEAPON_RESERVE["rifle"]
    assert any(e["t"] == "supply" for e in g10.events)
    snap10 = g10.snapshot(time.time() + 1.1)
    me10 = [x for x in snap10["ps"] if x["id"] == p10["id"]][0]
    assert "res" in me10
    score0, kills0 = p10["score"], p10["kills"]
    g10.switch_map(time.time() + 2.0)
    assert p10["score"] == score0 and p10["kills"] == kills0
    print("弹药补给 + 换图: reserve=%d, 战绩保留" % p10["reserve"]["rifle"])

    # 13) 高氯酸狙击：满缓冲一发酸中毒；切刀加速 + 物理击杀多给分
    g13 = server.Game()
    a13 = g13.add_player("A13", "red")
    b13 = g13.add_player("B13", "blue")
    g13._damage(a13["id"], b13, 150, "sniper", 0.0)
    assert not b13["chem"].alive and b13["chem"].death_cause == "acidosis"
    print("高氯酸单发: cause=%s" % b13["chem"].death_cause)

    g14 = server.Game()
    a14 = g14.add_player("A14", "red")
    b14 = g14.add_player("B14", "blue")
    a14["x"], a14["y"] = 400, 800
    b14["chem"].destroyed = 0.090       # 缓冲环只剩 10%
    b14["chem"]._invalidate()
    b14["chem"]._refresh()
    b14["hp"] = round(b14["chem"].ring, 1)
    g14._damage(a14["id"], b14, 55, "knife", 1.0)
    assert not b14["chem"].alive
    assert a14["score"] >= server.KILL_SCORE + server.PHYSICAL_KILL_BONUS
    print("物理击杀积分: %d" % a14["score"])

    g15 = server.Game()
    p15 = g15.add_player("P15", "red")
    p15["x"], p15["y"] = 600, 800
    p15["weapon"] = "knife"
    p15["in"] = [0, 1, 0, 0]
    x0 = p15["x"]
    g15.update(time.time(), 0.1)
    dx_knife = p15["x"] - x0
    p15["x"] = 600
    p15["weapon"] = "rifle"
    p15["in"] = [0, 1, 0, 0]
    g15.update(time.time() + 0.1, 0.1)
    dx_rifle = p15["x"] - 600
    assert dx_knife > dx_rifle, "切刀应当加速"
    print("切刀加速: %.1f > %.1f" % (dx_knife, dx_rifle))

    g16 = server.Game()
    p16 = g16.add_player("P16", "red")
    p16["ai_aim"] = True
    p16["weapon"] = "sniper"
    held = 0
    for i in range(900):
        t16 = 100.0 + i * 0.033
        if p16["weapon"] == "sniper":
            held += 1
        g16.update(t16, 0.033)
    frac = held / 900.0
    assert frac <= server.AI_SNIPER_MAX_FRAC + 0.03, frac
    print("AI 狙击时间占比: %.1f%% (上限 %.0f%%, 30s窗口)" % (frac * 100, server.AI_SNIPER_MAX_FRAC * 100))

    g17 = server.Game()
    for diff, want in (("easy", 0.08), ("normal", 0.12), ("hard", 0.18)):
        g17.bot_diff = diff
        pb = g17.add_player("BOT_" + diff, "red", bot=True)
        assert abs(pb["ai_sniper_frac"] - want) < 1e-9
    print("AI 狙击配额按难度: easy 8% / normal 12% / hard 18%")

    print("\n服务端化学接入冒烟测试通过 ✓")


if __name__ == "__main__":
    main()
