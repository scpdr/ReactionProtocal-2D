/* 用 Node 假 DOM/Canvas 跑一遍 UI 绘制函数，捕捉运行时错误（不验证像素）。 */
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
  location: { host: "127.0.0.1:8765", protocol: "http:", href: "http://127.0.0.1:8765/" },
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
ctx.window = ctx;
ctx.global = ctx;
els.botCount = makeEl("botCount"); els.botCount.value = "2";
els.botDiff = makeEl("botDiff"); els.botDiff.value = "normal";

const sandbox = vm.createContext(ctx);
const base = path.join(__dirname, "..", "public");
for (const f of ["ui.js", "game.js"]) {
  vm.runInContext(fs.readFileSync(path.join(base, f), "utf8"), sandbox, { filename: f });
}

// 构造一名玩家并调用所有 HUD 绘制函数
ctx.VW = 1280; ctx.VH = 800;
ctx.DPR = 1; ctx.UIS = 1; ctx.UW = 1280; ctx.UH = 800;
ctx.myId = 1;
ctx.joined = true;
ctx.players = {
  1: { id: 1, n: "测试员", team: "red", bot: false, al: true, x: 100, y: 100,
       hp: 78, buf: 78, ph: 7.01, s: 123, k: 2, d: 1, w: "rifle", am: 24, rl: 0,
       scd: 30, acd: 20, aoe: 0, pw: "none", hv: false, sg: false, exp: 0, nk_ok: 0, resp: 0 },
  2: { id: 2, n: "对手", team: "blue", bot: true, al: true, x: 200, y: 120,
       hp: 40, buf: 40, ph: 4.2, s: 80, k: 1, d: 2, w: "lmg", am: 0, rl: 0,
       scd: 0, acd: 0, aoe: 0, pw: "none", hv: true, sg: false, exp: 2, nk_ok: 0, resp: 0 }
};
ctx.scores = { red: 3, blue: 2 };
ctx.feed = { 1: { k: "测试员", kt: "red", v: "对手", vt: "blue", w: "rifle", t: 0 } };
ctx.smokes = [[500, 500, 220, 5]];
ctx.chatMsgs = [{ name: "测试员", team: "red", msg: "测试消息", t: 0 }];
ctx.scorePops = [{ txt: "+100 击杀", t: 0, big: true, gold: false }];
ctx.hvtTip = "对手";
ctx.mouse = { x: 640, y: 400 };
ctx.hitMarker = 0.1; ctx.hitMarkerKill = false;
ctx.window._regenT = 0;
ctx.window._serverUrls = ["http://192.168.1.2:8765"];
ctx.window._isHost = true;
ctx.showAdmin = true;
ctx.adminRects = [];
ctx.chemCfg = { ct0: 0.1, pka: 7.2, acid: 0.5, destroy: 1.0, low: 3.0, high: 11.0 };

const calls = ["drawHUD", "drawMinimap", "drawKillFeed", "drawChat", "drawScorePops",
               "drawHvtTip", "drawSmokeHud", "drawCrosshair", "drawBoard", "drawHintBanner",
               "drawAdminPanel"];
for (const fn of calls) {
  if (typeof ctx[fn] !== "function") {
    console.error("MISSING:", fn);
    process.exit(1);
  }
  try {
    if (fn === "drawHUD") ctx[fn](ctx.players[1], 1000);
    else if (fn === "drawKillFeed" || fn === "drawChat" || fn === "drawScorePops" || fn === "drawHintBanner") ctx[fn](1000);
    else if (fn === "drawSmokeHud") ctx[fn](ctx.players[1]);
    else ctx[fn]();
    console.log("OK:", fn);
  } catch (e) {
    console.error("FAIL:", fn, e && e.stack || e);
    process.exit(1);
  }
}
try {
  ctx.drawDeathScreen({ killer: "对手", cause: "acidosis", ph: 2.3, resp: 2 }, 1000);
  console.log("OK: drawDeathScreen");
} catch (e) {
  console.error("FAIL: drawDeathScreen", e && e.stack || e);
  process.exit(1);
}
try {
  ctx.applyMap({ id: "test", name: "TEST MAP", w: 1200, h: 800,
                 walls: [[0, 0, 1200, 20], [0, 780, 1200, 20]],
                 spawn: { red: [20, 300, 100, 100], blue: [1080, 300, 100, 100] },
                 ammo: [[600, 400, 24]], labels: [["TEST", 100, 100, "#fff"]] });
  if (ctx.WORLD_W !== 1200 || ctx.WALLS.length !== 2) throw new Error("applyMap 未生效");
  ctx.buildMapLayer();
  console.log("OK: applyMap + buildMapLayer");
} catch (e) {
  console.error("FAIL: applyMap", e && e.stack || e);
  process.exit(1);
}
try {
  ctx.UIS = 2;
  ctx.adminRects = [{ x: 20, y: 176, w: 26, h: 20,
                      msg: { t: "admin", team: "red", n: 1 } }];
  let sent = null;
  ctx.ws = { readyState: 1, send(m) { sent = m; } };
  if (!ctx.adminPanelClick(40, 352)) throw new Error("UIS=2 下按钮点击未命中");
  if (!sent || JSON.parse(sent).team !== "red") throw new Error("点击未发送 admin 消息");
  if (ctx.adminPanelClick(40, 176)) throw new Error("非按钮区域不应命中");
  console.log("OK: adminPanelClick scaling");
} catch (e) {
  console.error("FAIL: adminPanelClick", e && e.stack || e);
  process.exit(1);
}
console.log("\nUI runtime smoke OK");
