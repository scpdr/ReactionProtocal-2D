/* ============================================================
   Reaction Protocol — HUD 视觉层（全新重做）
   设计语言：实验室仪器 / 等宽数字 / 酸橙-碱青-中性绿
   依赖 game.js 的全局：ctx, UW, UH, players, myId, scores, feed,
   WEAPONS, TEAM_COLOR, smokes, WALLS, WORLD_W, WORLD_H, mouse,
   hitMarker, hitMarkerKill, scorePops, chatMsgs, hvtTip,
   phZone, ringColor, deadInfo, window._serverUrls
   ============================================================ */
var UI = {
  panel: "rgba(11,16,22,0.82)",
  panel2: "rgba(15,21,28,0.94)",
  line: "rgba(255,255,255,0.10)",
  lineHot: "rgba(255,255,255,0.22)",
  ink: "#e7eef6",
  dim: "#8291a1",
  faint: "#4c5a68",
  acid: "#ff6b35",
  base: "#39c7d8",
  neutral: "#7dffb2",
  warn: "#ffc857",
  ox: "#b07cff",
  danger: "#ff4d5e",
  cjk: '"Source Han Sans SC","Noto Sans CJK SC","Microsoft YaHei","PingFang SC",sans-serif',
  mono: '"JetBrains Mono","Cascadia Mono","Consolas","SFMono-Regular",monospace'
};

function uiFont(px, w) { return (w || 400) + " " + px + "px " + UI.cjk; }
function uiMono(px, w) { return (w || 400) + " " + px + "px " + UI.mono; }
function uiClamp(v, a, b) { return v < a ? a : (v > b ? b : v); }

function uiRR(x, y, w, h, r) {
  r = Math.min(r, w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}
function uiPanel(x, y, w, h, r, opts) {
  opts = opts || {};
  ctx.fillStyle = opts.fill || UI.panel;
  uiRR(x, y, w, h, r || 10); ctx.fill();
  if (opts.stroke !== false) {
    ctx.strokeStyle = opts.strokeColor || UI.line;
    ctx.lineWidth = 1;
    uiRR(x, y, w, h, r || 10); ctx.stroke();
  }
}
function uiCorners(x, y, w, h, len, color) {
  ctx.strokeStyle = color || UI.lineHot;
  ctx.lineWidth = 1.2;
  len = len || 10;
  ctx.beginPath();
  ctx.moveTo(x, y + len); ctx.lineTo(x, y); ctx.lineTo(x + len, y);
  ctx.moveTo(x + w - len, y); ctx.lineTo(x + w, y); ctx.lineTo(x + w, y + len);
  ctx.moveTo(x + w, y + h - len); ctx.lineTo(x + w, y + h); ctx.lineTo(x + w - len, y + h);
  ctx.moveTo(x + len, y + h); ctx.lineTo(x, y + h); ctx.lineTo(x, y + h - len);
  ctx.stroke();
}
function uiArc(cx, cy, r, a0, a1, lw, color) {
  ctx.strokeStyle = color;
  ctx.lineWidth = lw;
  ctx.lineCap = "round";
  ctx.beginPath();
  ctx.arc(cx, cy, r, a0, a1);
  ctx.stroke();
  ctx.lineCap = "butt";
}
function uiBar(x, y, w, h, pct, color) {
  ctx.fillStyle = "rgba(255,255,255,0.08)";
  uiRR(x, y, w, h, h / 2); ctx.fill();
  var fw = Math.max(0, Math.min(1, pct)) * w;
  if (fw > 0.5) {
    ctx.fillStyle = color;
    uiRR(x, y, fw, h, h / 2); ctx.fill();
  }
}
function uiKeyCap(x, y, txt, color, size) {
  size = size || 18;
  ctx.font = uiMono(10, 600);
  var tw = Math.max(size, ctx.measureText(txt).width + 12);
  ctx.fillStyle = "rgba(255,255,255,0.07)";
  uiRR(x, y - 12, tw, 18, 4); ctx.fill();
  ctx.strokeStyle = color || UI.line; ctx.lineWidth = 1;
  uiRR(x, y - 12, tw, 18, 4); ctx.stroke();
  ctx.fillStyle = color || UI.dim;
  ctx.textAlign = "center";
  ctx.fillText(txt, x + tw / 2, y + 1);
  ctx.textAlign = "left";
  return tw;
}

/* ---------------- 顶部比分 ---------------- */
function drawScoreBar() {
  var w = 316, h = 56, x = UW / 2 - w / 2, y = 14;
  uiPanel(x, y, w, h, 10);
  uiCorners(x, y, w, h, 12, "rgba(255,255,255,0.16)");
  var cy = y + 37;
  // 红
  ctx.textAlign = "left";
  ctx.font = uiMono(9, 600); ctx.fillStyle = "rgba(255,82,82,0.75)";
  ctx.fillText("RED / 红方", x + 18, y + 18);
  ctx.font = uiMono(25, 700); ctx.fillStyle = TEAM_COLOR.red;
  ctx.fillText(String(scores.red), x + 18, cy);
  // 蓝
  ctx.textAlign = "right";
  ctx.font = uiMono(9, 600); ctx.fillStyle = "rgba(77,166,255,0.75)";
  ctx.fillText("BLUE / 蓝方", x + w - 18, y + 18);
  ctx.font = uiMono(25, 700); ctx.fillStyle = TEAM_COLOR.blue;
  ctx.fillText(String(scores.blue), x + w - 18, cy);
  // 中缝
  ctx.strokeStyle = UI.line; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(UW / 2, y + 12); ctx.lineTo(UW / 2, y + h - 12); ctx.stroke();
  ctx.textAlign = "center";
  ctx.font = uiMono(9, 600); ctx.fillStyle = UI.faint;
  ctx.fillText("SCORE", UW / 2, y + 24);
  ctx.font = uiMono(15, 700); ctx.fillStyle = UI.dim;
  ctx.fillText(":", UW / 2, y + 43);
  ctx.textAlign = "left";
}

/* ---------------- 小地图 ---------------- */
function drawMinimap() {
  var w = 206, h = 138, x = 20, y = 20;
  uiPanel(x, y, w, h, 10);
  uiCorners(x, y, w, h, 12, "rgba(57,199,216,0.35)");
  // 标题
  ctx.textAlign = "left";
  ctx.font = uiMono(9, 600); ctx.fillStyle = UI.faint;
  ctx.fillText("TACTICAL // 情报", x + 12, y + 16);
  var alive = 0;
  for (var pid0 in players) if (players[pid0].al) alive++;
  ctx.textAlign = "right";
  ctx.fillStyle = UI.dim;
  ctx.fillText("ALIVE " + alive, x + w - 12, y + 16);
  ctx.textAlign = "left";

  var mx = x + 8, my = y + 24, mw = w - 16, mh = h - 32;
  var sc = Math.min(mw / WORLD_W, mh / WORLD_H);
  var ox = mx + (mw - WORLD_W * sc) / 2, oy = my + (mh - WORLD_H * sc) / 2;
  ctx.save();
  uiRR(mx, my, mw, mh, 6); ctx.clip();
  ctx.fillStyle = "rgba(0,0,0,0.35)";
  ctx.fillRect(mx, my, mw, mh);
  // 墙体
  ctx.fillStyle = "rgba(160,180,198,0.22)";
  for (var i = 0; i < WALLS.length; i++) {
    var wl = WALLS[i];
    uiRR(ox + wl[0] * sc, oy + wl[1] * sc, Math.max(1.5, wl[2] * sc), Math.max(1.5, wl[3] * sc), 1);
    ctx.fill();
  }
  // 烟雾
  for (i = 0; i < smokes.length; i++) {
    var s2 = smokes[i];
    ctx.fillStyle = "rgba(200,210,220,0.28)";
    ctx.beginPath(); ctx.arc(ox + s2[0] * sc, oy + s2[1] * sc, s2[2] * sc, 0, Math.PI * 2); ctx.fill();
  }
  // 玩家
  var meNow = players[myId];
  var now = performance.now();
  for (var pid in players) {
    var p = players[pid];
    if (!p.al) continue;
    var isMe = +pid === myId;
    var isMate = !isMe && meNow && p.team === meNow.team;
    var exposed = p.exp > 0;
    var isHvt = !!p.hv;
    if (!isMe && !isMate && !exposed && !isHvt) continue;
    if (!isMe && !isMate && (p.pw === "ghost" || p.sg) && !isHvt) continue;
    var px = ox + p.x * sc, py = oy + p.y * sc;
    if (isHvt) {
      ctx.fillStyle = UI.warn;
      ctx.beginPath(); ctx.arc(px, py, 3.4 + (Math.sin(now / 160) + 1) * 1.4, 0, Math.PI * 2); ctx.fill();
    } else if (isMe) {
      ctx.fillStyle = "#ffffff";
      ctx.beginPath(); ctx.arc(px, py, 3.6, 0, Math.PI * 2); ctx.fill();
      ctx.strokeStyle = "rgba(0,0,0,0.5)"; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.arc(px, py, 5.4, 0, Math.PI * 2); ctx.stroke();
    } else if (isMate) {
      ctx.fillStyle = TEAM_COLOR[p.team];
      ctx.beginPath(); ctx.arc(px, py, 3, 0, Math.PI * 2); ctx.fill();
    } else {
      ctx.fillStyle = TEAM_COLOR[p.team];
      ctx.beginPath(); ctx.arc(px, py, 2.6, 0, Math.PI * 2); ctx.fill();
      ctx.strokeStyle = "rgba(255,255,255,0.7)"; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.arc(px, py, 5, 0, Math.PI * 2); ctx.stroke();
    }
  }
  ctx.restore();
  ctx.textAlign = "left";
}

/* ---------------- 击杀播报 ---------------- */
function drawKillFeed(now) {
  var list = [];
  for (var fid in feed) list.push(feed[fid]);
  list.sort(function (a, b) { return b.t - a.t; });
  list = list.slice(0, 5);
  var xR = UW - 22, y = 92;
  ctx.font = uiMono(12, 600);
  for (var i = 0; i < list.length; i++) {
    var f = list[i];
    var age = (now - f.t) / 5000;
    var alpha = Math.max(0.28, 1 - age);
    var slide = Math.max(0, 1 - age * 12) * 12;          // 右滑入
    var wname = WEAPONS[f.w] ? WEAPONS[f.w].name : f.w;
    var causeMap = { acidosis: "酸中毒", alkalosis: "碱中毒", buffer_destroyed: "缓冲剥离" };
    var causeTxt = causeMap[f.cause] || "";
    var txtA = f.k, txtB = " ⟶ " + f.v, txtC = " [" + wname + (causeTxt ? "·" + causeTxt : "") + "]";
    var wA = ctx.measureText(txtA).width, wB = ctx.measureText(txtB).width, wC = ctx.measureText(txtC).width;
    var total = wA + wB + wC + 24;
    ctx.globalAlpha = alpha;
    var left = xR - total - slide;
    ctx.fillStyle = "rgba(8,12,17,0.78)";
    uiRR(left, y - 15, total, 22, 5); ctx.fill();
    ctx.strokeStyle = "rgba(255,255,255,0.06)"; ctx.lineWidth = 1;
    uiRR(left, y - 15, total, 22, 5); ctx.stroke();
    // 击杀者队色左条
    ctx.fillStyle = TEAM_COLOR[f.kt] || UI.dim;
    uiRR(left + 1, y - 14, 3, 20, 1.5); ctx.fill();
    var tx = left + 11;
    ctx.textAlign = "left";
    ctx.fillStyle = TEAM_COLOR[f.kt] || UI.ink;
    ctx.fillText(txtA, tx, y);
    ctx.fillStyle = UI.faint;
    ctx.fillText(txtB, tx + wA, y);
    ctx.fillStyle = CH_COLOR[weaponChannel(f.w)] || UI.dim;
    ctx.fillText(txtC, tx + wA + wB, y);
    y += 27;
  }
  ctx.globalAlpha = 1;
  ctx.textAlign = "left";
}

/* ---------------- 击杀确认卡（CS2 / Valorant 风格） ---------------- */
function drawKillConfirm(now) {
  if (!killConfirm) return;
  var life = killConfirm.hvt ? 2400 : 2200;
  var age = (now - killConfirm.t) / life;
  if (age >= 1) { killConfirm = null; return; }
  var alpha = age < 0.055 ? age / 0.055 : (age > 0.76 ? Math.max(0, 1 - (age - 0.76) / 0.24) : 1);
  var intro = Math.min(1, age / 0.12), ease = 1 - Math.pow(1 - intro, 3);
  var pop = 1 + (1 - ease) * 0.12;
  var wname = WEAPONS[killConfirm.w] ? WEAPONS[killConfirm.w].name : killConfirm.w;
  var causeMap = { acidosis: "酸中毒", alkalosis: "碱中毒", buffer_destroyed: "缓冲剥离" };
  var cause = causeMap[killConfirm.cause] || "";
  var accent = CH_COLOR[weaponChannel(killConfirm.w)] || UI.danger;
  var w = 540, h = 92, x = UW / 2 - w / 2;
  // 顶部还有事件条/Boss 血条时自动下移，避免遮挡
  var yTop = 88;
  if (typeof bossInfo !== "undefined" && bossInfo) yTop = 206;
  else if (typeof eventInfo !== "undefined" && eventInfo && eventInfo.left) yTop = 150;
  var y = yTop - (1 - ease) * 22;
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.translate(UW / 2, y + h / 2); ctx.scale(pop, pop); ctx.translate(-UW / 2, -(y + h / 2));

  // 切角深色面板（移植自 3D 版）
  ctx.beginPath();
  ctx.moveTo(x + 14, y); ctx.lineTo(x + w - 14, y); ctx.lineTo(x + w, y + 14);
  ctx.lineTo(x + w, y + h - 14); ctx.lineTo(x + w - 14, y + h);
  ctx.lineTo(x + 14, y + h); ctx.lineTo(x, y + h - 14); ctx.lineTo(x, y + 14); ctx.closePath();
  var bg = ctx.createLinearGradient(x, y, x + w, y + h);
  bg.addColorStop(0, "rgba(11,7,16,.96)"); bg.addColorStop(.55, "rgba(8,9,16,.94)");
  bg.addColorStop(1, "rgba(28,6,18,.92)");
  ctx.fillStyle = bg; ctx.fill();
  ctx.strokeStyle = "rgba(255,49,93,.7)"; ctx.lineWidth = 1.3; ctx.stroke();
  ctx.fillStyle = accent; ctx.globalAlpha = alpha * 0.9;
  ctx.fillRect(x, y + 14, 4, h - 28);
  ctx.globalAlpha = alpha;
  ctx.fillStyle = "rgba(255,49,93,.18)";
  ctx.fillRect(x + 4, y + h - 18, (w - 8) * Math.max(0, 1 - age * 1.15), 2);

  // 击杀徽章
  var cx = x + 48, cy = y + h / 2;
  ctx.strokeStyle = "rgba(255,73,108,.95)"; ctx.lineWidth = 2.2;
  ctx.beginPath(); ctx.arc(cx, cy, 22, 0, Math.PI * 2); ctx.stroke();
  ctx.strokeStyle = "rgba(255,255,255,.85)"; ctx.lineWidth = 1.1;
  ctx.beginPath(); ctx.arc(cx, cy, 16.5, 0, Math.PI * 2); ctx.stroke();
  ctx.strokeStyle = "#ff315d"; ctx.lineWidth = 3;
  ctx.beginPath();
  ctx.moveTo(cx - 11, cy - 11); ctx.lineTo(cx + 11, cy + 11);
  ctx.moveTo(cx + 11, cy - 11); ctx.lineTo(cx - 11, cy + 11);
  ctx.stroke();
  ctx.fillStyle = "#ff315d";
  ctx.beginPath(); ctx.arc(cx, cy, 2.6, 0, Math.PI * 2); ctx.fill();

  ctx.textAlign = "left";
  ctx.font = uiMono(9, 700); ctx.fillStyle = "rgba(255,73,108,.9)";
  ctx.fillText("KILL CONFIRMED // 击杀确认", x + 86, y + 22);
  ctx.font = uiMono(22, 800); ctx.fillStyle = "#ff315d";
  ctx.fillText("ELIMINATED", x + 86, y + 49);
  ctx.font = uiFont(15, 700); ctx.fillStyle = UI.ink;
  ctx.fillText("你淘汰了  " + killConfirm.name, x + 290, y + 49);
  ctx.font = uiMono(10.5, 600); ctx.fillStyle = accent;
  ctx.fillText("[" + wname + (cause ? " · " + cause : "") + "]", x + 86, y + 70);
  ctx.textAlign = "right";
  ctx.font = uiMono(24, 800); ctx.fillStyle = killConfirm.hvt ? "#ffd166" : "#ffffff";
  ctx.fillText("+" + (killConfirm.pts || 0), x + w - 22, y + 48);
  ctx.font = uiMono(8.5, 600); ctx.fillStyle = UI.faint;
  ctx.fillText(killConfirm.hvt ? "HVT BONUS" : "COMBAT SCORE", x + w - 22, y + 66);
  // 扫描高光
  var scan = (age * 1.7) % 1;
  ctx.globalAlpha = alpha * 0.14;
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(x + scan * (w - 70), y + 2, 70, h - 4);
  ctx.restore();
  ctx.textAlign = "left";
  ctx.globalAlpha = 1;
}

/* 击杀瞬间准心冲击环（移植自 3D 版，提供即时命中确认） */
function drawKillImpact(now) {
  if (!killImpactT) return;
  var age = (now - killImpactT) / 430;
  if (age >= 1) return;
  drawKillImpact._draws = (drawKillImpact._draws || 0) + 1;   // 供自动化测试断言
  var a = 1 - age, cx = UW / 2, cy = UH / 2;
  var r = 23 + age * 35;
  ctx.save();
  ctx.globalAlpha = a;
  ctx.strokeStyle = "#ff315d"; ctx.lineWidth = 2;
  ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2); ctx.stroke();
  ctx.strokeStyle = "rgba(255,255,255,.9)"; ctx.lineWidth = 1.3;
  for (var i = 0; i < 4; i++) {
    var ang = Math.PI / 4 + i * Math.PI / 2;
    ctx.beginPath();
    ctx.moveTo(cx + Math.cos(ang) * (r + 5), cy + Math.sin(ang) * (r + 5));
    ctx.lineTo(cx + Math.cos(ang) * (r + 13), cy + Math.sin(ang) * (r + 13));
    ctx.stroke();
  }
  ctx.restore();
}

