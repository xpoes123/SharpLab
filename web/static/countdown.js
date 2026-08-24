// SharpLab HQ — Countdown (Numbers Round). Build an arithmetic expression from a
// subset of six numbers to hit the target for coins. Rounds POST to
// /api/v1/arcade/countdown/* (session-cookie auth); the /solve response carries the
// authoritative new balance on an exact hit, which we push into the nav chip + header.
//
// Also has a 120-second SPRINT mode (mirrors sequence.js / zetamac.js): /sprint/start hands
// back a batch of rounds (numbers + target, no signed token needed), one round shown at a
// time with a single expression input that commits the typed expression and advances on
// Enter (empty = skip), a countdown + live solved counter, and on timeout a /sprint/submit +
// a highest-correct-wins leaderboard. Countdown solves are slow, so 30 rounds is plenty.

const app = document.getElementById("app");
const navRight = document.getElementById("navRight");

// ── Helpers (copied verbatim from casino.js / pokemon.js) ──
const num = (n) => (n == null ? "—" : Number(n).toLocaleString());
const coins = (n) => "🪙 " + num(Math.round(n || 0));
const esc = (s) =>
  String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function getJSON(url) {
  if (MOCK) return mockJSON(url);
  const r = await fetch(url, { credentials: "include" });
  if (!r.ok) return { _status: r.status };
  return r.json();
}

// ── Nav (login / logout) — mirrors casino.js (reads state.balance) ──
function renderNav(user) {
  if (!user) {
    navRight.innerHTML = `<a class="btn" href="/api/v1/auth/discord/login">Sign in with Discord</a>`;
    return;
  }
  const av = user.avatar ? `https://cdn.discordapp.com/avatars/${user.id}/${user.avatar}.png` : null;
  navRight.innerHTML = `<div class="userbar">
    ${av ? `<img class="avatar" src="${av}" alt="">` : `<div class="avatar"></div>`}
    <span>${esc(user.username)}</span>
    <span class="coinschip" title="Casino coins">🪙 ${num(state.balance)}</span>
    <a class="btn ghost" href="/api/v1/auth/logout">Sign out</a></div>`;
}

// ── State ──
const state = { me: null, balance: 0, solved: 0, mode: "practice" };
const round = { token: null, numbers: [], target: null, done: false, busy: false };

// ── Toast (copied verbatim from casino.js) ──
function toast(msg) {
  const t = document.createElement("div");
  t.className = "cardtoast";
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 3200);
}

// ── Coins hub (copied verbatim from casino.js) ──
const EARN_WAYS = [
  ["💬", "Chat in the server", "5 coins per message, up to 500 a day"],
  ["🎯", "Log a bet", "50 coins for logging a sports bet with /bet log"],
  ["📈", "Log a trade", "50 coins for recording a stock or option trade"],
  ["🏀", "Daily pick'em", "25 coins per pick — plus a coin payout when your pick wins"],
  ["🎁", "Free daily pack", "Open one free card pack every day — pure upside"],
  ["🃏", "Complete a set", "One-time coin bonus for owning every card in a set"],
  ["♻️", "Quick-sell dupes", "Sell duplicate cards back for coins in Discord"],
  ["🎮", "Win in the casino", "Win at /casino or /play — playing no longer hands out free coins"],
];
function showCoinsHub() {
  if (document.querySelector(".hubov")) return;
  const rows = EARN_WAYS.map(
    ([i, t, d]) => `<div class="hubrow"><div class="hubicon">${i}</div>
      <div><div class="hubt">${esc(t)}</div><div class="hubd">${esc(d)}</div></div></div>`
  ).join("");
  const ov = document.createElement("div");
  ov.className = "hubov";
  ov.innerHTML = `<div class="hubcard">
    <div class="hubhead"><h3>Ways to earn 🪙</h3><button class="hubx" aria-label="Close">✕</button></div>
    ${rows}
    <div class="hubfoot">Your balance: <b>🪙 ${num(state.balance)}</b></div>
  </div>`;
  ov.addEventListener("click", (e) => { if (e.target === ov || e.target.closest(".hubx")) ov.remove(); });
  document.body.appendChild(ov);
}
document.addEventListener("click", (e) => { if (e.target.closest(".coinschip")) showCoinsHub(); });

