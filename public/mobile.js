/* Reaction Protocol · 手机版客户端（mobile.html 专用）
 * 依赖：game.js（引擎/协议/世界渲染）、mobile_layout.js（布局与命中判定）
 *
 * 设计原则（v3）：
 *   1) 手机横屏按 768×362 这类真实视口设计，不再复用 PC 的 1366×768 HUD；
 *   2) 所有可点元素由 ML.compute 统一排布，互不重叠、不越安全区；
 *   3) 命中判定统一走 ML.hit，优先级 菜单 > 功能键 > 武器条 > 开火 > 分区，
 *      从根上避免“开火键吃掉聊天/管理”一类回归；
 *   4) 所有绘制显式 setTransform(DPR) ，坐标即 CSS 像素，与触摸一致。
 */
window.__MOBILE__ = true;

var isTouch = true;

// ================= 输入状态 =================
var touchMove = { active: false, id: null, ox: 0, oy: 0, dx: 0, dy: 0 };
var boardDrag = null;        // 战绩板拖动滚动状态 {id,y0,moved}
var touchFire = false;
var touchScope = false;
var aimAng = null;                                   // 持久瞄准角；null=跟随人物朝向
var fireDrag = { active: false, id: null, ox: 0, oy: 0 };
var aimDrag = { active: false, id: null, ox: 0, oy: 0 };
var LAY = null;                                      // 当前布局
var mlAdminRects = [];                               // 手机管理面板热区
window.__mobileLastHit = "";
window.__mobileLayoutReport = function () {
  var rep = ML.report(VW, VH, mlOpts());
  rep.VW = VW; rep.VH = VH; rep.DPR = DPR;      // 契约字段：真机校验脚本依赖
  return rep;
};

function mlOpts() {
  var me = players[myId];
  return {
    host: !!window._isHost,
    sniper: !!(me && me.w === "sniper"),
    nuke: !!(me && (me.nk_ok || (me.s || 0) >= 30000))
  };
}
function mlRefresh() { LAY = ML.compute(VW, VH, mlOpts()); return LAY; }
function mlLayout() { if (!LAY || LAY.vw !== VW || LAY.vh !== VH) mlRefresh(); return LAY; }

// 兼容旧调用点（game.js 里没有引用，但保留别名便于调试）
function touchLayout() { return mlRefresh(); }

// ================= 辅助绘制 =================
function mPanel(x, y, w, h, r, opts) {
  if (typeof uiPanel === "function") return uiPanel(x, y, w, h, r || 6, opts);
  ctx.fillStyle = (opts && opts.fill) || "rgba(8,12,17,0.78)";
  ctx.beginPath();
  if (ctx.roundRect) { ctx.roundRect(x, y, w, h, r || 6); ctx.fill(); }
  else ctx.fillRect(x, y, w, h);
}
function mText(txt, x, y, font, color, align) {
  ctx.font = font || "12px sans-serif";
  ctx.fillStyle = color || "#fff";
  ctx.textAlign = align || "center";
  ctx.fillText(txt, x, y);
  ctx.textAlign = "left";
}
function mHint(msg, ms) {
  hintMsg = msg;
  hintT = performance.now() + (ms || 1500);
}

// ================= 触摸输入 =================
cv.addEventListener("touchstart", function (e) {
  e.preventDefault();
  initAudio();
  var lay = mlRefresh();
  var ci = document.getElementById("chatInput");
  for (var i = 0; i < e.changedTouches.length; i++) {
    var t = e.changedTouches[i];
    var tx = t.clientX, ty = t.clientY;

    // 聊天输入打开时：点画面先收起聊天，避免误开火
    if (ci && ci.classList.contains("show")) {
      if (ci.value.trim() && ws && ws.readyState === 1)
        ws.send(JSON.stringify({ t: "chat", msg: ci.value.trim() }));
      ci.value = ""; ci.classList.remove("show"); ci.blur();
      continue;
    }
    // 战绩板：拖动滚列表；不拖动（轻点）才关闭
    if (showBoard) {
      if (!boardDrag) boardDrag = { id: t.identifier, y0: ty, moved: false };
      continue;
    }
    // 烟雾落点选择（模态）：再按一次“烟雾”取消，其余点击=落点
    if (typeof smokeAim !== "undefined" && smokeAim) {
      var hs = ML.hit(lay, tx, ty);
      if (hs && hs.kind === "ring" && hs.id === "smoke") {
        smokeAim = false;
        mHint("已取消烟雾", 1000);
        window.__mobileLastHit = "smoke:cancel";
        continue;
      }
      window.__mobileLastHit = "smokeaim";
      placeSmokeAt(tx, ty);
      continue;
    }
    // 管理面板独占输入
    if (showAdmin && window._isHost) {
      var msg = mobileAdminHit(tx, ty);
      if (msg) {
        if (ws && ws.readyState === 1) ws.send(JSON.stringify(msg));
        sfx("reload");
        window.__mobileLastHit = "admin:" + (msg.act || "ai");
      }
      continue;
    }

    var h = ML.hit(lay, tx, ty);
    window.__mobileLastHit = h.kind + ":" + (h.id || "");
    if (h.kind === "menu") { handleMenu(h.id); continue; }
    if (h.kind === "ring") { handleRing(h.id); continue; }
    if (h.kind === "chip") {
      if (ws && ws.readyState === 1) ws.send(JSON.stringify({ t: "sw", w: h.id }));
      sfx("reload");
      continue;
    }
    if (h.kind === "fire") {
      touchFire = true;
      fireDrag = { active: true, id: t.identifier, ox: tx, oy: ty };
      continue;
    }
    if (h.kind === "move") {
      if (!touchMove.active) {
        touchMove.active = true; touchMove.id = t.identifier;
        touchMove.ox = tx; touchMove.oy = ty; touchMove.dx = touchMove.dy = 0;
      }
      continue;
    }
    if (!aimDrag.active) aimDrag = { active: true, id: t.identifier, ox: tx, oy: ty };
  }
}, { passive: false });

