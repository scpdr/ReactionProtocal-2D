# -*- coding: utf-8 -*-
"""新手教程"独立小局"回归：独立小地图 / 每个玩家一个实例互不可见 / 只有静止人机靶子 / 不参与地图轮换。

跑法：python tests/tutorial_private_check.py    退出码 0/1
"""
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
    # ---------- 1. 教程地图存在且被标记（不参与轮换） ----------
    tut_maps = [m for m in server.MAPS if m.get("tutorial")]
    ok("教程地图存在且带 tutorial 标记", len(tut_maps) == 1,
       "1 张", str([m["id"] for m in tut_maps]))
    tm = tut_maps[0] if tut_maps else {}
    ok("教程地图是小地图（≤1400×900）", tm.get("w", 9999) <= 1400 and tm.get("h", 9999) <= 900,
       "≤1400×900", "%sx%s" % (tm.get("w"), tm.get("h")))
    rotate_ids = [m["id"] for m in server.MAPS if not m.get("tutorial")]
    ok("轮换池不含教程图", "tutorial" not in rotate_ids, "不含 tutorial", str(rotate_ids))

    # 轮换：从 foundry 换图应到 titration（而不是 tutorial）
    g0 = server.Game()
    server.apply_map("foundry")
    g0.map_switch_at = time.time()
    g0.maybe_switch_map(time.time())
    ok("换图不会切进教程图", server.ACTIVE_MAP["id"] != "tutorial", "≠tutorial",
       server.ACTIVE_MAP["id"])

    # ---------- 2. 两个玩家各一个私有小局，互相看不见 ----------
    cid1, pid1, g1, map1 = server.start_tutorial_session("新手甲")
    cid2, pid2, g2, map2 = server.start_tutorial_session("新手乙")
    try:
        ok("两个教程会话是不同实例", g1 is not g2, "不同 Game", "不同" if g1 is not g2 else "同一个")
        ok("会话用的都是教程地图", map1.get("id") == "tutorial" and map2.get("id") == "tutorial",
           "tutorial/tutorial", "%s/%s" % (map1.get("id"), map2.get("id")))
        ids1 = set(g1.players.keys())
        ids2 = set(g2.players.keys())
        ok("甲的快照只有自己 + 靶子（看不到乙）", len(ids1) == 4 and len(ids2) == 4,
           "各 4 人", "甲 %d / 乙 %d" % (len(ids1), len(ids2)))
        ok("甲的局里没有乙的玩家名字", all(p["name"] != "新手乙" for p in g1.players.values()),
           "无 新手乙", str([p["name"] for p in g1.players.values()]))

        snap1 = g1.snapshot(time.time())
        snap2 = g2.snapshot(time.time())
        ids_in_snap1 = set(p["id"] for p in snap1["ps"])
        ok("甲收到的快照 id 集合 = 甲局玩家", ids_in_snap1 == ids1,
           str(sorted(ids1)), str(sorted(ids_in_snap1)))

        dummies = [p for p in g1.players.values() if p.get("dummy")]
        ok("教程局里有 3 个人机靶子", len(dummies) == 3, "3", str(len(dummies)))
        ok("靶子都是 bot 且被标记为 dummy",
           all(p["bot"] and p.get("dummy") for p in dummies), "全部 bot+dummy",
           str([(p["bot"], p.get("dummy")) for p in dummies]))
        ok("靶子武器/缓冲状态已配置（便于练习不同通道）",
           len(set(p["weapon"] for p in dummies)) >= 3,
           "≥3 种武器", str(sorted(set(p["weapon"] for p in dummies))))

        # ---------- 3. 靶子静止、不开枪 ----------
        pos0 = {p["id"]: (p["x"], p["y"]) for p in dummies}
        t0 = time.time()
        while time.time() - t0 < 2.0:
            now = time.time()
            g1.update(now, 1.0 / 60)
            time.sleep(1.0 / 120)
        moved = max((((p["x"] - pos0[p["id"]][0]) ** 2 +
                      (p["y"] - pos0[p["id"]][1]) ** 2) ** 0.5) for p in dummies)
        ok("靶子 2 秒内完全静止", moved < 0.01, "<0.01px", "%.3fpx" % moved)
        ok("靶子不会开枪/产生子弹", len(g1.bullets) == 0, "0 子弹", str(len(g1.bullets)))

        # ---------- 4. 教程局地图被钉住：即使副本全局被搞乱，新会话也会重置回教程小地图 ----------
        import importlib
        tut_mod = server.TUT_MOD[0]
        if tut_mod is not None:
            tut_mod.apply_map("foundry")                # 模拟全局状态被别的逻辑改掉
            cid3, pid3, g3, map3 = server.start_tutorial_session("新手丙")
            ok("新会话强制重置回教程地图", map3.get("id") == "tutorial", "tutorial",
               str(map3.get("id")))
            ok("重置后世界尺寸仍是小地图", (tut_mod.WORLD_W, tut_mod.WORLD_H) == (1100, 700),
               "1100x700", "%dx%d" % (tut_mod.WORLD_W, tut_mod.WORLD_H))
            server.drop_tutorial_session(cid3)

        # ---------- 5. 结束会话 ----------
        nm = server.drop_tutorial_session(cid1)
        ok("drop_tutorial_session 返回名字并移除会话", nm == "新手甲" and cid1 not in server.TUT_SESSIONS,
           "新手甲 / 已移除", "%s / %s" % (nm, cid1 not in server.TUT_SESSIONS))
        ok("另一个会话不受影响", cid2 in server.TUT_SESSIONS, "仍在", str(cid2 in server.TUT_SESSIONS))
    finally:
        server.drop_tutorial_session(cid1)
        server.drop_tutorial_session(cid2)

    ok("清理后没有残留教程会话", len(server.TUT_SESSIONS) == 0, "0", str(len(server.TUT_SESSIONS)))

    print("")
    print("总计: %d 项检查，%d 通过 / %d 失败。" % (N[0], N[0] - len(F), len(F)))
    if F:
        print("失败项: " + ", ".join(F))
    return 0 if not F else 1


if __name__ == "__main__":
    sys.exit(main())
