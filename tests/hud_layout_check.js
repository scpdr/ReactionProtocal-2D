/* tests/hud_layout_check.js
 * PC HUD 布局回归：①状态卡/试剂架已缩小（v1.4）②战绩榜显示已退出玩家并标注"不在线"。
 * 纯 Node + 假 DOM/Canvas：加载 ui.js + game.js，用"记录型 ctx"观察绘制指令。
 *
 * 用法：node tests/hud_layout_check.js    退出码 0/1
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
  innerWidth: 1366, innerHeight: 768, devicePixelRatio: 1,
  location: { host: "127.0.0.1:8899", protocol: "http:", href: "http://127.0.0.1:8899/", search: "" },
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
js_after_load();
function js_after_load() { }

/* ---------- 记录型 canvas ctx：记录 translate/scale/fillText ---------- */
const rec = { ops: [], texts: [], arcs: 0, strokes: 0 };
function installRecorder() {
  rec.ops.length = 0; rec.texts.length = 0; rec.arcs = 0; rec.strokes = 0;
  const noop = () => { };
  const c = ctx.ctx;   // game.js 的 ctx 是 sandbox 里的全局对象
  const handlers = {
    translate: (x, y) => rec.ops.push(["translate", x, y]),
    scale: (x, y) => rec.ops.push(["scale", x, y]),
    fillText: (t) => rec.texts.push(String(t)),
    strokeText: (t) => rec.texts.push(String(t)),
    arc: () => { rec.arcs++; },
    stroke: () => { rec.strokes++; },
    save: () => rec.ops.push(["save"]),
    restore: () => rec.ops.push(["restore"]),
    setTransform: () => { },
    createLinearGradient: () => ({ addColorStop: () => { } }),
    createRadialGradient: () => ({ addColorStop: () => { } }),
    measureText: (s) => ({ width: String(s).length * 7 })
  };
  Object.keys(handlers).forEach((k) => { c[k] = handlers[k]; });
  // 其余方法一律当 no-op，属性读写保留
  const proxy = new Proxy(c, {
    get(o, k) { if (k in handlers) return handlers[k]; if (k in o) return o[k]; return noop; },
    set(o, k, v) { o[k] = v; return true; }
  });
  // 让 ui.js 里的 ctx 指向代理
  sandbox.ctx = proxy;
  ctx.ctx = proxy;
  return proxy;
}
installRecorder();

let fails = 0, checks = 0;
function ok(name, cond, want, got) {
  checks++;
  if (cond) { console.log(`[PASS] ${name}  期望 ${want} / 实测 ${got}`); return; }
  fails++;
  console.log(`[FAIL] ${name}  期望 ${want} / 实测 ${got}`);
}

/* ---------- 1. 状态卡缩小到 74% ---------- */
ctx.UH = 768; ctx.UW = 1366; ctx.UIS = 1; ctx.showAdmin = false;
const me = {
  id: 1, n: "测试员", team: "red", al: true, ph: 7.2, buf: 88, hp: 88, s: 320,
  w: "rifle", am: 24, res: 120, rl: 0, scd: 0, acd: 0, aoe: 0, indn: 2, indcd: 0
};
installRecorder();
ctx.drawStatusCard(me, 1000);
const scScale = rec.ops.find((o) => o[0] === "scale");
const scTrans = rec.ops.find((o) => o[0] === "translate");
ok("状态卡整体缩放 0.74", !!scScale && Math.abs(scScale[1] - 0.74) < 1e-9,
   "0.74", scScale ? String(scScale[1]) : "无");
ok("状态卡左下定位在 UH 减去缩放后高度",
   !!scTrans && Math.abs(scTrans[2] - (768 - 164 * 0.74 - 12)) < 0.01,
   "≈ 634.6", scTrans ? scTrans[2].toFixed(1) : "无");
ok("状态卡实际占屏 ≈281×121（原 380×164）",
   Math.round(380 * 0.74) === 281 && Math.round(164 * 0.74) === 121,
   "281×121", `${Math.round(380 * 0.74)}×${Math.round(164 * 0.74)}`);

/* ---------- 2. 试剂架缩小到 72% ---------- */
installRecorder();
ctx.drawWeaponRack(me);
const rkScale = rec.ops.find((o) => o[0] === "scale");
const rkTrans = rec.ops.find((o) => o[0] === "translate");
const rackW = (9 * 58 + 8 * 6);   // v2.1：加入碱式连狙后共 9 槽
ok("试剂架整体缩放 0.72", !!rkScale && Math.abs(rkScale[1] - 0.72) < 1e-9,
   "0.72", rkScale ? String(rkScale[1]) : "无");
ok("试剂架实际宽 ≈410（9 槽）", Math.round(rackW * 0.72) === 410,
   "410", String(Math.round(rackW * 0.72)));
