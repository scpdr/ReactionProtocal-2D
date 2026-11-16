#!/usr/bin/env node
/* tests/mobile_layout_check.js
 * Reaction Protocol · 手机端布局多尺寸自动校验（纯 Node，无第三方依赖）
 *
 * 校验对象：public/mobile_layout.js 暴露的 window.ML / module.exports = ML
 *   ML.compute(VW, VH, opts?) -> layout
 *   ML.hit(layout, x, y)      -> {kind:'menu'|'ring'|'chip'|'fire'|'aim'|'move', id?}
 *   ML.overlaps(layout)       -> [{a,b,kind}]
 *   ML.report(VW, VH)         -> {vw,vh,overlaps,minGap}
 *
 * 断言：
 *   1) ML.overlaps 为空；
 *   2) fire / 每个 ring / 每个 chip / 每个 menu 完整落在安全区内（各边 ≥4px）；
 *   3) 上述元素“中心点”调用 ML.hit 必须返回自身（防“开火键吃掉聊天/管理按钮”回归）；
 *   4) 无元素区域内点击：x<VW/2 → move，x≥VW/2 → aim；
 *   5) 打印每个尺寸的 minGap 表；失败时打印重叠元素与实际间距。
 *
 * 用法：
 *   node tests/mobile_layout_check.js
 *   node tests/mobile_layout_check.js --ml public/mobile_layout.js --verbose
 *   ML_PATH=/abs/path/mobile_layout.js node tests/mobile_layout_check.js
 * 退出码：0 = 全部通过；1 = 存在失败（含 ML 加载失败）
 */
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.resolve(__dirname, '..');
const SAFE_PAD = 4;      // 交互元素距安全区各边至少留 4px
const HIT_PAD_TOL = 8;   // 允许 ≤8px 的不可见命中扩展；超过则判为“越界命中”（吃掉空白区）

const DEFAULT_SIZES = [
  [640, 300], [667, 375], [736, 414], [768, 362], [800, 360],
  [844, 390], [915, 412], [960, 440], [1024, 600],
];

// 设备真机常见横屏 CSS 视口（768×362）已在默认表内；扩展状态探针用于覆盖 scope/nuke。
const EXTRA_OPTS = {
  isHost: true, host: true,
  weapon: 'sniper', w: 'sniper', sniper: true,
  nuke: true, hasNuke: true, nukeReady: true,
};

// ---------------------------------------------------------------- CLI
function parseArgs(argv) {
  const out = { ml: null, verbose: false, sizes: null };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--ml') out.ml = argv[++i];
    else if (a.startsWith('--ml=')) out.ml = a.slice(5);
    else if (a === '--verbose' || a === '-v') out.verbose = true;
    else if (a === '--sizes') out.sizes = argv[++i];
    else if (a.startsWith('--sizes=')) out.sizes = a.slice(8);
    else if (a === '--help' || a === '-h') out.help = true;
  }
  return out;
}

function parseSizes(spec) {
  if (!spec) return DEFAULT_SIZES.slice();
  const list = [];
  spec.split(',').forEach((part) => {
    const m = /^\s*(\d+)\s*[xX×]\s*(\d+)\s*$/.exec(part);
    if (m) list.push([parseInt(m[1], 10), parseInt(m[2], 10)]);
  });
  return list.length ? list : DEFAULT_SIZES.slice();
}