cv.addEventListener("touchmove", function (e) {
  e.preventDefault();
  for (var i = 0; i < e.changedTouches.length; i++) {
    var t = e.changedTouches[i];
    var tx = t.clientX, ty = t.clientY;
    // 战绩板拖动滚动（手机端列表可滚动）
    if (showBoard && boardDrag && t.identifier === boardDrag.id) {
      var mdy = ty - boardDrag.y0;
      if (Math.abs(mdy) > 10) {
        boardDrag.moved = true;
        if (typeof scrollY === "object" && scrollY) {
          var mMax = (typeof boardScrollMax === "function") ? boardScrollMax() : 0;
          var rowsMoved = Math.round(-mdy / 18);
          scrollY.board = Math.max(0, Math.min(mMax, scrollY.board + rowsMoved));
        }
        boardDrag.y0 = ty;
      }
      continue;
    }
    if (touchMove.active && t.identifier === touchMove.id) {
      var R = mlLayout().stick.r;
      var dx = tx - touchMove.ox, dy = ty - touchMove.oy;
      var len = Math.hypot(dx, dy);
      if (len > R) { dx = dx / len * R; dy = dy / len * R; }
      touchMove.dx = dx / R; touchMove.dy = dy / R;
    } else if (aimDrag.active && t.identifier === aimDrag.id) {
      var adx = tx - aimDrag.ox, ady = ty - aimDrag.oy;
      if (Math.hypot(adx, ady) > 6) aimAng = Math.atan2(ady, adx);
    } else if (fireDrag.active && t.identifier === fireDrag.id) {
      var fdx = tx - fireDrag.ox, fdy = ty - fireDrag.oy;
      if (Math.hypot(fdx, fdy) > 14) aimAng = Math.atan2(fdy, fdx);   // 拖动开火键=边拖边打
    }
  }
}, { passive: false });

function touchEnd(e) {
  e.preventDefault();
  for (var i = 0; i < e.changedTouches.length; i++) {
    var t = e.changedTouches[i];
    if (showBoard && boardDrag && t.identifier === boardDrag.id) {
      if (boardDrag.moved) {
        // 拖动过：保留面板（滚完还想看），松手停止滚动
      } else {
        showBoard = false;                 // 轻点关闭
      }
      boardDrag = null;
      continue;
    }
    if (touchMove.active && t.identifier === touchMove.id) {
      touchMove.active = false; touchMove.dx = touchMove.dy = 0;
    } else if (aimDrag.active && t.identifier === aimDrag.id) {
      aimDrag.active = false;
    } else if (fireDrag.active && t.identifier === fireDrag.id) {
      fireDrag.active = false; touchFire = false;
    }
  }
}
cv.addEventListener("touchend", touchEnd, { passive: false });
cv.addEventListener("touchcancel", touchEnd, { passive: false });

// ================= 按钮行为 =================
function handleMenu(id) {
  if (id === "board") {
    showBoard = !showBoard;
    boardDrag = null;
    if (showBoard) scrollY.board = 0;      // 每次打开从第一行开始
  }
  else if (id === "admin") showAdmin = !showAdmin;
  else if (id === "fov") cycleFov();
  else if (id === "chat") {
    var ci = document.getElementById("chatInput");
    if (ci) { ci.classList.add("show"); ci.focus(); }
  }
}
function handleRing(id) {
  if (!joined || !ws || ws.readyState !== 1) return;
  if (id === "reload") { ws.send(JSON.stringify({ t: "rl" })); sfx("reload"); }
  else if (id === "nuke") { ws.send(JSON.stringify({ t: "nuke" })); }
  else if (id === "smoke") {
    trySmokeAim();
    if (typeof smokeAim !== "undefined" && smokeAim) mHint("点击地图选择落点 · 再按“烟雾”取消", 2500);
  }
  else if (id === "scope") touchScope = !touchScope;
  else if (id === "skill") {
    var me = players[myId] || {};
    if (me.indn > 0 && me.indcd <= 0) { ws.send(JSON.stringify({ t: "indicator" })); sfx("reload"); }
    else mHint(me.indn <= 0 ? "指示剂已用完" : "指示剂冷却 " + Math.ceil(me.indcd || 0) + "s", 1400);
  }
}

// 轻量辅助瞄准：开火时，朝 14° 锥内最近敌人每帧最多转 2.2°
function aimAssist(me, base) {
  var best = null, bestDiff = 14 * Math.PI / 180;
  for (var pid in players) {
    var p = players[pid];
    if (!p.al || p.team === me.team || +pid === myId) continue;
    var d = Math.hypot(p.x - me.x, p.y - me.y);
    if (d > 640 || d < 1) continue;
    var a2 = Math.atan2(p.y - me.y, p.x - me.x);
    var diff = Math.abs(((a2 - base + Math.PI * 3) % (Math.PI * 2)) - Math.PI);
    if (diff < bestDiff) { bestDiff = diff; best = a2; }
  }
  if (best === null) return base;
  var step = 2.2 * Math.PI / 180;
  var d2 = ((best - base + Math.PI * 3) % (Math.PI * 2)) - Math.PI;
  return base + Math.max(-step, Math.min(step, d2));
}

// ================= 手机版 HUD =================
function mScreenPos(me, now) {
  var p = (typeof getDisplayPlayers === "function" ? getDisplayPlayers(now)[myId] : null) || me;
  var sc = (typeof cam !== "undefined" && cam) ? cam.scale : 1;
  return { x: (p.x - cam.x) * sc, y: (p.y - cam.y) * sc };
}

