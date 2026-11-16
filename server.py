#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Reaction Protocol 服务端
纯 Python 标准库，零第三方依赖，可在银河麒麟 / Windows / Linux 上运行。

启动:  python3 server.py [端口] [--bots 数量]
示例:  python3 server.py 8765 --bots 4
"""
import base64
import collections
import hashlib
import json
import math
import os
import queue
import random
import socket
import struct
import sys
import threading
import time

# 控制台编码兜底：Windows 默认 GBK，版权号/下标字符（©、₂）会让 print 抛 UnicodeEncodeError。
# 这里只把不可编码字符替换成 "?"，绝不因为一行 banner 就让服务器起不来。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except Exception:      # noqa: BLE001  旧版解释器/被重定向的流不支持 reconfigure
        pass

# 资源根目录：PC 上等于本文件所在目录；安卓 APK 内嵌模式会指向解包后的私有目录
ROOT = os.environ.get("RP_ROOT") or os.path.dirname(os.path.abspath(__file__))

# ---------------- 版本与版权水印 ----------------
RP_VERSION = "3.3"
RP_WATERMARK = ("Reaction Protocol 3.3 · 北京市建华实验学校 首届“研创杯”参赛作品 · "
                "AGPL-3.0 开源 · 保留署名 · github.com/scpdr/ReactionProtocal-2D")
RP_COPYRIGHT = "© 2026 北京市建华实验学校 · 研创杯 · Reaction Protocol"

# 随机事件系统开关（RP_EVENTS=0 可关掉；比赛演示默认开启）
EVENTS_ENABLED = os.environ.get("RP_EVENTS", "1") != "0"

# 化学引擎（纯标准库；与本文件同级的 game/ 包）
sys.path.insert(0, ROOT)
try:
    from game.chem import ChemModel
except ImportError as _e:  # pragma: no cover
    raise SystemExit("[错误] 缺少 game/chem.py，请确认部署包完整：%s" % (_e,))

# ---- 可选：训练好的纯 Python 策略接管机器人（LAN_AI_BOTS=1）----
from game import events as rpevents      # 随机事件系统（独立模块，见 game/events.py）
AI_POLICY = None
AI_WEAPON_IDS = ["knife", "pistol", "rifle", "sniper", "shotgun", "lmg", "limewater", "vitc"]
# 人味瞄准：转向速度、反应延迟、距离相关误差（避免 0 帧拉枪/枪枪锁头）
AI_TURN_RATE = 4.8          # rad/s（约 275°/s）
AI_REACTION_MIN = 0.18
AI_REACTION_MAX = 0.34
AI_AIM_ERR = 0.018
# AI 狙击配额：30s 窗口内最多 18% 时间持狙击（防止一发酸中毒的狙击被 AI 无限刷）
AI_SNIPER_WINDOW = 30.0
AI_SNIPER_MAX_FRAC = 0.18
AI_SNIPER_FRAC_BY_DIFF = {"easy": 0.08, "normal": 0.12, "hard": 0.18}
# 训练策略只会"看见目标才行动"，没目标时它会原地待机 → 真人没靠近就看不到机器人动。
# 连续这么多秒没有可打目标，就由巡逻兜底接管移动（一旦发现目标立刻交回策略）。
AI_PATROL_AFTER = 1.2
AI_SNIPER_BLOCK_AFTER_SHOT = 4.0


def ai_sniper_max_frac(p):
    return p.get("ai_sniper_frac", AI_SNIPER_MAX_FRAC)


def ai_sniper_reset(p, now):
    if now - p.get("ai_snip_start", -1e9) >= AI_SNIPER_WINDOW:
        p["ai_snip_start"] = now
        p["ai_snip_used"] = 0.0


def ai_sniper_available(p, now):
    ai_sniper_reset(p, now)
    if now < p.get("ai_snipe_block_until", 0.0):
        return False
    return p.get("ai_snip_used", 0.0) < AI_SNIPER_WINDOW * ai_sniper_max_frac(p)


def ai_sniper_consume(p, now, dt):
    ai_sniper_reset(p, now)
    if p.get("weapon") == "sniper" and p.get("alive"):
        p["ai_snip_used"] = p.get("ai_snip_used", 0.0) + dt


def load_ai_policy(weights=None):
    """加载训练好的纯 Python 策略权重（可选）；失败自动回退 FSM，不影响开服。"""
    global AI_POLICY
    path = weights or os.environ.get("LAN_AI_WEIGHTS") or os.path.join(ROOT, "data", "ai_weights.json")
    try:
        from game.ai_infer import AIPolicy
        AI_POLICY = AIPolicy(path)
        print("  [AI] 已加载策略权重: %s" % path)
        return True
    except Exception as _e:      # 权重缺失时回退 FSM，不影响正常开服
        AI_POLICY = None
        print("  [AI] 加载失败，回退 FSM: %s" % _e)
        return False


def parse_args(argv):
    """命令行参数 -> (port, bots, open_browser, tick)。PC 端 CLI 与安卓端共用同一套解析。

    --tick 60 可让 PC 也按 60Hz 跑（默认 30Hz）。
    """
    port, bots, open_browser, tick = 8765, 0, True, None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--bots" and i + 1 < len(argv):
            bots = max(0, int(argv[i + 1])); i += 2
        elif a == "--tick" and i + 1 < len(argv):
            tick = int(argv[i + 1]); i += 2
        elif a == "--no-browser":
            open_browser = False; i += 1
        elif a == "--ai":
            os.environ["LAN_AI_BOTS"] = "1"; i += 1
        elif a.isdigit():
            port = int(a); i += 1
        else:
            i += 1
    return port, bots, open_browser, tick


# ---------------- 启动参数（模块级默认值；import 时不再解析 argv） ----------------
PORT = 8765
BOTS = 0
OPEN_BROWSER = True
if os.environ.get("LAN_AI_BOTS") == "1":
    load_ai_policy()

BASE = os.path.join(ROOT, "public")
# 默认逻辑帧率：60Hz（PC 与手机一致）。机房/老旧机器可用 --tick 30 降低占用。
# 30Hz 时"输入→服务器步进"最多白等 33ms，叠加插值缓冲后明显不跟手，故默认 60。
TICK = int(os.environ.get("TICK", "60")) or 60
PLAYER_R = 16                   # 玩家半径
PLAYER_SPEED = 230.0            # 移动速度 px/s
RESPAWN_TIME = 3.0              # 重生秒数
# ---------------- 地图池（服务器加载；客户端通过 hello/map 事件同步） ----------------
MAPS = []
_MAP_FILE = os.path.join(ROOT, "data", "maps.json")


def _fallback_map():
    return {"id": "fallback", "name": "FALLBACK", "w": 2400, "h": 1600,
            "walls": [[0, 0, 2400, 20], [0, 1580, 2400, 20],
                      [0, 0, 20, 1600], [2380, 0, 20, 1600]],
            "spawn": {"red": [40, 500, 260, 600], "blue": [2100, 500, 260, 600]},
            "ammo": [], "labels": []}


def load_maps():
    """从 ROOT/data/maps.json 载入地图池；失败时用内置兜底地图。"""
    global MAPS, MAP_BY_ID
    try:
        with open(_MAP_FILE, encoding="utf-8") as _f:
            MAPS = json.load(_f)
    except Exception as _e:  # pragma: no cover
        print("[地图] 加载失败，使用内置空地图:", _e)
        MAPS = []
    if not MAPS:
        MAPS = [_fallback_map()]
    MAP_BY_ID = {m["id"]: m for m in MAPS}
    return MAPS


MAP_BY_ID = {}
load_maps()
MAP_SWITCH_SECONDS = int(os.environ.get("MAP_SWITCH_SECONDS", "240"))


def configure(root=None, port=None, bots=None, open_browser=None, map_switch_seconds=None,
              ai_weights=None, load_ai=False, tick=None):
    """把服务端指向某个资源根目录（安卓内嵌模式必用），并可覆盖运行参数。

    root 之下应有 public/ 与 data/ 两个目录（APK 首启从 assets 解包得到）。
    """
    global ROOT, BASE, _MAP_FILE, PORT, BOTS, OPEN_BROWSER, MAP_SWITCH_SECONDS, TICK
    if tick is not None:
        # 逻辑帧率可配：30Hz（PC 默认/省电）或 60Hz（手机开服用，需 CPU 跟得上）
        TICK = max(20, min(120, int(tick)))
    if root:
        ROOT = os.path.abspath(str(root))
        BASE = os.path.join(ROOT, "public")
        _MAP_FILE = os.path.join(ROOT, "data", "maps.json")
        if ROOT not in sys.path:
            sys.path.insert(0, ROOT)
    if port is not None:
        PORT = int(port)
    if bots is not None:
        BOTS = max(0, int(bots))
    if open_browser is not None:
        OPEN_BROWSER = bool(open_browser)
    if map_switch_seconds is not None:
        MAP_SWITCH_SECONDS = int(map_switch_seconds)
    load_maps()
    if load_ai:
        load_ai_policy(ai_weights)
    # 换 root 往往伴随换地图：必须重跑 apply_map，否则 ACTIVE_MAP/WORLD_W/WALLS/AMMO_PADS
    # 仍停留在旧地图（早期版本只重绑 MAPS，属潜伏缺陷）
    apply_map(ACTIVE_MAP["id"] if ACTIVE_MAP in MAPS else MAPS[0]["id"])
    load_tutorial_done()
    return {"root": ROOT, "base": BASE, "port": PORT, "bots": BOTS,
            "maps": [m["id"] for m in MAPS], "ai": AI_POLICY is not None}

WORLD_W, WORLD_H = 2400, 1600
WALLS = []
SPAWN_ZONES = {}
AMMO_PADS = []
ACTIVE_MAP = MAPS[0]


def apply_map(map_id):
    """切换当前地图（只改全局地图数据；玩家坐标/弹药由调用方处理）。"""
    global ACTIVE_MAP, WORLD_W, WORLD_H, WALLS, SPAWN_ZONES, AMMO_PADS
    m = MAP_BY_ID.get(map_id, MAPS[0])
    ACTIVE_MAP = m
    WORLD_W, WORLD_H = int(m["w"]), int(m["h"])
    WALLS = [tuple(w) for w in m.get("walls", [])]
    SPAWN_ZONES = m.get("spawn", SPAWN_ZONES)
    AMMO_PADS = [tuple(a) for a in m.get("ammo", [])]
    return m


apply_map(ACTIVE_MAP["id"])

# ---------------- 核心参数（混淆存储 + 完整性校验，防手改参数作弊） ----------------
# 校验失败会拒绝启动；请使用官方发布版本，不要手动编辑此段
_PARAMS_BLOB = (
    "Cdjw0BnEz9NOkcCrkCXhDyF4OzU/BTcvNi54YGp0bHZ4OzU/BSg7PjMvKXhga2pqdGp2eDsp"
    "KTMpLnhgaWp2eDspKTMpLgU3MzR4YGhqdng+Mzw8MzkvNi4jeGAheD87KSN4YCF4Pj8uPzku"
    "eGBuaGp2eD8oKAU3LzYueGBrdGJ2eDwzKD8FKig1OHhganRpb3Z4NDs3P3hgeL302r/Xz3gn"
    "dngyOyg+eGAheD4/Lj85LnhgYm9qdng/KCgFNy82LnhganRub3Z4PDMoPwUqKDU4eGBqdGJv"
    "dng0Ozc/eGB4v8Hqs8DkeCd2eDQ1KDc7NnhgIXg+Py4/OS54YGxqanZ4PygoBTcvNi54YGt0"
    "anZ4PDMoPwUqKDU4eGBqdGx2eDQ7Nz94YHi8w/Sz2sB4Jyd2eDIsLgU4NTQvKXhga29qdngy"
    "LC4FKS4oPzsxeGBidngxMzY2eGBramp2eDQvMT8FPj82OyN4YGl0anZ4NC8xPwU+Nz14YGtv"
    "anZ4NC8xPwU3LzYueGBodng0LzE/BSk5NSg/eGBpampqanZ4KD89PzQFPj82OyN4YG50anZ4"
    "KD89PzQFKDsuP3hgaGp0anZ4KTEzNjYFOT54YGxqdGp2eCkxMzY2BT4vKHhgYnRqdngpNzUx"
    "PwU5PnhgbGp0anZ4KTc1MT8FNzsieGBsdngpNzUxPwUoeGBoaGp0anZ4KTc1MT8FLjM3P3hg"
    "a2h0anZ4KSo/Pz4FNy82Lnhga3Rub3Z4KS4oPzsxeGBvanZ4LT87KjU0KXhgIXgxNDM8P3hg"
    "IXg+Nz14YG9vdng3PzY/P3hgLigvP3Z4NDs3P3hgeL/cwb/S2nh2eCg7ND0/eGBib3Z4KDsu"
    "P3hganRubyd2eDY3PXhgIXg4KDs0PT94YGhsamp2eDgpKj8/PnhgaWJqanZ4Pjc9eGBrb3Z4"
    "NjspPyh4YC4oLz92eDc7PXhga2pqdng3KSooPzs+eGBqdGprdng0Ozc/eGB4vMbgvMTweHZ4"
    "NDUoPzY1Oz54YC4oLz92eCg7Lj94YGp0amJvdngoPzY1Oz54YGp2eCkqKD87PnhganRqa2In"
    "dngqMykuNTZ4YCF4OCg7ND0/eGBrYmpqdng4KSo/Pz54YGhuamp2eD43PXhgaGx2eDc7PXhg"
    "a2h2eDcpKig/Oz54YGp0amtvdng0Ozc/eGB4vNPRvMTweHZ4KDsuP3hganRpdngoPzY1Oz54"
    "YGt0aXZ4KSooPzs+eGBqdGpraCd2eCgzPDY/eGAheDgoOzQ9P3hgaG5qanZ4OCkqPz8+eGBp"
    "ampqdng+Nz14YGhodng3Oz14YGlqdng3KSooPzs+eGBqdGprbHZ4NDs3P3hgeLz3/7zE8Hh2"
    "eCg7Lj94YGp0a2t2eCg/NjU7PnhgaHRrdngpKig/Oz54YGp0amhoJ3Z4KTI1Lj0vNHhgIXg4"
    "KDs0PT94YG9oanZ4OCkqPz8+eGBrYmpqdng+Nz14YGtpdng3Oz14YGx2eDcpKig/Oz54YGp0"
    "am52eDQ7Nz94YHi/zO2/98p4dngqPzY2Py4peGBidngoOy4/eGBqdGNvdngoPzY1Oz54YGh0"
    "YnZ4KSooPzs+eGBqdGsndngpNDMqPyh4YCF4OCg7ND0/eGBpampqdng4KSo/Pz54YG5qamp2"
    "eD43PXhga2tqdng3Oz14YG92eDcpKig/Oz54YGp0amJ2eDQ7Nz94YHi90cO/3eG8xPB4dngo"
    "Oy4/eGBrdG52eCg/NjU7PnhgaHRsdngpKig/Oz54YGp0amppJycn"
)


def _load_params():
    raw = bytes(b ^ 0x5A for b in base64.b64decode("".join(_PARAMS_BLOB)))
    digest, data = raw[:16], raw[16:]
    if hashlib.md5(data).digest() != digest:
        raise SystemExit("[错误] 游戏核心参数校验失败，文件被修改过，请使用官方发布版本。")
    return json.loads(data.decode("utf-8"))


_P = _load_params()
WEAPONS = _P["weapons"]
# 积分规则
KILL_SCORE = _P["kill"]           # 击杀基础分
STREAK_BONUS = _P["streak"]       # 连杀每级额外奖励
REGEN_DELAY = _P["regen_delay"]   # 脱战秒数（受击后多久开始回血）
REGEN_RATE = _P["regen_rate"]     # 回血速度 hp/s
# 烟雾弹
SMOKE_R = _P["smoke_r"]           # 烟雾半径
SMOKE_TIME = _P["smoke_time"]     # 烟雾持续秒数
SMOKE_CD = _P["smoke_cd"]         # 每人冷却秒数
SMOKE_MAX = _P["smoke_max"]       # 场上同时存在上限
# 助攻
ASSIST_SCORE = _P["assist"]       # 助攻积分
ASSIST_MIN_DMG = _P["assist_min"] # 助攻所需最低伤害
# 核弹
NUKE_SCORE = _P["nuke_score"]     # 每次氟泄漏消耗的隐藏可用积分
NUKE_DMG = _P["nuke_dmg"]         # 对敌方每人的伤害
NUKE_DELAY = _P["nuke_delay"]     # 引爆倒计时秒数
NUKE_KILL_MULT = _P["nuke_mult"]  # 核弹造成击杀的积分倍率

# ---------------- 新手教程：按 IP 只强制一次 ----------------
# 完成记录持久化到 ROOT/save/tutorial_done.json（服务端重启后仍然记得，不再强制）
TUT_DONE = set()                  # 已完成教程的 IP 集合
_TUT_LOCK = threading.Lock()


def _tutorial_path():
    # RP_SAVE_DIR：加密发布包用它把"已完成教程的 IP"存到启动器旁边（临时解包目录会被清理）
    save_dir = os.environ.get("RP_SAVE_DIR") or os.path.join(ROOT, "save")
    return os.path.join(save_dir, "tutorial_done.json")


def load_tutorial_done():
    """从磁盘读取已完成教程的 IP 列表。"""
    global TUT_DONE
    path = _tutorial_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        ips = data.get("ips", []) if isinstance(data, dict) else list(data or [])
    except (OSError, ValueError):
        ips = []
    with _TUT_LOCK:
        TUT_DONE = set(str(i) for i in ips)


def save_tutorial_done():
    """原子写回（先写临时文件再替换），避免异常退出把记录写坏。"""
    path = _tutorial_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with _TUT_LOCK:
            ips = sorted(TUT_DONE)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"ips": ips, "saved_at": time.strftime("%Y-%m-%d %H:%M:%S")},
                      f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except OSError as e:
        print("  [教程] 保存失败: %s" % e)


def tutorial_needed(ip):
    """该 IP 是否还需要（被强制）进入新手教程。"""
    with _TUT_LOCK:
        return ip not in TUT_DONE


def mark_tutorial_done(ip):
    with _TUT_LOCK:
        if ip in TUT_DONE:
            return
        TUT_DONE.add(ip)
    save_tutorial_done()
    print("  [教程] %s 已完成新手教程（以后直接进对局）" % ip)


# ---------------- 封禁名单（房主权威：按玩家 ID 或机器 IP 两种形式） ----------------
# 存到 ROOT/save/bans.json（与教程记录同目录；RP_SAVE_DIR 可改到启动器旁边）
BANS = {"names": [], "ips": []}
_BAN_LOCK = threading.Lock()
BAN_KINDS = {"id": "names", "ip": "ips"}


def _ban_path():
    save_dir = os.environ.get("RP_SAVE_DIR") or os.path.join(ROOT, "save")
    return os.path.join(save_dir, "bans.json")


def _norm_name(name):
    return str(name or "").strip()[:12].casefold()


def load_bans():
    """从磁盘读取封禁名单（玩家 ID 不区分大小写，IP 精确匹配）。"""
    global BANS
    path = _ban_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        names = data.get("names", []) if isinstance(data, dict) else []
        ips = data.get("ips", []) if isinstance(data, dict) else []
    except (OSError, ValueError):
        names, ips = [], []
    with _BAN_LOCK:
        BANS = {"names": [_norm_name(n) for n in names if str(n).strip()],
                "ips": [str(i).strip() for i in ips if str(i).strip()]}
    return BANS


def save_bans():
    path = _ban_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with _BAN_LOCK:
            data = {"names": list(BANS["names"]), "ips": list(BANS["ips"]),
                    "saved_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except OSError as e:
        print("  [封禁] 保存失败: %s" % e)


def ban_check(name, ip):
    """返回封禁命中类型：'id'（玩家ID）/ 'ip'（机器IP）/ None。"""
    with _BAN_LOCK:
        if ip and str(ip).strip() in BANS["ips"]:
            return "ip"
        if name and _norm_name(name) in BANS["names"]:
            return "id"
    return None


def ban_add(kind, value):
    """新增一条封禁；kind: 'id' / 'ip'。返回是否新增成功。"""
    key = BAN_KINDS.get(kind)
    if key is None:
        return False
    value = _norm_name(value) if kind == "id" else str(value or "").strip()
    if not value:
        return False
    with _BAN_LOCK:
        if value in BANS[key]:
            return False
        BANS[key].append(value)
    save_bans()
    print("  [封禁] 已封禁%s：%s" % ("玩家ID" if kind == "id" else "机器IP", value))
    return True


def ban_remove(kind, value):
    """解除一条封禁。"""
    key = BAN_KINDS.get(kind)
    if key is None:
        return False
    value = _norm_name(value) if kind == "id" else str(value or "").strip()
    with _BAN_LOCK:
        if value not in BANS[key]:
            return False
        BANS[key].remove(value)
    save_bans()
    print("  [封禁] 已解封%s：%s" % ("玩家ID" if kind == "id" else "机器IP", value))
    return True


def _close_conn_soon(wsc, delay=0.8):
    """延迟关闭连接：先让封禁/踢出提示发送完，再断开（避免提示还没到就掉线）。"""
    def _run():
        time.sleep(delay)
        try:
            wsc.close()
        except OSError:
            pass
    threading.Thread(target=_run, daemon=True).start()


def ban_player_and_kick(pid, mode):
    """封禁在线玩家（mode: id/ip）并立即踢下线；返回是否新增封禁。"""
    with GAME.lock:
        p = GAME.players.get(pid)
        if p is None:
            return False
        name, ip = p["name"], p.get("ip")
    kind = "id" if str(mode).lower() in ("id", "name") else "ip"
    value = name if kind == "id" else ip
    ok = ban_add(kind, value)
    tw = CLIENTS.get(pid)
    if tw is not None:
        tw.send_text(json.dumps({
            "t": "banned", "kind": kind, "name": name, "ip": ip,
            "why": ("你的玩家 ID 已被房主封禁" if kind == "id"
                    else "你的机器 IP 已被房主封禁"),
        }))
        _close_conn_soon(tw)
    print("  [封禁] %s（%s）已踢出房间（%s）" % (name, ip, ok and "新增" or "已在名单"))
    return ok


def kick_player(pid, why="被房主移出房间"):
    tw = CLIENTS.get(pid)
    if tw is None:
        return False
    tw.send_text(json.dumps({"t": "kick", "why": why}))
    _close_conn_soon(tw, 0.6)
    return True


def ban_snapshot(limit=8):
    """主机面板用：返回封禁名单（截断到 limit 条）。"""
    with _BAN_LOCK:
        return {"names": list(BANS["names"])[:limit],
                "ips": list(BANS["ips"])[:limit],
                "n_names": len(BANS["names"]), "n_ips": len(BANS["ips"])}


# ---------------- 新手教程：每个玩家一个私有独立小局 ----------------
# 需求：教程必须是独立关卡（独立小地图），每个用户独立、互相看不见，里面只有人机靶子。
# 实现：为教程单独加载一份 server.py 的"模块副本"（它拥有自己的一套地图/世界全局变量，
#       apply_map 改的是它自己的 WORLD_W/WALLS），然后在副本里为每个玩家各建一个 Game 实例。
#       这样教程小地图与对战大地图可以同时存在，且教程玩家之间也互相隔离。
TUT_MOD = [None]              # 教程专用模块副本
TUT_SESSIONS = {}             # 连接 id(900001+) -> {"game": Game, "pid": 局内 pid, "name": str}
_TUT_SESS_LOCK = threading.Lock()
_TUT_CONN_SEQ = [900000]      # 教程连接 id 序列（与主对局 pid 命名空间分开，避免冲突）
# 教程靶子：名字、坐标、武器、缓冲环（静止不动、不开枪，只挨打）
TUT_DUMMIES = [
    ("靶子A", 700, 210, "pistol", 100.0),
    ("靶子B", 770, 500, "rifle", 100.0),
    ("靶子C", 930, 350, "sniper", 55.0),
]


def _tutorial_module():
    """加载 server.py 的模块副本，专门跑教程小局（懒加载，只加载一次）。"""
    if TUT_MOD[0] is not None:
        return TUT_MOD[0]
    import importlib.util
    path = os.path.abspath(__file__)
    spec = importlib.util.spec_from_file_location("rp_tutorial_srv", path)
    mod = importlib.util.module_from_spec(spec)
    old_env = os.environ.pop("LAN_AI_BOTS", None)      # 教程不需要训练策略，避免重复加载权重
    try:
        spec.loader.exec_module(mod)
    finally:
        if old_env is not None:
            os.environ["LAN_AI_BOTS"] = old_env
    mod.configure(root=ROOT, port=PORT, bots=0, open_browser=False, load_ai=False,
                  map_switch_seconds=10 ** 8)
    mod.apply_map("tutorial")
    TUT_MOD[0] = mod
    print("  [教程] 已就绪：独立小地图 %s（%dx%d）" %
          (mod.ACTIVE_MAP.get("name"), mod.WORLD_W, mod.WORLD_H))
    return mod


def start_tutorial_session(name):
    """开一个私有教程小局：返回 (连接id, 局内pid, Game, 教程地图)。

    每个玩家一个 Game：自己的玩家 + 3 个静止靶子，看不到主对局，也看不到其他教程玩家。
    """
    mod = _tutorial_module()
    with _TUT_SESS_LOCK:
        mod.apply_map("tutorial")                       # 防御：确保副本的世界状态始终是教程小地图
        g = mod.Game()
        g.map_switch_at = time.time() + 10 ** 8        # 教程局不轮换地图
        g.rnd_events.enabled = False                   # 教程局不跑随机事件（保持安静）
        p = g.add_player(name, "red")
        for nm, x, y, wpn, buf in TUT_DUMMIES:
            d = g.add_player(nm, "blue", bot=True)
            d["dummy"] = True
            d["next_think"] = 1e18
            d["x"], d["y"] = float(x), float(y)
            d["weapon"] = wpn
            d["hp"] = float(buf)
            try:
                d["chem"].ring = float(buf)
            except Exception:
                pass
        _TUT_CONN_SEQ[0] += 1
        cid = _TUT_CONN_SEQ[0]
        TUT_SESSIONS[cid] = {"game": g, "pid": p["id"], "name": name}
    return cid, p["id"], g, mod.ACTIVE_MAP


def tutorial_session(cid):
    with _TUT_SESS_LOCK:
        return TUT_SESSIONS.get(cid)


def drop_tutorial_session(cid):
    """移除教程小局（通关/跳过/断线都走这里），返回玩家名字。"""
    with _TUT_SESS_LOCK:
        sess = TUT_SESSIONS.pop(cid, None)
    return sess["name"] if sess else None


# 疾速能力倍率
SPEED_MULT = _P["speed_mult"]
# 高价值目标
HVT_STREAK = _P["hvt_streak"]     # 连杀达到该值成为高价值目标
HVT_BONUS = _P["hvt_bonus"]       # 击杀高价值目标的额外积分
# 聊天
CHAT_KEEP = 120.0                 # 服务器保留聊天记录时长（秒）
# AOE 技能（Q）
SKILL_CD = _P["skill_cd"]         # 技能冷却秒数
SKILL_DUR = _P["skill_dur"]       # 触发后子弹 AOE 持续秒数
AOE_RADIUS = _P["aoe_radius"]     # 溅射半径
AOE_MULT = _P["aoe_mult"]         # 溅射伤害倍率（相对子弹伤害）

STREAK_NAMES = {2: "双杀！", 3: "三连杀！", 4: "四连超凡！", 5: "五连绝世！", 6: "大杀特杀！", 7: "无人能挡！"}

DIFFICULTY = _P["difficulty"]

# 新增两个支援槽（原有 6 把 + 2 = 8 试剂槽）
#   石灰水投掷瓶：碱 / 小范围 AOE（30 mmol OH- 当量）
#   维C 还原剂喷瓶：修复队友缓冲对（还原/清除氧化应激），不造成伤害
EXTRA_WEAPONS = {
    "limewater": {
        "name": "石灰水投掷瓶", "dmg": 60, "rate": 1.0, "mag": 2, "reload": 0,
        "spread": 0.01, "mspread": 0.01, "bspeed": 1500, "brange": 650,
        "noreload": True, "aoe": 80,
    },
    "vitc": {
        "name": "维C还原喷瓶", "dmg": 0, "rate": 0.8, "mag": 3, "reload": 0,
        "spread": 0.02, "mspread": 0.02, "bspeed": 2200, "brange": 900,
        "noreload": True, "support": True, "repair": 15,
    },
}
WEAPONS.update(EXTRA_WEAPONS)
# 备弹：按武器强度分配（不再是无限弹药）；补给点/重生补满
WEAPON_RESERVE = {
    "knife": 0, "pistol": 60, "rifle": 300, "sniper": 30,
    "shotgun": 40, "lmg": 400, "limewater": 2, "vitc": 3, "asniper": 30,
}
# 机枪改为可换弹（备弹 400），不再是“每条命固定弹匣”
WEAPONS["lmg"].pop("noreload", None)
WEAPONS["lmg"]["reload"] = 3.2
# 狙击改为高氯酸（HClO4）：一发即可让满缓冲目标酸中毒
WEAPONS["sniper"]["dmg"] = 150
WEAPONS["sniper"]["name"] = "高氯酸"
KNIFE_SPEED_MULT = 1.18          # 切刀加速
PHYSICAL_KILL_BONUS = 100        # 物理击杀额外积分

# ---- 碱性连狙（NaOH 半自动连发狙击枪）：补足中远距离的"碱侧"点杀能力，平衡高氯酸一家独大 ----
EXTRA_WEAPONS["asniper"] = {
    "name": "碱式连狙", "dmg": 85, "rate": 0.62, "mag": 5, "reload": 2.5,
    "spread": 0.013, "mspread": 0.050, "bspeed": 2600, "brange": 2600,
}
WEAPONS.update(EXTRA_WEAPONS)
# 补齐与狙击同构但缺失的字段（弹速/射程/弹丸数）
for _k in ("pellets",):
    WEAPONS["asniper"].setdefault(_k, WEAPONS["sniper"].get(_k, 1))

# ---- 机枪后坐力抖动 ----
# 每次射击累积 recoil，随后按 RECOIL_DECAY 衰减；实际散布 = 基础散布 + recoil
RECOIL_PER_SHOT = 0.011         # 每发累积（rad）
RECOIL_MAX = 0.085              # 抖动上限（rad）
RECOIL_DECAY = 0.055            # 每秒衰减（rad/s）
RECOIL_WEAPONS = ("lmg",)

# ---- 出生点缓冲地带（防"核弹堵家"）----
BUFFER_EXT = 44.0               # 缓冲带在出生矩形左右外扩
BUFFER_H = 250.0                # 缓冲带高度（出生区正上/正下方）
BUFFER_GRACE = 1.2              # 敌方进入后的宽限时间（秒）
BUFFER_DPS = 16.0               # 宽限结束后的持续伤害（等效旧伤害点/秒）
BUFFER_TICK = 0.4               # 伤害结算步长（秒）
BUFFER_BOSS_OFF = True          # Boss 战全员同队，此时不结算缓冲地带

# ---------------- 化学引擎 ----------------
# 玩家 = 1 L 虚拟缓冲体系；旧版“伤害点”按通道换算成 mmol（见 game/chem.py）
CHEM = ChemModel()
CHEM.cfg.COUNTER_RULES = True       # 线上启用酸碱克制/氧化破甲
CHEM.cfg.RING_IS_RESERVE = True     # 缓冲环=缓冲对储备；酸碱只改 pH，中和/氧化才扣环
#: 参数面板 B 可热调的化学参数（key: 最小, 最大, 步长）
CHEM_PARAMS = {
    "ct0": (0.020, 0.200, 0.010),      # 缓冲对总量 mol（20–200 mmol）
    "pka": (6.00, 8.50, 0.05),         # 缓冲体系 pKa
    "acid": (0.25, 1.00, 0.05),        # 酸/碱：1 旧伤害 -> mmol 系数
    "destroy": (0.50, 2.00, 0.10),     # 物理/氧化：1 旧伤害 -> mmol 缓冲剥离系数
    "low": (2.00, 4.00, 0.10),         # pH 阵亡下限
    "high": (10.00, 12.00, 0.10),      # pH 阵亡上限
}
#: 旧武器 -> 化学伤害通道（8 个试剂槽的完整映射在 data/weapons.json，接入 HUD 时使用）
WEAPON_CHANNEL = {
    "knife": "physical",
    "pistol": "acid",       # 硅酸（弱酸）
    "rifle": "acid",
    "sniper": "acid",
    "asniper": "base",       # 碱式连狙：NaOH 连发点杀（中和酸负荷，pH 推向碱侧）
    "shotgun": "oxidizing",
    "lmg": "base",
    "limewater": "base",     # 石灰水：碱
    "vitc": "reducing",      # 维C：还原/支援
    "nuke": "oxidizing",     # 氟泄漏：强氧化，直接剥离缓冲对
    "zone": "oxidizing",     # 出生点缓冲地带：持续氧化剥离，无视酸碱中和
    "lava": "oxidizing",     # 岩浆地板：氧化灼烧
    "potato": "oxidizing",   # 烫手山芋爆炸：氧化冲击
}


def channel_of(weapon):
    """把 'rifle' / 'rifle_aoe' / 'nuke' 等映射为化学通道。"""
    return WEAPON_CHANNEL.get(str(weapon).split("_")[0], "acid")


#: 客户端弹道特效用的通道/武器编号
CH_CODE = {"acid": 0, "base": 1, "oxidizing": 2, "physical": 3, "reducing": 4}
WEAPON_CODE = {"knife": 0, "pistol": 1, "rifle": 2, "sniper": 3,
               "shotgun": 4, "lmg": 5, "limewater": 6, "vitc": 7, "asniper": 8}

# ---------------- 连接签名（防自制客户端/改包客户端接入） ----------------
_AUTH_SALT = "L4N_Sh00t3r#9F2S_N4L"


def _fnv1a(s):
    h = 0x811C9DC5
    for ch in s.encode("utf-8"):
        h ^= ch
        h = (h * 0x01000193) & 0xFFFFFFFF
    return "%08x" % h


def calc_auth(challenge):
    """客户端需按相同规则对挑战值签名（规则在客户端 JS 内）"""
    return _fnv1a(_AUTH_SALT + str(challenge) + _AUTH_SALT)


def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def circle_hits_walls(x, y, r=PLAYER_R):
    for wx, wy, ww, wh in WALLS:
        cx = clamp(x, wx, wx + ww)
        cy = clamp(y, wy, wy + wh)
        if (x - cx) ** 2 + (y - cy) ** 2 < r * r:
            return True
    return False


def point_in_walls(x, y, margin=2):
    for wx, wy, ww, wh in WALLS:
        if wx - margin <= x <= wx + ww + margin and wy - margin <= y <= wy + wh + margin:
            return True
    return False


def has_los(x1, y1, x2, y2):
    d = math.hypot(x2 - x1, y2 - y1)
    n = int(d // 24) + 1
    for i in range(1, n):
        t = i / n
        if point_in_walls(x1 + (x2 - x1) * t, y1 + (y2 - y1) * t):
            return False
    return True


def spawn_buffer_rects():
    """出生点正上/正下方的缓冲地带矩形（每队一组，元素为 (x, y, w, h)）。

    设计意图：出生区是竖直长条，正上/正下是唯一绕后通道；敌方从那里"堵家"时，
    宽限 BUFFER_GRACE 秒后持续掉血（氧化通道，直接剥离缓冲对，不吃酸碱中和）。
    """
    out = {}
    for team, zone in (SPAWN_ZONES or {}).items():
        rects = []
        # 出生区既可能是单矩形 [x,y,w,h]，也可能是矩形列表 [[x,y,w,h], ...]
        if isinstance(zone, (list, tuple)) and len(zone) >= 4 \
                and all(isinstance(v, (int, float)) for v in list(zone)[:4]):
            zlist = [zone]
        else:
            zlist = list(zone or [])
        for z in zlist:
            try:
                x, y, w, h = [float(v) for v in list(z)[:4]]
            except (TypeError, ValueError):
                continue
            up_h = min(BUFFER_H, max(0.0, y - 24.0))
            if up_h > 40.0:
                rects.append((x - BUFFER_EXT, y - 24.0 - up_h,
                              w + BUFFER_EXT * 2.0, up_h))
            down_y = y + h
            down_h = min(BUFFER_H, max(0.0, WORLD_H - 24.0 - down_y))
            if down_h > 40.0:
                rects.append((x - BUFFER_EXT, down_y,
                              w + BUFFER_EXT * 2.0, down_h))
        out[team] = rects
    return out


# ---------------- 游戏核心 ----------------
class Game:
    def __init__(self):
        self.lock = threading.RLock()
        self.players = {}          # id -> dict
        self.bullets = []
        self.events = []           # 本帧事件，广播后清空
        self.event_log = []        # 累计事件（测试/验收用）
        self.feed = []             # 击杀播报 [{id,k,kt,v,vt,w,expire}]
        self.feed_seq = 0
        self.scores = {"red": 0, "blue": 0}
        self.next_pid = 1
        self.bot_diff = "normal"   # AI 强度
        self.bot_seq = 0           # 机器人命名序号
        self.smokes = []           # 活跃烟雾 [{x,y,r,until}]
        self.ip_stats = {}         # 本次运行期间按 IP 记忆的战绩 {ip: {...}}，断线保留、重连恢复
        self.offline = {}          # 已退出玩家的战绩 {name: {n,team,k,d,s,t}}，榜单上标注"不在线"
        self.seen_ips = set()      # 本次开服以来登录过的 IP
        # ---- 随机事件 / Boss 战 ----
        self.rnd_events = rpevents.RandomEvents(enabled=EVENTS_ENABLED,
                                                interval=(70.0, 110.0), first_delay=45.0)
        self.events_enabled = EVENTS_ENABLED
        self.boss_pid = None
        self.boss_contrib = {}     # pid -> 对 Boss 造成的累计伤害
        self.boss_result = None    # 最近一次 Boss 战结算（贡献榜）
        self._boss_team_backup = {}  # Boss 战期间"全员同阵营"的原阵营备份（结束后恢复）
        self.chat_log = []         # 聊天记录 [{name, team, msg, at}]，保留 CHAT_KEEP 秒
        self.map_switch_at = time.time() + MAP_SWITCH_SECONDS
        self.buffer_rects = {}     # 出生点缓冲地带（按当前地图懒加载）
        self._buf_key = None

    def reset(self):
        """清空对局状态（重开一局/切换地图时用），避免旧比分与机器人残留。"""
        with self.lock:
            self.players = {}
            self.bullets = []
            self.events = []
            self.event_log = []
            self.feed = []
            self.feed_seq = 0
            self.scores = {"red": 0, "blue": 0}
            self.next_pid = 1
            self.bot_seq = 0
            self.smokes = []
            self.ip_stats = {}
            self.offline = {}
            self.seen_ips = set()
            self.rnd_events = rpevents.RandomEvents(enabled=self.events_enabled,
                                                    interval=(70.0, 110.0), first_delay=45.0)
            self.boss_pid = None
            self.boss_contrib = {}
            self.boss_result = None
            self._boss_team_backup = {}
            self.chat_log = []
            self.map_switch_at = time.time() + MAP_SWITCH_SECONDS
            self.buffer_rects = {}
            self._buf_key = None
        apply_map(ACTIVE_MAP["id"] if ACTIVE_MAP else MAPS[0]["id"])

    def set_bots(self, n=None, diff=None):
        """主机创建房间时设置 AI 数量与强度（动态增删机器人）"""
        with self.lock:
            if diff in DIFFICULTY:
                self.bot_diff = diff
            if n is None:
                return
            n = max(0, min(10, int(n)))
            bots = [p for p in self.players.values() if p["bot"]]
            while len(bots) > n:
                self.players.pop(bots.pop()["id"], None)
            while len(bots) < n:
                self.bot_seq += 1
                bots.append(self.add_player("机器人%d号" % self.bot_seq, "auto", bot=True))

    def bot_count(self):
        with self.lock:
            return sum(1 for p in self.players.values() if p["bot"])

    def set_team_bots(self, team, n):
        """管理员游戏中实时调整某队 AI 数量"""
        if team not in ("red", "blue"):
            return
        with self.lock:
            n = max(0, min(6, int(n)))
            bots = [p for p in self.players.values() if p["bot"] and p["team"] == team]
            while len(bots) > n:
                self.players.pop(bots.pop()["id"], None)
            while len(bots) < n:
                self.bot_seq += 1
                bots.append(self.add_player("机器人%d号" % self.bot_seq, team, bot=True))
            print("  [管理] %s队 AI -> %d" % ("红" if team == "red" else "蓝", n))

    def admin_move_team(self, pid, team):
        """管理员将某真人玩家移至另一队（立即换出生点复活）"""
        if team not in ("red", "blue"):
            return
        with self.lock:
            p = self.players.get(int(pid))
            if p is None or p["bot"]:
                return
            if p["team"] == team:
                return
            p["team"] = team
            p["streak"] = 0
            self._respawn(p)
            self.events.append({"t": "moved", "pid": p["id"], "team": team, "name": p["name"]})
            print("  [管理] %s 移至%s队" % (p["name"], "红" if team == "red" else "蓝"))

    POWERS = ("none", "speed", "ghost")
    POWER_NAMES = {"none": "无", "speed": "疾速", "ghost": "隐身"}

    def admin_power(self, pid, power):
        """管理员赋予/切换玩家能力"""
        if power not in self.POWERS:
            return
        with self.lock:
            p = self.players.get(int(pid))
            if p is None or p["bot"]:
                return
            p["power"] = power
            if power == "ammo":
                p["reloading"] = False
            self.events.append({"t": "power", "pid": p["id"], "name": p["name"],
                                "pw": power, "pn": self.POWER_NAMES[power]})
            print("  [管理] %s 能力 -> %s" % (p["name"], self.POWER_NAMES[power]))

    # ---- 参数面板 B：化学参数热调（现场演示用） ----
    @staticmethod
    def chem_snapshot():
        c = CHEM.cfg
        return {"ct0": round(c.CT0, 3), "pka": round(c.PKA, 2),
                "acid": round(c.ACID_MMOL_PER_DMG, 2),
                "destroy": round(c.DESTROY_MMOL_PER_DMG, 2),
                "low": round(c.PH_LOW, 2), "high": round(c.PH_HIGH, 2)}

    def set_chem_param(self, key, value):
        """改一个化学参数并重置所有人的化学状态，改完马上能看到效果。"""
        spec = CHEM_PARAMS.get(key)
        if spec is None:
            return False
        with self.lock:
            lo, hi, step = spec
            try:
                v = float(value)
            except (TypeError, ValueError):
                return False
            v = clamp(v, lo, hi)
            v = round(round(v / step) * step, 3)
            c = CHEM.cfg
            if key == "ct0":
                c.CT0 = v
                c.HALF = v / 2.0
            elif key == "pka":
                c.PKA = v
            elif key == "acid":
                c.ACID_MMOL_PER_DMG = v
            elif key == "destroy":
                c.DESTROY_MMOL_PER_DMG = v
            elif key == "low":
                c.PH_LOW = v
            elif key == "high":
                c.PH_HIGH = v
            now = time.time()
            for p in self.players.values():
                p["chem"].reset(now)
                p["hp"] = 100.0
            self.events.append({"t": "cfg", "cfg": self.chem_snapshot()})
            print("  [参数] %s=%s -> %s" % (key, v, self.chem_snapshot()))
            return True

    def save_ip_stats(self, p, ip):
        """断线时按 IP 保留战绩（本次开服期间有效）"""
        with self.lock:
            if p is not None and not p["bot"]:
                self.ip_stats[ip] = {
                    "name": p["name"], "kills": p["kills"], "deaths": p["deaths"],
                    "score": p["score"], "streak": p["streak"], "bank": p.get("bank", 0),
                }

    def restore_ip_stats(self, p, ip):
        """同 IP 重连恢复战绩，返回是否命中记录"""
        with self.lock:
            st = self.ip_stats.get(ip)
            if st is None:
                return False
            p["kills"] = st["kills"]
            p["deaths"] = st["deaths"]
            p["score"] = st["score"]
            p["streak"] = st["streak"]
            p["bank"] = st.get("bank", 0)     # 隐藏可用积分也要跟着恢复，否则重连后放不出氟泄漏
            return True

    # ---- 积分：显示分 + 隐藏可用分 ----
    def _add_score(self, p, pts):
        """积分入账：显示积分（排名用）与隐藏可用积分同时累加。"""
        if p is None or not pts:
            return
        pts = int(pts)
        p["score"] += pts
        p["bank"] = p.get("bank", 0) + pts

    def can_spend(self, p, cost):
        """该行为是否可用：只用隐藏可用积分判断。"""
        return p is not None and p.get("bank", 0) >= cost

    def spend_points(self, p, cost):
        """扣隐藏可用积分；不足则返回 False（不扣）。"""
        if p is None or p.get("bank", 0) < cost:
            return False
        p["bank"] -= cost
        return True

    # ---- 玩家管理 ----
    def add_player(self, name, team, bot=False):
        with self.lock:
            if team not in ("red", "blue", "boss"):
                reds = sum(1 for p in self.players.values() if p["team"] == "red")
                blues = sum(1 for p in self.players.values() if p["team"] == "blue")
                team = "red" if reds <= blues else "blue"
            pid = self.next_pid
            self.next_pid += 1
            # Boss 战期间中途加入：也统一到"人类阵营"，并记录原阵营以便战后恢复
            if self.boss_pid is not None and team != "boss":
                self._boss_team_backup[pid] = team
                team = "red"
            p = {
                "id": pid, "name": name, "team": team, "bot": bot,
                "x": 0, "y": 0, "angle": 0.0, "hp": 100, "alive": True,
                "chem": CHEM.new_state(time.time()),   # 化学状态（pH/缓冲）
                "weapon": "pistol", "ammo": {w: WEAPONS[w].get("mag", 0) for w in WEAPONS},
                "reserve": {w: WEAPON_RESERVE.get(w, 0) for w in WEAPONS},
                "next_supply": 0.0,
                "st": {"dmg": 0, "rescue": 0, "break": 0},
                "ai_snip_start": -1e9, "ai_snip_used": 0.0, "ai_snipe_block_until": 0.0,                "ai_sniper_frac": (AI_SNIPER_FRAC_BY_DIFF.get(self.bot_diff, AI_SNIPER_MAX_FRAC)
                                   if bot else AI_SNIPER_MAX_FRAC),
                "reloading": False, "reload_end": 0.0, "last_shot": 0.0,
                "kills": 0, "deaths": 0, "streak": 0, "score": 0,
                # 隐藏可用积分：一切"扣积分"的行为（如氟泄漏）只看它够不够，用完直接扣；
                # 与显示积分 score 同步入账但不显示在任何 UI 上（见 _add_score/spend_points）
                "bank": 0,
                "tut": False,             # 是否处于新手教程（此期间不掉血、不被 AI 锁敌）
                "respawn_at": 0.0, "last_dmg_t": 0.0,
                "in": [0, 0, 0, 0],       # 上 右 下 左
                "ax": 0, "ay": 0, "shoot": False, "moving": False,
                "smoke_cd": 0.0, "expose_until": 0.0,
                "power": "none",          # 管理员赋予的能力: none/ammo/speed/ghost
                "nuke_at": None,          # 核弹引爆时刻（None=未激活）
                "hvt": False,             # 高价值目标（连杀达标）
                "last_chat_t": 0.0,
                "skill_cd": 0.0,          # AOE 技能下次可用时刻
                "aoe_until": 0.0,         # 子弹 AOE 生效截止时刻
                "in_spawn": False,        # 位于己方出生点（对敌隐身）
                "nuke_tier": 0,           # 已消耗的核弹档位
                "dmg_log": {},            # 本条命期间对各敌人造成的伤害（助攻结算用）
                # 机器人 AI（aim_off/turn 只在决策时更新，防止每帧抖动）
                "aim_off": [0.0, 0.0], "turn": 1,
                "wp": None, "wp_time": 0.0, "target": None, "next_think": 0.0, "fire": False,
                "next_weapon": 0.0,     # 机器人换枪冷却
                "ind_charges": 2,       # 指示剂侦察剩余次数
                "ind_cd": 0.0,          # 指示剂冷却
                "ind_until": 0.0,       # 被指示剂标记的截止时刻
                # ---- 随机事件 buff / Boss 字段 ----
                "buffs": {},             # {"speed":1.7,"dmg":1.5,"inf_ammo":1,"fire_rate":0.75}
                "forced_w": None,        # 事件强制武器
                "is_boss": False,
                "aim_instant": False,    # Boss：0 帧拉枪（不限制转向速度）
                "boss_shield": 0.0,      # Boss 额外护盾
                "boss_dmg": 0.0,         # 本人对 Boss 造成的累计伤害（贡献榜）
                "boss_cd": {},
                "boss_cfg": None,
            }
            self.players[pid] = p
            self._respawn(p)
            return p

    def remove_player(self, pid):
        with self.lock:
            self.players.pop(pid, None)

    def note_offline(self, p):
        """玩家退出时把战绩留在榜单里（标注"不在线"），下次同名/同 IP 回来时清除。"""
        if p is None or p.get("bot"):
            return
        with self.lock:
            self.offline[p["name"]] = {
                "n": p["name"], "team": p["team"],
                "k": p["kills"], "d": p["deaths"], "s": p["score"],
                "t": time.time(),
            }
            if len(self.offline) > 12:                 # 只留最近退出的 12 人，避免无限增长
                oldest = sorted(self.offline.items(), key=lambda kv: kv[1]["t"])
                for name, _ in oldest[:len(self.offline) - 12]:
                    self.offline.pop(name, None)

    def clear_offline(self, name):
        with self.lock:
            self.offline.pop(name, None)

    def _respawn(self, p):
        z = SPAWN_ZONES.get(p["team"], [50, 560, 200, 480])
        for _ in range(30):
            x = random.uniform(z[0] + 30, z[0] + z[2] - 30)
            y = random.uniform(z[1] + 30, z[1] + z[3] - 30)
            if not circle_hits_walls(x, y):
                break
        p["x"], p["y"] = x, y
        p["hp"] = 100
        p["chem"].reset(time.time())
        p["alive"] = True
        p["reloading"] = False
        p["last_dmg_t"] = time.time()
        for wname in p["ammo"]:
            p["ammo"][wname] = WEAPONS[wname].get("mag", 0)
            p["reserve"][wname] = WEAPON_RESERVE.get(wname, 0)
        p["wp"] = None
        p["target"] = None
        p["dmg_log"] = {}
        p["nuke_at"] = None
        p["hvt"] = False
        p["ind_charges"] = 2
        p["ind_cd"] = 0.0
        p["ai_snip_start"] = time.time()
        p["ai_snip_used"] = 0.0
        p["ai_snipe_block_until"] = 0.0
        p["ind_until"] = 0.0

    # ---- 主循环 ----
    def update(self, now, dt):
        with self.lock:
            self.rnd_events.update(self, now, dt)      # 随机事件/Boss 战
            self._update_players(now, dt)
            self._update_bullets(now, dt)
            # 重生
            for p in self.players.values():
                if not p["alive"] and now >= p["respawn_at"]:
                    self._respawn(p)
            # 清理过期播报和烟雾
            self.feed = [f for f in self.feed if f["expire"] > now]
            self.smokes = [s for s in self.smokes if s["until"] > now]

    # ---- 烟雾弹视觉规则 ----
    def _in_smoke(self, x, y):
        for s in self.smokes:
            if (x - s["x"]) ** 2 + (y - s["y"]) ** 2 < s["r"] * s["r"]:
                return s
        return None

    @staticmethod
    def _seg_near_smoke(x1, y1, x2, y2, s):
        """线段是否穿过烟雾圆（点到线段距离 < r）"""
        dx, dy = x2 - x1, y2 - y1
        ln = dx * dx + dy * dy
        if ln < 1e-6:
            return (s["x"] - x1) ** 2 + (s["y"] - y1) ** 2 < s["r"] * s["r"]
        t = max(0.0, min(1.0, ((s["x"] - x1) * dx + (s["y"] - y1) * dy) / ln))
        px, py = x1 + dx * t, y1 + dy * t
        return (s["x"] - px) ** 2 + (s["y"] - py) ** 2 < s["r"] * s["r"]

    def _can_see(self, x1, y1, x2, y2):
        """完整视线判定：墙体 + 烟雾（烟内互见，内外不互见，视线穿烟被挡）"""
        if not has_los(x1, y1, x2, y2):
            return False
        sa = self._in_smoke(x1, y1)
        sb = self._in_smoke(x2, y2)
        if sa is not None and sa is sb:
            return True                       # 同一团烟雾内互相可见
        if sa is not None or sb is not None:
            return False                      # 内外不可互相看见
        for s in self.smokes:
            if self._seg_near_smoke(x1, y1, x2, y2, s):
                return False                  # 视线穿过烟雾被挡
        return True

    SPAWN_RECTS = {
        "red": (20, 500, 260, 600),
        "blue": (WORLD_W - 280, 500, 260, 600),
    }

    def switch_map(self, now, map_id=None):
        """换图：保留击杀/阵亡/积分，重置位置、化学状态与弹药。

        新手教程地图（"tutorial": true）是独立小局专用，永远不参与轮换。
        """
        ids = [m["id"] for m in MAPS if not m.get("tutorial")]
        cur = ACTIVE_MAP["id"]
        if map_id is None:
            if cur not in ids:            # 当前在教程局等特殊地图：轮换到第一张对战图
                cur = ids[0]
            map_id = ids[(ids.index(cur) + 1) % len(ids)]
        apply_map(map_id)
        self.map_switch_at = now + MAP_SWITCH_SECONDS
        for p in self.players.values():
            self._respawn(p)
        self.bullets = []
        self.smokes = []
        self.events.append({"t": "map", "map": ACTIVE_MAP})
        print("  [地图] 切换到 %s（战绩保留）" % ACTIVE_MAP.get("name", map_id))

    def maybe_switch_map(self, now):
        if now >= self.map_switch_at:
            self.switch_map(now)

    def world_size(self):
        """给事件模块用：当前地图尺寸。"""
        return (WORLD_W, WORLD_H)

    def _in_own_spawn(self, p):
        z = SPAWN_ZONES.get(p["team"])
        if not z:
            return False
        return z[0] <= p["x"] <= z[0] + z[2] and z[1] <= p["y"] <= z[1] + z[3]

    def _buffer_rects(self):
        """当前地图的出生缓冲地带（按地图 id 缓存）。"""
        key = ACTIVE_MAP.get("id") if ACTIVE_MAP else None
        if self._buf_key != key or not self.buffer_rects:
            self._buf_key = key
            self.buffer_rects = spawn_buffer_rects()
        return self.buffer_rects

    def _spawn_zone_pressure(self, p, now, dt):
        """敌方闯入"出生点正上/正下方缓冲地带"超过宽限后持续掉血。

        规则：只对敌方（非本队出生区）生效；Boss 战期间全员同阵营，自动关闭。
        """
        if not p["alive"] or p.get("is_boss") or p.get("dummy") or p.get("tut"):
            p["zone_t"] = 0.0
            p["zone_acc"] = 0.0
            return
        if BUFFER_BOSS_OFF and self.boss_pid is not None:
            p["zone_t"] = 0.0
            p["zone_acc"] = 0.0
            return
        team = p["team"]
        inside = False
        for own, rects in self._buffer_rects().items():
            if own == team:
                continue
            for zx, zy, zw, zh in rects:
                if zx <= p["x"] <= zx + zw and zy <= p["y"] <= zy + zh:
                    inside = True
                    break
            if inside:
                break
        if not inside:
            p["zone_t"] = 0.0
            p["zone_acc"] = 0.0
            return
        p["zone_t"] = float(p.get("zone_t", 0.0)) + dt
        if p["zone_t"] < BUFFER_GRACE:
            p["zone_acc"] = 0.0
            return
        p["zone_acc"] = float(p.get("zone_acc", 0.0)) + dt
        if p["zone_acc"] >= BUFFER_TICK:
            p["zone_acc"] -= BUFFER_TICK
            self._damage(None, p, BUFFER_DPS * BUFFER_TICK, "zone", now)
            if not p["alive"]:
                self.feed_seq += 1
                self.feed.append({
                    "id": self.feed_seq, "k": "缓冲地带", "kt": "red",
                    "v": p["name"], "vt": p["team"], "w": "zone",
                    "expire": now + 5.0,
                })

    def throw_smoke(self, p, x, y, now):
        p["smoke_cd"] = now + SMOKE_CD
        self.smokes.append({
            "x": clamp(x, 40, WORLD_W - 40),
            "y": clamp(y, 40, WORLD_H - 40),
            "r": SMOKE_R, "until": now + SMOKE_TIME,
        })
        if len(self.smokes) > SMOKE_MAX:
            self.smokes.pop(0)

    def _update_players(self, now, dt):
        for p in self.players.values():
            fw = p.get("forced_w")
            if fw and p["weapon"] != fw:
                p["weapon"] = fw
                p["reloading"] = False
            if p["bot"]:
                self._bot_think(p, now)
            if p["alive"] and (p["bot"] or p.get("ai_aim")):
                ai_sniper_consume(p, now, dt)
                if p["weapon"] == "sniper" and not ai_sniper_available(p, now):
                    p["weapon"] = "rifle"      # 配额用完强制换回盐酸枪
                    p["reloading"] = False
            # 机枪后坐力抖动：随时间熨平
            if p.get("recoil"):
                p["recoil"] = max(0.0, float(p["recoil"]) - RECOIL_DECAY * dt)
            if p["alive"]:
                up, right, down, left = p["in"]
                ix, iy = right - left, down - up
                if ix or iy:
                    n = math.hypot(ix, iy)
                    ix, iy = ix / n, iy / n
                    spd = PLAYER_SPEED * (SPEED_MULT if p["power"] == "speed" else 1.0) \
                        * float(p.get("buffs", {}).get("speed", 1.0))
                    if p["weapon"] == "knife":
                        spd *= KNIFE_SPEED_MULT
                    nx = p["x"] + ix * spd * dt
                    if not circle_hits_walls(nx, p["y"]):
                        p["x"] = nx
                    ny = p["y"] + iy * spd * dt
                    if not circle_hits_walls(p["x"], ny):
                        p["y"] = ny
                    p["moving"] = True
                else:
                    p["moving"] = False
                desired = math.atan2(p["ay"] - p["y"], p["ax"] - p["x"])
                if p["bot"] or p.get("ai_aim"):
                    diff = (desired - p["angle"] + math.pi * 3) % (math.pi * 2) - math.pi
                    max_step = 999.0 if p.get("aim_instant") else AI_TURN_RATE * dt
                    if abs(diff) <= max_step:
                        p["angle"] = desired
                    else:
                        p["angle"] += max_step * (1 if diff > 0 else -1)
                else:
                    p["angle"] = desired
                # 换弹完成
                if p["reloading"] and now >= p["reload_end"]:
                    w = WEAPONS[p["weapon"]]
                    need = max(0, w.get("mag", 0) - p["ammo"][p["weapon"]])
                    take = min(need, p["reserve"].get(p["weapon"], 0))
                    p["ammo"][p["weapon"]] += take
                    p["reserve"][p["weapon"]] = p["reserve"].get(p["weapon"], 0) - take
                    p["reloading"] = False
                if p["shoot"]:
                    self._try_shoot(p, now)
                # 弹药补给点：站在补给圈内补满备弹（10s 冷却）
                if p["alive"] and now >= p.get("next_supply", 0.0):
                    for pad in AMMO_PADS:
                        if math.hypot(pad[0] - p["x"], pad[1] - p["y"]) < pad[2] + PLAYER_R:
                            for wn in p["ammo"]:
                                p["ammo"][wn] = WEAPONS[wn].get("mag", 0)
                                p["reserve"][wn] = WEAPON_RESERVE.get(wn, 0)
                            p["next_supply"] = now + 10.0
                            self.events.append({"t": "supply", "pid": p["id"],
                                                "x": p["x"], "y": p["y"]})
                            break
            # 特殊事件开始：取消尚未引爆的核弹倒计时（事件期间禁用核弹）
            if p["nuke_at"] is not None and self.rnd_events.current is not None:
                p["nuke_at"] = None
                self.events.append({"t": "nuke_block", "pid": p["id"],
                                    "why": "特殊事件开始，氟泄漏倒计时已取消"})
            # 核弹倒计时引爆
            if p["nuke_at"] is not None and now >= p["nuke_at"]:
                p["nuke_at"] = None
                self._detonate_nuke(p, now)
            # 出生点隐身：站在己方出生区域内对敌隐身（HVT 除外，持续暴露）
            p["in_spawn"] = bool(p["alive"] and not p["hvt"] and self._in_own_spawn(p))
            self._spawn_zone_pressure(p, now, dt)
            # 脱战代偿：清净酸/碱负荷 + 修复缓冲对（旧版“呼吸回血”的化学实现）
            if p["alive"]:
                p["chem"].tick_regen(now, dt)
                p["hp"] = round(p["chem"].ring, 1)
        # 重生检查放这里统一做
        for p in self.players.values():
            if not p["alive"] and now >= p["respawn_at"]:
                self._respawn(p)

    def _try_shoot(self, p, now):
        w = WEAPONS[p["weapon"]]
        if now - p["last_shot"] < w["rate"] * float(p.get("buffs", {}).get("fire_rate", 1.0)) \
                or p["reloading"]:
            return
        infinite = (p["power"] == "ammo") or bool(p.get("buffs", {}).get("inf_ammo"))
        if not w.get("melee") and not infinite and p["ammo"][p["weapon"]] <= 0:
            self._start_reload(p, now)
            return
        p["last_shot"] = now
        if p["weapon"] == "sniper" and (p["bot"] or p.get("ai_aim")):
            p["ai_snipe_block_until"] = now + AI_SNIPER_BLOCK_AFTER_SHOT
        # 开火暴露 2 秒，但人在烟雾里则保持雷达静默
        if self._in_smoke(p["x"], p["y"]) is None:
            p["expose_until"] = now + 2.0
        # ---- 机枪后坐力：连发越久散布越大，枪口也会被顶偏（松手后缓慢回正）----
        recoil = float(p.get("recoil", 0.0))
        if p["weapon"] in RECOIL_WEAPONS:
            recoil = min(RECOIL_MAX, recoil + RECOIL_PER_SHOT)
            p["recoil"] = recoil
            p["angle"] += random.uniform(-recoil * 0.60, recoil * 0.60)
            self.events.append({"t": "recoil", "pid": p["id"],
                                "v": round(recoil, 4),
                                "ang": round(p["angle"], 4)})
        if w.get("melee"):
            self.events.append({"t": "shoot", "pid": p["id"], "x": p["x"], "y": p["y"], "w": "knife"})
            for q in self.players.values():
                if q is p or not q["alive"] or q["team"] == p["team"]:
                    continue
                d = math.hypot(q["x"] - p["x"], q["y"] - p["y"])
                if d > w["range"] + PLAYER_R:
                    continue
                da = abs((math.atan2(q["y"] - p["y"], q["x"] - p["x"]) - p["angle"] + math.pi * 3) % (math.pi * 2) - math.pi)
                if da < 1.1:
                    self._damage(p["id"], q, w["dmg"], "knife", now)
                    self.apply_aoe(p["id"], q["x"], q["y"], w["dmg"], "knife", now, exclude=q)
        elif w.get("laser"):
            # 机枪：激光弹道，发射瞬间射线结算（省子弹实体运算），每发一跳伤害
            if not infinite:
                p["ammo"][p["weapon"]] -= 1
            spread = w["spread"] + (w["mspread"] if p["moving"] else 0) + recoil
            a = p["angle"] + random.uniform(-spread, spread)
            dx, dy = math.cos(a), math.sin(a)
            x, y = p["x"] + dx * (PLAYER_R + 4), p["y"] + dy * (PLAYER_R + 4)
            sx, sy = x, y
            hit = None
            left = w["brange"]
            step = 14.0
            while left > 0:
                s = min(step, left)
                x += dx * s
                y += dy * s
                left -= s
                if point_in_walls(x, y):
                    break
                for q in self.players.values():
                    if not q["alive"]:
                        continue
                    if q["team"] == p["team"] and channel_of(p["weapon"]) not in ("acid", "base"):
                        continue
                    if (q["x"] - x) ** 2 + (q["y"] - y) ** 2 < (PLAYER_R + 10) ** 2:
                        hit = q
                        break
                if hit is not None:
                    break
            self.events.append({"t": "shoot", "pid": p["id"], "x": p["x"], "y": p["y"], "w": p["weapon"]})
            self.events.append({"t": "laser", "x1": round(sx, 1), "y1": round(sy, 1),
                                "x2": round(x, 1), "y2": round(y, 1), "own": p["id"]})
            if hit is not None:
                self._damage(p["id"], hit, w["dmg"], p["weapon"], now)
                self.apply_aoe(p["id"], hit["x"], hit["y"], w["dmg"], p["weapon"], now, exclude=hit)
        else:
            if not infinite:
                p["ammo"][p["weapon"]] -= 1
            spread = w["spread"] + (w["mspread"] if p["moving"] else 0) + recoil
            n_pel = w.get("pellets", 1)
            for _ in range(n_pel):
                a = p["angle"] + random.uniform(-spread, spread)
                bb = {
                    "x": p["x"] + math.cos(a) * (PLAYER_R + 4),
                    "y": p["y"] + math.sin(a) * (PLAYER_R + 4),
                    "dx": math.cos(a), "dy": math.sin(a),
                    "speed": w["bspeed"], "left": w["brange"],
                    "owner": p["id"], "team": p["team"], "dmg": w["dmg"], "w": p["weapon"],
                }
                for opt in ("aoe", "support", "repair"):
                    if w.get(opt):
                        bb[opt] = w[opt]
                self.bullets.append(bb)
            self.events.append({"t": "shoot", "pid": p["id"], "x": p["x"], "y": p["y"], "w": p["weapon"]})

    def _start_reload(self, p, now):
        w = WEAPONS[p["weapon"]]
        if w.get("melee") or w.get("noreload"):
            return
        if p["reserve"].get(p["weapon"], 0) <= 0:
            return
        if p["reloading"] or p["ammo"][p["weapon"]] >= w.get("mag", 0):
            return
        p["reloading"] = True
        p["reload_end"] = now + w["reload"]

    def _update_bullets(self, now, dt):
        keep = []
        for b in self.bullets:
            dead = False
            step = b["speed"] * dt
            sub = int(step // 12) + 1
            for _ in range(sub):
                s = step / sub
                b["x"] += b["dx"] * s
                b["y"] += b["dy"] * s
                b["left"] -= s
                if b["left"] <= 0 or point_in_walls(b["x"], b["y"]):
                    if b.get("aoe") and not b.get("support"):
                        self._explode_bullet(b, now)
                    dead = True
                    break
                hit_p = None
                for q in self.players.values():
                    if not q["alive"] or q["id"] == b["owner"]:
                        continue
                    bch = channel_of(b["w"])
                    if b.get("support"):
                        if q["team"] != b["team"]:
                            continue
                    elif q["team"] == b["team"]:
                        # 友军只允许酸/碱支援弹命中（用于中和救援），其余友军伤害关闭
                        if bch not in ("acid", "base"):
                            continue
                    if (q["x"] - b["x"]) ** 2 + (q["y"] - b["y"]) ** 2 < (PLAYER_R + 10) ** 2:
                        hit_p = q
                        break
                if hit_p is not None:
                    if b.get("support"):
                        # 维C 支援弹：修复队友缓冲对，不造成伤害
                        mmol = float(b.get("repair", 15))
                        healed = hit_p["chem"].repair_buffer(mmol, now)
                        hit_p["hp"] = round(hit_p["chem"].ring, 1)
                        owner = self.players.get(b["owner"])
                        pts = max(1, int(healed))
                        if owner is not None:
                            self._add_score(owner, pts)
                            owner["st"]["rescue"] += 1
                            self.events.append({"t": "score", "for": owner["id"],
                                                "pts": pts, "reason": "repair"})
                        self.events.append({
                            "t": "restore", "pid": b["owner"], "vid": hit_p["id"],
                            "x": hit_p["x"], "y": hit_p["y"], "mmol": mmol,
                            "heal": round(healed, 1),
                            "ph": (None if hit_p["chem"].ph is None
                                   else round(hit_p["chem"].ph, 2)),
                        })
                    elif b.get("aoe"):
                        self._explode_bullet(b, now)
                    else:
                        self._damage(b["owner"], hit_p, b["dmg"], b["w"], now)
                        self.apply_aoe(b["owner"], hit_p["x"], hit_p["y"],
                                       b["dmg"], b["w"], now, exclude=hit_p)
                    dead = True
                    break
            if not dead:
                keep.append(b)
        self.bullets = keep

    def _explode_bullet(self, b, now):
        """石灰水投掷瓶：命中点小范围碱伤。"""
        radius = float(b.get("aoe", 80))
        self.events.append({"t": "boom", "x": round(b["x"], 1), "y": round(b["y"], 1)})
        owner = self.players.get(b["owner"])
        if owner is None:
            return
        for q in self.players.values():
            if not q["alive"] or q["id"] == b["owner"]:
                continue
            if q["team"] == owner["team"] and channel_of(b["w"]) not in ("acid", "base"):
                continue
            if (q["x"] - b["x"]) ** 2 + (q["y"] - b["y"]) ** 2 < radius * radius:
                self._damage(b["owner"], q, b["dmg"], b["w"], now)

    def apply_aoe(self, owner_id, x, y, dmg, weapon, now, exclude=None):
        """AOE 技能生效中：命中点溅射（对所有武器生效），返回命中人数"""
        ow = self.players.get(owner_id)
        if ow is None or ow["aoe_until"] <= now:
            return 0
        self.events.append({"t": "boom", "x": round(x, 1), "y": round(y, 1)})
        n = 0
        for q in self.players.values():
            if q is exclude or not q["alive"] or q["team"] == ow["team"]:
                continue
            if (q["x"] - x) ** 2 + (q["y"] - y) ** 2 < AOE_RADIUS ** 2:
                self._damage(owner_id, q, int(dmg * AOE_MULT), weapon + "_aoe", now)
                n += 1
        return n

    def _damage(self, owner_id, victim, dmg, weapon, now):
        if victim.get("tut"):
            return          # 新手教程期间免疫伤害（先讲清机制，再进真实对局）
        owner = self.players.get(owner_id)
        if owner is not None and owner["team"] == victim["team"]:
            # 关闭友军伤害；但允许“中和救援”：对酸蚀队友喷碱、对碱蚀队友喷酸
            ch = channel_of(weapon)
            if owner is victim or ch not in ("acid", "base"):
                return
            mmol, healed, ph = victim["chem"].neutralize(dmg, ch, now)
            if mmol <= 0.0:
                return
            victim["hp"] = round(victim["chem"].ring, 1)
            pts = max(1, int(healed))
            self._add_score(owner, pts)
            owner["st"]["rescue"] += 1
            self.events.append({
                "t": "neutralize", "pid": owner["id"], "vid": victim["id"],
                "x": victim["x"], "y": victim["y"], "mmol": round(mmol, 1),
                "heal": round(healed, 1), "ph": round(ph, 2) if ph is not None else None,
            })
            self.events.append({"t": "score", "for": owner_id, "pts": pts, "reason": "neutralize"})
            return
        if owner is not None and owner is not victim:
            dmg = dmg * float(owner.get("buffs", {}).get("dmg", 1.0))
        # ---- Boss 护盾：先扣护盾，护盾破了才进入化学伤害（"加厚血条"）----
        if victim.get("is_boss") and victim.get("boss_shield", 0.0) > 0:
            absorbed = min(victim["boss_shield"], float(dmg))
            victim["boss_shield"] -= absorbed
            if owner is not None and owner is not victim:
                owner["boss_dmg"] = owner.get("boss_dmg", 0.0) + absorbed
                self.boss_contrib[owner["id"]] = self.boss_contrib.get(owner["id"], 0.0) + absorbed
                self._add_score(owner, max(1, int(absorbed * 0.6)))
            self.events.append({"t": "bosshit", "x": victim["x"], "y": victim["y"],
                                "d": round(absorbed, 1), "for": owner_id,
                                "sh": round(victim["boss_shield"], 1)})
            dmg = float(dmg) - absorbed
            if dmg < 1.0:
                return
        # ---- 化学伤害：旧版“伤害点”按通道换算成 pH/缓冲变化 ----
        chem = victim["chem"]
        ring_before = chem.ring
        ch = channel_of(weapon)
        if victim.get("is_boss"):
            # Boss 抗性：缓冲环境不计伤害；只有"中和 / 氧化"真正消耗缓冲对才掉血
            dmg, report = self._boss_chem(victim, dmg, ch, now)
            if dmg <= 0.0 or report is None:
                victim["hp"] = round(chem.ring, 1)
                self.events.append({"t": "bosshit", "x": victim["x"], "y": victim["y"],
                                    "d": 0.0, "for": owner_id, "imm": 1,
                                    "sh": round(victim.get("boss_shield", 0.0), 1)})
                return
            dealt = int(min(dmg, ring_before))
        else:
            report = chem.apply_damage(dmg, ch, now)
            dealt = int(min(dmg, ring_before))   # 积分按“等效旧伤害”，取整
        victim["hp"] = round(chem.ring, 1)   # 兼容旧客户端血条：显示缓冲环
        victim["last_dmg_t"] = now           # 受击打断代偿
        died = not chem.alive
        if owner is not None:
            if owner is not victim:
                victim["dmg_log"][owner["id"]] = victim["dmg_log"].get(owner["id"], 0) + dealt
                owner["st"]["dmg"] += dealt
                if dealt > 0:
                    owner["st"]["hits"] = owner["st"].get("hits", 0) + 1
                if ring_before > 0 and chem.ring <= 0:
                    owner["st"]["break"] += 1
            self._add_score(owner, dealt)          # 伤害即积分（整数）
            # 攻击暴露 3 秒（烟内不暴露）
            if self._in_smoke(owner["x"], owner["y"]) is None:
                owner["expose_until"] = max(owner["expose_until"], now + 3.0)
            self.events.append({"t": "score", "for": owner_id, "pts": dealt, "reason": "hit"})
        # 受击暴露 1.5 秒（烟内不暴露）
        if self._in_smoke(victim["x"], victim["y"]) is None:
            victim["expose_until"] = max(victim["expose_until"], now + 1.5)
        self.events.append({"t": "hit", "for": owner_id, "x": victim["x"], "y": victim["y"],
                            "d": dmg, "kill": died, "ch": report["channel"],
                            "ph": report["after"]["ph"], "buf": report["after"]["ring"]})
        self.events.append({"t": "dmg", "for": victim["id"], "d": dmg, "ch": report["channel"],
                            "ax": (owner["x"] if owner is not None else None),
                            "ay": (owner["y"] if owner is not None else None),
                            "ph": report["after"]["ph"], "buf": report["after"]["ring"]})
        if died:
            was_hvt = victim["hvt"]
            victim["hp"] = 0
            victim["alive"] = False
            victim["deaths"] += 1
            victim["streak"] = 0
            victim["hvt"] = False
            victim["respawn_at"] = now + RESPAWN_TIME
            if victim.get("is_boss"):
                self._boss_defeated(now, owner)
            if owner is not None:
                owner["kills"] += 1
                owner["streak"] += 1
                bonus = KILL_SCORE + (owner["streak"] - 1) * STREAK_BONUS
                if channel_of(weapon) == "physical":
                    bonus += PHYSICAL_KILL_BONUS   # 物理击杀额外给分
                if weapon == "nuke":
                    bonus *= NUKE_KILL_MULT   # 核弹人头额外加成
                if was_hvt:
                    # 击杀高价值目标额外奖励，随其击杀人数递增（每多杀一人 +连杀奖）
                    bonus += HVT_BONUS + max(0, victim["kills"] - HVT_STREAK) * STREAK_BONUS
                self._add_score(owner, bonus)
                # 连杀达标晋升高价值目标：全局播报 + 持续暴露位置
                if owner["streak"] >= HVT_STREAK and not owner["hvt"]:
                    owner["hvt"] = True
                    self.feed_seq += 1
                    self.feed.append({
                        "id": self.feed_seq, "k": "☠", "kt": owner["team"],
                        "v": owner["name"] + " 成为高价值目标！", "vt": owner["team"], "w": "hvt",
                        "expire": now + 5.0,
                    })
                    self.events.append({"t": "hvt", "pid": owner["id"],
                                        "name": owner["name"], "team": owner["team"]})
                    print("  [高价值] %s 连杀 %d，成为高价值目标" % (owner["name"], owner["streak"]))
                if owner["team"] in self.scores and not victim.get("is_boss"):
                    self.scores[owner["team"]] += 1
                self.feed_seq += 1
                self.feed.append({
                    "id": self.feed_seq, "k": owner["name"], "kt": owner["team"],
                    "v": victim["name"], "vt": victim["team"], "w": weapon,
                    "cause": report["cause"], "ph": report["after"]["ph"],
                    "expire": now + 5.0,
                })
                self.events.append({
                    "t": "kill", "kid": owner["id"], "kname": owner["name"], "kt": owner["team"],
                    "vid": victim["id"], "vname": victim["name"], "vt": victim["team"], "w": weapon,
                    "pts": bonus, "hv": was_hvt, "cause": report["cause"],
                    "ph": report["after"]["ph"],
                })
                self.events.append({"t": "score", "for": owner_id, "pts": bonus,
                                    "reason": "kill", "n": owner["streak"]})
                if owner["streak"] >= 2:
                    self.events.append({"t": "streak", "for": owner["id"], "n": owner["streak"]})
            # 助攻结算：本条命对死者造成过 >= ASSIST_MIN_DMG 伤害的其他玩家
            for aid, dmg_dealt in victim["dmg_log"].items():
                if owner is not None and aid == owner_id:
                    continue
                helper = self.players.get(aid)
                if helper is None or helper["team"] == victim["team"]:
                    continue
                if dmg_dealt >= ASSIST_MIN_DMG:
                    self._add_score(helper, ASSIST_SCORE)
                    self.events.append({"t": "score", "for": aid, "pts": ASSIST_SCORE,
                                        "reason": "assist"})
            victim["dmg_log"] = {}

    # ---- Boss 化学抗性（"缓冲环境"不计伤害，只有中和/氧化有效）----
    @staticmethod
    def _boss_ph_clamp(chem):
        """酸碱同向只推动 pH（缓冲环境），钳制在安全区，禁止用 pH 中毒绕过缓冲条。"""
        for _ in range(8):
            ph = chem.ph
            if ph <= 4.2 and chem.acid > 0.0:
                chem.acid *= 0.55
            elif ph >= 9.8 and chem.base > 0.0:
                chem.base *= 0.55
            else:
                break
        chem._invalidate()
        chem._refresh()

    def _boss_chem(self, victim, dmg, ch, now):
        """对 Boss 的伤害结算：返回 (有效旧伤害点, 化学报告)。

        规则（与用户需求一致）：
        - 同向酸碱（缓冲环境）只改 pH，不掉缓冲条 → 返回 (0, None)，不计分；
        - 反向酸碱（中和反应）按实际被中和掉的缓冲对折算伤害；
        - 氧化/物理按正常化学伤害结算（直接剥离缓冲对）。
        """
        chem = victim["chem"]
        cfg = CHEM.cfg
        # 血量更厚：护盾破掉后，缓冲条承受的伤害按 hp_mul 缩水（默认 4.5 倍等效血量）
        hp_mul = max(1.0, float((victim.get("boss_cfg") or {}).get("hp_mul", 1.0)))
        dmg = float(dmg) / hp_mul
        if ch in ("acid", "base"):
            net = chem.net_acid
            same = (ch == "acid" and net <= 1e-9) or (ch == "base" and net >= -1e-9)
            if same:
                drift = dmg * cfg.ACID_MMOL_PER_DMG * 0.30 / 1000.0
                if ch == "acid":
                    chem.acid += drift
                else:
                    chem.base += drift
                chem.last_damage_t = now
                chem._invalidate()
                chem._refresh()
                self._boss_ph_clamp(chem)
                return 0.0, None
        before = chem.destroyed
        report = chem.apply_damage(dmg, ch, now)
        if ch in ("acid", "base"):
            neutralized = max(0.0, chem.destroyed - before)
            eff = neutralized * 1000.0 / cfg.ACID_MMOL_PER_DMG
            self._boss_ph_clamp(chem)
            return eff, report
        return float(dmg), report

    def _detonate_nuke(self, p, now):
        """核弹引爆：对敌方全体造成巨额伤害，核弹击杀积分翻倍"""
        self.events.append({"t": "nuke_boom", "pid": p["id"], "team": p["team"],
                            "x": p["x"], "y": p["y"]})
        self.feed_seq += 1
        self.feed.append({
            "id": self.feed_seq, "k": "F₂ " + p["name"], "kt": p["team"],
            "v": "引发氟泄漏！", "vt": p["team"], "w": "nuke",
            "expire": now + 5.0,
        })
        for q in list(self.players.values()):
            if q is p or not q["alive"] or q["team"] == p["team"]:
                continue
            self._damage(p["id"], q, NUKE_DMG, "nuke", now)
        print("  [氟泄漏] %s 触发了氟泄漏！" % p["name"])

    # ---- 机器人 ----
    def _ai_bot_think(self, p, now):
        """训练好的策略接管机器人：0.1s 决策 + 人类化转向/反应/误差，避免 0 帧拉枪。"""
        if p.get("dummy"):
            return
        if now < p["next_think"]:
            return
        p["next_think"] = now + 0.1
        p["ai_aim"] = True
        p["ai_sniper_frac"] = AI_SNIPER_FRAC_BY_DIFF.get(self.bot_diff, AI_SNIPER_MAX_FRAC)
        obs = self._ai_obs(p)
        try:
            acts = AI_POLICY.act(obs)
        except Exception:
            return
        move, aim, fire, reload_a, switch, indicator = acts[:6]
        dxdy = [None, (0, -1), (1, -1), (1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1)]
        if 0 < move < len(dxdy):
            dx, dy = dxdy[move]
            p["in"] = [int(dy < 0), int(dx > 0), int(dy > 0), int(dx < 0)]
        else:
            p["in"] = [0, 0, 0, 0]
        if reload_a:
            self._start_reload(p, now)
        if 0 <= switch < len(AI_WEAPON_IDS) and AI_WEAPON_IDS[switch] != p["weapon"]:
            if AI_WEAPON_IDS[switch] == "sniper" and not ai_sniper_available(p, now):
                pass                         # 配额不足，禁止切狙击
            else:
                p["weapon"] = AI_WEAPON_IDS[switch]
                p["reloading"] = False
        if indicator:
            self.use_indicator(p, now)

        vis_e, vis_a = [], []
        for q in self.players.values():
            if q["id"] == p["id"] or not q["alive"] or q.get("tut"):
                continue
            if not self._can_see(p["x"], p["y"], q["x"], q["y"]):
                continue
            d = math.hypot(q["x"] - p["x"], q["y"] - p["y"])
            (vis_e if q["team"] != p["team"] else vis_a).append((d, q))
        vis_e.sort(key=lambda x: x[0])
        vis_a.sort(key=lambda x: x[0])
        target = None
        if 0 <= aim < 3 and aim < len(vis_e):
            target = vis_e[aim][1]
        elif 3 <= aim < 5 and (aim - 3) < len(vis_a):
            target = vis_a[aim - 3][1]
        # ---- 巡逻/搜索兜底（解决"没人靠近就站着不动"）----
        # 训练策略在"看不见目标"时会输出原地待机；远离战斗的机器人因此一直卡住。
        if target is None:
            if not p.get("no_target_since"):
                p["no_target_since"] = now
            if now - p["no_target_since"] >= AI_PATROL_AFTER:
                self._ai_patrol(p, now)
        else:
            p["no_target_since"] = 0.0

        # 反应延迟：目标切换时先“看到”，延迟 0.18–0.34s 才把准星移过去
        tid = target["id"] if target is not None else None
        if tid != p.get("ai_target_id"):
            p["ai_target_id"] = tid
            p["ai_react_until"] = now + random.uniform(AI_REACTION_MIN, AI_REACTION_MAX)
        if now < p.get("ai_react_until", 0.0):
            p["shoot"] = False
            return
        if target is not None:
            err_mult = DIFFICULTY.get(self.bot_diff, DIFFICULTY["normal"])["err_mult"]
            d = math.hypot(target["x"] - p["x"], target["y"] - p["y"])
            err = AI_AIM_ERR * err_mult * max(80.0, d)
            p["ax"] = target["x"] + random.uniform(-err, err)
            p["ay"] = target["y"] + random.uniform(-err, err)
        p["shoot"] = bool(fire) and target is not None

    def _ai_patrol(self, p, now):
        """没有可打目标时的巡逻/搜索：朝最近敌人方向游走或随机游走，别再站着不动。

        只在"看不见任何敌人"超过 AI_PATROL_AFTER 秒时由 _ai_bot_think 调用；
        一旦策略重新看到目标，移动权立刻交回策略（对局强度不变，只是补了"找人"）。
        """
        wp = p.get("wp")
        need_new = (wp is None or now >= p.get("wp_time", 0.0)
                    or math.hypot(wp[0] - p["x"], wp[1] - p["y"]) < 90)
        if need_new:
            best, best_d = None, 1e18
            for q in self.players.values():
                if (q["id"] == p["id"] or q["team"] == p["team"]
                        or not q["alive"] or q.get("tut")):
                    continue
                dd = (q["x"] - p["x"]) ** 2 + (q["y"] - p["y"]) ** 2
                if dd < best_d:
                    best, best_d = q, dd
            chosen = None
            for _ in range(28):
                if best is not None and random.random() < 0.6:
                    ang = math.atan2(best["y"] - p["y"], best["x"] - p["x"]) \
                        + random.uniform(-0.8, 0.8)
                    dist = random.uniform(420, 1100)
                    wx = min(max(p["x"] + math.cos(ang) * dist, 80), WORLD_W - 80)
                    wy = min(max(p["y"] + math.sin(ang) * dist, 80), WORLD_H - 80)
                else:
                    wx = random.uniform(80, WORLD_W - 80)
                    wy = random.uniform(80, WORLD_H - 80)
                if circle_hits_walls(wx, wy):
                    continue
                if not self._can_see(p["x"], p["y"], wx, wy):
                    continue        # 直线被墙挡住的点不去，避免一直撞墙
                chosen = (wx, wy)
                break
            if chosen is None:      # 极端情况（四面被墙）：退化为随机朝向
                chosen = (random.uniform(80, WORLD_W - 80),
                          random.uniform(80, WORLD_H - 80))
            p["wp"] = chosen
            p["wp_time"] = now + random.uniform(2.5, 5.0)
            p["turn"] = random.choice((-1, 1))
        wx, wy = p["wp"]
        dx, dy = wx - p["x"], wy - p["y"]
        d = math.hypot(dx, dy)
        if d < 60:
            p["in"] = [0, 0, 0, 0]
            return
        dx, dy = dx / d, dy / d
        if circle_hits_walls(p["x"] + dx * 36, p["y"] + dy * 36):
            ang = math.atan2(dy, dx) + 0.9 * p.get("turn", 1)
            dx, dy = math.cos(ang), math.sin(ang)
        p["in"] = [int(dy < -0.3), int(dx > 0.3), int(dy > 0.3), int(dx < -0.3)]
        p["ax"] = p["x"] + dx * 200
        p["ay"] = p["y"] + dy * 200

    def _ai_obs(self, p):
        """与 game/env.py 的 encode_obs 完全一致（41 维）。"""
        w = WEAPONS.get(p["weapon"], {})
        mag = w.get("mag", 0) or 1
        ph = p["chem"].ph
        vec = [p["x"] / WORLD_W, p["y"] / WORLD_H,
               math.sin(p["angle"]), math.cos(p["angle"]),
               1.0 if p["alive"] else 0.0, p["hp"] / 100.0,
               p["chem"].ring / 100.0, ((ph or 7.0) - 7.0) / 4.0,
               p["chem"].l_mmol / 100.0, p["chem"].d_mmol / 100.0,
               1.0 if p["reloading"] else 0.0,
               p["ammo"][p["weapon"]] / float(mag),
               p["ind_charges"] / 2.0,
               p["reserve"].get(p["weapon"], 0) / float(max(1, WEAPON_RESERVE.get(p["weapon"], 1)))]
        onehot = [0.0] * 8
        if p["weapon"] in AI_WEAPON_IDS:
            onehot[AI_WEAPON_IDS.index(p["weapon"])] = 1.0
        vec += onehot
        enemies = []
        for q in self.players.values():
            if q["id"] == p["id"] or q["team"] == p["team"] or not q["alive"] or q.get("tut"):
                continue
            d = math.hypot(q["x"] - p["x"], q["y"] - p["y"])
            vis = 1.0 if self._can_see(p["x"], p["y"], q["x"], q["y"]) else 0.0
            enemies.append((d, q, vis))
        enemies.sort(key=lambda x: x[0])
        for i in range(3):
            if i < len(enemies):
                d, q, vis = enemies[i]
                vec += [(q["x"] - p["x"]) / 2000.0, (q["y"] - p["y"]) / 2000.0,
                        d / 2000.0, (q["chem"].ph or 7.0) / 14.0,
                        q["chem"].ring / 100.0, vis]
            else:
                vec += [0.0] * 6
        # 队友（最多 2）：AI 需要看到队友 pH/环才能救
        allies = []
        for q in self.players.values():
            if q["id"] == p["id"] or q["team"] != p["team"] or not q["alive"]:
                continue
            d = math.hypot(q["x"] - p["x"], q["y"] - p["y"])
            vis = 1.0 if self._can_see(p["x"], p["y"], q["x"], q["y"]) else 0.0
            allies.append((d, q, vis))
        allies.sort(key=lambda x: x[0])
        for i in range(2):
            if i < len(allies):
                d, q, vis = allies[i]
                vec += [(q["x"] - p["x"]) / 2000.0, (q["y"] - p["y"]) / 2000.0,
                        d / 2000.0, (q["chem"].ph or 7.0) / 14.0,
                        q["chem"].ring / 100.0, vis]
            else:
                vec += [0.0] * 6
        if AMMO_PADS:
            pad = min(AMMO_PADS, key=lambda a: (a[0] - p["x"]) ** 2 + (a[1] - p["y"]) ** 2)
            d = math.hypot(pad[0] - p["x"], pad[1] - p["y"])
            vec += [(pad[0] - p["x"]) / 2000.0, (pad[1] - p["y"]) / 2000.0, d / 2000.0]
        else:
            vec += [0.0] * 3
        vec.append(0.5)          # 线上剩余时间用常数占位
        return vec


    # ================= 随机事件 / Boss 战支持接口（game/events.py 调用） =================
    def announce(self, title, desc=""):
        """广播一条全屏播报（客户端画横幅）。"""
        self.events.append({"t": "announce", "title": title, "desc": desc})

    def feed_line(self, k, kt, v, vt, w, now, ttl=4.0):
        """给击杀播报栏补一条（事件模块用，避免直接操作内部序号）。"""
        self.feed_seq += 1
        self.feed.append({"id": self.feed_seq, "k": k, "kt": kt,
                          "v": v, "vt": vt, "w": w, "expire": now + ttl})

    def set_all_weapons(self, w):
        """事件：强制所有人使用某武器（None = 恢复自由选择）。"""
        with self.lock:
            for p in self.players.values():
                if p.get("dummy") or p.get("is_boss"):
                    continue
                p["forced_w"] = w
                if w:
                    p["weapon"] = w
                    p["reloading"] = False

    def set_all_buff(self, key, val):
        with self.lock:
            for p in self.players.values():
                if p.get("dummy") or p.get("is_boss"):
                    continue
                p.setdefault("buffs", {})[key] = val

    def clear_buffs(self):
        with self.lock:
            for p in self.players.values():
                p["buffs"] = {}
                p["forced_w"] = None
                if p.get("is_boss"):
                    p["buffs"] = {"dmg": p.get("boss_cfg", {}).get("dmg_mul", 1.6),
                                  "fire_rate": p.get("boss_cfg", {}).get("fire_rate_mul", 0.75)}

    def force_event(self, token=None):
        """主机手动触发：token=事件 id / "random" / "off" / "on"。"""
        now = time.time()
        if token == "off":
            self.rnd_events.enabled = False
            self.rnd_events.force_end(self, now, "关闭事件系统")
            return "events_off"
        if token == "on":
            self.rnd_events.enabled = True
            return "events_on"
        self.rnd_events.force_end(self, now, "切换事件")
        if token in (None, "random", ""):
            self.rnd_events.force_start(self, now)
        else:
            self.rnd_events.force_start(self, now, token)
        return "event:" + str(token)

    # ---- Boss（超级 AI）----
    def spawn_boss(self, cfg):
        with self.lock:
            if self.boss_pid is not None and self.boss_pid in self.players:
                return self.players[self.boss_pid]
            p = self.add_player(cfg.get("name", "BOSS"), "boss")
            p["bot"] = True
            p["is_boss"] = True
            p["aim_instant"] = True          # 0 帧拉枪
            p["boss_cfg"] = dict(cfg)
            p["boss_shield"] = float(cfg.get("shield", 1400.0))
            p["buffs"] = {"dmg": float(cfg.get("dmg_mul", 1.6)),
                          "fire_rate": float(cfg.get("fire_rate_mul", 0.75)),
                          "speed": 1.0}
            p["boss_cd"] = {"smoke": time.time() + 3.0, "aoe": time.time() + 5.0,
                            "indicator": time.time() + 6.0}
            p["weapon"] = "lmg"
            # 出生在地图中心附近的空地上（不能卡在墙里，否则 Boss 会原地不动）
            sx, sy = WORLD_W / 2.0, WORLD_H / 2.0
            best_xy, best_d = (sx, sy), 1e18
            for gi in range(40):
                gx = sx + (gi % 8 - 3.5) * 90
                gy = sy + ((gi // 8) - 2) * 90
                if gx < 80 or gy < 80 or gx > WORLD_W - 80 or gy > WORLD_H - 80:
                    continue
                if circle_hits_walls(gx, gy):
                    continue
                dd = (gx - sx) ** 2 + (gy - sy) ** 2
                if dd < best_d:
                    best_xy, best_d = (gx, gy), dd
            p["x"], p["y"] = best_xy
            p["in_spawn"] = False
            p["hp"] = round(p["chem"].ring, 1)
            p["next_think"] = time.time()
            # Boss 战期间：所有人（玩家 + 机器人）统一到同一阵营，共同对抗 Boss
            # 结束后在 despawn_boss 里按备份恢复原阵营
            self._boss_team_backup = {}
            for q in self.players.values():
                if q.get("is_boss"):
                    continue
                self._boss_team_backup[q["id"]] = q["team"]
                q["team"] = "red"
            self.boss_pid = p["id"]
            self.boss_contrib = {}
            self.boss_result = None
            self.announce("Boss 战 · " + p["name"],
                          "超级 AI：厚血/护盾 + 0 帧拉枪 + 道具连发，全员合力击杀")
            print("  [Boss] %s 登场（护盾 %.0f）" % (p["name"], p["boss_shield"]))
            return p

    def despawn_boss(self):
        with self.lock:
            pid = self.boss_pid
            self.boss_pid = None
            if pid is not None:
                self.players.pop(pid, None)
                print("  [Boss] 已离场")
            # 恢复 Boss 战前的阵营（玩家/机器人回到红蓝两队）
            bak = getattr(self, "_boss_team_backup", None) or {}
            restored = 0
            for qid, team in bak.items():
                q = self.players.get(qid)
                if q is not None and not q.get("is_boss"):
                    q["team"] = team
                    if not q["alive"]:
                        self._respawn(q)          # 换阵营后从自家出生点复活
                    restored += 1
            self._boss_team_backup = {}
            if restored:
                print("  [Boss] 已恢复 %d 名玩家的原阵营" % restored)

    def boss_tick(self, now, dt):
        """事件系统每帧回调（Boss 的能力节奏在 _boss_think 里，这里留接口）。"""
        pass

    def _boss_defeated(self, now, owner):
        """Boss 被击杀：按伤害贡献发奖励 + 记录贡献榜。"""
        p = self.players.get(self.boss_pid) if self.boss_pid else None
        name = p["name"] if p else "BOSS"
        total = sum(self.boss_contrib.values()) or 1.0
        rows = []
        for pid, dmg in self.boss_contrib.items():
            q = self.players.get(pid)
            if q is None:
                continue
            reward = int(dmg / 8.0)
            self._add_score(q, reward)
            rows.append({"id": pid, "n": q["name"], "team": q["team"],
                         "dmg": int(dmg), "reward": reward})
        rows.sort(key=lambda r: -r["dmg"])
        self.boss_result = {"boss": name, "by": (owner["name"] if owner else "?"),
                            "total": int(total), "top": rows[:5],
                            "at": round(now, 1)}
        top_txt = "、".join("%s(%d)" % (r["n"], r["reward"]) for r in rows[:3]) or "无人"
        self.announce("Boss 被击败！", "伤害贡献奖励：%s" % top_txt)
        self.events.append({"t": "boss_dead", "by": (owner["name"] if owner else "?"),
                            "top": rows[:5]})
        self.rnd_events.force_end(self, now, "Boss 被击败")
        self.despawn_boss()

    def boss_top_contrib(self, n=3):
        """Boss 战中贡献最高的前 n 名（客户端画贡献榜用）。"""
        rows = []
        for pid, dmg in self.boss_contrib.items():
            q = self.players.get(pid)
            if q is None:
                continue
            rows.append({"n": q["name"], "dmg": int(dmg)})
        rows.sort(key=lambda r: -r["dmg"])
        return rows[:n]

    def _boss_think(self, p, now):
        """超级 AI：0.03s 索敌 + 0 帧拉枪 + 预判提前量 + 按目标 pH 换试剂 + 道具压制。

        设计意图（对应需求“更逆天/血量更厚/索敌更快”）：
        - 索敌：每 0.03s 重算一次，可见目标权重远高于距离，优先打看得见的人；
        - 瞄准：按目标速度做弹道提前量（不是贴当前位置），且无转向速度上限；
        - 换枪：目标偏酸就用碱（中和掉对方缓冲），偏碱就用酸，中性/厚缓冲用氧化喷淋；
        - 道具：烟雾切视线、AOE 逼走位、指示剂削缓冲，冷却比旧版更短。
        """
        p["next_think"] = now + 0.03
        p["ai_aim"] = True
        cfg = p.get("boss_cfg") or {}
        # 先在所有"看得见"的人里挑（距离 × 高价值/残血权重），一个都看不见才退而求其次
        vis_pool, blind_pool = [], []
        for q in self.players.values():
            if q is p or q.get("is_boss") or not q["alive"] or q.get("dummy"):
                continue
            if q.get("in_spawn") or q.get("power") == "ghost":
                continue
            d = math.hypot(q["x"] - p["x"], q["y"] - p["y"])
            score = d
            if q.get("hvt"):
                score *= 0.72
            score *= 0.75 + 0.25 * min(1.0, q["hp"] / 100.0)
            if self._can_see(p["x"], p["y"], q["x"], q["y"]):
                vis_pool.append((score, d, q))
            else:
                blind_pool.append((d, q))
        best, best_vis, best_d = None, False, 1e18
        if vis_pool:
            vis_pool.sort(key=lambda t: (t[0], t[1]))
            best, best_d = vis_pool[0][2], vis_pool[0][1]
            best_vis = True
        elif blind_pool:
            blind_pool.sort(key=lambda t: t[0])
            best, best_d = blind_pool[0][1], blind_pool[0][0]
        if best is None:
            p["shoot"] = False
            p["in"] = [0, 0, 0, 0]
            return
        # ---- 预判提前量：按目标最近两帧位置估速度，按当前枪口初速算飞行时间 ----
        trk = p.setdefault("boss_trk", {})
        old = trk.get(best["id"])
        vx = vy = 0.0
        if old is not None:
            dtm = max(1e-3, now - old[2])
            if dtm < 0.6:
                vx = (best["x"] - old[0]) / dtm
                vy = (best["y"] - old[1]) / dtm
                vx = clamp(vx, -PLAYER_SPEED * 1.35, PLAYER_SPEED * 1.35)
                vy = clamp(vy, -PLAYER_SPEED * 1.35, PLAYER_SPEED * 1.35)
        trk[best["id"]] = (best["x"], best["y"], now)
        # ---- 按目标化学状态换试剂（中和/氧化才是对 Boss 有效的打法，AI 也照着打）----
        net = best["chem"].net_acid if hasattr(best.get("chem"), "net_acid") else 0.0
        if best_d > 900.0:
            wname = "asniper" if net > 0.012 else ("rifle" if net < -0.012 else "sniper")
        elif best_d > 420.0:
            wname = "asniper" if net > 0.008 else ("rifle" if net < -0.008 else "lmg")
        else:
            wname = "shotgun" if abs(net) <= 0.012 else ("lmg" if net > 0 else "rifle")
        if wname in WEAPONS:
            p["weapon"] = wname
        wcfg = WEAPONS.get(p["weapon"]) or {}
        tof = min(0.45, best_d / max(600.0, float(wcfg.get("bspeed", 1600.0))))
        aim_x = best["x"] + vx * tof * 0.92
        aim_y = best["y"] + vy * tof * 0.92
        ax, ay = aim_x, aim_y
        # 目标在高速横移时，稍微“锁前面一点”，近距离则直接锁身体
        if best_d < 160.0:
            ax, ay = best["x"], best["y"]
        p["ax"], p["ay"] = ax, ay
        p["shoot"] = bool(best_vis)
        # ---- 走位：远压近拉，中距横绕，卡墙时换切线方向 ----
        dx, dy = best["x"] - p["x"], best["y"] - p["y"]
        dist = math.hypot(dx, dy) or 1.0
        dx, dy = dx / dist, dy / dist
        if dist > 520.0:
            ix, iy = dx, dy
        elif dist < 200.0:
            ix, iy = -dx, -dy
        else:
            ix, iy = -dy, dx
            if p.get("turn", 1) < 0:
                ix, iy = -ix, -iy
        if random.random() < 0.02:
            p["turn"] = random.choice((-1, 1))
        if circle_hits_walls(p["x"] + ix * 42, p["y"] + iy * 42):
            for cand in ((-dy, dx), (dy, -dx), (-dx, -dy), (dx, dy)):
                if not circle_hits_walls(p["x"] + cand[0] * 42, p["y"] + cand[1] * 42):
                    ix, iy = cand
                    break
        p["in"] = [int(iy < -0.3), int(ix > 0.3), int(iy > 0.3), int(ix < -0.3)]
        # ---- 道具压制（冷却更短，且按距离挑选使用时机）----
        ab = (cfg.get("abilities") or {})
        cd = p.setdefault("boss_cd", {})
        if now >= cd.get("smoke", 0.0) and best_d > 340.0:
            cd["smoke"] = now + float(ab.get("smoke", 6.5))
            self.throw_smoke(p, best["x"], best["y"], now)
        if now >= cd.get("aoe", 0.0) and best_d < 560.0:
            cd["aoe"] = now + float(ab.get("aoe", 11.0))
            p["skill_cd"] = 0.0
            p["aoe_until"] = now + SKILL_DUR
        if now >= cd.get("indicator", 0.0) and best_vis and best_d < 1050.0:
            cd["indicator"] = now + float(ab.get("indicator", 8.0))
            self.use_indicator(p, now)
        # ---- 备弹无限感：没子弹就瞬间补满（Boss 不掉节奏）----
        if p["ammo"].get(p["weapon"], 0) <= 0:
            p["ammo"][p["weapon"]] = WEAPONS[p["weapon"]].get("mag", 0)
            p["reloading"] = False

    def _bot_think(self, p, now):
        if p.get("dummy"):
            return              # 新手教程的静止靶子：不思考、不移动、不开枪
        if p.get("is_boss"):
            self._boss_think(p, now)
            return
        if AI_POLICY is not None:
            self._ai_bot_think(p, now)
            return
        cfg = DIFFICULTY.get(self.bot_diff, DIFFICULTY["normal"])
        if now > p["next_think"]:
            # ===== 决策层：每 0.4~1.1 秒随机一次，避免每帧抖动 =====
            p["next_think"] = now + random.uniform(0.4, 1.1)
            best, best_d = None, 1e9
            for q in self.players.values():
                if not q["alive"] or q["team"] == p["team"]:
                    continue
                if q["power"] == "ghost" or q["in_spawn"] or q.get("tut"):
                    continue   # 隐身 / 出生点隐身的玩家 AI 看不见
                d = math.hypot(q["x"] - p["x"], q["y"] - p["y"])
                if q["hvt"]:
                    d *= 0.55               # 高价值目标：AI 更优先追击
                if d < cfg["detect"] and d < best_d and self._can_see(p["x"], p["y"], q["x"], q["y"]):
                    best, best_d = q, d
            p["target"] = best["id"] if best else None
            p["fire"] = best is not None and random.random() < cfg["fire_prob"]
            if best is not None:
                # 瞄准误差只在决策时抽签一次，期间保持稳定（修复枪口抖动）
                err = min(60.0, best_d * 0.08) * cfg["err_mult"] * random.random()
                ea = random.uniform(0, math.pi * 2)
                p["aim_off"] = [math.cos(ea) * err, math.sin(ea) * err]
            # 机器人按距离换枪（酸/碱/氧化/物理多通道；后续 AI 训练会替换成策略输出）
            self._bot_pick_weapon(p, now, best)
            # 路径点
            wp_far = p["wp"] is None or math.hypot(p["wp"][0] - p["x"], p["wp"][1] - p["y"]) < 50
            if p["wp"] is None or (best is None and (now > p["wp_time"] or wp_far)):
                for _ in range(20):
                    wx, wy = random.uniform(80, WORLD_W - 80), random.uniform(80, WORLD_H - 80)
                    if not circle_hits_walls(wx, wy):
                        p["wp"] = (wx, wy)
                        break
                p["wp_time"] = now + random.uniform(3, 7)
            # 撞墙绕行方向：决策时抽签，期间保持一致（修复原地抖动）
            if p["wp"]:
                dxx, dyy = p["wp"][0] - p["x"], p["wp"][1] - p["y"]
                dl = math.hypot(dxx, dyy)
                if dl > 1 and circle_hits_walls(p["x"] + dxx / dl * 40, p["y"] + dyy / dl * 40):
                    p["turn"] = random.choice((-1, 1))
        # ===== 执行层：每帧稳定执行决策结果 =====
        tgt = self.players.get(p["target"]) if p["target"] else None
        if tgt and tgt["alive"]:
            p["ax"] = tgt["x"] + p["aim_off"][0]
            p["ay"] = tgt["y"] + p["aim_off"][1]
            p["shoot"] = p["fire"]
        else:
            if p["wp"]:
                p["ax"], p["ay"] = p["wp"]
            p["shoot"] = False
        # 移动：朝路径点，撞墙按固定偏转绕行
        if p["wp"]:
            dx, dy = p["wp"][0] - p["x"], p["wp"][1] - p["y"]
            d = math.hypot(dx, dy)
            if d > 40:
                dx, dy = dx / d, dy / d
                if circle_hits_walls(p["x"] + dx * 40, p["y"] + dy * 40):
                    ang = math.atan2(dy, dx) + 0.9 * p["turn"]
                    dx, dy = math.cos(ang), math.sin(ang)
                p["in"] = [int(dy < -0.3), int(dx > 0.3), int(dy > 0.3), int(dx < -0.3)]
            else:
                p["in"] = [0, 0, 0, 0]
        else:
            p["in"] = [0, 0, 0, 0]

    def use_indicator(self, p, now):
        """指示剂侦察（F）：标记准星前方、有视线的最近敌人 3 秒，显示其 pH/缓冲。"""
        if not p["alive"] or p["ind_charges"] <= 0 or now < p["ind_cd"]:
            self.events.append({"t": "indicator_fail", "pid": p["id"],
                                "msg": "指示剂已用完或冷却中"})
            return False
        best, best_d = None, 1e9
        for q in self.players.values():
            if not q["alive"] or q["team"] == p["team"]:
                continue
            if q["power"] == "ghost" or q["in_spawn"]:
                continue
            d = math.hypot(q["x"] - p["x"], q["y"] - p["y"])
            if d > 900.0:
                continue
            da = abs((math.atan2(q["y"] - p["y"], q["x"] - p["x"]) - p["angle"] + math.pi * 3)
                     % (math.pi * 2) - math.pi)
            if da > 0.65:                 # 准星前方约 ±37°
                continue
            if not self._can_see(p["x"], p["y"], q["x"], q["y"]):
                continue
            if d < best_d:
                best, best_d = q, d
        if best is None:
            self.events.append({"t": "indicator_fail", "pid": p["id"],
                                "msg": "准星前方没有可标记的敌人"})
            return False
        best["ind_until"] = now + 3.0
        p["ind_charges"] -= 1
        p["ind_cd"] = now + 15.0
        self.events.append({
            "t": "indicator", "pid": p["id"], "vid": best["id"], "name": best["name"],
            "x": best["x"], "y": best["y"],
            "ph": (None if best["chem"].ph is None else round(best["chem"].ph, 2)),
            "buf": round(best["chem"].ring, 1),
        })
        return True

    def _bot_pick_weapon(self, p, now, target):
        """按距离选枪，让机器人用上酸/碱/氧化/物理多条化学通道。

        这是“占位策略”，后续用训练好的策略网络替换；先保证对局里有化学多样性。
        """
        if now < p["next_weapon"]:
            return
        p["next_weapon"] = now + random.uniform(2.5, 5.0)
        if target is not None:
            d = math.hypot(target["x"] - p["x"], target["y"] - p["y"])
        else:
            d = 9999.0
        r = random.random()
        if d < 120:
            w = "knife" if r < 0.40 else ("shotgun" if r < 0.72 else "pistol")
        elif d < 600:
            w = ("rifle" if r < 0.45 else ("shotgun" if r < 0.68
                 else ("pistol" if r < 0.84 else "lmg")))
        elif d < 1400:
            w = ("rifle" if r < 0.40 else ("sniper" if r < 0.64
                 else ("lmg" if r < 0.85 else "pistol")))
        else:
            w = "sniper" if r < 0.50 else ("rifle" if r < 0.82 else "lmg")
        if w != p["weapon"] and w in WEAPONS:
            p["weapon"] = w
            p["reloading"] = False
            p["last_shot"] = 0.0

    # ---- 只读状态（/status.json，给真机验收/自动化测试用）----
    def status(self):
        with self.lock:
            ps = []
            for p in self.players.values():
                ps.append({
                    "id": p["id"], "n": p["name"], "team": p["team"], "bot": p["bot"],
                    "x": round(p["x"], 1), "y": round(p["y"], 1),
                    "alive": p["alive"], "w": p["weapon"],
                    "ammo": p["ammo"][p["weapon"]],
                    "res": p["reserve"].get(p["weapon"], 0),
                    "ph": (None if p["chem"].ph is None else round(p["chem"].ph, 2)),
                    "buf": round(p["chem"].ring, 1),
                    "k": p["kills"], "d": p["deaths"], "s": p["score"],
                    "st": dict(p.get("st", {})),
                    "sniper_used": round(p.get("ai_snip_used", 0.0), 2),
                    "tut": 1 if p.get("tut") else 0,
                    "bos": 1 if p.get("is_boss") else 0,
                    "bsh": round(p.get("boss_shield", 0.0), 1),
                    "bmx": round(float((p.get("boss_cfg") or {}).get("shield", 0.0)), 1),
                    "bd": int(p.get("boss_dmg", 0.0)),     # 供验收脚本核对新手保护（隐藏分 bank 不对外暴露）
                })
            return {"t": round(time.time(), 2),
                    "version": RP_VERSION,
                    "map": (ACTIVE_MAP or {}).get("id"),
                    "map_switch_in": round(max(0.0, self.map_switch_at - time.time()), 1),
                    "event": self.rnd_events.snapshot(),
                    "boss_alive": bool(self.boss_pid is not None and
                                       self.boss_pid in self.players),
                    "pads": [{"x": a[0], "y": a[1], "r": (a[2] if len(a) > 2 else 60)}
                             for a in AMMO_PADS],
                    "diff": self.bot_diff, "players": ps,
                    "tutorials": len(TUT_DONE), "tutorial_sessions": len(TUT_SESSIONS),
                    "offline": list(self.offline.values()),
                    "bans": ban_snapshot(),        # 验收/联赛脚本可核对封禁名单
                    "fps": _host_fps(), "lag": _LAG_TOTAL[0], "tick": TICK,
                    "events": list(self.event_log[-200:])}

    # ---- 快照 ----
    def snapshot(self, now):
        with self.lock:
            ps = []
            for p in self.players.values():
                ps.append({
                    "id": p["id"], "n": p["name"], "team": p["team"], "bot": p["bot"],
                    "x": round(p["x"], 1), "y": round(p["y"], 1), "a": round(p["angle"], 3),
                    "hp": p["hp"], "al": p["alive"], "w": p["weapon"],
                    "ph": (None if p["chem"].ph is None else round(p["chem"].ph, 2)),
                    "buf": round(p["chem"].ring, 1),
                    "am": p["ammo"][p["weapon"]],
                    "res": p["reserve"].get(p["weapon"], 0),
                    "st": p.get("st", {}),
                    "rl": round(max(0.0, p["reload_end"] - now), 1) if p["reloading"] else 0,
                    "k": p["kills"], "d": p["deaths"], "s": p["score"],
                    "scd": round(max(0.0, p["smoke_cd"] - now), 1),
                    "acd": round(max(0.0, p["skill_cd"] - now), 1),
                    "aoe": round(max(0.0, p["aoe_until"] - now), 1),
                    "exp": round(max(0.0, p["expose_until"] - now), 1),
                    "pw": p["power"],
                    "nk": 1 if p["nuke_at"] is not None else 0,
                    "nk_ok": 1 if (p["nuke_at"] is None and
                                   p.get("bank", 0) >= NUKE_SCORE and
                                   self.rnd_events.current is None) else 0,
                    "hv": 1 if p["hvt"] else 0,
                    "sg": 1 if p["in_spawn"] else 0,
                    "ind": round(max(0.0, p["ind_until"] - now), 1),
                    "indn": p["ind_charges"],
                    "indcd": round(max(0.0, p["ind_cd"] - now), 1),
                    "resp": round(max(0.0, p["respawn_at"] - now), 1) if not p["alive"] else 0,
                    # Boss 专用字段（普通玩家为 0，不占带宽里的字符串）
                    "bos": 1 if p.get("is_boss") else 0,
                    "bsh": round(p.get("boss_shield", 0.0), 1),
                    "bmx": round(float((p.get("boss_cfg") or {}).get("shield", 0.0)), 1),
                    "bd": int(p.get("boss_dmg", 0.0)),
                })
            bs = [[round(b["x"], 1), round(b["y"], 1), round(b["dx"], 3), round(b["dy"], 3),
                   CH_CODE.get(channel_of(b["w"]), 0), WEAPON_CODE.get(str(b["w"]).split("_")[0], 2)]
                  for b in self.bullets]
            sk = [[round(s["x"], 1), round(s["y"], 1), round(s["r"], 1),
                   round(max(0.0, s["until"] - now), 1)] for s in self.smokes]
            ev = self.events
            if ev:
                self.event_log.extend(ev)
                if len(self.event_log) > 400:
                    del self.event_log[:-400]
            self.events = []
            fd = [{"id": f["id"], "k": f["k"], "kt": f["kt"], "v": f["v"], "vt": f["vt"],
                   "w": f["w"], "cause": f.get("cause"), "ph": f.get("ph")}
                  for f in self.feed]
            return {"t": "s", "ps": ps, "bs": bs, "ev": ev, "feed": fd,
                    "sc": self.scores, "sk": sk, "cfg": self.chem_snapshot(),
                    "off": list(self.offline.values()),
                    "evt": self.rnd_events.snapshot(),
                    "bct": self.boss_top_contrib(),
                    "bz": {k: [list(r) for r in v] for k, v in self._buffer_rects().items()},
                    "bans": ban_snapshot(),
                    "boss": ({**self.boss_result} if self.boss_result else None),          # 已退出玩家（榜单显示"不在线"）
                    "ms": round(max(0.0, self.map_switch_at - now), 1)}   # 换图倒计时（秒）


GAME = Game()
load_tutorial_done()
load_bans()
CLIENTS = {}                     # pid -> WSConn
CLIENTS_LOCK = threading.Lock()


def handle_message(pid, msg, game=None):
    """处理一条客户端消息。game=None 走主对局；教程玩家传其私有教程小局。"""
    G = game if game is not None else GAME
    t = msg.get("t")
    with G.lock:
        p = G.players.get(pid)
        if p is None:
            return
        now = time.time()
        if t == "in":
            k = msg.get("k", [0, 0, 0, 0])
            if isinstance(k, list) and len(k) == 4:
                p["in"] = [1 if v else 0 for v in k]
            p["ax"] = float(msg.get("ax", p["ax"]))
            p["ay"] = float(msg.get("ay", p["ay"]))
            p["shoot"] = bool(msg.get("s"))
        elif t == "sw":
            w = msg.get("w")
            if w in WEAPONS:
                p["weapon"] = w
                p["reloading"] = False
        elif t == "rl":
            G._start_reload(p, now)
        elif t == "smoke":
            # 投放烟雾弹：需存活且冷却完毕，位置由小地图选点
            if p["alive"] and now >= p["smoke_cd"]:
                try:
                    x = float(msg.get("x")); y = float(msg.get("y"))
                except (TypeError, ValueError):
                    return
                G.throw_smoke(p, x, y, now)
        elif t == "chat":
            # 全局聊天：记录 2 分钟，广播给所有人（简单防刷：0.5s 一条）
            if now - p["last_chat_t"] > 0.5:
                text = str(msg.get("msg", "")).strip()[:80]
                if text:
                    p["last_chat_t"] = now
                    G.chat_log.append({"name": p["name"], "team": p["team"],
                                          "msg": text, "at": now})
                    G.chat_log = [c for c in G.chat_log if now - c["at"] <= CHAT_KEEP]
                    G.events.append({"t": "chat", "name": p["name"],
                                        "team": p["team"], "msg": text})
        elif t == "indicator":
            G.use_indicator(p, now)
        elif t == "skill":
            # AOE 技能（Q）：CD 60 秒，触发后一段时间内子弹带溅射伤害
            if p["alive"] and now >= p["skill_cd"]:
                p["skill_cd"] = now + SKILL_CD
                p["aoe_until"] = now + SKILL_DUR
                G.events.append({"t": "skill_on", "pid": p["id"], "dur": SKILL_DUR})
        elif t == "nuke":
            # 氟泄漏：只看"隐藏可用积分"够不够（每次扣 NUKE_SCORE=30000），用完直接扣，
            # 与显示积分、核弹档位无关 —— 修复"过 3 万分一直提示可用""6 万分按了没反应"。
            # 特殊事件进行中禁止使用核弹（小游戏派对期间不允许一发清场）
            if G.rnd_events.current is not None:
                G.events.append({"t": "nuke_block", "pid": p["id"],
                                 "why": "特殊事件进行中，氟泄漏被禁用"})
                return
            if p["alive"] and p["nuke_at"] is None and G.can_spend(p, NUKE_SCORE):
                G.spend_points(p, NUKE_SCORE)
                p["nuke_at"] = now + NUKE_DELAY
                G.events.append({"t": "nuke_start", "pid": p["id"], "team": p["team"],
                                    "name": p["name"], "cd": NUKE_DELAY})
                print("  [核弹] %s 积分攒满，%d 秒后引爆！" % (p["name"], int(NUKE_DELAY)))


# ---------------- WebSocket（RFC6455 极简实现） ----------------
WS_MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class WSConn:
    def __init__(self, conn, buf=b""):
        self.conn = conn
        self.buf = buf
        self.closed = False
        self.q = queue.Queue(maxsize=64)

    def _read_more(self):
        data = self.conn.recv(4096)
        if not data:
            self.closed = True
            return False
        self.buf += data
        return True

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
            if len(buf) < off + 2:
                return None
            ln = struct.unpack(">H", buf[off:off + 2])[0]
            off += 2
        elif ln == 127:
            if len(buf) < off + 8:
                return None
            ln = struct.unpack(">Q", buf[off:off + 8])[0]
            off += 8
        if ln > 1 << 20:
            self.closed = True
            return None
        mask_len = 4 if masked else 0
        if len(buf) < off + mask_len + ln:
            return None
        if masked:
            mask = buf[off:off + 4]
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(buf[off + 4:off + 4 + ln]))
        else:
            payload = buf[off:off + ln]
        self.buf = buf[off + mask_len + ln:]
        return opcode, payload

    def recv_message(self):
        """返回文本消息或 None（连接关闭）"""
        while True:
            if self.closed:
                return None
            fr = self._parse_frame()
            if fr is None:
                if not self._read_more():
                    return None
                continue
            opcode, payload = fr
            if opcode == 0x8:            # close
                self.closed = True
                return None
            if opcode == 0x9:            # ping -> pong
                self._raw_send(0xA, payload)
                continue
            if opcode in (0x1, 0x2):
                return payload.decode("utf-8", "replace")
            # 忽略其他帧

    def _raw_send(self, opcode, payload):
        try:
            header = bytes([0x80 | opcode])
            n = len(payload)
            if n < 126:
                header += bytes([n])
            elif n < 65536:
                header += bytes([126]) + struct.pack(">H", n)
            else:
                header += bytes([127]) + struct.pack(">Q", n)
            self.conn.sendall(header + payload)
            return True
        except OSError:
            self.closed = True
            return False

    def close(self):
        """主动断开（停机时用）：关闭底层 socket，让 recv/发送线程退出。

        注意：早期版本 ServerHandle.stop() 调用了不存在的 WSConn.close()，
        异常被吞掉后玩家连接一直挂着，客户端只表现为画面冻结。
        """
        self.closed = True
        try:
            self.conn.shutdown(socket.SHUT_RDWR)   # 立刻唤醒阻塞中的 recv
        except OSError:
            pass
        try:
            self.conn.close()
        except OSError:
            pass

    def send_text(self, text):
        if self.closed:
            return
        try:
            self.q.put_nowait(text)
        except queue.Full:
            self.closed = True     # 发送积压，丢弃慢客户端

    def sender_loop(self):
        while not self.closed:
            try:
                text = self.q.get(timeout=5)
            except queue.Empty:
                continue
            if not self._raw_send(0x1, text.encode("utf-8")):
                return


def ws_handshake(conn, headers):
    key = None
    for line in headers.split("\r\n"):
        if line.lower().startswith("sec-websocket-key:"):
            key = line.split(":", 1)[1].strip()
            break
    if not key:
        return False
    accept = base64.b64encode(hashlib.sha1((key + WS_MAGIC).encode()).digest()).decode()
    resp = ("HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n\r\n")
    conn.sendall(resp.encode())
    return True


def ws_session(conn, buf, addr=None):
    is_local = bool(addr) and addr[0] in local_addrs()
    my_ip = addr[0] if addr else "?"
    wsc = WSConn(conn, buf)
    pid = None
    tut_cid = None          # 教程小局连接 id（非空表示该连接在私有教程局里）
    tut_name = ""
    sender = threading.Thread(target=wsc.sender_loop, daemon=True)
    sender.start()
    # 挑战值：客户端 join 时必须携带正确签名（防自制客户端/改包客户端）
    challenge = "%d.%s" % (int(time.time() * 1000), os.urandom(4).hex())
    # 告知客户端：是否为主机（本机连接）+ 当前房间 AI 设置 + 对战地址
    wsc.send_text(json.dumps({"t": "hello", "host": is_local,
                              "bots": GAME.bot_count(), "diff": GAME.bot_diff,
                              "ch": challenge,
                              "map": ACTIVE_MAP,
                              "urls": ["http://%s:%d" % (ip, PORT) for ip in lan_ips()]}))
    print("  [连接] %s%s" % (my_ip, "（主机）" if is_local else ""))
    GAME.seen_ips.add(my_ip)
    try:
        while True:
            raw = wsc.recv_message()
            if raw is None:
                break
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if msg.get("t") == "join" and pid is None:
                # 连接签名校验
                if msg.get("auth") != calc_auth(challenge):
                    wsc._raw_send(0x1, json.dumps(
                        {"t": "kick", "why": "客户端校验失败，请使用官方游戏页面"}).encode("utf-8"))
                    break
                name = str(msg.get("name", "")).strip()[:12] or "无名枪手"
                # ---- 封禁拦截：按玩家 ID 或机器 IP 命中后，客户端黑屏提示 + 断开 ----
                hit = ban_check(name, my_ip)
                if hit:
                    wsc.send_text(json.dumps({
                        "t": "banned", "kind": hit, "name": name, "ip": my_ip,
                        "why": ("你的玩家 ID 已被房主封禁" if hit == "id"
                                else "你的机器 IP 已被房主封禁"),
                    }))
                    print("  [封禁] 拒绝 %s（%s）：命中%s封禁名单" %
                          (name, my_ip, "玩家ID" if hit == "id" else "机器IP"))
                    time.sleep(0.6)
                    break
                team = msg.get("team")
                # ---- 首次进入的 IP：先进"独立新手教程小局"（独立小地图 + 只有人机靶子）----
                if tutorial_needed(my_ip):
                    cid, tp, tut_game, tut_map = start_tutorial_session(name)
                    tut_cid = cid
                    tut_name = name
                    pid = tp
                    with CLIENTS_LOCK:
                        CLIENTS[cid] = wsc
                    wsc.send_text(json.dumps({"t": "tut_start", "id": tp,
                                              "map": tut_map, "name": name}))
                    print("  [教程] %s（%s）进入私有教程小局 #%d（%s，只有靶子）" %
                          (name, my_ip, cid, tut_map.get("name")))
                    continue
                # 仅主机（运行服务器的电脑）能修改房间 AI 设置
                if is_local and isinstance(msg.get("settings"), dict):
                    st = msg["settings"]
                    try:
                        GAME.set_bots(int(st["bots"]), st.get("diff"))
                        print("  [房间] AI 数量=%d 强度=%s" %
                              (GAME.bot_count(), DIFFICULTY[GAME.bot_diff].get("name", GAME.bot_diff)))
                    except (KeyError, TypeError, ValueError):
                        pass
                p = GAME.add_player(name, team)
                pid = p["id"]
                p["ip"] = my_ip            # 记录来源 IP：主机面板"封 IP"时用
                # 同 IP 战绩恢复（断线重连/刷新页面不丢战绩）
                if GAME.restore_ip_stats(p, my_ip):
                    print("  [战绩恢复] %s（%s）击杀%d 阵亡%d 积分%d" %
                          (p["name"], my_ip, p["kills"], p["deaths"], p["score"]))
                GAME.clear_offline(p["name"])       # 回来了：从"不在线"名单里移除
                with CLIENTS_LOCK:
                    CLIENTS[pid] = wsc
                wsc.send_text(json.dumps({"t": "joined", "id": pid, "tut": 0}))
            elif pid is not None:
                sess = tutorial_session(tut_cid) if tut_cid is not None else None
                if msg.get("t") == "tut_done":
                    # 通关/跳过：销毁私有教程小局 → 正式加入主对局
                    if sess is not None:
                        nm = drop_tutorial_session(tut_cid) or tut_name or "无名枪手"
                        with CLIENTS_LOCK:
                            CLIENTS.pop(tut_cid, None)
                        p = GAME.add_player(nm, None)
                        pid = p["id"]
                        p["ip"] = my_ip        # 教程出关也算正式入局：补记 IP，封 IP 才有目标
                        GAME.restore_ip_stats(p, my_ip)
                        GAME.clear_offline(p["name"])
                        tut_cid = None
                        with CLIENTS_LOCK:
                            CLIENTS[pid] = wsc
                        wsc.send_text(json.dumps({"t": "joined", "id": pid, "tut": 0}))
                        wsc.send_text(json.dumps({"t": "map", "map": ACTIVE_MAP}))
                        print("  [教程] %s（%s）完成教程，已进入对局（pid=%d）" %
                              (nm, my_ip, pid))
                    mark_tutorial_done(my_ip)
                elif sess is not None:
                    # 教程玩家：消息与快照都走他自己的私有小局
                    handle_message(sess["pid"], msg, game=sess["game"])
                elif msg.get("t") == "admin":
                    # 仅主机可管理：分队 AI / 调整玩家队伍 / 赋予能力 / 封禁
                    if is_local:
                        act = msg.get("act")
                        if act == "ban":
                            ban_player_and_kick(msg.get("id"), msg.get("mode"))
                        elif act == "unban":
                            ban_remove(msg.get("kind"), msg.get("value"))
                        elif act == "kick":
                            kick_player(msg.get("id"))
                        elif act == "team":
                            GAME.admin_move_team(msg.get("id"), msg.get("team"))
                        elif act == "power":
                            GAME.admin_power(msg.get("id"), msg.get("pw"))
                        elif act == "chem":
                            # 参数面板 B：热调化学参数（现场演示）
                            GAME.set_chem_param(msg.get("k"), msg.get("v"))
                        elif act == "event":
                            # 主机手动触发随机事件：id=事件id / "random" / "on" / "off"
                            GAME.force_event(msg.get("id"))
                        else:
                            GAME.set_team_bots(msg.get("team"), msg.get("n"))
                else:
                    handle_message(pid, msg)
    except OSError:
        pass
    finally:
        wsc.closed = True
        if tut_cid is not None:                 # 教程中掉线：直接回收私有小局
            drop_tutorial_session(tut_cid)
            with CLIENTS_LOCK:
                CLIENTS.pop(tut_cid, None)
        elif pid is not None:
            # 断线后按 IP 保留战绩（本次开服期间有效）
            with GAME.lock:
                GAME.note_offline(GAME.players.get(pid))   # 榜单保留战绩并标注"不在线"
                GAME.save_ip_stats(GAME.players.get(pid), my_ip)
            with CLIENTS_LOCK:
                CLIENTS.pop(pid, None)
            GAME.remove_player(pid)
        try:
            conn.close()
        except OSError:
            pass


# ---------------- 静态文件 ----------------
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".svg": "image/svg+xml",
}


def serve_static(conn, head):
    line = head.split("\r\n", 1)[0]
    parts = line.split(" ")
    path = parts[1] if len(parts) > 1 else "/"
    path = path.split("?", 1)[0].split("#", 1)[0]   # 去掉查询串/锚点
    if path == "/status.json":
        body = json.dumps(GAME.status(), ensure_ascii=False,
                          separators=(",", ":")).encode("utf-8")
        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json; charset=utf-8\r\n"
                     b"Cache-Control: no-store\r\nContent-Length: " +
                     str(len(body)).encode() + b"\r\n\r\n" + body)
        return
    if path == "/":
        path = "/index.html"
    fp = os.path.normpath(os.path.join(BASE, path.lstrip("/")))
    if not fp.startswith(BASE) or not os.path.isfile(fp):
        body = b"<h1>404</h1>"
        conn.sendall(b"HTTP/1.1 404 Not Found\r\nContent-Type: text/html\r\n"
                     b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
        return
    ext = os.path.splitext(fp)[1].lower()
    with open(fp, "rb") as f:
        body = f.read()
    conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: " +
                 CONTENT_TYPES.get(ext, "application/octet-stream").encode() +
                 b"\r\nCache-Control: no-store\r\nContent-Length: " +
                 str(len(body)).encode() + b"\r\n\r\n" + body)


# ---------------- 游戏循环与广播 ----------------
_STOP = threading.Event()          # 内嵌模式下的优雅停机信号
_TICK_TS = collections.deque(maxlen=120)   # 最近若干帧时间戳（用于实测主机帧率）
_LAG_TOTAL = [0]                   # 追不上 30Hz 的帧数（sleep_t<=0）


def _host_fps():
    """用最近若干帧时间戳估算主机主循环实际帧率（手机端性能自查用）。"""
    if len(_TICK_TS) < 2:
        return 0.0
    span = _TICK_TS[-1] - _TICK_TS[0]
    if span <= 0:
        return 0.0
    return round((len(_TICK_TS) - 1) / span, 1)


_TIMER_FIXED = [False]


def _raise_timer_resolution():
    """Windows 默认定时器精度约 15.6ms，time.sleep(1/60) 会抖成 31ms 等，
    造成"画面一卡一卡、CPU 却很闲"。把系统定时器精度提到 1ms（仅 Windows）。"""
    if _TIMER_FIXED[0] or os.name != "nt":
        return
    try:
        import ctypes
        ctypes.windll.winmm.timeBeginPeriod(1)
        _TIMER_FIXED[0] = True
        print("  [计时] Windows 定时器精度已提升到 1ms（消除 sleep 抖动）")
    except Exception:
        pass


def game_loop():
    dt = 1.0 / TICK
    next_t = time.time()
    while not _STOP.is_set():
        now = time.time()
        _TICK_TS.append(now)
        GAME.maybe_switch_map(now)
        GAME.update(now, dt)
        snap = json.dumps(GAME.snapshot(now), separators=(",", ":"))
        # 教程玩家（私有小局）：各自推进自己的 Game，快照只发给他自己
        tut_snaps = {}
        with _TUT_SESS_LOCK:
            tut_list = list(TUT_SESSIONS.items())
        for cid, sess in tut_list:
            try:
                g = sess["game"]
                g.update(now, dt)
                tut_snaps[cid] = json.dumps(g.snapshot(now), separators=(",", ":"))
            except Exception as e:
                tut_snaps[cid] = None
                print("  [教程] 小局推进异常: %s" % e)
        with CLIENTS_LOCK:
            conns = list(CLIENTS.items())
        for cid, c in conns:
            payload = tut_snaps.get(cid, snap)
            if payload:
                c.send_text(payload)
        next_t += dt
        sleep_t = next_t - time.time()
        if sleep_t > 0:
            time.sleep(sleep_t)
        else:
            _LAG_TOTAL[0] += 1
            next_t = time.time()   # 跟不上时重置，避免雪崩


def handle_conn(conn, addr):
    try:
        conn.settimeout(10)
        data = b""
        while b"\r\n\r\n" not in data and len(data) < 65536:
            chunk = conn.recv(4096)
            if not chunk:
                return
            data += chunk
        head, _, rest = data.partition(b"\r\n\r\n")
        head_s = head.decode("utf-8", "replace")
        if "upgrade: websocket" in head_s.lower():
            if ws_handshake(conn, head_s):
                conn.settimeout(None)
                ws_session(conn, rest, addr)
        else:
            conn.settimeout(5)
            serve_static(conn, head_s)
    except OSError:
        pass
    finally:
        try:
            conn.close()
        except OSError:
            pass


def lan_ips():
    ips = set()
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
            if "." in ip and not ip.startswith("127."):
                ips.add(ip)
    except OSError:
        pass
    return sorted(ips)


_LOCAL_ADDRS = None


def local_addrs():
    """本机地址集合（延迟初始化）：主机用任何本机 IP 打开页面都识别为管理员"""
    global _LOCAL_ADDRS
    if _LOCAL_ADDRS is None:
        _LOCAL_ADDRS = set(lan_ips()) | {"127.0.0.1", "::1"}
    return _LOCAL_ADDRS


def _open_browser(port=None):
    time.sleep(0.8)
    try:
        import webbrowser
        webbrowser.open("http://localhost:%d" % (port or PORT))
    except Exception:
        pass


class ServerHandle(object):
    """一次服务端实例的句柄：安卓端用它取端口/局域网地址，也用于优雅停机。"""

    def __init__(self, srv, port, host, ips, bots):
        self.srv = srv
        self.port = port
        self.host = host
        self.ips = list(ips)
        self.bots = bots
        self._accept_thread = None
        self._loop_thread = None

    def urls(self):
        return ["http://%s:%d" % (ip, self.port) for ip in self.ips]

    def stop(self, timeout=3.0):
        _STOP.set()
        try:
            self.srv.close()                 # 让阻塞中的 accept() 抛错退出
        except OSError:
            pass
        with CLIENTS_LOCK:
            conns = list(CLIENTS.values())
            CLIENTS.clear()
        for c in conns:
            try:
                c.close()
            except Exception:
                pass
        if self._accept_thread is not None:
            self._accept_thread.join(timeout)
        if self._loop_thread is not None:
            self._loop_thread.join(timeout)   # 关键：否则秒级连开会跑出两个 game_loop

    def _accept_loop(self):
        try:
            while not _STOP.is_set():
                conn, addr = self.srv.accept()
                threading.Thread(target=handle_conn, args=(conn, addr), daemon=True).start()
        except OSError:
            pass


def _print_banner(ips, bots):
    print()
    print("=" * 60)
    print("  Reaction Protocol v%s · 主机模式已启动" % RP_VERSION)
    print("  （这台设备 = 服务器 + 玩家1，无需任何其他服务端）")
    print("  " + RP_COPYRIGHT)
    print("=" * 60)
    if OPEN_BROWSER:
        print("  [自己玩] 正在自动打开游戏页面...")
        print("           (没弹出就手动访问 http://localhost:%d)" % PORT)
    else:
        print("  [自己玩] 浏览器打开  http://localhost:%d" % PORT)
    print()
    print("  >>> 对战地址：把下面任意一行发给要对战的同学 <<<")
    print("  >>> 他在自己设备上打开，即可联机对枪 <<<")
    if ips:
        for ip in ips:
            print("        http://%s:%d" % (ip, PORT))
    else:
        print("        (未检测到局域网IP，请在终端执行 hostname -I 查询)")
    if bots:
        print()
        print("  机器人陪练: %d 个" % bots)
    print()
    print("  同学打不开？检查：1) 是否连同一WiFi/网线  2) 关闭本机防火墙")
    print("  结束游戏：按 Ctrl+C")
    print("=" * 60)


def run_server(port=8765, bots=0, host="0.0.0.0", root=None, open_browser=False,
               load_ai=None, ai_weights=None, map_switch_seconds=None, tick=None,
               ready_cb=None, block=False, quiet=False):
    """启动权威服务端。PC 的 CLI 与安卓内嵌（Chaquopy）共用这一个入口。

    block=True  -> 阻塞在 accept 循环（PC 命令行，Ctrl+C 退出）
    block=False -> 后台线程 accept，立即返回 ServerHandle（安卓前台服务用）
    """
    _STOP.clear()
    _raise_timer_resolution()          # Windows：把 sleep 抖动从 ~15.6ms 降到 1ms
    if load_ai is None:
        load_ai = os.environ.get("LAN_AI_BOTS") == "1"
    configure(root=root, port=port, bots=bots, open_browser=open_browser,
              map_switch_seconds=map_switch_seconds, ai_weights=ai_weights, load_ai=load_ai,
              tick=tick)
    GAME.reset()                       # 每次开服都是新对局：清掉旧比分/旧机器人/旧事件
    for i in range(BOTS):
        name = "机器人%d号" % (i + 1)
        p = GAME.add_player(name, "auto", bot=True)
        # AI 错峰：否则所有机器人在同一帧做推理，手机 CPU 峰值会顶满整帧预算
        if isinstance(p, dict):
            p["next_think"] = time.time() + i * 0.03
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, PORT))
    srv.listen(64)
    loop_thread = threading.Thread(target=game_loop, daemon=True)
    loop_thread.start()
    if OPEN_BROWSER:
        threading.Thread(target=_open_browser, args=(PORT,), daemon=True).start()
    ips = lan_ips()
    handle = ServerHandle(srv, PORT, host, ips, BOTS)
    handle._loop_thread = loop_thread
    if not quiet:
        _print_banner(ips, BOTS)
    if ready_cb is not None:
        try:
            ready_cb(handle)
        except Exception as _e:      # 回调失败不影响开服
            print("[回调] ready_cb 出错:", _e)
    if block:
        try:
            while not _STOP.is_set():
                conn, addr = srv.accept()
                threading.Thread(target=handle_conn, args=(conn, addr), daemon=True).start()
        except KeyboardInterrupt:
            print("\n主机已关闭")
        except OSError:
            pass
        finally:
            handle.stop()
        return handle
    handle._accept_thread = threading.Thread(target=handle._accept_loop, daemon=True)
    handle._accept_thread.start()
    return handle


def main():
    port, bots, open_browser, tick = parse_args(sys.argv[1:])
    run_server(port=port, bots=bots, open_browser=open_browser, tick=tick, block=True)


if __name__ == "__main__":
    main()
