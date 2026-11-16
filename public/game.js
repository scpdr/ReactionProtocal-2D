"use strict";
/* 机房对枪 · LAN Shooter 客户端（纯原生 JS，无依赖） */

// ================= 基础 =================
var cv = document.getElementById("cv");
var ctx = cv.getContext("2d");
var VW = 0, VH = 0, DPR = 1, UIS = 1, UW = 0, UH = 0;
/* ---------------- 画质/流畅度 ----------------
 * 机房/老旧电脑常见问题：大画布 + 高 DPR 时 GPU 填充率不够，帧率掉但 CPU 看起来不忙。
 * 策略：帧率连续低于 45fps 就把 DPR 降到 1（像素量直接减到 1/4），并减少粒子；
 * 也可用 URL 参数强制：?perf=1 低画质、?perf=0 不自动降级。 */
var DPR_CAP = 3;
var frameMs = 16.7, jankCount = 0, lowQuality = false;
var _qLast = 0, _qBad = 0;
var PERF_MODE = -1;          // -1=自动，0=强制高画质，1=强制低画质
try {
  var _pq = new URLSearchParams(location.search).get("perf");
  if (_pq === "1") PERF_MODE = 1;
  else if (_pq === "0") PERF_MODE = 0;
} catch (e) { }

function qualityTick(now) {
  if (PERF_MODE === 1 && !lowQuality) { setLowQuality(true); return; }
  if (PERF_MODE !== -1) return;                       // 手动指定画质时不自动降级
  if (now - _qLast < 1000) return;
  _qLast = now;
  var fps = 1000 / Math.max(1, frameMs);
  if (fps < 45) _qBad++; else if (_qBad > 0) _qBad--;
  if (_qBad >= 3 && !lowQuality) setLowQuality(true);   // 连续 3 秒低于 45fps → 降画质
}

function setLowQuality(on) {
  if (lowQuality === on) return;
  lowQuality = on;
  DPR_CAP = on ? 1 : 3;
  resize();                                            // 立刻按新 DPR 重建画布与地图层
  if (typeof hintMsg !== "undefined") {
    hintMsg = on ? "已自动切换到流畅模式（画质降低）" : "已恢复高画质";
    hintT = performance.now() + 2500;
  }
}

function perfFps() { return Math.round(1000 / Math.max(1, frameMs)); }
function perfLabel() {
  return "FPS " + perfFps() + " · " + frameMs.toFixed(1) + "ms · 插值 " +
    Math.round(interpDelayMs()) + "ms" + (lowQuality ? " · 流畅模式" : "");
}

function resize() {
  // 按 devicePixelRatio 放大画布位图，避免高分屏拉伸模糊
  DPR = Math.max(1, Math.min(DPR_CAP, window.devicePixelRatio || 1));
  VW = innerWidth; VH = innerHeight;
  // HUD 逻辑空间：大屏时整体放大（1366×768 为基准），避免 4K 下 UI 太小
  UIS = Math.max(1, Math.min(2.4, Math.min(VW / 1366, VH / 768)));
  UW = VW / UIS; UH = VH / UIS;
  cv.width = Math.floor(VW * DPR);
  cv.height = Math.floor(VH * DPR);
  cv.style.width = VW + "px";
  cv.style.height = VH + "px";
  mapLayer = null;                 // 重建高清地图层（见下）
}
addEventListener("resize", resize); resize();

var WORLD_W = 2400, WORLD_H = 1600;
var ACTIVE_MAP = null;
function applyMap(map) {
  if (!map || !map.w) return;
  ACTIVE_MAP = map;
  WORLD_W = +map.w; WORLD_H = +map.h;
  WALLS = (map.walls || []).map(function (w) { return [+w[0], +w[1], +w[2], +w[3]]; });
  mapLayer = null;                 // 下一帧重建
}
var WALLS = [
  [0, 0, WORLD_W, 20], [0, WORLD_H - 20, WORLD_W, 20], [0, 0, 20, WORLD_H], [WORLD_W - 20, 0, 20, WORLD_H],
  [1140, 640, 120, 320], [1000, 380, 400, 60], [1000, 1160, 400, 60],
  [500, 240, 60, 340], [500, 1020, 60, 340], [1840, 240, 60, 340], [1840, 1020, 60, 340],
  [760, 740, 140, 120], [1500, 740, 140, 120],
  [900, 140, 120, 120], [1380, 140, 120, 120], [900, 1340, 120, 120], [1380, 1340, 120, 120],
  [280, 720, 40, 160], [2080, 720, 40, 160]
];
var WEAPONS = {
  knife:   { name: "搅拌棒", mag: 0 },
  pistol:  { name: "硅酸", mag: 12 },
  rifle:   { name: "盐酸枪", mag: 30 },
  sniper:  { name: "高氯酸", mag: 5 },
  shotgun: { name: "喷淋器", mag: 6 },
  lmg:     { name: "碱液枪", mag: 100 },
  asniper: { name: "碱式连狙", mag: 5 },
  limewater: { name: "石灰水", mag: 2 },
  vitc:    { name: "维C", mag: 3, noreload: true },
  nuke:    { name: "氟泄漏", mag: 0 }
};
var WKEYS = { "Digit1": "knife", "Digit2": "pistol", "Digit3": "rifle", "Digit4": "sniper",
              "Digit5": "shotgun", "Digit6": "lmg", "Digit7": "asniper",
              "Digit8": "limewater", "Digit9": "vitc" };
var RELOAD_TIME = { pistol: 1.3, rifle: 2.1, sniper: 2.6, shotgun: 2.8, lmg: 3.2, asniper: 2.5 };
var PW_NAME = { none: "", speed: "⚡速", ghost: "隐身" };
var SMOKE_R = 220;           // 与服务端一致
var AOE_CLIENT_R = 100;      // AOE 溅射半径（与服务端一致）
var TEAM_COLOR = { red: "#ff5252", blue: "#4da6ff", boss: "#ff5bd0" };
var CH_COLOR = { acid: "#ff6b35", base: "#39c7d8", oxidizing: "#b07cff",
                 physical: "#c8d2dc", reducing: "#7dffb2", neutralize: "#7dffb2" };
// 每个试剂通道的弹道外观：core=内芯 glow=外发光 head=弹头
var BULLET_STYLE = [
  { core: "#ffd9c7", glow: "rgba(255,107,53,0.22)", len: 46, width: 4.0, head: "#ff6b35" },
  { core: "#d8f8fc", glow: "rgba(57,199,216,0.22)", len: 44, width: 4.0, head: "#39c7d8" },
  { core: "#f1e4ff", glow: "rgba(176,124,255,0.26)", len: 50, width: 4.4, head: "#b07cff" },
  { core: "#eef2f6", glow: "rgba(200,210,220,0.16)", len: 34, width: 3.0, head: "#c8d2dc" },
  { core: "#d9ffe9", glow: "rgba(125,255,178,0.24)", len: 42, width: 3.6, head: "#7dffb2" }
];
function weaponChannel(w) {
  if (w === "knife") return "physical";
  if (w === "shotgun" || w === "nuke") return "oxidizing";
  if (w === "lmg" || w === "limewater" || w === "asniper") return "base";
  if (w === "vitc") return "reducing";
  return "acid";
}
var STREAK_NAMES = { 2: "双杀！", 3: "三连杀！", 4: "四连超凡！", 5: "五连绝世！", 6: "大杀特杀！", 7: "无人能挡！" };

// ================= 状态 =================
var ws = null, myId = null, joined = false;
var players = {};            // id -> player（服务器最新原始状态，用于逻辑/HUD）
var bullets = [];
var scores = { red: 0, blue: 0 };
var offline = [];            // 已退出服务器的玩家 [{n,team,k,d,s}]：榜单显示但标注"不在线"
var chemCfg = null;             // 服务端化学参数（参数面板 B 显示/热调用）
var smokes = [];             // 活跃烟雾 [[x,y,r,remain]]
var laserBeams = {};         // 机枪常驻激光 {pid: {x1,y1,x2,y2,until}}
var booms = [];              // AOE 命中爆炸圈 [{x,y,t}]
var hvtTip = null;           // 当前高价值目标名字（右侧小字说明用）
var chatMsgs = [];           // 最近聊天消息 [{name,team,msg,t}]
var showAdmin = false;       // 管理面板展开状态（主机按 B 切换）
var nukeFlash = 0;           // 核弹白闪强度
var nukeInfo = null;         // {name, team, end} 核弹倒计时
var smokeAim = false;        // E 键进入的小地图选点投放模式
var hintMsg = "", hintT = 0; // 屏幕提示
var snapPrev = null, snapCur = null;   // 相邻两帧快照，用于插值渲染
var INTERP_DELAY = 80;       // 兜底值（实际用 interpDelayMs() 自适应）
var snapIvMs = 33;           // 快照到达间隔 EMA（毫秒）
var lastSnapArrive = 0;
var selfSmooth = null;       // 本地玩家的平滑显示位置 {x,y}

/* 插值缓冲只需覆盖约 1.5 个快照间隔：60Hz 服务器 ≈ 27ms，30Hz ≈ 50ms。
 * 原来死值 80ms 是"不跟手"的主因之一（叠加服务器步进后视觉延迟 >110ms）。 */
function interpDelayMs() {
  return Math.max(24, Math.min(80, snapIvMs * 1.55));
}


function lerpAngle(a, b, t) {
  var d = ((b - a + Math.PI * 3) % (Math.PI * 2)) - Math.PI;
  return a + d * t;
}
// 计算本帧应显示的平滑位置（在前两帧快照之间插值，轻微外推）
function getDisplayPlayers(now) {
  if (!snapCur) return players;
  var out = {};
  var alpha = 1;
  var delay = interpDelayMs();
  if (snapPrev && snapCur.t > snapPrev.t) {
    alpha = (now - delay - snapPrev.t) / (snapCur.t - snapPrev.t);
    if (alpha > 1.25) alpha = 1.25;
    if (alpha < 0) alpha = 0;
  }
  for (var id in snapCur.ps) {
    var c = snapCur.ps[id];
    var p0 = snapPrev ? snapPrev.ps[id] : null;
    if (p0 && alpha <= 1) {
      var o = {};
      for (var k in c) o[k] = c[k];
      o.x = p0.x + (c.x - p0.x) * alpha;
      o.y = p0.y + (c.y - p0.y) * alpha;
      o.a = lerpAngle(p0.a, c.a, alpha);
      out[id] = o;
    } else if (p0) {
      // 快照延迟时轻微外推，避免卡顿感
      var e = Math.min(1, alpha - 1);
      var o2 = {};
      for (var k2 in c) o2[k2] = c[k2];
      o2.x = c.x + (c.x - p0.x) * e;
      o2.y = c.y + (c.y - p0.y) * e;
      out[id] = o2;
    } else {
      out[id] = c;
    }
  }
  return out;
}

/* 切换世界（教程小地图 ↔ 对战大地图）时清掉上一张图的残留状态 */
function resetWorldState() {
  players = {}; bullets = []; smokes = []; feed = {}; offline = [];
  snapPrev = null; snapCur = null; selfSmooth = null;
  bufferZones = {}; party = null; bannedInfo = null;
  particles = []; dmgTexts = []; scorePops = []; dmgDirs = [];
  mapSwitchIn = 0;
  deadInfo = null; killConfirm = null; killImpactT = 0; banner = ""; bannerT = 0;
  cam.x = 0; cam.y = 0;
}

/* 本地玩家：不走插值缓冲，直接向"最新快照位置"快速收敛（视觉延迟只剩服务器步进+网络）。
 * 只影响绘制，不参与任何判定；自己死亡/未加入时回落到普通插值结果。 */