// ---------------------------------------------------------------- 加载 ML
function loadML(cliPath) {
  const cands = [];
  if (process.env.ML_PATH) cands.push(process.env.ML_PATH);
  if (cliPath) cands.push(cliPath);
  cands.push(path.join(ROOT, 'public', 'mobile_layout.js'));
  cands.push(path.join(ROOT, 'dist', 'mobile_layout.js'));

  const tried = [];
  const errors = [];
  for (const cand of cands) {
    const p = path.resolve(ROOT, cand);
    if (tried.indexOf(p) >= 0) continue;
    tried.push(p);
    if (!fs.existsSync(p)) continue;

    const noop = () => {};
    const win = {};
    const sandbox = {
      console,
      Math, JSON, Date, Object, Array, String, Number, Boolean, RegExp, Error,
      isFinite, isNaN, parseInt, parseFloat,
      performance: { now: () => Date.now() },
      window: win,
      self: win,
      module: { exports: {} },
      exports: {},
      document: {
        getElementById: () => null,
        querySelector: () => null,
        addEventListener: noop,
        documentElement: { style: {}, clientWidth: 0, clientHeight: 0 },
        body: { style: {}, clientWidth: 0, clientHeight: 0 },
      },
      navigator: { userAgent: 'node' },
      addEventListener: noop,
      removeEventListener: noop,
      requestAnimationFrame: noop,
      setTimeout, clearTimeout,
    };
    sandbox.globalThis = sandbox;
    let runErr = null;
    try {
      vm.createContext(sandbox);
      vm.runInContext(fs.readFileSync(p, 'utf8'), sandbox, { filename: p });
    } catch (e) {
      runErr = e;
    }

    const pick = (v) => (v && typeof v.compute === 'function' ? v : null);
    let ML = pick(sandbox.module && sandbox.module.exports);
    if (!ML) ML = pick(sandbox.exports);
    if (!ML) ML = pick(sandbox.module && sandbox.module.exports && sandbox.module.exports.ML);
    if (!ML) ML = pick(sandbox.exports && sandbox.exports.ML);
    if (!ML) ML = pick(win.ML);
    if (!ML) ML = pick(sandbox.ML);

    if (ML) return { ML, file: p, tried, errors, runErr };
    errors.push(`${p}: ${runErr ? '执行报错: ' + runErr.message : '未导出 ML（需要 module.exports = ML 或 window.ML）'}`);
  }
  return { ML: null, file: null, tried, errors };
}

// ---------------------------------------------------------------- 几何工具
const EPS = 1e-6;
const isNum = (v) => typeof v === 'number' && isFinite(v);

function ptShape(x, y) { return { kind: 'point', x, y }; }
function boundsOf(e) {
  if (e.kind === 'circle') return { l: e.cx - e.r, t: e.cy - e.r, r: e.cx + e.r, b: e.cy + e.r };
  return { l: e.x, t: e.y, r: e.x + e.w, b: e.y + e.h };
}
function centroidOf(e) {
  if (e.kind === 'circle') return { x: e.cx, y: e.cy };
  return { x: e.x + e.w / 2, y: e.y + e.h / 2 };
}
function rectLike(e) {
  if (e.kind === 'circle') return boundsOf(e);
  return { l: e.x, t: e.y, r: e.x + e.w, b: e.y + e.h };
}

/* 两形状间距：>0 为最小间隙，<0 为重叠深度（近似），0 为相切 */
function gapOf(a, b) {
  if (a.kind === 'point' && b.kind === 'point') return Math.hypot(a.x - b.x, a.y - b.y);
  if (a.kind === 'point') return gapOf(b, a);
  if (b.kind === 'point') {
    const r = rectLike(a);
    const dx = Math.max(r.l - b.x, b.x - r.r, 0);
    const dy = Math.max(r.t - b.y, b.y - r.b, 0);
    const d = Math.hypot(dx, dy);
    if (a.kind === 'circle') return d - a.r;
    if (d > 0) return d;
    return -Math.min(b.x - r.l, r.r - b.x, b.y - r.t, r.b - b.y);
  }
  if (a.kind === 'circle' && b.kind === 'circle')
    return Math.hypot(a.cx - b.cx, a.cy - b.cy) - a.r - b.r;
  if (a.kind === 'circle' || b.kind === 'circle') {
    const c = a.kind === 'circle' ? a : b;
    const r = rectLike(a.kind === 'circle' ? b : a);
    const dx = Math.max(r.l - c.cx, c.cx - r.r, 0);
    const dy = Math.max(r.t - c.cy, c.cy - r.b, 0);
    const d = Math.hypot(dx, dy);
    if (d > 0) return d - c.r;
    // 圆心在矩形内：到最近边距离 + 半径 的反向
    const inside = Math.min(c.cx - r.l, r.r - c.cx, c.cy - r.t, r.b - c.cy);
    return inside >= 0 ? -(c.r + inside) : d - c.r;
  }
  const A = rectLike(a), B = rectLike(b);
  const dx = Math.max(A.l - B.r, B.l - A.r, 0);
  const dy = Math.max(A.t - B.b, B.t - A.b, 0);
  if (dx > 0 || dy > 0) return Math.hypot(dx, dy);
  const penX = Math.min(A.r - B.l, B.r - A.l);
  const penY = Math.min(A.b - B.t, B.b - A.t);
  return -Math.min(penX, penY);
}