function drawMobileHud(me, now) {
  var lay = mlLayout();
  ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  ctx.textBaseline = "middle";
  ctx.textAlign = "left";

  // ---- 顶部：比分 / 地图 / 菜单 ----
  var s = lay.top.score;
  mPanel(s.x, s.y, s.w, s.h, 6, { fill: "rgba(8,12,17,0.72)" });
  mText("红", s.x + 12, s.y + s.h / 2, "bold 12px sans-serif", TEAM_COLOR.red, "left");
  mText(String(scores.red), s.x + 34, s.y + s.h / 2, "bold 15px sans-serif", TEAM_COLOR.red, "left");
  mText(":", s.x + s.w / 2, s.y + s.h / 2, "bold 13px sans-serif", "#8b97a3");
  mText(String(scores.blue), s.x + s.w - 34, s.y + s.h / 2, "bold 15px sans-serif", TEAM_COLOR.blue, "right");
  mText("蓝", s.x + s.w - 12, s.y + s.h / 2, "bold 12px sans-serif", TEAM_COLOR.blue, "right");

  var mp = lay.top.map;
  mPanel(mp.x, mp.y, mp.w, mp.h, 6, { fill: "rgba(8,12,17,0.6)" });
  var mapName = (typeof ACTIVE_MAP !== "undefined" && ACTIVE_MAP && ACTIVE_MAP.name) ? ACTIVE_MAP.name : "—";
  mText(mapName, mp.x + mp.w / 2, mp.y + mp.h * 0.34, "11px sans-serif", "#c7d2dc");
  // 换图倒计时（服务端下发 ms 字段；<30s 变色提醒；教程小局不轮换 → 不显示）
  if (mapSwitchIn > 0 && mapSwitchIn < 3600) {
    mText("换图 " + fmtCountdown(mapSwitchIn), mp.x + mp.w / 2, mp.y + mp.h * 0.74,
          "9px sans-serif", (mapSwitchIn < 30) ? "#ffd166" : "#8b97a3");
  }

  // ---- 随机事件（手机端紧凑版）----
  if (typeof eventInfo !== "undefined" && eventInfo && eventInfo.left) {
    var evTxt = "事件 " + eventInfo.name + " " + Math.ceil(eventInfo.left) + "s";
    mText(evTxt, VW / 2, mp.y + mp.h + 12, "bold 10px sans-serif", "#ffd166");
  }
  if (typeof killImpactT === "number" && killImpactT) {
    var kAge = (now - killImpactT) / 430;
    if (kAge < 1) {
      var kR = 18 + kAge * 30, kA = 1 - kAge;
      ctx.save();
      ctx.globalAlpha = kA;
      ctx.strokeStyle = "#ff315d"; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.arc(VW / 2, VH / 2, kR, 0, Math.PI * 2); ctx.stroke();
      ctx.strokeStyle = "rgba(255,255,255,.9)"; ctx.lineWidth = 1.2;
      for (var ki = 0; ki < 4; ki++) {
        var kAng = Math.PI / 4 + ki * Math.PI / 2;
        ctx.beginPath();
        ctx.moveTo(VW / 2 + Math.cos(kAng) * (kR + 4), VH / 2 + Math.sin(kAng) * (kR + 4));
        ctx.lineTo(VW / 2 + Math.cos(kAng) * (kR + 11), VH / 2 + Math.sin(kAng) * (kR + 11));
        ctx.stroke();
      }
      ctx.restore();
    }
  }
  if (typeof bossInfo !== "undefined" && bossInfo) {
    var bw = Math.min(420, VW * 0.55), bx = VW / 2 - bw / 2, by = mp.y + mp.h + 24;
    mPanel(bx, by, bw, 22, 5, { fill: "rgba(12,8,14,0.9)",
                                strokeColor: "rgba(255,91,208,0.55)" });
    var shFrac = Math.max(0, Math.min(1, (bossInfo.bsh || 0) / (bossInfo.bmx || 1)));
    ctx.fillStyle = "#ff5bd0";
    ctx.fillRect(bx + 2, by + 13, (bw - 4) * shFrac, 6);
    mText("BOSS " + Math.round(bossInfo.bsh || 0), bx + 6, by + 7, "bold 9px sans-serif", "#ff9fe4", "left");
  }

  for (var id in lay.top.menu) {
    var m = lay.top.menu[id];
    if (id === "admin" && !window._isHost) continue;
    var on = (id === "board" && showBoard) || (id === "admin" && showAdmin);
    mPanel(m.x, m.y, m.w, m.h, 6, { fill: on ? "rgba(255,209,102,0.24)" : "rgba(8,12,17,0.72)",
                                     strokeColor: on ? "#ffd166" : "rgba(255,255,255,0.16)" });
    mText(id === "board" ? "战绩" : id === "chat" ? "聊天" : id === "fov" ? fovTag() : "管理",
          m.x + m.w / 2, m.y + m.h / 2, "bold 11px sans-serif",
          id === "fov" ? "#9fe8ff" : (on ? "#ffd166" : "#e6edf3"));
  }

  // ---- 状态条：pH / 缓冲 / 弹药 ----
  var st = lay.status;
  var zone = phZone(me.ph);
  mPanel(st.x, st.y, st.w, st.h, 8, { fill: "rgba(8,12,17,0.74)" });
  if (me.ph !== null && me.ph !== undefined && (me.ph <= 3.0 || me.ph >= 11.0)) {
    ctx.strokeStyle = zone.color; ctx.lineWidth = 2;
    ctx.strokeRect(st.x + 1, st.y + 1, st.w - 2, st.h - 2);
  }
  mText("pH", st.x + 10, st.y + 12, "9px sans-serif", "#8b97a3", "left");
  mText(me.ph === null || me.ph === undefined ? "--" : Number(me.ph).toFixed(2),
        st.x + 10, st.y + 27, "bold 17px sans-serif", zone.color, "left");
  mText(zone.txt, st.x + 10, st.y + st.h - 9, "10px sans-serif", zone.color, "left");
  var bx = st.x + 76, bw = st.w - 88;
  var buf = (me.buf === null || me.buf === undefined) ? me.hp : me.buf;
  buf = Math.max(0, Math.min(100, buf === null || buf === undefined ? 0 : buf));
  mText("缓冲 " + Math.round(buf) + "%", bx, st.y + 11, "10px sans-serif", ringColor(buf), "left");
  if (typeof uiBar === "function") uiBar(bx, st.y + 16, bw, 7, buf / 100, ringColor(buf));
  // 弹药 / 换弹
  var amTxt;
  if (me.w === "knife") amTxt = "近战";
  else if (me.rl > 0) amTxt = "换弹 " + Number(me.rl).toFixed(1) + "s";
  else amTxt = (me.am || 0) + " / " + (me.res || 0);
  mText((WEAPONS[me.w] ? WEAPONS[me.w].name : me.w) + "  " + amTxt,
        bx, st.y + st.h - 9, "bold 11px sans-serif", me.rl > 0 ? "#ffd166" : "#e6edf3", "left");
  if (me.hvt) mText("★ HVT", st.x + st.w - 10, st.y + 11, "bold 10px sans-serif", "#ffd166", "right");

  // ---- 击杀播报（状态条右侧，最多 2 条） ----
  var feedRows = [];
  for (var fid in feed) feedRows.push(feed[fid]);
  feedRows.sort(function (a, b) { return b.t - a.t; });
  feedRows = feedRows.slice(0, 2);
  var fy = lay.feed.y + 10;
  for (var fi = 0; fi < feedRows.length; fi++) {
    var f = feedRows[fi];
    var age = (now - f.t) / 5000;
    if (age >= 1) continue;
    var line = (f.k || "?") + " ⟶ " + (f.v || "?");
    ctx.globalAlpha = Math.max(0.35, 1 - age);
    mPanel(lay.feed.x, fy - 9, lay.feed.w, 18, 4, { fill: "rgba(8,12,17,0.62)" });
    ctx.fillStyle = TEAM_COLOR[f.kt] || "#8b97a3";
    ctx.fillRect(lay.feed.x + 1, fy - 8, 3, 16);
    mText(line, lay.feed.x + 9, fy, "11px sans-serif", "#dfe7ee", "left");
    ctx.globalAlpha = 1;
    fy += 21;
  }

  // ---- 击杀确认 ----
  if (killConfirm && now - killConfirm.t < 1900) {
    var kc = killConfirm, ka = (now - kc.t) / 1900;
    ctx.globalAlpha = ka > 0.7 ? Math.max(0, 1 - (ka - 0.7) / 0.3) : 1;
    var kw = Math.min(300, lay.feed.w);
    var kx = lay.feed.x, ky = lay.feed.y + lay.feed.h + 8;
    mPanel(kx, ky, kw, 30, 6, { fill: "rgba(8,12,17,0.88)", strokeColor: "rgba(255,209,102,0.5)" });
    mText("击杀 " + kc.name, kx + 10, ky + 15, "bold 12px sans-serif", "#e6edf3", "left");
    mText("+" + (kc.pts || 0), kx + kw - 10, ky + 15, "bold 13px sans-serif", "#ffd166", "right");
    ctx.globalAlpha = 1;
  }

  // ---- 受击方向（围绕人物屏幕位置） ----
  if (dmgDirs && dmgDirs.length) {
    var sp = mScreenPos(me, now);
    for (var di = dmgDirs.length - 1; di >= 0; di--) {
      var d = dmgDirs[di], da = (now - d.t) / 900;
      if (da >= 1) { dmgDirs.splice(di, 1); continue; }
      ctx.strokeStyle = "rgba(255,77,94," + ((1 - da) * 0.9).toFixed(2) + ")";
      ctx.lineWidth = 4; ctx.lineCap = "round";
      ctx.beginPath(); ctx.arc(sp.x, sp.y, 40, d.ang - 0.24, d.ang + 0.24); ctx.stroke();
      ctx.lineCap = "butt";
    }
  }

  // ---- 连杀文字 ----
  if (streakText && now < streakT) {
    var t2 = (streakT - now) / 2000;
    ctx.globalAlpha = Math.min(1, t2 * 3);
    mText(streakText, VW / 2, lay.world.y + 26, "bold 26px sans-serif", "#ff5252");
    ctx.globalAlpha = 1;
  }

  // ---- HVT 提示 / 提示条 / 横幅 ----
  if (hvtTip) mText("★ 高价值目标：" + hvtTip, VW / 2, lay.status.y + lay.status.h + 12,
                    "bold 11px sans-serif", "#ffd166");
  if (typeof drawHintBanner === "function") drawHintBanner(now);

  // ---- 准星（画在人物屏幕位置，与手动瞄准方向一致） ----
  if (me.al) {
    var sp3 = mScreenPos(me, now);
    ctx.strokeStyle = "rgba(255,255,255,0.75)"; ctx.lineWidth = 1.6;
    ctx.beginPath();
    ctx.moveTo(sp3.x - 9, sp3.y); ctx.lineTo(sp3.x - 3, sp3.y);
    ctx.moveTo(sp3.x + 3, sp3.y); ctx.lineTo(sp3.x + 9, sp3.y);
    ctx.moveTo(sp3.x, sp3.y - 9); ctx.lineTo(sp3.x, sp3.y - 3);
    ctx.moveTo(sp3.x, sp3.y + 3); ctx.lineTo(sp3.x, sp3.y + 9);
    ctx.stroke();
  }
  drawMobileChat(now);
  return lay;
}

