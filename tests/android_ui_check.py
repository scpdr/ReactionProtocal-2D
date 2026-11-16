# -*- coding: utf-8 -*-
"""真机 UI 校验：Reaction Protocol 手机版（华为 FOA-AL00 / Android 12）。

校验内容
  1) 页面内 window.__mobileLayoutReport()（= ML.report(VW,VH,opts) + 当前 VW/VH/DPR）
     断言：overlaps 为空、vw/vh 与真机 CSS 视口一致（±2px）、关键元素齐全；
  2) 依次点击 board / chat / admin / reload / smoke / skill 的中心坐标（坐标每次从
     window.__mobileLayoutReport().layout 现取，不硬编码），断言：
       - board  → showBoard 翻转、__mobileLastHit 记录 menu:board
       - chat   → chatInput.className 含 show、__mobileLastHit 记录 menu:chat
       - admin  → showAdmin 翻转、__mobileLastHit 记录 menu:admin
       - reload/smoke/skill → __mobileLastHit 记录 ring:<id>
       - 全程无误触开火（__mobileLastHit 不得为 fire:）
  3) 截图 测试/安卓验收/ui_layout.png（adb screencap + pull 到 %TEMP% 再 Copy-Item，
     规避中文路径 adb pull 失败的问题）；
  4) 结果写入 测试/安卓验收/UI校验.md（表：项目/期望/实测/结论）。

前置
  - 手机关联 USB 且 `adb devices` 可见；
  - 服务器已在 PC 上运行（默认 127.0.0.1:8765）：
      cd lan-shooter-v2; $env:PYTHONIOENCODING='utf-8'; python server.py 8765 --bots 2 --no-browser
    脚本会自动 `adb reverse tcp:8765 tcp:8765`，并用 window.__mobileLayoutReport 的
    WebView devtools socket 做 `adb forward tcp:9222 localabstract:<sock>`。

用法
  python tests/android_ui_check.py
  python tests/android_ui_check.py --serial 9TM9K23404002482 --restart --spawn-server
  python tests/android_ui_check.py --no-launch --no-screenshot --skip-node
退出码
  0 = 全部通过；1 = 有断言/契约失败；2 = 真机或 CDP 环境不可用（无设备 / 无 WebView /
      服务器未启动）。

注意：本脚本只读页面状态、只做点击与截图，不修改 public/ 下任何代码。
"""
import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tools.cdp_eval import CDP, list_targets  # noqa: E402

try:  # 复用 soak 脚本里的 status()（裸 socket，抗高负载）
    from tests.android_soak import status as _soak_status
except Exception:  # pragma: no cover
    _soak_status = None

DEFAULT_SERIAL = "9TM9K23404002482"
PACKAGE = "com.reactionprotocol.app"
ACTIVITY = ".MainActivity"
DEFAULT_SERVER = "127.0.0.1:8765"
DEFAULT_PORT = 9222
MD_PATH = os.path.join("测试", "安卓验收", "UI校验.md")
PNG_PATH = os.path.join("测试", "安卓验收", "ui_layout.png")
EXPECT_VW, EXPECT_VH = 768, 362          # 环境事实：真机横屏 CSS 视口约 768×362

V_PASS, V_FAIL, V_WARN, V_SKIP = "PASS", "FAIL", "WARN", "SKIP"
ICON = {V_PASS: "✅ 通过", V_FAIL: "❌ 失败", V_WARN: "⚠️ 提示", V_SKIP: "⏭ 跳过"}


# ---------------------------------------------------------------- 基础工具
def run(cmd, serial=None, timeout=40, binary=False):
    full = ["adb"] + (["-s", serial] if serial else []) + list(cmd)
    p = subprocess.run(full, capture_output=True, timeout=timeout)
    if binary:
        return p.returncode, p.stdout
    return (p.returncode,
            p.stdout.decode("utf-8", "replace").strip(),
            p.stderr.decode("utf-8", "replace").strip())


class Report(object):
    def __init__(self):
        self.rows = []
        self.notes = []
        self.cmds = []
        self.env_error = None

    def add(self, section, item, expect, actual, verdict, detail="", cmd=""):
        self.rows.append({"section": section, "item": item, "expect": str(expect),
                          "actual": str(actual), "verdict": verdict,
                          "detail": detail, "cmd": cmd})
        flag = {V_PASS: "[PASS]", V_FAIL: "[FAIL]", V_WARN: "[WARN]", V_SKIP: "[SKIP]"}[verdict]
        print("%s %s | 期望 %s | 实测 %s" % (flag, item, expect, actual))
        if detail:
            print("       " + detail)

    def note(self, text):
        self.notes.append(text)
        print("[note] " + text)

    def count(self, verdict):
        return sum(1 for r in self.rows if r["verdict"] == verdict)

    def failed(self):
        return self.count(V_FAIL) > 0


def js_eval(cdp, expr, retries=3, delay=0.4):
    last = None
    for _ in range(retries):
        try:
            out = cdp.evaluate(expr)
            if isinstance(out, dict) and "error" in out:
                last = RuntimeError(out["error"])
            else:
                return out
        except Exception as e:                      # noqa: BLE001
            last = e
        time.sleep(delay)
    raise last


def js_json(cdp, expr):
    raw = js_eval(cdp, expr)
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except Exception:                               # noqa: BLE001
        return {"__raw": raw}


