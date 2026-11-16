/* Reaction Protocol · 新手教程 Node 校验
 * ============================================================
 * 用假的 DOM / Canvas 把 mobile_layout.js + ui.js + game.js + tutorial.js
 * 真正加载起来，然后：
 *   1) 校验 window.TUT 的冻结 API 与初始状态；
 *   2) 按“位移 → 开火 → 换弹+切枪 → 命中 → 氧化开火 → 指示剂 → 技能 → 点击完成”
 *      逐步喂玩家状态，断言 8 步依次推进、每步有 0.6s 停顿、最终只发一次 tut_done；
 *   3) 校验 skip()/finish() 防重发、面板点击只消费不误跳步；
 *   4) 校验 8 步文案包含 pH/缓冲/氟泄漏/中和/指示剂/烟雾/补给/地图，且数值与代码一致；
 *   5) 校验面板几何：PC 1366×768、2730×1536 不越界、宽度 ≤40% VW、
 *      不压 pH 表盘 / 小地图 / 试剂架；手机 768×362 / 640×300 / 844×390 等
 *      不越过屏幕、不与 mobile_layout 的任何可点控件重叠。
 *
 * 运行：node tests/tutorial_check.js   （退出码 0=全绿 / 1=有失败）
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

/* ---------------- 断言计数 ---------------- */
let pass = 0, fail = 0;
const failures = [];
function ok(cond, msg) {
  if (cond) { pass++; console.log("[PASS] " + msg); }
  else { fail++; failures.push(msg); console.log("[FAIL] " + msg); }
}
function okEq(actual, expect, msg) {
  ok(actual === expect, msg + "（期望 " + JSON.stringify(expect) + "，实测 " + JSON.stringify(actual) + "）");
}

/* ---------------- 假 DOM / Canvas（与 tests/hud_runtime.js 同款） ---------------- */
function makeCtx() {
  const t = {};
  return new Proxy(t, {
    get(o, k) {
      if (k in o) return o[k];
      if (k === "measureText") return (s) => ({ width: String(s).length * 7 });
      if (k === "createRadialGradient" || k === "createLinearGradient")
        return () => ({ addColorStop() {} });
      if (k === "canvas") return { width: 1280, height: 800 };
      return () => {};
    },
    set(o, k, v) { o[k] = v; return true; }
  });
}
function makeEl(id) {
  return {
    id, value: "", textContent: "", checked: false,
    style: {}, classList: { add() {}, remove() {}, contains() { return false; } },
    addEventListener() {}, focus() {}, click() {},
    getContext: () => makeCtx(), width: 1280, height: 800
  };
}

const els = {};
const sandbox = {
  console,
  performance: { now: () => Date.now() },
  innerWidth: 1366, innerHeight: 768,
  location: { host: "127.0.0.1:8765", protocol: "http:", href: "http://127.0.0.1:8765/", search: "" },
  addEventListener() {}, removeEventListener() {},
  requestAnimationFrame() { return 0; },
  setInterval() { return 0; }, clearInterval() {}, setTimeout() { return 0; }, clearTimeout() {},
  localStorage: { getItem() { return null; }, setItem() {} },
  document: {
    body: makeEl("body"),
    getElementById(id) { if (!els[id]) els[id] = makeEl(id); return els[id]; },
    getElementsByName() { return [{ checked: true, value: "auto" }]; },
    createElement() { return makeEl("canvas"); },
    addEventListener() {}
  },
  WebSocket: class { constructor() { this.readyState = 0; } send() {} close() {} },
  AudioContext: class { constructor() { this.sampleRate = 48000; } createBuffer() { return { getChannelData: () => new Float32Array(1024) }; } }
};
sandbox.window = sandbox;
sandbox.global = sandbox;
sandbox.self = sandbox;
els.botCount = makeEl("botCount"); els.botCount.value = "2";
els.botDiff = makeEl("botDiff"); els.botDiff.value = "normal";

const context = vm.createContext(sandbox);
const base = path.join(__dirname, "..", "public");
for (const f of ["mobile_layout.js", "ui.js", "game.js", "tutorial.js"]) {
  try {
    vm.runInContext(fs.readFileSync(path.join(base, f), "utf8"), context, { filename: f });
    console.log("[PASS] 加载 " + f);
    pass++;
  } catch (e) {
    console.error("[FAIL] 加载 " + f + "：" + (e && e.stack || e));
    process.exit(1);
  }
}

const TUT = sandbox.TUT;

