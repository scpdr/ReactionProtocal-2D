# -*- coding: utf-8 -*-
"""内嵌模式冒烟测试（PC 上模拟安卓 APK 内的服务端启动方式）。

验证「把资源 root 指到别处、不开浏览器、不依赖 argv」时服务端仍完整可用：
  - 随机空闲端口启动 + 后台 accept（block=False，等价于安卓前台服务里的调用方式）
  - /status.json 可用、静态页面可访问、AI 机器人已加入且真的在动
  - AI 策略权重加载成功（load_ai=True）
  - stop() 后端口释放，可以重新绑定

运行： python tests/host_server_embed.py
退出码：0 全通过 / 1 有失败
"""
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

RESULTS = []


def record(item, ok, detail):
    RESULTS.append((item, bool(ok), detail))
    print("[%s] %-26s %s" % ("PASS" if ok else "FAIL", item, detail))


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def http_get(url, timeout=4):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.status, r.read()


def make_embed_root():
    """模拟 APK 首启解包：把 public/ 与 data/ 复制到独立目录。"""
    tmp = tempfile.mkdtemp(prefix="rp_embed_")
    shutil.copytree(os.path.join(ROOT, "public"), os.path.join(tmp, "public"))
    shutil.copytree(os.path.join(ROOT, "data"), os.path.join(tmp, "data"))
    return tmp