STATE_EXPR = (
    "JSON.stringify({"
    "showBoard:(typeof showBoard!=='undefined'?!!showBoard:null),"
    "showAdmin:(typeof showAdmin!=='undefined'?!!showAdmin:null),"
    "chatClass:(function(){var e=document.getElementById('chatInput');return e?String(e.className):null;})(),"
    "lastHit:(typeof window.__mobileLastHit!=='undefined'?window.__mobileLastHit:null),"
    "touchFire:(typeof touchFire!=='undefined'?!!touchFire:null),"
    "smokeAim:(typeof smokeAim!=='undefined'?!!smokeAim:null),"
    "joined:(typeof joined!=='undefined'?!!joined:null),"
    "isHost:!!window._isHost,"
    "hostTag:!window._isHost,"
    "adminRects:(typeof mlAdminRects!=='undefined'&&mlAdminRects?"
    "mlAdminRects.map(function(r){return {x:r.x,y:r.y,w:r.w,h:r.h,act:(r.msg&&r.msg.act)||null};}):null),"
    "innerW:innerWidth,innerH:innerHeight,dpr:window.devicePixelRatio||1,"
    "VW:(typeof VW!=='undefined'?VW:null),VH:(typeof VH!=='undefined'?VH:null),"
    "DPR:(typeof DPR!=='undefined'?DPR:null)})"
)

REPORT_EXPR = (
    "JSON.stringify((function(){"
    "try{"
    "if(typeof window.__mobileLayoutReport!=='function')return {ok:false,err:'no __mobileLayoutReport'};"
    "var r=window.__mobileLayoutReport();"
    "if(!r)return {ok:false,err:'__mobileLayoutReport() 返回空'};"
    "return {ok:true,report:r,hasLayout:!!r.layout,"
    "innerW:innerWidth,innerH:innerHeight,dpr:window.devicePixelRatio||1,"
    "VW:(typeof VW!=='undefined'?VW:null),VH:(typeof VH!=='undefined'?VH:null),"
    "DPRg:(typeof DPR!=='undefined'?DPR:null)};"
    "}catch(e){return {ok:false,err:String(e&&e.message||e)};}"
    "})())"
)


def fetch_report(cdp):
    d = js_json(cdp, REPORT_EXPR)
    if not isinstance(d, dict) or not d.get("ok"):
        raise RuntimeError((d or {}).get("err", "report 不可用"))
    return d


def state(cdp):
    return js_json(cdp, STATE_EXPR) or {}


def last_hit_kind_id(raw):
    if not isinstance(raw, str):
        return None, None
    if ":" in raw:
        k, _, i = raw.partition(":")
        return k, (i or None)
    return raw or None, None


def center_of(rect):
    return float(rect["x"]) + float(rect["w"]) / 2.0, float(rect["y"]) + float(rect["h"]) / 2.0


def ring_center(layout, rid):
    for r in layout.get("ring") or []:
        if r.get("id") == rid:
            return float(r["cx"]), float(r["cy"])
    return None


def local_status(host, port, retries=8):
    last = None
    for _ in range(retries):
        try:
            s = socket.create_connection((host, port), timeout=5)
            try:
                s.sendall(b"GET /status.json HTTP/1.1\r\nHost: %s:%d\r\nConnection: close\r\n\r\n"
                          % (host.encode(), port))
                buf = b""
                while True:
                    c = s.recv(8192)
                    if not c:
                        break
                    buf += c
            finally:
                s.close()
            _, _, body = buf.partition(b"\r\n\r\n")
            if body:
                return json.loads(body.decode("utf-8"))
            last = "empty body"
        except Exception as e:                      # noqa: BLE001
            last = e
        time.sleep(0.5)
    raise RuntimeError("status failed: %r" % (last,))


def server_alive(host, port):
    if port == 8765 and _soak_status is not None:
        try:
            return _soak_status()
        except Exception:                           # noqa: BLE001
            return None
    try:
        return local_status(host, port)
    except Exception:                               # noqa: BLE001
        return None


# ---------------------------------------------------------------- 真机连接
def find_socket(serial):
    """返回 WebView devtools socket 名候选（优先 /proc/net/unix，其次 pidof）。"""
    cands = []
    try:
        _, out, _ = run(["shell", "cat", "/proc/net/unix"], serial=serial)
        for m in re.finditer(r"webview_devtools_remote_(\d+)", out or ""):
            name = "webview_devtools_remote_%s" % m.group(1)
            if name not in cands:
                cands.append(name)
    except Exception:                               # noqa: BLE001
        pass
    try:
        _, pid, _ = run(["shell", "pidof", PACKAGE], serial=serial)
        pid = (pid or "").split()[0] if pid else ""
        if pid:
            cands.append("webview_devtools_remote_%s" % pid)
    except Exception:                               # noqa: BLE001
        pass
    return cands