const REDUCE = matchMedia("(prefers-reduced-motion: reduce)").matches;

const $ = (id) => document.getElementById(id);

function applyBalance(bal) {
  if (bal == null) return;
  state.balance = bal;
  renderNav(state.me && state.me.user);
  const pb = document.getElementById("pageBal");
  if (pb) pb.textContent = coins(bal);
}

function setBusy(busy) {
  round.busy = busy;
  document.querySelectorAll(".cd-btns .btn, .cd-input").forEach((el) => (el.disabled = busy));
}

// ── POST to a countdown endpoint. Returns parsed JSON (with _status on error). ──
async function postCD(path, body) {
  if (MOCK) return mockCD(path, body);
  const r = await fetch("/api/v1/arcade/countdown" + path, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok && j.error == null) {
    j.error = r.status === 401 ? "sign in to play" : `error ${r.status}`;
  }
  return j;
}

// ── Confetti-ish burst (respects reduced-motion) ──
function celebrate() {
  if (REDUCE) return;
  const layer = document.createElement("div");
  layer.className = "cd-confetti";
  const colors = ["#7aa2f7", "#bb9af7", "#9ece6a", "#e0af68", "#f7768e"];
  for (let i = 0; i < 28; i++) {
    const b = document.createElement("i");
    b.style.left = Math.random() * 100 + "%";
    b.style.background = colors[i % colors.length];
    b.style.animationDelay = Math.random() * 0.2 + "s";
    b.style.transform = `rotate(${Math.random() * 360}deg)`;
    layer.appendChild(b);
  }
  document.body.appendChild(layer);
  setTimeout(() => layer.remove(), 1600);
}

function renderTiles() {
  return round.numbers
    .map((n) => `<button type="button" class="cd-tile${n >= 25 ? " large" : ""}" data-val="${n}">${n}</button>`)
    .join("");
}

// ── Start a new round ──
async function newRound() {
  round.done = false;
  setBusy(true);
  const res = await postCD("/new", {});
  if (res.error || res._status) {
    setBusy(false);
    return toast("❌ " + (res.error || "couldn't start a round"));
  }
  round.token = res.token;
  round.numbers = res.numbers || [];
  round.target = res.target;
  const tiles = $("cdTiles");
  const target = $("cdTarget");
  const input = $("cdInput");
  const result = $("cdResult");
  const btns = $("cdBtns");
  if (tiles) tiles.innerHTML = renderTiles();
  if (target) target.textContent = num(res.target);
  if (input) { input.value = ""; input.disabled = false; }
  if (result) { result.innerHTML = ""; result.className = "cd-result"; }
  if (btns) btns.innerHTML =
    `<button class="btn primary big" id="cdSubmit">Submit</button>
     <button class="btn ghost" id="cdGiveUp">Give up</button>`;
  setBusy(false);
  if (input) input.focus();
}

// ── Submit an expression ──
async function submitExpr() {
  if (round.busy || round.done) return;
  const input = $("cdInput");
  const expr = (input && input.value || "").trim();
  if (!expr) { if (input) input.focus(); return; }
  setBusy(true);
  const res = await postCD("/solve", { token: round.token, expression: expr });
  setBusy(false);
  const result = $("cdResult");
  if (res.error && res.valid === undefined && (res._status || res.error === "sign in to play")) {
    return toast("❌ " + res.error);
  }
  if (res.valid === false) {
    if (result) { result.className = "cd-result bad"; result.innerHTML = `⚠️ ${esc(res.error || "invalid expression")}`; }
    toast("❌ " + (res.error || "invalid expression"));
    if (input && !REDUCE) {
      input.classList.remove("shake"); void input.offsetWidth; input.classList.add("shake");
      input.addEventListener("animationend", () => input.classList.remove("shake"), { once: true });
    }
    return;
  }
  if (res.exact) {
    round.done = true;
    state.solved += 1;
    const sc = $("cdSolved");
    if (sc) sc.textContent = num(state.solved);
    if (res.balance != null) applyBalance(res.balance);
    const rw = res.reward ? ` <span class="cd-reward">+${num(res.reward)} 🪙</span>` : "";
    if (result) {
      result.className = "cd-result hit";
      result.innerHTML = `🎯 <b>${num(res.value)}</b> — exact!${rw}`;
    }
    celebrate();
    const btns = $("cdBtns");
    if (btns) btns.innerHTML = `<button class="btn primary big" id="cdNext">New numbers →</button>`;
    if (input) input.disabled = true;
    return;
  }
  // Valid but not the target.
  if (result) {
    result.className = "cd-result off";
    result.innerHTML = `= <b>${num(res.value)}</b> · off by <b>${num(res.delta)}</b>`;
  }
}