/* ---------------- 受击方向指示（CS2 风格） ---------------- */
function drawDamageDirs(now) {
  if (!dmgDirs || !dmgDirs.length) return;
  var cx = UW / 2, cy = UH / 2, rad = Math.min(UW, UH) * 0.17;
  for (var i = dmgDirs.length - 1; i >= 0; i--) {
    var d = dmgDirs[i];
    var age = (now - d.t) / 900;
    if (age >= 1) { dmgDirs.splice(i, 1); continue; }
    var a = 1 - age;
    ctx.strokeStyle = "rgba(255,77,94," + (a * 0.9).toFixed(2) + ")";
    ctx.lineWidth = 5; ctx.lineCap = "round";
    ctx.beginPath(); ctx.arc(cx, cy, rad, d.ang - 0.22, d.ang + 0.22); ctx.stroke();
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(cx + Math.cos(d.ang) * (rad + 10), cy + Math.sin(d.ang) * (rad + 10));
    ctx.lineTo(cx + Math.cos(d.ang) * (rad + 19), cy + Math.sin(d.ang) * (rad + 19));
    ctx.stroke();
  }
  ctx.lineCap = "butt";
}

/* ---------------- 左下体系状态卡 ---------------- */
function drawStatusCard(me, now) {
  // v1.4：整卡缩到 74%（原来 380×164 在 1366 宽屏上挡视野），内部按设计尺寸绘制再统一缩放
  var w = 380, h = 164, K = 0.74;
  var x = 0, y = 0;                       // 局部坐标（配合下面的 translate+scale）
  ctx.save();
  ctx.translate(18, UH - h * K - 12);
  ctx.scale(K, K);
  uiPanel(x, y, w, h, 12);
  uiCorners(x, y, w, h, 13, "rgba(125,255,178,0.30)");
  // 标题行
  ctx.textAlign = "left";
  ctx.font = uiMono(9, 600); ctx.fillStyle = UI.faint;
  ctx.fillText("SYSTEM // 体系状态", x + 16, y + 20);
  ctx.textAlign = "right";
  ctx.font = uiMono(10.5, 600); ctx.fillStyle = UI.warn;
  ctx.fillText("SCORE " + (me.s || 0), x + w - 16, y + 20);
  ctx.textAlign = "left";

  var ph = me.ph;
  var zone = phZone(ph);
  var phTxt = (ph === null || ph === undefined) ? "--" : ph.toFixed(2);
  var cx = x + 88, cy = y + 128, r = 32;

  // pH 数值：放到表盘上方，避免与指针/刻线重叠
  ctx.textAlign = "center";
  ctx.font = uiMono(9, 600); ctx.fillStyle = UI.faint;
  ctx.fillText("pH", cx, y + 44);
  ctx.font = uiMono(26, 700); ctx.fillStyle = zone.color;
  ctx.fillText(phTxt, cx, y + 72);

  // pH 半圆仪表
  uiArc(cx, cy, r, Math.PI, Math.PI * 2, 6, "rgba(255,255,255,0.09)");
  var frac = (ph === null || ph === undefined) ? 1 : uiClamp((ph - 3) / 8, 0, 1);
  if (frac > 0.001) uiArc(cx, cy, r, Math.PI, Math.PI + frac * Math.PI, 6, zone.color);
  // 刻度
  ctx.strokeStyle = "rgba(255,255,255,0.18)"; ctx.lineWidth = 1;
  for (var i = 0; i <= 8; i++) {
    var a = Math.PI + (i / 8) * Math.PI;
    var c1 = Math.cos(a), s1 = Math.sin(a);
    ctx.beginPath();
    ctx.moveTo(cx + c1 * (r - 8), cy + s1 * (r - 8));
    ctx.lineTo(cx + c1 * (r - 4), cy + s1 * (r - 4));
    ctx.stroke();
  }
  // 指针
  if (ph !== null && ph !== undefined) {
    var na = Math.PI + frac * Math.PI;
    ctx.strokeStyle = zone.color; ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.lineTo(cx + Math.cos(na) * (r - 7), cy + Math.sin(na) * (r - 7));
    ctx.stroke();
    ctx.fillStyle = zone.color;
    ctx.beginPath(); ctx.arc(cx, cy, 2.6, 0, Math.PI * 2); ctx.fill();
  }
  // 量程标注
  ctx.font = uiMono(8.5, 600); ctx.fillStyle = UI.faint;
  ctx.textAlign = "center";
  ctx.fillText("3", cx - r - 7, cy + 3);
  ctx.fillText("11", cx + r + 9, cy + 3);
  ctx.textAlign = "left";

  // 右侧：状态 + 缓冲
  var bx = x + 172, bw = w - (bx - x) - 16;
  ctx.font = uiFont(16, 700); ctx.fillStyle = zone.color;
  ctx.fillText(zone.txt, bx, y + 58);
  ctx.font = uiMono(9, 600); ctx.fillStyle = UI.faint;
  ctx.fillText("BUFFER // 缓冲环", bx, y + 86);
  var buf = (me.buf === null || me.buf === undefined) ? me.hp : me.buf;
  buf = uiClamp(buf, 0, 100);
  ctx.textAlign = "right";
  ctx.font = uiMono(14, 700); ctx.fillStyle = ringColor(buf);
  ctx.fillText(Math.round(buf) + "%", x + w - 16, y + 86);
  ctx.textAlign = "left";
  uiBar(bx, y + 94, bw, 11, buf / 100, ringColor(buf));
  // 低缓冲脉冲
  if (buf < 15 && me.al) {
    var pulse = (Math.sin(now / 150) + 1) / 2;
    ctx.globalAlpha = 0.25 + pulse * 0.45;
    ctx.strokeStyle = UI.danger; ctx.lineWidth = 1.4;
    uiRR(x + 0.5, y + 0.5, w - 1, h - 1, 12); ctx.stroke();
    ctx.globalAlpha = 1;
  }
  // 代偿提示
  if (me.al && now - (window._regenT || -1e9) < 600) {
    ctx.font = uiMono(9.5, 600); ctx.fillStyle = UI.neutral;
    ctx.textAlign = "right";
    ctx.fillText("＋ 缓冲恢复中", x + w - 16, y + h - 10);
    ctx.textAlign = "left";
  }
  ctx.restore();                          // 结束状态卡缩放
}