/* ---------------- 场景初始化 ---------------- */
sandbox.VW = 1366; sandbox.VH = 768; sandbox.DPR = 1; sandbox.UIS = 1;
sandbox.UW = 1366; sandbox.UH = 768;
sandbox.myId = 1;
sandbox.joined = true;
sandbox.chatMsgs = [];
sandbox.feed = {};
sandbox.dmgTexts = [];
sandbox.showBoard = false;
sandbox.showHelp = false;
sandbox.deadInfo = null;
sandbox.__MOBILE__ = false;

function mkMe() {
  return {
    id: 1, n: "测试员", team: "red", bot: false, al: true,
    x: 100, y: 100, a: 0, hp: 100, buf: 100, ph: 7.00,
    w: "pistol", am: 12, res: 60, rl: 0,
    scd: 0, acd: 0, aoe: 0, indn: 2, indcd: 0, nk_ok: 0, resp: 0,
    s: 0, k: 0, d: 0, hv: false, sg: false, exp: 0, pw: "none", st: {}
  };
}
function mkEnemy() {
  return {
    id: 2, n: "对手", team: "blue", bot: true, al: true,
    x: 600, y: 400, a: 0, hp: 100, buf: 100, ph: 7.00,
    w: "rifle", am: 30, res: 300, rl: 0,
    scd: 0, acd: 0, aoe: 0, indn: 0, indcd: 0, nk_ok: 0, resp: 0,
    s: 0, k: 0, d: 0, hv: false, sg: false, exp: 0, pw: "none", st: {}
  };
}

const players = { 1: mkMe(), 2: mkEnemy() };
sandbox.players = players;
const me = players[1];

/* 重置自己为“刚进教程”的干净状态（多轮流程之间复用同一个对象引用） */
function resetMe() {
  const fresh = mkMe();
  for (const k in fresh) me[k] = fresh[k];
  players[2] = mkEnemy();
  sandbox.dmgTexts = [];
  sandbox.feed = {};
  sandbox.ws = { readyState: 1, send(m) { sent.push(m); } };
}

let sent = [];
sandbox.ws = { readyState: 1, send(m) { sent.push(m); } };

/* ---------------- 帧驱动工具 ---------------- */
function frame(dt) { TUT.update(dt === undefined ? 0.02 : dt); }
function run(seconds, dt) {
  const stepDt = dt || 0.05;
  const n = Math.max(1, Math.ceil(seconds / stepDt));
  for (let i = 0; i < n; i++) frame(stepDt);
}
function settle() { run(0.8, 0.05); }

function rectsOverlap(a, b) {
  return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
}
function hudScale(vw, vh) {
  return Math.max(1, Math.min(2.4, Math.min(vw / 1366, vh / 768)));
}

/* 把玩家从第 1 步一路做操作推到第 8 步（返回每阶段结束时的 TUT.step 快照） */
function driveToLastStep(log) {
  const steps = [];

  /* 1) 位移 300px */
  frame();
  for (let i = 0; i < 40; i++) { me.x += 10; frame(); }
  steps.push(TUT.step);                 // 反馈期内仍是 0
  settle();
  steps.push(TUT.step);                 // 1

  /* 2) 开火 3 发（弹药 12→9） */
  me.am = 12; frame();
  me.am = 11; frame();
  me.am = 10; frame();
  me.am = 9; frame();
  settle();
  steps.push(TUT.step);                 // 2

  /* 3) 换弹 + 切枪（两个子条件都要） */
  me.rl = 1.3; frame();
  me.rl = 0.9; frame();
  me.rl = 0; me.am = 12; frame();
  steps.push(TUT.step);                 // 只换弹：仍 2
  me.w = "rifle"; frame();
  settle();
  steps.push(TUT.step);                 // 3

  /* 4) 命中敌人：自己命中的伤害飘字是最直接证据 */
  sandbox.dmgTexts.push({
    x: 600, y: 380, txt: "26", sub: "pH 6.83",
    life: 0.9, max: 0.9, kill: false, ch: "acid"
  });
  frame();
  settle();
  steps.push(TUT.step);                 // 4

  /* 5) 氧化通道（5 号喷淋器）开火一次 */
  me.w = "shotgun"; me.am = 6; frame();
  me.am = 5; frame();
  settle();
  steps.push(TUT.step);                 // 5

  /* 6) 指示剂次数 2→1 */
  me.indn = 2; frame();
  me.indn = 1; frame();
  settle();
  steps.push(TUT.step);                 // 6

  /* 7) 技能：烟雾 CD 从 0 拉起 */
  me.scd = 60; frame();
  settle();
  steps.push(TUT.step);                 // 7
  return steps;
}

/* ============================================================
 * 1) API 与初始状态
 * ============================================================ */