// ── Give up ──
async function giveUp() {
  if (round.busy || round.done) return;
  setBusy(true);
  const res = await postCD("/reveal", { token: round.token });
  setBusy(false);
  if (res.error || res._status) return toast("❌ " + (res.error || "something went wrong"));
  round.done = true;
  const result = $("cdResult");
  if (result) {
    result.className = "cd-result off";
    result.innerHTML = esc(res.note || "No solution shown.");
  }
  const btns = $("cdBtns");
  if (btns) btns.innerHTML = `<button class="btn primary big" id="cdNext">New numbers →</button>`;
  const input = $("cdInput");
  if (input) input.disabled = true;
}

// ═══════════════════════════════════════════════════════════════════════════
// Sprint mode — 120s batch run (modeled on zetamac.js / sequence.js)
// ═══════════════════════════════════════════════════════════════════════════

const DEFAULT_SPRINT_SECONDS = 120;
const HOT_THRESHOLD = 15; // seconds remaining before the bar/label turn "hot"
const sprint = {
  active: false, problems: [], token: null, idx: 0, answers: [], solved: 0,
  seconds: DEFAULT_SPRINT_SECONDS, endAt: 0, raf: 0,
};

// ── POST to a sprint endpoint ──
async function postSprint(path, body) {
  if (MOCK) return mockSprint(path, body);
  const r = await fetch("/api/v1/arcade/countdown/sprint" + path, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok && j.error == null) {
    j.error = r.status === 401 ? "sign in to play" : `error ${r.status}`;
  }
  return j;
}

async function startSprint() {
  if (sprint.active) return;
  state.mode = "sprint";
  const btn = $("cdSprintStart");
  if (btn) btn.disabled = true;
  const res = await postSprint("/start", {});
  if (res.error || res._status) {
    if (btn) btn.disabled = false;
    return toast("❌ " + (res.error || "couldn't start"));
  }
  sprint.active = true;
  sprint.problems = res.problems || [];
  sprint.token = res.token;
  sprint.seconds = res.duration || DEFAULT_SPRINT_SECONDS;
  sprint.idx = 0;
  sprint.answers = [];
  sprint.solved = 0;
  sprint.endAt = performance.now() + sprint.seconds * 1000;
  renderSprintPlaying();
  sprintTick();
  showSprintProblem();
}

// ── Countdown loop (rAF for the bar, interval-free — just paint each frame) ──
function sprintTick() {
  const remaining = Math.max(0, sprint.endAt - performance.now());
  const frac = remaining / (sprint.seconds * 1000);
  const hot = remaining <= HOT_THRESHOLD * 1000;
  const bar = $("cdBar");
  if (bar) { bar.style.transform = `scaleX(${frac})`; bar.classList.toggle("hot", hot); }
  const secs = $("cdSecs");
  if (secs) { secs.textContent = Math.ceil(remaining / 1000) + "s"; secs.classList.toggle("hot", hot); }
  if (remaining <= 0) { finishSprint(); return; }
  sprint.raf = requestAnimationFrame(sprintTick);
}

// ── Show the current round's numbers + target ──
function showSprintProblem() {
  const p = sprint.problems[sprint.idx];
  const tiles = $("cdSprintTiles");
  const target = $("cdSprintTarget");
  const input = $("cdSprintInput");
  if (!p) { finishSprint(); return; }
  if (tiles) tiles.innerHTML = (p.numbers || [])
    .map((n) => `<div class="cd-tile sprint-display${n >= 25 ? " large" : ""}">${n}</div>`).join("");
  if (target) target.textContent = num(p.target);
  if (input) { input.value = ""; input.focus(); }
}