function smoothSelf(display, now, dt) {
  var s = players[myId];
  var shown = display[myId];
  if (!s || !shown || !s.al) { selfSmooth = null; return display; }
  if (!selfSmooth || Math.hypot(selfSmooth.x - s.x, selfSmooth.y - s.y) > 260) {
    selfSmooth = { x: s.x, y: s.y };          // 首次/瞬移（重生、换图）直接对齐
  } else {
    var k = 1 - Math.exp(-dt * 26);           // 约 40ms 收敛到最新位置
    selfSmooth.x += (s.x - selfSmooth.x) * k;
    selfSmooth.y += (s.y - selfSmooth.y) * k;
  }
  var o = {};
  for (var kk in shown) o[kk] = shown[kk];
  o.x = selfSmooth.x; o.y = selfSmooth.y;
  o.a = s.a;                                   // 朝向本来就是本地即时的
  display[myId] = o;
  return display;
}
var particles = [];          // {x,y,vx,vy,life,max,size,color}
var dmgTexts = [];           // {x,y,txt,life,kill}
var feed = {};               // serverFeedId -> {k,kt,v,vt,w,t}
var shake = 0, redFlash = 0, hitMarker = 0, hitMarkerKill = false;
var banner = "", bannerT = 0, streakText = "", streakT = 0;
var eventInfo = null;        // 当前随机事件 {id,name,desc,left,kind}
var announceInfo = null;     // 事件播报横幅 {title,desc,t}
var bossInfo = null;         // 当前 Boss（来自快照 ps 里的 bos=1 玩家）
var bossContrib = [];        // Boss 战伤害贡献前几名 [{n,dmg}]
var bossResult = null;       // 最近一次 Boss 战结算 {boss,by,top,at}
var bossResultT = 0;         // 结算面板显示到什么时候（本地时钟）
var killConfirm = null;       // ELIMINATED 击杀确认卡 {name,w,cause,pts,t,hvt}
var killImpactT = 0;          // 击杀瞬间准心冲击环时间戳（移植自 3D 版）
var dmgDirs = [];             // 受击方向指示 {ang,t}
var scorePops = [];          // 战地式积分反馈 [{txt,t,big}]
var adminRects = [];         // 主机管理面板按钮热区
var deadInfo = null;         // {killer, resp}
var bufferZones = {};       // 出生缓冲地带 {team: [[x,y,w,h],...]}（服务端快照 bz）
var banList = null;         // 封禁名单 {names,ips,n_names,n_ips}（服务端快照 bans）
var bannedInfo = null;      // 封禁/踢出全屏状态 {kind,name,ip,why}
var party = null;           // 派对事件临时画面数据（山芋/岩浆/椅子）
var recoilVis = 0;          // 机枪后坐力视觉强度（0~1）
var nukeBlockT = 0;         // “事件中禁用核弹”提示过期时间
var lastFireTime = 0;
var cam = { x: 0, y: 0, scale: 1 };

/* ---------------- 视野（FOV）档位 ----------------
 * 手机横屏默认 "area"：视野面积 ≈ 半张地图（用户要求"至少能看见半个地图"）；
 * PC 默认 "orig"：维持原来的 1:1 观感，不受影响。
 * 覆盖方式：URL 参数 ?fov=area|width|height|orig|<数字>，或手机顶部菜单的"视野"键循环切换。
 */
var FOV_MODES = ["area", "width", "height", "orig"];
var FOV_LABEL = { area: "视野½", width: "视野宽", height: "视野高", orig: "视野1:1" };
/* 视野基准 = 半个地图；再按用户要求把视野缩小到 80%（线性，v1.2.1 反馈"fov 太大"）
 * → 面积占比 = 0.5×0.8² = 0.32，即默认"视野½"档实际看到的是地图面积的 32%。 */
var FOV_VIEW = 0.8;
var FOV_FRAC = { area: 0.5 * FOV_VIEW * FOV_VIEW, width: 0.5 * FOV_VIEW, height: 0.5 * FOV_VIEW };
var fovMode = "orig";

function fovScale(vw, vh) {
  vw = vw || VW; vh = vh || VH;
  var cover = Math.max(vw / WORLD_W, vh / WORLD_H);      // 下限：不露出地图外
  var s;
  if (fovMode === "area") s = Math.sqrt((vw * vh) / (FOV_FRAC.area * WORLD_W * WORLD_H));
  else if (fovMode === "width") s = vw / (FOV_FRAC.width * WORLD_W);
  else if (fovMode === "height") s = vh / (FOV_FRAC.height * WORLD_H);
  else if (typeof fovMode === "number") s = fovMode;
  else s = Math.max(1, cover);                          // orig
  return Math.max(cover, Math.min(s, 1.6));
}

function setFovMode(m) {
  if (m === undefined || m === null) return;
  fovMode = m;
  try { localStorage.setItem("rp_fov", String(m)); } catch (e) { }
}

function cycleFov() {
  var i = FOV_MODES.indexOf(fovMode);
  setFovMode(FOV_MODES[(i + 1) % FOV_MODES.length]);
  var s = fovScale();
  var txt = (typeof fovTag === "function" ? fovTag() : String(fovMode)) +
    "（视野 " + Math.round(VW / s) + "×" + Math.round(VH / s) + "，地图 " + WORLD_W + "×" + WORLD_H + "）";
  if (typeof mHint === "function") mHint(txt, 2200);
  return fovMode;
}

/** 视野档位短标签（手机顶部菜单按钮用） */
function fovTag() { return FOV_LABEL[fovMode] || "视野"; }

/** 解析视野参数：数字视作缩放值，字符串视作档位名 */
function initFov(isMobile) {
  var q = null;
  try { q = new URLSearchParams(location.search).get("fov"); } catch (e) { }
  var saved = null;
  try { saved = localStorage.getItem("rp_fov"); } catch (e) { }
  if (q) {
    var num = parseFloat(q);
    fovMode = (!isNaN(num) && num > 0) ? num : (FOV_MODES.indexOf(q) >= 0 ? q : "orig");
  } else if (saved && (FOV_MODES.indexOf(saved) >= 0 || (!isNaN(parseFloat(saved)) && parseFloat(saved) > 0))) {
    fovMode = FOV_MODES.indexOf(saved) >= 0 ? saved : parseFloat(saved);
  } else {
    fovMode = isMobile ? "area" : "orig";
  }
  return fovMode;
}

/* ---------------- 地图切换倒计时（全端） ---------------- */
var mapSwitchIn = 0;                  // 距离下次换图的秒数（服务端下发）
function fmtCountdown(s) {
  s = Math.max(0, Math.round(s || 0));
  var m = Math.floor(s / 60), r = s % 60;
  return m + ":" + (r < 10 ? "0" : "") + r;
}
var mouse = { x: 0, y: 0, down: false, right: false };
var keys = {};
var showBoard = false;
var showHelp = false;      // H：实验手册（机制说明层）

// ================= 音效（WebAudio 程序合成，无需音频文件） =================
var AC = null, noiseBuf = null;
function initAudio() {
  if (AC) return;
  try {
    AC = new (window.AudioContext || window.webkitAudioContext)();
    var len = AC.sampleRate;
    noiseBuf = AC.createBuffer(1, len, AC.sampleRate);
    var d = noiseBuf.getChannelData(0);
    for (var i = 0; i < len; i++) d[i] = Math.random() * 2 - 1;
  } catch (e) { AC = null; }
}
function playNoise(dur, freq, vol, type) {
  if (!AC) return;
  var src = AC.createBufferSource(); src.buffer = noiseBuf;
  var f = AC.createBiquadFilter(); f.type = type || "lowpass"; f.frequency.value = freq;
  var g = AC.createGain();
  var t = AC.currentTime;
  g.gain.setValueAtTime(vol, t);
  g.gain.exponentialRampToValueAtTime(0.001, t + dur);
  src.connect(f); f.connect(g); g.connect(AC.destination);
  src.start(t); src.stop(t + dur + 0.02);
}
function playTone(f0, f1, dur, type, vol, delay) {
  if (!AC) return;
  var o = AC.createOscillator(), g = AC.createGain();
  var t = AC.currentTime + (delay || 0);
  o.type = type || "sine";
  o.frequency.setValueAtTime(f0, t);
  o.frequency.exponentialRampToValueAtTime(Math.max(1, f1), t + dur);
  g.gain.setValueAtTime(vol, t);
  g.gain.exponentialRampToValueAtTime(0.001, t + dur);
  o.connect(g); g.connect(AC.destination);
  o.start(t); o.stop(t + dur + 0.02);
}
function sfx(name, vol) {
  if (!AC) return;
  vol = vol === undefined ? 1 : vol;
  if (vol <= 0.02) return;
  switch (name) {
    case "pistol": playNoise(0.09, 2500, 0.40 * vol); playTone(420, 110, 0.08, "square", 0.22 * vol); break;
    case "rifle":  playNoise(0.07, 3200, 0.38 * vol); playTone(300, 90, 0.06, "square", 0.20 * vol); break;
    case "sniper": playNoise(0.30, 1200, 0.55 * vol); playTone(170, 45, 0.28, "sawtooth", 0.30 * vol); break;
    case "knife":  playNoise(0.12, 4500, 0.18 * vol, "highpass"); break;
    case "hit":    playTone(1150, 900, 0.05, "square", 0.30 * vol); break;
    case "kill":   playTone(880, 880, 0.09, "sine", 0.38 * vol); playTone(1318, 1318, 0.28, "sine", 0.38 * vol, 0.09); break;
    case "death":  playTone(200, 55, 0.5, "sawtooth", 0.40 * vol); break;
    case "reload": playTone(500, 300, 0.07, "square", 0.18 * vol); break;
    case "dry":    playTone(230, 180, 0.05, "square", 0.18 * vol); break;
    case "spawn":  playTone(440, 660, 0.15, "sine", 0.2 * vol); break;
    case "score":  playTone(740, 980, 0.05, "sine", 0.12 * vol); break;
    case "nuke_siren": playTone(320, 640, 0.7, "sawtooth", 0.30 * vol); playTone(320, 640, 0.7, "sawtooth", 0.22 * vol, 0.8); break;
    case "nuke_boom":
      playNoise(1.4, 220, 0.9); playTone(90, 24, 1.3, "sawtooth", 0.6 * vol);
      playTone(52, 18, 1.6, "square", 0.4 * vol, 0.1); break;
    case "lmg":    playNoise(0.06, 3800, 0.30 * vol); playTone(340, 100, 0.05, "square", 0.16 * vol); break;
    case "hvt":    playTone(520, 1040, 0.35, "square", 0.28 * vol); playTone(780, 1560, 0.35, "square", 0.22 * vol, 0.18); break;
    case "skill":  playTone(440, 880, 0.18, "sine", 0.30 * vol); playNoise(0.15, 2400, 0.20 * vol, "highpass"); break;
    case "boom":   playNoise(0.28, 700, 0.45 * vol); playTone(140, 50, 0.25, "square", 0.25 * vol); break;
  }
}