console.log("\n--- 1) API 契约 ---");
ok(TUT && typeof TUT === "object", "window.TUT 存在");
["begin", "update", "draw", "skip", "finish", "state"].forEach((k) => {
  ok(typeof TUT[k] === "function", "TUT." + k + " 是函数");
});
okEq(TUT.total, 8, "TUT.total === 8");
ok(TUT.active === false, "初始 active === false");
okEq(TUT.step, 0, "初始 step === 0");
ok(TUT.click(10, 10) === false, "未开始时 click() 不消费");
TUT.update(0.016);                       // 未开始时调用不应抛错
ok(TUT.active === false, "未开始时 update() 不会自动开始");

TUT.begin();
ok(TUT.active === true, "begin() 后 active === true");
okEq(TUT.step, 0, "begin() 后 step === 0");
const st0 = TUT.state();
ok(st0 && st0.active === true && st0.step === 0, "state() 返回 {active,step,...}");
ok(Array.isArray(st0.done) && st0.done.length === 0, "state().done 初始为空数组");
okEq(typeof st0.hints, "number", "state().hints 是数字");

/* ============================================================
 * 2) 8 步互动推进（PC）
 * ============================================================ */
console.log("\n--- 2) 步骤推进（PC 1366×768） ---");
resetMe();
const stepsLog = driveToLastStep();
okEq(stepsLog[0], 0, "位移达标后仍在 step 0（0.6s 反馈期内不跳步）");
okEq(stepsLog[1], 1, "位移 300px → step 1");
okEq(stepsLog[2], 2, "弹药 12→9（3 发）→ step 2");
okEq(stepsLog[3], 2, "只换弹不切枪时不算完成");
okEq(stepsLog[4], 3, "换弹 + 切枪 → step 3");
okEq(stepsLog[5], 4, "命中敌人 1 次 → step 4");
okEq(stepsLog[6], 5, "喷淋器（氧化）开火 → step 5");
okEq(stepsLog[7], 6, "指示剂次数 2→1 → step 6");
okEq(stepsLog[8], 7, "烟雾 CD 拉起 → step 7（最后一步）");
okEq(TUT.step, 7, "推进后 TUT.step === 7");
const beforeState = TUT.state();
ok(beforeState.done.indexOf(0) >= 0 && beforeState.done.indexOf(6) >= 0,
   "state().done 记录已完成步骤（含第 1、7 步）");

/* 2.8 点击“我知道了，开始对战” */
const layEnd = TUT.layout(1366, 768, { step: 7 });
ok(layEnd && layEnd.finishBtn, "最后一步布局提供 finishBtn 热区");
const fb = layEnd.finishBtn, uisEnd = layEnd.UIS;
const clickX = (fb.x + fb.w / 2) * uisEnd;
const clickY = (fb.y + fb.h / 2) * uisEnd;
ok(TUT.active === true, "点击前仍在教程中");
const consumed = TUT.click(clickX, clickY);
ok(consumed === true, "点击完成按钮 → click() 返回 true（消费点击）");
ok(TUT.active === false, "完成后 active === false");
okEq(TUT.step, 8, "完成后 step === 8");
okEq(sent.length, 1, "ws.send 恰好调用 1 次");
let sentMsg = null;
try { sentMsg = JSON.parse(sent[0]); } catch (e) {}
ok(!!sentMsg && sentMsg.t === "tut_done", "发送内容为 {\"t\":\"tut_done\"}");
ok(sentMsg && Object.keys(sentMsg).length === 1, "tut_done 消息不带多余字段");
okEq(TUT.state().done.length, 8, "state().done 含全部 8 步");
TUT.finish();
okEq(sent.length, 1, "再次 finish() 不重复发送（防重）");

/* ============================================================
 * 3) skip() 与完成同样的收尾、同样只发一次
 * ============================================================ */
console.log("\n--- 3) skip() / 面板点击 ---");
sent = [];
TUT.begin();
ok(TUT.active === true, "重新 begin() → active === true");
okEq(TUT.step, 0, "重新 begin() 清空进度（step 0）");
okEq(TUT.state().done.length, 0, "重新 begin() 清空 done");
TUT.skip();
ok(TUT.active === false, "skip() 后 active === false");
okEq(TUT.step, 8, "skip() 后 step === 8（与通关同收尾）");
okEq(sent.length, 1, "skip() 发送 1 次 tut_done");
TUT.skip();
TUT.finish();
okEq(sent.length, 1, "重复 skip()/finish() 仍只发送 1 次");