ok("试剂架贴右下角（右侧留 16）",
   !!rkTrans && Math.abs(rkTrans[1] - (1366 - 16 - rackW * 0.72)) < 0.01,
   "≈940", rkTrans ? rkTrans[1].toFixed(0) : "无");
ok("状态卡与试剂架不再抢中间视野（间距 > 300px）",
   (1366 - 16 - rackW * 0.72) - (18 + 380 * 0.74) > 300, ">300px",
   String(Math.round((1366 - 16 - rackW * 0.72) - (18 + 380 * 0.74))));

/* ---------- 3. 战绩榜显示离线玩家 ---------- */
ctx.players = { 1: me, 2: { id: 2, n: "对手", team: "blue", k: 2, d: 4, s: 900, ph: 5.2, buf: 40 } };
ctx.myId = 1;
ctx.offline = [{ n: "离线侠", team: "red", k: 7, d: 3, s: 1234 }];
ctx.TEAM_COLOR = ctx.TEAM_COLOR || { red: "#ff5252", blue: "#39c7d8" };
ctx.window._serverUrls = [];
installRecorder();
ctx.drawBoard();
const joined = rec.texts.join("|");
ok("榜单出现离线玩家名字", joined.indexOf("离线侠") >= 0, "含 离线侠", joined.indexOf("离线侠") >= 0 ? "含" : "缺");
ok("榜单标注“不在线”", joined.indexOf("不在线") >= 0, "含 不在线", joined.indexOf("不在线") >= 0 ? "含" : "缺");
ok("离线玩家积分仍然显示", joined.indexOf("1234") >= 0, "含 1234", joined.indexOf("1234") >= 0 ? "含" : "缺");
ok("在线玩家仍然显示", joined.indexOf("对手") >= 0, "含 对手", joined.indexOf("对手") >= 0 ? "含" : "缺");

/* ---------- 4. 可滚动战绩榜 ---------- */
const mk = (id, s) => ({ id, n: "P" + id, team: id % 2 ? "red" : "blue",
                         k: 1, d: 1, s, ph: 7.2, buf: 80 });
ctx.players = {};
for (let i = 1; i <= 20; i++) ctx.players[i] = mk(i, 1000 - i * 10);
ctx.myId = 1;
ctx.offline = [];
ctx.showAdmin = false;
ctx.scrollY = { board: 0, admin: 0 };
ok("20 人时榜单可滚动（max=6）", ctx.boardScrollMax() === 6, "6", String(ctx.boardScrollMax()));
installRecorder();
ctx.drawBoard();
const topTexts = rec.texts.join("|");
ok("初始显示第 1 名", topTexts.indexOf("P1") >= 0, "含 P1",
   topTexts.indexOf("P1") >= 0 ? "含" : "缺");
ctx.scrollY.board = 6;
installRecorder();
ctx.drawBoard();
const botTexts = rec.texts.join("|");
ok("滚到底后能看到第 20 名（原来看不到）", botTexts.indexOf("P20") >= 0, "含 P20",
   botTexts.indexOf("P20") >= 0 ? "含" : "缺");
ok("滚动后第 1 名移出可见区", rec.texts.indexOf("P1") < 0, "不含 P1",
   rec.texts.indexOf("P1") < 0 ? "不含" : "仍含");
ok("滚动提示显示总人数", botTexts.indexOf("共 20 人") >= 0, "含 共 20 人",
   botTexts.indexOf("共 20 人") >= 0 ? "含" : "缺");
ctx.scrollY.board = 0;

/* ---------- 5. 面板上鼠标可见（cursor） ---------- */
const cvEl = ctx.document.getElementById("cv");
ctx.showBoard = false; ctx.showAdmin = false; ctx.showHelp = false;
ctx.mouse = { x: 600, y: 400 };
ctx.updateCursor();
ok("游戏中光标隐藏（自绘准星）", cvEl.style.cursor === "none", "none", String(cvEl.style.cursor));
ctx.showBoard = true;
ctx.updateCursor();
ok("战绩榜打开后显示系统光标", cvEl.style.cursor === "default", "default", String(cvEl.style.cursor));
ctx.showBoard = false; ctx.showAdmin = true; ctx.window._isHost = true;
ctx.mouse = { x: 30, y: 190 };
ctx.updateCursor();
ok("管理面板内显示系统光标", cvEl.style.cursor === "default", "default", String(cvEl.style.cursor));
ctx.showAdmin = false;

