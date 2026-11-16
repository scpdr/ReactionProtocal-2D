# -*- coding: utf-8 -*-
"""v1.3 服务端回归：隐藏可用积分（氟泄漏）+ 新手教程按 IP 记录 + 新手保护 + AI 巡逻兜底。

跑法：python tests/v13_server_check.py        退出码 0/1
只用标准库，进程内驱动 Game / 模块函数，不依赖网络与浏览器。
"""
import json
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server  # noqa: E402

N = [0]
F = []


def ok(name, cond, want, got):
    N[0] += 1
    if cond:
        print("[PASS] %s  期望 %s / 实测 %s" % (name, want, got))
    else:
        F.append(name)
        print("[FAIL] %s  期望 %s / 实测 %s" % (name, want, got))


def main():
    g = server.Game()
    a = g.add_player("甲", "red")
    b = g.add_player("乙", "blue")

    # ================= 1. 隐藏可用积分 =================
    ok("初始隐藏分", a["bank"] == 0 and a["score"] == 0, "bank=0,score=0",
       "bank=%s,score=%s" % (a["bank"], a["score"]))

    g._add_score(a, 20000)
    ok("入账后同步显示分与隐藏分", a["score"] == 20000 and a["bank"] == 20000,
       "score=20000,bank=20000", "score=%s,bank=%s" % (a["score"], a["bank"]))
    ok("2 万分时氟泄漏不可用", not g.can_spend(a, server.NUKE_SCORE), "False",
       str(g.can_spend(a, server.NUKE_SCORE)))

    g._add_score(a, 10000)
    ok("3 万分时氟泄漏可用", g.can_spend(a, server.NUKE_SCORE), "True",
       str(g.can_spend(a, server.NUKE_SCORE)))
    ok("扣除隐藏分成功", g.spend_points(a, server.NUKE_SCORE), "True",
       str(g.spend_points(a, server.NUKE_SCORE)))
    ok("扣后隐藏分=0（显示分不变）", a["bank"] == 0 and a["score"] == 30000,
       "bank=0,score=30000", "bank=%s,score=%s" % (a["bank"], a["score"]))
    ok("隐藏分不足时拒绝扣", not g.spend_points(a, server.NUKE_SCORE), "False",
       str(g.spend_points(a, server.NUKE_SCORE)))

    # 再攒 3 万 → 又能放（这就是"用完直接扣、按隐藏分判断"的核心行为）
    g._add_score(a, 30000)
    ok("攒到第二个 3 万后再次可用", g.can_spend(a, server.NUKE_SCORE) and a["score"] == 60000,
       "bank>=30000, score=60000", "bank=%s, score=%s" % (a["bank"], a["score"]))

    # 快照里的就绪标志只看隐藏分
    def nk_ok(p):
        for row in g.snapshot(time.time())["ps"]:
            if row["id"] == p["id"]:
                return row["nk_ok"]
        return None

    ok("快照 nk_ok=1（隐藏分够）", nk_ok(a) == 1, "1", str(nk_ok(a)))
    a["nuke_at"] = time.time() + 5          # 正在引爆中 → 不再显示可用
    ok("引爆中 nk_ok=0", nk_ok(a) == 0, "0", str(nk_ok(a)))
    a["nuke_at"] = None
    a["bank"] = 29999
    ok("隐藏分 29999 时 nk_ok=0", nk_ok(a) == 0, "0", str(nk_ok(a)))

    # ================= 2. 新手保护：教程中不掉血 =================
    # 用氧化通道（shotgun）直接剥离缓冲环，才能看出"免疫"是否生效（酸碱只改 pH，hp 本来就不变）
    a["bank"] = 0
    a["tut"] = True
    ring_before, score_before = a["chem"].ring, b["score"]
    g._damage(b["id"], a, 60, "shotgun", time.time())
    ok("教程中免疫伤害（缓冲环不减）", abs(a["chem"].ring - ring_before) < 0.01,
       "ring=%.1f" % ring_before, "ring=%.1f" % a["chem"].ring)
    ok("教程中不给对方加分", b["score"] == score_before, "score=%s" % score_before,
       "score=%s" % b["score"])
    a["tut"] = False
    g._damage(b["id"], a, 60, "shotgun", time.time())
    ok("退出教程后正常受伤（缓冲环下降）", a["chem"].ring < ring_before - 5,
       "ring<%.1f" % (ring_before - 5), "ring=%.1f" % a["chem"].ring)

    # ================= 3. 教程按 IP 记录且落盘 =================
    tmp = tempfile.mkdtemp(prefix="rp_tut_")
    old_root = server.ROOT
    try:
        server.ROOT = tmp
        server.TUT_DONE = set()
        ip = "192.168.3.99"
        ok("新 IP 需要教程", server.tutorial_needed(ip), "True",
           str(server.tutorial_needed(ip)))
        server.mark_tutorial_done(ip)
        ok("完成后不再需要", not server.tutorial_needed(ip), "False",
           str(server.tutorial_needed(ip)))
        path = server._tutorial_path()
        ok("记录已落盘", os.path.isfile(path), "存在 %s" % path,
           "存在" if os.path.isfile(path) else "缺失")
        data = json.load(open(path, encoding="utf-8"))
        ok("落盘内容含该 IP", ip in data.get("ips", []), "含 %s" % ip, str(data.get("ips")))
        server.TUT_DONE = set()
        server.load_tutorial_done()
        ok("重新加载后仍然记得", not server.tutorial_needed(ip), "False",
           str(server.tutorial_needed(ip)))
        ok("其它 IP 仍需教程", server.tutorial_needed("10.0.0.1"), "True",
           str(server.tutorial_needed("10.0.0.1")))
    finally:
        server.ROOT = old_root
        server.TUT_DONE = set()
        shutil.rmtree(tmp, ignore_errors=True)

    # ================= 4. AI 巡逻兜底 =================
    g2 = server.Game()
    bots = [g2.add_player("机器人%d" % i, "red" if i % 2 == 0 else "blue", bot=True)
            for i in range(4)]
    for i, p in enumerate(bots):
        p["x"], p["y"] = 200 + i * 400, 200 + i * 300
    moved = 0
    for p in bots:
        x0, y0 = p["x"], p["y"]
        for _ in range(6):
            g2._ai_patrol(p, time.time())
        d = ((p["x"] - x0) ** 2 + (p["y"] - y0) ** 2) ** 0.5
        has_dir = any(p["in"])
        if has_dir and p["wp"] is not None:
            moved += 1
        print("   巡逻 %s: 方向=%s wp=%s" % (p["name"], p["in"],
                                         None if p["wp"] is None else
                                         (round(p["wp"][0]), round(p["wp"][1]))))
    ok("4 个机器人都拿到了巡逻方向与路径点", moved == 4, "4/4", "%d/4" % moved)

    # 集成：真实主循环里"场上没有真人"时机器人应当移动（原来 6s 位移 0px）
    if server.AI_POLICY is not None:
        g3 = server.Game()
        b3 = [g3.add_player("训练机器人%d" % i, "red" if i % 2 == 0 else "blue", bot=True)
              for i in range(4)]
        for i, p in enumerate(b3):
            p["next_think"] = 0.0
            p["x"], p["y"] = 300 + i * 500, 300 + i * 250
        t0 = time.time()
        x0 = [(p["x"], p["y"]) for p in b3]
        while time.time() - t0 < 3.0:
            now = time.time()
            g3.update(now, 1.0 / 60)
            time.sleep(1.0 / 120)
        dists = [((p["x"] - x0i[0]) ** 2 + (p["y"] - x0i[1]) ** 2) ** 0.5
                 for p, x0i in zip(b3, x0)]
        ok("训练策略机器人在无真人时也会移动（原来 0px）", max(dists) > 100,
           "最大位移 >100px", "位移=%s" % [round(d) for d in dists])
    else:
        print("[SKIP] 训练策略未加载（LAN_AI_BOTS!=1），跳过集成巡逻断言")

    print("")
    print("总计: %d 项检查，%d 通过 / %d 失败。" % (N[0], N[0] - len(F), len(F)))
    if F:
        print("失败项: " + ", ".join(F))
    return 0 if not F else 1


if __name__ == "__main__":
    sys.exit(main())