/* 面板内点击（非最后一步）只消费点击、不结束教程 */
sent = [];
TUT.begin();
frame();
const layMid = TUT.layout(1366, 768, { step: 3 });
ok(TUT.click((layMid.x + 40) * layMid.UIS, (layMid.y + 60) * layMid.UIS) === true,
   "非最后一步点面板 → 消费点击");
ok(TUT.active === true, "非最后一步点面板不会结束教程");
okEq(sent.length, 0, "非最后一步点面板不会发送 tut_done");
ok(TUT.click(5, 5) === false, "面板外点击不被消费（不影响开火）");
const laySkip = TUT.layout(1366, 768, { step: 1 });
ok(TUT.click((laySkip.skip.x + laySkip.skip.w / 2) * laySkip.UIS,
             (laySkip.skip.y + laySkip.skip.h / 2) * laySkip.UIS) === true,
   "点击“跳过教程”被消费");
ok(TUT.active === false, "点击跳过按钮结束教程");

/* ============================================================
 * 4) 8 步文案关键词（防机制讲漏 / 讲错）
 * ============================================================ */
console.log("\n--- 4) 文案关键词与机制数值 ---");
const steps = TUT.steps();
okEq(steps.length, 8, "steps() 返回 8 步");
const allText = steps.map((s) => s.title + " " + s.body + " " + s.cond).join("\n");
["pH", "缓冲", "氟泄漏", "中和", "指示剂", "烟雾", "补给", "地图"].forEach((kw) => {
  ok(allText.indexOf(kw) >= 0, "文案包含关键词：" + kw);
});
ok(steps.every((s) => s.body.length >= 40), "每步正文都是 2~4 句的完整说明（长度 ≥40 字）");
ok(steps.every((s) => s.body.indexOf("。") >= 0), "每步正文以中文句号成句");
["300 px", "开火", "换弹", "切枪", "命中", "氧化", "指示剂", "烟雾", "AOE", "氟泄漏", "240"]
  .forEach((kw) => { ok(allText.indexOf(kw) >= 0, "文案覆盖具体机制：" + kw); });
/* 机制数值必须与代码一致，避免教程教错 */
["3.00", "11.00", "30000", "+12%", "−15%", "×0.70", "×1.25", "×0.85", "150 mmol", "2 次", "15 秒"]
  .forEach((kw) => { ok(allText.indexOf(kw) >= 0, "文案含正确数值：" + kw); });

/* ============================================================
 * 5) 面板几何
 * ============================================================ */
console.log("\n--- 5) 面板几何 ---");

/* PC：不越界 + 宽度 ≤40% VW + 不压 pH 表盘 / 小地图 / 试剂架 */
[[1366, 768], [2730, 1536]].forEach(([vw, vh]) => {
  const uis = hudScale(vw, vh);
  const uw = vw / uis, uh = vh / uis;
  const keyHud = [
    { name: "pH 表盘/状态卡", x: 20, y: uh - 184, w: 380, h: 164 },
    { name: "小地图", x: 20, y: 20, w: 206, h: 138 },
    { name: "试剂架", x: uw - 20 - 506, y: uh - 90, w: 506, h: 70 }
  ].map((r) => ({ name: r.name, x: r.x * uis, y: r.y * uis, w: r.w * uis, h: r.h * uis }));

  let allInside = true, allNarrow = true, allClear = true, geom = null;
  for (let s = 0; s < 8; s++) {
    const lay = TUT.layout(vw, vh, { step: s });
    const r = lay.css;
    if (!lay || lay.mode !== "pc") { allInside = false; break; }
    if (geom === null) geom = lay;
    if (r.x < 0 || r.y < 0 || r.x + r.w > vw + 0.5 || r.y + r.h > vh + 0.5) allInside = false;
    if (r.w > vw * 0.4 + 0.5) allNarrow = false;
    for (const k of keyHud) if (rectsOverlap(r, k)) allClear = false;
  }
  ok(allInside, "PC " + vw + "×" + vh + "：8 步面板全部在屏幕内");
  ok(allNarrow, "PC " + vw + "×" + vh + "：面板宽度 ≤40% VW");
  ok(allClear, "PC " + vw + "×" + vh + "：面板不压 pH 表盘/小地图/试剂架");
  console.log("       几何 " + vw + "×" + vh + "：" +
    "css=(" + geom.css.x.toFixed(1) + "," + geom.css.y.toFixed(1) + ") " +
    geom.css.w.toFixed(1) + "×" + geom.css.h.toFixed(1) +
    " · HUD=(" + geom.x + "," + geom.y + ") " + geom.w + "×" + geom.h +
    " · UIS=" + geom.UIS.toFixed(3) +
    " · 占屏宽 " + (geom.css.w / vw * 100).toFixed(1) + "%");
  const lay0 = TUT.layout(vw, vh, { step: 0 });
  const panelHud = { x: lay0.x, y: lay0.y, w: lay0.w, h: lay0.h };
  ok(rectsOverlap(panelHud, lay0.skip), "PC " + vw + "×" + vh + "：“跳过教程”按钮在面板内");
  const lay7 = TUT.layout(vw, vh, { step: 7 });
  ok(rectsOverlap(panelHud, lay7.finishBtn), "PC " + vw + "×" + vh + "：“我知道了，开始对战”按钮在面板内");
});

