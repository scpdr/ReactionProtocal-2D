# -*- coding: utf-8 -*-
"""生成 Reaction Protocol **v3.0 加密发布包**（给机房电脑用）。

做了什么：
  1. 只挑"运行必需"的文件（服务端 + 游戏内核 + 数据 + 前端 + 说明），
  2. 在每个源码/前端文件头部注入版权水印（构建 ID 唯一），
  3. 整体压缩后用 PBKDF2-HMAC-SHA256 派生的密钥流加密，附 HMAC 完整性校验，
  4. 生成 _bootstrap.py（解密器）+ payload.bin + 启动脚本 + LICENSE，
  5. 打成 dist/ReactionProtocol-PC-3.0.zip，并**自动解压回放自检**：
     起服务端 → 校验 /status.json 的 version=3.0、地图与教程可用、前端资源可取，
     并确认包内没有明文源码。

用法：
    python tools/make_secure_release.py                 # 打包 + 自检
    python tools/make_secure_release.py --no-verify
"""
import argparse
import hashlib
import hmac
import io
import json
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
VERSION = "3.3"
PKG_NAME = "ReactionProtocol-PC-%s" % VERSION
MAGIC = b"RP33"
KDF_ROUNDS = 60000

SOURCE_FILES = ["server.py"]
GAME_FILES = ["__init__.py", "chem.py", "ai_infer.py", "reactions.py", "weapons.py",
              "events.py"]
DATA_FILES = ["maps.json", "weapons.json", "reactions.json", "ai_weights.json"]
EXTRA_FILES = ["start.sh", "start.bat", "启动说明.md", "使用说明.md", "使用协议.md", "LICENSE"]

WATERMARK_PY = ("# \u00a9 2026 北京市建华实验学校 · 研创杯 · Reaction Protocol v%s\n"
                "# 以 AGPL-3.0 开源 · 保留署名与构建编号 · 源码 github.com/scpdr/ReactionProtocal-2D\n"
                "# build=%s\n" % (VERSION, "%BUILD%"))
WATERMARK_JS = ("/* \u00a9 2026 北京市建华实验学校 · 研创杯 · Reaction Protocol v%s | "
                "AGPL-3.0 · 保留署名 | build=%s */\n" % (VERSION, "%BUILD%"))


def build_id():
    import random
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return stamp + "-" + "%04X" % random.randint(0, 0xFFFF)


def passphrase():
    parts = ["R3n", "ct10n", "-PR0", "t0c0l", "-2026", "-JHSY"]
    return "".join(parts).encode("utf-8")


