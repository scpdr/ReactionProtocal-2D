# -*- coding: utf-8 -*-
"""安卓真机验收：连续 3 轮，覆盖操作/化学/AI/地图/面板/重连，并生成结论文档。

前置（两种模式任选）：
  A) 电脑开服：python server.py 8765 --bots 4 --no-browser，再 adb reverse tcp:8765 tcp:8765
  B) 手机自己开服（推荐，验证"手机=服务器"）：python tests/android_rounds.py --host --map-switch 15
     —— 脚本用 --ez host true 启动，等内嵌 Python 服务端就绪后自动开测，全程无需电脑服务端。
用法：python tests/android_rounds.py --out 测试/安卓验收 [--host] [--map-switch 15]
"""
import argparse
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from tools.cdp_eval import CDP, list_targets  # noqa: E402
from tests.android_soak import status  # noqa: E402

TMP = os.path.join(os.environ.get("TEMP", "/tmp"), "rp_rounds")
RESULTS = []
HOST_MODE = False        # True = 手机自己开服（内嵌 Python 服务端），不用电脑服务端
HOST_MAP_SWITCH = 0      # 传给主机的换图间隔（秒），0 = 用默认值


def sh(*args, check=False):
    r = subprocess.run(list(args), capture_output=True, text=True, timeout=90)
    if check and r.returncode != 0:
        raise RuntimeError("adb failed: %s %s" % (args, r.stderr[:200]))
    return r.stdout.strip()


def find_sock():
    out = sh("adb", "shell", "cat /proc/net/unix")
    import re
    m = re.search(r"webview_devtools_remote_\d+", out)
    return m.group(0) if m else None


def launch_and_cdp(server="127.0.0.1:8765", extra=None, host_mode=False, map_switch=0):
    sh("adb", "shell", "am", "force-stop", "com.reactionprotocol.app")
    sh("adb", "logcat", "-c")
    args = ["adb", "shell", "am", "start", "-n",
            "com.reactionprotocol.app/.MainActivity"]
    if host_mode:
        args += ["--ez", "host", "true"]
        if map_switch:
            args += ["--ei", "map_switch", str(map_switch)]
    elif server:
        args += ["--es", "server", server]
    if extra:
        args += list(extra)
    sh(*args)
    if host_mode:
        # 手机自己开服：PC 侧通过 adb forward 探活，确认真的是手机在提供服务
        sh("adb", "forward", "tcp:8765", "tcp:8765")
        for _ in range(80):
            try:
                if status()["players"] is not None:
                    break
            except Exception:
                pass
            time.sleep(0.5)
        else:
            raise RuntimeError("内嵌服务端 40s 内未就绪")
    for _ in range(20):
        time.sleep(0.5)
        sock = find_sock()
        if sock:
            sh("adb", "forward", "--remove-all")
            sh("adb", "forward", "tcp:9222", "localabstract:" + sock)
            if host_mode:
                sh("adb", "forward", "tcp:8765", "tcp:8765")   # remove-all 会清掉，补回来
            targets = [t for t in list_targets() if t.get("type") == "page"]
            if targets:
                return CDP(targets[0]["webSocketDebuggerUrl"])
    raise RuntimeError("no cdp target")


def ui_centers(cdp):
    """从页面布局模块取所有可点控件中心（避免写死坐标）。"""
    raw = cdp.evaluate(
        "JSON.stringify((function(){var r=window.__mobileLayoutReport().layout;"
        "var o={menu:{},ring:{},chip:{},fire:{},stick:{}};"
        "for(var id in r.top.menu){o.menu[id]={x:r.top.menu[id].x+r.top.menu[id].w/2,"
        "y:r.top.menu[id].y+r.top.menu[id].h/2};}"
        "r.ring.forEach(function(g){o.ring[g.id]={x:g.cx,y:g.cy};});"
        "r.chips.order.forEach(function(w,i){o.chip[w]={x:r.chips.x0+i*(r.chips.w+r.chips.gap)+r.chips.w/2,"
        "y:r.chips.y0+r.chips.h/2};});"
        "o.fire={x:r.fire.cx,y:r.fire.cy};o.stick={x:r.stick.cx,y:r.stick.cy};"
        "o.aim={x:r.vw*0.75,y:r.vh*0.35};o.zone={x:r.vw*0.35,y:r.vh*0.62};"
        "o.move={x:r.vw*0.22,y:r.vh*0.58};return o;})())")
    return json.loads(raw)


