/* Reaction Protocol · 新手教程（互动式机制教学）
 * ============================================================
 * 单文件、无构建、无第三方库、无 DOM 元素；全部用 Canvas 绘制，
 * 复用 ui.js 的 uiPanel / uiMono / uiFont / uiRR / uiBar / UI 配色。
 *
 * 设计原则：
 *   1) 每个机制都要玩家“真的做一次”（位移/开火/换弹/命中/氧化开火/
 *      指示剂或补给/技能），最后一步点击按钮确认；
 *   2) 教程只读游戏全局状态（players/myId/joined/ws/feed/dmgTexts…），
 *      不改 game.js 的任何逻辑，也不写 localStorage；
 *   3) 服务端已在 join 时给新手玩家打 tut 标记（免疫伤害、AI 不锁敌），
 *      教程只在 joined 后运行；
 *   4) 完成/跳过统一 ws.send({t:"tut_done"}) 且只发一次（连接断了会重试）。
 *
 * 对外 API（Lead 接线用）：
 *   window.TUT = {
 *     active: false, step: 0, total: 8,
 *     begin(), update(dt), draw(), skip(), finish(),
 *     state() -> {active, step, done:[...], hints},
 *     // 以下是额外提供给接线 / 调试的（不属于冻结契约，但稳定）：
 *     click(cssX,cssY) -> bool   // 教程面板点击；true=已消费，别再让子弹打出去
 *     layout(VW,VH,opts) -> 面板几何（CSS 像素 + HUD 坐标）
 *     steps() -> 8 步文案（{id,title,body,cond}）
 *     KEYS -> 按键表（默认与 game.js 一致：烟雾 E / AOE Q / 指示剂 F / 氟泄漏 N）
 *   }
 *
 * Lead 接线（4 处，均不在本文件内）：
 *   1) index.html / mobile.html：在 game.js 之后加 script 标签加载 tutorial.js（`<script src="tutorial.js">`）
 *   2) ws.onmessage 的 m.t === "joined" 分支：if (m.tut) TUT.begin();
 *   3) render(now) 主循环：drawSmokeAim(); 之后加 if (window.TUT) TUT.update(dt);
 *   4) （可选，双保险）game.js mousedown 开头：
 *        if (TUT.active && TUT.click(e.clientX, e.clientY)) return;
 *      本文件自己也挂了 window 捕获阶段的 mousedown/touchstart 监听来拦截面板点击，
 *      所以即使不改 game.js 点击也不会走火；但显式转交更直观，也能避免冒泡歧义。
 */
