# -*- coding: utf-8 -*-
"""安卓内嵌服务端入口（由 Java 通过 Chaquopy 调用）。

Java 侧只需三个调用：
    serve(root, port, bots, load_ai) -> "端口|ip1,ip2"  或  "ERROR 原因"
    status()                        -> dict
    stop()                          -> True

root = APK 首启从 assets 解包出来的目录，里面应有 public/ 与 data/。

注意：锁必须可重入（RLock），且加锁路径只调用 *_locked 版本 ——
早期版本在 serve() 持锁时调用 status()（再次加同一把 Lock）导致自锁死，
服务端虽然已经监听端口，但 serve() 永不返回，Java 侧一直以为没开服。
"""
import os
import sys
import threading

_HANDLE = None
_ROOT = None
_ERROR = ""
_LOCK = threading.RLock()


def serve(root, port=8765, bots=0, load_ai=True, map_switch_seconds=None, tick=60):
    """启动内嵌权威服务端（幂等：已在跑则直接返回状态）。

    tick 默认 60Hz（手机开服目标帧率）；实际帧率看 /status.json 的 fps，tick 是目标值。
    """
    global _HANDLE, _ROOT, _ERROR
    with _LOCK:
        if _HANDLE is not None:
            return _info_locked()
        _ROOT = str(root)
        if _ROOT not in sys.path:
            sys.path.insert(0, _ROOT)
        # 必须在 import server 之前设置：server 在 import 期就会按 ROOT 定位 data/ 与 public/
        os.environ["RP_ROOT"] = _ROOT
        try:
            import server
            _HANDLE = server.run_server(
                port=int(port), bots=int(bots), root=_ROOT, open_browser=False,
                load_ai=bool(load_ai), map_switch_seconds=map_switch_seconds,
                tick=int(tick) if tick else None,
                block=False, quiet=True)
            _ERROR = ""
            print("[host] 已开服 root=%s port=%s ips=%s ai=%s tick=%s"
                  % (_ROOT, _HANDLE.port, _HANDLE.ips, server.AI_POLICY is not None,
                     server.TICK))
        except Exception as e:            # 端口占用、资源缺失等
            _ERROR = "%s: %s" % (type(e).__name__, e)
            _HANDLE = None
            print("[host] 开服失败: %s" % _ERROR)
        return _info_locked()


def status():
    with _LOCK:
        return _status_locked()


def _status_locked():
    d = {"running": _HANDLE is not None, "error": _ERROR, "root": _ROOT or ""}
    if _HANDLE is not None:
        d.update({"port": _HANDLE.port, "ips": list(_HANDLE.ips),
                  "bots": _HANDLE.bots, "urls": _HANDLE.urls()})
    return d


def _info_locked():
    """单行字符串，方便 Java 解析（避免跨语言取 dict）。调用方需已持锁。"""
    s = _status_locked()
    if not s["running"]:
        return "ERROR %s" % (s.get("error") or "not running")
    return "%d|%s" % (s["port"], ",".join(s["ips"]))


def stop():
    global _HANDLE
    with _LOCK:
        if _HANDLE is not None:
            try:
                _HANDLE.stop()
            finally:
                _HANDLE = None
    return True