/* ---------------- 右下试剂架 ---------------- */
var RACK = [
  { id: "knife", formula: "PHYS", ch: "物理", color: "#8291a1" },
  { id: "pistol", formula: "H₂SiO₃", ch: "酸", color: "#ff6b35" },
  { id: "rifle", formula: "HCl", ch: "酸", color: "#ff6b35" },
  { id: "sniper", formula: "HClO₄", ch: "酸", color: "#ff6b35" },
  { id: "shotgun", formula: "H₂O₂", ch: "氧化", color: "#b07cff" },
  { id: "lmg", formula: "NaOH", ch: "碱", color: "#39c7d8" },
  { id: "asniper", formula: "NaOH·LR", ch: "碱 · 连狙", color: "#39c7d8" },
  { id: "limewater", formula: "Ca(OH)₂", ch: "碱", color: "#39c7d8" },
  { id: "vitc", formula: "C₆H₈O₆", ch: "还原", color: "#7dffb2" }
];
function drawWeaponRack(me) {
  var slotW = 58, gap = 6, total = RACK.length * slotW + (RACK.length - 1) * gap;
  // v1.4：试剂架缩到 72%（原来 506×70，占右下角太多），内部按设计尺寸绘制再统一缩放
  var K = 0.72;
  var x = 0, y = 0;                       // 局部坐标
  ctx.save();
  ctx.translate(UW - 16 - total * K, UH - 10 - 86 * K);
  ctx.scale(K, K);
  ctx.textAlign = "left";
  ctx.font = uiMono(9, 600); ctx.fillStyle = UI.faint;
  ctx.fillText("REAGENT RACK // 试剂架", x, y - 16);
  for (var i = 0; i < RACK.length; i++) {
    var it = RACK[i], sx = x + i * (slotW + gap);
    var active = me.w === it.id;
    uiPanel(sx, y, slotW, 70, 8, {
      fill: active ? "rgba(20,28,36,0.96)" : "rgba(8,12,17,0.72)",
      strokeColor: active ? it.color : UI.line
    });
    if (active) {
      ctx.fillStyle = it.color;
      uiRR(sx + 1, y + 1, slotW - 2, 3, 2); ctx.fill();
    }
    // 槽位号
    ctx.textAlign = "left";
    ctx.font = uiMono(8.5, 600); ctx.fillStyle = active ? it.color : UI.faint;
    ctx.fillText(String(i + 1).padStart(2, "0"), sx + 7, y + 15);
    // 试剂式
    ctx.textAlign = "center";
    ctx.font = uiMono(11, 700);
    ctx.fillStyle = active ? it.color : UI.dim;
    ctx.fillText(it.formula, sx + slotW / 2, y + 36);
    // 通道
    ctx.font = uiFont(9, 500); ctx.fillStyle = UI.faint;
    ctx.fillText(it.ch, sx + slotW / 2, y + 49);
    // 弹药
    var ammoTxt = "";
    if (it.id !== "knife") {
      if (active && me.rl > 0) ammoTxt = "RL…";
      else if (active) ammoTxt = (me.pw === "ammo") ? "∞" : (String(me.am || 0) + "|" + String(me.res || 0));
      else ammoTxt = String(WEAPONS[it.id] ? WEAPONS[it.id].mag : 0);
    }
    ctx.font = uiMono(10.5, 600);
    ctx.fillStyle = active ? UI.ink : UI.faint;
    ctx.fillText(ammoTxt || "—", sx + slotW / 2, y + 63);
    ctx.textAlign = "left";
  }
  // 换弹进度
  if (me.rl > 0) {
    var rw = total, rx = x, ry = y - 28;
    var totalRl = (window.RELOAD_TIME && RELOAD_TIME[me.w]) || 2.1;
    uiBar(rx, ry, rw, 4, 1 - me.rl / totalRl, UI.warn);
    ctx.font = uiMono(9, 600); ctx.fillStyle = UI.warn;
    ctx.fillText("RELOADING…", rx, ry - 6);
  }
  ctx.restore();                          // 结束试剂架缩放
}

/* ---------------- 烟雾 / AOE 技能条 ---------------- */
function drawSmokeHud(me) {
  var chips = [];
  chips.push({ k: "E", t: "烟雾弹 " + (me.scd <= 0 ? "就绪" : "CD " + Math.ceil(me.scd) + "s"),
               c: me.scd <= 0 ? UI.neutral : UI.faint });
  if (me.aoe > 0) chips.push({ k: "Q", t: "AOE 生效 " + Math.ceil(me.aoe) + "s", c: UI.acid });
  else if (me.acd <= 0) chips.push({ k: "Q", t: "AOE 就绪", c: UI.neutral });
  else chips.push({ k: "Q", t: "AOE CD " + Math.ceil(me.acd) + "s", c: UI.faint });
  var indTxt, indC;
  var indn = me.indn || 0, indcd = me.indcd || 0;
  if (indn > 0 && indcd <= 0) { indTxt = "指示剂 ×" + indn; indC = UI.neutral; }
  else if (indn <= 0) { indTxt = "指示剂 用完"; indC = UI.faint; }
  else { indTxt = "指示剂 CD " + Math.ceil(indcd) + "s"; indC = UI.faint; }
  chips.push({ k: "F", t: indTxt, c: indC });

  ctx.font = uiMono(10.5, 600);
  var pad = 16, gap = 10, widths = [], totalW = 0;
  for (var i = 0; i < chips.length; i++) {
    var w = ctx.measureText(chips[i].t).width + 26 + 14 + pad;
    widths.push(w); totalW += w + (i ? gap : 0);
  }
  var x = UW / 2 - totalW / 2, y = UH - 34;
  for (i = 0; i < chips.length; i++) {
    uiPanel(x, y - 15, widths[i], 24, 5, { fill: "rgba(8,12,17,0.72)" });
    uiKeyCap(x + 7, y, chips[i].k, chips[i].c);
    ctx.fillStyle = chips[i].c;
    ctx.textAlign = "left";
    ctx.font = uiMono(10.5, 600);
    ctx.fillText(chips[i].t, x + 7 + 18 + 10, y);
    x += widths[i] + gap;
  }
  ctx.textAlign = "left";
}