function drawMobileChat(now) {
  if (!chatMsgs || !chatMsgs.length) return;
  var lay = mlLayout();
  var list = chatMsgs.slice(-3);
  var y = lay.world.y + lay.world.h - 6;
  for (var i = list.length - 1; i >= 0; i--) {
    if (now - list[i].t > 10000) continue;
    mPanel(8, y - 16, Math.min(300, 60 + list[i].msg.length * 12), 17, 4, { fill: "rgba(8,12,17,0.7)" });
    mText(list[i].name + "：" + list[i].msg, 14, y - 7, "11px sans-serif", "#cfe8f5", "left");
    y -= 20;
  }
}

// ---- 触摸层：摇杆 / 瞄准线 / 武器条 / 功能键 / 开火键 ----
function drawTouchStick(lay) {
  if (!touchMove.active) {
    ctx.strokeStyle = "rgba(255,255,255,0.13)"; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.arc(lay.stick.cx, lay.stick.cy, lay.stick.r, 0, Math.PI * 2); ctx.stroke();
    mText("移动", lay.stick.cx, lay.stick.cy, "12px sans-serif", "rgba(255,255,255,0.28)");
    return;
  }
  ctx.strokeStyle = "rgba(255,255,255,0.32)"; ctx.lineWidth = 2;
  ctx.beginPath(); ctx.arc(touchMove.ox, touchMove.oy, lay.stick.r, 0, Math.PI * 2); ctx.stroke();
  ctx.fillStyle = "rgba(255,255,255,0.30)";
  ctx.beginPath();
  ctx.arc(touchMove.ox + touchMove.dx * lay.stick.r, touchMove.oy + touchMove.dy * lay.stick.r, 24, 0, Math.PI * 2);
  ctx.fill();
}