def pick_page_target(port):
    targets = [t for t in list_targets(port=port) if t.get("type") == "page"]
    if not targets:
        return None
    for t in targets:
        url = t.get("url") or ""
        if "mobile" in url:
            return t
    return targets[0]


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser(description="Reaction Protocol 真机 UI 校验")
    ap.add_argument("--serial", default=DEFAULT_SERIAL)
    ap.add_argument("--server", default=DEFAULT_SERVER, help="app 连接的服务端 host:port")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help="PC 侧 CDP 转发端口")
    ap.add_argument("--out", default=MD_PATH, help="Markdown 报告路径（相对仓库根）")
    ap.add_argument("--png", default=PNG_PATH, help="截图路径（相对仓库根）")
    ap.add_argument("--launch", dest="launch", action="store_true", default=True)
    ap.add_argument("--no-launch", dest="launch", action="store_false")
    ap.add_argument("--restart", action="store_true", help="先 force-stop 再启动")
    ap.add_argument("--spawn-server", action="store_true", help="若 8765 未监听则自行拉起 server.py")
    ap.add_argument("--no-reverse", action="store_true",
                    help="跳过 adb reverse（共享设备上由 Lead 统一维护时使用）")
    ap.add_argument("--reuse-forward", action="store_true",
                    help="复用已有 tcp:<port> forward，不做 forward --remove/新增（共享设备安全模式）")
    ap.add_argument("--screenshot", dest="screenshot", action="store_true", default=True)
    ap.add_argument("--no-screenshot", dest="screenshot", action="store_false")
    ap.add_argument("--run-node", dest="run_node", action="store_true", default=True)
    ap.add_argument("--skip-node", dest="run_node", action="store_false")
    ap.add_argument("--tap-wait", type=float, default=0.45, help="点击后等待状态刷新秒数")
    args = ap.parse_args()

    rep = Report()
    t0 = time.time()
    host, _, port_s = args.server.partition(":")
    port = int(port_s or 8765)
    spawn = None
    cdp = None

    def md_out_path():
        return args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)

    def png_out_path():
        return args.png if os.path.isabs(args.png) else os.path.join(ROOT, args.png)

    try:
        # ---- 0. 设备 ----
        rc, devs, err = run(["devices"])
        rep.cmds.append("adb devices")
        if rc != 0:
            raise RuntimeError("adb 不可用: %s" % (err or "unknown"))
        if args.serial not in devs:
            raise RuntimeError("设备 %s 未连接：\n%s" % (args.serial, devs))
        rep.add("环境", "adb 设备在线", args.serial, "在线", V_PASS, cmd="adb devices")

        _, wm, _ = run(["shell", "wm", "size"], serial=args.serial)
        _, rel, _ = run(["shell", "getprop", "ro.build.version.release"], serial=args.serial)
        _, model, _ = run(["shell", "getprop", "ro.product.model"], serial=args.serial)
        rep.cmds.append("adb -s %s shell wm size" % args.serial)
        rep.note("设备: %s / Android %s / %s" % (model, rel, wm))

        # ---- 1. 服务器 ----
        st = server_alive(host, port)
        if st is None and args.spawn_server:
            logf = open(os.path.join(ROOT, "logs", "ui_check_server.log"), "ab")
            env = dict(os.environ, PYTHONIOENCODING="utf-8")
            spawn = subprocess.Popen([sys.executable, "server.py", str(port), "--bots", "2", "--no-browser"],
                                     cwd=ROOT, env=env, stdout=logf, stderr=subprocess.STDOUT)
            rep.note("已自行拉起 server.py pid=%s（日志 logs/ui_check_server.log）" % spawn.pid)
            for _ in range(40):
                time.sleep(0.5)
                st = server_alive(host, port)
                if st is not None:
                    break
        if st is None:
            raise RuntimeError(
                "服务器 %s 未监听。请先启动：\n"
                "  cd lan-shooter-v2; $env:PYTHONIOENCODING='utf-8'; python server.py 8765 --bots 2 --no-browser"
                % args.server)
        players = st.get("players") or []
        rep.add("环境", "服务器 /status.json 可达", "%s 返回 JSON" % args.server,
                "玩家 %d（AI %d）" % (len(players), sum(1 for p in players if p.get("bot"))), V_PASS)
        rep.cmds.append("python server.py 8765 --bots 2 --no-browser")

        # ---- 2. adb reverse + 启动 app ----
        if args.no_reverse:
            rep.note("按 --no-reverse 跳过 adb reverse（假设 Lead 已设好）")
        else:
            run(["reverse", "tcp:%d" % port, "tcp:%d" % port], serial=args.serial)
            rep.cmds.append("adb -s %s reverse tcp:%d tcp:%d" % (args.serial, port, port))
        if args.launch:
            if args.restart:
                run(["shell", "am", "force-stop", PACKAGE], serial=args.serial)
                time.sleep(0.8)
            rc, out, err = run(["shell", "am", "start", "-W", "-n",
                                "%s/%s" % (PACKAGE, ACTIVITY), "--es", "server", args.server],
                               serial=args.serial, timeout=40)
            rep.cmds.append("adb -s %s shell am start -W -n %s/%s --es server %s"
                            % (args.serial, PACKAGE, ACTIVITY, args.server))
            if rc != 0:
                raise RuntimeError("am start 失败: %s %s" % (out, err))
            rep.note("app 启动: " + (out or "").replace("\n", " ")[:160])

        # ---- 3. WebView devtools socket → adb forward → CDP ----
        targets = []
        sock_used = None
        if args.reuse_forward:
            try:
                targets = [t for t in list_targets(port=args.port) if t.get("type") == "page"]
            except Exception:                       # noqa: BLE001
                targets = []
            if targets:
                sock_used = "复用现有 forward tcp:%d" % args.port
        if not targets:
            run(["forward", "--remove", "tcp:%d" % args.port], serial=args.serial)
            for sock in find_socket(args.serial):
                run(["forward", "tcp:%d" % args.port, "localabstract:%s" % sock], serial=args.serial)
                rep.cmds.append("adb -s %s forward tcp:%d localabstract:%s"
                                % (args.serial, args.port, sock))
                for _ in range(12):
                    try:
                        targets = [t for t in list_targets(port=args.port) if t.get("type") == "page"]
                    except Exception:               # noqa: BLE001
                        targets = []
                    if targets:
                        break
                    time.sleep(0.5)
                if targets:
                    sock_used = sock
                    break
        if not targets:
            raise RuntimeError("未找到 WebView page target（socket 候选：%s）。"
                               "确认 app 已启动且 WebView 调试可用。" % (find_socket(args.serial) or "无"))
        rep.add("环境", "WebView devtools 可达", "page target ≥1",
                "%d 个（socket %s）" % (len(targets), sock_used), V_PASS,
                cmd="adb -s %s shell cat /proc/net/unix | findstr webview_devtools_remote"
                    % args.serial)

        tgt = pick_page_target(args.port)
        cdp = CDP(tgt["webSocketDebuggerUrl"])
        rep.cmds.append("python tools/cdp_eval.py \"JSON.stringify(window.__mobileLayoutReport())\"")
        rep.note("page url: " + str(tgt.get("url")))

        # 等页面就绪
        ready = False
        for _ in range(40):
            try:
                v = js_eval(cdp, "typeof window.__mobileLayoutReport", retries=1)
                if v == "function":
                    ready = True
                    break
            except Exception:                       # noqa: BLE001
                pass
            time.sleep(0.5)
        if not ready:
            rep.add("布局报告", "window.__mobileLayoutReport 存在", "function", "缺失（Lead 尚未暴露）",
                    V_FAIL, "mobile.js 未实现 __mobileLayoutReport；Node 侧可用 tests/mobile_layout_check.js 先验")
            raise RuntimeError("__mobileLayoutReport 缺失")

        # ---- 4. 布局报告断言 ----
        data = fetch_report(cdp)
        r = data["report"]
        layout = r.get("layout")
        inner_w, inner_h = data.get("innerW"), data.get("innerH")
        dpr = data.get("dpr")
        rep.add("布局报告", "window.__mobileLayoutReport() 可读", "返回对象",
                "OK（%d 个字段）" % len(r), V_PASS)

        rvw, rvh = r.get("vw"), r.get("vh")
        rep.add("布局报告", "vw 与真机 CSS 视口一致(±2)", "innerWidth=%s" % inner_w,
                "report.vw=%s" % rvw,
                V_PASS if (isinstance(rvw, (int, float)) and isinstance(inner_w, (int, float))
                           and abs(rvw - inner_w) <= 2) else V_FAIL)
        rep.add("布局报告", "vh 与真机 CSS 视口一致(±2)", "innerHeight=%s" % inner_h,
                "report.vh=%s" % rvh,
                V_PASS if (isinstance(rvh, (int, float)) and isinstance(inner_h, (int, float))
                           and abs(rvh - inner_h) <= 2) else V_FAIL)
        if r.get("VW") is not None and r.get("VH") is not None:
            rep.add("布局报告", "报告含 VW/VH 且与 vw/vh 一致(±2)",
                    "VW/VH ≈ %s×%s" % (rvw, rvh),
                    "VW=%s VH=%s" % (r.get("VW"), r.get("VH")),
                    V_PASS if (abs((r.get("VW") or 0) - (rvw or 0)) <= 2
                               and abs((r.get("VH") or 0) - (rvh or 0)) <= 2) else V_FAIL)
        else:
            rep.add("布局报告", "报告含 VW/VH 字段（契约）", "顶层含 VW/VH",
                    "缺失（仅有 vw/vh；已用页面 innerWidth/innerHeight 代替）", V_WARN,
                    "请在 __mobileLayoutReport 里补 {VW,VH,DPR}")
        if r.get("DPR") is not None:
            rep.add("布局报告", "报告含 DPR 且与 devicePixelRatio 一致", "DPR=%s" % dpr,
                    "report.DPR=%s" % r.get("DPR"),
                    V_PASS if (isinstance(r.get("DPR"), (int, float)) and isinstance(dpr, (int, float))
                               and abs(r["DPR"] - dpr) <= 0.01) else V_FAIL)
        else:
            rep.add("布局报告", "报告含 DPR 字段（契约）", "顶层含 DPR", "缺失", V_WARN,
                    "请在 __mobileLayoutReport 里补 {VW,VH,DPR}")
        rep.add("布局报告", "横屏（VW>VH）", "VW>VH", "%s>%s" % (rvw, rvh),
                V_PASS if (isinstance(rvw, (int, float)) and isinstance(rvh, (int, float))
                           and rvw > rvh) else V_FAIL)
        exp_dpr = round((1084 / float(rvh or 1)), 2) if rvh else None
        rep.add("布局报告", "真机像素比自洽", "1084/%s≈%s" % (rvh, exp_dpr), "devicePixelRatio=%s" % dpr,
                V_WARN if (exp_dpr is None or dpr is None or abs(exp_dpr - dpr) > 0.2) else V_PASS,
                "设备物理 1084×2412，横屏 CSS 视口约 %dx%d（环境事实）" % (EXPECT_VW, EXPECT_VH))
        rep.add("布局报告", "vw/vh 接近环境事实 %d×%d" % (EXPECT_VW, EXPECT_VH),
                "±4px", "vw=%s vh=%s" % (rvw, rvh),
                V_WARN if (not isinstance(rvw, (int, float)) or abs(rvw - EXPECT_VW) > 4
                           or abs(rvh - EXPECT_VH) > 4) else V_PASS)

        if not isinstance(layout, dict):
            rep.add("布局报告", "报告含 layout（点击坐标来源）", "report.layout 存在",
                    "缺失", V_FAIL, "契约要求 __mobileLayoutReport().layout 可用")
            raise RuntimeError("report.layout 缺失")
        ov = r.get("overlaps")
        rep.add("布局报告", "overlaps 为空", "[]",
                json.dumps(ov, ensure_ascii=False),
                V_PASS if (isinstance(ov, list) and not ov) else V_FAIL,
                cmd="python tools/cdp_eval.py \"JSON.stringify(window.__mobileLayoutReport().overlaps)\"")
        oos = r.get("outOfSafe")
        if oos is not None:
            rep.add("布局报告", "outOfSafe（越安全区元素）为空", "[]",
                    json.dumps(oos, ensure_ascii=False),
                    V_PASS if (isinstance(oos, list) and not oos) else V_FAIL)

        # 关键元素齐全
        rings = [x.get("id") for x in (layout.get("ring") or [])]
        menu = (layout.get("top") or {}).get("menu") or {}
        chips = (layout.get("chips") or {}).get("order") or []
        missing = [i for i in ("reload", "smoke", "skill") if i not in rings]
        miss_menu = [k for k in ("board", "chat", "admin") if k not in menu]
        ok_elems = (not missing and not miss_menu and len(chips) == 6 and layout.get("fire"))
        rep.add("布局报告", "关键元素齐全", "ring reload/smoke/skill + menu board/chat/admin + 6 chips + fire",
                "ring=%s menu=%s chips=%d fire=%s" % (rings, sorted(menu.keys()), len(chips), bool(layout.get("fire"))),
                V_PASS if ok_elems else V_FAIL,
                ("缺少 ring:%s " % missing if missing else "") + ("缺少 menu:%s" % miss_menu if miss_menu else ""))
        rep.note("布局: vw=%s vh=%s safe=%s fire=%s" % (rvw, rvh, layout.get("safe"), layout.get("fire")))

        # ---- 5. 加入房间（若会话已加入则复用，绝不打扰现有对局） ----
        pre = state(cdp)
        if pre.get("joined"):
            joined = True
            used_click = False
            rep.add("点击序列", "复用已加入的会话（不重新 join）", "joined=true",
                    "joined=True（isHost=%s），已有对局，跳过 joinBtn 点击" % pre.get("isHost"), V_PASS,
                    detail="共享设备安全模式：已加入时不再点击开始界面（start 遮罩已隐藏，"
                           "点其坐标会误触发到游戏画面）")
        else:
            join_res = js_json(cdp, (
                "JSON.stringify((function(){var n=document.getElementById('nameInput');"
                "if(n){n.value='UIChk';n.dispatchEvent(new Event('input',{bubbles:true}));}"
                "var b=document.getElementById('joinBtn');"
                "var r=b?b.getBoundingClientRect():null;"
                "return {name:n?n.value:null,btn:r?{x:r.x,y:r.y,w:r.width,h:r.height}:null};})())"))
            if not join_res or not join_res.get("btn"):
                raise RuntimeError("找不到开始界面的 joinBtn/nameInput")
            bx, by = center_of({"x": join_res["btn"]["x"], "y": join_res["btn"]["y"],
                                "w": join_res["btn"]["w"], "h": join_res["btn"]["h"]})
            cdp.tap(bx, by)
            rep.cmds.append("CDP Input.dispatchTouchEvent tap(%0.1f,%0.1f)  # joinBtn 中心" % (bx, by))
            joined, used_click = False, False
            for _ in range(20):
                time.sleep(0.5)
                s = state(cdp)
                if s.get("joined"):
                    joined = True
                    break
            if not joined:                              # 真机 tap 合成 click 失败时兜底
                js_eval(cdp, "(function(){var b=document.getElementById('joinBtn');if(b)b.click();return 1;})()")
                used_click = True
                for _ in range(20):
                    time.sleep(0.5)
                    if state(cdp).get("joined"):
                        joined = True
                        break
            st_now = state(cdp)
            rep.add("点击序列", "加入房间（joinBtn 中心点击）", "joined=true",
                    "joined=%s（isHost=%s）%s" % (st_now.get("joined"), st_now.get("isHost"),
                                                "（真机 tap 未生效，已用 joinBtn.click() 兜底）" if used_click else ""),
                    V_PASS if joined else V_FAIL,
                    cmd="python tools/cdp_eval.py \"document.getElementById('joinBtn').click()\"")
            if not joined:
                raise RuntimeError("未能加入房间（服务器/连接问题），后续点击无法校验")
            if used_click:
                rep.add("点击序列", "joinBtn 真实触摸点击可用", "tap 后 joined=true",
                        "tap 未触发，需 click() 兜底", V_WARN)
        st_now = state(cdp)
        is_host = bool(st_now.get("isHost"))
        rep.add("点击序列", "本客户端为主机（admin 菜单可用）", "isHost=true",
                "isHost=%s" % is_host, V_PASS if is_host else V_WARN,
                "非主机时 admin 行按 SKIP 处理")

        # ---- 6. 依次点击 board/chat/admin/reload/smoke/skill ----
        taps = []

        def tap_target(label, kind, tid):
            d = fetch_report(cdp)                       # 每次现取布局，不硬编码坐标
            lay = d["report"]["layout"]
            if kind == "menu":
                rect = (lay.get("top") or {}).get("menu", {}).get(tid)
                if not rect:
                    return None, None, None, None
                x, y = center_of(rect)
            else:
                c = ring_center(lay, tid)
                if not c:
                    return None, None, None, None
                x, y = c
            cdp.tap(x, y)
            time.sleep(args.tap_wait)
            st = state(cdp)
            taps.append({"label": label, "kind": kind, "id": tid, "x": x, "y": y,
                         "lastHit": st.get("lastHit"),
                         "VW": d["report"].get("vw"), "VH": d["report"].get("vh")})
            return x, y, st, d

        def reset(js, expect_expr, label):
            js_eval(cdp, js)
            time.sleep(0.35)
            return state(cdp)

        # (1) board
        x, y, s, _ = tap_target("board", "menu", "board")
        if s is None:
            rep.add("点击序列", "点击 board 中心", "layout 含 top.menu.board", "缺失", V_FAIL)
        else:
            before = False
            rep.add("点击序列", "点击 board 中心 → showBoard 翻转", "showBoard: False→True",
                    "showBoard=%s（坐标 %0.1f,%0.1f）" % (s.get("showBoard"), x, y),
                    V_PASS if s.get("showBoard") is not before else V_FAIL)
            k, i = last_hit_kind_id(s.get("lastHit"))
            rep.add("点击序列", "board 的 __mobileLastHit", "menu:board",
                    "lastHit=%r" % s.get("lastHit"), V_PASS if i == "board" else V_FAIL)
            # 复位：再点一次 board 关闭（此时触摸被“关板”分支消费）
            cdp.tap(x, y)
            time.sleep(args.tap_wait)
            s2 = state(cdp)
            rep.add("点击序列", "board 复位（再点一次关闭）", "showBoard=False",
                    "showBoard=%s" % s2.get("showBoard"),
                    V_PASS if not s2.get("showBoard") else V_FAIL)

        # (2) chat
        x, y, s, _ = tap_target("chat", "menu", "chat")
        if s is None:
            rep.add("点击序列", "点击 chat 中心", "layout 含 top.menu.chat", "缺失", V_FAIL)
        else:
            cls = s.get("chatClass") or ""
            rep.add("点击序列", "点击 chat 中心 → chatInput.className 含 show", "含 'show'",
                    "%r（坐标 %0.1f,%0.1f）" % (cls, x, y), V_PASS if "show" in cls.split() else V_FAIL)
            k, i = last_hit_kind_id(s.get("lastHit"))
            rep.add("点击序列", "chat 的 __mobileLastHit", "menu:chat",
                    "lastHit=%r" % s.get("lastHit"), V_PASS if i == "chat" else V_FAIL)
            s2 = reset("(function(){var e=document.getElementById('chatInput');"
                       "if(e){e.value='';e.classList.remove('show');e.blur();}return 1;})()",
                       None, "chat")
            rep.add("点击序列", "chat 复位（隐藏输入框，避免吞掉后续点击）", "className 不含 show",
                    "%r" % s2.get("chatClass"), V_PASS if "show" not in (s2.get("chatClass") or "").split() else V_FAIL)

        # (3) admin
        x, y, s, _ = tap_target("admin", "menu", "admin")
        if s is None and not is_host:
            rep.add("点击序列", "点击 admin 中心", "layout 含 top.menu.admin",
                    "非主机，布局未提供 admin 菜单", V_SKIP)
            x = y = None
        elif s is None:
            rep.add("点击序列", "点击 admin 中心", "layout 含 top.menu.admin", "缺失", V_FAIL)
        else:
            rep.add("点击序列", "点击 admin 中心 → showAdmin 翻转", "showAdmin: False→True",
                    "showAdmin=%s（坐标 %0.1f,%0.1f）" % (s.get("showAdmin"), x, y),
                    V_PASS if s.get("showAdmin") else V_FAIL)
            k, i = last_hit_kind_id(s.get("lastHit"))
            rep.add("点击序列", "admin 的 __mobileLastHit", "menu:admin",
                    "lastHit=%r" % s.get("lastHit"), V_PASS if i == "admin" else V_FAIL)
            # 复位：点管理面板自带的“关闭”热区（mlAdminRects 中 act=close）
            close_rect = None
            for rr in (s.get("adminRects") or []):
                if rr.get("act") == "close":
                    close_rect = rr
                    break
            if close_rect:
                cx, cy = center_of(close_rect)
                cdp.tap(cx, cy)
                time.sleep(args.tap_wait)
                s2 = state(cdp)
                rep.add("点击序列", "admin 复位（点面板“关闭”热区）", "showAdmin=False",
                        "showAdmin=%s（坐标 %0.1f,%0.1f）" % (s2.get("showAdmin"), cx, cy),
                        V_PASS if not s2.get("showAdmin") else V_FAIL)
            else:
                s2 = reset("(function(){showAdmin=false;return 1;})()", None, "admin")
                rep.add("点击序列", "admin 复位（未找到关闭热区，用 JS 复位）", "showAdmin=False",
                        "showAdmin=%s" % s2.get("showAdmin"),
                        V_PASS if not s2.get("showAdmin") else V_WARN)

        # (4)(5)(6) reload / smoke / skill
        for label, extra in (("reload", None), ("smoke", None), ("skill", None)):
            x, y, s, _ = tap_target(label, "ring", label)
            if s is None:
                rep.add("点击序列", "点击 %s 中心" % label, "layout.ring 含 %s" % label, "缺失", V_FAIL)
                continue
            k, i = last_hit_kind_id(s.get("lastHit"))
            rep.add("点击序列", "点击 %s 中心 → __mobileLastHit" % label, "ring:%s" % label,
                    "lastHit=%r（坐标 %0.1f,%0.1f）" % (s.get("lastHit"), x, y),
                    V_PASS if i == label else V_FAIL)
            if label == "smoke":
                s2 = state(cdp)
                if s2.get("smokeAim"):
                    cdp.tap(x, y)                       # 再按一次烟雾=取消落点模式
                    time.sleep(args.tap_wait)
                    s3 = state(cdp)
                    rep.add("点击序列", "smoke 复位（再按一次取消落点模式）", "smokeAim=false",
                            "smokeAim=%s" % s3.get("smokeAim"),
                            V_PASS if not s3.get("smokeAim") else V_WARN,
                            "落点模式下会吞掉后续点击，必须先退出")
                else:
                    rep.add("点击序列", "smoke 复位（未进入落点模式）", "smokeAim=false",
                            "smokeAim=False", V_PASS)

        # 误触开火汇总（逐次点击记录 lastHit）
        fire_taps = [t for t in taps if last_hit_kind_id(t.get("lastHit"))[0] == "fire"]
        rep.add("点击序列", "全程无误触开火", "任何菜单/功能键点击 lastHit 均非 'fire:'",
                "%d 次点击 lastHit：%s" % (len(taps),
                                          "; ".join("%s→%r" % (t["label"], t.get("lastHit")) for t in taps)),
                V_PASS if not fire_taps else V_FAIL,
                detail=("命中开火键的点击：%s" % [t["label"] for t in fire_taps]) if fire_taps else "")
        rep.add("点击序列", "点击序列坐标（每次现取布局，非硬编码）", "坐标来自 report.layout",
                "; ".join("%s(%0.1f,%0.1f)@%sx%s" % (t["label"], t["x"], t["y"], t.get("VW"), t.get("VH"))
                          for t in taps), V_PASS)

        # ---- 7. 截图 ----
        if args.screenshot:
            try:
                js_eval(cdp, "(function(){var e=document.getElementById('chatInput');"
                             "if(e){e.classList.remove('show');e.blur();}showBoard=false;showAdmin=false;return 1;})()")
                time.sleep(0.8)
                try:
                    run(["shell", "input", "keyevent", "111"], serial=args.serial)   # ESC 收键盘
                except Exception:                   # noqa: BLE001
                    pass
                remote = "/sdcard/ui_layout.png"
                tmp = os.path.join(tempfile.gettempdir(), "ui_layout.png")
                run(["shell", "screencap", "-p", remote], serial=args.serial)
                rc, out, err = run(["pull", remote, tmp], serial=args.serial, timeout=60)
                rep.cmds.append("adb -s %s shell screencap -p %s" % (args.serial, remote))
                rep.cmds.append("adb -s %s pull %s $env:TEMP\\ui_layout.png" % (args.serial, remote))
                if rc != 0 or not os.path.exists(tmp):
                    raise RuntimeError("adb pull 失败: %s %s" % (out, err))
                dst = png_out_path()
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copyfile(tmp, dst)
                size = os.path.getsize(dst)
                rep.add("截图", "真机截图已保存", "测试/安卓验收/ui_layout.png 存在且 >10KB",
                        "%s（%d 字节）" % (args.png, size), V_PASS if size > 10240 else V_WARN)
            except Exception as e:                  # noqa: BLE001
                rep.add("截图", "真机截图已保存", "ui_layout.png 存在", "失败: %s" % e, V_FAIL)
        else:
            rep.add("截图", "真机截图", "-", "按 --no-screenshot 跳过", V_SKIP)

        # ---- 8. Node 多尺寸布局校验 ----
        if args.run_node:
            node = shutil.which("node")
            if not node:
                rep.add("Node 多尺寸", "node tests/mobile_layout_check.js", "退出码 0", "未找到 node", V_SKIP)
            else:
                try:
                    env = dict(os.environ)
                    env.pop("ML_PATH", None)
                    p = subprocess.run([node, os.path.join("tests", "mobile_layout_check.js")],
                                       cwd=ROOT, capture_output=True, timeout=180, env=env)
                    txt = p.stdout.decode("utf-8", "replace")
                    tail = [l for l in txt.splitlines() if l.startswith("总计") or l.startswith("== minGap")]
                    rep.cmds.append("node tests/mobile_layout_check.js")
                    rep.add("Node 多尺寸", "9 尺寸布局校验全部通过", "退出码 0",
                            "退出码 %d；%s" % (p.returncode, tail[0] if tail else ""),
                            V_PASS if p.returncode == 0 else V_FAIL,
                            detail="\n".join([l for l in txt.splitlines()
                                              if l.startswith("尺寸") or re.match(r"^\d+x\d+", l)])[:2000])
                except Exception as e:              # noqa: BLE001
                    rep.add("Node 多尺寸", "node tests/mobile_layout_check.js", "退出码 0",
                            "执行失败: %s" % e, V_FAIL)
        else:
            rep.add("Node 多尺寸", "node tests/mobile_layout_check.js", "-", "按 --skip-node 跳过", V_SKIP)

    except Exception as e:                          # noqa: BLE001
        rep.env_error = str(e)
        rep.add("环境", "真机 UI 校验可执行", "无环境错误", "中断: %s" % e, V_FAIL)
    finally:
        if cdp is not None:
            try:
                cdp.close()
            except Exception:                       # noqa: BLE001
                pass
        if spawn is not None:
            try:
                spawn.terminate()
                rep.note("已停止自行拉起的 server.py")
            except Exception:                       # noqa: BLE001
                pass

    # ---- 写报告 ----
    write_md(md_out_path(), rep, args, time.time() - t0)
    print("")
    print("== 汇总 ==")
    print("通过 %d / 失败 %d / 提示 %d / 跳过 %d" %
          (rep.count(V_PASS), rep.count(V_FAIL), rep.count(V_WARN), rep.count(V_SKIP)))
    if rep.env_error:
        print("环境错误: %s" % rep.env_error)
    print("报告: %s" % md_out_path())
    if rep.env_error and rep.count(V_PASS) <= 2:
        return 2
    return 1 if rep.failed() else 0


