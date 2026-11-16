# -*- coding: utf-8 -*-
"""Lead 专用：v1.2 四项需求真机校验（开火键位置 / 60Hz / FOV 档位 / 换图倒计时）。

用法：python tools/_v12_check.py
前置：手机已用 --ez host true 开服（本脚本只读 + 触摸，不改设备生命周期）。
"""
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp_eval import CDP, list_targets   # noqa: E402


def sh(*args):
    return subprocess.run(list(args), capture_output=True, text=True).stdout


def forward_cdp():
    out = sh("adb", "shell", "cat", "/proc/net/unix")
    pids = re.findall(r"webview_devtools_remote_(\d+)", out)
    if not pids:
        raise SystemExit("找不到 webview_devtools_remote（WebView 未起来？）")
    sh("adb", "forward", "--remove-all")
    sh("adb", "forward", "tcp:9222", "localabstract:webview_devtools_remote_%s" % pids[-1])
    time.sleep(0.6)
    for _ in range(20):
        ts = [t for t in list_targets() if t.get("type") == "page"]
        if ts:
            return CDP(ts[0]["webSocketDebuggerUrl"]), pids[-1]
        time.sleep(0.4)
    raise SystemExit("CDP 无 page target")


R = {}
c, pid = forward_cdp()
ev = c.evaluate
print("webview pid:", pid)

# ---------- 1. 基础状态 ----------
R["state"] = ev("JSON.stringify({vw:VW,vh:VH,w:WORLD_W,h:WORLD_H,fov:fovMode,"
                "s:+fovScale().toFixed(4),cam:+cam.scale.toFixed(3),ms:mapSwitchIn,"
                "joined:joined,tick60:!0,errs:(window.__errors||[]).slice(-3)})")

# ---------- 2. 如未进入对局：先填名字，再点开始界面的按钮 ----------
def try_join():
    for _ in range(3):
        st = json.loads(ev("JSON.stringify({joined:joined})") or "{}")
        if st.get("joined"):
            return True
        ev("(function(){var n=document.getElementById('nameInput');"
           "if(n&&!n.value){n.value='主机自测';n.dispatchEvent(new Event('input',{bubbles:true}));}"
           "return 1;})()")
        btn = ev("(function(){var ids=['joinBtn','startBtn','join','btnJoin','play'];"
                 "for(var i=0;i<ids.length;i++){var e=document.getElementById(ids[i]);"
                 "if(e){var r=e.getBoundingClientRect();if(r.width>0)return JSON.stringify({id:ids[i],"
                 "x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)});}}"
                 "var bs=document.querySelectorAll('button,.btn,.panel button');"
                 "for(var j=0;j<bs.length;j++){var b=bs[j].getBoundingClientRect();"
                 "if(b.width>0&&/开战|加入|开始/.test(bs[j].textContent))return JSON.stringify({"
                 "id:'text:'+bs[j].textContent.trim().slice(0,6),x:Math.round(b.x+b.width/2),"
                 "y:Math.round(b.y+b.height/2)});}return 'null';})()")
        if not btn or btn == "null":
            return False
        b = json.loads(btn)
        c.tap(b["x"], b["y"])
        print("点了开始界面按钮:", b)
        time.sleep(1.6)
    return bool(json.loads(ev("JSON.stringify({joined:joined})") or "{}").get("joined"))


R["joined_ok"] = try_join()

# ---------- 3. 布局：开火键位置 + 零重叠 ----------
R["layout"] = ev("(function(){var r=ML.report(VW,VH,{host:true,sniper:true});"
                 "var f=r.layout.fire;return JSON.stringify({overlaps:r.overlaps.length,"
                 "outOfSafe:r.outOfSafe,minGap:r.minGap,"
                 "fire:{cx:f.cx,cy:f.cy,r:f.r},cxPct:+(f.cx/VW*100).toFixed(1),"
                 "cyPct:+(f.cy/VH*100).toFixed(1),els:ML.elements(r.layout).length,"
                 "menu:Object.keys(r.layout.top.menu)});})()")

# ---------- 4. 开火键真按：命中判定 + 弹药下降 ----------
def ammo():
    return ev("(function(){var p=players[myId];return p?p.am+'/'+p.w:'';})()")


before = ammo()
fc = json.loads(ev("(function(){var f=mlLayout().fire;return JSON.stringify({x:f.cx,y:f.cy});})()") or "{}")
c.tap(fc["x"], fc["y"], hold_ms=60)
hit = ev("window.__mobileLastHit")
c.hold(fc["x"], fc["y"], 700)          # 按住 = 连发/开火
time.sleep(0.5)
R["fire"] = json.dumps({"hit": hit, "tap_at": fc, "ammo_before": before, "ammo_after": ammo()},
                       ensure_ascii=False)

# ---------- 5. FOV 档位循环 ----------
seq = ev("(function(){var out=[];for(var i=0;i<4;i++){cycleFov();var s=fovScale();"
         "out.push({m:fovMode,tag:fovTag(),s:+s.toFixed(3),vw:Math.round(VW/s),vh:Math.round(VH/s),"
         "areaPct:+(VW*VH/(s*s)/(WORLD_W*WORLD_H)*100).toFixed(1)});}setFovMode('area');"
         "return JSON.stringify(out);})()")
R["fov_cycle"] = seq
R["fov_default"] = ev("(function(){var s=fovScale();return JSON.stringify({m:fovMode,s:+s.toFixed(3),"
                      "vw:Math.round(VW/s),vh:Math.round(VH/s),"
                      "areaPct:+(VW*VH/(s*s)/(WORLD_W*WORLD_H)*100).toFixed(1)});})()")

# ---------- 6. 换图倒计时：快照在推进 ----------
a = ev("mapSwitchIn")
time.sleep(4)
b = ev("mapSwitchIn")
R["countdown"] = json.dumps({"t0": a, "t4s": b, "decreasing": (a is not None and b is not None and b < a),
                             "hud_text": "换图 " + ev("fmtCountdown(mapSwitchIn)")}, ensure_ascii=False)

for k, v in R.items():
    print("\n[%s] %s" % (k, v))
c.close()