(function (root) {
  "use strict";

  var TOTAL = 8;
  var TRANS_SECONDS = 0.6;          // 每步完成后的停顿（视觉反馈 + 自动进入下一步）
  var HINT_AFTER = 4.5;             // 一步卡住多少秒后给操作提示

  /* ---------------- 按键表：与 public/game.js 当前实现保持一致 ----------------
   * game.js: KeyE=烟雾（trySmokeAim）、KeyQ=AOE、KeyF=指示剂、KeyN=氟泄漏、KeyR=换弹。
   * 如果 Lead 之后把烟雾改绑到 G，只需在页面里先设 window.RP_TUT_KEYS = {smoke:"G"}，
   * 或把下面默认值改掉即可（教程文案会跟着变）。
   */
  var KEYS = { smoke: "E", aoe: "Q", indicator: "F", nuke: "N", reload: "R" };
  try {
    if (root && root.RP_TUT_KEYS) {
      for (var kk in root.RP_TUT_KEYS) {
        if (Object.prototype.hasOwnProperty.call(KEYS, kk)) KEYS[kk] = String(root.RP_TUT_KEYS[kk]);
      }
    }
  } catch (e) { /* 忽略 */ }

  /* ---------------- 8 步教程文案 ----------------
   * 机制数字全部取自当前服务端代码，别凭印象改：
   *   pH 死线 3.00 / 11.00（server.py low/high = 3.0 / 11.0）
   *   缓冲环 = 缓冲对储备 C_T（server.py: RING_IS_RESERVE = True）
   *   酸碱同向 +12% / 反向 -15% / 满缓冲(>60%) 酸碱 ×0.70（game/chem.py:279-287）
   *   氧化/物理：缓冲 >60% 效率 ×1.25，<25% 效率 ×0.85（game/chem.py:311-316）
   *   合成氨/氟泄漏：隐藏可用积分每 30000 一次，3 秒预警，150 mmol 剥离（server.py nuke_*）
   *   地图默认 240s 轮换且战绩保留（server.py MAP_SWITCH_SECONDS=240）
   *   指示剂每命 2 次、CD 15s、标记 3s（server.py use_indicator）
   *   烟雾 CD 60s / 持续 12s；AOE CD 60s / 持续 8s / 半径 100 / 60% 伤害
   */
  var STEPS = [
    {
      id: "move",
      title: "移动与走位",
      body: "局域网 2D 对战 FPS：鼠标瞄准、WASD 走位、墙会挡子弹。你现在在独立训练室，只有 3 个静止靶子，不会被打。",
      cond: "累计移动 300 px"
    },
    {
      id: "fire",
      title: "瞄准与开火",
      body: "左键开火。连续射击散布会变大、点射更稳，打空会有空仓提示音。先朝空地打 3 发。",
      cond: "开火 3 发"
    },
    {
      id: "reload",
      title: "换弹与切枪",
      body: "弹匣打空按 R 换弹（1.3–3.2 秒），备弹打光要去补给点。按 1–9 或滚轮换试剂，不同试剂属于不同化学通道。",
      cond: "完成一次换弹 + 一次切枪"
    },
    {
      id: "chem",
      title: "化学核心：pH 与缓冲",
      body: "你的“血条”是 pH：3.00–11.00 为安全区，越界即阵亡。缓冲环是缓冲对储备：酸/碱只改 pH，氧化与物理才直接剥缓冲。",
      cond: "命中靶子 1 次（靶子 A/B/C，打完自动复位）"
    },
    {
      id: "channels",
      title: "三个通道与克制",
      body: "酸推低 pH、碱推高 pH、氧化/物理剥缓冲。同向补伤 +12%，反向 −15%；缓冲 >60% 时酸碱 ×0.70、氧化 ×1.25，<25% 时 ×0.85。",
      cond: "切到 5 号喷淋器（氧化）并开火 1 次"
    },
    {
      id: "support",
      title: "救援与补给",
      body: "队友被酸蚀就喷碱、被碱蚀就喷酸＝中和救援（方向错完全无效）。地图上的 + 补给点补满弹药（10 秒冷却）。",
      cond: "用一次指示剂 或 走进补给点补满备弹"
    },
    {
      id: "skills",
      title: "技能三件套",
      body: "E 烟雾弹遮挡视线；Q 技能让子弹带溅射 8 秒；F 指示剂标记敌人 3 秒并显示 pH/缓冲（每命 2 次，CD 15 秒）。",
      cond: "触发烟雾 / AOE / 指示剂任意一个"
    },
    {
      id: "score",
      title: "积分、氟泄漏与地图轮换",
      body: "积分来自伤害、击杀与助攻。隐藏可用积分每 30000 可放一次氟泄漏（剥 150 mmol 缓冲，满缓冲必死），放完直接扣、界面不显示数值；地图每 240 秒轮换。",
      cond: "点击“我知道了，开始对战”"
    }
  ];

  var ACCENT = ["#7dffb2", "#ffc857", "#ff9f43", "#39c7d8",
                "#b07cff", "#7dffb2", "#ffc857", "#ff6b35"];

  /* ---------------- 运行状态 ---------------- */
  var active = false;
  var step = 0;                     // 0..7 当前步骤；完成/跳过 = 8
  var doneList = [];                // 已完成步骤下标
  var hintCount = 0;                // 显示过多少次操作提示（state().hints）
  var transT = 0;                   // 步骤完成后的 0.6s 停顿
  var flashT = 0;                   // “✓ 完成”反馈
  var stepStartMs = 0;
  var hinted = false;
  var doneSent = false;             // ws.send({t:"tut_done"}) 只发一次
  var pendingDone = false;          // 断线时等待重试
  var prev = null;                  // 上一帧自己
  var prevQ = {};                   // 上一帧敌人 {pid:{ph,buf,hp}}
  var seenDmg = [];                 // 已处理的 dmgTexts 引用（自己命中的证据）
  var seenFeed = {};                // 已处理的击杀播报 id
  var installed = false;
  var layoutCache = { key: "", lay: null };

  var S = {
    dist: 0, shots: 0, oxShots: 0, hits: 0,
    reloads: 0, switches: 0, indUses: 0, smokeUses: 0, skillUses: 0, supply: 0,
    baseDist: 0, baseShots: 0, baseOx: 0, baseHits: 0,
    baseReloads: 0, baseSwitches: 0, baseInd: 0, baseSmoke: 0, baseSkill: 0, baseSup: 0
  };

  /* ---------------- 小工具 ---------------- */
  function nowMs() {
    try {
      if (typeof performance !== "undefined" && performance && performance.now) return performance.now();
    } catch (e) { }
    return Date.now();
  }
  function num(v, d) { return (typeof v === "number" && isFinite(v)) ? v : (d === undefined ? 0 : d); }
  function clamp(v, a, b) { return v < a ? a : (v > b ? b : v); }
  function round(v) { return Math.round(v); }

  function sfxSafe(name, vol) {
    try { if (typeof sfx === "function") sfx(name, vol); } catch (e) { }
  }

  /* ---------------- 全局状态读取（不 eval，逐项 typeof 保护） ---------------- */
  function readEnv() {
    var e = {
      players: null, myId: null, joined: false, ws: null, feed: null, dmgTexts: null,
      ctx: null, VW: 0, VH: 0, DPR: 1, mobile: false, modal: false
    };
    try { if (typeof players !== "undefined") e.players = players; } catch (x) { }
    try { if (typeof myId !== "undefined") e.myId = myId; } catch (x) { }
    try { if (typeof joined !== "undefined") e.joined = !!joined; } catch (x) { }
    try { if (typeof ws !== "undefined") e.ws = ws; } catch (x) { }
    try { if (typeof feed !== "undefined") e.feed = feed; } catch (x) { }
    try { if (typeof dmgTexts !== "undefined") e.dmgTexts = dmgTexts; } catch (x) { }
    try { if (typeof ctx !== "undefined") e.ctx = ctx; } catch (x) { }
    try { if (typeof VW !== "undefined") e.VW = num(VW); } catch (x) { }
    try { if (typeof VH !== "undefined") e.VH = num(VH); } catch (x) { }
    try { if (typeof DPR !== "undefined") e.DPR = Math.max(1, num(DPR, 1)); } catch (x) { }
    try {
      if (typeof window !== "undefined" && window && window.__MOBILE__) e.mobile = true;
    } catch (x) { }
    try {
      if (typeof showBoard !== "undefined" && showBoard) e.modal = true;
      if (typeof showHelp !== "undefined" && showHelp) e.modal = true;
      if (typeof deadInfo !== "undefined" && deadInfo) e.modal = true;
    } catch (x) { }
    if (!e.VW) { try { e.VW = root.innerWidth || 1280; } catch (x) { } }
    if (!e.VH) { try { e.VH = root.innerHeight || 800; } catch (x) { } }
    return e;
  }

  function myPlayer(env) {
    if (!env.players || env.myId === null || env.myId === undefined) return null;
    return env.players[env.myId] || null;
  }

  /* ---------------- 每帧观测 ---------------- */
  function observe(me) {
    return {
      x: num(me.x), y: num(me.y), al: !!me.al, w: me.w || "",
      am: num(me.am), res: num(me.res), rl: num(me.rl),
      ph: (me.ph === null || me.ph === undefined) ? null : num(me.ph),
      buf: num(me.buf === null || me.buf === undefined ? me.hp : me.buf),
      hp: num(me.hp), scd: num(me.scd), acd: num(me.acd), aoe: num(me.aoe),
      indn: num(me.indn), indcd: num(me.indcd)
    };
  }

  function enemySnapshot(env) {
    var out = {};
    if (!env.players) return out;
    for (var pid in env.players) {
      if (+pid === env.myId) continue;
      var q = env.players[pid];
      if (!q || !q.al) continue;
      out[pid] = { ph: (q.ph === null || q.ph === undefined) ? null : num(q.ph),
                   buf: num(q.buf === null || q.buf === undefined ? q.hp : q.buf),
                   hp: num(q.hp) };
    }
    return out;
  }

  /* 自己命中敌人的证据：dmgTexts（只有 ev.for===myId 才入列）+ 自己造成的击杀播报 */
  function collectEvents(env, me, t) {
    // 步骤切换的 0.6s 反馈期内先不消费事件：这一击留给下一步算（否则会被 base 吸收）
    if (transT > 0) return;
    var i, arr = env.dmgTexts || [];
    for (i = 0; i < arr.length; i++) {
      var it = arr[i];
      if (!it || seenDmg.indexOf(it) >= 0) continue;
      seenDmg.push(it);
      var ch = it.ch;
      if (ch === "acid" || ch === "base" || ch === "oxidizing" || ch === "physical") {
        S.hits++;
      }
    }
    if (seenDmg.length > 48) seenDmg = seenDmg.slice(seenDmg.length - 32);

    var f = env.feed;
    if (f && me) {
      for (var fid in f) {
        if (seenFeed[fid]) continue;
        seenFeed[fid] = 1;
        var row = f[fid];
        if (row && me.n && row.k === me.n && row.v !== me.n) {
          S.hits++;                       // 自己击杀：同样算命中
        }
      }
    }
  }

  /* ---------------- 判定 ---------------- */
  function progress(env, t) {
    var s = Math.min(step, TOTAL - 1);
    var cur, need = 1, txt, frac = 0, ok = false;
    switch (s) {
      case 0:
        cur = Math.max(0, S.dist - S.baseDist); need = 300;
        ok = cur >= need; frac = clamp(cur / need, 0, 1);
        txt = "位移 " + Math.min(need, round(cur)) + " / 300";
        break;
      case 1:
        cur = Math.max(0, S.shots - S.baseShots); need = 3;
        ok = cur >= need; frac = clamp(cur / need, 0, 1);
        txt = "开火 " + Math.min(need, round(cur)) + " / 3";
        break;
      case 2:
        // 本步内做一遍最好；若玩家在前面步骤已经换过弹/切过枪，等 1.5 秒后也算过（防卡死）
        var settled = (t - stepStartMs) > 1500;
        var r = S.reloads > S.baseReloads || (settled && S.reloads > 0);
        var w = S.switches > S.baseSwitches || (settled && S.switches > 0);
        ok = r && w; frac = (r ? 0.5 : 0) + (w ? 0.5 : 0);
        txt = "换弹 " + (r ? "✓" : "✗") + " · 切枪 " + (w ? "✓" : "✗");
        break;
      case 3:
        cur = Math.max(0, S.hits - S.baseHits); need = 1;
        ok = cur >= need; frac = clamp(cur / need, 0, 1);
        txt = "命中 " + Math.min(need, round(cur)) + " / 1";
        break;
      case 4:
        cur = Math.max(0, S.oxShots - S.baseOx); need = 1;
        ok = cur >= need; frac = clamp(cur / need, 0, 1);
        txt = "氧化通道开火 " + Math.min(need, round(cur)) + " / 1";
        break;
      case 5:
        // 本步内再用一次最好；若前面已经用过指示剂/补给，等 1.5 秒后也算过
        var ind = S.indUses > S.baseInd, sup = S.supply > S.baseSup;
        var hist5 = (t - stepStartMs) > 1500 && (S.indUses > 0 || S.supply > 0);
        ok = ind || sup || hist5; frac = (ind || sup || hist5) ? 1 : 0;
        txt = "指示剂 " + (ind || hist5 ? "✓" : "✗") + " · 补给 " + (sup ? "✓" : "✗");
        break;
      case 6:
        var sm = S.smokeUses > S.baseSmoke, ao = S.skillUses > S.baseSkill, iv = S.indUses > S.baseInd;
        var hist6 = (t - stepStartMs) > 1500 && (S.smokeUses > 0 || S.skillUses > 0 || S.indUses > 0);
        ok = sm || ao || iv || hist6; frac = ok ? 1 : 0;
        if (hist6 && !(sm || ao || iv)) { sm = S.smokeUses > 0; ao = S.skillUses > 0; iv = S.indUses > 0; }
        txt = "烟雾 " + (sm ? "✓" : "✗") + " · AOE " + (ao ? "✓" : "✗") + " · 指示剂 " + (iv ? "✓" : "✗");
        break;
      default:
        ok = false; frac = 1;
        txt = "按钮确认";
        break;
    }
    return { ok: ok, txt: txt, frac: frac };
  }

  function shortProgress(s, p) {
    if (s === 7) return "点击开始对战";
    if (s === 2) return p.txt;
    if (s === 5 || s === 6) return p.txt;
    if (s === 0) return p.txt;
    return p.txt;
  }

  function baseCounters() {
    S.baseDist = S.dist; S.baseShots = S.shots; S.baseOx = S.oxShots;
    S.baseHits = S.hits; S.baseReloads = S.reloads; S.baseSwitches = S.switches;
    S.baseInd = S.indUses; S.baseSmoke = S.smokeUses; S.baseSkill = S.skillUses;
    S.baseSup = S.supply;
  }

  function isDone(i) { return doneList.indexOf(i) >= 0; }

  function completeStep() {
    if (isDone(step)) return;
    doneList.push(step);
    transT = TRANS_SECONDS;
    flashT = Math.max(flashT, TRANS_SECONDS);
    sfxSafe("reload", 0.7);
  }

  function advanceStep() {
    if (step >= TOTAL - 1) return;
    step++;
    stepStartMs = nowMs();
    hinted = false;
    baseCounters();
  }

  /* ---------------- 主更新 ---------------- */
  function tick(dt, env) {
    var t = nowMs();
    var me = myPlayer(env);
    if (!me) { prev = null; prevQ = {}; flashT = Math.max(0, flashT - dt); return; }
    var o = observe(me);
    collectEvents(env, me, t);

    if (!prev) {
      prev = o;
      prevQ = enemySnapshot(env);
      flashT = Math.max(0, flashT - dt);
      return;
    }

    /* 1) 位移（忽略瞬移/复活的大跳） */
    var dx = o.x - prev.x, dy = o.y - prev.y;
    var d = Math.sqrt(dx * dx + dy * dy);
    if (d > 0 && d < 150 && o.al && prev.al) S.dist += d;

    /* 2) 切枪 */
    if (prev.w && o.w && o.w !== prev.w) S.switches++;

    /* 3) 换弹（快照里 rl>0 表示正在换弹） */
    if (o.rl > 0 && prev.rl <= 0) S.reloads++;

    /* 4) 开火：同一把试剂弹药数下降（切枪会换弹匣，必须同枪才计） */
    if (prev.w && o.w === prev.w && o.am < prev.am && o.rl <= 0 && prev.rl <= 0) {
      var drop = prev.am - o.am;
      if (drop > 0 && drop <= 20) {
        S.shots += drop;
        if (o.w === "shotgun") S.oxShots += drop;
      }
    }

    /* 5) 指示剂使用：次数减少，或冷却被重新拉起 */
    if (o.indn < prev.indn) S.indUses++;
    else if (o.indcd > prev.indcd + 0.5) S.indUses++;

    /* 6) 烟雾：冷却从 0 被拉起 */
    if (o.scd > prev.scd + 0.5) S.smokeUses++;

    /* 7) AOE 技能：冷却被拉起或进入 AOE 生效期 */
    if (o.acd > prev.acd + 0.5 || o.aoe > prev.aoe + 0.1) S.skillUses++;

    /* 8) 补给点：备弹增加（同枪、活着、没有重生跳变） */
    if (prev.w && o.w === prev.w && o.al && prev.al &&
        o.res > prev.res && (o.res - prev.res) < 500 && o.hp >= prev.hp - 0.5) {
      S.supply++;
    }

    /* 9) 兜底：敌人被明显打掉缓冲/血（只在上面两条直接证据都拿不到时起作用） */
    var qs = enemySnapshot(env);
    for (var pid in qs) {
      var pq = prevQ[pid];
      if (!pq) continue;
      if (pq.buf - qs[pid].buf >= 3 || pq.hp - qs[pid].hp >= 3) S.hits++;
    }
    prevQ = qs;
    prev = o;

    /* 10) 步骤推进 */
    if (flashT > 0) flashT = Math.max(0, flashT - dt);
    if (transT > 0) {
      transT -= dt;
      if (transT <= 0) { transT = 0; advanceStep(); }
      return;
    }
    var p = progress(env, t);
    if (p.ok && step < TOTAL - 1) {
      completeStep();
      return;
    }
    if (!hinted && step < TOTAL - 1 && !p.ok && (t - stepStartMs) > HINT_AFTER * 1000) {
      hinted = true;
      hintCount++;
    }
  }

  /* ---------------- 收尾：完成 / 跳过 ---------------- */
  function sendTutDone() {
    if (doneSent) return;
    var wsObj = null;
    try { if (typeof ws !== "undefined") wsObj = ws; } catch (e) { }
    try {
      if (wsObj && wsObj.readyState === 1 && typeof wsObj.send === "function") {
        wsObj.send(JSON.stringify({ t: "tut_done" }));
        doneSent = true;
        pendingDone = false;
        return;
      }
    } catch (e) { }
    pendingDone = true;    // ws 还没连上：每帧重试，直到发出去（只发一次）
  }

  function finish() {
    if (!isDone(TOTAL - 1)) doneList.push(TOTAL - 1);
    step = TOTAL;
    active = false;
    transT = 0;
    sendTutDone();
    sync();
  }

  function skip() {
    // 跳过与通关走同一收尾（都标记完成，服务端按 IP 记教程已过）
    finish();
  }

  function begin() {
    active = true;
    step = 0;
    doneList = [];
    hintCount = 0;
    transT = 0;
    flashT = 0;
    hinted = false;
    doneSent = false;
    pendingDone = false;
    prev = null;
    prevQ = {};
    seenDmg = [];
    seenFeed = {};
    stepStartMs = nowMs();
    S.dist = S.shots = S.oxShots = S.hits = 0;
    S.reloads = S.switches = S.indUses = S.smokeUses = S.skillUses = S.supply = 0;
    baseCounters();
    layoutCache = { key: "", lay: null };
    installInput();
    // 顶部提示条也提示一下（服务端 tut 标记期间玩家免疫伤害）
    try {
      if (root) {
        root.hintMsg = "新手教程开始：按右侧提示完成 8 个互动任务（本局你不会掉血）";
        root.hintT = nowMs() + 4200;
      }
    } catch (e) { }
    sfxSafe("spawn", 0.6);
    sync();
  }

  function state() {
    return { active: active, step: step, done: doneList.slice(), hints: hintCount };
  }

  /* ---------------- 布局 ----------------
   * PC：右侧留白面板（HUD 逻辑空间 = 1366×768 基准，按 UIS 缩放）；
   * 手机：mobile_layout 控件之间的底部横向窄条（CSS 像素）。
   */
  function hudScale(vw, vh) {
    return Math.max(1, Math.min(2.4, Math.min(vw / 1366, vh / 768)));
  }

  function charW(ch, fs) {
    var c = ch.charCodeAt(0);
    if (c > 0x2e80) return fs * 1.08;          // 中文/全角：留 8% 余量，避免真实字体比估算宽而溢出
    if (ch === " ") return fs * 0.34;
    if (c >= 48 && c <= 57) return fs * 0.64;
    if (c >= 65 && c <= 90) return fs * 0.72;
    if (c >= 97 && c <= 122) return fs * 0.60;
    return fs * 0.58;                          // 其它 ASCII 标点/符号
  }
  var NO_LINE_START = "，。、；：！？）】」』％%,.;:!?)]}";      // 不能出现在行首（行首禁则）
  var NO_LINE_END = "（【「『《“‘([{";                     // 不能出现在行尾（行尾禁则）
  function wrapText(text, maxW, fs) {
    var lines = [], cur = "", w = 0, i, ch, cw, last;
    var pendingBreak = false;          // 本行已判定要断，但先把连续的后置标点吃进来
    for (i = 0; i < text.length; i++) {
      ch = text.charAt(i);
      if (ch === "\n") { lines.push(cur); cur = ""; w = 0; pendingBreak = false; continue; }
      cw = charW(ch, fs);
      if (pendingBreak) {
        if (NO_LINE_START.indexOf(ch) >= 0) { cur += ch; continue; }  // 标点跟着上一行走
        lines.push(cur); cur = ""; w = 0; pendingBreak = false;
      }
      if (w + cw > maxW && cur.length) {
        if (NO_LINE_START.indexOf(ch) >= 0) {        // 行首禁则：标点挤在本行
          cur += ch; pendingBreak = true; continue;
        }
        last = cur.charAt(cur.length - 1);
        if (NO_LINE_END.indexOf(last) >= 0 && cur.length > 1) {
          // 行尾禁则：把左括号挪到下一行行首
          cur = cur.slice(0, cur.length - 1);
          lines.push(cur);
          cur = last + ch; w = charW(last, fs) + cw;
          continue;
        }
        lines.push(cur); cur = ch; w = cw;
      } else {
        cur += ch; w += cw;
      }
    }
    if (cur.length) lines.push(cur);
    return lines;
  }

  function maxLines(field, maxW, fs) {
    var m = 1;
    for (var i = 0; i < STEPS.length; i++) {
      var txt = (field === "cond") ? condFull(i) : STEPS[i][field];
      var n = wrapText(txt, maxW, fs).length;
      if (n > m) m = n;
    }
    return m;
  }

  /* 完成条件的“最长形态”（含实时进度占位），用于给条件区预留足够高度，避免压到按钮 */
  var COND_WORST = [
    "位移 300 / 300", "开火 3 / 3", "换弹 ✓ · 切枪 ✓", "命中 1 / 1",
    "氧化通道开火 1 / 1", "指示剂 ✓ · 补给 ✓", "烟雾 ✓ · AOE ✓ · 指示剂 ✓", ""
  ];
  function condFull(i) {
    var s = STEPS[Math.min(i, STEPS.length - 1)];
    return i >= STEPS.length - 1 ? s.cond : (s.cond + "（" + COND_WORST[i] + "）");
  }

  function pcLayout(vw, vh) {
    var uis = hudScale(vw, vh);
    var uw = vw / uis, uh = vh / uis;
    var pad = 16, headerH = 32, gap = 10, listRowH = 21, listRows = 4;
    var w = round(Math.min(Math.max(uw * 0.32, 380), Math.min(uw * 0.42, 560)));
    if (w > uw - 32) w = Math.max(220, round(uw - 32));
    var inner = w - pad * 2;
    // 放大字号、拉大行距：原来 12.5px/17px 在机房屏幕上又密又累
    var bodyFs = 14, bodyLh = 23;
    var condFs = 12, condLh = 16;
    var wrapW = inner - 6;                 // 留右侧余量，避免真实字体比估算宽时越界
    var bodyLines = maxLines("body", wrapW, bodyFs);
    var condLines = maxLines("cond", wrapW, condFs);
    var listH = listRows * listRowH;
    var bodyH = bodyLines * bodyLh;
    var condH = condLines * condLh + 18;      // 给“完成条件”标签 + 进度条留足高度，别顶到按钮
    var footerH = 30;
    var h = pad + headerH + gap + listH + gap + bodyH + 6 + condH + gap + footerH + pad;
    if (h > uh - 16) h = Math.max(160, round(uh - 16));

    var x = round(uw - 20 - w);
    if (x < 8) x = 8;
    var yCandidate = round(uw < 900 ? 104 : uh * 0.30);
    var rackTop = uh - 112;                       // 试剂架标题上方
    var yMax = Math.min(uh - 12 - h, rackTop - h);
    var y = round(Math.max(12, Math.min(yCandidate, yMax)));

    var header = { x: x + pad, y: y + pad, w: inner, h: headerH };
    var skipBtn = { x: x + w - pad - 56, y: y + pad + 4, w: 56, h: 20 };
    var list = { x: x + pad, y: header.y + headerH + gap, w: inner, h: listH,
                 colW: (inner - 10) / 2, rowH: listRowH };
    var body = { x: x + pad, y: list.y + listH + gap, w: inner, h: bodyH };
    var cond = { x: x + pad, y: body.y + bodyH + 6, w: inner, h: condH };
    var footer = { x: x + pad, y: cond.y + condH + gap, w: inner, h: footerH };
    var finishBtn = { x: footer.x, y: footer.y, w: inner, h: 28 };

    return {
      mode: "pc", mobile: false, step: step, UIS: uis, UW: uw, UH: uh,
      x: x, y: y, w: w, h: h,
      css: { x: x * uis, y: y * uis, w: w * uis, h: h * uis },
      pad: pad, header: header, skip: skipBtn, list: list, body: body,
      cond: cond, footer: footer, finishBtn: finishBtn,
      bodyLines: bodyLines, condLines: condLines
    };
  }

  function mobileLayout(vw, vh) {
    var lay = null;
    try {
      if (typeof ML !== "undefined" && ML && typeof ML.compute === "function") {
        lay = ML.compute(vw, vh, {
          host: !!(root && root._isHost),
          sniper: false
        });
      }
    } catch (e) { lay = null; }

    var chipsY = lay ? lay.chips.y0 : vh - 60;
    var statusB = lay ? (lay.status.y + lay.status.h) : 70;
    var h = round(clamp(vh * 0.078, 22, 30));
    var y = round(chipsY - 6 - h);
    var x0 = 8, x1 = vw - 8, i, el, ovY, leftSide, nx;

    var els = (lay && typeof ML.elements === "function") ? ML.elements(lay) : [];
    for (i = 0; i < els.length; i++) {
      el = els[i];
      if (el.cx !== undefined) ovY = (y < el.cy + el.r + 4) && (y + h > el.cy - el.r - 4);
      else ovY = (y < el.y + el.h + 4) && (y + h > el.y - 4);
      if (!ovY) continue;
      leftSide = (el.cx !== undefined) ? (el.cx < vw / 2) : ((el.x + el.w / 2) < vw / 2);
      if (leftSide) {
        nx = (el.cx !== undefined ? el.cx + el.r : el.x + el.w) + 8;
        if (nx > x0) x0 = nx;
      } else {
        nx = (el.cx !== undefined ? el.cx - el.r : el.x) - 8;
        if (nx < x1) x1 = nx;
      }
    }
    // 太窄就退到状态条下方的顶部条（宽度优先），仍然避开可点控件
    if (x1 - x0 < Math.max(240, vw * 0.42)) {
      y = round(statusB + 8);
      x0 = 8; x1 = vw - 8;
      for (i = 0; i < els.length; i++) {
        el = els[i];
        if (el.cx !== undefined) ovY = (y < el.cy + el.r + 4) && (y + h > el.cy - el.r - 4);
        else ovY = (y < el.y + el.h + 4) && (y + h > el.y - 4);
        if (!ovY) continue;
        leftSide = (el.cx !== undefined) ? (el.cx < vw / 2) : ((el.x + el.w / 2) < vw / 2);
        if (leftSide) {
          nx = (el.cx !== undefined ? el.cx + el.r : el.x + el.w) + 8;
          if (nx > x0) x0 = nx;
        } else {
          nx = (el.cx !== undefined ? el.cx - el.r : el.x) - 8;
          if (nx < x1) x1 = nx;
        }
      }
    }

    var w = Math.max(200, round(x1 - x0));
    w = Math.min(w, vw - 12);
    var x = round(clamp(x0, 6, Math.max(6, vw - 6 - w)));
    var btnW = (step === 7) ? 86 : 52;
    var btn = { x: x + w - btnW - 4, y: y + 3, w: btnW, h: h - 6 };
    return {
      mode: "mobile", mobile: true, step: step, UIS: 1, UW: vw, UH: vh,
      x: x, y: y, w: w, h: h,
      css: { x: x, y: y, w: w, h: h },
      pad: 8, btn: btn, skip: btn, finishBtn: { x: btn.x, y: btn.y, w: btn.w, h: btn.h },
      strip: { x: x, y: y, w: w, h: h }
    };
  }

  function currentLayout(env) {
    env = env || readEnv();
    var vw = env.VW || 1280, vh = env.VH || 800;
    var mobile = !!(env && env.mobile);
    var key = Math.round(vw) + "x" + Math.round(vh) + ":" + step + ":" + (mobile ? "m" : "p");
    if (layoutCache.lay && layoutCache.key === key) return layoutCache.lay;
    var lay = mobile ? mobileLayout(vw, vh) : pcLayout(vw, vh);
    layoutCache = { key: key, lay: lay };
    return lay;
  }

  function layout(vw, vh, opts) {
    opts = opts || {};
    var savedStep = step;
    if (typeof opts.step === "number") step = clamp(Math.round(opts.step), 0, TOTAL - 1);
    var lay = opts.mobile ? mobileLayout(vw, vh) : pcLayout(vw, vh);
    step = savedStep;
    return lay;
  }

  /* ---------------- 点击 ---------------- */
  function inRect(r, x, y) {
    return !!r && x >= r.x && x <= r.x + r.w && y >= r.y && y <= r.y + r.h;
  }

  function click(cssX, cssY) {
    if (!active) return false;
    var env = readEnv();
    var lay = currentLayout(env);
    if (!lay) return false;
    if (lay.mobile) {
      if (inRect(lay.finishBtn, cssX, cssY)) {
        if (step === TOTAL - 1) finish(); else skip();
        return true;
      }
      if (inRect(lay.strip, cssX, cssY)) {
        if (step === TOTAL - 1) finish();   // 最后一步整条可点，手机上更好按
        return true;
      }
      return false;
    }
    var uis = lay.UIS || 1;
    var hx = cssX / uis, hy = cssY / uis;
    if (inRect(lay.skip, hx, hy)) { skip(); return true; }
    if (step === TOTAL - 1 && inRect(lay.finishBtn, hx, hy)) { finish(); return true; }
    if (inRect(lay, hx, hy)) return true;
    return false;
  }

  /* 面板点击拦截：捕获阶段先于 game.js 的 window mousedown / mobile.js 的 cv touchstart */
  function installInput() {
    if (installed) return;
    if (typeof addEventListener !== "function") return;
    installed = true;
    try {
      addEventListener("mousedown", function (ev) {
        if (!active || !ev) return;
        var tg = ev.target;
        if (tg && (tg.id === "chatInput" || tg.id === "nameInput")) return;
        if (ev.button !== undefined && ev.button !== 0) return;
        if (click(ev.clientX, ev.clientY)) {
          if (ev.preventDefault) ev.preventDefault();
          if (ev.stopPropagation) ev.stopPropagation();
        }
      }, true);
    } catch (e) { }
    try {
      addEventListener("touchstart", function (ev) {
        if (!active || !ev || !ev.changedTouches || !ev.changedTouches.length) return;
        var t = ev.changedTouches[0];
        if (click(t.clientX, t.clientY)) {
          if (ev.preventDefault) ev.preventDefault();
          if (ev.stopPropagation) ev.stopPropagation();
        }
      }, { capture: true, passive: false });
    } catch (e) {
      try {
        addEventListener("touchstart", function (ev) {
          if (!active || !ev || !ev.changedTouches || !ev.changedTouches.length) return;
          var t2 = ev.changedTouches[0];
          if (click(t2.clientX, t2.clientY)) {
            if (ev.preventDefault) ev.preventDefault();
            if (ev.stopPropagation) ev.stopPropagation();
          }
        }, true);
      } catch (e2) { }
    }
  }

  /* ---------------- 绘制 ---------------- */
  var P = {
    panel: "rgba(11,16,22,0.82)", panel2: "rgba(15,21,28,0.94)",
    line: "rgba(255,255,255,0.10)", lineHot: "rgba(255,255,255,0.22)",
    ink: "#e7eef6", dim: "#8291a1", faint: "#4c5a68",
    acid: "#ff6b35", base: "#39c7d8", neutral: "#7dffb2", warn: "#ffc857",
    ox: "#b07cff", danger: "#ff4d5e",
    cjk: "\"Source Han Sans SC\",\"Noto Sans CJK SC\",\"Microsoft YaHei\",\"PingFang SC\",sans-serif",
    mono: "\"JetBrains Mono\",\"Cascadia Mono\",\"Consolas\",\"SFMono-Regular\",monospace"
  };
  try {
    if (typeof UI !== "undefined" && UI) {
      for (var pk in UI) { if (Object.prototype.hasOwnProperty.call(UI, pk)) P[pk] = UI[pk]; }
    }
  } catch (e) { }

  function font(px, w) {
    try { if (typeof uiFont === "function") return uiFont(px, w); } catch (e) { }
    return (w || 400) + " " + px + "px " + P.cjk;
  }
  function mono(px, w) {
    try { if (typeof uiMono === "function") return uiMono(px, w); } catch (e) { }
    return (w || 400) + " " + px + "px " + P.mono;
  }
  function rr(c, x, y, w, h, r) {
    r = Math.min(r, w / 2, h / 2);
    c.beginPath();
    c.moveTo(x + r, y);
    c.arcTo(x + w, y, x + w, y + h, r);
    c.arcTo(x + w, y + h, x, y + h, r);
    c.arcTo(x, y + h, x, y, r);
    c.arcTo(x, y, x + w, y, r);
    c.closePath();
  }
  function panel(c, x, y, w, h, r, opts) {
    try { if (typeof uiPanel === "function") { uiPanel(x, y, w, h, r, opts); return; } } catch (e) { }
    c.fillStyle = (opts && opts.fill) || P.panel;
    rr(c, x, y, w, h, r || 10); c.fill();
    if (!opts || opts.stroke !== false) {
      c.strokeStyle = (opts && opts.strokeColor) || P.line;
      c.lineWidth = 1;
      rr(c, x, y, w, h, r || 10); c.stroke();
    }
  }
  function corners(c, x, y, w, h, len, color) {
    try { if (typeof uiCorners === "function") { uiCorners(x, y, w, h, len, color); return; } } catch (e) { }
    c.strokeStyle = color || P.lineHot; c.lineWidth = 1.2;
    len = len || 10;
    c.beginPath();
    c.moveTo(x, y + len); c.lineTo(x, y); c.lineTo(x + len, y);
    c.moveTo(x + w - len, y); c.lineTo(x + w, y); c.lineTo(x + w, y + len);
    c.moveTo(x + w, y + h - len); c.lineTo(x + w, y + h); c.lineTo(x + w - len, y + h);
    c.moveTo(x + len, y + h); c.lineTo(x, y + h); c.lineTo(x, y + h - len);
    c.stroke();
  }
  function bar(c, x, y, w, h, frac, color) {
    try { if (typeof uiBar === "function") { uiBar(x, y, w, h, frac, color); return; } } catch (e) { }
    c.fillStyle = "rgba(255,255,255,0.08)"; rr(c, x, y, w, h, h / 2); c.fill();
    if (frac > 0) {
      c.fillStyle = color; rr(c, x, y, Math.max(1, w * clamp(frac, 0, 1)), h, h / 2); c.fill();
    }
  }

  function draw(env) {
    env = env || readEnv();
    if (!active || !env.joined || !env.ctx) return;
    if (env.modal) return;                    // 战绩板 / 手册 / 死亡界面打开时不抢屏
    if (env.mobile) drawMobile(env); else drawPC(env);
  }

  function drawPC(env) {
    var c = env.ctx;
    var lay = currentLayout(env);
    var s = Math.min(step, TOTAL - 1);
    var stp = STEPS[s];
    var acc = ACCENT[s];
    var z = lay.UIS;
    var p = progress(env, nowMs());

    c.save();
    c.setTransform(env.DPR * z, 0, 0, env.DPR * z, 0, 0);
    c.textBaseline = "alphabetic";
    c.textAlign = "left";

    panel(c, lay.x, lay.y, lay.w, lay.h, 12,
          { fill: P.panel2, strokeColor: "rgba(125,255,178,0.30)" });
    corners(c, lay.x, lay.y, lay.w, lay.h, 13, "rgba(125,255,178,0.45)");
    // 顶部步骤色条
    c.fillStyle = acc;
    rr(c, lay.x + 1, lay.y + 1, lay.w - 2, 3, 2); c.fill();

    // ---- 标题 ----
    var hd = lay.header;
    c.font = mono(8.5, 600); c.fillStyle = P.faint;
    c.fillText("TUTORIAL // 互动教学", hd.x, hd.y + 9);
    c.font = font(15, 800); c.fillStyle = P.ink;
    c.fillText("新手训练 " + (s + 1) + "/" + TOTAL, hd.x, hd.y + 26);
    // 跳过按钮
    var sk = lay.skip;
    panel(c, sk.x, sk.y, sk.w, sk.h, 4, { fill: "rgba(255,255,255,0.06)",
                                          strokeColor: "rgba(255,255,255,0.16)" });
    c.font = font(10, 600); c.fillStyle = P.dim; c.textAlign = "center";
    c.fillText("跳过教程", sk.x + sk.w / 2, sk.y + 14);
    c.textAlign = "left";

    c.strokeStyle = P.line; c.lineWidth = 1;
    c.beginPath(); c.moveTo(hd.x, hd.y + hd.h + 1); c.lineTo(hd.x + hd.w, hd.y + hd.h + 1); c.stroke();

    // ---- 步骤列表（2 列 × 4 行） ----
    var list = lay.list, i, col, row, cx, cy;
    for (i = 0; i < TOTAL; i++) {
      col = i % 2; row = (i - col) / 2;
      cx = list.x + col * (list.colW + 10);
      cy = list.y + row * list.rowH;
      var isCur = (i === s), isDoneI = isDone(i);
      if (isCur) {
        c.fillStyle = "rgba(125,255,178,0.10)";
        rr(c, cx - 3, cy - 1, list.colW, list.rowH - 2, 4); c.fill();
        c.fillStyle = acc;
        rr(c, cx - 3, cy - 1, 2, list.rowH - 2, 1); c.fill();
      }
      c.font = mono(10, isCur ? 700 : 500);
      c.fillStyle = isDoneI ? P.neutral : (isCur ? P.ink : P.faint);
      var mark = isDoneI ? "✓ " : (isCur ? "▶ " : "· ");
      var label = mark + (i + 1) + " " + STEPS[i].title;
      if (isCur) { c.fillStyle = P.ink; }
      c.fillText(label, cx + 4, cy + 13);
    }

    // ---- 正文 ----
    var bd = lay.body;
    c.font = font(12.5, 500);
    var lines = wrapText(stp.body, bd.w - 6, 12.5);
    for (i = 0; i < lines.length; i++) {
      c.fillStyle = "rgba(231,238,246,0.90)";
      c.fillText(lines[i], bd.x, bd.y + 12 + i * 17);
    }

    // ---- 完成条件 + 实时进度 ----
    var cd = lay.cond;
    c.font = mono(8.5, 700); c.fillStyle = acc;
    c.fillText("完成条件 // CONDITION", cd.x, cd.y + 9);
    // 最后一步的条件就是按钮本身，不再追加“（…）”，保持一行
    var ctxt = (s === TOTAL - 1) ? stp.cond
      : (stp.cond + "（" + p.txt + "）");
    var clines = wrapText(ctxt, cd.w - 6, 11);
    c.font = font(11, 500);
    for (i = 0; i < clines.length; i++) {
      c.fillStyle = "rgba(231,238,246,0.86)";
      c.fillText(clines[i], cd.x, cd.y + 23 + i * 14);
    }
    // 进度条
    var barY = cd.y + cd.h - 3;
    bar(c, cd.x, barY, cd.w, 3, p.frac, acc);

    // ---- 底部：自动继续提示 / 最后一步按钮 ----
    var ft = lay.footer;
    if (s === TOTAL - 1) {
      var fb = lay.finishBtn;
      var pulse = (Math.sin(nowMs() / 240) + 1) / 2;
      c.fillStyle = "rgba(125,255,178," + (0.16 + pulse * 0.10).toFixed(2) + ")";
      rr(c, fb.x, fb.y, fb.w, fb.h, 6); c.fill();
      c.strokeStyle = P.neutral; c.lineWidth = 1.2;
      rr(c, fb.x, fb.y, fb.w, fb.h, 6); c.stroke();
      c.font = font(12.5, 700); c.fillStyle = P.neutral; c.textAlign = "center";
      c.fillText("我知道了，开始对战", fb.x + fb.w / 2, fb.y + 19);
      c.textAlign = "left";
    } else {
      c.font = font(10, 500); c.fillStyle = P.faint;
      c.fillText(hinted ? "提示：" + nudgeFor(s, env) : "完成当前步骤后自动进入下一步", ft.x, ft.y + 12);
      c.font = mono(10, 600); c.fillStyle = P.dim; c.textAlign = "right";
      c.fillText((s + 1) + " / " + TOTAL, ft.x + ft.w, ft.y + 12);
      c.textAlign = "left";
      bar(c, ft.x, ft.y + ft.h - 5, ft.w, 3, (s + 1) / TOTAL, "rgba(125,255,178,0.55)");
    }

    // ---- 完成反馈 ----
    if (flashT > 0) {
      var a = clamp(flashT / TRANS_SECONDS, 0, 1);
      c.globalAlpha = a;
      c.fillStyle = "rgba(125,255,178,0.10)";
      rr(c, lay.x + 2, lay.y + 2, lay.w - 4, lay.h - 4, 11); c.fill();
      c.strokeStyle = P.neutral; c.lineWidth = 2;
      rr(c, lay.x + 2, lay.y + 2, lay.w - 4, lay.h - 4, 11); c.stroke();
      c.font = font(16, 800); c.fillStyle = P.neutral; c.textAlign = "center";
      c.fillText("✓ 完成：" + STEPS[s].title, lay.x + lay.w / 2, lay.y + lay.h / 2);
      c.textAlign = "left";
      c.globalAlpha = 1;
    }

    c.restore();
  }

  function drawMobile(env) {
    var c = env.ctx;
    var lay = currentLayout(env);
    var s = Math.min(step, TOTAL - 1);
    var stp = STEPS[s];
    var acc = ACCENT[s];
    var p = progress(env, nowMs());
    var D = Math.max(1, env.DPR || 1);

    c.save();
    c.setTransform(D, 0, 0, D, 0, 0);
    c.textBaseline = "middle";
    c.textAlign = "left";

    panel(c, lay.x, lay.y, lay.w, lay.h, 7,
          { fill: "rgba(8,12,17,0.90)", strokeColor: "rgba(125,255,178,0.38)" });
    // 左侧步骤色条
    c.fillStyle = acc;
    rr(c, lay.x + 1, lay.y + 2, 3, lay.h - 4, 2); c.fill();

    // 单行文案：训练 3/8 标题 · 进度
    var btn = lay.btn;
    var textX = lay.x + 9;
    var textW = (btn ? btn.x : lay.x + lay.w) - textX - 6;
    var line = "训练 " + (s + 1) + "/" + TOTAL + " " + stp.title + " · " + shortProgress(s, p);
    var fs = lay.h >= 27 ? 10.5 : 10;
    c.font = font(fs, 600);
    while (line.length > 4 && measure(c, line) > textW) line = line.slice(0, line.length - 2) + "…";
    c.fillStyle = P.ink;
    c.fillText(line, textX, lay.y + lay.h / 2 - 2);

    // 底部细进度
    bar(c, lay.x + 6, lay.y + lay.h - 4, lay.w - 12, 2, p.frac, acc);

    // 右侧按钮
    if (btn) {
      var last = (s === TOTAL - 1);
      c.fillStyle = last ? "rgba(125,255,178,0.22)" : "rgba(255,255,255,0.08)";
      rr(c, btn.x, btn.y, btn.w, btn.h, 4); c.fill();
      c.strokeStyle = last ? P.neutral : "rgba(255,255,255,0.20)";
      c.lineWidth = 1; rr(c, btn.x, btn.y, btn.w, btn.h, 4); c.stroke();
      c.font = font(10.5, 700);
      c.fillStyle = last ? P.neutral : P.dim;
      c.textAlign = "center";
      c.fillText(last ? "开始对战" : "跳过", btn.x + btn.w / 2, btn.y + btn.h / 2);
      c.textAlign = "left";
    }

    if (flashT > 0) {
      c.globalAlpha = clamp(flashT / TRANS_SECONDS, 0, 1);
      c.strokeStyle = P.neutral; c.lineWidth = 2;
      rr(c, lay.x + 1, lay.y + 1, lay.w - 2, lay.h - 2, 6); c.stroke();
      c.font = font(11, 800); c.fillStyle = P.neutral; c.textAlign = "center";
      c.fillText("✓ 完成", lay.x + lay.w / 2, lay.y + lay.h / 2);
      c.textAlign = "left";
      c.globalAlpha = 1;
    }
    c.restore();
  }

  function measure(c, txt) {
    try {
      if (c.measureText) return c.measureText(txt).width || 0;
    } catch (e) { }
    var w = 0;
    for (var i = 0; i < txt.length; i++) w += charW(txt.charAt(i), 10.5);
    return w;
  }

  function nudgeFor(s, env) {
    var me = env ? myPlayer(env) : null;
    var wid = me ? (me.w || "") : "";
    var am = me ? num(me.am) : 0;
    var res = me ? num(me.res) : 0;
    switch (s) {
      case 0: return "按住 W/A/S/D 走一段路，位移会实时累计。";
      case 1:
        if (wid === "knife") return "近战没有弹药计数，先按 3（或滚轮）切到有弹的试剂再按住左键。";
        if (am <= 0 && res > 0) return "弹匣空了，先按 " + KEYS.reload + " 换弹再开火。";
        return "按住鼠标左键朝空地开火 3 发。";
      case 2:
        if (am <= 0 && res <= 0) return "这瓶试剂没备弹了，站到地图上的 + 补给点补满，再按 R 换弹。";
        return "先打几发再按 R 换弹，然后按 2 或滚轮切一次试剂。";
      case 3: return "把准星对准敌人开火；命中后对方头顶会跳出 pH 变化。";
      case 4:
        if (wid === "shotgun") return "已经是喷淋器了，朝敌人方向开 1 枪即可。";
        return "按 5 切到喷淋器（H₂O₂，紫色氧化通道）后开火。";
      case 5: return "按 " + KEYS.indicator + " 用一次指示剂，或站到地图上的 + 补给点。";
      case 6: return "投个烟雾（" + KEYS.smoke + "）、放个 AOE（" + KEYS.aoe + "），或用 " + KEYS.indicator + " 标记敌人。";
      default: return "点下方按钮结束教程。";
    }
  }

  /* ---------------- 更新入口 ---------------- */
  function update(dt) {
    // 断线时把 tut_done 补发出去（仍只发一次）
    if (pendingDone && !doneSent) sendTutDone();
    var env = readEnv();
    if (!active) { sync(); return; }
    if (!(typeof dt === "number" && isFinite(dt))) dt = 1 / 60;
    dt = clamp(dt, 0, 0.5);
    if (env.joined) tick(dt, env);
    draw(env);
    sync();
  }

  function sync() {
    api.active = active;
    api.step = step;
  }

  var api = {
    active: false,
    step: 0,
    total: TOTAL,
    begin: begin,
    update: update,
    draw: function () { draw(readEnv()); },
    skip: skip,
    finish: finish,
    state: state,
    click: click,
    layout: layout,
    steps: function () {
      var out = [], i;
      for (i = 0; i < STEPS.length; i++) {
        out.push({ id: STEPS[i].id, title: STEPS[i].title,
                   body: STEPS[i].body, cond: STEPS[i].cond });
      }
      return out;
    },
    KEYS: KEYS
  };

  root.TUT = api;
  if (typeof module === "object" && module && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window
   : (typeof globalThis !== "undefined" ? globalThis : this));
