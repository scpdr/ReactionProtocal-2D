# -*- coding: utf-8 -*-
"""极简 Chrome DevTools Protocol 客户端（只用标准库）。

用途：真机验收时连接安卓 WebView，读取页面内真实状态（joined / 武器 / pH / 报错等）。
  先 adb forward tcp:9222 localabstract:webview_devtools_remote_<pid>
  python tools/cdp_eval.py "JSON.stringify(window.__errors||[])"
"""
import base64
import json
import os
import socket
import struct
import sys
import time
import urllib.request


def list_targets(host="127.0.0.1", port=9222):
    with urllib.request.urlopen("http://%s:%d/json" % (host, port), timeout=5) as r:
        return json.loads(r.read().decode("utf-8"))


class CDP(object):
    def __init__(self, ws_url, timeout=10.0):
        assert ws_url.startswith("ws://")
        rest = ws_url[5:]
        hostport, path = rest.split("/", 1)
        host, port = hostport.split(":")
        self.sock = socket.create_connection((host, int(port)), timeout=timeout)
        self.sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = ("GET /%s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\n"
               "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
               "Sec-WebSocket-Version: 13\r\n\r\n" % (path, hostport, key))
        self.sock.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += self.sock.recv(4096)
        if b"101" not in buf.split(b"\r\n", 1)[0]:
            raise RuntimeError("handshake failed: %r" % buf[:120])
        self.buf = buf.split(b"\r\n\r\n", 1)[1]
        self.next_id = 1

    def _recv_exact(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise RuntimeError("socket closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _recv_frame(self):
        b1, b2 = self._recv_exact(2)
        opcode = b1 & 0x0F
        masked = b2 & 0x80
        ln = b2 & 0x7F
        if ln == 126:
            ln = struct.unpack(">H", self._recv_exact(2))[0]
        elif ln == 127:
            ln = struct.unpack(">Q", self._recv_exact(8))[0]
        mask = self._recv_exact(4) if masked else b""
        data = self._recv_exact(ln)
        if masked:
            data = bytes(c ^ mask[i % 4] for i, c in enumerate(data))
        if opcode == 0x8:
            raise RuntimeError("closed by peer")
        if opcode == 0x9:          # ping -> pong
            self._send_frame(data, 0xA)
            return self._recv_frame()
        return data

    def _send_frame(self, data, opcode=0x1):
        mask = os.urandom(4)
        n = len(data)
        hdr = bytes([0x80 | opcode])
        if n < 126:
            hdr += bytes([0x80 | n])
        elif n < 65536:
            hdr += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            hdr += bytes([0x80 | 127]) + struct.pack(">Q", n)
        self.sock.sendall(hdr + mask + bytes(c ^ mask[i % 4] for i, c in enumerate(data)))

    def call(self, method, params=None):
        mid = self.next_id
        self.next_id += 1
        msg = {"id": mid, "method": method, "params": params or {}}
        self._send_frame(json.dumps(msg).encode("utf-8"))
        while True:
            data = self._recv_frame()
            try:
                obj = json.loads(data.decode("utf-8"))
            except Exception:
                continue
            if obj.get("id") == mid:
                return obj

    def evaluate(self, expression):
        r = self.call("Runtime.evaluate", {
            "expression": expression, "returnByValue": True, "awaitPromise": True})
        res = r.get("result", {})
        if "exceptionDetails" in res:
            return {"error": res["exceptionDetails"].get("text", "exception")}
        return res.get("result", {}).get("value")

    def insert_text(self, text):
        return self.call("Input.insertText", {"text": text})

    def _touch(self, ttype, points):
        return self.call("Input.dispatchTouchEvent", {"type": ttype, "touchPoints": points})

    def tap(self, x, y, hold_ms=70):
        p = [{"x": x, "y": y, "id": 1, "radiusX": 6, "radiusY": 6, "force": 1}]
        self._touch("touchStart", p)
        time.sleep(max(0.02, hold_ms / 1000.0))
        self._touch("touchEnd", [])

    def hold(self, x, y, ms):
        p = [{"x": x, "y": y, "id": 1, "radiusX": 6, "radiusY": 6, "force": 1}]
        self._touch("touchStart", p)
        time.sleep(ms / 1000.0)
        self._touch("touchEnd", [])

    def swipe(self, x1, y1, x2, y2, ms=260):
        steps = max(2, int(ms / 16))
        p = [{"x": x1, "y": y1, "id": 1, "radiusX": 6, "radiusY": 6, "force": 1}]
        self._touch("touchStart", p)
        for i in range(1, steps + 1):
            t = i / float(steps)
            p[0]["x"] = x1 + (x2 - x1) * t
            p[0]["y"] = y1 + (y2 - y1) * t
            self._touch("touchMove", p)
            time.sleep(ms / 1000.0 / steps)
        self._touch("touchEnd", [])

    def close(self):
        try:
            self._send_frame(b"", 0x8)
        except Exception:
            pass


def main():
    argv = sys.argv[1:]
    targets = [t for t in list_targets() if t.get("type") == "page"]
    if not targets:
        print("no page target")
        return 1
    c = CDP(targets[0]["webSocketDebuggerUrl"])
    try:
        if not argv:
            print(json.dumps(c.evaluate("document.title"), ensure_ascii=False))
        elif argv[0] == "--tap":
            c.tap(float(argv[1]), float(argv[2]))
            print("tap", argv[1], argv[2])
        elif argv[0] == "--hold":
            c.hold(float(argv[1]), float(argv[2]), float(argv[3]))
            print("hold", argv[1], argv[2], argv[3])
        elif argv[0] == "--swipe":
            c.swipe(float(argv[1]), float(argv[2]), float(argv[3]), float(argv[4]),
                    float(argv[5]) if len(argv) > 5 else 260)
            print("swipe", argv[1], argv[2], argv[3], argv[4])
        elif argv[0] == "--text":
            c.insert_text(argv[1])
            print("text ok")
        else:
            print(json.dumps(c.evaluate(argv[0]), ensure_ascii=False, indent=2))
    finally:
        c.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
