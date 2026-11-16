# -*- coding: utf-8 -*-
"""打包"纯净运行包"（给机房电脑用）：只含运行必需文件，不含开发/训练/安卓/文档工程。

产物：dist/ReactionProtocol-PC-<version>.zip
包里保留 start.sh 的可执行位（zip external_attr = 0o755），并校验能真正跑起来。

用法：
    python tools/make_release_zip.py                # 打包 + 解压回放自检
    python tools/make_release_zip.py --no-verify    # 只打包
"""
import argparse
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERSION = "1.4"

# 运行必需：服务端 + 游戏内核 + 数据 + 前端 + 启动脚本 + 说明
INCLUDE_FILES = [
    "server.py",
    "start.sh",
    "start.bat",
    "启动说明.md",
    "使用说明.md",
]
INCLUDE_DIRS = [
    ("game", ["__init__.py", "chem.py", "ai_infer.py", "reactions.py", "weapons.py"]),
    ("data", ["maps.json", "weapons.json", "reactions.json", "ai_weights.json"]),
]
# public/ 整个目录都要（前端页面 + 资源）
PUBLIC_DIR = "public"
EXECUTABLE = {"start.sh"}


def collect():
    entries = []          # (abs_path, arcname, executable)
    for rel in INCLUDE_FILES:
        p = os.path.join(ROOT, rel)
        if not os.path.isfile(p):
            raise SystemExit("缺少必需文件: %s" % rel)
        entries.append((p, rel, rel in EXECUTABLE))
    for d, files in INCLUDE_DIRS:
        for f in files:
            p = os.path.join(ROOT, d, f)
            if not os.path.isfile(p):
                raise SystemExit("缺少必需文件: %s/%s" % (d, f))
            entries.append((p, "%s/%s" % (d, f), False))
    pub = os.path.join(ROOT, PUBLIC_DIR)
    if not os.path.isdir(pub):
        raise SystemExit("缺少 public/ 目录")
    for name in sorted(os.listdir(pub)):
        p = os.path.join(pub, name)
        if os.path.isfile(p) and not name.endswith(".pyc"):
            entries.append((p, "public/" + name, False))
    return entries


def build_zip(entries, out_path):
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p, arc, exe in entries:
            zi = zipfile.ZipInfo(arc, date_time=(2026, 10, 25, 12, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            mode = 0o755 if exe else 0o644
            zi.external_attr = (mode & 0xFFFF) << 16
            with open(p, "rb") as f:
                data = f.read()
            if arc.endswith(".sh"):
                # 防御：Windows 上取到的脚本可能是 CRLF，Linux 会报 "bash\r"
                data = data.replace(b"\r\n", b"\n")
            z.writestr(zi, data)
    return out_path


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def verify(out_path):
    """解压到临时目录 → 启动 → 抓 /status.json 与首页 → 关闭。"""
    tmp = tempfile.mkdtemp(prefix="rp_zip_check_")
    ok = True
    try:
        with zipfile.ZipFile(out_path) as z:
            z.extractall(tmp)
            names = z.namelist()
        print("解压文件数:", len(names))
        # 关键文件存在性 + start.sh 可执行位（zip 里记录）
        for req in ("server.py", "start.sh", "public/index.html", "data/maps.json",
                    "game/chem.py", "启动说明.md"):
            if req not in names:
                print("[FAIL] 包内缺少", req)
                ok = False
        with zipfile.ZipFile(out_path) as z:
            info = z.getinfo("start.sh")
            mode = (info.external_attr >> 16) & 0o777
            print("start.sh 权限位: 0o%o" % mode)
            if mode != 0o755:
                print("[FAIL] start.sh 在包里没有可执行位")
                ok = False
        # 真正跑起来
        port = free_port()
        proc = subprocess.Popen([sys.executable, "server.py", str(port), "--no-browser", "--bots", "2"],
                                cwd=tmp, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            deadline = time.time() + 25
            status = None
            while time.time() < deadline:
                try:
                    with urllib.request.urlopen(
                            "http://127.0.0.1:%d/status.json" % port, timeout=3) as r:
                        status = r.read().decode("utf-8")
                    break
                except Exception:
                    time.sleep(0.4)
            if status is None:
                print("[FAIL] 解压后服务端起不来")
                ok = False
            else:
                import json
                d = json.loads(status)
                print("[OK] 解压后服务端可运行：map=%s tick=%s players=%d tutorials=%d"
                      % (d.get("map"), d.get("tick"), len(d.get("players", [])), d.get("tutorials", 0)))
                html = urllib.request.urlopen("http://127.0.0.1:%d/" % port, timeout=4).read()
                print("[OK] 首页 %d 字节，含标题: %s" % (len(html), b"Reaction Protocol" in html))
                if b"Reaction Protocol" not in html:
                    ok = False
                # 前端资源可访问
                for f in ("game.js", "ui.js", "tutorial.js", "mobile.js", "mobile_layout.js"):
                    u = urllib.request.urlopen("http://127.0.0.1:%d/%s" % (port, f), timeout=4)
                    if u.status != 200 or len(u.read()) < 1000:
                        print("[FAIL] 前端资源异常:", f)
                        ok = False
                print("[OK] 前端资源 5/5 可访问")
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=6)
            except Exception:
                proc.kill()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-verify", action="store_true")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    entries = collect()
    out = a.out or os.path.join(ROOT, "dist", "ReactionProtocol-PC-%s.zip" % VERSION)
    build_zip(entries, out)
    size = os.path.getsize(out)
    print("已打包: %s（%d 个文件，%.2f MB）" % (out, len(entries), size / 1048576.0))
    total = sum(os.path.getsize(p) for p, _, _ in entries)
    print("原始大小 %.2f MB → 压缩后 %.2f MB" % (total / 1048576.0, size / 1048576.0))
    if not a.no_verify:
        ok = verify(out)
        print("\n自检结果:", "通过 ✓" if ok else "失败 ✗")
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
