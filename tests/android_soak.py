# -*- coding: utf-8 -*-
"""真机 soak：轮询 /status.json 与 WebView 页面状态，验证化学/AI/弹药机制。

用法：python tests/android_soak.py --seconds 60 --out 测试/安卓验收/soak_round2.csv
前置：adb forward tcp:9222 localabstract:webview_devtools_remote_<pid>
"""
import argparse
import csv
import json
import os
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from tools.cdp_eval import CDP, list_targets  # noqa: E402


def status(url="http://127.0.0.1:8765/status.json", retries=12):
    """用裸 socket 请求，避免 urllib 在服务端高负载下的偶发空响应。"""
    import socket as _socket
    host, port = "127.0.0.1", 8765
    last = None
    for _ in range(retries):
        try:
            s = _socket.create_connection((host, port), timeout=6)
            try:
                s.sendall(b"GET /status.json HTTP/1.1\r\nHost: %s:%d\r\n"
                          b"Connection: close\r\n\r\n" % (host.encode(), port))
                buf = b""
                while True:
                    c = s.recv(8192)
                    if not c:
                        break
                    buf += c
            finally:
                s.close()
            head, _, body = buf.partition(b"\r\n\r\n")
            if body:
                return json.loads(body.decode("utf-8"))
            last = "empty body (%d bytes head)" % len(head)
        except Exception as e:
            last = e
        time.sleep(0.5)
    raise RuntimeError("status failed: %r" % (last,))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--server", default="http://127.0.0.1:8765")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    targets = [t for t in list_targets() if t.get("type") == "page"]
    cdp = CDP(targets[0]["webSocketDebuggerUrl"]) if targets else None
    rows = []
    ev_types = {}
    seen_ev = set()
    supply = 0
    bot_weapon_samples = {}
    bot_snip_used = {}
    min_ph, max_ph, min_buf = 9e9, -9e9, 9e9
    max_kills = {}
    deaths = 0
    errors = []
    t0 = time.time()
    while time.time() - t0 < args.seconds:
        try:
            st = status(args.server)
        except Exception as e:
            print("status skip:", repr(e))
            time.sleep(1.0)
            continue
        page = None
        if cdp is not None:
            try:
                page = cdp.evaluate(
                    "JSON.stringify({joined:joined,id:myId,x:(players[myId]||{}).x,"
                    "y:(players[myId]||{}).y,ph:(players[myId]||{}).ph,"
                    "buf:(players[myId]||{}).buf,w:(players[myId]||{}).w,"
                    "am:(players[myId]||{}).am,res:(players[myId]||{}).res,"
                    "err:(window.__errors||[])})")
                page = json.loads(page) if isinstance(page, str) else page
            except Exception as e:
                page = {"cdp_error": str(e)}
        row = {"t": round(time.time() - t0, 1), "map": st.get("map"),
               "page": json.dumps(page, ensure_ascii=False)}
        for p in st.get("players", []):
            key = "p%d" % p["id"]
            row[key + "_w"] = p["w"]
            row[key + "_ph"] = p["ph"]
            row[key + "_buf"] = p["buf"]
            row[key + "_res"] = p["res"]
            if p["ph"] is not None:
                min_ph = min(min_ph, p["ph"]); max_ph = max(max_ph, p["ph"])
            min_buf = min(min_buf, p["buf"])
            if p["bot"]:
                bot_weapon_samples.setdefault(p["id"], []).append(p["w"])
                bot_snip_used[p["id"]] = p.get("sniper_used", 0.0)
            if not p["alive"]:
                deaths += 1
            max_kills[p["id"]] = max(max_kills.get(p["id"], 0), p["k"])
        for e in st.get("events", []):
            key = json.dumps(e, sort_keys=True, ensure_ascii=False)
            if key in seen_ev:
                continue
            seen_ev.add(key)
            ev_types[e.get("t")] = ev_types.get(e.get("t"), 0) + 1
            if e.get("t") == "supply":
                supply += 1
        if page and page.get("err"):
            errors.extend(page["err"])
        rows.append(row)
        time.sleep(1.0)
    if cdp is not None:
        cdp.close()

    print("samples=%d map_last=%s" % (len(rows), rows[-1]["map"] if rows else "-"))
    print("pH range %.2f–%.2f, min buffer %.1f%%" % (min_ph, max_ph, min_buf))
    print("event types:", json.dumps(ev_types, ensure_ascii=False))
    print("supply events:", supply)
    for bid, ws in bot_weapon_samples.items():
        frac = sum(1 for w in ws if w == "sniper") / float(len(ws))
        print("bot %d on-device sniper sample frac %.3f, ai_snip_used %.2fs" %
              (bid, frac, bot_snip_used.get(bid, 0.0)))
    print("deadsampled=%d kills_max=%s js_errors=%s" %
          (deaths, json.dumps(max_kills, ensure_ascii=False), errors[:3]))
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        keys = sorted(set(k for r in rows for k in r))
        with open(args.out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader(); w.writerows(rows)
        print("saved", args.out)


if __name__ == "__main__":
    main()