/* 点落在哪些元素内部（几何），按重叠深度排序 */
function containing(els, x, y) {
  const pt = ptShape(x, y);
  return els
    .map((e) => ({ e, gap: gapOf(pt, e) }))
    .filter((o) => o.gap <= 0)
    .sort((A, B) => A.gap - B.gap);
}

function fmt(g) { return (Math.abs(g) < 0.05 ? 0 : g).toFixed(1); }

// ---------------------------------------------------------------- 契约解析
function extract(layout, vw, vh) {
  const fails = [];
  const warns = [];
  const els = [];
  let stick = null;
  if (!layout || typeof layout !== 'object') {
    return { els, stick, fails: ['ML.compute 未返回对象: ' + JSON.stringify(layout)], warns, safe: null };
  }
  const safe = layout.safe && typeof layout.safe === 'object' ? layout.safe : null;
  if (!safe) fails.push('layout.safe 缺失');
  else ['l', 'r', 't', 'b'].forEach((k) => {
    if (!isNum(safe[k])) fails.push(`layout.safe.${k} 缺失或非数字`);
  });

  // fire
  if (layout.fire && isNum(layout.fire.cx) && isNum(layout.fire.r)) els.push(mkCircle('fire', layout.fire));
  else fails.push('layout.fire 缺失或非法');

  // rings
  if (!Array.isArray(layout.ring)) fails.push('layout.ring 不是数组');
  else {
    const known = ['reload', 'smoke', 'skill', 'scope', 'nuke'];
    layout.ring.forEach((r, i) => {
      if (!r || !isNum(r.cx) || !isNum(r.cy) || !isNum(r.r)) { fails.push(`layout.ring[${i}] 非法`); return; }
      if (known.indexOf(r.id) < 0) fails.push(`layout.ring[${i}] 未知 id=${r.id}（契约仅允许 ${known.join('/')}）`);
      els.push(mkCircle('ring:' + r.id, r));
    });
    ['reload', 'smoke', 'skill'].forEach((id) => {
      if (!layout.ring.some((r) => r && r.id === id)) fails.push(`layout.ring 缺少必需按钮 ${id}`);
    });
  }

  // chips
  const c = layout.chips;
  if (!c || !Array.isArray(c.order)) fails.push('layout.chips 缺失或 order 非数组');
  else {
    const okGeo = isNum(c.x0) && isNum(c.y0) && isNum(c.w) && isNum(c.h) && isNum(c.gap);
    if (!okGeo) fails.push('layout.chips 几何字段（x0/y0/w/h/gap）缺失');
    if (c.order.length !== 9) fails.push(`layout.chips.order 长度 ${c.order.length} ≠ 9`);
    if (new Set(c.order).size !== c.order.length) fails.push('layout.chips.order 存在重复 id');
    if (okGeo) c.order.forEach((id, i) => {
      els.push({ name: 'chip:' + id, kind: 'rect', id,
        x: c.x0 + i * (c.w + c.gap), y: c.y0, w: c.w, h: c.h });
    });
  }

  // menu（v1.2 起第 4 个键 fov = 手机端"视野"档位切换，所有人可用）
  const menu = layout.top && layout.top.menu;
  if (!menu || typeof menu !== 'object') fails.push('layout.top.menu 缺失');
  else {
    ['board', 'chat', 'admin', 'fov'].forEach((k) => {
      const r = menu[k];
      if (!r || !isNum(r.x) || !isNum(r.y) || !isNum(r.w) || !isNum(r.h)) {
        fails.push(`layout.top.menu.${k} 缺失或非法`); return;
      }
      els.push({ name: 'menu:' + k, kind: 'rect', id: k, x: r.x, y: r.y, w: r.w, h: r.h });
    });
    Object.keys(menu).forEach((k) => {
      if (['board', 'chat', 'admin', 'fov'].indexOf(k) < 0) warns.push(`layout.top.menu 含未登记项 ${k}（请确认契约）`);
    });
  }

  if (layout.stick && isNum(layout.stick.cx) && isNum(layout.stick.r)) stick = mkCircle('stick', layout.stick);
  return { els, stick, fails, warns, safe };
}
function mkCircle(name, c) { return { name, kind: 'circle', id: c.id, cx: c.cx, cy: c.cy, r: c.r }; }