def join(cdp, name, bots=None):
    if bots is not None:
        cdp.evaluate("document.getElementById('botCount').value='%s'" % bots)
    cdp.evaluate("document.getElementById('nameInput').value='%s'" % name)
    btn = json.loads(cdp.evaluate(
        "JSON.stringify((function(){var b=document.getElementById('joinBtn').getBoundingClientRect();"
        "return {x:b.left+b.width/2,y:b.top+b.height/2};})())"))
    cdp.tap(btn["x"], btn["y"])
    time.sleep(2.5)
    return cdp.evaluate("joined")


def shot(name, outdir):
    os.makedirs(TMP, exist_ok=True)
    remote = "/sdcard/%s.png" % name
    sh("adb", "shell", "screencap", "-p", remote)
    sh("adb", "pull", remote, os.path.join(TMP, name + ".png"))
    dst = os.path.join(outdir, name + ".png")
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(TMP, name + ".png"), "rb") as f:
        data = f.read()
    with open(dst, "wb") as f:
        f.write(data)
    return dst


def record(round_name, item, ok, detail):
    RESULTS.append((round_name, item, bool(ok), detail))
    print("[%s] %-22s %s  %s" % (round_name, item, "PASS" if ok else "FAIL", detail))


def main():
    global HOST_MODE, HOST_MAP_SWITCH
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "..", "测试", "安卓验收"))
    ap.add_argument("--soak", type=float, default=70)
    ap.add_argument("--host", action="store_true",
                    help="手机自己开服（内嵌服务端），不依赖电脑上的 server.py")
    ap.add_argument("--map-switch", type=int, default=0, help="主机换图间隔（秒），配合 --host")
    args = ap.parse_args()
    HOST_MODE = args.host
    HOST_MAP_SWITCH = args.map_switch
    outdir = os.path.abspath(args.out)
    if HOST_MODE:
        print("[模式] 手机自己开服（内嵌 Python 服务端），换图间隔=%s" % (HOST_MAP_SWITCH or "默认"))

    # ---------------- Round 1：操作与客户端 ----------------
    cdp = launch_and_cdp(host_mode=HOST_MODE, map_switch=HOST_MAP_SWITCH)
    ok = join(cdp, "R1Final")
    record("R1", "启动/加入房间", ok, "joined=%s" % ok)
    c = ui_centers(cdp)
    # 布局自检：控件互不重叠、不越安全区
    rep = json.loads(cdp.evaluate("JSON.stringify(window.__mobileLayoutReport())"))
    record("R1", "控件布局无重叠", not rep["overlaps"] and not rep["outOfSafe"],
           "overlaps=%s outOfSafe=%s viewport=%dx%d" % (rep["overlaps"], rep["outOfSafe"], rep["vw"], rep["vh"]))
    s0 = json.loads(cdp.evaluate("JSON.stringify({x:players[myId].x,y:players[myId].y,w:players[myId].w,am:players[myId].am,res:players[myId].res})"))
    cdp.swipe(c["move"]["x"], c["move"]["y"], c["move"]["x"] + 120, c["move"]["y"], 600)
    time.sleep(0.5)
    s1 = json.loads(cdp.evaluate("JSON.stringify({x:players[myId].x,y:players[myId].y})"))
    record("R1", "左半屏移动", abs(s1["x"] - s0["x"]) > 20, "x %.0f→%.0f" % (s0["x"], s1["x"]))
    cdp.swipe(c["aim"]["x"], c["aim"]["y"], c["aim"]["x"], c["aim"]["y"] - 70, 400)
    time.sleep(0.4)
    a1 = cdp.evaluate("players[myId].a")
    cdp.tap(c["chip"]["sniper"]["x"], c["chip"]["sniper"]["y"])     # 狙击槽
    time.sleep(0.4)
    w1 = cdp.evaluate("players[myId].w")
    record("R1", "右半屏瞄准/切枪", w1 == "sniper", "angle=%.2f weapon=%s" % (a1, w1))
    am0 = cdp.evaluate("players[myId].am")
    cdp.hold(c["fire"]["x"], c["fire"]["y"], 700)                   # 开火键
    time.sleep(0.4)
    am1 = cdp.evaluate("players[myId].am")
    record("R1", "开火与弹药消耗", am1 < am0, "ammo %s→%s" % (am0, am1))
    cdp.tap(c["ring"]["reload"]["x"], c["ring"]["reload"]["y"])     # 换弹键
    time.sleep(3)
    am2 = cdp.evaluate("players[myId].am")
    record("R1", "换弹与备弹扣减", am2 > am1, "ammo=%s" % am2)
    err = cdp.evaluate("window.__errors||[]")
    record("R1", "页面无 JS 报错", not err, str(err))
    record("R1", "截图 HUD", bool(shot("r1_hud_final", outdir)), "r1_hud_final.png")
    cdp.close()

    # ---------------- Round 2：化学机制 / AI / 弹药补给（手机端玩家主动参战） ----------------
    cdp = launch_and_cdp(host_mode=HOST_MODE, map_switch=HOST_MAP_SWITCH)
    ok = join(cdp, "R2Final", bots="4")
    time.sleep(1)
    n = json.loads(cdp.evaluate("JSON.stringify(Object.keys(players).length)"))
    record("R2", "4 AI + 玩家同局", n >= 4, "players=%s" % n)

    # 用客户端真实的输入状态驱动玩家（mobile.js 的 touchMove/aimAng/touchFire 是全局量），
    # 这样断言针对的是「主机侧化学/AI/补给是否真在跑」，而不是等机器人自己送上门。
    DRIVE = ("JSON.stringify((function(){var me=players[myId];if(!me)return null;"
             "var best=null,bd=1e9;for(var k in players){var q=players[k];"
             "if(Number(k)===Number(myId)||q.team===me.team)continue;"
             "var d=Math.hypot(q.x-me.x,q.y-me.y);if(d<bd){bd=d;best=q;}}"
             "if(!best)return null;var ux=(best.x-me.x)/bd,uy=(best.y-me.y)/bd;"
             "touchMove.active=true;touchMove.id=1;touchMove.dx=ux;touchMove.dy=uy;"
             "aimAng=Math.atan2(uy,ux);touchFire=true;"
             "return {d:Math.round(bd)};})())")
    GO_PAD = ("JSON.stringify((function(){var me=players[myId];if(!me)return null;"
              "var ps=(typeof pads!=='undefined'?pads:[]);var best=null,bd=1e9;"
              "for(var i=0;i<ps.length;i++){var a=ps[i];"
              "var d=Math.hypot(a.x-me.x,a.y-me.y);if(d<bd){bd=d;best=a;}}"
              "if(!best)return null;var ux=(best.x-me.x)/bd,uy=(best.y-me.y)/bd;"
              "touchMove.active=true;touchMove.id=1;touchMove.dx=ux;touchMove.dy=uy;"
              "aimAng=Math.atan2(uy,ux);touchFire=(bd>150);"
              "return {d:Math.round(bd)};})())")
    STOP = "touchMove.active=false;touchMove.dx=0;touchMove.dy=0;touchFire=false;"

    seen, err = set(), []
    T = {"min_ph": 9e9, "max_ph": -9e9, "min_buf": 9e9, "kills": 0,
         "kill_events": 0, "supply": 0, "shoot": 0, "hits": 0, "max_ammo_drop": 0}

    def harvest():
        st = status()
        for p in st["players"]:
            if p["ph"] is not None:
                T["min_ph"] = min(T["min_ph"], p["ph"])
                T["max_ph"] = max(T["max_ph"], p["ph"])
            T["min_buf"] = min(T["min_buf"], p["buf"])
            T["kills"] = max(T["kills"], p["k"])
            if not p["bot"]:
                T["max_ammo_drop"] = max(T["max_ammo_drop"], p.get("am", 0))
        for e in st["events"]:
            key = json.dumps(e, sort_keys=True, ensure_ascii=False)
            if key in seen:
                continue
            seen.add(key)
            et = e.get("t")
            if et == "kill":
                T["kill_events"] += 1
            elif et == "shoot":
                T["shoot"] += 1
            elif et in ("hit", "dmg"):
                T["hits"] += 1
            elif et == "supply":
                T["supply"] += 1

    # 阶段 A：主动找敌人对枪（35s）
    t0 = time.time()
    while time.time() - t0 < min(35.0, max(15.0, args.soak * 0.6)):
        harvest()
        cdp.evaluate(DRIVE)
        err = err or (cdp.evaluate("window.__errors||[]") or [])
        time.sleep(0.5)
    # 阶段 B：打空弹药后主动跑去补给点（剩余时间）
    t1 = time.time()
    while time.time() - t1 < min(30.0, max(12.0, args.soak * 0.4)):
        harvest()
        cdp.evaluate(GO_PAD)
        time.sleep(0.5)
    cdp.evaluate(STOP)

    record("R2", "酸碱 pH 越界", T["max_ph"] - T["min_ph"] > 4 or T["min_ph"] < 3 or T["max_ph"] > 11,
           "pH %.2f–%.2f" % (T["min_ph"], T["max_ph"]))
    record("R2", "击杀结算与复活", T["kill_events"] > 0,
           "kill=%d（最低缓冲 %.0f%%）" % (T["kill_events"], T["min_buf"]))
    record("R2", "命中/击杀事件", T["shoot"] > 0 and T["hits"] > 0,
           "shoot=%d hit=%d" % (T["shoot"], T["hits"]))
    record("R2", "弹药补给点事件", T["supply"] > 0, "supply=%d" % T["supply"])
    record("R2", "页面无 JS 报错", not err, str(err[:2]))
    record("R2", "截图化学 HUD", bool(shot("r2_chem_final", outdir)), "r2_chem_final.png")
    cdp.close()

    # ---------------- Round 3：地图/面板/重连 ----------------
    cdp = launch_and_cdp(host_mode=HOST_MODE, map_switch=HOST_MAP_SWITCH)
    ok = join(cdp, "R3Final")
    record("R3", "第三次启动并加入", ok, "joined=%s" % ok)
    maps = set()
    for _ in range(10):
        maps.add(status()["map"])
        time.sleep(2)
    record("R3", "地图自动轮换", len(maps) >= 2, "maps=%s" % sorted(maps))
    c3 = ui_centers(cdp)
    # 每个控件点一次，确认命中不串键（防“开火键吃掉聊天/管理”回归）
    hitset, bad_hit = [], []
    for kind, mid in [("menu", "board"), ("menu", "chat"), ("menu", "admin"),
                      ("ring", "reload"), ("ring", "smoke"), ("ring", "skill"),
                      ("chip", "rifle"), ("chip", "pistol")]:
        p = c3[kind][mid]
        cdp.tap(p["x"], p["y"])
        time.sleep(0.35)
        got = cdp.evaluate("window.__mobileLastHit")
        want = "%s:%s" % (kind, mid)
        hitset.append("%s→%s" % (want, got))
        if got != want:
            bad_hit.append("%s→%s" % (want, got))
        cdp.evaluate("showBoard=false")
        cdp.evaluate("showAdmin=false")
        cdp.evaluate("(function(){var x=document.getElementById('chatInput');"
                     "if(x){x.classList.remove('show');x.blur();}})()")
        cdp.evaluate("smokeAim=false")
    record("R3", "控件命中不串键", not bad_hit, "; ".join(hitset))

    cdp.tap(c3["menu"]["board"]["x"], c3["menu"]["board"]["y"])
    time.sleep(0.4)
    board = cdp.evaluate("showBoard")
    record("R3", "战绩板/实验记录", bool(board), "showBoard=%s" % board)
    shot("r3_board_final", outdir)
    cdp.tap(c3["menu"]["board"]["x"], c3["menu"]["board"]["y"])
    cdp.tap(c3["menu"]["chat"]["x"], c3["menu"]["chat"]["y"])
    time.sleep(0.4)
    chat = cdp.evaluate("document.getElementById('chatInput').className")
    record("R3", "聊天输入打开", "show" in (chat or ""), "class=%r" % chat)
    cdp.tap(c3["zone"]["x"], c3["zone"]["y"])       # 点画面收起聊天
    time.sleep(0.3)
    cdp.tap(c3["menu"]["admin"]["x"], c3["menu"]["admin"]["y"])
    time.sleep(0.5)
    admin = cdp.evaluate("showAdmin")
    record("R3", "管理面板打开", bool(admin), "showAdmin=%s" % admin)
    shot("r3_admin_final", outdir)
    err = cdp.evaluate("window.__errors||[]")
    record("R3", "页面无 JS 报错", not err, str(err))
    cdp.close()

    # 断线重连
    cdp = launch_and_cdp(host_mode=HOST_MODE, map_switch=HOST_MAP_SWITCH)
    ok = join(cdp, "R3Rejoin")
    record("R3", "断线重连后再加入", ok, "joined=%s" % ok)
    cdp.close()

    # ---------------- 结论文档 ----------------
    os.makedirs(outdir, exist_ok=True)
    md = ["# 安卓真机验收结论（自动生成）", "",
          "- 设备：HUAWEI FOA-AL00（Android 12，1084×2412）",
          "- APK：android/app/build/outputs/apk/debug/app-debug.apk",
          "- 服务端：Reaction Protocol，PC 端 `server.py`，adb reverse tcp:8765",
          "- 轮次：3 轮（操作 / 化学与 AI / 地图与面板与重连）", "",
          "| 轮次 | 项目 | 结果 | 证据/数值 |", "|---|---|---|---|"]
    for r, item, ok, detail in RESULTS:
        md.append("| %s | %s | %s | %s |" % (r, item, "通过" if ok else "**失败**", detail))
    md += ["", "截图：`r1_hud_final.png`、`r2_chem_final.png`、`r3_board_final.png`、`r3_admin_final.png`"]
    out_md = os.path.join(outdir, "验收结论.md")
    with open(out_md, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    fails = [x for x in RESULTS if not x[2]]
    print("\n%d checks, %d failed" % (len(RESULTS), len(fails)))
    print("saved", out_md)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