// ================= 网络 =================
// 连接签名：与服务器约定的挑战应答（防自制/改包客户端接入）
function _fnv1a(s) {
  var h = 0x811C9DC5;
  for (var i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return ("0000000" + h.toString(16)).slice(-8);
}
var _KS = ["L", "4", "N", "_", "S", "h", "0", "0", "t", "3", "r", "#", "9", "F", "2"];
function signAuth(ch) {
  var k = _KS.join("") + _KS.slice(0, 5).reverse().join("");
  return _fnv1a(k + ch + k);
}

var startEl = document.getElementById("start");
var connTip = document.getElementById("connTip");
var _reJoin = null;          // 上次的加入动作：断线重连时自动复用（服务端按 IP 恢复战绩）
var _retryN = 0;             // 连续重连次数
var _retryTimer = null;
var _wasRetry = false;       // 本次成功连接是否来自重连（用于提示玩家）

function wsTarget() {
  // 安卓/本地打包：可用 ?server=192.168.1.5:8765 指定 PC 服务端；缺省跟随当前页面地址
  try {
    var q = new URLSearchParams(location.search);
    var sv = (q.get("server") || "").trim();
    if (sv) return sv.replace(/^wss?:\/\//i, "").replace(/\/.*$/, "");
  } catch (e) {}
  return location.host;
}

function connect(onOpen) {
  connTip.textContent = "连接服务器中…";
  if (onOpen) _reJoin = onOpen;
  ws = new WebSocket("ws://" + wsTarget() + "/ws");
  var pendingJoin = onOpen;   // 等 hello（含签名挑战）到达后再执行加入，避免竞态
  ws.onopen = function () { connTip.textContent = ""; };
  ws.onclose = function () {
    ws = null;
    if (bannedInfo) return;      // 被封禁/被移出：不再自动重连
    if (_retryTimer) return;
    _retryN++;
    var delay = Math.min(8000, 400 * _retryN);
    connTip.textContent = "与服务器断开，正在自动重连…（第 " + _retryN + " 次）";
    _retryTimer = setTimeout(function () {
      _retryTimer = null;
      joined = false;
      connect(_reJoin);
    }, delay);
  };
  ws.onmessage = function (e) {
    var m;
    try { m = JSON.parse(e.data); } catch (err) { return; }
    if (m.t === "hello") {
      // 服务器告知身份：host=true 表示本机是主机（可修改房间设置）
      // 注意：不回填 AI 数量/强度，避免覆盖用户刚选好的值
      window._isHost = !!m.host;
      window._serverUrls = m.urls || [];
      window._challenge = m.ch;
      if (m.map) applyMap(m.map);
      if (pendingJoin) { var fj = pendingJoin; pendingJoin = null; fj(); }
      if (!m.host) {
        // 非管理员：精简开始界面（无人机设置，只有名字/队伍/加入）
        document.body.classList.add("guest");
        document.getElementById("joinBtn").textContent = "加入实验";
      } else {
        document.body.classList.remove("guest");
        document.getElementById("hostTag").textContent = "· 你是主机";
      }
    } else if (m.t === "kick") {
      bannedInfo = { kind: "kick", why: m.why || "连接被服务器拒绝" };
      connTip.textContent = bannedInfo.why;
      joined = false; myId = null; mouse.down = false;
      try { ws.close(); } catch (err) {}
    } else if (m.t === "banned") {
      // 房主封禁：整屏变黑 + 封禁提示 + 社区公约（不再自动重连）
      bannedInfo = { kind: m.kind === "ip" ? "ip" : "id", name: m.name || "",
                     ip: m.ip || "", why: m.why || "" };
      joined = false; myId = null; mouse.down = false;
      startEl.classList.add("hidden");
      try { ws.close(); } catch (err) {}
    } else if (m.t === "joined") {
      myId = m.id; joined = true;
      _wasRetry = _retryN > 0;
      _retryN = 0;
      startEl.classList.add("hidden");
      sfx("spawn");
      if (_wasRetry) {
        hintMsg = "已重新连接服务器，战绩已恢复";
        hintT = performance.now() + 2600;
      }
      if (window._isHost) {
        hintMsg = "你是主机：按 B 打开管理面板";
        hintT = performance.now() + 3200;
      }
      if (m.tut && typeof TUT !== "undefined" && TUT && typeof TUT.begin === "function") {
        TUT.begin();                       // 兼容旧协议（现在教程走 tut_start）
        hintMsg = "";
      }
    } else if (m.t === "tut_start") {
      // 服务端把该 IP 送进"独立教程小局"：独立小地图、只有人机靶子、看不到其他玩家
      resetWorldState();
      if (m.map) applyMap(m.map);
      myId = m.id; joined = true;
      _wasRetry = false; _retryN = 0;
      startEl.classList.add("hidden");
      cam.scale = fovScale(VW, VH);
      if (typeof TUT !== "undefined" && TUT && typeof TUT.begin === "function") TUT.begin();
      sfx("spawn");
    } else if (m.t === "map") {
      // 服务端显式切图（教程小地图 ↔ 对战大地图）
      if (m.map) applyMap(m.map);
      cam.scale = fovScale(VW, VH);
      snapPrev = snapCur = null;
      selfSmooth = null;
      hintMsg = "地图：" + ((m.map && m.map.name) || "");
      hintT = performance.now() + 2200;
    } else if (m.t === "s") {
      applySnapshot(m);
    }
  };
}
function applySnapshot(m) {
  var newPlayers = {};
  var i, p;
  for (i = 0; i < m.ps.length; i++) {
    p = m.ps[i];
    var old = players[p.id];
    if (old && !old.al && p.al) sfxDistance("spawn", p, 0.5);   // 有人复活
    newPlayers[p.id] = p;
  }
  // 检测自己开枪/干枪（用事件，见下）
  players = newPlayers;
  bullets = m.bs;
  scores = m.sc;
  eventInfo = (m.evt === undefined) ? eventInfo : m.evt;
  bossContrib = m.bct || [];
  if (m.boss && (!bossResult || m.boss.at !== bossResult.at)) {
    bossResult = m.boss;
    bossResultT = performance.now() + 9000;
  }
  // 从玩家列表里找 Boss（bos=1），供 HUD 画大血条
  bossInfo = null;
  for (var bi = 0; bi < m.ps.length; bi++) {
    if (m.ps[bi].bos) { bossInfo = m.ps[bi]; break; }
  }
  offline = m.off || [];       // 已退出玩家（榜单显示战绩 + "不在线"）
  if (typeof m.ms === "number") mapSwitchIn = m.ms;    // 换图倒计时（全端显示）
  if (m.cfg) chemCfg = m.cfg;
  smokes = m.sk || [];
  bufferZones = m.bz || {};
  if (m.bans) banList = m.bans;
  updateParty(eventInfo);

  // 记录快照供插值渲染
  var _arr = performance.now();
  if (lastSnapArrive) {
    var _iv = _arr - lastSnapArrive;
    if (_iv > 2 && _iv < 500) snapIvMs += (_iv - snapIvMs) * 0.2;   // 快照间隔 EMA
  }
  lastSnapArrive = _arr;
  snapPrev = snapCur;
  var curSnap = { t: performance.now(), ps: {}, bullets: m.bs };
  for (i = 0; i < m.ps.length; i++) curSnap.ps[m.ps[i].id] = m.ps[i];
  snapCur = curSnap;

  // 击杀播报（按 id 增量合并）
  var seen = {};
  for (i = 0; i < m.feed.length; i++) {
    var f = m.feed[i];
    seen[f.id] = true;
    if (!feed[f.id]) feed[f.id] = { k: f.k, kt: f.kt, v: f.v, vt: f.vt, w: f.w,
                                    cause: f.cause, ph: f.ph, t: performance.now() };
  }
  for (var fid in feed) if (!seen[fid]) delete feed[fid];

  // 事件
  var now = performance.now();
  for (i = 0; i < m.ev.length; i++) {
    var ev = m.ev[i];
    if (ev.t === "shoot") {
      var shooter = players[ev.pid];
      if (ev.pid !== myId) sfxDistance(ev.w, ev, 1);
      spawnMuzzle(ev.x, ev.y, shooter ? shooter.a : 0, ev.w);
    } else if (ev.t === "hit" && ev["for"] === myId) {
      hitMarker = 0.18; hitMarkerKill = ev.kill;
      sfx("hit");
      spawnBlood(ev.x, ev.y, ev.kill ? 22 : 8, CH_COLOR[ev.ch]);
      var phTxt = (ev.ph === null || ev.ph === undefined) ? "" : "pH " + Number(ev.ph).toFixed(2);
      dmgTexts.push({ x: ev.x, y: ev.y - 26, txt: String(ev.d), sub: phTxt,
                      life: 0.9, max: 0.9, kill: ev.kill, ch: ev.ch });
      shake += ev.kill ? 7 : 2;
    } else if (ev.t === "dmg" && ev["for"] === myId) {
      redFlash = Math.min(0.7, redFlash + 0.35);
      // 化学状态告警（缓冲快耗尽 / pH 接近死线）
      if (ev.buf !== null && ev.buf !== undefined && ev.buf < 15 &&
          performance.now() - (window._lowBufWarnT || 0) > 2600) {
        window._lowBufWarnT = performance.now();
        hintMsg = "缓冲环即将耗尽：下一发可能直接击穿";
        hintT = performance.now() + 1600;
      }
      shake += 5;
      // 受击方向指示（CS2 风格：屏幕中心向攻击者方向的红弧）
      if (ev.ax !== null && ev.ax !== undefined && ev.ay !== null && ev.ay !== undefined) {
        var meDir = players[myId];
        if (meDir) {
          dmgDirs.push({ ang: Math.atan2(ev.ay - meDir.y, ev.ax - meDir.x), t: performance.now() });
          if (dmgDirs.length > 8) dmgDirs.shift();
        }
      }
    } else if (ev.t === "kill") {
      var hvtGuard = now < (window._hvtGuardUntil || 0);
      if (ev.hv) {
        // 击杀高价值目标：全局特殊播报（含击杀者获得分数）
        if (!hvtGuard) {
          banner = "☠ " + ev.kname + " 击杀了高价值目标 " + ev.vname + "！+" + ev.pts + " 分";
          bannerT = now + 3000;
        }
        if (ev.kid === myId) {
          killConfirm = { name: ev.vname, w: ev.w, cause: ev.cause, pts: ev.pts,
                          t: now, hvt: true };
          killImpactT = now;
          sfx("kill");
        }
        hvtTip = null;
      } else if (ev.kid === myId) {
        // 击杀确认卡（ELIMINATED）+ 准心击杀冲击环（移植自 3D 版）
        killConfirm = { name: ev.vname, w: ev.w, cause: ev.cause, pts: ev.pts, t: now };
        killImpactT = now;
        sfx("kill");
      } else if (ev.vid === myId) {
        deadInfo = { killer: ev.kname, w: ev.w, cause: ev.cause, ph: ev.ph };
        sfx("death");
        shake += 10;
      } else {
        sfxDistance(ev.w === "knife" ? "knife" : "death", { x: ev.x || 0, y: ev.y || 0 }, 0.4);
      }
    } else if (ev.t === "neutralize") {
      // 中和救援：绿色飘字（与伤害区分）
      dmgTexts.push({ x: ev.x, y: ev.y - 30, txt: "中和 +" + Math.round(ev.heal || 0),
                      sub: (ev.ph === null || ev.ph === undefined) ? "" : "pH " + Number(ev.ph).toFixed(2),
                      life: 1.2, max: 1.2, kill: false, ch: "neutralize" });
      if (ev.vid === myId) {
        hintMsg = "队友中和了你的体系：pH " + ev.ph;
        hintT = performance.now() + 1800;
      }
      if (ev.pid === myId) sfx("hit");
    } else if (ev.t === "restore") {
      // 维C 支援：队友缓冲被修复
      dmgTexts.push({ x: ev.x, y: ev.y - 30, txt: "缓冲修复 +" + Math.round(ev.heal || 0),
                      sub: (ev.ph === null || ev.ph === undefined) ? "" : "pH " + Number(ev.ph).toFixed(2),
                      life: 1.2, max: 1.2, kill: false, ch: "reducing" });
      if (ev.vid === myId) { hintMsg = "队友用维C修复了你的缓冲：" + Math.round(ev.heal || 0) + "%"; hintT = performance.now() + 1800; }
    } else if (ev.t === "indicator") {
      hintMsg = "已标记 " + ev.name + "：pH " + ev.ph + " / 缓冲 " + ev.buf + "%";
      hintT = performance.now() + 2200;
      sfx("hvt");
    } else if (ev.t === "indicator_fail") {
      if (ev.pid === myId) { hintMsg = ev.msg; hintT = performance.now() + 1500; }
    } else if (ev.t === "recoil") {
      // 机枪后坐力：自己开火时屏幕抖动 + 准星散布放大
      if (ev.pid === myId) {
        recoilVis = Math.max(recoilVis, Math.min(1, (ev.v || 0) * 9));
        shake += 1.6 + (ev.v || 0) * 8;
      }
    } else if (ev.t === "nuke_block") {
      if (ev.pid === myId) {
        nukeBlockT = now + 2000;
        hintMsg = "特殊事件进行中：氟泄漏被禁用";
        hintT = now + 1800;
      }
    } else if (ev.t === "potato") {
      if (ev.pid === myId) { hintMsg = "山芋在你手上！贴住别人传出去"; hintT = now + 2200; }
      sfxDistance("hvt", ev, 0.5);
    } else if (ev.t === "potato_pass") {
      if (ev.pid === myId) { hintMsg = "你接到了山芋，快传出去！"; hintT = now + 1800; }
      if (ev.from === myId) sfx("hvt");
    } else if (ev.t === "potato_boom") {
      booms.push({ x: ev.x, y: ev.y, t: now, big: 1 });
      spawnSparks(ev.x, ev.y);
      shake += 18;
      sfxDistance("boom", ev, 1.0);
    } else if (ev.t === "chair") {
      if (ev.pid === myId) scorePops.push({ txt: "+" + ev.v + " 抢椅子", t: now, big: false, gold: true });
    } else if (ev.t === "laser") {
      // 常驻激光：按射手刷新光束端点，开火期间一直存在
      laserBeams[ev.own] = { x1: ev.x1, y1: ev.y1, x2: ev.x2, y2: ev.y2,
                             until: now + 250 };
    } else if (ev.t === "boom") {
      booms.push({ x: ev.x, y: ev.y, t: now });
      spawnSparks(ev.x, ev.y);
      sfxDistance("boom", ev, 0.7);
    } else if (ev.t === "skill_on" && ev.pid === myId) {
      hintMsg = "⚡ AOE 弹药生效 " + Math.round(ev.dur) + " 秒！";
      hintT = now + 2000;
    } else if (ev.t === "chat") {
      chatMsgs.push({ name: ev.name, team: ev.team, msg: ev.msg, t: now });
      if (chatMsgs.length > 8) chatMsgs.shift();
    } else if (ev.t === "supply") {
      if (ev.pid === myId) { hintMsg = "弹药已补充（补给点 10s 冷却）"; hintT = now + 1500; }
      dmgTexts.push({ x: ev.x, y: ev.y - 24, txt: "补弹", sub: "", life: 0.8, max: 0.8, kill: false, ch: "neutral" });
    } else if (ev.t === "announce") {
      // 随机事件/Boss 播报：只在"事件面板"里显示（标题 + 说明），不再叠加中间大横幅
      announceInfo = { title: ev.title || "", desc: ev.desc || "", t: now + 4200 };
      sfx("hvt");
    } else if (ev.t === "boss_dead") {
      bossResult = { boss: (bossResult && bossResult.boss) || "BOSS", by: ev.by,
                     top: ev.top || [], at: now };
      bossResultT = now + 9000;
      if (typeof sfx === "function") sfx("nuke_boom");
    } else if (ev.t === "bosshit") {
      // Boss 护盾被击碎的特效字
      dmgTexts.push({ x: ev.x, y: ev.y - 30, txt: String(ev.d),
                      sub: "", life: 0.6, max: 0.6, kill: false, ch: "oxidizing" });
    } else if (ev.t === "map") {
      applyMap(ev.map);
      banner = "MAP // " + ((ev.map && ev.map.name) || "NEW SECTOR");
      bannerT = now + 2600;
      hintMsg = "地图切换，战绩保留；找补给点补弹";
      hintT = now + 2600;
    } else if (ev.t === "hvt") {
      // 与击杀播报同款大字横幅；加 900ms 保护期，避免紧随其后的击杀播报把它顶掉
      window._hvtGuardUntil = now + 900;
      banner = "⚠ " + ev.name + " 成为高价值目标！"; bannerT = now + 3000;
      hvtTip = ev.name;
      sfx("hvt");
    } else if (ev.t === "moved") {
      hintMsg = "管理员将 " + ev.name + " 移至" + (ev.team === "red" ? "红" : "蓝") + "队";
      hintT = now + 2500;
    } else if (ev.t === "power") {
      // 能力调整只有房主可见，其他玩家（含被赋能者）无感知
      if (window._isHost) {
        hintMsg = ev.pw === "none" ? ("已收回 " + ev.name + " 的能力")
                                   : ("已赋予 " + ev.name + "【" + ev.pn + "】");
        hintT = now + 2500;
      }
    } else if (ev.t === "nuke_start") {
      nukeInfo = { name: ev.name, team: ev.team, end: now + ev.cd * 1000 };
      banner = "F₂ " + ev.name + " 引发了氟泄漏！"; bannerT = now + 2500;
      sfx("nuke_siren");
    } else if (ev.t === "nuke_boom") {
      nukeInfo = null;
      nukeFlash = 1;
      shake += 34;
      sfx("nuke_boom");
    } else if (ev.t === "score" && ev["for"] === myId) {
      // 战地/三角洲大战场式积分反馈（右侧堆叠浮动）
      var lbl;
      if (ev.reason === "kill") lbl = "+" + ev.pts + " 击杀" + (ev.n >= 2 ? " ×" + ev.n : "");
      else if (ev.reason === "assist") lbl = "+" + ev.pts + " 助攻";
      else lbl = "+" + ev.pts;
      scorePops.push({ txt: lbl, t: now, big: ev.reason === "kill", gold: ev.reason === "assist" });
      if (ev.reason !== "kill") sfx("score");
    } else if (ev.t === "streak" && ev["for"] === myId) {
      streakText = STREAK_NAMES[Math.min(ev.n, 7)] || STREAK_NAMES[7];
      streakT = now + 2000;
    }
  }
  // 自己的死亡/复活状态 + 脱战回血检测
  var me = players[myId];
  if (me) {
    if (me.al && deadInfo) { deadInfo = null; sfx("spawn"); }
    if (!me.al && deadInfo) deadInfo.resp = me.resp;
    if (me.al) {
      if (window._prevHp !== undefined && me.hp > window._prevHp + 0.5) window._regenT = now;
      window._prevHp = me.hp;
    } else {
      window._prevHp = undefined;
    }
  }
}

function sfxDistance(name, pos, base) {
  var me = players[myId];
  if (!me || !pos) return;
  var d = Math.hypot(pos.x - me.x, pos.y - me.y);
  var v = Math.max(0, 1 - d / 1100) * base;
  sfx(name, v);
}

// ================= 粒子 =================
var MAX_PARTICLES = 380;   // 性能保护：粒子超限后不再生成
function spawnBlood(x, y, n, color) {
  if (particles.length > MAX_PARTICLES) return;
  for (var i = 0; i < n; i++) {
    var a = Math.random() * Math.PI * 2, sp = 40 + Math.random() * 160;
    particles.push({
      x: x, y: y, vx: Math.cos(a) * sp, vy: Math.sin(a) * sp,
      life: 0.35 + Math.random() * 0.3, max: 0.6,
      size: 2 + Math.random() * 3, color: color || "#c0392b"
    });
  }
}
function spawnMuzzle(x, y, ang, weapon) {
  if (particles.length > MAX_PARTICLES) return;
  var color = CH_COLOR[weaponChannel(weapon)] || "#ffd166";
  for (var i = 0; i < 6; i++) {
    var a = ang + (Math.random() - 0.5) * 0.7, sp = 80 + Math.random() * 220;
    particles.push({
      x: x + Math.cos(ang) * 22, y: y + Math.sin(ang) * 22,
      vx: Math.cos(a) * sp, vy: Math.sin(a) * sp,
      life: 0.08 + Math.random() * 0.1, max: 0.18,
      size: 2 + Math.random() * 2.4, color: color
    });
  }
}
function spawnSparks(x, y) {
  if (particles.length > MAX_PARTICLES) return;
  for (var i = 0; i < 4; i++) {
    var a = Math.random() * Math.PI * 2, sp = 30 + Math.random() * 100;
    particles.push({
      x: x, y: y, vx: Math.cos(a) * sp, vy: Math.sin(a) * sp,
      life: 0.15, max: 0.15, size: 1.5, color: "#95a5a6"
    });
  }
}

// ================= 输入 =================
addEventListener("keydown", function (e) {
  if (e.target && e.target.id === "chatInput") return;   // 聊天输入时不碰游戏按键
  if (e.code === "Tab") { e.preventDefault(); showBoard = true; return; }
  if (e.code === "KeyH") { e.preventDefault(); showHelp = !showHelp; return; }
  keys[e.code] = true;
  if (e.code === "Enter" && joined) {
    e.preventDefault();
    var ci = document.getElementById("chatInput");
    if (ci) { ci.classList.add("show"); ci.focus(); }
    return;
  }
  if (joined && ws && ws.readyState === 1) {
    if (WKEYS[e.code]) ws.send(JSON.stringify({ t: "sw", w: WKEYS[e.code] }));
    if (e.code === "KeyR") {
      var meR = players[myId];
      if (meR && WEAPONS[meR.w] && WEAPONS[meR.w].noreload) {
        hintMsg = "该试剂为固定携带量，无法换弹";
        hintT = performance.now() + 1600;
      } else { ws.send(JSON.stringify({ t: "rl" })); sfx("reload"); }
    }
    if (e.code === "KeyE") trySmokeAim();
    if (e.code === "KeyF" && joined) {
      var meF = players[myId];
      if (meF && meF.indn > 0 && meF.indcd <= 0) ws.send(JSON.stringify({ t: "indicator" }));
      else if (meF && meF.indn <= 0) {
        hintMsg = "指示剂已用完（每条命 2 次）"; hintT = performance.now() + 1400;
      } else {
        hintMsg = "指示剂冷却中 " + Math.ceil(meF ? meF.indcd : 0) + " 秒";
        hintT = performance.now() + 1200;
      }
    }
    if (e.code === "KeyQ" && joined) {
      var meQ = players[myId];
      if (meQ && meQ.acd <= 0) {
        ws.send(JSON.stringify({ t: "skill" }));
        sfx("skill");
      } else {
        hintMsg = "AOE 技能冷却中 " + Math.ceil(meQ ? meQ.acd : 0) + " 秒";
        hintT = performance.now() + 1200;
      }
    }
    if (e.code === "KeyN" && joined) {
      var meN = players[myId];
      if (meN && meN.nk_ok) ws.send(JSON.stringify({ t: "nuke" }));
      else { hintMsg = "积分未达到下一个 3 万档位，还不能释放氟泄漏"; hintT = performance.now() + 1500; }
    }
    if (e.code === "KeyB" && window._isHost) showAdmin = !showAdmin;   // 主机开合管理面板
    if (e.code === "Escape" && smokeAim) smokeAim = false;
  }
});
addEventListener("keyup", function (e) {
  if (e.target && e.target.id === "chatInput") return;
  if (e.code === "Tab") { showBoard = false; return; }
  keys[e.code] = false;
});
addEventListener("blur", function () { keys = {}; mouse.down = false; });
addEventListener("mousemove", function (e) { mouse.x = e.clientX; mouse.y = e.clientY; });
addEventListener("mousedown", function (e) {
  initAudio();
  // 新手教程面板优先：点在面板上不要打出子弹（tutorial.js 自己也拦了一层，这里是显式双保险）
  if (typeof TUT !== "undefined" && TUT && TUT.active && typeof TUT.click === "function"
      && TUT.click(e.clientX, e.clientY)) return;
  // 烟雾弹选点模式：左键投放 / 右键取消
  if (smokeAim) {
    if (e.button === 2) { smokeAim = false; return; }
    if (e.button === 0) placeSmokeAt(e.clientX, e.clientY);
    return;
  }
  // 主机管理面板按钮优先（不触发射击）
  if (joined && window._isHost && adminPanelClick(e.clientX, e.clientY)) return;
  if (e.button === 0) {
    mouse.down = true;
    var me = players[myId];
    if (me && me.al && me.w !== "knife") {
      var now = performance.now();
      if (now - lastFireTime > 90) { lastFireTime = now; }
    }
  }
  if (e.button === 2) mouse.right = true;
});
addEventListener("mouseup", function (e) {
  if (e.button === 0) mouse.down = false;
  if (e.button === 2) mouse.right = false;
});
addEventListener("contextmenu", function (e) { e.preventDefault(); });

// 每 40ms 上报输入（PC 键鼠；手机端由 mobile.js 独立上报）
setInterval(function () {
  if (window.__MOBILE__) return;
  if (!joined || !ws || ws.readyState !== 1) return;
  var me0 = players[myId];
  var wm = screenToWorld(mouse.x, mouse.y);
  ws.send(JSON.stringify({
    t: "in",
    k: [keys.KeyW ? 1 : 0, keys.KeyD ? 1 : 0, keys.KeyS ? 1 : 0, keys.KeyA ? 1 : 0],
    ax: wm.x, ay: wm.y,
    s: !!mouse.down,
    z: (mouse.right && me0 && me0.w === "sniper") ? 1 : 0
  }));
}, 40);

// ================= 坐标换算 =================
function screenToWorld(sx, sy) {
  // cam.x/cam.y 是视野左上角（不是中心），直接除以缩放再加偏移
  return {
    x: sx / cam.scale + cam.x,
    y: sy / cam.scale + cam.y
  };
}

// ================= 渲染 =================
var lastFrame = performance.now();
requestAnimationFrame(render);   // 先注册 render，触屏 HUD 在其后绘制（覆盖在上层）

// 静态地图层：地板+网格+出生区+墙体 只画一次缓存（性能优化核心项）
var mapLayer = null;
function buildMapLayer() {
  mapLayer = document.createElement("canvas");
  mapLayer.width = Math.floor(WORLD_W * DPR);
  mapLayer.height = Math.floor(WORLD_H * DPR);
  var c = mapLayer.getContext("2d");
  c.scale(DPR, DPR);
  // 实验室暗色地板
  c.fillStyle = "#0b1015"; c.fillRect(0, 0, WORLD_W, WORLD_H);
  // 细网格
  c.strokeStyle = "rgba(255,255,255,0.028)"; c.lineWidth = 1;
  c.beginPath();
  for (var x = 100; x < WORLD_W; x += 100) { c.moveTo(x, 0); c.lineTo(x, WORLD_H); }
  for (var y = 100; y < WORLD_H; y += 100) { c.moveTo(0, y); c.lineTo(WORLD_W, y); }
  c.stroke();
  // 出生实验区 / 补给点 / 地图标签（由服务器地图数据驱动）
  var sp = ACTIVE_MAP && ACTIVE_MAP.spawn;
  if (sp) {
    var zr = sp.red, zb = sp.blue;
    if (zr) {
      c.fillStyle = "rgba(255,82,82,0.055)";
      c.fillRect(zr[0], zr[1], zr[2], zr[3]);
      c.strokeStyle = "rgba(255,82,82,0.18)"; c.lineWidth = 1;
      c.strokeRect(zr[0] + 0.5, zr[1] + 0.5, zr[2] - 1, zr[3] - 1);
      c.fillStyle = "rgba(255,82,82,0.22)";
      c.font = "600 30px 'JetBrains Mono', Consolas, monospace";
      c.textAlign = "left";
      c.fillText("RED / 红区", zr[0] + 16, zr[1] + 60);
    }
    if (zb) {
      c.fillStyle = "rgba(77,166,255,0.055)";
      c.fillRect(zb[0], zb[1], zb[2], zb[3]);
      c.strokeStyle = "rgba(77,166,255,0.18)"; c.lineWidth = 1;
      c.strokeRect(zb[0] + 0.5, zb[1] + 0.5, zb[2] - 1, zb[3] - 1);
      c.fillStyle = "rgba(77,166,255,0.22)";
      c.font = "600 30px 'JetBrains Mono', Consolas, monospace";
      c.textAlign = "left";
      c.fillText("BLUE / 蓝区", zb[0] + 16, zb[1] + 60);
    }
  }
  if (ACTIVE_MAP && ACTIVE_MAP.ammo) {
    for (var ai = 0; ai < ACTIVE_MAP.ammo.length; ai++) {
      var ap = ACTIVE_MAP.ammo[ai];
      c.strokeStyle = "rgba(255,200,87,0.55)"; c.lineWidth = 2;
      c.beginPath(); c.arc(ap[0], ap[1], ap[2], 0, Math.PI * 2); c.stroke();
      c.fillStyle = "rgba(255,200,87,0.10)";
      c.beginPath(); c.arc(ap[0], ap[1], ap[2], 0, Math.PI * 2); c.fill();
      c.fillStyle = "rgba(255,200,87,0.8)";
      c.font = "700 16px 'JetBrains Mono', Consolas, monospace";
      c.textAlign = "center";
      c.fillText("+", ap[0], ap[1] + 6);
    }
  }
  if (ACTIVE_MAP && ACTIVE_MAP.labels) {
    c.font = "600 18px 'JetBrains Mono', Consolas, monospace";
    c.textAlign = "left";
    for (var li = 0; li < ACTIVE_MAP.labels.length; li++) {
      var lb = ACTIVE_MAP.labels[li];
      c.fillStyle = lb[3] || "rgba(255,255,255,0.25)";
      c.fillText(lb[0], lb[1], lb[2]);
    }
  }
  // 墙体：深色金属 + 顶面高光
  for (var i = 0; i < WALLS.length; i++) {
    var w = WALLS[i];
    var wr = Math.min(10, w[2] / 2, w[3] / 2);
    c.fillStyle = "#1a222b";
    rrPath(c, w[0], w[1], w[2], w[3], wr); c.fill();
    c.fillStyle = "#26313d";
    rrPath(c, w[0], w[1], w[2], Math.min(4 + wr, w[3]), wr); c.fill();
    c.strokeStyle = "rgba(255,255,255,0.05)"; c.lineWidth = 1;
    rrPath(c, w[0] + 0.5, w[1] + 0.5, w[2] - 1, w[3] - 1, wr); c.stroke();
    c.strokeStyle = "rgba(0,0,0,0.5)";
    rrPath(c, w[0] + 1.5, w[1] + 1.5, w[2] - 3, w[3] - 3, Math.max(2, wr - 1)); c.stroke();
  }
}

// 离屏画布专用圆角路径（不依赖主 ctx 的 roundRect）
function rrPath(c, x, y, w, h, r) {
  c.beginPath();
  c.moveTo(x + r, y);
  c.arcTo(x + w, y, x + w, y + h, r);
  c.arcTo(x + w, y + h, x, y + h, r);
  c.arcTo(x, y + h, x, y, r);
  c.arcTo(x, y, x + w, y, r);
  c.closePath();
}

function render(now) {
  requestAnimationFrame(render);
  var dt = Math.min(0.05, (now - lastFrame) / 1000);
  // 帧耗时统计：HUD 显示 + 自动降画质（弱机器上先把 DPR 降下来）
  if (lastFrame > 0) {
    var _fdt = now - lastFrame;
    frameMs += (_fdt - frameMs) * 0.12;
    if (_fdt > 40) jankCount++;
  }
  qualityTick(now);
  if (!window.__MOBILE__) updateCursor();
  lastFrame = now;

  // 清屏：窗口变大/世界边界外不能残留旧画面
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.fillStyle = "#070a0e";
  ctx.fillRect(0, 0, cv.width, cv.height);

  // 相机（跟随插值后的平滑位置，逻辑状态用原始数据）
  var meRaw = players[myId];
  var display = getDisplayPlayers(now);
  smoothSelf(display, now, dt);          // 自己的位置不走插值缓冲，降低"不跟手"
  var me = display[myId] || meRaw;
  // 世界始终铺满视口：视野不得超出地图（否则露黑边）
  var coverScale = Math.max(VW / WORLD_W, VH / WORLD_H);
  var targetScale = fovScale(VW, VH) * ((meRaw && meRaw.w === "sniper" && mouse.right) ? 1.6 : 1);
  cam.scale += (targetScale - cam.scale) * 0.15;
  var viewW = VW / cam.scale, viewH = VH / cam.scale;
  var fx, fy;
  if (me) { fx = me.x; fy = me.y; } else { fx = WORLD_W / 2; fy = WORLD_H / 2; }
  cam.x = Math.max(0, Math.min(WORLD_W - viewW, fx - viewW / 2));
  cam.y = Math.max(0, Math.min(WORLD_H - viewH, fy - viewH / 2));
  if (viewW > WORLD_W) cam.x = (WORLD_W - viewW) / 2;
  if (viewH > WORLD_H) cam.y = (WORLD_H - viewH) / 2;

  shake = Math.max(0, shake - dt * 40);
  var sx = (Math.random() - 0.5) * shake, sy = (Math.random() - 0.5) * shake;
  ctx.setTransform(DPR * cam.scale, 0, 0, DPR * cam.scale,
                   (-cam.x * cam.scale + sx) * DPR, (-cam.y * cam.scale + sy) * DPR);

  // 静态地图（地板/网格/出生区/墙体）一次 drawImage，替代每帧几十次绘制
  if (!mapLayer) buildMapLayer();
  ctx.drawImage(mapLayer, 0, 0, WORLD_W, WORLD_H);
  // 出生点缓冲地带（地面上，人物之下）
  drawBufferZones(now);

  // 机枪常驻激光（开火期间光束一直存在，按射手刷新）
  for (var lid in laserBeams) {
    var L = laserBeams[lid];
    if (L.until < now) { delete laserBeams[lid]; continue; }
    var own = +lid === myId;
    ctx.strokeStyle = own ? "rgba(120,255,170,0.35)" : "rgba(255,80,80,0.40)";
    ctx.lineWidth = 4;
    ctx.beginPath(); ctx.moveTo(L.x1, L.y1); ctx.lineTo(L.x2, L.y2); ctx.stroke();
    ctx.strokeStyle = own ? "rgba(225,255,235,0.85)" : "rgba(255,200,200,0.9)";
    ctx.lineWidth = 1.4;
    ctx.beginPath(); ctx.moveTo(L.x1, L.y1); ctx.lineTo(L.x2, L.y2); ctx.stroke();
  }

  // 子弹曳光：按试剂通道 + 武器定制
  for (var i = 0; i < bullets.length; i++) {
    var b = bullets[i];
    var bch = (b[4] | 0), bwd = (b[5] | 0);
    var st = BULLET_STYLE[bch] || BULLET_STYLE[0];
    var blen = st.len, bwid = st.width;
    if (bwd === 3) { blen = 78; bwid = 6.0; }        // 狙击：长亮弹道
    else if (bwd === 1) { blen = 34; bwid = 3.2; }   // 硅酸：短促
    else if (bwd === 4) { blen = 30; bwid = 3.4; }   // 喷淋器：短紫雾
    // 外层柔光
    ctx.strokeStyle = st.glow;
    ctx.lineWidth = bwid + 3;
    ctx.beginPath();
    ctx.moveTo(b[0] - b[2] * blen, b[1] - b[3] * blen);
    ctx.lineTo(b[0], b[1]);
    ctx.stroke();
    // 内芯
    ctx.strokeStyle = st.core;
    ctx.lineWidth = Math.max(1.2, bwid * 0.5);
    ctx.beginPath();
    ctx.moveTo(b[0] - b[2] * blen, b[1] - b[3] * blen);
    ctx.lineTo(b[0], b[1]);
    ctx.stroke();
    // 弹头
    ctx.fillStyle = st.head;
    ctx.beginPath();
    ctx.arc(b[0], b[1], bwd === 3 ? 3.0 : (bwd === 4 ? 2.0 : 2.3), 0, Math.PI * 2);
    ctx.fill();
  }

  // 烟雾（画在人物之下层，配合可见性过滤实现"烟内互见/内外不互见"）
  drawWorldSmokes(now);
  // 派对事件场景（山芋引信/岩浆安全圈/椅子）
  drawPartyWorld(now);

  // 玩家（用插值后的平滑位置绘制；被烟雾遮挡的不绘制）
  for (var pid in display) {
    var p = display[pid];
    if (!p.al) continue;
    if (+pid !== myId && meRaw && smokeBlocks(meRaw.x, meRaw.y, p.x, p.y)) continue;
    var col = TEAM_COLOR[p.team];
    var isMe = +pid === myId;
    var isEnemy = !isMe && meRaw && p.team !== meRaw.team;
    // 出生点隐身：站在己方出生区的敌人不渲染（子弹仍可命中，走出区域即现身）
    if (isEnemy && p.sg) continue;
    // 隐身能力：对敌完全隐身（无虚影），子弹仍可命中
    if (isEnemy && p.pw === "ghost") continue;

    // 阴影
    ctx.fillStyle = "rgba(0,0,0,0.3)";
    ctx.beginPath(); ctx.ellipse(p.x, p.y + 12, 14, 6, 0, 0, Math.PI * 2); ctx.fill();
    // 枪管：按试剂通道着色（酸橙/碱青/氧化紫/物理灰）
    var chColor = p.w === "shotgun" ? "#b07cff"
                : p.w === "lmg" ? "#39c7d8"
                : (p.w === "knife" || p.w === "sniper" ? (p.w === "knife" ? "#8291a1" : "#ff6b35") : "#ff6b35");
    var gl = p.w === "sniper" ? 34 : (p.w === "lmg" ? 30 : 26);
    var gx2 = p.x + Math.cos(p.a) * gl, gy2 = p.y + Math.sin(p.a) * gl;
    ctx.lineCap = "round";
    ctx.strokeStyle = "#0a0d11"; ctx.lineWidth = 7;
    ctx.beginPath(); ctx.moveTo(p.x, p.y); ctx.lineTo(gx2, gy2); ctx.stroke();
    ctx.strokeStyle = chColor; ctx.lineWidth = 3;
    ctx.beginPath(); ctx.moveTo(p.x, p.y); ctx.lineTo(gx2, gy2); ctx.stroke();
    ctx.fillStyle = "#ffc857";
    ctx.beginPath(); ctx.arc(gx2, gy2, 2.4, 0, Math.PI * 2); ctx.fill();
    ctx.lineCap = "butt";
    // 身体：队色 + 内层高光 + 外环
    ctx.fillStyle = col;
    ctx.beginPath(); ctx.arc(p.x, p.y, 16, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = "rgba(255,255,255,0.16)";
    ctx.beginPath(); ctx.arc(p.x, p.y, 8.5, 0, Math.PI * 2); ctx.fill();
    if (isMe) {
      ctx.strokeStyle = "#ffffff"; ctx.lineWidth = 2.5;
      ctx.beginPath(); ctx.arc(p.x, p.y, 19, 0, Math.PI * 2); ctx.stroke();
      ctx.strokeStyle = "rgba(255,255,255,0.18)"; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.arc(p.x, p.y, 23, 0, Math.PI * 2); ctx.stroke();
    } else {
      ctx.strokeStyle = "rgba(0,0,0,0.45)"; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.arc(p.x, p.y, 16, 0, Math.PI * 2); ctx.stroke();
      ctx.strokeStyle = "rgba(255,255,255,0.10)"; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.arc(p.x, p.y, 20, 0, Math.PI * 2); ctx.stroke();
    }
    // 名字（高价值目标金色 + 骷髅标记）
    if (p.hv) {
      ctx.fillStyle = "#ffd166";
      ctx.font = uiFont(11, 700);
      ctx.textAlign = "center";
      ctx.fillText("◆ " + p.n + (p.bot ? " AI" : ""), p.x, p.y - 26);
    } else {
      ctx.fillStyle = isMe ? "#ffffff" : "rgba(255,255,255,0.82)";
      ctx.font = uiFont(11, 600);
      ctx.textAlign = "center";
      ctx.fillText(p.n + (p.bot ? " AI" : ""), p.x, p.y - 26);
    }
    // 能力徽章（仅房主可见，被赋能者与他人均不可见）
    if (window._isHost && p.pw && p.pw !== "none") {
      ctx.font = uiMono(9, 600);
      ctx.fillStyle = "#7dffb0";
      ctx.fillText("[" + (PW_NAME[p.pw] || p.pw) + "]", p.x, p.y - 38);
    }
    // 敌方体系读数：可见敌人的 pH 常显（缓冲环仍隐藏，需 F 指示剂才显示）
    if (!isMe && isEnemy && p.ph !== null && p.ph !== undefined) {
      var epz = phZone(p.ph);
      ctx.font = uiMono(9.5, 600);
      ctx.fillStyle = epz.color;
      ctx.textAlign = "center";
      var eTxt = "pH " + Number(p.ph).toFixed(2);
      if (p.ind > 0) eTxt += " · " + Math.round(p.buf === undefined ? p.hp : p.buf) + "%";
      ctx.fillText(eTxt, p.x, p.y - 38);
    }
    // 队友 pH 提示：只对友军显示，方便判断该用酸还是用碱去“中和救援”
    if (!isMe && meRaw && p.team === meRaw.team && p.ph !== null && p.ph !== undefined &&
        (p.ph < 6.0 || p.ph > 8.0)) {
      var pz = phZone(p.ph);
      ctx.font = uiMono(9.5, 600);
      ctx.fillStyle = pz.color;
      ctx.textAlign = "center";
      ctx.fillText("pH " + Number(p.ph).toFixed(2), p.x, p.y - 38);
    }
    // 指示剂标记的缓冲信息已合并在上面的敌方 pH 标签里
    // 血条
    ctx.fillStyle = "rgba(0,0,0,0.55)";
    ctx.fillRect(p.x - 17, p.y - 22, 34, 5);
    var bufP = (p.buf === null || p.buf === undefined) ? p.hp : p.buf;
    ctx.fillStyle = ringColor(bufP);
    ctx.fillRect(p.x - 16, p.y - 21, 32 * (Math.max(0, Math.min(100, bufP)) / 100), 3);
  }

  // 粒子
  for (i = particles.length - 1; i >= 0; i--) {
    var pt = particles[i];
    pt.life -= dt;
    if (pt.life <= 0) { particles.splice(i, 1); continue; }
    pt.x += pt.vx * dt; pt.y += pt.vy * dt;
    pt.vx *= 0.92; pt.vy *= 0.92;
    ctx.globalAlpha = pt.life / pt.max;
    ctx.fillStyle = pt.color;
    ctx.beginPath(); ctx.arc(pt.x, pt.y, pt.size, 0, Math.PI * 2); ctx.fill();
  }
  ctx.globalAlpha = 1;

  // AOE 爆炸冲击圈
  for (i = booms.length - 1; i >= 0; i--) {
    var bm = booms[i];
    var ba = (now - bm.t) / 380;
    if (ba >= 1) { booms.splice(i, 1); continue; }
    ctx.globalAlpha = 1 - ba;
    var bR = bm.big ? (bm.r || 210) : AOE_CLIENT_R;
    ctx.strokeStyle = bm.big ? "#ff5a3c" : "#ffb347"; ctx.lineWidth = bm.big ? 5 : 3;
    ctx.beginPath(); ctx.arc(bm.x, bm.y, bR * ba, 0, Math.PI * 2); ctx.stroke();
    ctx.strokeStyle = "rgba(255,209,102,0.5)"; ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.arc(bm.x, bm.y, bR * ba * 0.6, 0, Math.PI * 2); ctx.stroke();
  }
  ctx.globalAlpha = 1;

  // 伤害数字
  ctx.textAlign = "center";
  for (i = dmgTexts.length - 1; i >= 0; i--) {
    var dt2 = dmgTexts[i];
    dt2.life -= dt;
    if (dt2.life <= 0) { dmgTexts.splice(i, 1); continue; }
    dt2.y -= dt * 55;
    ctx.globalAlpha = Math.min(1, dt2.life / dt2.max * 1.5);
    ctx.font = uiMono(dt2.kill ? 20 : 15, 700);
    ctx.fillStyle = dt2.kill ? "#ff4d5e" : (CH_COLOR[dt2.ch] || "#ffc857");
    ctx.fillText(dt2.txt, dt2.x, dt2.y);
    if (dt2.sub) {                       // 化学反馈：这次命中把对方 pH 打到多少
      ctx.font = uiMono(9.5, 600);
      ctx.fillStyle = "rgba(255,255,255,0.72)";
      ctx.fillText(dt2.sub, dt2.x, dt2.y + 13);
    }
  }
  ctx.globalAlpha = 1;

  // ===== HUD（虚拟坐标空间，按 DPR×UIS 缩放） =====
  ctx.setTransform(DPR * UIS, 0, 0, DPR * UIS, 0, 0);

  // 狙击开镜效果
  if (me && me.w === "sniper" && mouse.right && me.al) {
    ctx.fillStyle = "rgba(0,0,0,0.92)";
    ctx.beginPath();
    ctx.rect(0, 0, UW, UH);
    ctx.arc(UW / 2, UH / 2, Math.min(UW, UH) * 0.36, 0, Math.PI * 2, true);
    ctx.fill();
    ctx.strokeStyle = "rgba(0,0,0,0.9)";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(UW / 2, 0); ctx.lineTo(UW / 2, UH);
    ctx.moveTo(0, UH / 2); ctx.lineTo(UW, UH / 2);
    ctx.stroke();
  }

  // 红色受击边缘
  redFlash = Math.max(0, redFlash - dt * 1.8);
  var lowHp = meRaw && meRaw.al ? Math.max(0, (35 - meRaw.hp) / 35 * 0.35) : 0;
  var vig = Math.max(redFlash, lowHp);
  if (vig > 0.01) {
    var grad = ctx.createRadialGradient(UW / 2, UH / 2, Math.min(UW, UH) * 0.35, UW / 2, UH / 2, Math.max(UW, UH) * 0.72);
    grad.addColorStop(0, "rgba(200,30,30,0)");
    grad.addColorStop(1, "rgba(200,30,30," + vig.toFixed(3) + ")");
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, UW, UH);
  }

  // 脱战回血呼吸效果（绿色脉冲）
  if (meRaw && meRaw.al && now - (window._regenT || -1e9) < 500) {
    var pulse = (Math.sin(now / 130) + 1) / 2;
    var g2 = ctx.createRadialGradient(UW / 2, UH / 2, Math.min(UW, UH) * 0.38, UW / 2, UH / 2, Math.max(UW, UH) * 0.7);
    g2.addColorStop(0, "rgba(120,255,170,0)");
    g2.addColorStop(1, "rgba(120,255,170," + (0.04 + pulse * 0.09).toFixed(3) + ")");
    ctx.fillStyle = g2;
    ctx.fillRect(0, 0, UW, UH);
  }

  // 核弹倒计时
  if (nukeInfo) {
    var remainN = (nukeInfo.end - now) / 1000;
    if (remainN <= 0) {
      nukeInfo = null;
    } else {
      ctx.textAlign = "center";
      ctx.font = "bold 18px sans-serif";
      ctx.fillStyle = "#ffd166";
      ctx.fillText("F₂ 氟泄漏即将扩散", UW / 2, UH * 0.5 - 116);
      ctx.font = "bold 64px sans-serif";
      ctx.fillStyle = "rgba(176,124,255,0.92)";
      ctx.fillText(String(Math.ceil(remainN)), UW / 2, UH * 0.5 - 62);
    }
  }
  // 核弹白闪
  if (nukeFlash > 0.01) {
    nukeFlash = Math.max(0, nukeFlash - dt * 0.55);
    ctx.fillStyle = "rgba(255,250,235," + Math.min(1, nukeFlash * 1.3).toFixed(3) + ")";
    ctx.fillRect(0, 0, UW, UH);
  }

  if (meRaw && joined) {
    if (window.__MOBILE__) {
      // 手机端：独立紧凑 HUD（不再把 1366×768 的 PC HUD 塞进 768×362）
      if (typeof drawMobileHud === "function") drawMobileHud(meRaw, now);
    } else {
      drawMinimap();
      drawChat(now);
      drawHvtTip();
      drawHUD(meRaw, now);
      drawScorePops(now);
      drawSmokeHud(meRaw);
      drawAdminPanel();      // 主机面板画在 HUD 之上：加长后的封禁名单才不会被状态卡遮住
    }
  }
  drawSmokeAim();

  if (!window.__MOBILE__) {
    // 屏幕提示 / 击杀横幅（新 UI 字体）
    drawHintBanner(now);

    // 准星
    if (joined && meRaw && meRaw.al) drawCrosshair();

    // 连杀文字
    if (streakText && now < streakT) {
      var t = (streakT - now) / 2000;
      ctx.save();
      ctx.translate(UW / 2, UH * 0.42);
      ctx.globalAlpha = Math.min(1, t * 3);
      ctx.scale(1 + (1 - t) * 0.15, 1 + (1 - t) * 0.15);
      ctx.font = uiFont(50, 800);
      ctx.textAlign = "center";
      ctx.strokeStyle = "rgba(0,0,0,0.7)"; ctx.lineWidth = 6;
      ctx.strokeText(streakText, 0, 0);
      ctx.fillStyle = "#ff5252";
      ctx.fillText(streakText, 0, 0);
      ctx.restore();
      ctx.globalAlpha = 1;
    }
  }

  // 死亡界面
  if (deadInfo) {
    if (window.__MOBILE__ && typeof drawMobileDeath === "function") drawMobileDeath(deadInfo, now);
    else drawDeathScreen(deadInfo, now);
  }

  // 战绩板
  if (showBoard) {
    if (window.__MOBILE__ && typeof drawMobileBoard === "function") drawMobileBoard();
    else drawBoard();
  }
  // 主机管理面板（手机端独立绘制，热区写在 mlAdminRects）
  if (window.__MOBILE__ && typeof drawMobileAdmin === "function") drawMobileAdmin();

  // 新手教程层：画在所有 HUD 之上（教程中玩家有服务端新手保护，不会被打死）
  if (typeof TUT !== "undefined" && TUT && typeof TUT.update === "function") {
    TUT.update(dt);
  }

  // 封禁/踢出：黑屏遮罩压在所有层之上（含教程层）
  if (bannedInfo) drawBanScreen(bannedInfo, now);
}

// ================= 烟雾弹 =================
function inSmokeAt(x, y) {
  for (var i = 0; i < smokes.length; i++) {
    var s = smokes[i];
    var dx = x - s[0], dy = y - s[1];
    if (dx * dx + dy * dy < s[2] * s[2]) return s;
  }
  return null;
}
function segNearSmoke(ax, ay, bx, by, s) {
  var dx = bx - ax, dy = by - ay;
  var ln = dx * dx + dy * dy;
  var t = ln < 1 ? 0 : Math.max(0, Math.min(1, ((s[0] - ax) * dx + (s[1] - ay) * dy) / ln));
  var ex = s[0] - (ax + dx * t), ey = s[1] - (ay + dy * t);
  return ex * ex + ey * ey < s[2] * s[2];
}
// 无畏契约规则：同烟内互见；烟内外不互见；视线穿烟被挡
function smokeBlocks(ax, ay, bx, by) {
  var sa = inSmokeAt(ax, ay), sb = inSmokeAt(bx, by);
  if (sa && sb && sa === sb) return false;
  if (sa || sb) return true;
  for (var i = 0; i < smokes.length; i++) {
    if (segNearSmoke(ax, ay, bx, by, smokes[i])) return true;
  }
  return false;
}
function trySmokeAim() {
  var me = players[myId];
  if (!me || !me.al) return;
  if (me.scd > 0) {
    hintMsg = "烟雾弹冷却中 " + Math.ceil(me.scd) + " 秒";
    hintT = performance.now() + 1500;
    return;
  }
  smokeAim = true;
  mouse.down = false;
}
function placeSmokeAt(sx, sy) {
  var ow = 480, oh = 320, ox = VW / 2 - ow / 2, oy = VH / 2 - oh / 2;
  if (sx < ox || sx > ox + ow || sy < oy || sy > oy + oh) { smokeAim = false; return; }
  var wx = (sx - ox) / ow * WORLD_W;
  var wy = (sy - oy) / oh * WORLD_H;
  if (ws && ws.readyState === 1) ws.send(JSON.stringify({ t: "smoke", x: wx, y: wy }));
  smokeAim = false;
  sfx("reload");
}
function drawSmokeAim() {
  if (!smokeAim) return;
  ctx.fillStyle = "rgba(0,0,0,0.6)";
  ctx.fillRect(0, 0, UW, UH);
  var ow = 480, oh = 320, ox = UW / 2 - ow / 2, oy = UH / 2 - oh / 2, sc = ow / WORLD_W;
  ctx.fillStyle = "#23272b";
  ctx.fillRect(ox, oy, ow, oh);
  ctx.strokeStyle = "#ffd166"; ctx.lineWidth = 2;
  ctx.strokeRect(ox, oy, ow, oh);
  ctx.fillStyle = "#465059";
  for (var i = 0; i < WALLS.length; i++) {
    var w = WALLS[i];
    ctx.fillRect(ox + w[0] * sc, oy + w[1] * sc, Math.max(2, w[2] * sc), Math.max(2, w[3] * sc));
  }
  for (i = 0; i < smokes.length; i++) {
    var s2 = smokes[i];
    ctx.fillStyle = "rgba(200,205,210,0.4)";
    ctx.beginPath(); ctx.arc(ox + s2[0] * sc, oy + s2[1] * sc, s2[2] * sc, 0, Math.PI * 2); ctx.fill();
  }
  for (var pid in players) {
    var p = players[pid];
    if (!p.al) continue;
    ctx.fillStyle = +pid === myId ? "#ffffff" : TEAM_COLOR[p.team];
    ctx.beginPath(); ctx.arc(ox + p.x * sc, oy + p.y * sc, 3, 0, Math.PI * 2); ctx.fill();
  }
  var mx = Math.max(ox, Math.min(ox + ow, (mouse.x / UIS)));
  var my = Math.max(oy, Math.min(oy + oh, (mouse.y / UIS)));
  ctx.strokeStyle = "#ffd166"; ctx.lineWidth = 1.5;
  ctx.setLineDash([6, 4]);
  ctx.beginPath(); ctx.arc(mx, my, SMOKE_R * sc, 0, Math.PI * 2); ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle = "#ffd166"; ctx.font = "bold 14px sans-serif"; ctx.textAlign = "center";
  ctx.fillText("左键点击投放烟雾 · 右键取消", UW / 2, oy + oh + 28);
}
function drawWorldSmokes(now) {
  for (var i = 0; i < smokes.length; i++) {
    var s = smokes[i];
    ctx.fillStyle = "rgba(172,177,184,0.82)";
    ctx.beginPath(); ctx.arc(s[0], s[1], s[2], 0, Math.PI * 2); ctx.fill();
    for (var k = 0; k < 5; k++) {
      var ang = k * 1.3 + now / 900;
      ctx.fillStyle = "rgba(205,210,216,0.3)";
      ctx.beginPath();
      ctx.arc(s[0] + Math.cos(ang) * s[2] * 0.35, s[1] + Math.sin(ang) * s[2] * 0.35,
              s[2] * 0.45, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.strokeStyle = "rgba(228,232,238,0.45)"; ctx.lineWidth = 3;
    ctx.beginPath(); ctx.arc(s[0], s[1], s[2], 0, Math.PI * 2); ctx.stroke();
  }
}

// ================= 出生点缓冲地带（服务端 bz） =================
function drawBufferZones(now) {
  var pulse = 0.5 + 0.5 * Math.sin(now / 420);
  var me = players[myId];
  for (var team in bufferZones) {
    var rects = bufferZones[team] || [];
    var col = (team === "red") ? "255,82,82" : "77,166,255";
    for (var i = 0; i < rects.length; i++) {
      var r = rects[i];
      ctx.fillStyle = "rgba(" + col + "," + (0.045 + 0.03 * pulse).toFixed(3) + ")";
      ctx.fillRect(r[0], r[1], r[2], r[3]);
      ctx.save();
      ctx.beginPath(); ctx.rect(r[0], r[1], r[2], r[3]); ctx.clip();
      ctx.strokeStyle = "rgba(" + col + ",0.055)";
      ctx.lineWidth = 12;
      for (var d = -r[3]; d < r[2]; d += 52) {
        ctx.beginPath();
        ctx.moveTo(r[0] + d, r[1] + r[3]);
        ctx.lineTo(r[0] + d + r[3], r[1]);
        ctx.stroke();
      }
      ctx.restore();
      ctx.save();
      ctx.strokeStyle = "rgba(" + col + "," + (0.28 + 0.18 * pulse).toFixed(3) + ")";
      ctx.lineWidth = 2;
      ctx.setLineDash([16, 10]);
      ctx.lineDashOffset = (now / 40) % 26;
      ctx.strokeRect(r[0], r[1], r[2], r[3]);
      ctx.restore();
      var near = me && me.x > r[0] - 140 && me.x < r[0] + r[2] + 140 &&
                 me.y > r[1] - 180 && me.y < r[1] + r[3] + 180;
      if (near) {
        ctx.fillStyle = "rgba(" + col + ",0.60)";
        ctx.font = uiMono(11, 700);
        ctx.textAlign = "center";
        ctx.fillText("缓冲地带 / BUFFER", r[0] + r[2] / 2, r[1] + r[3] / 2);
      }
      // 自己站在敌方缓冲地带：周期性警告
      if (me && me.al && team !== me.team &&
          me.x >= r[0] && me.x <= r[0] + r[2] && me.y >= r[1] && me.y <= r[1] + r[3]) {
        if (now - (window._bufWarnT || 0) > 2600) {
          window._bufWarnT = now;
          hintMsg = "⚠ 进入敌方出生缓冲地带：停留 1.2 秒后持续剥离缓冲";
          hintT = now + 2000;
        }
      }
    }
  }
}

// ================= 小游戏派对事件（烫手山芋/岩浆地板/抢椅子） =================
function updateParty(evt) {
  if (!evt || evt.kind !== "party" || !evt.extra) { party = null; return; }
  var ex = evt.extra;
  if (evt.id === "hot_potato") {
    party = { kind: "potato", x: ex.x, y: ex.y, r: ex.r || 210,
              car: ex.car, fuse: ex.fuse, fmax: ex.fuse_max || 3.8 };
  } else if (evt.id === "lava_floor") {
    party = { kind: "lava", x: ex.x, y: ex.y, r: ex.r || 330 };
  } else if (evt.id === "musical_chairs") {
    party = { kind: "chairs", chairs: ex.chairs || [] };
  } else {
    party = { kind: "other" };
  }
}

function drawPartyWorld(now) {
  if (!party) return;
  if (party.kind === "potato") {
    if (party.car === null || party.car === undefined) return;
    var frac = Math.max(0, Math.min(1, (party.fuse || 0) / (party.fmax || 1)));
    ctx.strokeStyle = "rgba(255,90,60,0.42)";
    ctx.lineWidth = 2;
    ctx.setLineDash([10, 7]);
    ctx.beginPath(); ctx.arc(party.x, party.y, party.r, 0, Math.PI * 2); ctx.stroke();
    ctx.setLineDash([]);
    // 引信环：越少越红
    ctx.strokeStyle = frac > 0.5 ? "#ffb347" : (frac > 0.22 ? "#ff7a3d" : "#ff3b30");
    ctx.lineWidth = 5;
    ctx.beginPath();
    ctx.arc(party.x, party.y, 34, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * frac);
    ctx.stroke();
    ctx.fillStyle = "#ffd166";
    ctx.font = uiMono(11, 800);
    ctx.textAlign = "center";
    ctx.fillText("烫手山芋 " + Math.max(0, party.fuse || 0).toFixed(1) + "s", party.x, party.y - 44);
    if (party.car === myId) {
      ctx.fillStyle = "rgba(255,80,60,0.9)";
      ctx.font = uiMono(13, 800);
      ctx.fillText("山芋在你手上！贴人去传", party.x, party.y + 58);
    }
  } else if (party.kind === "lava") {
    var g = ctx.createRadialGradient(party.x, party.y, party.r * 0.2, party.x, party.y, party.r);
    g.addColorStop(0, "rgba(80,255,160,0.16)");
    g.addColorStop(0.75, "rgba(80,255,160,0.10)");
    g.addColorStop(1, "rgba(80,255,160,0.02)");
    ctx.fillStyle = g;
    ctx.beginPath(); ctx.arc(party.x, party.y, party.r, 0, Math.PI * 2); ctx.fill();
    ctx.strokeStyle = "rgba(120,255,170," + (0.55 + 0.2 * Math.sin(now / 300)).toFixed(2) + ")";
    ctx.lineWidth = 4;
    ctx.beginPath(); ctx.arc(party.x, party.y, party.r, 0, Math.PI * 2); ctx.stroke();
    ctx.fillStyle = "rgba(120,255,170,0.75)";
    ctx.font = uiMono(12, 800);
    ctx.textAlign = "center";
    ctx.fillText("岩浆地板 · 安全圈", party.x, party.y - party.r * 0.5);
  } else if (party.kind === "chairs") {
    for (var i = 0; i < party.chairs.length; i++) {
      var ch = party.chairs[i];
      var occ = ch[2];
      var cxp = ch[0], cyp = ch[1];
      ctx.strokeStyle = occ ? "rgba(255,209,102,0.9)" : "rgba(255,255,255,0.35)";
      ctx.lineWidth = occ ? 3 : 2;
      ctx.beginPath(); ctx.arc(cxp, cyp, 34, 0, Math.PI * 2); ctx.stroke();
      ctx.fillStyle = occ ? "rgba(255,209,102,0.16)" : "rgba(255,255,255,0.05)";
      ctx.beginPath(); ctx.arc(cxp, cyp, 34, 0, Math.PI * 2); ctx.fill();
      var q = occ !== null && occ !== undefined ? players[occ] : null;
      if (q) {
        ctx.fillStyle = "#ffd166";
        ctx.font = uiMono(10, 700);
        ctx.textAlign = "center";
        ctx.fillText(q.n, cxp, cyp - 42);
      }
      ctx.fillStyle = "rgba(255,255,255,0.55)";
      ctx.font = uiMono(10, 600);
      ctx.textAlign = "center";
      ctx.fillText("椅子", cxp, cyp + 4);
    }
  }
}

// ================= 小地图（左上角） =================
// ================= 主机管理面板（AI 数量 + 玩家调队/赋能） =================
var PW_CYCLE = ["none", "speed", "ghost"];
function adminPanelClick(x, y) {
  // 面板绘制在 HUD 虚拟坐标里（ctx 变换是 DPR×UIS），点击坐标要除以 UIS
  var uis = (typeof UIS === "number" && UIS > 0) ? UIS : 1;
  var vx = x / uis, vy = y / uis;
  for (var i = 0; i < adminRects.length; i++) {
    var r = adminRects[i];
    if (vx >= r.x && vx <= r.x + r.w && vy >= r.y && vy <= r.y + r.h) {
      if (ws && ws.readyState === 1) ws.send(JSON.stringify(r.msg));
      sfx("reload");
      return true;
    }
  }
  return false;
}

// ================= 聊天消息（左下状态卡上方） =================
// ================= 高价值目标右侧说明（概念解释小字） =================
// ================= 战地式积分反馈（右侧堆叠） =================
// ===== 化学 HUD 辅助 =====
function phZone(ph) {
  if (ph === null || ph === undefined) return { txt: "缓冲耗尽", color: "#e74c3c" };
  if (ph <= 3.0) return { txt: "酸中毒", color: "#e74c3c" };
  if (ph >= 11.0) return { txt: "碱中毒", color: "#2fb6c9" };
  if (ph < 4.5) return { txt: "强酸蚀", color: "#ff6b35" };
  if (ph > 9.5) return { txt: "强碱蚀", color: "#2fb6c9" };
  if (ph < 6.5) return { txt: "偏酸", color: "#ffb26b" };
  if (ph > 8.0) return { txt: "偏碱", color: "#7fd6e0" };
  return { txt: "中性", color: "#2ecc71" };
}
function ringColor(v) {
  if (v < 10) return "#e74c3c";
  if (v < 30) return "#ff8c42";
  if (v < 60) return "#ffd166";
  return "#2ecc71";
}

function roundRect(x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

// ================= 开始界面 =================
var nameInput = document.getElementById("nameInput");
try { nameInput.value = localStorage.getItem("lanshooter_name") || ""; } catch (e) {}
document.getElementById("joinBtn").addEventListener("click", function () {
  initAudio();
  var name = nameInput.value.trim();
  if (!name) { nameInput.focus(); connTip.textContent = "先给自己起个名字吧！"; return; }
  try { localStorage.setItem("lanshooter_name", name); } catch (e) {}
  var team = "auto";
  var radios = document.getElementsByName("team");
  for (var i = 0; i < radios.length; i++) if (radios[i].checked) team = radios[i].value;
  var doJoin = function () {
    ws.send(JSON.stringify({
      t: "join", name: name, team: team,
      auth: signAuth(window._challenge || ""),
      settings: {
        bots: parseInt(document.getElementById("botCount").value, 10) || 0,
        diff: document.getElementById("botDiff").value
      }
    }));
  };
  if (ws && ws.readyState === 1) doJoin();
  else connect(doJoin);
});
nameInput.addEventListener("keydown", function (e) {
  if (e.key === "Enter") document.getElementById("joinBtn").click();
});
// 输入名字时点击设置面板不触发射击
document.getElementById("start").addEventListener("mousedown", function (e) { e.stopPropagation(); });

// 页面加载即预连接：提前拿到身份（管理员/普通客户端），开始界面立刻区分，
// 而不是等点击"加入"后才识别（否则普通玩家看到的一直是管理端界面）
initFov(!!window.__MOBILE__);
connect(null);

// ================= 滚轮：面板内滚列表 / 游戏中切枪 =================
var lastWheel = 0;
var scrollY = { board: 0, admin: 0 };      // 战绩榜 / 管理面板的滚动位置（行）
var SCROLL_STEP = 3;                        // 滚一格 = 3 行
addEventListener("wheel", function (e) {
  if (window.__MOBILE__) return;
  if (e.target && e.target.id === "chatInput") return;
  var nowW = performance.now();
  // 战绩榜打开：滚轮滚列表，不再切枪
  if (showBoard) {
    e.preventDefault();
    if (nowW - lastWheel < 30) return;
    lastWheel = nowW;
    var mx = (typeof boardScrollMax === "function") ? boardScrollMax() : 0;
    scrollY.board = Math.max(0, Math.min(mx, scrollY.board + (e.deltaY > 0 ? SCROLL_STEP : -SCROLL_STEP)));
    return;
  }
  // 管理面板打开且鼠标在面板上：滚人类玩家列表
  if (showAdmin && window._isHost) {
    e.preventDefault();
    if (nowW - lastWheel < 30) return;
    lastWheel = nowW;
    var ax = (typeof adminScrollMax === "function") ? adminScrollMax() : 0;
    scrollY.admin = Math.max(0, Math.min(ax, scrollY.admin + (e.deltaY > 0 ? SCROLL_STEP : -SCROLL_STEP)));
    return;
  }
  if (!joined || !ws || ws.readyState !== 1) return;
  var me = players[myId];
  if (!me) return;
  if (nowW - lastWheel < 130) return;   // 节流，一格滚轮切一把
  lastWheel = nowW;
  var order = ["knife", "pistol", "rifle", "sniper", "shotgun", "lmg", "asniper", "limewater", "vitc"];
  var idx = order.indexOf(me.w);
  if (idx < 0) idx = 0;
  idx = (idx + (e.deltaY > 0 ? 1 : -1) + order.length) % order.length;
  ws.send(JSON.stringify({ t: "sw", w: order[idx] }));
  hintMsg = "切换到 " + WEAPONS[order[idx]].name;
  hintT = nowW + 900;
}, { passive: false });

/* 面板上的系统光标：canvas 平时 cursor:none（准星自绘），但列表/按钮面板上要能看见鼠标 */
function updateCursor() {
  var want = "none";
  var overBtn = false;
  if (showAdmin && window._isHost && typeof adminRects !== "undefined") {
    for (var i = 0; i < adminRects.length; i++) {
      var r = adminRects[i];
      if (mouse.x >= r.x && mouse.x <= r.x + r.w && mouse.y >= r.y && mouse.y <= r.y + r.h) {
        overBtn = true; break;
      }
    }
  }
  var panelOpen = showBoard || showHelp ||
    (showAdmin && window._isHost) ||
    (typeof TUT !== "undefined" && TUT && TUT.active) ||
    (document.activeElement && document.activeElement.id === "chatInput");
  if (overBtn) want = "pointer";
  else if (panelOpen) want = "default";
  if (cv.style.cursor !== want) cv.style.cursor = want;
}
addEventListener("mousemove", updateCursor);

// ================= 全局聊天 =================
var chatInput = document.getElementById("chatInput");
if (chatInput) {
  chatInput.addEventListener("keydown", function (e) {
    e.stopPropagation();
    if (e.key === "Enter") {
      var txt = chatInput.value.trim();
      if (txt && joined && ws && ws.readyState === 1)
        ws.send(JSON.stringify({ t: "chat", msg: txt }));
      chatInput.value = "";
      chatInput.classList.remove("show");
      chatInput.blur();
    } else if (e.key === "Escape") {
      chatInput.value = "";
      chatInput.classList.remove("show");
      chatInput.blur();
    }
  });
}

// 机器人开枪时 dry 反馈：自己没子弹点射提示
setInterval(function () {
  var me = players[myId];
  if (me && me.al && mouse.down && me.w !== "knife" && me.am === 0 && me.rl === 0) {
    var now = performance.now();
    if (now - lastFireTime > 400) { lastFireTime = now; sfx("dry"); }
  }
}, 100);

// 断线提示
addEventListener("beforeunload", function () { if (ws) ws.close(); });

// ================= 离线演示模式（?demo=1，仅用于截图/UI 调试） =================
(function () {
  try {
    var q = new URLSearchParams(location.search);
    if (q.get("demo") !== "1") return;
    var mk = function (id, n, team, x, y, w, ph, buf, extra) {
      var o = { id: id, n: n, team: team, bot: team === "blue", al: true,
                x: x, y: y, a: 0.2, hp: buf, buf: buf, ph: ph,
                s: extra && extra.s || 0, k: extra && extra.k || 0,
                d: extra && extra.d || 0, w: w, am: extra && extra.am != null ? extra.am : 24,
                res: extra && extra.res != null ? extra.res : 300,
                rl: 0, scd: extra && extra.scd != null ? extra.scd : 30,
                acd: 20, aoe: 0, pw: "none", hv: !!(extra && extra.hv),
                sg: false, exp: 0, nk_ok: extra && extra.nk_ok ? 1 : 0, resp: 0,
                ind: extra && extra.ind ? 2.0 : 0, indn: extra && extra.indn || 0,
                indcd: 0 };
      return o;
    };
    var list = [
      mk(1, "测试员", "red", 1180, 900, "rifle", 7.01, 78, { s: 1234, k: 5, d: 2, am: 24, indn: 2 }),
      mk(2, "机器人1号", "blue", 1450, 760, "lmg", 10.4, 32, { s: 880, k: 3, d: 3 }),
      mk(3, "机器人2号", "red", 980, 620, "sniper", 4.2, 12, { s: 640, k: 2, d: 4 }),
      mk(4, "机器人3号", "blue", 1600, 1040, "shotgun", 7.9, 90, { s: 1520, k: 7, d: 1, hv: true, ind: true }),
      mk(5, "机器人4号", "blue", 1320, 1180, "pistol", 2.9, 0, { s: 320, k: 1, d: 6 })
    ];
    players = {};
    for (var i = 0; i < list.length; i++) players[list[i].id] = list[i];
    myId = 1;
    joined = true;
    scores = { red: 68, blue: 61 };
    chemCfg = { ct0: 0.1, pka: 7.2, acid: 0.5, destroy: 1.0, low: 3.0, high: 11.0 };
    feed = {
      1: { k: "测试员", kt: "red", v: "机器人2号", vt: "blue", w: "rifle", t: performance.now() },
      2: { k: "机器人3号", kt: "blue", v: "机器人1号", vt: "blue", w: "shotgun", t: performance.now() - 1200 }
    };
    chatMsgs = [{ name: "测试员", team: "red", msg: "改 C_T0 看平台期变短", t: performance.now() }];
    dmgTexts = [
      { x: 1450, y: 720, txt: "26", sub: "pH 6.83", life: 2.2, max: 2.2, kill: false, ch: "acid" },
      { x: 1000, y: 600, txt: "中和 +40", sub: "pH 7.20", life: 2.2, max: 2.2, kill: false, ch: "neutralize" }
    ];
    killConfirm = { name: "机器人2号", w: "rifle", cause: "acidosis", pts: 126,
                    t: performance.now() - 250 };
    dmgDirs = [{ ang: -0.85, t: performance.now() - 180 }];
    // 演示不同通道的弹道特效：[x,y,dx,dy,通道码,武器码]
    bullets = [
      [900, 690, 1, 0, 0, 2],    // 盐酸枪：橙
      [1500, 930, -1, 0, 2, 4],  // 喷淋器：紫
      [1250, 1020, 0, -1, 1, 5]  // 碱液枪：青
    ];
    window._isHost = true;
    window._serverUrls = ["http://192.168.1.23:8765"];
    // 离线演示：同步读取地图池第一张，保证截图/调试有真实地图
    try {
      var xhr = new XMLHttpRequest();
      xhr.open("GET", "maps.json", false);
      xhr.send(null);
      var ms = JSON.parse(xhr.responseText);
      if (ms && ms.length) applyMap(ms[0]);
    } catch (e) {}
    document.getElementById("start").classList.add("hidden");
    if (q.get("panel") === "1") showAdmin = true;
    if (q.get("help") === "1") showHelp = true;
  } catch (e) {}
})();
(function () {
  try {
    var q = new URLSearchParams(location.search);
    if (q.get("autoplay") !== "1") return;
    var bc = document.getElementById("botCount");
    var bd = document.getElementById("botDiff");
    if (bc && q.get("bots")) bc.value = q.get("bots");
    if (bd && q.get("diff")) bd.value = q.get("diff");
    setTimeout(function () {
      var ni = document.getElementById("nameInput");
      if (ni) ni.value = q.get("name") || "截图测试";
      var jb = document.getElementById("joinBtn");
      if (jb) jb.click();
      if (q.get("panel") === "1") {
        setTimeout(function () { showAdmin = true; }, 1500);
      }
    }, 400);
  } catch (e) {}
})();
