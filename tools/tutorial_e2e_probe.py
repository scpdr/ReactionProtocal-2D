# -*- coding: utf-8 -*-
"""新手教程端到端探针（Lead 用）：真浏览器进局 → 进"独立教程小局"（独立小地图/只有靶子）
→ 通关 → 回到对战大地图 → 服务端按 IP 记住 → 重进不再强制。

用法：python tools/tutorial_e2e_probe.py --url http://127.0.0.1:8899/
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp_eval import CDP  # noqa: E402

EDGE = [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"]

JOIN_JS = ("(function(){var n=document.getElementById('nameInput');"
           "if(n&&!n.value){n.value='教程探针';n.dispatchEvent(new Event('input',{bubbles:true}));}"
           "var b=document.getElementById('joinBtn');if(b)b.click();return 1;})()")

STATE_JS = ("JSON.stringify({joined:joined, myId:myId, mapId:(ACTIVE_MAP&&ACTIVE_MAP.id)||null,"
            "world:[WORLD_W,WORLD_H], tut:!!(window.TUT&&TUT.active),"
            "step:window.TUT?TUT.step:-1, players:Object.keys(players||{}).length,"
            "dummies:(function(){var n=0;for(var k in players){if(players[k].bot&&players[k].n&&players[k].n.indexOf('靶子')>=0)n++;}return n;})(),"
            "errors:(window.__errors||[]).slice(-3)})")


def get_json(url):
    with urllib.request.urlopen(url, timeout=6) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8899/")
    ap.add_argument("--port", type=int, default=9367)
    ap.add_argument("--w", type=int, default=1366)
    ap.add_argument("--h", type=int, default=768)
    a = ap.parse_args()
    base = a.url.rstrip("/")
    edge = next((p for p in EDGE if os.path.isfile(p)), None)
    if not edge:
        raise SystemExit("找不到 msedge.exe")
    prof = os.path.join(os.environ.get("TEMP", "."), "rp_tut_profile2")
    proc = subprocess.Popen([edge, "--headless=new", "--remote-debugging-port=%d" % a.port,
                             "--window-size=%d,%d" % (a.w, a.h), "--user-data-dir=" + prof,
                             "--no-first-run", "--no-default-browser-check", a.url],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ok, bad = [], []

    def check(name, cond, want, got):
        (ok if cond else bad).append(name)
        print(("[PASS] " if cond else "[FAIL] ") + "%s 期望 %s / 实测 %s" % (name, want, got))

    try:
        t0, ws = time.time(), None
        while time.time() - t0 < 25 and ws is None:
            try:
                with urllib.request.urlopen("http://127.0.0.1:%d/json" % a.port, timeout=2) as r:
                    for t in json.loads(r.read().decode("utf-8")):
                        if t.get("type") == "page":
                            ws = t["webSocketDebuggerUrl"]
            except Exception:
                pass
            if ws is None:
                time.sleep(0.4)
        if ws is None:
            raise SystemExit("CDP 未就绪")
        c = CDP(ws, timeout=30)
        ev = c.evaluate
        time.sleep(1.5)
        ev(JOIN_JS)
        time.sleep(2.5)

        st = json.loads(ev(STATE_JS) or "{}")
        print("进局状态:", st)
        check("首次加入进入教程", st.get("tut") is True, "true", str(st.get("tut")))
        check("教程从第 1 步开始", st.get("step") == 0, "0", str(st.get("step")))
        check("进入的是独立教程小地图", st.get("mapId") == "tutorial", "tutorial", str(st.get("mapId")))
        check("世界尺寸是教程小地图", st.get("world") == [1100, 700], "[1100,700]", str(st.get("world")))
        check("教程局里只有自己 + 3 个靶子", st.get("players") == 4 and st.get("dummies") == 3,
              "4 人 / 3 靶子", "%s 人 / %s 靶子" % (st.get("players"), st.get("dummies")))
        check("页面无 JS 报错", not st.get("errors"), "[]", str(st.get("errors")))

        stj = get_json(base + "/status.json")
        check("服务端有 1 个教程小局", stj.get("tutorial_sessions") == 1, "1",
              str(stj.get("tutorial_sessions")))
        main_humans = [p["n"] for p in stj["players"] if not p.get("bot")]
        check("主对局里看不到教程玩家", main_humans == [], "[]", str(main_humans))

        # 通关
        ev("(function(){TUT.finish();return 1;})()")
        time.sleep(2.0)
        st2 = json.loads(ev(STATE_JS) or "{}")
        print("通关后:", st2)
        check("通关后离开教程局", st2.get("tut") is False, "false", str(st2.get("tut")))
        check("通关后回到对战大地图", st2.get("mapId") not in (None, "tutorial"),
              "≠tutorial", str(st2.get("mapId")))
        check("通关后世界尺寸变大", st2.get("world") != [1100, 700], "≠1100x700",
              str(st2.get("world")))
        stj2 = get_json(base + "/status.json")
        check("教程小局已销毁", stj2.get("tutorial_sessions") == 0, "0",
              str(stj2.get("tutorial_sessions")))
        check("服务端记录了该 IP 通关", (stj2.get("tutorials") or 0) >= 1, "≥1",
              str(stj2.get("tutorials")))
        humans_now = [p["n"] for p in stj2["players"] if not p.get("bot")]
        check("已加入主对局", "教程探针" in humans_now, "含 教程探针", str(humans_now))

        # 重进（同一 IP）→ 直接进对战
        ev("location.reload()")
        time.sleep(4.0)
        ev(JOIN_JS)
        time.sleep(2.5)
        st3 = json.loads(ev(STATE_JS) or "{}")
        check("同一 IP 重进不再强制教程", st3.get("tut") is False and st3.get("joined") is True,
              "joined=true, tut=false", str(st3))
        check("重进直接在对战地图", st3.get("mapId") not in (None, "tutorial"),
              "≠tutorial", str(st3.get("mapId")))
        c.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=6)
        except Exception:
            proc.kill()

    print("\n结果: %d 通过 / %d 失败" % (len(ok), len(bad)))
    if bad:
        print("失败: " + ", ".join(bad))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