/* ---------------- 聊天 ---------------- */
function drawChat(now) {
  if (window._isHost && showAdmin) return;   // 主机打开参数面板时先收起聊天，避免遮挡
  while (chatMsgs.length && now - chatMsgs[0].t > 10000) chatMsgs.shift();
  var x = 20, baseY = UH - 190;
  var n = chatMsgs.length;
  for (var i = 0; i < n; i++) {
    var m = chatMsgs[i];
    var age = now - m.t;
    var alpha = age > 8000 ? Math.max(0, 1 - (age - 8000) / 2000) : 1;
    var y = baseY - (n - 1 - i) * 20;
    ctx.font = uiFont(12, 500);
    var nameTxt = m.name + "：";
    var wName = ctx.measureText(nameTxt).width;
    var wMsg = ctx.measureText(m.msg).width;
    ctx.globalAlpha = alpha;
    ctx.fillStyle = "rgba(8,12,17,0.68)";
    uiRR(x - 6, y - 14, wName + wMsg + 16, 20, 4); ctx.fill();
    ctx.textAlign = "left";
    ctx.fillStyle = TEAM_COLOR[m.team] || UI.ink;
    ctx.fillText(nameTxt, x, y);
    ctx.fillStyle = UI.ink;
    ctx.fillText(m.msg, x + wName, y);
    ctx.globalAlpha = 1;
  }
  ctx.textAlign = "left";
}

/* ---------------- 积分飘字 ---------------- */
function drawScorePops(now) {
  for (var i = scorePops.length - 1; i >= 0; i--) {
    if (now - scorePops[i].t > 1600) scorePops.splice(i, 1);
  }
  var n = scorePops.length;
  for (var j = 0; j < n; j++) {
    var sp = scorePops[n - 1 - j];
    var age = (now - sp.t) / 1600;
    var x = UW - 320, y = UH * 0.42 - j * 30 - age * 26;
    ctx.globalAlpha = Math.max(0, 1 - age * age);
    ctx.font = uiMono(sp.big ? 20 : 15, 700);
    ctx.textAlign = "right";
    ctx.fillStyle = sp.big ? UI.warn : (sp.gold ? UI.neutral : UI.ink);
    ctx.fillText(sp.txt, x, y);
  }
  ctx.globalAlpha = 1;
  ctx.textAlign = "left";
}

/* ---------------- 高价值目标说明 ---------------- */
function drawHvtTip() {
  if (!hvtTip) return;
  ctx.textAlign = "right";
  ctx.font = uiMono(10, 600); ctx.fillStyle = UI.warn;
  ctx.fillText("HVT // 高价值目标", UW - 20, UH - 226);
  ctx.font = uiFont(11, 400);
  ctx.fillStyle = UI.dim;
  ctx.fillText("连杀超过 7 次的玩家，位置持续暴露；", UW - 20, UH - 209);
  ctx.fillText("击杀他得额外积分，他杀得越多奖励越高", UW - 20, UH - 194);
  ctx.textAlign = "left";
}

/* ---------------- 准星 ---------------- */
function drawCrosshair() {
  var x = mouse.x / UIS, y = mouse.y / UIS;
  var me = players[myId];
  var sniper = me && me.w === "sniper";
  var gap = sniper ? 14 : 8, len = sniper ? 8 : 7;
  ctx.strokeStyle = "rgba(255,255,255,0.82)";
  ctx.lineWidth = 1.4;
  ctx.beginPath();
  // 四角括线
  ctx.moveTo(x - gap - len, y - gap); ctx.lineTo(x - gap - len, y - gap - len); ctx.lineTo(x - gap, y - gap - len);
  ctx.moveTo(x + gap + len, y - gap); ctx.lineTo(x + gap + len, y - gap - len); ctx.lineTo(x + gap, y - gap - len);
  ctx.moveTo(x - gap - len, y + gap); ctx.lineTo(x - gap - len, y + gap + len); ctx.lineTo(x - gap, y + gap + len);
  ctx.moveTo(x + gap + len, y + gap); ctx.lineTo(x + gap + len, y + gap + len); ctx.lineTo(x + gap, y + gap + len);
  ctx.stroke();
  ctx.fillStyle = "rgba(255,255,255,0.9)";
  ctx.beginPath(); ctx.arc(x, y, 1.4, 0, Math.PI * 2); ctx.fill();
  // 命中标记
  if (hitMarker > 0) {
    hitMarker -= 1 / 60;
    ctx.strokeStyle = hitMarkerKill ? UI.danger : "#ffffff";
    ctx.lineWidth = hitMarkerKill ? 3 : 2;
    var s = hitMarkerKill ? 12 : 9;
    ctx.beginPath();
    ctx.moveTo(x - s, y - s); ctx.lineTo(x - 5, y - 5);
    ctx.moveTo(x + s, y - s); ctx.lineTo(x + 5, y - 5);
    ctx.moveTo(x - s, y + s); ctx.lineTo(x - 5, y + 5);
    ctx.moveTo(x + s, y + s); ctx.lineTo(x + 5, y + 5);
    ctx.stroke();
  }
}

/* ---------------- 死亡界面 ---------------- */
function drawDeathScreen(info, now) {
  ctx.fillStyle = "rgba(4,7,10,0.66)";
  ctx.fillRect(0, 0, UW, UH);
  var w = 520, h = 232, x = UW / 2 - w / 2, y = UH / 2 - h / 2;
  uiPanel(x, y, w, h, 14, { fill: "rgba(10,14,19,0.94)", strokeColor: "rgba(255,77,94,0.35)" });
  uiCorners(x, y, w, h, 16, "rgba(255,77,94,0.5)");
  ctx.textAlign = "center";
  ctx.font = uiMono(10, 600); ctx.fillStyle = "rgba(255,77,94,0.85)";
  ctx.fillText("SYSTEM FAILURE // 体系失衡", UW / 2, y + 34);
  ctx.font = uiFont(30, 800); ctx.fillStyle = UI.ink;
  ctx.fillText("实验终止", UW / 2, y + 80);
  ctx.font = uiFont(14, 500); ctx.fillStyle = UI.dim;
  ctx.fillText("击杀者  " + info.killer, UW / 2, y + 112);

  var causeMap = {
    acidosis: "酸中毒：pH 越过 3.00，体系酸化",
    alkalosis: "碱中毒：pH 越过 11.00，体系碱化",
    buffer_destroyed: "缓冲体系被剥离：C_T → 0"
  };
  var causeTxt = causeMap[info.cause] || "死因未知";
  // 死因行
  ctx.font = uiFont(13, 600); ctx.fillStyle = UI.warn;
  ctx.fillText(causeTxt, UW / 2, y + 146);
  // 终末 pH
  ctx.font = uiMono(11, 600); ctx.fillStyle = UI.faint;
  var phTxt = (info.ph === null || info.ph === undefined) ? "--" : Number(info.ph).toFixed(2);
  ctx.fillText("TERMINAL pH  " + phTxt, UW / 2, y + 172);
  // 重生倒计时
  ctx.font = uiMono(13, 700); ctx.fillStyle = UI.neutral;
  ctx.fillText("REBOOT IN " + Math.ceil(info.resp || 0) + "s", UW / 2, y + 204);
  ctx.textAlign = "left";
}

/* ---------------- 战绩板（Tab） ---------------- */
/* ---------------- 战绩榜（可滚动） ---------------- */
var BOARD_ROW_H = 30, BOARD_HEAD_H = 120, BOARD_VISIBLE_ROWS = 14;
var ADMIN_HUMAN_ROWS = 4;

/** 榜单当前应有的行（在线玩家 + 已退出玩家），按积分/击杀排序 */
function boardRowsNow() {
  var arr = [];
  for (var pid in players) arr.push(players[pid]);
  arr.sort(function (a, b) { return (b.s || 0) - (a.s || 0) || (b.k || 0) - (a.k || 0); });
  var offRows = (typeof offline !== "undefined" && offline) ? offline.slice() : [];
  offRows.sort(function (a, b) { return (b.s || 0) - (a.s || 0); });
  for (var oi = 0; oi < offRows.length; oi++) {
    var o = offRows[oi];
    arr.push({ _off: true, n: o.n, team: o.team, k: o.k, d: o.d, s: o.s,
               ph: null, buf: null, hp: 0, st: {}, bot: false });
  }
  return arr;
}

function boardScrollMax() {
  return Math.max(0, boardRowsNow().length - BOARD_VISIBLE_ROWS);
}

/** 管理面板里"人类玩家"列表（可滚动） */
function adminHumansNow() {
  var humans = [];
  for (var pid in players) if (!players[pid].bot) humans.push(players[pid]);
  humans.sort(function (a, b) { return a.id - b.id; });
  return humans;
}

function adminScrollMax() {
  return Math.max(0, adminHumansNow().length - ADMIN_HUMAN_ROWS);
}

/** 右侧滚动条：track 从 y0 到 y0+trackH，thumb 表示当前窗口 */
function drawScrollBar(x, y0, trackH, total, visible, offset, labelTxt) {
  if (total <= visible) return;
  ctx.fillStyle = "rgba(255,255,255,0.08)";
  uiRR(x, y0, 4, trackH, 2); ctx.fill();
  var thumbH = Math.max(24, Math.round(trackH * visible / total));
  var maxOff = Math.max(1, total - visible);
  var ty = y0 + Math.round((trackH - thumbH) * Math.min(1, offset / maxOff));
  ctx.fillStyle = "rgba(255,209,102,0.75)";
  uiRR(x, ty, 4, thumbH, 2); ctx.fill();
  if (labelTxt) {
    ctx.font = uiMono(9, 600); ctx.fillStyle = UI.faint;
    ctx.textAlign = "right";
    ctx.fillText(labelTxt, x - 6, y0 + trackH + 12);
    ctx.textAlign = "left";
  }
}