function drawAimLine(me, now) {
  if (aimAng === null || !me || !me.al) return;
  var p = (typeof getDisplayPlayers === "function" ? getDisplayPlayers(now)[myId] : null) || me;
  var sc = cam.scale;
  ctx.strokeStyle = "rgba(255,209,102,0.6)"; ctx.lineWidth = 2;
  ctx.setLineDash([10, 8]);
  ctx.beginPath();
  ctx.moveTo((p.x - cam.x) * sc, (p.y - cam.y) * sc);
  ctx.lineTo((p.x + Math.cos(aimAng) * 340 - cam.x) * sc, (p.y + Math.sin(aimAng) * 340 - cam.y) * sc);
  ctx.stroke();
  ctx.setLineDash([]);
}

function drawTouchUI() {
  requestAnimationFrame(drawTouchUI);
  if (!isTouch || !joined) return;
  var me = players[myId];
  if (!me) return;
  var now = performance.now();
  ctx.setTransform(DPR, 0, 0, DPR, 0, 0);   // 触摸层永远在 CSS 像素空间
  var lay = mlLayout();
  ctx.textAlign = "center"; ctx.textBaseline = "middle";

  drawTouchStick(lay);
  if (typeof smokeAim !== "undefined" && smokeAim) {
    mText("烟雾选点中 · 点地图投放 / 再按“烟雾”取消", VW / 2, lay.hint.y - 12,
          "bold 12px sans-serif", "#ffd166");
  }
  if (aimDrag.active) {
    ctx.strokeStyle = "rgba(255,209,102,0.35)"; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.arc(aimDrag.ox, aimDrag.oy, 26, 0, Math.PI * 2); ctx.stroke();
  }
  drawAimLine(me, now);

  // 武器条
  var ch = lay.chips;
  for (var i = 0; i < ch.order.length; i++) {
    var wn = ch.order[i];
    var x = ch.x0 + i * (ch.w + ch.gap);
    var active = me.w === wn;
    mPanel(x, ch.y0, ch.w, ch.h, 7, {
      fill: active ? "rgba(255,209,102,0.20)" : "rgba(0,0,0,0.46)",
      strokeColor: active ? "#ffd166" : "rgba(255,255,255,0.14)"
    });
    mText(WEAPONS[wn].name.slice(0, 3), x + ch.w / 2, ch.y0 + ch.h / 2 - 6,
          "bold 12px sans-serif", active ? "#ffd166" : "#9aa4ae");
    var am = WEAPONS[wn].mag;
    mText(active && me.w !== "knife" ? (me.am || 0) + "" : (am ? am + "" : "—"),
          x + ch.w / 2, ch.y0 + ch.h / 2 + 10, "10px sans-serif",
          active ? "rgba(255,209,102,0.85)" : "rgba(154,164,174,0.6)");
  }

  // 功能键
  for (i = 0; i < lay.ring.length; i++) {
    var b = lay.ring[i];
    var dim = 0;
    if (b.id === "reload") dim = me.rl > 0 ? Number(me.rl).toFixed(1) : 0;
    if (b.id === "smoke") dim = me.scd > 0 ? Math.ceil(me.scd) : 0;
    if (b.id === "skill") dim = me.indcd > 0 ? Math.ceil(me.indcd) : 0;
    var label = { reload: "换弹", smoke: "烟雾", skill: "指示剂", scope: touchScope ? "关镜" : "开镜", nuke: "核弹" }[b.id] || b.id;
    ctx.fillStyle = "rgba(0,0,0,0.44)";
    ctx.beginPath(); ctx.arc(b.cx, b.cy, b.r, 0, Math.PI * 2); ctx.fill();
    ctx.strokeStyle = dim ? "rgba(150,150,150,0.5)" : "rgba(255,255,255,0.5)";
    ctx.lineWidth = 1.5; ctx.stroke();
    mText(dim ? dim + "s" : label, b.cx, b.cy, "bold 11px sans-serif",
          dim ? "rgba(180,180,180,0.9)" : "#fff");
  }

  // 开火键
  if (me.al) {
    ctx.fillStyle = "rgba(255,107,82,0.16)";
    ctx.beginPath(); ctx.arc(lay.fire.cx, lay.fire.cy, lay.fire.r, 0, Math.PI * 2); ctx.fill();
    ctx.strokeStyle = touchFire ? "#ff8a70" : "rgba(255,107,82,0.75)";
    ctx.lineWidth = 3; ctx.stroke();
    if (me.w !== "knife" && WEAPONS[me.w].mag > 0) {
      var frac = me.rl > 0 ? 0 : (me.am || 0) / WEAPONS[me.w].mag;
      if (frac > 0) {
        ctx.strokeStyle = "#ffd166"; ctx.lineWidth = 4;
        ctx.beginPath();
        ctx.arc(lay.fire.cx, lay.fire.cy, lay.fire.r - 6, -Math.PI / 2, -Math.PI / 2 + frac * Math.PI * 2);
        ctx.stroke();
      }
    }
    mText(me.rl > 0 ? "换弹中" : "开火", lay.fire.cx, lay.fire.cy, "bold 14px sans-serif", "#fff");
  }
}
requestAnimationFrame(drawTouchUI);