def main():
    import server

    # ---- 先验证安卓侧真正调用的入口 host_runner（曾因自锁死导致 serve() 永不返回） ----
    hr_root = make_embed_root()
    hr_port = free_port()
    sys.path.insert(0, os.path.join(ROOT, "android", "app", "src", "main", "python"))
    try:
        import host_runner

        box = {}

        def _call():
            box["info"] = host_runner.serve(hr_root, hr_port, 2, True)

        t = threading.Thread(target=_call, daemon=True)
        t.start()
        t.join(20)
        record("host_runner.serve 不卡死", not t.is_alive(),
               "info=%s" % box.get("info", "<超时未返回>"))
        record("host_runner 端口正确", str(box.get("info", "")).startswith(str(hr_port)),
               "info=%s" % box.get("info"))
        st, body = http_get("http://127.0.0.1:%d/status.json" % hr_port)
        record("host_runner 服务可用", st == 200 and len(json.loads(body.decode("utf-8"))["players"]) == 2,
               "status=%s" % st)
        record("host_runner.stop()", host_runner.stop() and not host_runner.status()["running"],
               "status=%s" % (host_runner.status(),))
    finally:
        shutil.rmtree(hr_root, ignore_errors=True)

    tmp = make_embed_root()
    port = free_port()
    handle = None
    try:
        t0 = time.time()
        handle = server.run_server(port=port, bots=2, root=tmp, open_browser=False,
                                   load_ai=True, block=False, quiet=True)
        dt = time.time() - t0
        record("启动耗时 < 3s", dt < 3.0, "%.2fs port=%s" % (dt, handle.port))
        record("root 生效", server.BASE == os.path.join(tmp, "public"),
               "BASE=%s" % server.BASE)
        record("地图从 root 载入", len(server.MAPS) >= 2,
               "maps=%s" % [m["id"] for m in server.MAPS])
        record("AI 策略加载", server.AI_POLICY is not None,
               "weights=%s" % os.path.join(tmp, "data", "ai_weights.json"))

        # ---- HTTP ----
        st, body = http_get("http://127.0.0.1:%d/status.json" % port)
        js = json.loads(body.decode("utf-8"))
        record("GET /status.json", st == 200 and "players" in js and "map" in js,
               "status=%s players=%d map=%s" % (st, len(js["players"]), js.get("map")))
        bots = [p for p in js["players"] if p.get("bot")]
        record("机器人已在主机侧", len(bots) == 2, "bots=%d" % len(bots))

        st, body = http_get("http://127.0.0.1:%d/" % port)
        html = body.decode("utf-8", "replace")
        record("GET / 返回游戏页", st == 200 and len(html) > 500,
               "status=%s bytes=%d" % (st, len(body)))
        st, body = http_get("http://127.0.0.1:%d/mobile.html" % port)
        record("GET /mobile.html", st == 200 and len(body) > 500,
               "status=%s bytes=%d" % (st, len(body)))
        st, body = http_get("http://127.0.0.1:%d/ui.js" % port)
        record("GET /ui.js", st == 200 and len(body) > 1000,
               "status=%s bytes=%d" % (st, len(body)))

        # ---- 机器人真的在动（证明 game_loop 在跑） ----
        p0 = {p["id"]: (p["x"], p["y"]) for p in js["players"] if p.get("bot")}
        moved = 0
        deadline = time.time() + 8
        while time.time() < deadline and moved < 2:
            time.sleep(1.0)
            _, body = http_get("http://127.0.0.1:%d/status.json" % port)
            cur = {p["id"]: (p["x"], p["y"]) for p in json.loads(body.decode("utf-8"))["players"]
                   if p.get("bot")}
            moved = sum(1 for k in p0 if k in cur and
                        (abs(cur[k][0] - p0[k][0]) + abs(cur[k][1] - p0[k][1])) > 20)
        record("机器人坐标变化", moved >= 1, "移动过的机器人=%d/%d" % (moved, len(p0)))

        # ---- stop() 释放端口 ----
        handle.stop()
        time.sleep(0.6)
        ok_closed = False
        try:
            c = socket.create_connection(("127.0.0.1", port), timeout=1.5)
            c.close()
        except OSError:
            ok_closed = True
        record("stop() 后端口关闭", ok_closed, "port=%d" % port)

        s = socket.socket()
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("0.0.0.0", port))
            ok_rebind = True
        except OSError as e:
            ok_rebind = False
            print("   rebind error:", e)
        finally:
            s.close()
        record("端口可重新绑定", ok_rebind, "port=%d" % port)
        handle = None

        # ---- 审计发现的 4 个缺陷的回归检查 ----
        # 1) WSConn.close() 必须真的断开连接（旧版没这个方法，异常被吞，玩家挂着不动）
        a, b = socket.socketpair()
        wc = server.WSConn(a)
        wc.close()
        closed_room = b.recv(1)
        b.close()
        record("WSConn.close 真断开", wc.closed and closed_room == b"",
               "closed=%s peer_recv=%r" % (wc.closed, closed_room))

        # 2) 换 root（换地图池）后必须重跑 apply_map：WORLD_W/H 要跟着新地图走
        map_root = make_embed_root()
        with open(os.path.join(map_root, "data", "maps.json"), "w", encoding="utf-8") as f:
            json.dump([{"id": "tiny", "name": "TINY", "w": 1000, "h": 800,
                        "walls": [], "spawn": {"red": [50, 50, 10, 10], "blue": [900, 700, 10, 10]},
                        "ammo": [], "labels": []}], f)
        h2 = server.run_server(port=free_port(), bots=0, root=map_root, block=False, quiet=True)
        record("换 root 后地图生效", (server.WORLD_W, server.WORLD_H) == (1000, 800),
               "WORLD=%dx%d map=%s" % (server.WORLD_W, server.WORLD_H,
                                       (server.ACTIVE_MAP or {}).get("id")))
        h2.stop()
        shutil.rmtree(map_root, ignore_errors=True)

        # 3) 重复开服必须重置对局（否则旧比分/旧机器人残留）
        h3 = server.run_server(port=free_port(), bots=3, root=tmp, block=False, quiet=True)
        stagger = sorted(round(p.get("next_think", 0.0) - time.time(), 2)
                         for p in server.GAME.players.values())
        server.GAME.scores["red"] = 999
        h3.stop()
        h4 = server.run_server(port=free_port(), bots=2, root=tmp, block=False, quiet=True)
        record("重开一局状态清空",
               len(server.GAME.players) == 2 and server.GAME.scores == {"red": 0, "blue": 0},
               "players=%d scores=%s" % (len(server.GAME.players), server.GAME.scores))
        record("AI 错峰思考", len(set(stagger)) == len(stagger), "next_think 偏移=%s" % stagger)
        h4.stop()
        time.sleep(0.4)
        loops = [t for t in threading.enumerate() if t.name.startswith("Thread-") and t.is_alive()]
        record("stop() 后无遗留服务线程",
               not any(t for t in threading.enumerate() if t is not threading.current_thread()
                       and t.daemon and t.name.startswith("Thread-") and "host_server" not in t.name),
               "alive_daemon=%d" % len(loops))

    finally:
        if handle is not None:
            try:
                handle.stop()
            except Exception:
                pass
        shutil.rmtree(tmp, ignore_errors=True)

    fails = [x for x in RESULTS if not x[1]]
    print("\n%d 项检查，%d 项失败" % (len(RESULTS), len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