function kindOf(name, id) {
  if (name === 'fire') return 'fire';
  if (name.indexOf('ring:') === 0) return 'ring';
  if (name.indexOf('chip:') === 0) return 'chip';
  if (name.indexOf('menu:') === 0) return 'menu';
  return null;
}
function hitKey(kind, id) {
  if (!id) return kind;
  if (kind === 'ring') return 'ring:' + id;
  if (kind === 'chip') return 'chip:' + id;
  if (kind === 'menu') return 'menu:' + id;
  return kind;
}

// ---------------------------------------------------------------- 单项断言
function checkSafeArea(e, safe, vw, vh, fails) {
  const L = safe.l + SAFE_PAD, T = safe.t + SAFE_PAD;
  const R = vw - safe.r - SAFE_PAD, B = vh - safe.b - SAFE_PAD;
  const b = boundsOf(e);
  const bad = [];
  if (b.l < L - EPS) bad.push(`左 ${b.l.toFixed(1)} < ${L.toFixed(1)}（差 ${(L - b.l).toFixed(1)}px）`);
  if (b.t < T - EPS) bad.push(`上 ${b.t.toFixed(1)} < ${T.toFixed(1)}（差 ${(T - b.t).toFixed(1)}px）`);
  if (b.r > R + EPS) bad.push(`右 ${b.r.toFixed(1)} > ${R.toFixed(1)}（超 ${(b.r - R).toFixed(1)}px）`);
  if (b.b > B + EPS) bad.push(`下 ${b.b.toFixed(1)} > ${B.toFixed(1)}（超 ${(b.b - B).toFixed(1)}px）`);
  if (bad.length) fails.push(`安全区越界 ${e.name}: ` + bad.join('; '));
}

function checkCenterHit(ML, layout, e, els, fails) {
  const pt = centroidOf(e);
  const wantKind = kindOf(e.name, e.id);
  let h;
  try { h = ML.hit(layout, pt.x, pt.y); } catch (err) {
    fails.push(`中心命中 ${e.name}: ML.hit 抛异常 ${err.message}`); return;
  }
  const gotKind = h && h.kind;
  const gotName = hitKey(gotKind, h && h.id);
  if (gotKind === wantKind && (!e.id || h.id === e.id)) return;
  const others = containing(els, pt.x, pt.y).filter((o) => o.e.name !== e.name);
  const detail = others.length
    ? '；该点实际被 ' + others.map((o) => `${o.e.name}(重叠 ${fmt(-o.gap)}px)`).join('、') + ' 覆盖'
    : '；该点几何上不在任何元素内（自身缺失中心）';
  fails.push(`中心命中 ${e.name}: 期望 {kind:${wantKind}${e.id ? ',id:' + e.id : ''}}，` +
    `实测 ${JSON.stringify(h)} @ (${pt.x.toFixed(1)},${pt.y.toFixed(1)})${detail}`);
}