// ── Commit the typed expression + advance (Enter only; empty = skip — the client
// never knows whether an expression is right, so there's no auto-advance here). ──
function advanceSprint() {
  if (!sprint.active) return;
  const input = $("cdSprintInput");
  const raw = (input && input.value || "").trim();
  sprint.answers[sprint.idx] = raw; // empty string = skipped, still valid per the API
  sprint.solved += 1;
  const sc = $("cdSprintSolved");
  if (sc) sc.textContent = num(sprint.solved);
  sprint.idx += 1;
  if (sprint.idx >= sprint.problems.length) { finishSprint(); return; }
  showSprintProblem();
}

// ── Finish + submit ──
async function finishSprint() {
  if (!sprint.active) return;
  sprint.active = false;
  if (sprint.raf) cancelAnimationFrame(sprint.raf);
  const res = await postSprint("/submit", { token: sprint.token, answers: sprint.answers });
  if (res.error || res._status) {
    renderSprintIntro();
    return toast("❌ " + (res.error || "couldn't submit run"));
  }
  applyBalance(res.balance);
  renderSprintResult(res);
  loadSprintLeaderboard();
}

// ── Sprint views ──
function renderSprintIntro() {
  const signedOut = !state.me
    ? `<div class="card" style="max-width:460px;margin:0 auto 16px;text-align:center">
        <p class="muted" style="margin:0 0 10px">Sign in with Discord to play for coins.</p>
        <a class="btn" href="/api/v1/auth/discord/login">Sign in with Discord</a></div>`
    : "";
  app.innerHTML = `
    ${modeToggle()}
    <div class="cd-head">
      <h1>🔢 Countdown Sprint <span class="balancechip" id="pageBal">${coins(state.balance)}</span></h1>
      <p>Solve as many numbers rounds as you can in 120 seconds. Type an expression and hit Enter to lock it in and move on.</p>
    </div>
    ${signedOut}
    <div class="cd-wrap">
      <div class="card cd-card">
        <div class="zmbig">⏱️ 120</div>
        <p class="muted">Empty + Enter skips a round. Ready?</p>
        <button class="btn primary big" id="cdSprintStart"${state.me ? "" : " disabled"}>Start (120s)</button>
      </div>
    </div>
    <div class="zmleaderboard" id="cdLeaderboard"></div>`;
  loadSprintLeaderboard();
}

function renderSprintPlaying() {
  app.innerHTML = `
    <div class="cd-head">
      <h1>🔢 Countdown Sprint <span class="balancechip" id="pageBal">${coins(state.balance)}</span></h1>
    </div>
    <div class="cd-wrap">
      <div class="card cd-card">
        <div class="zmtimebar"><div class="zmtimefill" id="cdBar"></div></div>
        <div class="zmmeta">
          <span class="zmsecs" id="cdSecs">${sprint.seconds}s</span>
          <span class="zmsolved">Solved: <b id="cdSprintSolved">0</b></span>
        </div>
        <div class="cd-targetbox"><span class="cd-tlabel">Target</span><span class="cd-target" id="cdSprintTarget">—</span></div>
        <div class="cd-tiles" id="cdSprintTiles"></div>
        <input class="cd-input" id="cdSprintInput" type="text" inputmode="text" autocomplete="off"
               autocapitalize="off" spellcheck="false" placeholder="e.g. (100 + 25) * 3 - 4" />
      </div>
    </div>`;
}