// ================= 战绩板（手机版） =================
function drawMobileBoard() {
  ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  ctx.textAlign = "left"; ctx.textBaseline = "middle";
  ctx.fillStyle = "rgba(4,7,10,0.92)";
  ctx.fillRect(0, 0, VW, VH);
  var arr = [];
  for (var pid in players) arr.push(players[pid]);
  arr.sort(function (a, b) { return (b.s || 0) - (a.s || 0) || (b.k || 0) - (a.k || 0); });
  // 已退出服务器的玩家：战绩保留在榜单上，排在在线玩家之后并标注"不在线"
  var offRows = (typeof offline !== "undefined" && offline) ? offline.slice() : [];
  offRows.sort(function (a, b) { return (b.s || 0) - (a.s || 0); });
  for (var oi = 0; oi < offRows.length; oi++) {
    var oo = offRows[oi];
    arr.push({ _off: true, n: oo.n, team: oo.team, k: oo.k, d: oo.d, s: oo.s,
               ph: null, buf: null, hp: 0, st: {}, bot: false });
  }
  var x = 10, y = 8, w = VW - 20, h = VH - 16;
  mPanel(x, y, w, h, 10, { fill: "rgba(10,14,19,0.96)", strokeColor: "rgba(57,199,216,0.4)" });
  mText("实验记录 // 战绩榜（点击任意处关闭）", x + 14, y + 16, "bold 13px sans-serif", "#e6edf3", "left");
  var cols = [x + 12, x + 42, x + 40 + w * 0.2, x + 40 + w * 0.38, x + 40 + w * 0.46,
              x + 40 + w * 0.54, x + 40 + w * 0.64, x + 40 + w * 0.74, x + 40 + w * 0.84];
  var heads = ["#", "玩家", "积分", "K", "D", "pH", "缓冲", "伤害", "救援"];
  var hy = y + 36;
  for (var c = 0; c < heads.length; c++)
    mText(heads[c], cols[c], hy, "10px sans-serif", "#8b97a3", "left");
  var rowH = Math.min(22, (h - 52) / Math.max(1, Math.min(arr.length, 10)));
  for (var i = 0; i < Math.min(arr.length, 10); i++) {
    var p = arr[i], ry = hy + 16 + i * rowH;
    var isOff = !!p._off;
    if (i % 2 === 0) { ctx.fillStyle = "rgba(255,255,255,0.04)"; ctx.fillRect(x + 8, ry - rowH / 2, w - 16, rowH - 2); }
    var pz = phZone(p.ph);
    mText(isOff ? "—" : String(i + 1), cols[0], ry, "11px sans-serif",
          (i < 3 && !isOff) ? "#ffd166" : "#8b97a3", "left");
    mText(p.n + (p.bot ? " AI" : "") + (isOff ? " 不在线" : "") + (p.id === myId ? "（你）" : ""),
          cols[1], ry, "bold 12px sans-serif",
          isOff ? "#8b97a3" : (p.id === myId ? "#fff" : "#dfe7ee"), "left");
    mText(String(p.s || 0), cols[2], ry, "11px sans-serif", isOff ? "#6f7b87" : "#ffd166", "left");
    mText(String(p.k || 0), cols[3], ry, "11px sans-serif", isOff ? "#6f7b87" : "#dfe7ee", "left");
    mText(String(p.d || 0), cols[4], ry, "11px sans-serif", "#8b97a3", "left");
    if (isOff) {
      for (var cc = 5; cc < cols.length; cc++) mText("—", cols[cc], ry, "11px sans-serif", "#6f7b87", "left");
      continue;
    }
    mText(p.ph === null || p.ph === undefined ? "--" : Number(p.ph).toFixed(1), cols[5], ry, "11px sans-serif", pz.color, "left");
    mText(Math.round(p.buf === null || p.buf === undefined ? (p.hp || 0) : p.buf) + "%", cols[6], ry,
          "11px sans-serif", ringColor(p.buf === null || p.buf === undefined ? (p.hp || 0) : p.buf), "left");
    var pst = p.st || {};
    mText(String(pst.dmg || 0), cols[7], ry, "11px sans-serif", "#dfe7ee", "left");
    mText(String(pst.rescue || 0), cols[8], ry, "11px sans-serif", "#7dffb2", "left");
  }
}

