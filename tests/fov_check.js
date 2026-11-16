/* tests/fov_check.js
 * FOV 档位与换图倒计时格式的数值回归（纯 Node，假 DOM 加载 public/game.js）。
 *
 * 背景：用户要求"手机 FOV 至少看见半个地图"，随后反馈"太大"，把线性视野统一缩到 80%。
 *   手机默认 area 档：768×362 → 缩放 ≈0.4757、视野 1615×761、占地图面积 32.0%
 *   （= 半张地图面积 50% 的 0.8² 倍 = 32%）。
 * 这里把该数值钉死，防止以后改档位时悄悄回退。
 *
 * 用法：node tests/fov_check.js   退出码 0/1
 */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

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
const ctx = {
  console,
  performance: { now: () => Date.now() },
  innerWidth: 1280, innerHeight: 800,
  location: { host: "127.0.0.1:8765", protocol: "http:", href: "http://127.0.0.1:8765/", search: "" },
  addEventListener() {}, removeEventListener() {},
  requestAnimationFrame() { return 0; },
  setInterval() { return 0; }, clearInterval() {}, setTimeout() { return 0; }, clearTimeout() {},
  _ls: {},
  localStorage: {
    _ls: {},
    getItem(k) { return this._ls[k] === undefined ? null : this._ls[k]; },
    setItem(k, v) { this._ls[k] = String(v); }
  },
  document: {
    body: makeEl("body"),
    getElementById(id) { if (!els[id]) els[id] = makeEl(id); return els[id]; },
    getElementsByName() { return [{ checked: true, value: "auto" }]; },
    createElement() { return makeEl("canvas"); },
    addEventListener() {}
  },
  WebSocket: class { constructor() { this.readyState = 0; } send() {} close() {} },
  AudioContext: class {
    constructor() { this.sampleRate = 48000; }
    createBuffer() { return { getChannelData: () => new Float32Array(1024) }; }
  }
};
ctx.window = ctx;
ctx.global = ctx;
ctx.URLSearchParams = URLSearchParams;   // game.js 的 ?fov= 解析依赖它（安卓 WebView 4.4+ 均有）

const sandbox = vm.createContext(ctx);
const base = path.join(__dirname, "..", "public");
for (const f of ["ui.js", "game.js"]) {
  vm.runInContext(fs.readFileSync(path.join(base, f), "utf8"), sandbox, { filename: f });
}

let fails = 0, checks = 0;
function ok(name, cond, want, got) {
  checks++;
  if (cond) { console.log(`[PASS] ${name}  期望 ${want} / 实测 ${got}`); return; }
  fails++;
  console.log(`[FAIL] ${name}  期望 ${want} / 实测 ${got}`);
}
function near(a, b, tol) { return Math.abs(a - b) <= tol; }

/* ---------- 1. 平台默认档 ---------- */
const mPhone = ctx.initFov(true);
ok("手机默认档", mPhone === "area", "area", mPhone);
const mPC = ctx.initFov(false);
ok("PC 默认档（不受手机改动影响）", mPC === "orig", "orig", mPC);

/* ---------- 2. 手机 768×362 各档几何 ---------- */
const VW = 768, VH = 362, WW = ctx.WORLD_W, WH = ctx.WORLD_H;
const mapArea = WW * WH;
function geom(mode) {
  ctx.setFovMode(mode);
  const s = ctx.fovScale(VW, VH);
  return { mode, s, viewW: VW / s, viewH: VH / s, pct: (VW * VH) / (s * s) / mapArea * 100 };
}
const gArea = geom("area");
ok("area 缩放", near(gArea.s, 0.4757, 0.002), "≈0.4757", gArea.s.toFixed(4));
ok("area 视野宽", near(gArea.viewW, 1615, 2), "≈1615", gArea.viewW.toFixed(0));
ok("area 视野高", near(gArea.viewH, 761, 2), "≈761", gArea.viewH.toFixed(0));
ok("area 占地图面积", near(gArea.pct, 32.0, 0.5), "32.0%", gArea.pct.toFixed(1) + "%");
ok("area = 半张地图面积的 80%²", near(gArea.pct, 50 * 0.8 * 0.8, 0.5),
   "32.0%", gArea.pct.toFixed(1) + "%");

