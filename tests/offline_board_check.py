# -*- coding: utf-8 -*-
"""服务端"退出玩家保留战绩 + 榜单离线标记"回归。

跑法：python tests/offline_board_check.py    退出码 0/1
"""
import os
import sys

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
    g = server.Game()
    a = g.add_player("离线侠", "red")
    a["kills"], a["deaths"], a["score"] = 7, 3, 1234
    bot = g.add_player("机器人9号", "blue", bot=True)

    ok("初始没有离线记录", g.offline == {}, "{}", str(g.offline))

    # 退出 → 记录
    g.note_offline(a)
    ok("退出后记入离线名单", "离线侠" in g.offline, "含 离线侠", str(list(g.offline)))
    rec = g.offline.get("离线侠", {})
    ok("保留了击杀/阵亡/积分", (rec.get("k"), rec.get("d"), rec.get("s")) == (7, 3, 1234),
       "(7,3,1234)", str((rec.get("k"), rec.get("d"), rec.get("s"))))
    ok("保留了队伍", rec.get("team") == "red", "red", str(rec.get("team")))

    g.note_offline(bot)
    ok("机器人退出不记入榜单", "机器人9号" not in g.offline, "不含机器人",
       str(list(g.offline)))

    # 快照里带 off 字段
    snap = g.snapshot(0.0)
    ok("快照包含 off 列表", isinstance(snap.get("off"), list) and len(snap["off"]) == 1,
       "1 条", str(snap.get("off")))
    ok("快照 off 条目带名字与积分",
       snap["off"][0].get("n") == "离线侠" and snap["off"][0].get("s") == 1234,
       "离线侠/1234", "%s/%s" % (snap["off"][0].get("n"), snap["off"][0].get("s")))

    # status.json 也带上（验收用）
    st = g.status()
    ok("status.json 包含 offline", isinstance(st.get("offline"), list) and len(st["offline"]) == 1,
       "1 条", str(len(st.get("offline") or [])))

    # 回到服务器 → 从离线名单移除
    g.clear_offline("离线侠")
    ok("重新加入后从离线名单移除", "离线侠" not in g.offline, "不含", str(list(g.offline)))
    snap2 = g.snapshot(0.0)
    ok("快照 off 清空", snap2.get("off") == [], "[]", str(snap2.get("off")))

    # 上限：最多保留 12 人，超出丢最早的
    for i in range(15):
        p = g.add_player("临时%d" % i, "red")
        p["score"] = i
        g.note_offline(p)
        g.remove_player(p["id"])
    ok("离线名单最多保留 12 人", len(g.offline) == 12, "12", str(len(g.offline)))

    # 重开一局清空
    g.reset()
    ok("重开一局清空离线名单", g.offline == {}, "{}", str(g.offline))

    print("")
    print("总计: %d 项检查，%d 通过 / %d 失败。" % (N[0], N[0] - len(F), len(F)))
    if F:
        print("失败项: " + ", ".join(F))
    return 0 if not F else 1


if __name__ == "__main__":
    sys.exit(main())