def write_md(path, rep, args, elapsed):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    sections = []
    for row in rep.rows:
        if row["section"] not in sections:
            sections.append(row["section"])
    lines = []
    lines.append("# 手机端 UI 校验报告（Reaction Protocol）")
    lines.append("")
    lines.append("- 生成时间：%s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    lines.append("- 设备：HUAWEI FOA-AL00（Android 12，物理 1084×2412，横屏 CSS 视口约 %d×%d）" % (EXPECT_VW, EXPECT_VH))
    lines.append("- 包名/Activity：`%s/%s`，启动参数 `--es server %s`" % (PACKAGE, ACTIVITY, args.server))
    lines.append("- 校验脚本：`tests/android_ui_check.py`（CDP 真机）+ `tests/mobile_layout_check.js`（Node 多尺寸）")
    lines.append("- 耗时：%.1fs" % elapsed)
    lines.append("")
    lines.append("## 结论")
    lines.append("")
    lines.append("- 通过 **%d** 项，失败 **%d** 项，提示 %d 项，跳过 %d 项 → **%s**"
                 % (rep.count(V_PASS), rep.count(V_FAIL), rep.count(V_WARN), rep.count(V_SKIP),
                    "存在失败" if rep.failed() else "全部通过"))
    if rep.env_error:
        lines.append("- 环境/流程中断：`%s`" % rep.env_error)
    lines.append("")
    for sec in sections:
        lines.append("## %s" % sec)
        lines.append("")
        lines.append("| 项目 | 期望 | 实测 | 结论 |")
        lines.append("|---|---|---|---|")
        for row in [r for r in rep.rows if r["section"] == sec]:
            act = row["actual"].replace("|", "\\|").replace("\n", " ")
            exp = row["expect"].replace("|", "\\|").replace("\n", " ")
            lines.append("| %s | %s | %s | %s |" % (row["item"], exp, act, ICON[row["verdict"]]))
            if row["detail"]:
                lines.append("| ↳ 说明 | | %s | |" % row["detail"].replace("|", "\\|").replace("\n", "<br>"))
        lines.append("")
    if rep.notes:
        lines.append("## 备注")
        lines.append("")
        for n in rep.notes:
            lines.append("- %s" % n)
        lines.append("")
    lines.append("## 复现命令（独立可复现）")
    lines.append("")
    lines.append("前提：Node ≥ 18；Python 3.10+（仅标准库）；`adb` 在 PATH；`public/mobile_layout.js` 已落盘；")
    lines.append("Android 设备经 USB 连接且 `adb devices` 可见；PC 端 `server.py` 已启动。")
    lines.append("两个脚本都能在任意工作目录下运行（内部按 `__file__` 解析仓库根目录）。")
    lines.append("")
    lines.append("### 1) Node 多尺寸布局校验（不需要设备）")
    lines.append("")
    lines.append("```powershell")
    lines.append("cd lan-shooter-v2")
    lines.append("node tests/mobile_layout_check.js             # 期望 18/18 PASS，退出码 0")
    lines.append("node tests/mobile_layout_check.js --verbose   # 追加完整 minGap JSON")
    lines.append("node tests/mobile_layout_check.js --sizes 768x362,640x300   # 只跑指定尺寸")
    lines.append("```")
    lines.append("")
    lines.append("### 2) 真机 UI 校验（需要设备 + 服务器）")
    lines.append("")
    lines.append("```powershell")
    lines.append("cd lan-shooter-v2")
    lines.append("# 终端 A：启动服务器（已在运行可跳过）；另开一个窗口")
    lines.append("$env:PYTHONIOENCODING='utf-8'; python server.py 8765 --bots 2 --no-browser")
    lines.append("# 终端 B：模式一 独立跑（自带 adb reverse / forward / 启动 app）")
    lines.append("python tests/android_ui_check.py --serial %s --restart" % args.serial)
    lines.append("# 终端 B：模式二 共享设备安全模式（复用他人已启动+已加入的会话，"
                 "不 force-stop / 不 am start / 不 adb reverse）")
    lines.append("python tests/android_ui_check.py --serial %s --no-launch --reuse-forward --no-reverse"
                 % args.serial)
    lines.append("```")
    lines.append("")
    lines.append("退出码：`0` 全部通过；`1` 存在断言/契约失败；"
                 "`2` 真机或 CDP 环境不可用（无设备 / 无 WebView devtools / 服务器未监听）。")
    lines.append("")
    lines.append("### 3) 本次实际执行的逐步命令")
    lines.append("")
    lines.append("```powershell")
    for c in rep.cmds:
        lines.append(c)
    lines.append("```")
    lines.append("")
    lines.append("### 4) 本次产物")
    lines.append("")
    lines.append("- 本报告：`%s`" % args.out)
    lines.append("- 真机截图：`%s`" % args.png)
    lines.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    sys.exit(main())