function drawBoard() {
  ctx.fillStyle = "rgba(4,7,10,0.9)";
  ctx.fillRect(0, 0, UW, UH);
  var arr = boardRowsNow();
  var maxRows = Math.min(arr.length, BOARD_VISIBLE_ROWS);
  var maxScroll = Math.max(0, arr.length - BOARD_VISIBLE_ROWS);
  var sc = Math.max(0, Math.min(maxScroll, (typeof scrollY === "object" && scrollY) ? scrollY.board : 0));
  var rowH = BOARD_ROW_H, headH = BOARD_HEAD_H;
  var w = Math.min(1080, UW - 60), h = Math.min(UH - 80, headH + maxRows * rowH + 120);
  var x = UW / 2 - w / 2, y = UH / 2 - h / 2;
  uiPanel(x, y, w, h, 14, { fill: "rgba(10,14,19,0.94)" });
  uiCorners(x, y, w, h, 18, "rgba(57,199,216,0.4)");
  ctx.textAlign = "left";
  ctx.font = uiMono(10, 600); ctx.fillStyle = UI.base;
  ctx.fillText("LAB REPORT // 实验记录", x + 26, y + 32);
  ctx.font = uiFont(24, 800); ctx.fillStyle = UI.ink;
  ctx.fillText("战绩榜", x + 26, y + 66);
  ctx.font = uiFont(11, 400); ctx.fillStyle = UI.faint;
  ctx.fillText("按研究积分排名 · 滚轮滚动列表 · 松开 Tab 关闭", x + 26, y + 88);

  // 表头
  var tx = x + 26, tw = w - 52;
  var cols = [52, tx + 76, tx + 400, tx + 500, tx + 570, tx + 650, tx + 730,
              tx + 820, tx + 900, tx + 980];
  var hy = y + headH;
  ctx.strokeStyle = UI.line; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(tx, hy - 18); ctx.lineTo(tx + tw, hy - 18); ctx.stroke();
  ctx.font = uiMono(9, 600); ctx.fillStyle = UI.faint;
  ctx.textAlign = "left";
  ctx.fillText("#", cols[0], hy - 4);
  ctx.fillText("PLAYER / 玩家", cols[1], hy - 4);
  ctx.fillText("SCORE / 积分", cols[2], hy - 4);
  ctx.fillText("K", cols[3], hy - 4);
  ctx.fillText("D", cols[4], hy - 4);
  ctx.fillText("pH", cols[5], hy - 4);
  ctx.fillText("BUFFER", cols[6], hy - 4);
  ctx.fillText("中和", cols[7], hy - 4);
  ctx.fillText("救援", cols[8], hy - 4);
  ctx.fillText("破甲", cols[9], hy - 4);

  for (var i = 0; i < maxRows; i++) {
    var p = arr[sc + i];
    if (!p) break;
    var isOff = !!p._off;
    var ry = hy + 18 + i * rowH;
    if (i % 2 === 0) {
      ctx.fillStyle = "rgba(255,255,255,0.035)";
      ctx.fillRect(tx, ry - 16, tw, rowH - 4);
    }
    ctx.textAlign = "left";
    ctx.font = uiMono(12, 700); ctx.fillStyle = (sc + i < 3 && !isOff) ? UI.warn : UI.faint;
    ctx.fillText(isOff ? "—" : String(sc + i + 1).padStart(2, "0"), cols[0], ry);
    // 队伍色条（离线玩家用灰色）
    ctx.fillStyle = isOff ? "rgba(120,132,144,0.5)" : (TEAM_COLOR[p.team] || UI.dim);
    uiRR(cols[1] - 14, ry - 10, 3, 13, 2); ctx.fill();
    ctx.font = uiFont(13, 600);
    ctx.fillStyle = isOff ? "rgba(150,163,175,0.9)"
      : (p.id === myId ? "#ffffff" : UI.ink);
    ctx.fillText(p.n + (p.bos ? "  BOSS" : (p.bot ? "  AI" : "")) + (isOff ? "  不在线" : "") +
                 (p.id === myId ? "（你）" : ""), cols[1], ry);
    ctx.font = uiMono(12, 700); ctx.fillStyle = isOff ? UI.dim : UI.warn;
    ctx.fillText(String(p.s || 0), cols[2], ry);
    if (isOff) {
      ctx.fillStyle = UI.dim;
      ctx.fillText(String(p.k || 0), cols[3], ry);
      ctx.fillText(String(p.d || 0), cols[4], ry);
      ctx.fillText("—", cols[5], ry);
      ctx.fillText("—", cols[6], ry);
      ctx.fillText("—", cols[7], ry);
      ctx.fillText("—", cols[8], ry);
      ctx.fillText("—", cols[9], ry);
      continue;
    }
    ctx.fillStyle = UI.ink; ctx.fillText(String(p.k || 0), cols[3], ry);
    ctx.fillStyle = UI.dim; ctx.fillText(String(p.d || 0), cols[4], ry);
    var pz = phZone(p.ph);
    ctx.fillStyle = pz.color;
    ctx.fillText((p.ph === null || p.ph === undefined) ? "--" : Number(p.ph).toFixed(2), cols[5], ry);
    ctx.fillStyle = ringColor(p.buf === null || p.buf === undefined ? p.hp : p.buf);
    ctx.fillText(Math.round(p.buf === null || p.buf === undefined ? p.hp : p.buf) + "%", cols[6], ry);
    var st = p.st || {};
    ctx.fillStyle = UI.base;
    ctx.fillText(String(st.dmg || 0), cols[7], ry);
    ctx.fillStyle = UI.neutral;
    ctx.fillText(String(st.rescue || 0), cols[8], ry);
    ctx.fillStyle = UI.warn;
    ctx.fillText(String(st.break || 0), cols[9], ry);
  }
  // 可滚动列表：滚动条 + 提示（人数超过可见行数时）
  if (arr.length > BOARD_VISIBLE_ROWS) {
    var trackTop = hy + 2, trackH = maxRows * rowH - 6;
    drawScrollBar(x + w - 22, trackTop, trackH, arr.length, BOARD_VISIBLE_ROWS, sc,
                  "共 " + arr.length + " 人 · 滚轮滚动（第 " + (sc + 1) + "-" +
                  Math.min(arr.length, sc + BOARD_VISIBLE_ROWS) + " 行）");
  }

  // 底部：对战地址
  var urls = window._serverUrls || [];
  var by = y + h - 30;
  ctx.textAlign = "left";
  ctx.font = uiMono(9.5, 600); ctx.fillStyle = UI.faint;
  ctx.fillText("JOIN // 把地址发给同学", x + 26, by);
  ctx.font = uiMono(12, 600); ctx.fillStyle = UI.base;
  ctx.fillText(urls.length ? urls.join("    ") : "—", x + 26, by + 18);
  ctx.textAlign = "left";
}

/* ---------------- 顶部提示 / 横幅（新字体） ---------------- */
/* ---------------- 主机控制面板（B）：AI 数量 + 化学参数热调 ---------------- */
var CHEM_ROWS = [
  { k: "ct0", label: "缓冲总量 C_T0", step: 0.01,
    fmt: function (v) { return Math.round(v * 1000) + " mmol"; } },
  { k: "pka", label: "缓冲 pKa", step: 0.05,
    fmt: function (v) { return Number(v).toFixed(2); } },
  { k: "acid", label: "酸/碱换算", step: 0.05,
    fmt: function (v) { return Number(v).toFixed(2) + " mmol/点"; } },
  { k: "destroy", label: "物理/氧化换算", step: 0.10,
    fmt: function (v) { return Number(v).toFixed(1) + " mmol/点"; } },
  { k: "low", label: "pH 阵亡下限", step: 0.10,
    fmt: function (v) { return Number(v).toFixed(2); } },
  { k: "high", label: "pH 阵亡上限", step: 0.10,
    fmt: function (v) { return Number(v).toFixed(2); } }
];