function renderSprintResult(res) {
  const newBest = res.is_new_best
    ? `<div class="zmnewbest${REDUCE ? "" : " pop"}">🏆 new best!</div>`
    : "";
  const bestLine = res.best != null
    ? `<div class="zmbest">Your best: <b>${num(res.best)}</b></div>` : "";
  const rankLine = res.rank != null
    ? `<div class="zmrank">#${num(res.rank)} on the board</div>` : "";
  app.innerHTML = `
    ${modeToggle()}
    <div class="cd-head">
      <h1>🔢 Countdown Sprint <span class="balancechip" id="pageBal">${coins(res.balance)}</span></h1>
    </div>
    <div class="cd-wrap">
      <div class="card cd-card zmresult">
        <div class="zmbig">${num(res.correct)} solved</div>
        <div class="zmreward${REDUCE ? "" : " pop"}">+${num(res.coins)} 🪙</div>
        <p class="muted">Balance: <b>${coins(res.balance)}</b></p>
        ${newBest}${bestLine}${rankLine}
        <button class="btn primary big" id="cdSprintStart">Play again</button>
      </div>
    </div>
    <div class="zmleaderboard" id="cdLeaderboard"></div>`;
  loadSprintLeaderboard();
}

// ── Sprint leaderboard ──
async function loadSprintLeaderboard() {
  const res = await getJSON("/api/v1/arcade/countdown/sprint/leaderboard");
  if (!res || res._status || !res.top) { renderSprintLeaderboard({ top: [] }); return; }
  renderSprintLeaderboard(res);
}

function renderSprintLeaderboard(res) {
  const sec = $("cdLeaderboard");
  if (!sec) return;
  const top = res.top || [];
  if (!top.length) {
    sec.innerHTML = `<div class="zmlbhead">🏁 Best of 120 seconds</div>
      <div class="zmlbempty">No runs yet — be the first!</div>`;
    return;
  }
  const rows = top.map((r) => {
    const runs = r.runs != null ? `<span class="zmlbruns">${num(r.runs)} run${r.runs === 1 ? "" : "s"}</span>` : "";
    return `<div class="zmlbrow${r.me ? " me" : ""}">
      <span class="zmlbrank">#${num(r.rank)}</span>
      <span class="zmlbname">${esc(r.name)}</span>
      ${runs}
      <span class="zmlbscore">${num(r.score)}</span>
    </div>`;
  }).join("");
  sec.innerHTML = `<div class="zmlbhead">🏁 Best of 120 seconds</div>
    <div class="zmlblist">${rows}</div>`;
}

// ── Mode toggle (practice ⇄ sprint) ──
function modeToggle() {
  return `<div class="cd-modetoggle">
    <button class="btn ${state.mode === "practice" ? "primary" : "ghost"}" id="cdModePractice">Practice</button>
    <button class="btn ${state.mode === "sprint" ? "primary" : "ghost"}" id="cdModeSprint">⏱️ 120s Sprint</button>
  </div>`;
}