/* 手机：底部窄条，不与 mobile_layout 的任何可点控件重叠、不越过屏幕 */
[[768, 362], [640, 300], [844, 390], [915, 412], [1024, 600]].forEach(([vw, vh]) => {
  let ml = null, geom = null;
  try { ml = sandbox.ML.compute(vw, vh, { host: false, sniper: false }); } catch (e) { ml = null; }
  const els = (sandbox.ML && ml) ? sandbox.ML.elements(ml) : [];
  let inside = true, noOverlap = true, aboveChips = true;
  for (const s of [0, 3, 7]) {
    const lay = TUT.layout(vw, vh, { step: s, mobile: true });
    if (!lay || lay.mode !== "mobile") { inside = false; break; }
    if (geom === null) geom = lay;
    const r = { x: lay.x, y: lay.y, w: lay.w, h: lay.h };
    if (r.x < 0 || r.y < 0 || r.x + r.w > vw + 0.5 || r.y + r.h > vh + 0.5) inside = false;
    if (lay.w < 200) inside = false;
    if (ml && r.y + r.h > ml.chips.y0 + 0.5) aboveChips = false;
    for (const el of els) {
      let hit = false;
      if (el.cx !== undefined) {
        const dx = el.cx - Math.max(r.x, Math.min(el.cx, r.x + r.w));
        const dy = el.cy - Math.max(r.y, Math.min(el.cy, r.y + r.h));
        hit = dx * dx + dy * dy < el.r * el.r;
      } else {
        hit = rectsOverlap(r, el);
      }
      if (hit) noOverlap = false;
    }
  }
  ok(inside, "手机 " + vw + "×" + vh + "：窄条在屏幕内且宽度 ≥200px");
  ok(aboveChips, "手机 " + vw + "×" + vh + "：窄条位于底部武器条上方");
  ok(noOverlap, "手机 " + vw + "×" + vh + "：窄条不与 mobile_layout 控件（菜单/开火/摇杆/功能键/武器条）重叠");
  if (geom) {
    console.log("       几何 " + vw + "×" + vh + "：css=(" + geom.x + "," + geom.y + ") " +
      geom.w + "×" + geom.h + " · 占屏宽 " + (geom.w / vw * 100).toFixed(1) + "%");
  }
});

/* ============================================================
 * 6) 手机端全流程（8 步绘制不抛错 + 最后一步整条可点）
 * ============================================================ */
console.log("\n--- 6) 手机端全流程 ---");
sandbox.__MOBILE__ = true;
sandbox.VW = 768; sandbox.VH = 362;
sent = [];
resetMe();
TUT.begin();
const mobileSteps = driveToLastStep();
okEq(mobileSteps[8], 7, "手机端同样能推进到第 8 步（PC/手机共享判定）");
const layM7 = TUT.layout(768, 362, { step: 7, mobile: true });
ok(layM7.finishBtn && layM7.finishBtn.w > 60, "手机：最后一步提供“开始对战”按钮热区");
ok(TUT.click(layM7.x + layM7.w / 2, layM7.y + layM7.h / 2) === true, "手机：点窄条任意处（最后一步）被消费");
ok(TUT.active === false, "手机：点击后结束教程");
okEq(sent.length, 1, "手机：tut_done 只发 1 次");
/* 手机跳过按钮 */
sent = [];
TUT.begin();
frame();
const layM3 = TUT.layout(768, 362, { step: 3, mobile: true });
ok(TUT.click(layM3.btn.x + 2, layM3.btn.y + 2) === true, "手机：点“跳过”热区被消费");
ok(TUT.active === false, "手机：点“跳过”结束教程");
sandbox.__MOBILE__ = false;
sandbox.VW = 1366; sandbox.VH = 768;

/* ============================================================
 * 汇总
 * ============================================================ */
console.log("\n================ 结果 ================");
console.log("通过 " + pass + " / " + (pass + fail));
if (fail) {
  console.log("失败项：");
  failures.forEach((f) => console.log("  - " + f));
  process.exit(1);
}
console.log("教程校验全绿 ✓");
process.exit(0);