function probeBlankAreas(ML, layout, els, stick, safe, vw, vh, fails, warns) {
  const L = safe.l + SAFE_PAD, T = safe.t + SAFE_PAD;
  const R = vw - safe.r - SAFE_PAD, B = vh - safe.b - SAFE_PAD;
  const step = Math.max(4, Math.round(Math.min(vw, vh) / 48));
  let nL = 0, nR = 0;
  const exL = [], exR = [];
  for (let y = T + step / 2; y <= B + EPS; y += step) {
    for (let x = L + step / 2; x <= R + EPS; x += step) {
      const px = Math.round(x * 10) / 10, py = Math.round(y * 10) / 10;
      const pt = ptShape(px, py);
      if (els.some((e) => gapOf(pt, e) <= 0)) continue;
      if (stick && gapOf(pt, stick) <= 0) continue;
      let h;
      try { h = ML.hit(layout, px, py); } catch (err) { fails.push(`空白区 (${px},${py}): ML.hit 抛异常 ${err.message}`); continue; }
      const want = px < vw / 2 ? 'move' : 'aim';
      const kind = h && h.kind;
      if (kind !== want) {
        const target = (kind === 'fire' || kind === 'ring' || kind === 'chip' || kind === 'menu')
          ? els.find((e) => e.name === hitKey(kind, h && h.id)) : null;
        if (target) {
          const g = gapOf(pt, target);
          if (g > HIT_PAD_TOL) {
            fails.push(`越界命中 (${px},${py}): 距最近元素 ${target.name} 尚有 ${fmt(g)}px，` +
              `却命中 ${JSON.stringify(h)}（允许扩展 ≤${HIT_PAD_TOL}px）`);
          } else {
            warns.push(`(${px},${py}) 在 ${target.name} 外侧 ${fmt(g)}px 内命中 ${JSON.stringify(h)}（≤${HIT_PAD_TOL}px 容差，仅提示）`);
          }
        } else {
          fails.push(`空白区 (${px},${py}): x${px < vw / 2 ? '<' : '≥'}VW/2 期望 ${want}，实测 ${JSON.stringify(h)}`);
        }
      }
      if (px < vw / 2) { nL++; if (exL.length < 3) exL.push(`(${px},${py})→${kind}`); }
      else { nR++; if (exR.length < 3) exR.push(`(${px},${py})→${kind}`); }
    }
  }
  if (nL === 0) fails.push('空白区: 左半屏（x<VW/2）找不到任何无元素采样点，布局过于拥挤');
  if (nR === 0) fails.push('空白区: 右半屏（x≥VW/2）找不到任何无元素采样点，布局过于拥挤');
  if (nL > 0 && nL < 3) warns.push(`空白区: 左半屏仅 ${nL} 个采样点（布局拥挤）`);
  if (nR > 0 && nR < 3) warns.push(`空白区: 右半屏仅 ${nR} 个采样点（布局拥挤）`);
  return { nL, nR, exL, exR, step };
}