/* ---------- 6. 事件/Boss HUD ---------- */
ctx.eventInfo = { id: "super_speed", name: "神速力", desc: "所有人移速 ×1.7", left: 12.4 };
ctx.bossInfo = { id: 99, n: "液氮守卫 BOSS", bos: 1, bsh: 700, bmx: 1400, buf: 100 };
ctx.bossContrib = [{ n: "P1", dmg: 500 }, { n: "P2", dmg: 300 }];
ctx.announceInfo = { title: "随机事件 · 神速力", desc: "所有人移速 ×1.7", t: 1e12 };
ctx.bossResult = null; ctx.bossResultT = 0;
installRecorder();
ctx.drawHUD(mk(1, 100), 1000);
const hudTexts = rec.texts.join("|");
ok("HUD 显示事件名与倒计时", hudTexts.indexOf("神速力") >= 0 && hudTexts.indexOf("13s") >= 0,
   "含 神速力/13s", String(hudTexts.indexOf("神速力") >= 0) + "/" +
   String(hudTexts.indexOf("13s") >= 0));
ok("HUD 显示 Boss 大血条", hudTexts.indexOf("液氮守卫 BOSS") >= 0 &&
   hudTexts.indexOf("700") >= 0, "含 BOSS名/护盾值",
   String(hudTexts.indexOf("液氮守卫 BOSS") >= 0) + "/" +
   String(hudTexts.indexOf("700") >= 0));
ok("HUD 显示伤害贡献", hudTexts.indexOf("伤害贡献") >= 0, "含 伤害贡献",
   hudTexts.indexOf("伤害贡献") >= 0 ? "含" : "缺");
ok("事件播报横幅可见", hudTexts.indexOf("随机事件 · 神速力") >= 0, "含 随机事件",
   hudTexts.indexOf("随机事件") >= 0 ? "含" : "缺");
ctx.bossResult = { boss: "液氮守卫 BOSS", by: "P1", at: 1, top: [
  { n: "P1", dmg: 500, reward: 60 }, { n: "P2", dmg: 300, reward: 37 }] };
ctx.bossResultT = 1e12;
installRecorder();
ctx.drawHUD(mk(1, 100), 1000);
const resultTexts = rec.texts.join("|");
ok("Boss 战结算面板显示奖励", resultTexts.indexOf("BOSS 战结算") >= 0 &&
   resultTexts.indexOf("+60 分") >= 0, "含 结算/+60 分",
   String(resultTexts.indexOf("BOSS 战结算") >= 0) + "/" +
   String(resultTexts.indexOf("+60 分") >= 0));

/* ---------- 7. 击杀特效（移植自 3D 版） ---------- */
ctx.eventInfo = null; ctx.bossInfo = null; ctx.announceInfo = null;
ctx.bossResult = null; ctx.bossResultT = 0;
ctx.killConfirm = null; ctx.killImpactT = 0;
installRecorder();
ctx.drawHUD(mk(1, 100), 1000);
const baseArcs = rec.arcs || 0;        // 状态卡表盘等固定圆弧的基准数
ctx.killConfirm = { name: "靶子B", w: "rifle", cause: "acidosis", pts: 126,
                    t: 1000 - 60 };
ctx.killImpactT = 1000 - 60;
installRecorder();
ctx.drawHUD(mk(1, 100), 1000);
const killTexts = rec.texts.join("|");
ok("击杀确认卡显示 ELIMINATED", killTexts.indexOf("ELIMINATED") >= 0, "含 ELIMINATED",
   killTexts.indexOf("ELIMINATED") >= 0 ? "含" : "缺");
ok("击杀确认卡显示 KILL CONFIRMED 标题",
   killTexts.indexOf("KILL CONFIRMED") >= 0, "含 KILL CONFIRMED",
   killTexts.indexOf("KILL CONFIRMED") >= 0 ? "含" : "缺");
ok("击杀确认卡显示被淘汰者与得分", killTexts.indexOf("靶子B") >= 0 &&
   killTexts.indexOf("+126") >= 0, "含 靶子B/+126",
   String(killTexts.indexOf("靶子B") >= 0) + "/" + String(killTexts.indexOf("+126") >= 0));
ok("击杀瞬间准心冲击环有绘制", (function () {
  var before = ctx.drawKillImpact._draws || 0;
  ctx.killImpactT = 1000 - 60;
  ctx.drawHUD(mk(1, 100), 1000);
  return (ctx.drawKillImpact._draws || 0) === before + 1;
})(), "绘制次数 +1", String(ctx.drawKillImpact._draws || 0));
ok("冲击环会随时间消失（430ms 后不再画）", (function () {
  var before = ctx.drawKillImpact._draws || 0;
  ctx.killImpactT = 1000 - 600;
  ctx.drawHUD(mk(1, 100), 1000);
  return (ctx.drawKillImpact._draws || 0) === before;
})(), "绘制次数不变", String(ctx.drawKillImpact._draws || 0));
ctx.killConfirm = null; ctx.killImpactT = 0;

console.log("");
console.log(`总计: ${checks} 项检查，${checks - fails} 通过 / ${fails} 失败。`);
process.exitCode = fails ? 1 : 0;
