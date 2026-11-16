#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reaction Protocol v3.0 · 加密发布包启动器。

这个文件只做三件事：
  1. 校验并解密同目录的 payload.bin（游戏源码 + 数据 + 前端资源都放在里面，**不以明文分发**）；
  2. 解到一个随机临时目录，在里面启动 server.py；
  3. 退出（正常结束 / Ctrl+C / 关窗口）时把临时目录删掉。

真正的游戏逻辑不在这里，也不在任何明文文件里。想抄的人直接拷文件夹只能拿到密文。

⚠ 诚实说明：本机可执行的程序无法做到"理论上的绝对加密"——运行时文件必然会被还原
   到磁盘（否则 Python 无法 import）。这里做的是"静态加密 + 运行时临时解密 + 水印追溯"，
   目的是防止直接拷走 / 顺手抄袭，挡不住有耐心的人逆向。
"""
import hashlib
import hmac
import io
import os
import shutil
import sys
import tempfile
import zipfile

MAGIC = b"RP33"
SALT_LEN = 16
MAC_LEN = 32
KDF_ROUNDS = 60000

# 口令拆成几段存放（防止一眼看到完整串；不是密码学意义上的秘密）
_WM1 = "Reaction Protocol v3.3 · 北京市建华实验学校 首届“研创杯”参赛作品"
_WM2 = "AGPL-3.0 开源 · 保留署名 · 源码 github.com/scpdr/ReactionProtocal-2D · © 2026"


def _passphrase():
    parts = ["R3n", "ct10n", "-PR0", "t0c0l", "-2026", "-JHSY"]
    return "".join(parts).encode("utf-8")


def _derive(salt):
    return hashlib.pbkdf2_hmac("sha256", _passphrase(), salt, KDF_ROUNDS, dklen=32)


def _xor_stream(data, key):
    out = bytearray(len(data))
    nblocks = (len(data) + 31) // 32
    for b in range(nblocks):
        blk = hashlib.sha256(key + b.to_bytes(4, "big")).digest()
        i = b * 32
        n = min(32, len(data) - i)
        for j in range(n):
            out[i + j] = data[i + j] ^ blk[j]
    return bytes(out)


def load_payload(path):
    """返回内存中的 zip 字节串；校验失败直接报错。"""
    with open(path, "rb") as f:
        blob = f.read()
    if len(blob) < len(MAGIC) + SALT_LEN + MAC_LEN + 8 or not blob.startswith(MAGIC):
        raise SystemExit("payload.bin 格式错误或被截断")
    off = len(MAGIC)
    salt = blob[off:off + SALT_LEN]
    off += SALT_LEN
    mac = blob[off:off + MAC_LEN]
    off += MAC_LEN
    body = blob[off:]
    key = _derive(salt)
    if not hmac.compare_digest(hmac.new(key, body, hashlib.sha256).digest(), mac):
        raise SystemExit("payload.bin 完整性校验失败（文件被修改或损坏）")
    return _xor_stream(body, key)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    payload = os.path.join(here, "payload.bin")
    if not os.path.isfile(payload):
        raise SystemExit("找不到 payload.bin：请确认它和 bootstrap.py 在同一个文件夹里")

    print("=" * 62)
    print("  " + _WM1)
    print("  " + _WM2)
    print("=" * 62)
    print("  正在解密游戏数据 ...")

    raw = load_payload(payload)
    tmpdir = tempfile.mkdtemp(prefix="rp3_")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            z.extractall(tmpdir)
        # 存档目录放在启动器旁边（临时目录退出即删，不能用来存"教程已通关"记录）
        if not os.environ.get("RP_SAVE_DIR"):
            os.environ["RP_SAVE_DIR"] = os.path.join(here, "save")
        os.makedirs(os.environ["RP_SAVE_DIR"], exist_ok=True)
        os.environ["RP_ROOT"] = tmpdir

        server = os.path.join(tmpdir, "server.py")
        if not os.path.isfile(server):
            raise SystemExit("解密失败：负载里没有 server.py")
        sys.argv = [server] + sys.argv[1:]
        sys.path.insert(0, tmpdir)
        print("  解密完成，正在启动 ...\n")
        import runpy
        runpy.run_path(server, run_name="__main__")
        return 0
    except KeyboardInterrupt:
        print("\n  已停止。")
        return 0
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
        print("  临时解密文件已清理。")


if __name__ == "__main__":
    sys.exit(main())