function checkOverlaps(ML, layout, els, fails, warns) {
  let rep;
  try { rep = ML.overlaps(layout); } catch (err) {
    fails.push(`ML.overlaps 抛异常 ${err.message}`); return { list: null, tight: [], geoOverlap: [] };
  }
  if (!Array.isArray(rep)) {
    fails.push('ML.overlaps 未返回数组: ' + JSON.stringify(rep)); return { list: null, tight: [], geoOverlap: [] };
  }
  if (rep.length) {
    fails.push('ML.overlaps 非空 → ' + rep.map((o) => {
      const g = findGap(els, o && o.a, o && o.b);
      return `${o && o.a} ↔ ${o && o.b} (kind=${o && o.kind}${g === null ? '' : '，实测间距 ' + fmt(g) + 'px'})`;
    }).join(' | '));
  }
  // 独立几何交叉校验（仅提示，避免与 Lead 的 kind 语义冲突）
  const geo = [];
  for (let i = 0; i < els.length; i++) {
    for (let j = i + 1; j < els.length; j++) {
      const g = gapOf(els[i], els[j]);
      if (g < -0.5) geo.push({ a: els[i].name, b: els[j].name, gap: g });
    }
  }
  const key = (a, b) => [a, b].sort().join('|');
  const reported = new Set((rep || []).map((o) => key(o && o.a, o && o.b)));
  geo.forEach((g) => {
    if (!reported.has(key(g.a, g.b)))
      warns.push(`几何检测到重叠但 ML.overlaps 未报: ${g.a} ↔ ${g.b}（重叠 ${fmt(-g.gap)}px）`);
  });
  (rep || []).forEach((o) => {
    if (findGap(els, o && o.a, o && o.b) !== null &&
        !geo.some((g) => key(g.a, g.b) === key(o && o.a, o && o.b)))
      warns.push(`ML.overlaps 报了 ${o && o.a} ↔ ${o && o.b}，但独立几何未测得重叠（疑似误报）`);
  });
  // 最紧的三对（含正间距），用于 minGap 表
  const pairs = [];
  for (let i = 0; i < els.length; i++)
    for (let j = i + 1; j < els.length; j++)
      pairs.push({ a: els[i].name, b: els[j].name, gap: gapOf(els[i], els[j]) });
  pairs.sort((A, B) => A.gap - B.gap);
  return { list: rep, tight: pairs.slice(0, 3), allPairs: pairs, geoOverlap: geo };
}

function findGap(els, a, b) {
  const ea = els.find((e) => e.name === a), eb = els.find((e) => e.name === b);
  if (!ea || !eb) return null;
  return gapOf(ea, eb);
}

let REPORT_FN_FAILED = false;
function checkReport(ML, vw, vh, fails) {
  if (typeof ML.report !== 'function') {
    if (!REPORT_FN_FAILED) { REPORT_FN_FAILED = true; fails.push('ML.report 不是函数（契约要求 ML.report(VW,VH)）'); }
    return null;
  }
  let rep;
  try { rep = ML.report(vw, vh); } catch (err) {
    if (!REPORT_FN_FAILED) { REPORT_FN_FAILED = true; fails.push(`ML.report 抛异常 ${err.message}`); }
    return null;
  }
  if (!rep || typeof rep !== 'object') { fails.push('ML.report 未返回对象'); return null; }
  if (rep.vw !== vw || rep.vh !== vh) fails.push(`ML.report(${vw}×${vh}) 的 vw/vh 不一致: 实测 ${rep.vw}×${rep.vh}`);
  if (!Array.isArray(rep.overlaps)) fails.push('ML.report().overlaps 不是数组');
  else if (rep.overlaps.length) fails.push(`ML.report(${vw}×${vh}).overlaps 非空 (${rep.overlaps.length} 条)`);
  if (!rep.minGap || typeof rep.minGap !== 'object') fails.push('ML.report().minGap 缺失或非对象');
  return rep;
}

function flattenMinGap(obj, prefix, out) {
  out = out || [];
  prefix = prefix || '';
  if (obj === null || obj === undefined) return out;
  if (typeof obj === 'number') { out.push([prefix || '(min)', obj]); return out; }
  if (typeof obj === 'object') {
    Object.keys(obj).forEach((k) => flattenMinGap(obj[k], prefix ? prefix + '.' + k : k, out));
    return out;
  }
  return out;
}