def xor_stream(data, key):
    out = bytearray(len(data))
    for b in range((len(data) + 31) // 32):
        blk = hashlib.sha256(key + b.to_bytes(4, "big")).digest()
        i = b * 32
        n = min(32, len(data) - i)
        for j in range(n):
            out[i + j] = data[i + j] ^ blk[j]
    return bytes(out)


def encrypt(plain_zip):
    salt = os.urandom(16)
    key = hashlib.pbkdf2_hmac("sha256", passphrase(), salt, KDF_ROUNDS, dklen=32)
    body = xor_stream(plain_zip, key)
    mac = hmac.new(key, body, hashlib.sha256).digest()
    return MAGIC + salt + mac + body, salt


def collect(bid):
    """返回 [(arcname, bytes)]，源码/前端都注入水印。"""
    out = []

    def add(arc, raw, py=False):
        text = raw
        wm = (WATERMARK_PY.replace("%BUILD%", bid) if py
              else WATERMARK_JS.replace("%BUILD%", bid))
        out.append((arc, wm.encode("utf-8") + text))

    for rel in SOURCE_FILES:
        with open(os.path.join(ROOT, rel), "rb") as f:
            add(rel, f.read(), py=True)
    for f in GAME_FILES:
        with open(os.path.join(ROOT, "game", f), "rb") as fh:
            add("game/" + f, fh.read(), py=True)
    for f in DATA_FILES:
        with open(os.path.join(ROOT, "data", f), "rb") as fh:
            out.append(("data/" + f, fh.read()))
    pub = os.path.join(ROOT, "public")
    for name in sorted(os.listdir(pub)):
        p = os.path.join(pub, name)
        if not os.path.isfile(p) or name.endswith(".pyc"):
            continue
        with open(p, "rb") as fh:
            raw = fh.read()
        if name.endswith(".js") or name.endswith(".css"):
            out.append(("public/" + name,
                        WATERMARK_JS.replace("%BUILD%", bid).encode("utf-8") + raw))
        else:
            out.append(("public/" + name, raw))
    return out


def make_plain_zip(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for arc, data in entries:
            zi = zipfile.ZipInfo(arc, date_time=(2026, 10, 25, 18, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
            z.writestr(zi, data)
    return buf.getvalue()


def license_text(bid):
    return """Reaction Protocol v%s · 许可与使用说明（发布包简版）
=================================================

版权所有 (C) 2026 北京市建华实验学校 · 首届“研创杯” · Reaction Protocol 参赛作品
构建编号（Build ID）：%s
源码仓库：https://github.com/scpdr/ReactionProtocal-2D

1. 代码许可：服务端、游戏内核、前端脚本与工具以 GNU AGPL-3.0 开源（全文见包内 LICENSE
   与仓库 https://github.com/scpdr/ReactionProtocal-2D/blob/main/LICENSE）。
   你可以运行、学习、修改、分发；分发修改版或通过网络提供修改版服务时，必须同样以
   AGPL-3.0 公开对应源码，并保留作者署名、版权声明与构建编号。

2. 名称与标识：“Reaction Protocol”、学校与“研创杯”名称/标识不随代码授权。修改版必须
   改名并注明“基于 Reaction Protocol 修改”，不得冒充原作者或学校发布。

3. 非代码内容：地图数据、教程与界面文案、程序合成音效等采用 CC BY-NC-SA 4.0，
   禁止商业使用与商业二次分发；商业授权请联系作者。

4. 本发布包为静态加密包：源码、数据与前端资源存放于 payload.bin，由 bootstrap.py 在
   运行时解密到临时目录，包内不含明文源码；构建时注入唯一 Build ID 与水印，用于溯源。
   加密只是打包方式，不构成对 AGPL-3.0 权利的额外限制，源码始终可从上述仓库获取。

5. 社区公约：文明 ID（禁止侮辱、脏话、人身攻击、刷屏、冒充）；政治正确（禁止涉政敏感、
   地域/民族/性别/宗教歧视、色情暴力与违法内容）；公平竞技（禁止外挂、脚本、刷分、堵家）；
   保护隐私。房主可封禁玩家 ID 或机器 IP，记录保存在房主电脑 save/bans.json，可申诉解封。

6. 免责：本软件按“现状”提供，不附带任何担保；作者不对联机言行、数据丢失或第三方使用
   后果承担责任。完整条款见仓库 使用协议.md。

发现盗用或需要授权，请通过仓库 Issue 联系作者。
""" % (VERSION, bid)


def package_dir(bid, payload, out_dir):
    boot_src = os.path.join(ROOT, "tools", "release_bootstrap.py")
    shutil.copyfile(boot_src, os.path.join(out_dir, "bootstrap.py"))
    with open(os.path.join(out_dir, "payload.bin"), "wb") as f:
        f.write(payload)
    for rel in EXTRA_FILES:
        src = os.path.join(ROOT, rel)
        dst = os.path.join(out_dir, os.path.basename(rel))
        shutil.copyfile(src, dst)
    with open(os.path.join(out_dir, "LICENSE.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write(license_text(bid))
    # 启动脚本：指向 bootstrap.py（保持 LF + 可执行）
    with open(os.path.join(out_dir, "start.sh"), "w", encoding="utf-8", newline="\n") as f:
        f.write("""#!/usr/bin/env bash
# Reaction Protocol v%s 加密发布版 · 启动脚本
# 最稳：bash start.sh        （双击无效时见 启动说明.md 里"麒麟双击 .sh"一节）
cd "$(dirname "$0")" || exit 1
PY=""
for c in python3 python3.12 python3.11 python3.10 python3.9 python; do
  command -v "$c" >/dev/null 2>&1 && PY="$c" && break
done
[ -z "$PY" ] && { echo "未找到 Python 3，请先：sudo apt install -y python3"; read -r -p "回车退出..." _; exit 1; }
exec "$PY" bootstrap.py "$@"
""" % VERSION)
    os.chmod(os.path.join(out_dir, "start.sh"), 0o755)
    bat = """@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY ( where python >nul 2>nul && set "PY=python" )
if not defined PY (
  echo 未找到 Python 3，请安装后重试（勾选 Add Python to PATH）。
  pause
  exit /b 1
)
echo 启动 Reaction Protocol v@VER@（加密发布版）...
%PY% bootstrap.py %*
pause
""".replace("@VER@", VERSION)
    with open(os.path.join(out_dir, "start.bat"), "w", encoding="utf-8", newline="\r\n") as f:
        f.write(bat)


def zip_package(src_dir, out_path):
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for name in sorted(os.listdir(src_dir)):
            p = os.path.join(src_dir, name)
            if not os.path.isfile(p):
                continue
            data = open(p, "rb").read()
            if name.endswith(".sh"):
                data = data.replace(b"\r\n", b"\n")
            zi = zipfile.ZipInfo(PKG_NAME + "/" + name, date_time=(2026, 10, 25, 18, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = ((0o755 if name.endswith(".sh") else 0o644) & 0xFFFF) << 16
            z.writestr(zi, data)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def verify(zip_path):
    tmp = tempfile.mkdtemp(prefix="rp3_verify_")
    ok = True
    try:
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(tmp)
            names = z.namelist()
            start_info = z.getinfo(PKG_NAME + "/start.sh")
            mode = (start_info.external_attr >> 16) & 0o777
        base = os.path.join(tmp, PKG_NAME)
        print("包内文件：", ", ".join(sorted(os.listdir(base))))
        if mode != 0o755:
            print("[FAIL] start.sh 权限位不是 755：0o%o" % mode)
            ok = False
        payload = open(os.path.join(base, "payload.bin"), "rb").read()
        # 密文不应包含明文源码特征
        leaks = [m for m in (b"def update(", b"function render", b"PLAYER_SPEED",
                             b"class Game", b"ai_weights") if m in payload]
        print("明文泄露检查：", "无" if not leaks else "发现 %r" % leaks)
        if leaks:
            ok = False
        if any(n.endswith(".py") and not n.endswith("/bootstrap.py") for n in names):
            bad = [n for n in names if n.endswith(".py") and not n.endswith("/bootstrap.py")]
            print("[FAIL] 包内出现了游戏源码 .py：%r" % bad)
            ok = False
        else:
            print("[OK] 包内只有 bootstrap.py（启动器），没有游戏源码明文")

        port = free_port()
        proc = subprocess.Popen([sys.executable, "bootstrap.py", str(port), "--no-browser"],
                                cwd=base, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            deadline = time.time() + 40
            status = None
            while time.time() < deadline:
                try:
                    with urllib.request.urlopen("http://127.0.0.1:%d/status.json" % port,
                                                timeout=3) as r:
                        status = json.loads(r.read().decode("utf-8"))
                    break
                except Exception:
                    time.sleep(0.5)
            if not status:
                out = proc.stdout.read(4000).decode("utf-8", "replace") if proc.stdout else ""
                print("[FAIL] 解压后启动失败；输出：\n" + out)
                return False
            print("[OK] 启动成功：version=%s map=%s tick=%s players=%d"
                  % (status.get("version"), status.get("map"), status.get("tick"),
                     len(status.get("players", []))))
            if status.get("version") != VERSION:
                print("[FAIL] version 字段不是 %s" % VERSION)
                ok = False
            # 前端资源（含水印）
            checks = {}
            for f in ("index.html", "game.js", "ui.js", "tutorial.js", "mobile.js"):
                data = urllib.request.urlopen("http://127.0.0.1:%d/%s" % (port, f),
                                              timeout=5).read()
                checks[f] = len(data)
                if len(data) < 500:
                    print("[FAIL] 资源过小:", f)
                    ok = False
            print("[OK] 前端资源：", checks)
            js = urllib.request.urlopen("http://127.0.0.1:%d/game.js" % port, timeout=5).read()
            if "北京市建华实验学校".encode("utf-8") not in js:
                print("[FAIL] game.js 里没有版权水印")
                ok = False
            else:
                print("[OK] 前端已带版权水印")
            # 教程小地图（服务端会再加载一份 server.py 副本，验证加密包下仍可用）
            st2 = json.loads(urllib.request.urlopen(
                "http://127.0.0.1:%d/status.json" % port, timeout=5).read().decode("utf-8"))
            if "tutorial_sessions" not in st2:
                print("[FAIL] status 缺少 tutorial_sessions（教程功能异常）")
                ok = False
            else:
                print("[OK] 教程子局字段正常：tutorial_sessions=%s"
                      % st2.get("tutorial_sessions"))
            # 随机事件 / Boss / 离线榜单字段
            if "event" in st2 and "boss_alive" in st2:
                print("[OK] 随机事件字段正常：event=%s boss_alive=%s"
                      % (st2.get("event"), st2.get("boss_alive")))
            else:
                print("[FAIL] status 缺少随机事件字段（event/boss_alive）")
                ok = False
            if "offline" in st2:
                print("[OK] 离线榜单字段正常：offline=%d 条"
                      % len(st2.get("offline") or []))
            else:
                print("[FAIL] status 缺少 offline 字段")
                ok = False
            # 存档目录应落在启动器旁边
            save_env = os.path.join(base, "save")
            if not os.path.isdir(save_env):
                print("[WARN] 尚未生成 save/（首次进教程后才会写）")
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=8)
            except Exception:
                proc.kill()
        # 退出后临时目录：Windows 上 terminate() 是强杀、不跑 finally，所以这里只记录不判失败
        leftovers = [d for d in os.listdir(tempfile.gettempdir()) if d.startswith("rp3_")]
        print("[INFO] 强制结束进程后残留的临时解密目录：%d 个（正常按 Ctrl+C 退出会自动清理）"
              % len(leftovers))
        for d in leftovers:
            shutil.rmtree(os.path.join(tempfile.gettempdir(), d), ignore_errors=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-verify", action="store_true")
    a = ap.parse_args()
    bid = build_id()
    entries = collect(bid)
    plain = make_plain_zip(entries)
    payload, salt = encrypt(plain)
    print("源文件 %d 个，明文 zip %.1f KB → 密文 %.1f KB（build=%s）"
          % (len(entries), len(plain) / 1024.0, len(payload) / 1024.0, bid))

    stage = os.path.join(ROOT, "dist", "_stage_" + PKG_NAME)
    shutil.rmtree(stage, ignore_errors=True)
    os.makedirs(stage, exist_ok=True)
    package_dir(bid, payload, stage)
    out_zip = os.path.join(ROOT, "dist", PKG_NAME + ".zip")
    zip_package(stage, out_zip)
    shutil.rmtree(stage, ignore_errors=True)
    print("已生成：%s（%.2f MB）" % (out_zip, os.path.getsize(out_zip) / 1048576.0))
    if a.no_verify:
        return 0
    ok = verify(out_zip)
    print("\n自检结果：", "通过 ✓" if ok else "失败 ✗")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