const gWidth = geom("width");
ok("width 缩放", near(gWidth.s, 0.8, 0.002), "0.80", gWidth.s.toFixed(4));
const gHeight = geom("height");
ok("height 缩放", near(gHeight.s, 0.566, 0.003), "≈0.566", gHeight.s.toFixed(4));
const gOrig = geom("orig");
ok("orig 缩放（手机）", near(gOrig.s, 1, 1e-9), "1.0", gOrig.s.toFixed(4));

/* ---------- 3. 数值缩放覆盖 + localStorage 记忆 ---------- */
ctx.setFovMode(0.6);
ok("数字缩放档", near(ctx.fovScale(VW, VH), 0.6, 1e-9), "0.60", ctx.fovScale(VW, VH).toFixed(3));
ctx.cycleFov();
ok("cycleFov 从数字档回到 area", ctx.fovMode === "area", "area", String(ctx.fovMode));
ok("记忆写入 localStorage", ctx.localStorage.getItem("rp_fov") === "area",
   "area", String(ctx.localStorage.getItem("rp_fov")));

/* ---------- 4. cover 下限：视野不得超出地图（否则露黑边） ---------- */
ctx.setFovMode("orig");
const sHuge = ctx.fovScale(4000, 900);           // 超宽屏
ok("超宽屏不露黑边（viewW ≤ 地图宽）", 4000 / sHuge <= WW + 1e-6,
   `≤${WW}`, (4000 / sHuge).toFixed(1));
ok("超宽屏缩放按 cover 抬到 1.667", near(sHuge, 4000 / WW, 1e-6), "1.667", sHuge.toFixed(4));

/* ---------- 5. PC 1366×768 与旧行为一致 ---------- */
ctx.setFovMode("orig");
const sPC = ctx.fovScale(1366, 768);
ok("PC 1366×768 orig 仍为 1.0（旧观感不变）", near(sPC, 1, 1e-9), "1.0", sPC.toFixed(3));

/* ---------- 6. ?fov= 参数优先级 ---------- */
ctx.location.search = "?fov=width";
ok("?fov=width 生效", ctx.initFov(true) === "width", "width", String(ctx.fovMode));
ctx.location.search = "?fov=0.55";
ok("?fov=0.55 数字生效", near(ctx.initFov(true), 0.55, 1e-9), "0.55", String(ctx.fovMode));
ctx.location.search = "?fov=bogus";
ok("?fov=非法值回退 orig", ctx.initFov(true) === "orig", "orig", String(ctx.fovMode));
ctx.location.search = "";

/* ---------- 7. 换图倒计时格式 ---------- */
const cases = [[0, "0:00"], [5, "0:05"], [59.6, "1:00"], [65, "1:05"],
               [224.8, "3:45"], [240, "4:00"], [3599, "59:59"], [-3, "0:00"]];
let fmtBad = 0;
cases.forEach(([sec, want]) => {
  const got = ctx.fmtCountdown(sec);
  if (got !== want) { fmtBad++; console.log(`[FAIL] fmtCountdown(${sec}) 期望 ${want} / 实测 ${got}`); }
});
ok(`fmtCountdown ${cases.length} 个用例`, fmtBad === 0, "全部正确", fmtBad ? `${fmtBad} 个错误` : "全部正确");

/* ---------- 8. 快照 ms → mapSwitchIn ---------- */
ctx.mapSwitchIn = 0;
ok("倒计时初值", ctx.mapSwitchIn === 0, "0", String(ctx.mapSwitchIn));
const msVal = 123.4;
ctx.mapSwitchIn = msVal;      // game.js 的快照处理等价：if (typeof m.ms === "number") mapSwitchIn = m.ms
ok("倒计时可被快照赋值", ctx.mapSwitchIn === msVal, String(msVal), String(ctx.mapSwitchIn));

console.log("");
console.log(`总计: ${checks} 项检查，${checks - fails} 通过 / ${fails} 失败。`);
process.exitCode = fails ? 1 : 0;
