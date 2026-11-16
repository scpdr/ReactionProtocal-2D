# -*- coding: utf-8 -*-
"""随机事件 + Boss 战 端到端探针（真浏览器，主机可手动触发事件）。

用法：python tools/events_boss_e2e_probe.py --url http://127.0.0.1:8899/
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
           "if(n&&!n.value){n.value='事件探针';n.dispatchEvent(new Event('input',{bubbles:true}));}"
           "var b=document.getElementById('joinBtn');if(b)b.click();return 1;})()")


def state_js():
    return ("JSON.stringify({joined:joined,"
            "event:window.eventInfo?window.eventInfo.name:null,"
            "eventLeft:window.eventInfo?window.eventInfo.left:null,"
            "boss:window.bossInfo?window.bossInfo.n:null,"
            "bossShield:window.bossInfo?(window.bossInfo.bsh||0):null,"
            "bossMax:window.bossInfo?(window.bossInfo.bmx||0):null,"
            "contrib:(window.bossContrib||[]).length,"
            "result:window.bossResult?window.bossResult.boss:null,"
            "weapon:(players[myId]||{}).w,"
            "banner:(typeof banner==='string'?banner:''),"
            "bannerAlive:(typeof bannerT==='number'?bannerT>performance.now():false),"
            "announce:!!window.announceInfo,"
            "errors:(window.__errors||[]).slice(-3)})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8899/")
    ap.add_argument("--port", type=int, default=9377)
    a = ap.parse_args()
    base = a.url.rstrip("/")
    edge = next((p for p in EDGE if os.path.isfile(p)), None)
    if not edge:
        raise SystemExit("找不到 msedge.exe")
    prof = os.path.join(os.environ.get("TEMP", "."), "rp_event_profile")
    proc = subprocess.Popen([edge, "--headless=new", "--remote-debugging-port=%d" % a.port,
                             "--window-size=1366,768", "--user-data-dir=" + prof,
                             "--no-first-run", "--no-default-browser-check", a.url],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ok, bad = [], []

    def check(name, cond, want, got):
        (ok if cond else bad).append(name)
        print(("[PASS] " if cond else "[FAIL] ") + "%s 期望 %s / 实测 %s" % (name, want, got))

    def admin_js(eid):
        return ("(function(){if(!ws||ws.readyState!==1)return 'no-ws';"
                "ws.send(JSON.stringify({t:'admin',act:'event',id:%s}));"
                "return 'sent';})()" % json.dumps(eid))

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
        # 新 IP 会先进新手教程小局；先通关（发 tut_done）再测事件
        if ev("!!(window.TUT&&TUT.active)"):
            ev("(function(){TUT.finish();return 1;})()")
            time.sleep(2.0)
        st = json.loads(ev(state_js()) or "{}")
        print("进局:", st)
        check("已进局", st.get("joined") is True, "true", str(st.get("joined")))
        check("已离开教程进入对战", ev("!!(window.TUT&&TUT.active)") is False, "false",
              str(ev("!!(window.TUT&&TUT.active)")))
        check("初始没有事件", st.get("event") is None, "null", str(st.get("event")))
        check("页面无 JS 报错", not st.get("errors"), "[]", str(st.get("errors")))

        # --- 触发武器事件 ---
        print("触发 all_shotgun:", ev(admin_js("all_shotgun")))
        time.sleep(1.5)
        st2 = json.loads(ev(state_js()) or "{}")
        check("事件通过 HUD 显示", st2.get("event") == "喷淋器狂欢", "喷淋器狂欢",
              str(st2.get("event")))
        check("事件倒计时存在", isinstance(st2.get("eventLeft"), (int, float)) and
              st2.get("eventLeft") > 0, ">0", str(st2.get("eventLeft")))
        check("全员被强制成喷淋器", st2.get("weapon") == "shotgun", "shotgun",
              str(st2.get("weapon")))
        check("事件只有一个提示（有事件面板、没有重复大横幅）",
              st2.get("announce") is True and not st2.get("banner"),
              "announce=true, banner=''",
              "announce=%s banner=%r" % (st2.get("announce"), st2.get("banner")))

        # --- 触发 Boss 战 ---
        print("触发 boss:", ev(admin_js("boss")))
        time.sleep(2.0)
        st3 = json.loads(ev(state_js()) or "{}")
        check("Boss 战事件已开始", "Boss" in str(st3.get("event")), "含 Boss",
              str(st3.get("event")))
        check("HUD 拿到 Boss 与护盾", st3.get("boss") and st3.get("bossShield") > 0,
              "Boss/护盾>0", "%s/%.0f" % (st3.get("boss"), st3.get("bossShield") or 0))
        check("Boss 护盾上限字段正确", (st3.get("bossMax") or 0) >= 1000, "≥1000",
              str(st3.get("bossMax")))
        stj = json.loads(urllib.request.urlopen(base + "/status.json", timeout=6).read())
        check("服务端确认 Boss 在场", stj.get("boss_alive") is True, "True",
              str(stj.get("boss_alive")))
        check("服务端事件字段 >= 客户端一致", (stj.get("event") or {}).get("id") == "boss",
              "boss", str((stj.get("event") or {}).get("id")))

        # --- 关闭事件系统 ---
        print("关闭事件:", ev(admin_js("off")))
        time.sleep(1.5)
        st4 = json.loads(ev(state_js()) or "{}")
        check("关闭后事件消失", st4.get("event") is None, "null", str(st4.get("event")))
        check("关闭后 Boss 消失", st4.get("boss") is None, "null", str(st4.get("boss")))
        stj2 = json.loads(urllib.request.urlopen(base + "/status.json", timeout=6).read())
        check("服务端 Boss 已离场", stj2.get("boss_alive") is False, "False",
              str(stj2.get("boss_alive")))
        check("全程无 JS 报错", not st4.get("errors"), "[]", str(st4.get("errors")))
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