function drawAdminPanel() {
  if (!window._isHost || !joined || !showAdmin) { adminRects = []; return; }
  adminRects = [];
  var humansAll = adminHumansNow();
  var hMax = Math.max(0, humansAll.length - ADMIN_HUMAN_ROWS);
  var hs = Math.max(0, Math.min(hMax, (typeof scrollY === "object" && scrollY) ? scrollY.admin : 0));
  var humans = humansAll.slice(hs, hs + ADMIN_HUMAN_ROWS);
  var px = 20, py = 176, pw = 440;
  var bl = (typeof banList !== "undefined" && banList) ? banList : { names: [], ips: [] };
  var bn = (bl.names || []).slice(0, 3), bi = (bl.ips || []).slice(0, 3);
  var banRows = bn.length + bi.length;
  var h = 46 + 2 * 26 + 8 + 20 + CHEM_ROWS.length * 26 + 52 +
          (humansAll.length ? 26 + Math.max(1, humans.length) * 26 : 0) +
          (banRows ? 26 + banRows * 22 : 0) + 18;
  uiPanel(px, py, pw, h, 10, { fill: "rgba(9,13,18,0.94)",
                               strokeColor: "rgba(255,200,87,0.35)" });
  uiCorners(px, py, pw, h, 12, "rgba(255,200,87,0.4)");
  ctx.textAlign = "left";
  ctx.font = uiMono(9.5, 600); ctx.fillStyle = UI.warn;
  ctx.fillText("HOST PANEL // 主机控制", px + 14, py + 20);
  ctx.textAlign = "right"; ctx.font = uiMono(9, 600); ctx.fillStyle = UI.faint;
  ctx.fillText("B 关闭", px + pw - 14, py + 20);
  ctx.textAlign = "left";

  var y = py + 44;
  var aiRows = [["red", "红队 AI"], ["blue", "蓝队 AI"]];
  for (var i = 0; i < aiRows.length; i++) {
    var team = aiRows[i][0], cnt = 0;
    for (var pid2 in players)
      if (players[pid2].bot && players[pid2].team === team) cnt++;
    ctx.font = uiFont(12, 500); ctx.fillStyle = UI.dim;
    ctx.fillText(aiRows[i][1], px + 14, y + 14);
    ctx.font = uiMono(13, 700); ctx.fillStyle = TEAM_COLOR[team];
    ctx.fillText(String(cnt).padStart(2, "0"), px + 118, y + 14);
    for (var b = 0; b < 2; b++) {
      var bx = px + pw - (b === 0 ? 66 : 34), by = y;
      uiPanel(bx, by, 26, 20, 4, { fill: "rgba(255,255,255,0.07)" });
      ctx.textAlign = "center"; ctx.font = uiMono(12, 700); ctx.fillStyle = UI.ink;
      ctx.fillText(b === 0 ? "−" : "＋", bx + 13, by + 14);
      ctx.textAlign = "left";
      adminRects.push({ x: bx, y: by, w: 26, h: 20,
        msg: { t: "admin", team: team, n: Math.max(0, Math.min(6, cnt + (b === 0 ? -1 : 1))) } });
    }
    y += 26;
  }

  y += 8;
  ctx.font = uiMono(9.5, 600); ctx.fillStyle = UI.base;
  ctx.fillText("CHEM PARAMS // 化学参数（现场可改）", px + 14, y + 12);
  y += 20;
  var cfg = chemCfg || { ct0: 0.1, pka: 7.2, acid: 0.5, destroy: 1.0, low: 3.0, high: 11.0 };
  for (i = 0; i < CHEM_ROWS.length; i++) {
    var r = CHEM_ROWS[i], v = Number(cfg[r.k]);
    ctx.font = uiFont(11.5, 500); ctx.fillStyle = UI.dim;
    ctx.fillText(r.label, px + 14, y + 14);
    ctx.textAlign = "right"; ctx.font = uiMono(11, 700); ctx.fillStyle = UI.ink;
    ctx.fillText(r.fmt(v), px + pw - 70, y + 14);
    ctx.textAlign = "left";
    for (b = 0; b < 2; b++) {
      var sx = px + pw - (b === 0 ? 66 : 34), sy = y;
      uiPanel(sx, sy, 26, 20, 4, { fill: "rgba(255,255,255,0.07)" });
      ctx.textAlign = "center"; ctx.font = uiMono(12, 700); ctx.fillStyle = UI.ink;
      ctx.fillText(b === 0 ? "−" : "＋", sx + 13, sy + 14);
      ctx.textAlign = "left";
      adminRects.push({ x: sx, y: sy, w: 26, h: 20,
        msg: { t: "admin", act: "chem", k: r.k,
               v: Number((v + (b === 0 ? -1 : 1) * r.step).toFixed(3)) } });
    }
    y += 26;
  }

  // ---- 随机事件控制（主机现场演示）----
  y += 8;
  ctx.font = uiMono(9.5, 600); ctx.fillStyle = UI.warn;
  ctx.fillText("EVENTS // 随机事件", px + 14, y + 12);
  y += 20;
  var evBtns = [
    { label: "随机事件", id: "random", w: 76 },
    { label: "Boss 战", id: "boss", w: 62 },
    { label: "关闭事件", id: "off", w: 72 }
  ];
  var evx = px + 14;
  for (var ei = 0; ei < evBtns.length; ei++) {
    var eb = evBtns[ei];
    uiPanel(evx, y, eb.w, 20, 4, { fill: "rgba(255,209,102,0.14)",
                                   strokeColor: "rgba(255,209,102,0.35)" });
    ctx.textAlign = "center"; ctx.font = uiMono(10, 600); ctx.fillStyle = UI.warn;
    ctx.fillText(eb.label, evx + eb.w / 2, y + 14);
    ctx.textAlign = "left";
    adminRects.push({ x: evx, y: y, w: eb.w, h: 20,
                      msg: { t: "admin", act: "event", id: eb.id } });
    evx += eb.w + 8;
  }
  y += 26;

  if (humansAll.length) {
    y += 6;
    ctx.font = uiMono(9.5, 600); ctx.fillStyle = UI.warn;
    ctx.fillText("PLAYERS // 调队 / 赋能 / 封禁", px + 14, y + 12);
    if (humansAll.length > ADMIN_HUMAN_ROWS) {
      ctx.textAlign = "right"; ctx.font = uiMono(9, 600); ctx.fillStyle = UI.faint;
      ctx.fillText("滚轮滚动 " + (hs + 1) + "-" + (hs + humans.length) + " / " + humansAll.length,
                   px + pw - 14, y + 12);
      ctx.textAlign = "left";
    }
    y += 20;
    for (i = 0; i < humans.length; i++) {
      var p = humans[i];
      var nm = String(p.n || "");
      if (nm.length > 10) nm = nm.slice(0, 9) + "…";
      ctx.font = uiFont(11.5, 500);
      ctx.fillStyle = p.id === myId ? "#ffffff" : UI.ink;
      ctx.fillText(nm + (p.id === myId ? "（你）" : ""), px + 14, y + 14);
      for (var tb = 0; tb < 2; tb++) {
        var t2 = tb === 0 ? "red" : "blue", tx = px + 150 + tb * 30;
        var cur = p.team === t2;
        uiPanel(tx, y, 28, 20, 4,
                { fill: cur ? TEAM_COLOR[t2] : "rgba(255,255,255,0.07)", stroke: false });
        ctx.textAlign = "center"; ctx.font = uiMono(10, 700);
        ctx.fillStyle = cur ? "#08110d" : UI.dim;
        ctx.fillText(tb === 0 ? "红" : "蓝", tx + 14, y + 14);
        ctx.textAlign = "left";
        adminRects.push({ x: tx, y: y, w: 28, h: 20,
          msg: { t: "admin", act: "team", id: p.id, team: t2 } });
      }
      var pow = p.pw || "none";
      var pidx = PW_CYCLE.indexOf(pow);
      var nextP = PW_CYCLE[(pidx + 1) % PW_CYCLE.length];
      var pwx = px + 218;
      uiPanel(pwx, y, 78, 20, 4,
              { fill: pow === "none" ? "rgba(255,255,255,0.07)" : "rgba(125,255,178,0.18)",
                stroke: false });
      ctx.textAlign = "center"; ctx.font = uiMono(9.5, 600);
      ctx.fillStyle = pow === "none" ? UI.dim : UI.neutral;
      ctx.fillText(pow === "none" ? "赋能 +" : PW_NAME[pow], pwx + 39, y + 14);
      ctx.textAlign = "left";
      adminRects.push({ x: pwx, y: y, w: 78, h: 20,
        msg: { t: "admin", act: "power", id: p.id, pw: nextP } });
      // 封禁：ID / IP（立即踢线并写入 save/bans.json）
      var banBtns = [{ k: "id", label: "封ID" }, { k: "ip", label: "封IP" }];
      for (var bb = 0; bb < 2; bb++) {
        var bbx = px + 304 + bb * 50;
        uiPanel(bbx, y, 46, 20, 4,
                { fill: "rgba(255,77,94,0.16)", strokeColor: "rgba(255,77,94,0.5)" });
        ctx.textAlign = "center"; ctx.font = uiMono(9, 700);
        ctx.fillStyle = UI.danger;
        ctx.fillText(banBtns[bb].label, bbx + 23, y + 14);
        ctx.textAlign = "left";
        adminRects.push({ x: bbx, y: y, w: 46, h: 20,
          msg: { t: "admin", act: "ban", id: p.id, mode: banBtns[bb].k } });
      }
      uiPanel(px + 406, y, 26, 20, 4, { fill: "rgba(255,255,255,0.07)" });
      ctx.textAlign = "center"; ctx.font = uiMono(10, 700); ctx.fillStyle = UI.dim;
      ctx.fillText("踢", px + 419, y + 14);
      ctx.textAlign = "left";
      adminRects.push({ x: px + 406, y: y, w: 26, h: 20,
        msg: { t: "admin", act: "kick", id: p.id } });
      y += 26;
    }
    if (humansAll.length > ADMIN_HUMAN_ROWS) {
      drawScrollBar(px + pw - 9, y - humans.length * 26 - 2, humans.length * 26,
                    humansAll.length, ADMIN_HUMAN_ROWS, hs, "");
    }
  }

  // ---- 封禁名单（点击解封；封禁记录持久化在服务器）----
  if (banRows) {
    y += 6;
    ctx.font = uiMono(9.5, 600); ctx.fillStyle = UI.danger;
    ctx.fillText("BANNED // 封禁名单", px + 14, y + 12);
    ctx.textAlign = "right"; ctx.font = uiMono(8.5, 600); ctx.fillStyle = UI.faint;
    var totB = ((bl.n_names || bn.length) + (bl.n_ips || bi.length));
    ctx.fillText("ID " + (bl.n_names || 0) + " · IP " + (bl.n_ips || 0) + (totB > banRows ? "（显示前 3）" : ""),
                 px + pw - 14, y + 12);
    ctx.textAlign = "left";
    y += 20;
    var banItems = [];
    for (i = 0; i < bn.length; i++) banItems.push({ k: "id", v: bn[i] });
    for (i = 0; i < bi.length; i++) banItems.push({ k: "ip", v: bi[i] });
    for (i = 0; i < banItems.length; i++) {
      var bit = banItems[i];
      var btxt = (bit.k === "id" ? "ID " : "IP ") + String(bit.v || "");
      if (btxt.length > 34) btxt = btxt.slice(0, 33) + "…";
      ctx.font = uiMono(9.5, 500); ctx.fillStyle = UI.dim;
      ctx.fillText(btxt, px + 16, y + 13);
      uiPanel(px + pw - 74, y, 62, 18, 4,
              { fill: "rgba(125,255,178,0.10)", strokeColor: "rgba(125,255,178,0.35)" });
      ctx.textAlign = "center"; ctx.font = uiMono(9, 600); ctx.fillStyle = UI.neutral;
      ctx.fillText("解封", px + pw - 43, y + 13);
      ctx.textAlign = "left";
      adminRects.push({ x: px + pw - 74, y: y, w: 62, h: 18,
        msg: { t: "admin", act: "unban", kind: bit.k, value: bit.v } });
      y += 22;
    }
  }

  ctx.font = uiMono(9, 600); ctx.fillStyle = UI.faint;
  ctx.fillText("封ID/封IP 会立即踢线并写入 save/bans.json", px + 14, py + h - 10);
}

