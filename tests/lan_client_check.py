#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""局域网客户端端到端校验（纯 Python 标准库）。

目的：证明「某台主机开服后，另一台设备能完整加入并正常对战」。
本脚本扮演一个真实客户端：走 /status.json + 手写 RFC6455 WebSocket，
按 public/game.js 的协议握手、join（含 FNV-1a 签名）、上报输入、收快照。

用法：
    python tests/lan_client_check.py --host 127.0.0.1 --port 8765 --name LANCheck --seconds 20
    python tests/lan_client_check.py --out 测试/安卓验收/手机开服验收.md

退出码：0 全通过 / 1 有失败 / 2 环境不可用（连不上、不是本游戏的服）
注意：本脚本不改动服务端；手机实例请先 adb forward tcp:8765 tcp:8765。

--seconds 表示「主动对战校验时长」（移动/射击/化学/AI 采样都在这个窗口内完成），
断线重连与聊天附加项在该窗口之后进行，总耗时约 seconds + 12 秒。

事件计数只取「快照流里的 ev」（服务端每帧广播后即清空，按客户端收到的顺序计数，
不会重复也不会跨源重复计数）；/status.json 只用于地图/机器人/AI 计数与 pH/弹药采样。
"""
import argparse
import base64
import hashlib
import json
import math
import os
import re
import socket
import struct
import sys
import threading
import time

WS_MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
# 与 server.py _AUTH_SALT 等价；优先从 public/game.js 的 signAuth 反推，避免硬编码漂移
_AUTH_SALT_FALLBACK = "L4N_Sh00t3r#9F2S_N4L"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MOVE_MIN_PX = 40.0          # 位移阈值（任务要求 > 40px）
BOT_MOVE_MIN_PX = 40.0      # 机器人位移阈值
PH_EPS = 0.05               # pH 变化阈值
TELEPORT_PX = 200.0         # 单帧位置跳变超过该值视为重生/换图，位移基准重置


class EnvError(Exception):
    """环境不可用（退出码 2）。"""


class AbortError(Exception):
    """环境可用但校验无法继续（退出码 1）。"""


# --------------------------------------------------------------------------
# 签名：与 public/game.js signAuth 一致（FNV-1a 32bit）
# --------------------------------------------------------------------------
def fnv1a(s):
    h = 0x811C9DC5
    for ch in s.encode("utf-8"):
        h ^= ch
        h = (h * 0x01000193) & 0xFFFFFFFF
    return "%08x" % h


def auth_salt(game_js=None):
    """从 public/game.js 的 _KS 反推签名密钥；失败则用已知常量。"""
    path = game_js or os.path.join(ROOT, "public", "game.js")
    try:
        with open(path, encoding="utf-8") as f:
            src = f.read()
    except OSError as e:
        return _AUTH_SALT_FALLBACK, "public/game.js 不可读(%s)，使用内置常量" % e
    m = re.search(r"_KS\s*=\s*\[([^\]]*)\]", src)
    if not m:
        return _AUTH_SALT_FALLBACK, "game.js 未找到 _KS，使用内置常量"
    parts = re.findall(r'"([^"]*)"', m.group(1)) or re.findall(r"'([^']*)'", m.group(1))
    base = "".join(parts)
    if not base:
        return _AUTH_SALT_FALLBACK, "game.js _KS 解析为空，使用内置常量"
    # game.js: k = _KS.join("") + _KS.slice(0,5).reverse().join("")
    return base + base[:5][::-1], "public/game.js signAuth（_KS %d 项）" % len(parts)


def calc_auth(challenge, salt):
    return fnv1a(salt + str(challenge) + salt)


# --------------------------------------------------------------------------
# 原生 HTTP（裸 socket，短连接；参考 tests/android_soak.py 的重试客户端）
# --------------------------------------------------------------------------
def http_get(host, port, path, timeout=6.0, retries=3, delay=0.4):
    last = None
    for _ in range(max(1, retries)):
        try:
            s = socket.create_connection((host, port), timeout=timeout)
        except OSError as e:
            last = e
            time.sleep(delay)
            continue
        try:
            s.settimeout(timeout)
            req = ("GET %s HTTP/1.1\r\nHost: %s:%d\r\nConnection: close\r\n\r\n"
                   % (path, host, port))
            s.sendall(req.encode("ascii"))
            buf = b""
            while True:
                c = s.recv(65536)
                if not c:
                    break
                buf += c
        except OSError as e:
            last = e
            time.sleep(delay)
            continue
        finally:
            try:
                s.close()
            except OSError:
                pass
        head, _, body = buf.partition(b"\r\n\r\n")
        line = head.split(b"\r\n", 1)[0].decode("latin-1", "replace")
        parts = line.split(" ")
        code = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        return code, head.decode("latin-1", "replace"), body
    raise RuntimeError("HTTP 请求失败 %s: %r" % (path, last))


def safe_get(host, port, path, timeout=6.0, retries=2):
    """返回 (code, body, 错误文本)。"""
    try:
        code, _head, body = http_get(host, port, path, timeout=timeout, retries=retries)
        return code, body, None
    except RuntimeError as e:
        return None, b"", str(e)


def tcp_probe(host, port, retries=8, delay=0.5, timeout=3.0):
    last = None
    for _ in range(retries):
        try:
            s = socket.create_connection((host, port), timeout=timeout)
            s.close()
            return True, ""
        except OSError as e:
            last = e
            time.sleep(delay)
    return False, "%s: %s" % (type(last).__name__, last)


# --------------------------------------------------------------------------
# 手写 WebSocket 客户端（参考 tools/cdp_eval.py 的 CDP 实现，增加读取线程）
# --------------------------------------------------------------------------
class WsClient(object):
    def __init__(self, host, port, path="/ws", timeout=8.0):
        self.host = host
        self.port = port
        self.path = path
        self.timeout = timeout
        self.sock = None
        self.buf = b""
        self.send_lock = threading.Lock()
        self.msg_lock = threading.Lock()
        self.closed = False
        self.error = None
        self.accept_ok = False
        self.hello = None
        self.my_id = None
        self.joined = False
        self.main_joined = False    # 是否已进入主对局（False 时可能还在新手教程小局里）
        self.kicked = None
        self.banned = None          # 服务端封禁通知（t=="banned"）
        self.latest = None          # 最新快照
        self.tut_start = None       # 服务端下发的新手教程开局消息（独立小局）
        self.map_msg = None         # 服务端显式切图消息
        self.snapshots = 0
        self.frames = 0
        self.events = []            # 快照流里的 ev（每帧清空 -> 按到达顺序累积，主线程 drain）
        self._reader = None

    # ---- 握手 ----
    def connect(self):
        self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        req = ("GET %s HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\n"
               "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
               "Sec-WebSocket-Version: 13\r\n\r\n"
               % (self.path, self.host, self.port, key))
        self.sock.sendall(req.encode("ascii"))
        head = b""
        while b"\r\n\r\n" not in head:
            c = self.sock.recv(4096)
            if not c:
                raise RuntimeError("WebSocket 握手期间连接被关闭")
            head += c
        head, _, rest = head.partition(b"\r\n\r\n")
        self.buf = rest
        lines = head.decode("latin-1", "replace").split("\r\n")
        status = lines[0] if lines else ""
        if "101" not in status:
            raise RuntimeError("WebSocket 握手失败: %s" % status)
        want = base64.b64encode(
            hashlib.sha1((key + WS_MAGIC).encode("ascii")).digest()).decode("ascii")
        got = None
        for ln in lines[1:]:
            if ln.lower().startswith("sec-websocket-accept:"):
                got = ln.split(":", 1)[1].strip()
        self.accept_ok = (got == want)
        self.sock.settimeout(0.5)
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    # ---- 帧 ----
    def _parse_frame(self):
        buf = self.buf
        if len(buf) < 2:
            return None
        b0, b1 = buf[0], buf[1]
        opcode = b0 & 0x0F
        masked = b1 & 0x80
        ln = b1 & 0x7F
        off = 2
        if ln == 126:
            if len(buf) < 4:
                return None
            ln = struct.unpack(">H", buf[2:4])[0]
            off = 4
        elif ln == 127:
            if len(buf) < 10:
                return None
            ln = struct.unpack(">Q", buf[2:10])[0]
            off = 10
        if ln > (1 << 22):
            raise RuntimeError("帧长度异常: %d" % ln)
        mask_len = 4 if masked else 0
        if len(buf) < off + mask_len + ln:
            return None
        if masked:
            mask = buf[off:off + 4]
            payload = bytes(b ^ mask[i % 4]
                            for i, b in enumerate(buf[off + 4:off + 4 + ln]))
        else:
            payload = bytes(buf[off:off + ln])
        self.buf = buf[off + mask_len + ln:]
        return opcode, payload

    def _send(self, opcode, payload):
        if self.sock is None:
            raise OSError("socket 未建立")
        mask = os.urandom(4)
        n = len(payload)
        hdr = bytes([0x80 | opcode])
        if n < 126:
            hdr += bytes([0x80 | n])
        elif n < 65536:
            hdr += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            hdr += bytes([0x80 | 127]) + struct.pack(">Q", n)
        data = hdr + mask + bytes(c ^ mask[i % 4] for i, c in enumerate(payload))
        with self.send_lock:
            self.sock.sendall(data)

    def send_text(self, obj):
        self._send(0x1, json.dumps(obj, ensure_ascii=False,
                                   separators=(",", ":")).encode("utf-8"))

    def _next_text(self, timeout):
        """返回 str；超时返回 ""；对端关闭返回 None。"""
        deadline = time.time() + timeout
        while True:
            while True:
                fr = self._parse_frame()
                if fr is None:
                    break
                opcode, payload = fr
                if opcode == 0x8:
                    return None
                if opcode == 0x9:
                    try:
                        self._send(0xA, payload)
                    except OSError:
                        return None
                    continue
                if opcode == 0xA:
                    continue
                if opcode in (0x1, 0x2):
                    return payload.decode("utf-8", "replace")
            if self.closed:
                return None
            remain = deadline - time.time()
            if remain <= 0:
                return ""
            self.sock.settimeout(max(0.05, min(0.5, remain)))
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                return ""
            except OSError:
                return None
            if not chunk:
                return None
            self.buf += chunk

    def _read_loop(self):
        while not self.closed:
            try:
                raw = self._next_text(0.3)
            except Exception as e:                      # noqa: BLE001
                self.error = "%s: %s" % (type(e).__name__, e)
                self.closed = True
                return
            if raw is None:
                self.closed = True
                return
            if raw == "":
                continue
            try:
                m = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(m, dict):
                continue
            self.frames += 1
            t = m.get("t")
            if t == "hello":
                self.hello = m
            elif t == "joined":
                self.my_id = m.get("id")
                self.joined = True
                self.main_joined = True
            elif t == "tut_start":
                # 新手教程：服务端把该玩家放进独立小局（独立小地图，只有人机靶子）
                self.tut_start = m
                self.my_id = m.get("id")
                self.joined = True
                self.map_msg = {"t": "map", "map": m.get("map")}
            elif t == "map":
                self.map_msg = m
            elif t == "kick":
                self.kicked = m.get("why") or "被服务端拒绝"
            elif t == "banned":
                self.banned = m
            elif t == "s":
                self.latest = m
                self.snapshots += 1
                ev = m.get("ev")
                if ev:
                    with self.msg_lock:
                        self.events.extend(ev)
                        if len(self.events) > 20000:
                            del self.events[:10000]

    def drain_events(self):
        with self.msg_lock:
            out = self.events
            self.events = []
            return out

    def close(self):
        self.closed = True
        try:
            self._send(0x8, b"")
        except Exception:                               # noqa: BLE001
            pass
        try:
            if self.sock is not None:
                self.sock.close()
        except OSError:
            pass


def wait_join(ws, name, team, salt, timeout=6.0, skip_tutorial=True):
    """等 hello -> join -> joined。

    新 IP 首次连服务器会先进"独立新手教程小局"（收到 tut_start）。
    skip_tutorial=True（默认）时自动发 tut_done 跳过教程，再等真正的 joined + 切图消息；
    skip_tutorial=False 时停在教程小局里返回（供"教程隔离"类测试使用）。
    """
    dl = time.time() + timeout
    while ws.hello is None and not ws.closed and time.time() < dl:
        time.sleep(0.05)
    if ws.hello is None:
        return {"hello": None, "joined": False, "id": None, "kicked": None,
                "in_tutorial": False}
    ws.send_text({"t": "join", "name": name, "team": team,
                  "auth": calc_auth(ws.hello.get("ch"), salt)})
    skipped_tut = False
    while (not ws.main_joined and ws.kicked is None and not ws.closed
           and time.time() < dl):
        if ws.tut_start is not None:
            if not skip_tutorial:
                break                      # 就停在教程小局里
            if not skipped_tut:
                ws.send_text({"t": "tut_done"})   # 自动跳过新手教程，直接进主对局
                skipped_tut = True
        time.sleep(0.03)
    return {"hello": ws.hello, "joined": ws.main_joined or (not skip_tutorial and ws.tut_start is not None),
            "id": ws.my_id, "kicked": ws.kicked, "skipped_tutorial": skipped_tut,
            "in_tutorial": ws.tut_start is not None and not ws.main_joined}


# --------------------------------------------------------------------------
# 数据小工具
# --------------------------------------------------------------------------
def snap_players(snap):
    out = {}
    if not isinstance(snap, dict):
        return out
    for p in snap.get("ps", []) or []:
        if isinstance(p, dict) and "id" in p:
            out[p["id"]] = p
    return out


def short_json(obj, limit=240):
    try:
        s = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        s = repr(obj)
    return s if len(s) <= limit else s[:limit] + "..."


def keys_toward(cur, target):
    """朝目标移动的按键 [上,右,下,左]（与 mobile.js 的 k 顺序一致）。

    用对角（多轴）按键，避免目标移动导致的单轴抖动；服务端会归一化速度。
    """
    if cur is None or target is None:
        return [0, 0, 0, 0]
    dx = target[0] - cur[0]
    dy = target[1] - cur[1]
    n = math.hypot(dx, dy)
    if n < 1e-6:
        return [0, 0, 0, 0]
    ux, uy = dx / n, dy / n
    return [1 if uy < -0.3 else 0, 1 if ux > 0.3 else 0,
            1 if uy > 0.3 else 0, 1 if ux < -0.3 else 0]


class World(object):
    """从 hello.map 还原当前地图几何，用于视线判定（判定逻辑与 server.py 一致）。"""

    def __init__(self, amap):
        amap = amap if isinstance(amap, dict) else {}
        self.walls = [tuple(w) for w in (amap.get("walls") or []) if len(w) >= 4]
        self.w = float(amap.get("w") or 2400)
        self.h = float(amap.get("h") or 1600)
        self.id = amap.get("id")

    @staticmethod
    def _clamp(v, lo, hi):
        return lo if v < lo else (hi if v > hi else v)

    def point_in_walls(self, x, y, margin=2):
        for wx, wy, ww, wh in self.walls:
            if (wx - margin <= x <= wx + ww + margin
                    and wy - margin <= y <= wy + wh + margin):
                return True
        return False

    def has_los(self, x1, y1, x2, y2):
        d = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
        n = int(d // 24) + 1
        for i in range(1, n):
            t = i / float(n)
            if self.point_in_walls(x1 + (x2 - x1) * t, y1 + (y2 - y1) * t):
                return False
        return True


def local_ip_set():
    ips = {"127.0.0.1", "::1", "localhost"}
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            ip = info[4][0]
            if "." in ip:
                ips.add(ip)
    except OSError:
        pass
    return ips


def guess_server_type(host):
    if host in ("127.0.0.1", "localhost", "::1"):
        return "回环地址（本机；注意 127.0.0.1 也可能被 adb forward 占用而指向手机）"
    if host in local_ip_set():
        return "本机 LAN 地址（本机运行的 server.py）"
    return "另一台设备（LAN 直连；如手机 APK 内嵌 CPython server.py）"


def load_map_pool(maps_file=None):
    """本地地图池（用于校验服务端 map 字段合法性）。"""
    cands = []
    if maps_file:
        cands.append(maps_file)
    cands += [os.path.join(ROOT, "data", "maps.json"),
              os.path.join(ROOT, "public", "maps.json")]
    pool = {}
    used = []
    for p in cands:
        if not p or not os.path.isfile(p):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            used.append("%s(读取失败:%s)" % (p, e))
            continue
        used.append(p)
        for m in data if isinstance(data, list) else []:
            if isinstance(m, dict) and m.get("id"):
                pool[m["id"]] = m.get("name", "")
    return pool, used


# --------------------------------------------------------------------------
# 报告
# --------------------------------------------------------------------------
class Report(object):
    def __init__(self):
        self.rows = []
        self.infos = []

    def check(self, cid, name, expect, actual, ok, extra=False, note=""):
        self.rows.append({"id": cid, "name": name, "expect": expect,
                          "actual": actual, "ok": ok, "extra": extra, "note": note})
        tag = "PASS" if ok is True else ("SKIP" if ok is None else "FAIL")
        line = "[%s] %-16s 期望: %s | 实测: %s" % (tag, cid, expect, actual)
        if note:
            line += " | " + note
        print(line, flush=True)
        return ok

    def info(self, name, value):
        self.infos.append({"name": name, "value": value})
        print("[INFO] %-16s %s" % (name, value), flush=True)

    @property
    def passed(self):
        return sum(1 for r in self.rows if r["ok"] is True)

    @property
    def failed(self):
        return sum(1 for r in self.rows if r["ok"] is False)

    @property
    def skipped(self):
        return sum(1 for r in self.rows if r["ok"] is None)

    def summary_line(self):
        return "通过 %d / 失败 %d / 跳过 %d" % (self.passed, self.failed, self.skipped)

    def write_md(self, path, args, meta):
        lines = []
        lines.append("# 局域网客户端端到端校验报告\n")
        lines.append("- 生成时间：%s" % time.strftime("%Y-%m-%d %H:%M:%S"))
        lines.append("- 目标服务：`http://%s:%d`（WS `%s`）" % (args.host, args.port, args.ws_path))
        lines.append("- 客户端身份：`%s`（队 red），主动对战时长 `--seconds %s`"
                     % (args.name, args.seconds))
        lines.append("- 客户端 = PC（本脚本）；服务端 = %s"
                     % (args.server_label or "见“目标环境”表（按 --host 推测）"))
        lines.append("- 脚本：`tests/lan_client_check.py`（纯 Python 标准库）")
        lines.append("- 结果：**%s**" % self.summary_line())
        lines.append("")
        lines.append("## 目标环境（服务端自述）\n")
        lines.append("| 项 | 值 |")
        lines.append("| --- | --- |")
        for k, v in meta.get("env", []):
            lines.append("| %s | %s |" % (k, str(v).replace("|", "\\|")))
        for inf in self.infos:
            lines.append("| %s | %s |" % (inf["name"], str(inf["value"]).replace("|", "\\|")))
        lines.append("")
        lines.append("## 校验明细\n")
        lines.append("| # | 校验项 | 期望 | 实测 | 结论 |")
        lines.append("| --- | --- | --- | --- | --- |")
        for i, r in enumerate(self.rows, 1):
            if r["ok"] is True:
                tag = "通过（附加项）" if r["extra"] else "通过"
            elif r["ok"] is None:
                tag = "跳过（附加项）" if r["extra"] else "跳过"
            else:
                tag = "**失败（附加项）**" if r["extra"] else "**失败**"
            lines.append("| %d | %s | %s | %s | %s |"
                         % (i, r["name"], r["expect"].replace("|", "\\|"),
                            r["actual"].replace("|", "\\|"), tag))
        notes = [r for r in self.rows if r["note"]]
        if notes:
            lines.append("")
            lines.append("### 备注\n")
            for r in notes:
                lines.append("- `%s`：%s" % (r["id"], r["note"]))
        lines.append("")
        lines.append("## 关键证据\n")
        for k, v in meta.get("evidence", []):
            lines.append("- **%s**：%s" % (k, v))
        lines.append("")
        lines.append("## 复现命令\n")
        lines.append("```bash")
        lines.append("# 1) 主机开服（PC：另开一个终端；MAP_SWITCH_SECONDS=15 便于观察换图）")
        lines.append("set MAP_SWITCH_SECONDS=15 && python server.py 8765 --bots 4 --no-browser")
        lines.append("# 2) 另一台设备/本机跑校验")
        lines.append("python tests/lan_client_check.py --host 127.0.0.1 --port 8765 "
                     "--name %s --seconds %s" % (args.name, args.seconds))
        lines.append("")
        lines.append("# 3) 手机 APK 内嵌服务端：把手机 8765 映射到 PC 环回")
        lines.append("adb forward tcp:8765 tcp:8765")
        lines.append("python tests/lan_client_check.py --host 127.0.0.1 --port 8765 "
                     "--name %s --seconds %s" % (args.name, args.seconds))
        lines.append("adb forward --remove tcp:8765")
        lines.append("```")
        lines.append("")
        out = os.path.abspath(path)
        d = os.path.dirname(out)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print("[报告] 已写入 %s" % out, flush=True)


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def run(args, rpt):
    host, port = args.host, args.port
    base = "http://%s:%d" % (host, port)
    meta = {"env": [], "evidence": []}
    salt, salt_src = auth_salt()
    rpt.info("签名密钥来源", salt_src)
    meta["evidence"].append(("签名密钥来源", salt_src + "（FNV-1a 32bit，与 public/game.js 同规则）"))

    # ---- 0. 环境探测 ----
    ok, err = tcp_probe(host, port, retries=args.probe_retries, delay=0.5)
    if not ok:
        raise EnvError("TCP 无法连接 %s：%s" % (base, err))
    code, body, herr = safe_get(host, port, "/status.json", timeout=args.timeout, retries=2)
    if code != 200:
        raise EnvError("GET /status.json 不可用（code=%s err=%s）；目标可能不是本游戏服务端"
                       % (code, herr))
    try:
        st0 = json.loads(body.decode("utf-8"))
    except ValueError as e:
        raise EnvError("GET /status.json 返回的不是合法 JSON：%s" % e)
    if not isinstance(st0, dict) or "players" not in st0:
        raise EnvError("GET /status.json 结构不符合本游戏协议：keys=%s" % list(st0)[:8])
    meta["env"] += [
        ("目标", base),
        ("客户端", "PC（tests/lan_client_check.py，纯 Python 标准库 WebSocket 客户端）"),
        ("服务端类型", args.server_label or guess_server_type(host)),
        ("校验开始时间", time.strftime("%Y-%m-%d %H:%M:%S")),
        ("服务端当前地图", st0.get("map")),
        ("服务端玩家数", len(st0.get("players", []))),
        ("服务端机器人", sum(1 for p in st0.get("players", []) if p.get("bot"))),
        ("服务端强度", st0.get("diff")),
        ("距下次换图(秒)", st0.get("map_switch_in")),
    ]
    if host in ("127.0.0.1", "localhost", "::1"):
        print("[WARN] --host=%s 是回环地址：若本机同时开着 adb forward tcp:%d，"
              "127.0.0.1:%d 会指向手机而不是本机服务端；要区分请用真实 LAN IP。"
              % (host, port, port), flush=True)
        rpt.info("连接地址提醒", "回环地址可能命中 adb forward（手机）；验收请用真实 LAN IP")
    print("[阶段] 环境可用：%s（地图=%s 玩家=%d）"
          % (base, st0.get("map"), len(st0.get("players", []))), flush=True)

    # ---- 1. HTTP 检查 ----
    code, body, err = safe_get(host, port, "/", timeout=args.timeout)
    txt = body.decode("utf-8", "replace")
    idx_ok = (code == 200 and "Reaction Protocol" in txt and 'id="cv"' in txt)
    rpt.check("http_index", "GET / 首页",
              "200 且含游戏页面标记（Reaction Protocol / <canvas id=\"cv\">）",
              "code=%s len=%d markers=%s" % (code, len(body), "OK" if idx_ok else "缺失"),
              idx_ok, note=(err or ""))

    code_m, body_m, err_m = safe_get(host, port, "/mobile.html", timeout=args.timeout)
    txt_m = body_m.decode("utf-8", "replace")
    mob_ok = (code_m == 200 and "mobile.js" in txt_m and len(body_m) > 0)
    rpt.check("http_mobile", "GET /mobile.html 手机页",
              "200 且引用 mobile.js、非 0 字节",
              "code=%s len=%d mobile.js=%s" % (code_m, len(body_m), "mobile.js" in txt_m),
              mob_ok, note=(err_m or ""))

    code_s, body_s, err_s = safe_get(host, port, "/status.json", timeout=args.timeout)
    st_json = None
    if code_s == 200:
        try:
            st_json = json.loads(body_s.decode("utf-8"))
        except ValueError as e:
            err_s = "JSON 解析失败: %s" % e
    keys_ok = isinstance(st_json, dict) and all(k in st_json for k in ("players", "map", "events"))
    rpt.check("http_status", "GET /status.json",
              "200 且为合法 JSON，含 players/map/events",
              "code=%s json=%s keys=%s"
              % (code_s, not (st_json is None),
                 ",".join(list(st_json)[:8]) if st_json else "-"),
              keys_ok, note=(err_s or ""))

    # 静态资源完整性：index.html / mobile.html 引用的本地资源
    ref_fail = []
    ref_ok = []
    for _page_name, page_txt in (("/index.html", txt), ("/mobile.html", txt_m)):
        if not page_txt:
            continue
        refs = re.findall(r'(?:src|href)\s*=\s*["\']([^"\']+)["\']', page_txt)
        for ref in refs:
            if re.match(r'^(https?:)?//|^(data|javascript|mailto):|^#', ref):
                continue
            path = ref if ref.startswith("/") else "/" + ref
            c, b, e = safe_get(host, port, path, timeout=args.timeout, retries=2)
            if c == 200 and len(b) > 0:
                ref_ok.append("%s=%d/%dB" % (ref, c, len(b)))
            else:
                ref_fail.append("%s(code=%s,%dB,%s)" % (ref, c, len(b), e or ""))
    rpt.check("static_refs", "静态资源完整性",
              "index.html/mobile.html 引用的 ui.js/game.js 等均 200 且非 0 字节",
              "OK:%d [%s]%s" % (len(ref_ok), ", ".join(ref_ok[:6]),
                                (" 失败:" + ", ".join(ref_fail)) if ref_fail else ""),
              (len(ref_fail) == 0 and len(ref_ok) > 0))

    # 附加项：服务端对外提供的 public/ 与仓库当前 public/ 是否逐字节一致
    # （手机 APK 内嵌站点靠打包时的资源解包，最易出现"新服务端 + 旧前端"的漂移）
    # 加密发布版（v3.0）会给前端注入版权水印首行，比对时去掉水印行再比。
    pub_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "public")

    def _strip_watermark(data):
        if data.startswith(b"/* \xc2\xa9 2026") or data.startswith(b"/* \xa9 2026"):
            nl = data.find(b"\n")
            return True, data[nl + 1:] if nl >= 0 else b""
        return False, data

    drift = []
    same_n = 0
    watermarked = 0
    for f in ("index.html", "mobile.html", "ui.js", "game.js", "mobile.js",
              "mobile_layout.js", "style.css", "maps.json"):
        lp = os.path.join(pub_dir, f)
        if not os.path.exists(lp):
            continue
        with open(lp, "rb") as fh:
            local = fh.read()
        c, body, err = safe_get(host, port, "/" + f, timeout=args.timeout, retries=2)
        if c != 200 or not body:
            drift.append("%s(HTTP %s%s)" % (f, c, ("," + err) if err else ""))
            continue
        if hashlib.sha256(local).hexdigest() == hashlib.sha256(body).hexdigest():
            same_n += 1
            continue
        has_wm, body2 = _strip_watermark(body)
        if has_wm and hashlib.sha256(local).hexdigest() == hashlib.sha256(body2).hexdigest():
            same_n += 1
            watermarked += 1
            continue
        drift.append("%s(本机 %dB vs 服务端 %dB)" % (f, len(local), len(body)))
    if same_n or drift:
        rpt.check("static_match", "附加项：内嵌资源与仓库一致性",
                  "服务端对外提供的 public/ 与仓库当前 public/ 一致（%d 个文件，"
                  "允许加密发布版注入水印首行）" % (same_n + len(drift)),
                  ("全部一致（%d 个文件%s）" % (same_n,
                                            "，其中 %d 个带水印" % watermarked if watermarked else ""))
                  if not drift else ("漂移：" + "; ".join(drift)),
                  not drift,
                  note=(("漂移：手机端资源版本落后于仓库，需重新打包/解包；"
                         "仅影响前端功能（如自动重连），不影响服务端协议") if drift else ""))
    else:
        rpt.info("内嵌资源一致性", "无法比对（仓库 public/ 不存在或不可读）")

    # ---- 2. WebSocket 加入 ----
    ws = WsClient(host, port, args.ws_path, timeout=args.timeout)
    try:
        ws.connect()
    except OSError as e:
        raise EnvError("WebSocket 连接失败：%s" % e)
    except RuntimeError as e:
        raise EnvError("WebSocket 握手失败：%s" % e)

    jr = wait_join(ws, args.name, "red", salt, timeout=6.0)
    if jr["hello"] is None:
        raise EnvError("WS 已升级但未收到 hello（可能不是本游戏服务端）")
    hello = jr["hello"]
    rpt.check("ws_hello", "WS 握手 + hello",
              "Sec-WebSocket-Accept 校验通过且收到 hello（含 ch 挑战值）",
              "accept=%s hello=%s" % ("匹配" if ws.accept_ok else "不匹配", short_json(hello, 160)),
              bool(ws.accept_ok and hello.get("ch")))
    meta["env"] += [
        ("hello.host（是否主机本机连接）", hello.get("host")),
        ("hello.bots", hello.get("bots")),
        ("hello.diff", hello.get("diff")),
        ("hello.map", (hello.get("map") or {}).get("id")),
        ("hello.urls", hello.get("urls")),
    ]
    if jr["kicked"]:
        rpt.check("ws_join", "WS join（FNV-1a 签名）",
                  "服务端接受 join 并返回 joined+id",
                  "被拒绝：%s" % jr["kicked"], False)
        raise AbortError("join 被服务端拒绝：%s" % jr["kicked"])
    if not jr["joined"]:
        rpt.check("ws_join", "WS join（FNV-1a 签名）",
                  "服务端接受 join 并返回 joined+id",
                  "6 秒内未收到 joined（closed=%s err=%s）" % (ws.closed, ws.error), False)
        raise AbortError("未收到 joined")
    my_id = jr["id"]
    rpt.check("ws_join", "WS join（FNV-1a 签名）",
              "服务端接受 join 并返回 joined+id",
              "joined id=%s（签名来源：%s）" % (my_id, salt_src), True)
    rpt.info("客户端 id", my_id)

    dl = time.time() + 5.0
    while ws.latest is None and not ws.closed and time.time() < dl:
        time.sleep(0.02)
    snap0 = ws.latest
    ps0 = snap_players(snap0)
    first_ok = bool(snap0) and (my_id in ps0) and len(ps0) > 0
    rpt.check("ws_snapshot", "首个快照",
              "收到快照且含玩家列表与自身 id",
              "快照=%s 玩家=%d 自身=%s 自身坐标=%s"
              % ("有" if snap0 else "无", len(ps0), (my_id in ps0),
                 ("(%s,%s)" % (ps0[my_id].get("x"), ps0[my_id].get("y"))) if my_id in ps0 else "-"),
              first_ok)
    if not first_ok:
        raise AbortError("未收到含自身 id 的首个快照")

    ammo_first = ps0[my_id].get("am")
    map_pool, pool_src = load_map_pool(args.maps_file)
    rpt.info("本地地图池", "%s（来源 %s）" % (sorted(map_pool), "; ".join(pool_src)))
    map_seen = set()
    if hello.get("map") and isinstance(hello["map"], dict) and hello["map"].get("id"):
        map_seen.add(hello["map"]["id"])
    # 出生区（用于判断是否已离开出生点，离开后才会被机器人看见）
    spawn_rect = None
    try:
        sp = (hello.get("map") or {}).get("spawn") or {}
        spawn_rect = sp.get("red") or None
    except AttributeError:
        spawn_rect = None
    world = World(hello.get("map"))
    rpt.info("地图几何", "id=%s 墙 %d 段（用于视线判定与接近路线）"
             % (world.id, len(world.walls)))
    ENGAGE_PX = 320.0     # 接近到该距离内停下对枪

    # ---- 3. 主动对战阶段 ----
    print("[阶段] 开始主动对战校验（%.1fs）" % args.seconds, flush=True)
    dirs = [("右/D", [0, 1, 0, 0]), ("下/S", [0, 0, 1, 0]),
            ("上/W", [1, 0, 0, 0]), ("左/A", [0, 0, 0, 1])]
    t0 = time.time()
    deadline = t0 + args.seconds
    probe_win = max(1.2, min(2.5, args.seconds / 8.0))
    dir_i = 0
    dir_t = t0
    start_xy = (ps0[my_id].get("x"), ps0[my_id].get("y"))
    base_xy = start_xy                 # 位移基准（重生/换图会重置）
    dir_base = start_xy
    probe_done = False
    move_dir_used = None
    dir_results = []
    max_disp = 0.0
    probe_fail_note = ""
    stop_move_at = None
    prev_xy = start_xy
    prev_alive = True
    alive_now = True
    teleports = 0
    hunt_axis_flip = False
    stuck_since = None
    stuck_ref = start_xy
    stuck_flips = 0
    detour_until = 0.0
    detour_keys = None
    probe_start_t = t0
    probe_start_xy = start_xy
    move_speed = 0.0            # 主机侧"有效移动速度"(px/s)：反映真机主循环是否跟得上
    move_speed_dir = None
    hist_pos = []
    shoot_from = t0 + min(1.0, args.seconds * 0.1)
    shoot_sent = 0
    ammo_min = ammo_first
    ammo_last = ammo_first
    my_pos_samples = []
    bot_samples = {}
    bot_ids = set()
    humans_other = set()
    ph_series = []
    all_ph = []
    ev_counts = {}
    ev_samples = []
    shoot_self = 0
    chem_events = 0
    dmg_mine = 0
    hit_by_me = 0
    last_status_t = 0.0
    status_errs = 0
    cur_map = st0.get("map")
    map_change_t = None
    host_fps_min = None
    host_fps_last = None
    host_lag_max = None
    min_enemy_d = None
    los_frames = 0
    my_st = None
    my_score = None
    trace_f = None
    last_trace_t = 0.0
    if args.trace:
        try:
            trace_f = open(args.trace, "w", encoding="utf-8")
        except OSError as e:
            rpt.info("轨迹文件", "打开失败：%s" % e)
            trace_f = None

    def count_events(events):
        nonlocal shoot_self, chem_events, dmg_mine, hit_by_me
        for e in events or []:
            if not isinstance(e, dict):
                continue
            t = e.get("t")
            ev_counts[t] = ev_counts.get(t, 0) + 1
            if len(ev_samples) < 30:
                ev_samples.append(short_json(e, 160))
            if t == "shoot" and e.get("pid") == my_id:
                shoot_self += 1
            if t in ("hit", "dmg"):
                chem_events += 1
                if t == "dmg" and e.get("for") == my_id:
                    dmg_mine += 1
                if t == "hit" and e.get("for") == my_id:
                    hit_by_me += 1

    def in_spawn(pos):
        if not spawn_rect or not pos or pos[0] is None:
            return False
        return (spawn_rect[0] <= pos[0] <= spawn_rect[0] + spawn_rect[2]
                and spawn_rect[1] <= pos[1] <= spawn_rect[1] + spawn_rect[3])

    while True:
        now = time.time()
        if now >= deadline or ws.closed:
            break
        snap = ws.latest
        count_events(ws.drain_events())
        keys = [0, 0, 0, 0]
        me = None
        enemy_near = None
        enemy_vis = None
        cur_pos = None
        if snap:
            ps = snap_players(snap)
            me = ps.get(my_id)
            myteam = (me or {}).get("team")
            if me and me.get("x") is not None:
                cur_pos = (me["x"], me["y"])
                alive_now = bool(me.get("al", 1))
                if me.get("ph") is not None:
                    ph_series.append((now, me["ph"]))
                am = me.get("am")
                if isinstance(am, (int, float)):
                    ammo_last = am
                    ammo_min = am if ammo_min is None else min(ammo_min, am)
            for pid, p in ps.items():
                if p.get("bot"):
                    bot_ids.add(pid)
                    bot_samples.setdefault(pid, []).append((now, p.get("x"), p.get("y")))
                elif pid != my_id:
                    humans_other.add(pid)
                if (pid != my_id and p.get("al") and myteam
                        and p.get("team") != myteam and p.get("x") is not None):
                    d = (((p["x"] - cur_pos[0]) ** 2 + (p["y"] - cur_pos[1]) ** 2) ** 0.5
                         if cur_pos else 1e18)
                    if enemy_near is None or d < enemy_near[0]:
                        enemy_near = (d, p)
                    if (cur_pos and (enemy_vis is None or d < enemy_vis[0])
                            and world.has_los(cur_pos[0], cur_pos[1], p["x"], p["y"])):
                        enemy_vis = (d, p)
            # 位置跳变/复活 -> 位移基准重置（避免把重生/换图当成"移动成功"）
            if cur_pos:
                if prev_xy and prev_xy[0] is not None:
                    jump = ((cur_pos[0] - prev_xy[0]) ** 2
                            + (cur_pos[1] - prev_xy[1]) ** 2) ** 0.5
                    if jump > TELEPORT_PX or (prev_alive and not alive_now):
                        teleports += 1
                        base_xy = cur_pos
                        dir_base = cur_pos
                        max_disp = 0.0
                        hist_pos = []          # 重生/换图跳变不计入有效速度
                prev_xy = cur_pos
                prev_alive = alive_now
                my_pos_samples.append((now, cur_pos[0], cur_pos[1]))
                if base_xy and base_xy[0] is not None:
                    max_disp = max(max_disp, ((cur_pos[0] - base_xy[0]) ** 2
                                              + (cur_pos[1] - base_xy[1]) ** 2) ** 0.5)

        # ---- 移动：先探测方向；探测成功后"清野"（离开出生点、追敌） ----
        if not probe_done:
            delta = 0.0
            if cur_pos and dir_base and dir_base[0] is not None:
                delta = ((cur_pos[0] - dir_base[0]) ** 2
                         + (cur_pos[1] - dir_base[1]) ** 2) ** 0.5
            if delta > MOVE_MIN_PX:
                probe_done = True
                move_dir_used = dirs[dir_i][0]
                dir_results.append("%s=%.1fpx(通过,%.1fs)"
                                   % (dirs[dir_i][0], delta, now - probe_start_t))
                if now - probe_start_t > 0.05:
                    move_speed = delta / (now - probe_start_t)
                    move_speed_dir = dirs[dir_i][0]
                stop_move_at = now + min(2.0, max(1.0, args.seconds * 0.1))
            elif now - dir_t > probe_win:
                dir_results.append("%s=%.1fpx" % (dirs[dir_i][0], delta))
                dir_i += 1
                dir_t = now
                dir_base = cur_pos or dir_base
                probe_start_t = now
                probe_start_xy = cur_pos or probe_start_xy
                if dir_i >= len(dirs):
                    probe_done = True
                    probe_fail_note = "四个方向各 %.1fs 均无明显位移" % probe_win
                    move_dir_used = None
            if dir_i < len(dirs):
                keys = dirs[dir_i][1]
        if probe_done:
            if stop_move_at is not None and now < stop_move_at and move_dir_used:
                keys = (dirs[dir_i][1] if dir_i < len(dirs) else keys)
                if not move_dir_used:
                    keys = [0, 0, 0, 0]
            else:
                # 不在出生点、且没有"近处且有视线"的敌人时：朝目标移动（绕墙/清野）
                target = enemy_vis or enemy_near
                need_move = (in_spawn(cur_pos) or (target is None)
                             or (target[0] > ENGAGE_PX) or (enemy_vis is None))
                if need_move:
                    tgt = ((target[1]["x"], target[1]["y"]) if target else (1200.0, 800.0))
                    keys_a = keys_toward(cur_pos, tgt)
                    keys = keys_a
                    # 卡墙检测：1.2s 位移 < 5px 则绕行 0.8s（按键旋转 90°）
                    if stuck_ref and cur_pos:
                        if ((cur_pos[0] - stuck_ref[0]) ** 2
                                + (cur_pos[1] - stuck_ref[1]) ** 2) ** 0.5 > 5.0:
                            stuck_since = None
                            stuck_ref = cur_pos
                        elif stuck_since is None:
                            stuck_since = now
                        elif now - stuck_since > 1.2:
                            detour_until = now + 0.8
                            detour_keys = [keys_a[1], keys_a[2], keys_a[3], keys_a[0]]
                            stuck_since = None
                            stuck_ref = cur_pos
                            stuck_flips += 1
                    elif cur_pos:
                        stuck_ref = cur_pos
                    if now < detour_until and detour_keys is not None:
                        keys = detour_keys

        # ---- 瞄准目标并持续开火（有视线时优先打可见敌人） ----
        # 有效移动速度：按住方向键时，最近 ~1s 窗口内实测位移/时间（主机主循环是否跟得上）
        if cur_pos:
            hist_pos.append((now, cur_pos[0], cur_pos[1]))
            while len(hist_pos) > 2 and now - hist_pos[0][0] > 1.0:
                hist_pos.pop(0)
            if (any(keys) and len(hist_pos) >= 2
                    and now - hist_pos[0][0] >= 0.4):
                dt_w = now - hist_pos[0][0]
                d_w = ((cur_pos[0] - hist_pos[0][1]) ** 2
                       + (cur_pos[1] - hist_pos[0][2]) ** 2) ** 0.5
                if d_w / dt_w > move_speed:
                    move_speed = d_w / dt_w
                    move_speed_dir = "实测(%.2fs/%.1fpx)" % (dt_w, d_w)
        if enemy_near:
            min_enemy_d = enemy_near[0] if min_enemy_d is None else min(min_enemy_d, enemy_near[0])
        if enemy_vis:
            los_frames += 1
        aim_t = enemy_vis or enemy_near
        aim = ((aim_t[1]["x"], aim_t[1]["y"]) if aim_t else (cur_pos or (1200.0, 800.0)))
        if trace_f is not None and now - last_trace_t >= 0.5:
            last_trace_t = now
            bear_err = None
            if cur_pos and aim_t and me is not None:
                want = math.atan2(aim[1] - cur_pos[1], aim[0] - cur_pos[0])
                got = me.get("a", 0.0) or 0.0
                bear_err = round((want - got + math.pi * 3) % (math.pi * 2) - math.pi, 3)
            try:
                trace_f.write(json.dumps({
                    "t": round(now - t0, 2), "id": my_id,
                    "x": None if not cur_pos else round(cur_pos[0], 1),
                    "y": None if not cur_pos else round(cur_pos[1], 1),
                    "a": None if me is None else me.get("a"),
                    "ax": round(aim[0], 1) if aim else None,
                    "ay": round(aim[1], 1) if aim else None,
                    "dist": None if not enemy_near else round(enemy_near[0], 1),
                    "los": bool(enemy_vis), "bear_err": bear_err,
                    "am": None if me is None else me.get("am"),
                    "alive": None if me is None else bool(me.get("al")),
                    "hp": None if me is None else me.get("hp"),
                    "st": my_st, "score": my_score, "keys": keys,
                }, ensure_ascii=False) + "\n")
                trace_f.flush()
            except OSError:
                pass
        try:
            ws.send_text({"t": "in", "k": keys, "ax": aim[0], "ay": aim[1],
                          "s": bool(now >= shoot_from), "z": 0})
            if now >= shoot_from:
                shoot_sent += 1
        except OSError:
            break

        # ---- /status.json：地图合法性、机器人标记、pH/弹药采样 ----
        if now - last_status_t >= 0.6:
            last_status_t = now
            c, b, e = safe_get(host, port, "/status.json", timeout=args.timeout, retries=1)
            if c == 200:
                try:
                    st = json.loads(b.decode("utf-8"))
                except ValueError:
                    st = None
                if st:
                    f = st.get("fps")
                    if isinstance(f, (int, float)):
                        host_fps_last = f
                        host_fps_min = f if host_fps_min is None else min(host_fps_min, f)
                    lg = st.get("lag")
                    if isinstance(lg, (int, float)):
                        host_lag_max = lg if host_lag_max is None else max(host_lag_max, lg)
                    if st.get("map"):
                        map_seen.add(st["map"])
                        if cur_map and st["map"] != cur_map:
                            cur_map = st["map"]
                            map_change_t = now
                            base_xy = cur_pos or base_xy
                            dir_base = cur_pos or dir_base
                            max_disp = 0.0
                            hist_pos = []      # 换图会全员重生，不计入有效速度
                    for p in st.get("players", []):
                        if p.get("bot"):
                            bot_ids.add(p.get("id"))
                        if p.get("ph") is not None:
                            all_ph.append((now, p.get("ph")))
                        if p.get("id") == my_id:
                            my_st = p.get("st")
                            my_score = p.get("s")
                            if p.get("ph") is not None:
                                ph_series.append((now, p["ph"]))
                            am = p.get("ammo")
                            if isinstance(am, (int, float)):
                                ammo_last = am
                                ammo_min = am if ammo_min is None else min(ammo_min, am)
            else:
                status_errs += 1
        time.sleep(0.02)

    battle_span = 0.0
    if my_pos_samples:
        battle_span = my_pos_samples[-1][0] - my_pos_samples[0][0]

    # 3.1 移动
    move_ok = max_disp > MOVE_MIN_PX
    rpt.check("move", "自身移动",
              "发送方向输入后自身位移 > %.0fpx" % MOVE_MIN_PX,
              "最大位移 %.1fpx（生效方向 %s；有效速度 %.0fpx/s；方向探测 %s；跳变重置 %d 次）%s"
              % (max_disp, move_dir_used or "无", move_speed,
                 ", ".join(dir_results) or "-",
                 teleports, ("；" + probe_fail_note) if probe_fail_note else ""),
              move_ok)
    meta["evidence"].append(("自身位移", "%.1fpx（阈值 %.0fpx），生效方向 %s，跳变重置 %d 次"
                             % (max_disp, MOVE_MIN_PX, move_dir_used or "无", teleports)))

    # 3.2 射击
    shoot_ok = (shoot_self >= 1) or (ammo_min is not None and ammo_first is not None
                                     and ammo_min < ammo_first)
    rpt.check("shoot", "射击生效",
              "发送射击输入后 /status.json events 出现 shoot（或弹药下降）",
              "shoot(pid=self)=%d 弹药 %s->%s 实际发送输入 %d 次"
              % (shoot_self, ammo_first, ammo_min, shoot_sent),
              shoot_ok)

    # 3.3 化学
    ph_vals = [v for _t, v in ph_series if v is not None]
    ph_min = min(ph_vals) if ph_vals else None
    ph_max = max(ph_vals) if ph_vals else None
    ph_range = (ph_max - ph_min) if ph_vals else 0.0
    chem_ok = (ph_range > PH_EPS) or (chem_events >= 1)
    all_vals = [v for _t, v in all_ph if v is not None]
    all_range = (max(all_vals) - min(all_vals)) if all_vals else 0.0
    solo = (not bot_ids) and (not humans_other)
    if solo and not chem_ok:
        rpt.check("chem", "化学引擎（主机侧）",
                  "观察到自身 pH 变化(>%.2f) 或 >=1 次 hit/dmg 事件" % PH_EPS,
                  "房间内只有本客户端（无机器人、无其他玩家），无人可打："
                  "pH 恒 %.2f、hit/dmg 事件 0" % (ph_vals[0] if ph_vals else 0.0),
                  None, note="solo 房间无法观察伤害型化学事件；不判失败")
    else:
        rpt.check("chem", "化学引擎（主机侧）",
                  "观察到自身 pH 变化(>%.2f) 或 >=1 次 hit/dmg 事件" % PH_EPS,
                  "自身 pH %s 范围 %.2f（采样 %d）；hit/dmg 事件 %d（我方造成 %d / 我方受击 %d）"
                  % ("%.2f~%.2f" % (ph_min, ph_max) if ph_vals else "无",
                     ph_range, len(ph_vals), chem_events, hit_by_me, dmg_mine),
                  chem_ok)
    rpt.info("全场 pH 范围", "%.2f（%.2f~%.2f，%d 次采样；证明主机侧化学状态在变）"
             % (all_range, min(all_vals) if all_vals else 0,
                max(all_vals) if all_vals else 0, len(all_vals)))
    rpt.info("我方化学交互", "hit(我造成)=%d dmg(我受击)=%d 自身 pH 范围=%.2f（对局是否真的打起来）"
             % (hit_by_me, dmg_mine, ph_range))
    rpt.info("接敌过程", "最近敌人距离 %s；有视线帧数 %d / 快照 %d"
             % ("%.0fpx" % min_enemy_d if min_enemy_d is not None else "全程无敌人",
                los_frames, ws.snapshots))
    snap_rate = (ws.snapshots / battle_span) if battle_span > 0 else 0.0
    rpt.info("主机主循环健康度", "有效移动速度 %.0fpx/s（%s）| 快照 %.1f 帧/s（30Hz 为满速）%s"
             % (move_speed, move_speed_dir or "未测到", snap_rate,
                (" | 服务端自报 fps 最低 %.1f（末次 %.1f），lag 累计丢帧 %s 帧"
                 % (host_fps_min, host_fps_last, host_lag_max))
                if host_fps_min is not None else
                " | 服务端未上报 fps/lag（该构建较旧，用左侧实测值判断）"))
    meta["evidence"].append(("主机主循环健康度",
                             "有效移动速度 %.0fpx/s；快照 %.1f 帧/s；服务端自报 fps %s lag %s"
                             % (move_speed, snap_rate, host_fps_min, host_lag_max)))
    rpt.info("服务端侧战绩", "st=%s 积分=%s（服务端累计的我方伤害/命中）"
             % (short_json(my_st, 120), my_score))
    if trace_f is not None:
        try:
            trace_f.close()
        except OSError:
            pass
    meta["evidence"].append(("化学事件", "hit/dmg=%d（我方造成 %d，我方受击 %d）；自身 pH 范围 %.2f"
                             % (chem_events, hit_by_me, dmg_mine, ph_range)))

    # 3.4 AI
    max_bot_move = 0.0
    if not bot_ids:
        rpt.check("ai_bots", "机器人 AI（主机侧）",
                  "主机有 bot 时，10s 内机器人坐标发生变化",
                  "主机无机器人（/status.json players 中 bot=true 数量=0）",
                  None, note="主机未开 --bots，跳过（不判失败）")
    else:
        need_span = min(args.ai_window, args.seconds * 0.8)
        moves = []
        for bid in sorted(bot_ids):
            s = bot_samples.get(bid) or []
            if len(s) < 2 or s[0][1] is None:
                moves.append("bot%d=采样不足(%d)" % (bid, len(s)))
                continue
            span = s[-1][0] - s[0][0]
            d = 0.0
            for _t, x, y in s:
                if x is None:
                    continue
                d = max(d, ((x - s[0][1]) ** 2 + (y - s[0][2]) ** 2) ** 0.5)
            max_bot_move = max(max_bot_move, d)
            moves.append("bot%d=%.1fpx/%.1fs" % (bid, d, span))
        enough = (battle_span >= need_span
                  and any(len(s) >= 2 for s in bot_samples.values()))
        if not enough:
            rpt.check("ai_bots", "机器人 AI（主机侧）",
                      "主机有 bot 时，%ds 内机器人坐标发生变化" % args.ai_window,
                      "观察窗口仅 %.1fs（需 %.1fs；bot %d 个）：%s"
                      % (battle_span, need_span, len(bot_ids), ", ".join(moves)),
                      None, note="增大 --seconds 可完成该项")
        else:
            rpt.check("ai_bots", "机器人 AI（主机侧）",
                      "主机有 bot 时，%ds 内机器人坐标发生变化" % args.ai_window,
                      "bot %d 个，窗口 %.1fs，最大位移 %.1fpx：%s"
                      % (len(bot_ids), battle_span, max_bot_move, ", ".join(moves)),
                      max_bot_move > BOT_MOVE_MIN_PX)
    meta["evidence"].append(("机器人位移", "最大 %.1fpx（bot %d 个，窗口 %.1fs）"
                             % (max_bot_move, len(bot_ids), battle_span)))

    # 3.5 地图合法性
    bad_maps = sorted(m for m in map_seen if m not in map_pool)
    map_ok = bool(map_seen) and not bad_maps
    rpt.check("map_pool", "地图字段合法",
              "全程 /status.json 的 map 均在 maps.json 池内（%s）" % sorted(map_pool),
              "观察到 %s%s" % (sorted(map_seen), ("；越界 %s" % bad_maps) if bad_maps else ""),
              map_ok)
    rpt.info("地图轮换事件", "map 事件 %d 次；本次观察到地图 %s；status.map_switch_in=%s"
             % (ev_counts.get("map", 0), sorted(map_seen),
                st_json.get("map_switch_in") if st_json else "?"))

    meta["evidence"].append(("事件类型统计", short_json(ev_counts, 400) or "无"))
    if ev_samples:
        meta["evidence"].append(("事件样例", "; ".join(ev_samples[:6])))
    meta["evidence"].append(("状态轮询失败次数", status_errs))
    meta["evidence"].append(("位置跳变重置次数", "%d（重生/换图不计入位移）" % teleports))
    meta["evidence"].append(("卡墙换轴次数", stuck_flips))

    # ---- 4. 聊天（附加项，协议完整性） ----
    chat_token = "LANCHK%d" % int(time.time() % 100000)
    chat_ok = False
    for _i in range(3):
        try:
            ws.send_text({"t": "chat", "msg": chat_token})
        except OSError:
            break
        dl = time.time() + 1.5
        while time.time() < dl and not ws.closed:
            count_events(ws.drain_events())
            if ev_counts.get("chat", 0) >= 1:
                chat_ok = True
                break
            time.sleep(0.05)
        if chat_ok:
            break
        c, b, _e = safe_get(host, port, "/status.json", timeout=args.timeout, retries=1)
        if c == 200:
            try:
                count_events(json.loads(b.decode("utf-8")).get("events"))
            except ValueError:
                pass
        if ev_counts.get("chat", 0) >= 1:
            chat_ok = True
            break
    rpt.check("chat", "聊天消息（附加项）",
              "发送 {t:chat,msg} 后服务端 events 出现 chat 事件",
              "chat 事件 %d 次（token=%s；快照流 %d 帧）"
              % (ev_counts.get("chat", 0), chat_token, ws.snapshots),
              chat_ok, extra=True)

    # ---- 5. 断线重连 ----
    print("[阶段] 断线重连校验", flush=True)
    k_before = d_before = s_before = None
    c, b, _e = safe_get(host, port, "/status.json", timeout=args.timeout, retries=1)
    if c == 200:
        try:
            st_last = json.loads(b.decode("utf-8"))
            for p in st_last.get("players", []):
                if p.get("id") == my_id:
                    k_before, d_before, s_before = p.get("k"), p.get("d"), p.get("s")
        except ValueError:
            pass
    ws.close()
    time.sleep(0.8)
    ws2 = None
    re_ok = False
    re_actual = ""
    try:
        ws2 = WsClient(host, port, args.ws_path, timeout=args.timeout)
        ws2.connect()
        jr2 = wait_join(ws2, args.name, "red", salt, timeout=6.0)
        if jr2["kicked"]:
            re_actual = "重连被拒绝：%s" % jr2["kicked"]
        elif not jr2["joined"]:
            re_actual = "未收到 joined（hello=%s）" % ("有" if jr2["hello"] else "无")
        else:
            dl = time.time() + 5.0
            found = False
            stats = None
            while time.time() < dl and not ws2.closed:
                snaps = ws2.latest
                if snaps:
                    for p in snaps.get("ps", []) or []:
                        if p.get("id") == ws2.my_id and p.get("n") == args.name:
                            found = True
                            stats = (p.get("k"), p.get("d"), p.get("s"))
                            break
                if found:
                    break
                time.sleep(0.05)
            re_ok = found
            re_actual = ("重连成功 id=%s 同名玩家在快照中=%s 战绩(k,d,s)=%s"
                         % (ws2.my_id, found, stats))
            if jr2["hello"] and jr2["hello"].get("ch") != hello.get("ch"):
                re_actual += "（服务端下发新挑战值）"
            if k_before is not None and any(v for v in (k_before, d_before, s_before)):
                keep = (stats == (k_before, d_before, s_before))
                re_ok = bool(found and keep)
                re_actual += "；断线前(k,d,s)=%s，按 IP 恢复=%s" % (
                    (k_before, d_before, s_before), "是" if keep else "否（不一致）")
    except (OSError, RuntimeError) as e:
        re_actual = "重连异常：%s: %s" % (type(e).__name__, e)
    finally:
        if ws2 is not None:
            ws2.close()
    rpt.check("reconnect", "断线重连（对齐 game.js 自动重连契约）",
              "断开后用同名重连成功（收到 joined，快照中出现同名玩家，"
              "且有战绩时按 IP 恢复 k/d/s）",
              re_actual or "无结果", re_ok)
    if k_before is not None:
        rpt.info("重连前战绩快照(k,d,s)",
                 "%s,%s,%s（同 IP 战绩恢复由服务端 restore_ip_stats 处理）"
                 % (k_before, d_before, s_before))
    rpt.info("客户端自动重连实现",
             "public/game.js:252-263 onclose -> 400ms*次数(上限8s) 重连并复用上次 join；"
             "joined 后提示“战绩已恢复”。本脚本用同一契约（同名+重签挑战值）验证服务端侧。")
    meta["evidence"].append(("快照统计", "首个快照玩家 %d，对战窗口 %.1fs，本客户端收到快照 %d 帧"
                             % (len(ps0), battle_span, ws.snapshots)))
    meta["evidence"].append(("服务端版本提示",
                             "校验目标 server.py 由主机提供；PC 侧脚本针对仓库当前 server.py 校验，"
                             "APK 内嵌版本需与之一致"))
    return meta


def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description="局域网客户端端到端校验（证明主机开服后另一设备可加入并对战）")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--name", default="LANCheck")
    ap.add_argument("--seconds", type=float, default=20.0,
                    help="主动对战校验时长（移动/射击/化学/AI 采样窗口）")
    ap.add_argument("--out", default=None, help="可选：输出 markdown 报告路径")
    ap.add_argument("--ws-path", default="/ws", help="WebSocket 路径（默认 /ws）")
    ap.add_argument("--ai-window", type=float, default=10.0, help="机器人位移观察窗口(秒)")
    ap.add_argument("--timeout", type=float, default=6.0, help="单次 HTTP 超时(秒)")
    ap.add_argument("--probe-retries", type=int, default=8, help="启动时 TCP 探测重试次数")
    ap.add_argument("--maps-file", default=None, help="可选：指定地图池文件校验 map 字段")
    ap.add_argument("--trace", default=None,
                    help="可选：输出 JSONL 诊断轨迹（瞄角/距离/弹药/服务端 st），排查打不中问题时用")
    ap.add_argument("--server-label", default=None,
                    help="可选：报告里标注服务端类型，如 '手机 APK 内嵌 CPython server.py'")
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    rpt = Report()
    t_start = time.time()
    rc = 1
    meta = {"env": [], "evidence": []}
    try:
        meta = run(args, rpt)
        rc = 0 if rpt.failed == 0 else 1
    except EnvError as e:
        rpt.check("env", "环境可用性", "目标服务可连接且为本游戏服务端", str(e), False)
        print("[环境不可用] %s" % e, flush=True)
        rc = 2
    except AbortError as e:
        print("[中止] %s" % e, flush=True)
        rc = 1 if rpt.failed else 2
    except KeyboardInterrupt:
        print("[中断] 用户取消", flush=True)
        rc = 1
    finally:
        if args.out:
            try:
                meta.setdefault("env", []).append(
                    ("总耗时(秒)", "%.1f" % (time.time() - t_start)))
                meta["env"].append(("退出码", "%d（0 全通过 / 1 有失败 / 2 环境不可用）" % rc))
                rpt.write_md(args.out, args, meta)
            except OSError as e:
                print("[报告] 写入失败：%s" % e, flush=True)
    print("=" * 60)
    print("结果：%s  退出码=%d  目标=http://%s:%d"
          % (rpt.summary_line(), rc, args.host, args.port), flush=True)
    print("=" * 60)
    return rc


if __name__ == "__main__":
    sys.exit(main())