// ================= 主机管理面板（手机版） =================
function drawMobileAdmin() {
  mlAdminRects = [];
  if (!window._isHost || !joined || !showAdmin) return;
  ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  ctx.textAlign = "left"; ctx.textBaseline = "middle";
  ctx.fillStyle = "rgba(4,7,10,0.72)"; ctx.fillRect(0, 0, VW, VH);
  var x = 10, y = 8, w = Math.min(320, VW - 20), h = VH - 16;
  mPanel(x, y, w, h, 10, { fill: "rgba(9,13,18,0.97)", strokeColor: "rgba(255,200,87,0.4)" });
  mText("主机控制 // HOST", x + 12, y + 16, "bold 12px sans-serif", "#ffd166", "left");
  mPanel(x + w - 46, y + 6, 38, 20, 5, { fill: "rgba(255,255,255,0.08)" });
  mText("关闭", x + w - 27, y + 16, "11px sans-serif", "#dfe7ee");
  mlAdminRects.push({ x: x + w - 46, y: y + 6, w: 38, h: 20, msg: { t: "admin", act: "close" } });

  var yy = y + 40;
  var teams = [["red", "红队 AI"], ["blue", "蓝队 AI"]];
  for (var i = 0; i < teams.length; i++) {
    var team = teams[i][0], cnt = 0;
    for (var pid in players) if (players[pid].bot && players[pid].team === team) cnt++;
    mText(teams[i][1], x + 12, yy + 10, "12px sans-serif", "#dfe7ee", "left");
    mText(String(cnt), x + 96, yy + 10, "bold 12px sans-serif", TEAM_COLOR[team], "left");
    for (var b = 0; b < 2; b++) {
      var bx = x + w - (b === 0 ? 72 : 34);
      mPanel(bx, yy, 28, 20, 5, { fill: "rgba(255,255,255,0.08)" });
      mText(b === 0 ? "−" : "＋", bx + 14, yy + 10, "bold 13px sans-serif", "#dfe7ee");
      mlAdminRects.push({ x: bx, y: yy, w: 28, h: 20,
        msg: { t: "admin", team: team, n: Math.max(0, Math.min(6, cnt + (b === 0 ? -1 : 1))) } });
    }
    yy += 24;
  }
  mText("化学参数", x + 12, yy + 10, "11px sans-serif", "#39c7d8", "left");
  yy += 22;
  var cfg = (typeof chemCfg !== "undefined" && chemCfg) ? chemCfg : { ct0: 0.1, pka: 7.2, acid: 0.5, destroy: 1.0, low: 3.0, high: 11.0 };
  for (i = 0; i < CHEM_ROWS.length; i++) {
    var r = CHEM_ROWS[i], v = Number(cfg[r.k]);
    mText(r.label, x + 12, yy + 10, "11px sans-serif", "#9aa4ae", "left");
    mText(r.fmt(v), x + w - 82, yy + 10, "11px sans-serif", "#e6edf3", "right");
    for (b = 0; b < 2; b++) {
      var sx2 = x + w - (b === 0 ? 72 : 34);
      mPanel(sx2, yy, 28, 18, 4, { fill: "rgba(255,255,255,0.08)" });
      mText(b === 0 ? "−" : "＋", sx2 + 14, yy + 9, "bold 12px sans-serif", "#dfe7ee");
      mlAdminRects.push({ x: sx2, y: yy, w: 28, h: 18,
        msg: { t: "admin", act: "chem", k: r.k, v: Number((v + (b === 0 ? -1 : 1) * r.step).toFixed(3)) } });
    }
    yy += 22;
  }
  var humans = [];
  for (pid in players) if (!players[pid].bot) humans.push(players[pid]);
  if (humans.length && yy < y + h - 60) {
    mText("调队 / 赋能 / 封禁", x + 12, yy + 10, "11px sans-serif", "#ffd166", "left");
    yy += 22;
    for (i = 0; i < Math.min(2, humans.length); i++) {
      var p = humans[i];
      var nm2 = String(p.n || "");
      if (nm2.length > 6) nm2 = nm2.slice(0, 5) + "…";
      mText(nm2, x + 12, yy + 10, "11px sans-serif", p.id === myId ? "#fff" : "#dfe7ee", "left");
      for (var tb = 0; tb < 2; tb++) {
        var t2 = tb === 0 ? "red" : "blue", tx2 = x + 112 + tb * 32;
        var cur = p.team === t2;
        mPanel(tx2, yy, 28, 20, 4, { fill: cur ? TEAM_COLOR[t2] : "rgba(255,255,255,0.08)" });
        mText(tb === 0 ? "红" : "蓝", tx2 + 14, yy + 10, "11px sans-serif", cur ? "#08110d" : "#9aa4ae");
        mlAdminRects.push({ x: tx2, y: yy, w: 28, h: 20,
          msg: { t: "admin", act: "team", id: p.id, team: t2 } });
      }
      var pow = p.pw || "none";
      var pidx = PW_CYCLE.indexOf(pow);
      var nextP = PW_CYCLE[(pidx + 1) % PW_CYCLE.length];
      mPanel(x + 176, yy, 62, 20, 4, { fill: "rgba(255,255,255,0.08)" });
      mText(pow === "none" ? "赋能 +" : PW_NAME[pow], x + 207, yy + 10,
            "10px sans-serif", pow === "none" ? "#9aa4ae" : "#7dffb2");
      mlAdminRects.push({ x: x + 176, y: yy, w: 62, h: 20,
        msg: { t: "admin", act: "power", id: p.id, pw: nextP } });
      // 封禁：ID / IP（与 PC 面板一致，写入服务器 save/bans.json）
      for (var bb = 0; bb < 2; bb++) {
        var bbx = x + 242 + bb * 36;
        mPanel(bbx, yy, 32, 20, 4, { fill: "rgba(255,77,94,0.18)" });
        mText(bb === 0 ? "封ID" : "封IP", bbx + 16, yy + 10, "9px sans-serif", "#ff6b7a");
        mlAdminRects.push({ x: bbx, y: yy, w: 32, h: 20,
          msg: { t: "admin", act: "ban", id: p.id, mode: (bb === 0 ? "id" : "ip") } });
      }
      yy += 24;
    }
  }
  // 封禁名单（空间够时显示前 2 条，点击解封）
  var mbl = (typeof banList !== "undefined" && banList) ? banList : null;
  if (mbl && (mbl.names.length || mbl.ips.length) && yy < y + h - 30) {
    mText("封禁名单（点解封）", x + 12, yy + 9, "10px sans-serif", "#ff6b7a", "left");
    yy += 20;
    var mbItems = [];
    for (i = 0; i < (mbl.names || []).length && i < 2; i++) mbItems.push({ k: "id", v: mbl.names[i] });
    for (i = 0; i < (mbl.ips || []).length && mbItems.length < 2; i++) mbItems.push({ k: "ip", v: mbl.ips[i] });
    for (i = 0; i < mbItems.length && yy < y + h - 22; i++) {
      var bi2 = mbItems[i];
      var bt2 = (bi2.k === "id" ? "ID " : "IP ") + String(bi2.v || "");
      if (bt2.length > 24) bt2 = bt2.slice(0, 23) + "…";
      mText(bt2, x + 12, yy + 9, "9px sans-serif", "#9aa4ae", "left");
      mPanel(x + w - 56, yy, 46, 18, 4, { fill: "rgba(125,255,178,0.12)" });
      mText("解封", x + w - 33, yy + 9, "9px sans-serif", "#7dffb2");
      mlAdminRects.push({ x: x + w - 56, y: yy, w: 46, h: 18,
        msg: { t: "admin", act: "unban", kind: bi2.k, value: bi2.v } });
      yy += 20;
    }
  }
}
function mobileAdminHit(x, y) {
  for (var i = 0; i < mlAdminRects.length; i++) {
    var r = mlAdminRects[i];
    if (x >= r.x && x <= r.x + r.w && y >= r.y && y <= r.y + r.h) {
      if (r.msg.act === "close") { showAdmin = false; return null; }
      return r.msg;
    }
  }
  return null;
}