/* ---------------- 随机事件 / Boss 战 HUD ---------------- */
function drawEventChip(now) {
  if (typeof eventInfo === "undefined" || !eventInfo || !eventInfo.left) return;
  var left = Math.ceil(eventInfo.left);
  var label = "事件 · " + eventInfo.name;
  var sub = left + "s";
  ctx.font = uiMono(10.5, 700);
  var w = ctx.measureText(label).width + ctx.measureText(sub).width + 44;
  var x = UW / 2 - w / 2, y = 112;
  var urgent = left <= 5;
  uiPanel(x, y, w, 24, 6, {
    fill: "rgba(8,12,17,0.84)",
    strokeColor: urgent ? "rgba(255,82,82,0.8)" : "rgba(255,209,102,0.5)" });
  ctx.textAlign = "left";
  ctx.font = uiMono(10.5, 700);
  ctx.fillStyle = urgent ? UI.danger : UI.warn;
  ctx.fillText(label, x + 12, y + 16);
  ctx.textAlign = "right";
  ctx.fillStyle = urgent ? UI.danger : UI.ink;
  ctx.fillText(sub, x + w - 12, y + 16);
  ctx.textAlign = "left";
}

function drawBossBar(now) {
  if (typeof bossInfo === "undefined" || !bossInfo) return;
  var w = Math.min(720, UW * 0.62), h = 54;
  var x = UW / 2 - w / 2, y = 142;
  uiPanel(x, y, w, h, 8, { fill: "rgba(12,8,14,0.9)",
                           strokeColor: "rgba(255,91,208,0.55)" });
  ctx.textAlign = "left";
  ctx.font = uiFont(13, 800);
  ctx.fillStyle = "#ff5bd0";
  ctx.fillText("BOSS · " + (bossInfo.n || "?"), x + 12, y + 18);
  var sh = bossInfo.bsh || 0, shMax = bossInfo.bmx || 1;
  var frac = Math.max(0, Math.min(1, sh / shMax));
  uiBar(x + 12, y + 24, w - 24, 10, frac, "#ff5bd0");
  ctx.font = uiMono(9, 600);
  ctx.fillStyle = UI.faint;
  ctx.textAlign = "right";
  ctx.fillText("护盾 " + Math.round(sh) + " / " + Math.round(shMax), x + w - 12, y + 18);
  ctx.textAlign = "left";
  var ring = (bossInfo.buf === null || bossInfo.buf === undefined) ? 0 : bossInfo.buf;
  uiBar(x + 12, y + 38, w - 24, 7, Math.max(0, Math.min(1, ring / 100)),
        ringColor(ring));
  ctx.fillStyle = UI.faint;
  ctx.font = uiMono(9, 600);
  ctx.fillText("缓冲 " + Math.round(ring) + "%", x + 12, y + 52);
  // 贡献前 3
  if (typeof bossContrib !== "undefined" && bossContrib.length) {
    var txt = "伤害贡献：" + bossContrib.slice(0, 3).map(function (r) {
      return r.n + " " + r.dmg;
    }).join(" · ");
    ctx.textAlign = "right";
    ctx.fillStyle = UI.neutral;
    ctx.fillText(txt, x + w - 12, y + 52);
    ctx.textAlign = "left";
  }
}

function drawEventBanner(now) {
  if (typeof announceInfo === "undefined" || !announceInfo) return;
  if (now > announceInfo.t) { announceInfo = null; return; }
  var w = Math.min(620, UW * 0.6), h = 74;
  var x = UW / 2 - w / 2, y = UH * 0.24;
  uiPanel(x, y, w, h, 10, { fill: "rgba(8,12,17,0.9)",
                            strokeColor: "rgba(255,209,102,0.6)" });
  ctx.textAlign = "center";
  ctx.font = uiFont(18, 800);
  ctx.fillStyle = UI.warn;
  ctx.fillText(announceInfo.title, UW / 2, y + 32);
  ctx.font = uiFont(11.5, 500);
  ctx.fillStyle = UI.ink;
  ctx.fillText(announceInfo.desc, UW / 2, y + 56);
  ctx.textAlign = "left";
}

function drawBossResult(now) {
  if (typeof bossResult === "undefined" || !bossResult) return;
  if (now > (typeof bossResultT === "number" ? bossResultT : 0)) return;
  var rows = (bossResult.top || []).slice(0, 5);
  var w = Math.min(560, UW * 0.56), h = 92 + rows.length * 22;
  var x = UW / 2 - w / 2, y = UH * 0.3;
  uiPanel(x, y, w, h, 10, { fill: "rgba(10,14,19,0.94)",
                            strokeColor: "rgba(255,91,208,0.6)" });
  ctx.textAlign = "left";
  ctx.font = uiFont(17, 800);
  ctx.fillStyle = "#ff5bd0";
  ctx.fillText("BOSS 战结算 · " + (bossResult.boss || "BOSS"), x + 18, y + 30);
  ctx.font = uiMono(9.5, 600);
  ctx.fillStyle = UI.faint;
  ctx.fillText("最后一击：" + (bossResult.by || "?") + " · 按伤害贡献发放积分", x + 18, y + 50);
  for (var i = 0; i < rows.length; i++) {
    var r = rows[i], ry = y + 78 + i * 22;
    ctx.font = uiMono(11, 700);
    ctx.fillStyle = i === 0 ? UI.warn : UI.dim;
    ctx.fillText(String(i + 1).padStart(2, "0"), x + 18, ry);
    ctx.font = uiFont(12, 600);
    ctx.fillStyle = UI.ink;
    ctx.fillText(r.n || "?", x + 46, ry);
    ctx.textAlign = "right";
    ctx.font = uiMono(11, 600);
    ctx.fillStyle = UI.neutral;
    ctx.fillText("伤害 " + (r.dmg || 0), x + w - 110, ry);
    ctx.fillStyle = UI.warn;
    ctx.fillText("+" + (r.reward || 0) + " 分", x + w - 18, ry);
    ctx.textAlign = "left";
  }
}

/* ---------------- HUD 总入口 ---------------- */
function drawHUD(me, now) {
  drawScoreBar();
  drawEventChip(now);
  drawBossBar(now);
  drawEventBanner(now);
  drawBossResult(now);
  drawKillFeed(now);
  drawKillConfirm(now);
  drawKillImpact(now);
  drawDamageDirs(now);
  drawStatusCard(me, now);
  drawWeaponRack(me);
  // 酸雨弹就绪提示
  if (me.nk_ok && me.al) {
    var pulse = (Math.sin(now / 220) + 1) / 2;
    ctx.textAlign = "center";
    ctx.font = uiMono(11, 700);
    var txt = "F₂ LEAK READY // 氟泄漏就绪 [ N ]";
    var w = ctx.measureText(txt).width + 34;
    uiPanel(UW / 2 - w / 2, 80, w, 28, 5,
            { fill: "rgba(8,12,17,0.8)", strokeColor: "rgba(255,200,87," + (0.25 + pulse * 0.4).toFixed(2) + ")" });
    ctx.fillStyle = UI.warn;
    ctx.fillText(txt, UW / 2, 99);
    ctx.textAlign = "left";
  }
  // 地图名 + 换图倒计时 + 帧率自检（左上角，管理面板打开时让位）
  if (typeof ACTIVE_MAP !== "undefined" && ACTIVE_MAP && !showAdmin) {
    var cd = (typeof mapSwitchIn === "number" && mapSwitchIn > 0 && mapSwitchIn < 3600)
      ? "   ⟳ 换图 " + fmtCountdown(mapSwitchIn) : "";
    ctx.font = uiMono(9, 600);
    ctx.fillStyle = (mapSwitchIn > 0 && mapSwitchIn < 30) ? UI.warn : UI.faint;
    ctx.textAlign = "left";
    ctx.fillText("MAP // " + (ACTIVE_MAP.name || "") + cd, 22, 174);
    if (typeof perfLabel === "function") {          // 帧率/卡顿自检（低画质时变黄）
      ctx.fillStyle = (typeof lowQuality !== "undefined" && lowQuality) ? UI.warn : UI.faint;
      ctx.fillText(perfLabel(), 22, 187);
    }
  }
  // 实验手册（H）
  drawHelpPanel(now);
}