// ---------------------------------------------------------------- 主流程
function evaluateCase(ML, vw, vh, tag, opts, optsLabel) {
  const fails = [], warns = [];
  let layout;
  try { layout = ML.compute(vw, vh, opts); } catch (err) {
    return { vw, vh, tag, fails: [`ML.compute 抛异常: ${err.message}`], warns, stats: null };
  }
  const ex = extract(layout, vw, vh);
  fails.push.apply(fails, ex.fails);
  warns.push.apply(warns, ex.warns);

  let stats = { els: ex.els.length, overlaps: null, minGap: null, hits: 0, hitTotal: 0, blankL: 0, blankR: 0, tight: [], minGapFlat: [] };

  if (ex.safe) {
    ex.els.forEach((e) => checkSafeArea(e, ex.safe, vw, vh, fails));
    const ov = checkOverlaps(ML, layout, ex.els, fails, warns);
    stats.overlaps = ov.list ? ov.list.length : null;
    stats.tight = ov.tight;
    stats.allPairs = ov.allPairs;
    // 中心点必须命中自身
    ex.els.forEach((e) => {
      stats.hitTotal++;
      const before = fails.length;
      checkCenterHit(ML, layout, e, ex.els, fails);
      if (fails.length === before) stats.hits++;
    });
    const blank = probeBlankAreas(ML, layout, ex.els, ex.stick, ex.safe, vw, vh, fails, warns);
    stats.blankL = blank.nL; stats.blankR = blank.nR;
    stats.blankEx = blank.exL.concat(blank.exR);
    stats.blankStep = blank.step;
  }

  const rep = checkReport(ML, vw, vh, fails);
  if (rep) {
    stats.minGap = rep.minGap;
    const flat = flattenMinGap(rep.minGap);
    flat.sort((A, B) => A[1] - B[1]);
    stats.minGapFlat = flat;
  }
  if (optsLabel) stats.optsLabel = optsLabel;
  stats.layout = layout;
  return { vw, vh, tag, fails, warns, stats };
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    console.log('用法: node tests/mobile_layout_check.js [--ml <path>] [--sizes 640x300,768x362] [--verbose]');
    return 0;
  }
  const sizes = parseSizes(args.sizes);
  const loaded = loadML(args.ml);

  console.log('== Reaction Protocol · 手机端布局多尺寸校验 ==');
  console.log('仓库根目录: ' + ROOT);
  if (!loaded.ML) {
    console.log('[FAIL] 无法加载 ML（public/mobile_layout.js 尚未创建或未导出 ML）');
    loaded.tried.forEach((p) => console.log('  尝试: ' + p + (fs.existsSync(p) ? '（存在但未导出 ML）' : '（不存在）')));
    loaded.errors.forEach((e) => console.log('  ' + e));
    console.log('  提示：Lead 完成后重跑；或用 ML_PATH=<路径> 指定模块。');
    return 1;
  }
  console.log('ML 来源: ' + loaded.file);
  if (loaded.runErr) console.log('[WARN] 模块执行时报错（仍取到 ML）: ' + loaded.runErr.message);
  console.log('');

  const cases = [];
  sizes.forEach(([vw, vh]) => {
    cases.push(evaluateCase(loaded.ML, vw, vh, `${vw}x${vh}`, undefined, null));
    // 扩展状态探针：尝试让 scope/nuke 一同出现（不支持则忽略）
    let alt = null;
    try { alt = loaded.ML.compute(vw, vh, EXTRA_OPTS); } catch (e) { alt = null; }
    if (alt && alt.ring) {
      const base = cases[cases.length - 1].stats && cases[cases.length - 1].stats.layout;
      const baseIds = ((base && base.ring) || []).map((r) => r.id).sort().join(',');
      const altIds = alt.ring.map((r) => r.id).sort().join(',');
      if (baseIds !== altIds) {
        const c = evaluateCase(loaded.ML, vw, vh, `${vw}x${vh}+sniper/nuke`, EXTRA_OPTS, 'scope/nuke');
        cases.push(c);
      }
    }
  });

  let failCases = 0, totalFails = 0;
  const minGapTable = [];

  cases.forEach((c) => {
    const ok = c.fails.length === 0;
    if (!ok) { failCases++; totalFails += c.fails.length; }
    console.log(`[${c.tag}] ${ok ? 'PASS' : 'FAIL'}${c.stats ? `  元素 ${c.stats.els} 个, 中心命中 ${c.stats.hits}/${c.stats.hitTotal}, 空白采样 L/R=${c.stats.blankL}/${c.stats.blankR} (step ${c.stats.blankStep}px)` : ''}`);
    if (c.stats) {
      const flat = c.stats.minGapFlat || [];
      const top = flat.slice(0, 6).map(([k, v]) => `${k}=${(Math.round(v * 100) / 100)}`).join('  ');
      console.log('  minGap(最小6项): ' + (top || '(空)'));
      const raw = JSON.stringify(c.stats.minGap);
      if (args.verbose || raw.length <= 260) console.log('  minGap(raw): ' + raw);
      else console.log(`  minGap(raw): ${raw.slice(0, 200)} …（共 ${flat.length} 个数值项，用 --verbose 查看完整 JSON）`);
      const t = (c.stats.tight || []).map((p) => `${p.a}↔${p.b} ${fmt(p.gap)}px`).join(' | ');
      console.log('  最紧三对: ' + (t || '(无)'));
      if (c.stats.blankEx && c.stats.blankEx.length)
        console.log('  空白区样例: ' + c.stats.blankEx.join('  '));
      minGapTable.push({
        tag: c.tag,
        min: flat.length ? flat[0][1] : null,
        minKey: flat.length ? flat[0][0] : null,
        tight: (c.stats.tight || [])[0] || null,
        overlaps: c.stats.overlaps,
        hits: `${c.stats.hits}/${c.stats.hitTotal}`,
        blank: `${c.stats.blankL}/${c.stats.blankR}`,
        ok,
      });
    }
    c.warns.forEach((w, i) => {
      if (i < 6) console.log('  [WARN] ' + w);
    });
    if (c.warns.length > 6) console.log(`  [WARN] ...（另有 ${c.warns.length - 6} 条同类提示已省略）`);
    if (!ok) {
      console.log('');
      c.fails.forEach((f) => console.log('  [FAIL] ' + f));
      // 失败时打印实际间距明细
      const pairs = (c.stats && c.stats.allPairs) || [];
      const tightest = pairs.slice(0, 8);
      if (tightest.length)
        console.log('  实际间距(最紧8对): ' + tightest.map((p) => `${p.a}↔${p.b} ${fmt(p.gap)}px`).join(' | '));
      const neg = pairs.filter((p) => p.gap < 0);
      if (neg.length)
        console.log('  重叠对: ' + neg.map((p) => `${p.a}↔${p.b} 重叠 ${fmt(-p.gap)}px`).join(' | '));
    }
    if (args.verbose) {
      console.log('  layout 摘要: ' + JSON.stringify({
        safe: (c.stats && c.stats.layout && c.stats.layout.safe) || null,
        fire: (c.stats && c.stats.layout && c.stats.layout.fire) || null,
        ring: (c.stats && c.stats.layout && c.stats.layout.ring) || null,
      }));
    }
    console.log('');
  });

  console.log('== minGap 汇总表 ==');
  console.log('尺寸'.padEnd(22) + '最小间距'.padEnd(12) + '最小间距键'.padEnd(30) +
    '最紧元素对'.padEnd(34) + 'overlaps'.padEnd(11) + '中心命中'.padEnd(11) + '空白L/R'.padEnd(12) + '结论');
  minGapTable.forEach((r) => {
    const tight = r.tight ? `${r.tight.a}↔${r.tight.b} ${fmt(r.tight.gap)}px` : '-';
    console.log(
      r.tag.padEnd(22) +
      String(r.min === null ? '-' : (Math.round(r.min * 100) / 100)).padEnd(12) +
      String(r.minKey || '-').padEnd(30) +
      tight.padEnd(34) +
      String(r.overlaps === null ? '-' : r.overlaps).padEnd(11) +
      String(r.hits).padEnd(11) +
      String(r.blank).padEnd(12) +
      (r.ok ? 'PASS' : 'FAIL'));
  });
  console.log('');
  console.log(`总计: ${cases.length} 个尺寸用例，${cases.length - failCases} 通过 / ${failCases} 失败，共 ${totalFails} 条断言失败。`);
  return failCases ? 1 : 0;
}

/* 用 exitCode 而非 process.exit()，避免 Windows 下管道输出被截断 */
process.exitCode = main();
