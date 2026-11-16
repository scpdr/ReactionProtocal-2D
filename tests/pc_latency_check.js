/* tests/pc_latency_check.js
 * PC 端"跟手/卡顿"相关数值回归（纯 Node + 假 DOM 加载 public/game.js）。
 *
 * 钉住三件事：
 *  1) 插值缓冲自适应：60Hz 快照 ≈26ms、30Hz ≈51ms、异常值被 clamp 到 [24,80]（原来死值 80ms）
 *  2) 自动降画质：连续 3 秒 <45fps 时 DPR 降到 1；?perf=0 时不自动降级
 *  3) 自己的位置走快速收敛（smoothSelf），不再吃满插值缓冲
 *
 * 用法：node tests/pc_latency_check.js   退出码 0/1
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

function makeSandbox(search) {
  const els = {};
  const ctx = {
    console,
    performance: { now: () => Date.now() },
    innerWidth: 1366, innerHeight: 768,
    devicePixelRatio: 2,
    location: { host: "127.0.0.1:8899", protocol: "http:", href: "http://127.0.0.1:8899/", search: search || "" },
    addEventListener() {}, removeEventListener() {},
    requestAnimationFrame() { return 0; },
    setInterval() { return 0; }, clearInterval() {}, setTimeout() { return 0; }, clearTimeout() {},
    localStorage: { _ls: {}, getItem(k) { return this._ls[k] === undefined ? null : this._ls[k]; },
                    setItem(k, v) { this._ls[k] = String(v); } },
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
  ctx.URLSearchParams = URLSearchParams;
  const sandbox = vm.createContext(ctx);
  const base = path.join(__dirname, "..", "public");
  for (const f of ["ui.js", "game.js"]) {
    vm.runInContext(fs.readFileSync(path.join(base, f), "utf8"), sandbox, { filename: f });
  }
  return ctx;
}

let fails = 0, checks = 0;
function ok(name, cond, want, got) {
  checks++;
  if (cond) { console.log(`[PASS] ${name}  期望 ${want} / 实测 ${got}`); return; }
  fails++;
  console.log(`[FAIL] ${name}  期望 ${want} / 实测 ${got}`);
}
const near = (a, b, tol) => Math.abs(a - b) <= tol;

/* ---------- 1. 插值缓冲自适应 ---------- */
let c = makeSandbox("");
c.snapIvMs = 16.7;
const d60 = c.interpDelayMs();
ok("60Hz 快照的插值缓冲 ≈26ms", near(d60, 25.9, 1.0), "≈25.9ms", d60.toFixed(1) + "ms");
c.snapIvMs = 33.3;
const d30 = c.interpDelayMs();
ok("30Hz 快照的插值缓冲 ≈52ms", near(d30, 51.6, 1.5), "≈51.6ms", d30.toFixed(1) + "ms");
c.snapIvMs = 16.7;
ok("比原来的死值 80ms 明显更跟手", d60 < 40, "<40ms", d60.toFixed(1) + "ms");
c.snapIvMs = 1;
ok("异常小间隔被下限 24ms 兜住", near(c.interpDelayMs(), 24, 0.01), "24ms",
   c.interpDelayMs().toFixed(1) + "ms");
c.snapIvMs = 500;
ok("网络很差时被上限 80ms 兜住", near(c.interpDelayMs(), 80, 0.01), "80ms",
   c.interpDelayMs().toFixed(1) + "ms");

/* ---------- 2. 帧率标签 ---------- */
c.frameMs = 7.8;
c.snapIvMs = 16.7;
const label = c.perfLabel();
ok("帧率标签含 FPS 与插值毫秒数", /FPS \d+/.test(label) && /插值 \d+ms/.test(label),
   "FPS n · x.xms · 插值 nms", label);

/* ---------- 3. 自动降画质 ---------- */
const c2 = makeSandbox("");            // 自动模式
ok("初始为高画质", c2.lowQuality === false && c2.DPR_CAP === 3, "false / cap=3",
   `${c2.lowQuality} / cap=${c2.DPR_CAP}`);
c2.frameMs = 50;                        // ≈20fps
for (let i = 1; i <= 4; i++) c2.qualityTick(i * 1200);
ok("连续低帧率 3 秒后自动降画质", c2.lowQuality === true && c2.DPR_CAP === 1,
   "true / cap=1", `${c2.lowQuality} / cap=${c2.DPR_CAP}`);
ok("降画质后 DPR 变成 1（像素量 1/4）", c2.DPR === 1, "DPR=1", String(c2.DPR));

const c3 = makeSandbox("?perf=0");      // 手动锁定高画质
c3.frameMs = 60;
for (let i = 1; i <= 6; i++) c3.qualityTick(i * 1200);
ok("?perf=0 时不自动降级", c3.lowQuality === false && c3.DPR_CAP === 3, "false / cap=3",
   `${c3.lowQuality} / cap=${c3.DPR_CAP}`);

const c4 = makeSandbox("?perf=1");      // 手动锁定低画质
c4.qualityTick(1200);
ok("?perf=1 时立刻低画质", c4.lowQuality === true && c4.DPR_CAP === 1, "true / cap=1",
   `${c4.lowQuality} / cap=${c4.DPR_CAP}`);

/* ---------- 4. 自己的位置快速收敛（smoothSelf） ---------- */
const c5 = makeSandbox("");
c5.myId = 1;
c5.players = { 1: { id: 1, x: 100, y: 50, a: 0.3, al: true } };
let display = { 1: { x: 0, y: 0, a: 0 } };
c5.smoothSelf(display, 1000, 1 / 60);
ok("首次直接对齐最新快照位置（重生/换图不拖影）",
   near(display[1].x, 100, 0.01) && near(display[1].y, 50, 0.01),
   "(100,50)", `(${display[1].x.toFixed(1)},${display[1].y.toFixed(1)})`);
ok("朝向用本地即时值", near(display[1].a, 0.3, 1e-9), "0.3", String(display[1].a));

// 服务器把玩家推到 x=200：平滑值应当逐帧追上去，且不会超调
c5.players[1].x = 200;
let prev = display[1].x, monotone = true, overshoot = false;
for (let i = 0; i < 30; i++) {
  display = { 1: { x: prev, y: 50, a: 0 } };
  c5.players[1].x = 200;
  c5.smoothSelf(display, 1000 + i * 16.7, 1 / 60);
  if (display[1].x < prev - 1e-9) monotone = false;
  if (display[1].x > 200 + 1e-6) overshoot = true;
  prev = display[1].x;
}
ok("平滑追帧单调不抖", monotone, "单调递增", monotone ? "是" : "否");
ok("不超调（不会画到服务器位置之外）", !overshoot, "x ≤ 200", overshoot ? "超调" : "未超调");
ok("30 帧（≈0.5s）内基本追上", near(prev, 200, 2), "≈200", prev.toFixed(1));

// 瞬移（重生）直接对齐，不做长距离插值
c5.players[1].x = 1800;
display = { 1: { x: prev, y: 50, a: 0 } };
c5.smoothSelf(display, 2000, 1 / 60);
ok("远距离瞬移直接对齐（重生不飞过去）", near(display[1].x, 1800, 0.01), "1800",
   display[1].x.toFixed(1));

console.log("");
console.log(`总计: ${checks} 项检查，${checks - fails} 通过 / ${fails} 失败。`);
process.exitCode = fails ? 1 : 0;