/* ---------------- 实验手册（H）：把设定/机制讲清楚 ---------------- */
function drawHelpPanel(now) {
  if (!showHelp) return;
  ctx.fillStyle = "rgba(4,7,10,0.86)";
  ctx.fillRect(0, 0, UW, UH);
  var w = Math.min(880, UW - 60), h = Math.min(690, UH - 40);
  var x = (UW - w) / 2, y = (UH - h) / 2;
  uiPanel(x, y, w, h, 12, { fill: "rgba(9,13,18,0.97)", strokeColor: "rgba(125,255,178,0.32)" });
  uiCorners(x, y, w, h, 14, "rgba(125,255,178,0.4)");
  ctx.textAlign = "left";
  ctx.font = uiMono(10, 600); ctx.fillStyle = UI.neutral;
  ctx.fillText("FIELD MANUAL // 实验手册", x + 22, y + 28);
  ctx.textAlign = "right"; ctx.font = uiMono(9, 600); ctx.fillStyle = UI.faint;
  ctx.fillText("H 关闭", x + w - 22, y + 28);
  ctx.textAlign = "left";

  var lines = [
    ["目标", "把对方的 1 L 虚拟缓冲体系推出安全区：pH ≤ 3.00 或 ≥ 11.00，或缓冲对被剥离到 0；按团队积分/击杀持续对抗。"],
    ["操作", "WASD 移动 · 鼠标瞄准 · 左键开火 · 1–9 换试剂 · R 换弹 · E 烟雾 · Q AOE · F 指示剂 · N 氟泄漏 · B 主机面板"],
    ["试剂架", "酸：盐酸枪 / 硅酸 / 高氯酸　氧化：喷淋器　碱：碱液枪 / 碱式连狙 / 石灰水　还原支援：维C　物理：搅拌棒"],
    ["碱式连狙", "7 号位：半自动碱性连狙，85 伤 / 5 发弹匣 / 2.5s 装填；射速介于步枪与狙击之间，用来平衡酸系一家独大。"],
    ["机枪抖动", "6 号位 NaOH 机枪连射会累积后坐力：散布逐步扩大并带准星抖动，点射比长按更稳。"],
    ["pH 尺子", "7 是中性；数字越小越酸，越大越碱。它不是线性血条——先被缓冲“顶住”，越过平台后会断崖式下跌。"],
    ["缓冲环", "缓冲对储备：酸碱只改 pH，不直接扣环；只有中和/氧化才消耗缓冲对。环越低，越接近“无缓冲”状态。"],
    ["四个通道", "酸/碱：只改 pH（反向会中和并消耗缓冲对）；氧化：直接剥离缓冲对（满缓冲效率最高）；物理：稳定剥离缓冲。"],
    ["克制 1", "满缓冲时酸/碱伤害 ×0.70；氧化剂满缓冲 ×1.25，缓冲 <25% 后 ×0.85。"],
    ["克制 2", "同向补伤 +12%，反向 −15%；反向会消耗缓冲对。节奏：氧化开甲 → 酸/碱补刀。"],
    ["中和救援", "对酸蚀队友喷碱、对碱蚀队友喷酸；中和会消耗缓冲对（pH 回中性但环下降）；方向错无效。维C 修复氧化剥离。"],
    ["情报", "F 指示剂标记 3 秒，显示敌人 pH 与缓冲；可见敌人头顶常显 pH；队友异常时自动提示。"],
    ["弹药", "所有武器有备弹（步枪 300 / 狙击 30 / 机枪 400）；站上 + 补给点回满，重生/换图补满。"],
    ["氟泄漏", "3 万分触发；F₂ 强氧化，造成 150 mmol 缓冲剥离，满缓冲也必死。特殊事件进行中禁用（N 无效）。"],
    ["出生缓冲带", "每队出生区正上/正下方有一条缓冲地带：敌方连续停留 1.2s 后持续剥离缓冲，堵家会被反噬。"],
    ["派对事件", "随机事件含烫手山芋（贴人传递炸弹）、岩浆地板（圈外掉缓冲）、抢椅子（占点得分）等小游戏玩法。"],
    ["移速", "切刀（1 号搅拌棒）移速 +18%；物理击杀额外 +100 分。"],
    ["计分", "击杀 100、助攻 30、连杀有额外加成；8 连杀成为高价值目标（击杀 +150）；积分达到 3 万可放氟泄漏。"],
    ["现实提醒", "游戏里的“中和救援”是虚构机制。真实酸碱灼伤：立即大量清水冲洗，不要自行中和；详见《游戏机制与科学说明》。"],
  ];
  var yy = y + 58;
  for (var i = 0; i < lines.length; i++) {
    ctx.font = uiFont(11.5, 700); ctx.fillStyle = UI.neutral;
    ctx.fillText(lines[i][0], x + 22, yy);
    ctx.font = uiFont(11.5, 500); ctx.fillStyle = UI.ink;
    ctx.fillText(lines[i][1], x + 112, yy);
    yy += 40;
  }
  ctx.font = uiMono(9.5, 600); ctx.fillStyle = UI.faint;
  ctx.fillText("课堂模式：把化学当“规则”而不是“题库”；AI 训练用同一套规则做对手/被试。", x + 22, y + h - 18);
}


// ================= 封禁屏（黑屏 + 封禁提示 + 社区公约） =================
var BAN_COVENANT = [
  ["文明 ID / 文明交流", "昵称与聊天不得含侮辱、脏话、人身攻击、刷屏或冒充他人；违规 ID 将被房主直接封禁。"],
  ["政治正确", "禁止涉政敏感、地域/民族/性别/宗教歧视、色情暴力与任何违法违规内容，违者永久封禁。"],
  ["公平竞技", "禁止外挂、脚本、恶意刷分、卡点堵家与消极挂机；请尊重对手与队友。"],
  ["隐私与安全", "不得在房间内发布他人真实身份、住址、联系方式或任何隐私信息。"],
  ["处罚说明", "房主可封禁玩家 ID 或机器 IP；本地封禁记录保存在服务器，重开浏览器不会解除。"],
  ["申诉", "认为误封请携带昵称与时间联系房主解封；房主可在主机面板随时解封 ID / IP。"]
];

function drawBanScreen(info, now) {
  if (!info) return;
  ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  ctx.fillStyle = "#04060a";
  ctx.fillRect(0, 0, VW, VH);
  // 背景网格与暗红光晕，避免"纯黑死机"观感
  ctx.strokeStyle = "rgba(255,77,94,0.05)";
  ctx.lineWidth = 1;
  for (var gx = 0; gx < VW; gx += 64) { ctx.beginPath(); ctx.moveTo(gx, 0); ctx.lineTo(gx, VH); ctx.stroke(); }
  for (var gy = 0; gy < VH; gy += 64) { ctx.beginPath(); ctx.moveTo(0, gy); ctx.lineTo(VW, gy); ctx.stroke(); }
  var rg = ctx.createRadialGradient(VW / 2, VH * 0.42, 20, VW / 2, VH * 0.42, Math.max(VW, VH) * 0.7);
  rg.addColorStop(0, "rgba(255,77,94,0.10)");
  rg.addColorStop(1, "rgba(0,0,0,0)");
  ctx.fillStyle = rg;
  ctx.fillRect(0, 0, VW, VH);

  var w = Math.min(760, VW - 40), h = Math.min(600, VH - 40);
  var x = (VW - w) / 2, y = (VH - h) / 2;
  var uis = (typeof UIS === "number" && UIS > 0) ? UIS : 1;
  ctx.save();
  ctx.translate(VW / 2, VH / 2);
  ctx.scale(uis, uis);
  ctx.translate(-VW / 2, -VH / 2);
  var xx = (VW - w) / 2, yy = (VH - h) / 2;

  uiPanel(xx, yy, w, h, 12, { fill: "rgba(8,10,14,0.96)", strokeColor: "rgba(255,77,94,0.42)" });
  uiCorners(xx, yy, w, h, 16, "rgba(255,77,94,0.55)");

  var pulse = 0.55 + 0.45 * Math.sin(now / 520);
  ctx.textAlign = "left";
  ctx.font = uiMono(10, 700);
  ctx.fillStyle = "rgba(255,77,94," + pulse.toFixed(2) + ")";
  ctx.fillText("RP SECURITY // ACCESS DENIED", xx + 26, yy + 34);
  ctx.textAlign = "right";
  ctx.fillStyle = UI.faint;
  ctx.fillText("LAN SESSION · 本机已被拒绝接入", xx + w - 26, yy + 34);

  ctx.textAlign = "center";
  ctx.font = uiFont(38, 800);
  ctx.fillStyle = info.kind === "kick" ? "#ffc857" : "#ff4d5e";
  ctx.fillText(info.kind === "kick" ? "你已被移出房间" : "你已被封禁", VW / 2, yy + 96);
  ctx.font = uiMono(11, 700);
  ctx.fillStyle = "rgba(255,255,255,0.55)";
  ctx.fillText(info.kind === "kick" ? "REMOVED BY HOST" : "BANNED BY HOST", VW / 2, yy + 120);

  // 封禁信息
  var lines = [];
  if (info.kind === "ip") {
    lines.push(["封禁类型", "机器 IP 封禁 / IP BAN"]);
    lines.push(["封禁目标", String(info.ip || info.name || "本机 IP")]);
  } else if (info.kind === "id") {
    lines.push(["封禁类型", "玩家 ID 封禁 / NAME BAN"]);
    lines.push(["封禁目标", String(info.name || "该昵称")]);
  }
  lines.push(["原因", String(info.why || "违反社区公约")]);
  ctx.textAlign = "left";
  var ly = yy + 152;
  for (var i = 0; i < lines.length; i++) {
    ctx.font = uiFont(11, 700); ctx.fillStyle = UI.faint;
    ctx.fillText(lines[i][0], xx + 34, ly);
    ctx.font = uiFont(12.5, 600); ctx.fillStyle = UI.ink;
    ctx.fillText(lines[i][1], xx + 118, ly);
    ly += 24;
  }

  // 分割线 + 社区公约
  ctx.strokeStyle = "rgba(255,77,94,0.28)"; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(xx + 26, ly + 6); ctx.lineTo(xx + w - 26, ly + 6); ctx.stroke();
  ctx.font = uiMono(10, 700); ctx.fillStyle = UI.warn;
  ctx.fillText("社区公约 / COMMUNITY COVENANT", xx + 34, ly + 32);
  var cy = ly + 56;
  for (var j = 0; j < BAN_COVENANT.length; j++) {
    ctx.font = uiFont(11.5, 700); ctx.fillStyle = "rgba(255,200,87,0.92)";
    ctx.fillText("· " + BAN_COVENANT[j][0], xx + 34, cy);
    ctx.font = uiFont(11.5, 500); ctx.fillStyle = "rgba(231,238,246,0.86)";
    ctx.fillText(BAN_COVENANT[j][1], xx + 190, cy);
    cy += 30;
  }

  ctx.textAlign = "center";
  ctx.font = uiMono(10, 600); ctx.fillStyle = UI.faint;
  ctx.fillText("如有异议请联系房主申诉 · 本机封禁写入服务器 save/bans.json", VW / 2, yy + h - 26);
  ctx.restore();
}

function drawHintBanner(now) {
  if (hintMsg && now < hintT) {
    ctx.textAlign = "center";
    ctx.font = uiFont(15, 600);
    var w = ctx.measureText(hintMsg).width + 36;
    uiPanel(UW / 2 - w / 2, UH * 0.25 - 20, w, 32, 6, { fill: "rgba(8,12,17,0.82)" });
    ctx.fillStyle = UI.warn;
    ctx.fillText(hintMsg, UW / 2, UH * 0.25 + 2);
    ctx.textAlign = "left";
  }
  if (banner && now < bannerT) {
    ctx.textAlign = "center";
    ctx.font = uiFont(26, 800);
    var w2 = ctx.measureText(banner).width + 56;
    uiPanel(UW / 2 - w2 / 2, UH * 0.32 - 24, w2, 40, 8,
            { fill: "rgba(8,12,17,0.86)", strokeColor: "rgba(255,200,87,0.4)" });
    ctx.fillStyle = UI.warn;
    ctx.fillText(banner, UW / 2, UH * 0.32 + 3);
    ctx.textAlign = "left";
  }
}
