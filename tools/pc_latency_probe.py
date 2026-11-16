# -*- coding: utf-8 -*-
"""PC 端延迟/帧率探针（Lead 用）：无头 Edge 进局后读自适应插值、帧耗时、真实 rAF 帧率。

用法：python tools/pc_latency_probe.py --url http://127.0.0.1:8899/ --w 2730 --h 1536
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


def find_edge():
    for p in EDGE:
        if os.path.isfile(p):
            return p
    raise SystemExit("找不到 msedge.exe")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8899/")
    ap.add_argument("--w", type=int, default=2730)
    ap.add_argument("--h", type=int, default=1536)
    ap.add_argument("--port", type=int, default=9355)
    ap.add_argument("--seconds", type=float, default=4.0)
    a = ap.parse_args()

    prof = os.path.join(os.environ.get("TEMP", "."), "rp_lat_profile")
    cmd = [find_edge(), "--headless=new", "--remote-debugging-port=%d" % a.port,
           "--window-size=%d,%d" % (a.w, a.h), "--user-data-dir=" + prof,
           "--no-first-run", "--no-default-browser-check", a.url]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        t0 = time.time()
        ws = None
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
        ev("(function(){var n=document.getElementById('nameInput');"
           "if(n){n.value='延迟探针';n.dispatchEvent(new Event('input',{bubbles:true}));}"
           "var b=document.getElementById('joinBtn');if(b)b.click();"
           "window.__frames=0;(function tk(){window.__frames++;requestAnimationFrame(tk);})();"
           "return 1;})()")
        time.sleep(2.5)
        print("加入:", ev("JSON.stringify({joined:joined,players:Object.keys(players||{}).length})"))
        f0 = ev("window.__frames")
        time.sleep(a.seconds)
        f1 = ev("window.__frames")
        stats = ev("JSON.stringify({"
                   "canvas:[cv.width,cv.height], css:[VW,VH], dpr:+DPR.toFixed(2),"
                   "snapIvMs:+snapIvMs.toFixed(1), interpDelayMs:+interpDelayMs().toFixed(1),"
                   "frameMs:+frameMs.toFixed(2), lowQuality:lowQuality, fpsLabel:perfLabel(),"
                   "predSelf:!!selfSmooth, players:Object.keys(players||{}).length,"
                   "bullets:(bullets||[]).length, particles:(particles||[]).length})")
        print("真实 rAF 帧率: %.1f fps" % ((f1 - f0) / a.seconds))
        print(json.dumps(json.loads(stats), ensure_ascii=False, indent=1))
        c.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=6)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    main()
