/* Reaction Protocol · 手机端布局模块（浏览器 window.ML / Node module.exports）
 *
 * 设计目标：把“手机横屏”当成一等平台对待，而不是把 PC 的 1366×768 HUD 硬塞进
 * 768×362。所有可点元素由本模块统一计算，既保证互不重叠，也让布局可以被
 * Node（多尺寸单测）与真机（CDP 自评）共同校验。
 *
 * 冻结 API：
 *   ML.compute(VW, VH, opts) -> layout
 *   ML.hit(layout, x, y)     -> { kind: 'menu'|'ring'|'chip'|'fire'|'move'|'aim', id }
 *   ML.overlaps(layout)      -> [{ a, b, kind }]   （只检查可点元素）
 *   ML.report(VW, VH, opts)  -> { vw, vh, overlaps, minGap, layout }
 *
 * 坐标一律为 CSS 像素（与 touch.clientX / clientY 一致）。
 */
(function (root, factory) {
  var ML = factory();
  if (typeof module === "object" && module.exports) module.exports = ML;
  if (root) root.ML = ML;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var ORDER = ["knife", "pistol", "rifle", "sniper", "shotgun", "lmg", "asniper", "limewater", "vitc"];
  var MARGIN = 8;        // 屏幕边到控件的间距
  var GAP = 8;           // 控件之间的最小间距（hit 判定会吃掉一部分，靠优先级消歧）

  function clamp(v, a, b) { return v < a ? a : (v > b ? b : v); }
  function round(v) { return Math.round(v); }

  /* ---------- 安全区探测（env(safe-area-inset-*)，浏览器端缓存） ---------- */
  var _safeCache = null;
  function probeSafe() {
    if (_safeCache) return _safeCache;
    var s = { l: 0, r: 0, t: 0, b: 0 };
    try {
      if (typeof document !== "undefined" && document.body) {
        var el = document.createElement("div");
        el.style.cssText = "position:fixed;left:0;top:0;visibility:hidden;pointer-events:none;" +
          "padding:env(safe-area-inset-top) env(safe-area-inset-right) " +
          "env(safe-area-inset-bottom) env(safe-area-inset-left);";
        document.body.appendChild(el);
        var cs = getComputedStyle(el);
        s.t = parseFloat(cs.paddingTop) || 0;
        s.r = parseFloat(cs.paddingRight) || 0;
        s.b = parseFloat(cs.paddingBottom) || 0;
        s.l = parseFloat(cs.paddingLeft) || 0;
        document.body.removeChild(el);
      }
    } catch (e) { /* 老 WebView 不认 env()，按 0 处理 */ }
    _safeCache = s;
    return s;
  }

  /* ---------- 主布局 ---------- */
  function compute(VW, VH, opts) {
    opts = opts || {};
    var safe = opts.safe || probeSafe();
    var vw = Math.max(320, VW | 0), vh = Math.max(240, VH | 0);
    var L = safe.l + MARGIN, R = vw - safe.r - MARGIN;
    var T = safe.t + MARGIN, B = vh - safe.b - MARGIN;

    // 顶部条：比分 / 地图 / 右上角菜单
    var hTop = clamp(round(vh * 0.09), 26, 34);
    var bh = Math.max(18, hTop - 8);
    var menuIds = ["board", "chat"];
    if (opts.host !== false) menuIds.push("admin");   // 手机端默认只有房主能管，这里由调用方传 host
    if (opts.fov !== false) menuIds.push("fov");      // 视野档位切换入口（本地设置，所有人可用）
    var bw = clamp(round(vw * 0.062), 42, 54);
    var mgap = 6;
    var menuW = menuIds.length * bw + (menuIds.length - 1) * mgap;
    var menu = {};
    for (var i = 0; i < menuIds.length; i++) {
      menu[menuIds[i]] = { x: R - menuW + i * (bw + mgap), y: T, w: bw, h: bh, label: menuIds[i] };
    }
    var score = { x: L, y: T, w: clamp(round(vw * 0.2), 128, 176), h: bh };
    var map = { x: round(vw / 2 - 70), y: T, w: 140, h: bh };
    var top = { h: hTop, score: score, map: map, menu: menu };

    // 状态条：pH + 缓冲 + 弹药（左上角，HUD 用）
    var status = {
      x: L, y: T + hTop + 4,
      w: clamp(round(vw * 0.32), 196, 250),
      h: clamp(round(vh * 0.13), 40, 48)
    };

    // 击杀播报（状态条右侧；纯展示，不参与重叠校验，但已避开可点控件）
    var feed = {
      x: status.x + status.w + 8, y: T + hTop + 6,
      w: clamp(round(vw * 0.3), 176, 236), h: 46
    };

    // 开火键（贴左边缘、垂直居中）：左手拇指自然落点，右侧整片留给瞄准与功能键
    var fireR = clamp(round(Math.min(vw, vh) * 0.12), 34, 46);
    var fire = { cx: L + fireR, cy: round(vh / 2), r: fireR };

    // 功能键：贴右边缘，自下而上 换弹→烟雾→技能→开镜
    var ringR = clamp(round(fireR * 0.62), 20, 27);
    var step = 2 * ringR + 6;
    var railX = R - ringR;
    var ring = [
      { id: "reload", cx: railX, cy: B - ringR, r: ringR },
      { id: "smoke", cx: railX, cy: B - ringR - step, r: ringR },
      { id: "skill", cx: railX, cy: B - ringR - 2 * step, r: ringR }
    ];
    if (opts.sniper) ring.push({ id: "scope", cx: railX, cy: B - ringR - 3 * step, r: ringR });

    // 移动摇杆（左下，静止提示圈）
    var stickR = clamp(round(Math.min(vw, vh) * 0.16), 44, 58);
    var stick = { cx: L + stickR, cy: B - stickR, r: stickR };

    // 底部武器条（9 把试剂）：按摇杆右侧到右边缘的剩余宽度自适应，居中但不压摇杆/核弹键
    var chipH = clamp(round(vh * 0.115), 34, 42);
    var chipGap = 6;
    var nukeR = clamp(round(Math.min(ringR, chipH * 0.5)), 16, ringR);
    var chipsLeft = L + 2 * stickR + (opts.nuke ? (2 * nukeR + 2 * GAP) : GAP);
    var chipsRight = R - 2 * ringR - GAP;   // 右侧竖直功能键（换弹/烟雾…）所占宽度
    var chipAvail = Math.max(120, chipsRight - chipsLeft);
    var chipW = clamp(Math.floor((chipAvail - (ORDER.length - 1) * chipGap) / ORDER.length), 26, 46);
    var chipTotal = ORDER.length * chipW + (ORDER.length - 1) * chipGap;
    var chipX = round(vw / 2 - chipTotal / 2);
    chipX = clamp(chipX, chipsLeft, Math.max(chipsLeft, chipsRight - chipTotal));
    var chips = {
      x0: chipX, y0: B - chipH,
      w: chipW, h: chipH, gap: chipGap, order: ORDER.slice(), total: chipTotal
    };
    // 核弹键：放在武器条左侧（竖直排会顶到顶部条；半径收进武器条高度内才不会越界）
    if (opts.nuke) {
      ring.push({ id: "nuke", cx: chips.x0 - GAP - nukeR, cy: chips.y0 + chips.h / 2, r: nukeR });
    }

    var layout = {
      vw: vw, vh: vh, safe: safe, margin: MARGIN,
      top: top, status: status, feed: feed, chips: chips,
      fire: fire, ring: ring, stick: stick,
      hint: { y: chips.y0 - round(vh * 0.06) - 4 },
      world: {
        x: 0, y: status.y + status.h + 6,
        w: vw, h: Math.max(40, chips.y0 - (status.y + status.h + 6) - 6)
      }
    };
    return layout;
  }

  /* ---------- 可点元素（重叠校验用） ---------- */
  function elements(lay) {
    var out = [];
    for (var id in lay.top.menu) {
      var m = lay.top.menu[id];
      out.push({ name: "menu:" + id, kind: "menu", id: id, x: m.x, y: m.y, w: m.w, h: m.h });
    }
    for (var i = 0; i < lay.ring.length; i++) {
      var g = lay.ring[i];
      out.push({ name: "ring:" + g.id, kind: "ring", id: g.id, cx: g.cx, cy: g.cy, r: g.r });
    }
    for (var c = 0; c < lay.chips.order.length; c++) {
      out.push({
        name: "chip:" + lay.chips.order[c], kind: "chip", id: lay.chips.order[c],
        x: lay.chips.x0 + c * (lay.chips.w + lay.chips.gap), y: lay.chips.y0,
        w: lay.chips.w, h: lay.chips.h
      });
    }
    out.push({ name: "fire", kind: "fire", id: "fire", cx: lay.fire.cx, cy: lay.fire.cy, r: lay.fire.r });
    out.push({ name: "stick", kind: "stick", id: "move", cx: lay.stick.cx, cy: lay.stick.cy, r: lay.stick.r });
    return out;
  }

  function rectRect(a, b) {
    return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
  }
  function circCirc(a, b) { return Math.hypot(a.cx - b.cx, a.cy - b.cy) < a.r + b.r; }
  function circRect(c, r) {
    var dx = c.cx - Math.max(r.x, Math.min(c.cx, r.x + r.w));
    var dy = c.cy - Math.max(r.y, Math.min(c.cy, r.y + r.h));
    return dx * dx + dy * dy < c.r * c.r;
  }
  function test(a, b) {
    if (a.cx !== undefined && b.cx !== undefined) return circCirc(a, b);
    if (a.cx !== undefined) return circRect(a, b);
    if (b.cx !== undefined) return circRect(b, a);
    return rectRect(a, b);
  }
  function gap(a, b) {
    if (a.cx !== undefined && b.cx !== undefined) return Math.hypot(a.cx - b.cx, a.cy - b.cy) - a.r - b.r;
    if (a.cx !== undefined || b.cx !== undefined) {
      var c = a.cx !== undefined ? a : b, r = a.cx !== undefined ? b : a;
      var dx = Math.max(r.x - c.cx, 0, c.cx - (r.x + r.w));
      var dy = Math.max(r.y - c.cy, 0, c.cy - (r.y + r.h));
      return Math.hypot(dx, dy) - c.r;
    }
    var gx = Math.max(b.x - (a.x + a.w), a.x - (b.x + b.w));
    var gy = Math.max(b.y - (a.y + a.h), a.y - (b.y + b.h));
    return Math.max(gx, gy);
  }

  function overlaps(lay) {
    var els = elements(lay), bad = [];
    for (var i = 0; i < els.length; i++)
      for (var j = i + 1; j < els.length; j++)
        if (test(els[i], els[j])) bad.push({ a: els[i].name, b: els[j].name, kind: "intersect" });
    return bad;
  }

  function minGap(lay) {
    var els = elements(lay), g = {};
    for (var i = 0; i < els.length; i++)
      for (var j = i + 1; j < els.length; j++) {
        var k = els[i].name + "|" + els[j].name;
        g[k] = round(gap(els[i], els[j]) * 10) / 10;
      }
    return g;
  }

  /* ---------- 命中判定（优先级：菜单 > 功能键 > 武器条 > 开火 > 分区） ---------- */
  function hit(lay, x, y) {
    var id;
    for (id in lay.top.menu) {
      var m = lay.top.menu[id];
      if (x >= m.x && x <= m.x + m.w && y >= m.y && y <= m.y + m.h)
        return { kind: "menu", id: id };
    }
    for (var i = 0; i < lay.ring.length; i++) {
      var g = lay.ring[i];
      if (Math.hypot(x - g.cx, y - g.cy) <= g.r + 4) return { kind: "ring", id: g.id };
    }
    for (var c = 0; c < lay.chips.order.length; c++) {
      var cx0 = lay.chips.x0 + c * (lay.chips.w + lay.chips.gap);
      if (x >= cx0 - 2 && x <= cx0 + lay.chips.w + 2 &&
          y >= lay.chips.y0 - 2 && y <= lay.chips.y0 + lay.chips.h + 2)
        return { kind: "chip", id: lay.chips.order[c] };
    }
    if (Math.hypot(x - lay.fire.cx, y - lay.fire.cy) <= lay.fire.r + 6)
      return { kind: "fire", id: "fire" };
    return x < lay.vw / 2 ? { kind: "move", id: "move" } : { kind: "aim", id: "aim" };
  }

  function inSafe(lay, el, pad) {
    pad = pad === undefined ? 4 : pad;
    var l = lay.safe.l + pad, r = lay.vw - lay.safe.r - pad;
    var t = lay.safe.t + pad, b = lay.vh - lay.safe.b - pad;
    if (el.cx !== undefined)
      return el.cx - el.r >= l && el.cx + el.r <= r && el.cy - el.r >= t && el.cy + el.r <= b;
    return el.x >= l && el.x + el.w <= r && el.y >= t && el.y + el.h <= b;
  }

  function report(VW, VH, opts) {
    var lay = compute(VW, VH, opts);
    var els = elements(lay), esc = [];
    for (var i = 0; i < els.length; i++)
      if (!inSafe(lay, els[i])) esc.push(els[i].name);
    return {
      vw: lay.vw, vh: lay.vh, safe: lay.safe,
      overlaps: overlaps(lay), outOfSafe: esc, minGap: minGap(lay), layout: lay
    };
  }

  return {
    ORDER: ORDER, MARGIN: MARGIN,
    compute: compute, hit: hit, overlaps: overlaps,
    elements: elements, minGap: minGap, inSafe: inSafe, report: report,
    probeSafe: probeSafe
  };
});