// ── Delegated events ──
app.addEventListener("click", (e) => {
  const tile = e.target.closest(".cd-tile");
  if (tile && !tile.classList.contains("sprint-display")) {
    const input = $("cdInput");
    if (input && !input.disabled) {
      input.value += input.value && !/[\s(]$/.test(input.value) ? " " + tile.dataset.val : tile.dataset.val;
      input.focus();
    }
    return;
  }
  const op = e.target.closest(".cd-op");
  if (op) {
    const input = $("cdInput");
    if (input && !input.disabled) { input.value += op.dataset.op; input.focus(); }
    return;
  }
  if (e.target.closest("#cdSubmit")) return submitExpr();
  if (e.target.closest("#cdGiveUp")) return giveUp();
  if (e.target.closest("#cdNext")) return newRound();
  if (e.target.closest("#cdClear")) { const i = $("cdInput"); if (i && !i.disabled) { i.value = ""; i.focus(); } return; }
  if (e.target.closest("#cdSprintStart")) return startSprint();
  if (e.target.closest("#cdModePractice")) { state.mode = "practice"; return buildPage(); }
  if (e.target.closest("#cdModeSprint")) { state.mode = "sprint"; return renderSprintIntro(); }
});
app.addEventListener("keydown", (e) => {
  if (e.target.id === "cdInput" && e.key === "Enter") { e.preventDefault(); submitExpr(); }
  if (e.target.id === "cdSprintInput" && e.key === "Enter") { e.preventDefault(); advanceSprint(); }
});

function buildPage() {
  if (state.mode === "sprint") return renderSprintIntro();
  const signedOut = !state.me
    ? `<div class="card" style="max-width:520px;margin:0 auto 16px;text-align:center">
        <p class="muted" style="margin:0 0 10px">Sign in with Discord to play for coins.</p>
        <a class="btn" href="/api/v1/auth/discord/login">Sign in with Discord</a></div>`
    : "";
  const ops = ["+", "−", "×", "÷", "(", ")"].map((sym, i) => {
    const real = ["+", "-", "*", "/", "(", ")"][i];
    return `<button type="button" class="cd-op" data-op="${real}">${sym}</button>`;
  }).join("");
  app.innerHTML = `
    ${modeToggle()}
    <div class="cd-head">
      <h1>🔢 Countdown <span class="balancechip" id="pageBal">${coins(state.balance)}</span></h1>
      <p>Use any subset of the six numbers with + − × ÷ and parentheses to reach the target.
         Whole numbers only — no fractions. Nail it exactly for coins.</p>
    </div>
    ${signedOut}
    <div class="cd-wrap">
      <div class="card cd-card">
        <div class="cd-targetbox"><span class="cd-tlabel">Target</span><span class="cd-target" id="cdTarget">—</span></div>
        <div class="cd-tiles" id="cdTiles"></div>
        <div class="cd-oprow">${ops}<button type="button" class="cd-op danger" id="cdClear">clear</button></div>
        <input class="cd-input" id="cdInput" type="text" inputmode="text" autocomplete="off"
               autocapitalize="off" spellcheck="false" placeholder="e.g. (100 + 25) * 3 - 4" />
        <div class="cd-result" id="cdResult"></div>
        <div class="cd-btns" id="cdBtns">
          <button class="btn primary big" id="cdSubmit">Submit</button>
          <button class="btn ghost" id="cdGiveUp">Give up</button>
        </div>
        <div class="cd-counter">Solved this session: <b id="cdSolved">0</b></div>
      </div>
    </div>`;
  newRound();
}

async function main() {
  const me = await getJSON("/api/v1/hq/me");
  const loggedIn = me && me.authenticated;
  state.me = loggedIn ? me : null;
  state.balance = loggedIn ? (me.balance || 0) : 0;
  renderNav(loggedIn ? me.user : null);
  buildPage();
}

// ─────────────────────────────────────────────────────────────
// Mock mode (?mock=1): fake rounds + a LOCAL integer-only expression
// evaluator (same rules as the server) so the page works offline for
// screenshot testing. Real endpoints win by default.
// ─────────────────────────────────────────────────────────────
const MOCK = new URLSearchParams(location.search).get("mock") === "1";

function mockJSON(url) {
  if (url.startsWith("/api/v1/hq/me"))
    return { authenticated: true, user: { id: "1", username: "davidj", avatar: null }, balance: 12500 };
  if (url.includes("/sprint/leaderboard"))
    return { game: "countdown", duration: 120, top: [
      { rank: 1, name: "davidj", score: 9, runs: 4, me: true },
      { rank: 2, name: "steph", score: 7, runs: 2 },
      { rank: 3, name: "nova", score: 5, runs: 1 },
    ] };
  return {};
}

// Local integer-only evaluator (hand-written tokenizer + shunting-yard — NO eval).
function mockEval(expr, numbers) {
  const toks = [];
  let i = 0;
  while (i < expr.length) {
    const c = expr[i];
    if (/\s/.test(c)) { i++; continue; }
    if (/\d/.test(c)) { let j = i; while (j < expr.length && /\d/.test(expr[j])) j++; toks.push(parseInt(expr.slice(i, j), 10)); i = j; continue; }
    if ("+-*/()".includes(c)) { toks.push(c); i++; continue; }
    throw new Error("illegal character");
  }
  if (!toks.length) throw new Error("empty expression");
  const prec = { "+": 1, "-": 1, "*": 2, "/": 2 };
  const out = [], st = [];
  let prev = null;
  for (const t of toks) {
    if (typeof t === "number") {
      if (prev === "num" || prev === ")") throw new Error("missing operator");
      out.push(t); prev = "num";
    } else if ("+-*/".includes(t)) {
      if (prev === null || prev === "op" || prev === "(") throw new Error("misplaced operator");
      while (st.length && "+-*/".includes(st[st.length - 1]) && prec[st[st.length - 1]] >= prec[t]) out.push(st.pop());
      st.push(t); prev = "op";
    } else if (t === "(") {
      if (prev === "num" || prev === ")") throw new Error("missing operator");
      st.push(t); prev = "(";
    } else {
      if (prev !== "num" && prev !== ")") throw new Error("misplaced parenthesis");
      while (st.length && st[st.length - 1] !== "(") out.push(st.pop());
      if (!st.length) throw new Error("unbalanced parentheses");
      st.pop(); prev = ")";
    }
  }
  if (prev === "op" || prev === "(") throw new Error("incomplete expression");
  while (st.length) { const op = st.pop(); if (op === "(") throw new Error("unbalanced parentheses"); out.push(op); }
  const vs = [], used = [];
  for (const t of out) {
    if (typeof t === "number") { vs.push(t); used.push(t); continue; }
    if (vs.length < 2) throw new Error("malformed expression");
    const b = vs.pop(), a = vs.pop();
    if (t === "+") vs.push(a + b);
    else if (t === "-") vs.push(a - b);
    else if (t === "*") vs.push(a * b);
    else { if (b === 0 || a % b !== 0) throw new Error("division must be exact"); vs.push(a / b); }
  }
  if (vs.length !== 1) throw new Error("malformed expression");
  const avail = {};
  for (const x of numbers) avail[x] = (avail[x] || 0) + 1;
  for (const x of used) { if (!avail[x]) throw new Error(`number ${x} is not available`); avail[x]--; }
  return vs[0];
}

const MOCK_NUMBERS = [100, 75, 25, 6, 3, 2];
const MOCK_TARGET = 452;
function mockCD(path, body) {
  if (path === "/new") return { token: "mock", numbers: MOCK_NUMBERS, target: MOCK_TARGET };
  if (path === "/reveal") return { numbers: MOCK_NUMBERS, target: MOCK_TARGET, note: "No solution shown — reach the target yourself next time." };
  // /solve
  let value;
  try { value = mockEval(String((body && body.expression) || ""), MOCK_NUMBERS); }
  catch (e) { return { valid: false, error: e.message }; }
  const exact = value === MOCK_TARGET;
  const out = { value, target: MOCK_TARGET, valid: true, exact, delta: Math.abs(value - MOCK_TARGET) };
  if (exact) { const reward = 30; state.balance += reward; out.reward = reward; out.balance = state.balance; }
  return out;
}

// Mock round generator mirroring the server's _new_round (0-4 large from {25,50,75,100},
// rest small from two 1..10 sets, target 101..999).
function mockRound() {
  const large = [25, 50, 75, 100];
  const nLarge = Math.floor(Math.random() * 5); // 0..4
  const numbers = [];
  for (let i = 0; i < nLarge; i++) numbers.push(large.splice(Math.floor(Math.random() * large.length), 1)[0]);
  const small = [];
  for (let n = 1; n <= 10; n++) { small.push(n, n); }
  for (let i = 0; i < 6 - nLarge; i++) numbers.push(small.splice(Math.floor(Math.random() * small.length), 1)[0]);
  numbers.sort(() => Math.random() - 0.5);
  const target = 101 + Math.floor(Math.random() * 899);
  return { numbers, target };
}

function mockSprint(path, body) {
  if (path === "/start") {
    const rounds = [];
    for (let i = 0; i < 30; i++) rounds.push(mockRound());
    mockSprint._rounds = rounds;
    return { token: "mock", problems: rounds, duration: 120 };
  }
  // /submit
  const answers = body.answers || [];
  let solved = 0;
  (mockSprint._rounds || []).forEach((r, i) => {
    const expr = answers[i];
    if (!expr) return;
    try { if (mockEval(String(expr), r.numbers) === r.target) solved += 1; } catch (e) { /* invalid — no solve */ }
  });
  const coinsWon = solved * 30;
  state.balance += coinsWon;
  mockSprint._best = Math.max(mockSprint._best || 0, solved);
  return {
    correct: solved, coins: coinsWon, balance: state.balance,
    best: mockSprint._best, is_new_best: solved === mockSprint._best, rank: 1,
  };
}

main();