// ================= 阵亡界面（手机版） =================
function drawMobileDeath(info, now) {
  if (!info) return;
  ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  ctx.textAlign = "center"; ctx.textBaseline = "middle";
  ctx.fillStyle = "rgba(6,4,6,0.66)";
  ctx.fillRect(0, 0, VW, VH);
  mText("阵亡", VW / 2, VH / 2 - 34, "bold 34px sans-serif", "#ff5252");
  mText("被 " + (info.killer || "?") + " 击杀", VW / 2, VH / 2 + 2, "14px sans-serif", "#e6edf3");
  var cause = { acidosis: "酸中毒", alkalosis: "碱中毒", buffer_destroyed: "缓冲剥离" }[info.cause] || "";
  mText((WEAPONS[info.w] ? WEAPONS[info.w].name : "") + (cause ? " · " + cause : ""),
        VW / 2, VH / 2 + 24, "12px sans-serif", "#9aa4ae");
  if (info.resp !== undefined && info.resp !== null)
    mText(Number(info.resp).toFixed(1) + "s 后复活", VW / 2, VH / 2 + 52, "bold 14px sans-serif", "#ffd166");
}

// ================= 输入上报（独立循环） =================
setInterval(function () {
  if (!joined || !ws || ws.readyState !== 1) return;
  var me0 = players[myId];
  if (!me0) return;
  var ang = (aimAng !== null) ? aimAng : me0.a;
  if (touchFire) ang = aimAssist(me0, ang);
  var wm = { x: me0.x + Math.cos(ang) * 420, y: me0.y + Math.sin(ang) * 420 };
  var k;
  if (touchMove.active) {
    var mdz = 0.28;
    k = [touchMove.dy < -mdz ? 1 : 0, touchMove.dx > mdz ? 1 : 0,
         touchMove.dy > mdz ? 1 : 0, touchMove.dx < -mdz ? 1 : 0];
  } else k = [0, 0, 0, 0];
  ws.send(JSON.stringify({
    t: "in", k: k, ax: wm.x, ay: wm.y,
    s: touchFire, z: touchScope ? 1 : 0
  }));
}, 40);

// ================= 进游戏全屏横屏 =================
document.getElementById("joinBtn").addEventListener("click", function () {
  try {
    var rootEl = document.documentElement;
    var reqFs = rootEl.requestFullscreen || rootEl.webkitRequestFullscreen;
    if (reqFs && !document.fullscreenElement) {
      var fp = reqFs.call(rootEl);
      if (fp && fp.catch) fp.catch(function () {});   // WebView 可能不支持，静默降级
    }
    if (screen.orientation && screen.orientation.lock)
      screen.orientation.lock("landscape").catch(function () {});
  } catch (err) {}
});
(function () {
  var helpEl = document.querySelector(".help");
  if (helpEl) helpEl.innerHTML =
    "<b>左半屏拖动</b> 移动 · <b>右半屏拖动</b> 瞄准（松手保留方向）<br>" +
    "<b>开火键</b> 按住连发 · 拖动开火键可边拖边打 · <b>底部武器条</b> 切枪<br>" +
    "右侧功能键：换弹 / 烟雾 / 指示剂 / 开镜";
})();
