// SharpLab HQ — Math 24. Deal 4 numbers, type an expression using ALL four (with
// + − × ÷ and parentheses) that equals 24. Rounds POST to /api/v1/arcade/math24/*
// (session-cookie auth). All grading happens server-side (bot.cogs.math24's real
// expression evaluator, no eval() anywhere including here) — a wrong guess keeps the
// same round live so the player can retry with the same 4 numbers.
//
// Also has a 120-second SPRINT mode (mirrors sequence.js/zetamac.js): /sprint/start
// hands back a batch of puzzles, one hand shown at a time with a single expression
// input that advances on Enter (empty Enter = skip that round), a countdown + live
// attempted counter, and on timeout a /sprint/submit + a highest-solved-wins leaderboard.

const app = document.getElementById("app");
const navRight = document.getElementById("navRight");

// ── Helpers (copied verbatim from countdown.js / sequence.js) ──
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

// ── Nav (login / logout) — mirrors sequence.js (reads state.balance) ──
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
const round = { token: null, numbers: [], done: false, busy: false, attempts: 0 };

// ── Toast (copied verbatim from countdown.js) ──
function toast(msg) {
  const t = document.createElement("div");
  t.className = "cardtoast";
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 3200);
}

// ── Coins hub (copied verbatim from countdown.js) ──
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
  document.querySelectorAll(".m24-btns .btn, .m24-input, .m24-key, .m24-card-num")
    .forEach((el) => (el.disabled = busy));
}

// ── POST to a math24 endpoint. Returns parsed JSON (with _status on error). ──
async function postM24(path, body) {
  if (MOCK) return mockM24(path, body);
  const r = await fetch("/api/v1/arcade/math24" + path, {
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
  layer.className = "m24-confetti";
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

// ── Render 4 dealt numbers as clickable "hand" cards ──
function renderHand(numbers) {
  return numbers.map((n) => `<div class="m24-card-num" data-n="${esc(n)}">${esc(n)}</div>`).join("");
}

// ── Operator keypad (inserts ASCII operators the validator accepts; × ÷ are display-only) ──
const KEYPAD = [["+", "+"], ["−", "-"], ["×", "*"], ["÷", "/"], ["(", "("], [")", ")"]];
function renderKeypad() {
  const keys = KEYPAD.map(([label, val]) =>
    `<button type="button" class="m24-key" data-insert="${esc(val)}">${esc(label)}</button>`).join("");
  return `<div class="m24-keypad">${keys}
    <button type="button" class="m24-key wide" data-backspace="1">⌫</button>
    <button type="button" class="m24-key wide" data-clear="1">Clear</button>
  </div>`;
}

// ── Insert text at the cursor of whichever expression input is currently active ──
function activeInputId() { return sprint.active ? "m24SprintInput" : "m24Input"; }
function insertAtInput(text) {
  const input = $(activeInputId());
  if (!input) return;
  const start = input.selectionStart ?? input.value.length;
  const end = input.selectionEnd ?? input.value.length;
  input.value = input.value.slice(0, start) + text + input.value.slice(end);
  const pos = start + text.length;
  input.focus();
  input.setSelectionRange(pos, pos);
}
function backspaceInput() {
  const input = $(activeInputId());
  if (!input) return;
  const start = input.selectionStart ?? input.value.length;
  const end = input.selectionEnd ?? input.value.length;
  if (start === end && start > 0) {
    input.value = input.value.slice(0, start - 1) + input.value.slice(end);
    input.setSelectionRange(start - 1, start - 1);
  } else {
    input.value = input.value.slice(0, start) + input.value.slice(end);
    input.setSelectionRange(start, start);
  }
  input.focus();
}
function clearInput() {
  const input = $(activeInputId());
  if (!input) return;
  input.value = "";
  input.focus();
}

// ── Start a new (practice) round ──
async function newRound() {
  round.done = false;
  round.attempts = 0;
  setBusy(true);
  const res = await postM24("/new", {});
  if (res.error || res._status) {
    setBusy(false);
    return toast("❌ " + (res.error || "couldn't start a round"));
  }
  round.token = res.token;
  round.numbers = res.numbers || [];
  const hand = $("m24Hand");
  const input = $("m24Input");
  const result = $("m24Result");
  const btns = $("m24Btns");
  if (hand) hand.innerHTML = renderHand(round.numbers);
  if (input) { input.value = ""; input.disabled = false; }
  if (result) { result.innerHTML = ""; result.className = "m24-result"; }
  if (btns) btns.innerHTML =
    `<button class="btn primary big" id="m24Submit">Submit</button>
     <button class="btn ghost" id="m24Skip">Skip →</button>`;
  setBusy(false);
  if (input) input.focus();
}

// ── Submit an expression (practice mode) ──
async function submitGuess() {
  if (round.busy || round.done) return;
  const input = $("m24Input");
  const expr = (input && input.value || "").trim();
  if (!expr) {
    if (input && !REDUCE) {
      input.classList.remove("shake"); void input.offsetWidth; input.classList.add("shake");
      input.addEventListener("animationend", () => input.classList.remove("shake"), { once: true });
    }
    if (input) input.focus();
    return;
  }
  setBusy(true);
  const res = await postM24("/guess", { token: round.token, expr });
  setBusy(false);
  const result = $("m24Result");
  if (res.error || res._status) return toast("❌ " + (res.error || "something went wrong"));

  if (res.correct) {
    round.done = true;
    state.solved += 1;
    const sc = $("m24Solved");
    if (sc) sc.textContent = num(state.solved);
    if (res.balance != null) applyBalance(res.balance);
    const reward = res.reward || 0;
    const rewardTxt = reward
      ? `<span class="m24-reward">+${num(reward)} 🪙</span>`
      : `<span class="m24-cap">daily cap reached — no coins this time</span>`;
    if (result) {
      result.className = "m24-result hit";
      result.innerHTML = `✅ <b>${esc(expr)}</b> = 24 ${rewardTxt}`;
    }
    celebrate();
    const btns = $("m24Btns");
    if (btns) btns.innerHTML = `<button class="btn primary big" id="m24Next">Next puzzle →</button>`;
    if (input) input.disabled = true;
    return;
  }

  // Wrong → keep the round live (same 4 numbers), flash the input, nudge to retry.
  round.attempts += 1;
  if (result) {
    result.className = "m24-result bad";
    result.innerHTML = `⚠️ ${esc(res.msg || "Not 24 — try again")}`;
  }
  if (input && !REDUCE) {
    input.classList.remove("shake"); void input.offsetWidth; input.classList.add("shake");
    input.addEventListener("animationend", () => input.classList.remove("shake"), { once: true });
  }
  if (input) { input.select(); input.focus(); }
}

// ── Skip (practice mode) — deal a fresh hand, no reveal, no reward ──
async function skipRound() {
  if (round.busy || round.done) return;
  newRound();
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
  const r = await fetch("/api/v1/arcade/math24/sprint" + path, {
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
  const btn = $("m24SprintStart");
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
  sprint.answers = new Array(sprint.problems.length).fill("");
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
  const bar = $("m24Bar");
  if (bar) { bar.style.transform = `scaleX(${frac})`; bar.classList.toggle("hot", hot); }
  const secs = $("m24Secs");
  if (secs) { secs.textContent = Math.ceil(remaining / 1000) + "s"; secs.classList.toggle("hot", hot); }
  if (remaining <= 0) { finishSprint(); return; }
  sprint.raf = requestAnimationFrame(sprintTick);
}

// ── Show the current hand ──
function showSprintProblem() {
  const numbers = sprint.problems[sprint.idx];
  const hand = $("m24SprintHand");
  const input = $("m24SprintInput");
  if (!numbers) { finishSprint(); return; }
  if (hand) hand.innerHTML = renderHand(numbers);
  if (input) { input.value = ""; input.focus(); }
}

// ── Commit the typed expression + advance (Enter only; empty = skip) ──
function advanceSprint() {
  if (!sprint.active) return;
  const input = $("m24SprintInput");
  const expr = (input && input.value || "").trim();
  sprint.answers[sprint.idx] = expr; // "" = skipped, grading happens server-side at submit
  if (expr) {
    sprint.solved += 1;
    const sc = $("m24SprintSolved");
    if (sc) sc.textContent = num(sprint.solved);
  }
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
    <div class="m24-head">
      <h1>🧮 Math 24 Sprint <span class="balancechip" id="pageBal">${coins(state.balance)}</span></h1>
      <p>Solve as many "make 24 from 4 numbers" puzzles as you can in 120 seconds.</p>
    </div>
    ${signedOut}
    <div class="m24-wrap">
      <div class="card m24-card">
        <div class="zmbig">⏱️ 120</div>
        <p class="muted">Use all 4 numbers with + − × ÷ ( ) to hit 24, hit Enter to lock it in and move on. Empty Enter skips. Ready?</p>
        <button class="btn primary big" id="m24SprintStart"${state.me ? "" : " disabled"}>Start (120s)</button>
      </div>
    </div>
    <div class="zmleaderboard" id="m24Leaderboard"></div>`;
  loadSprintLeaderboard();
}

function renderSprintPlaying() {
  app.innerHTML = `
    <div class="m24-head">
      <h1>🧮 Math 24 Sprint <span class="balancechip" id="pageBal">${coins(state.balance)}</span></h1>
    </div>
    <div class="m24-wrap">
      <div class="card m24-card">
        <div class="zmtimebar"><div class="zmtimefill" id="m24Bar"></div></div>
        <div class="zmmeta">
          <span class="zmsecs" id="m24Secs">${sprint.seconds}s</span>
          <span class="zmsolved">Solved: <b id="m24SprintSolved">0</b></span>
        </div>
        <div class="m24-target"><span class="tlabel">Make</span><span class="tnum">24</span></div>
        <div class="m24-hand" id="m24SprintHand"></div>
        <input class="m24-input" id="m24SprintInput" type="text" inputmode="text" autocomplete="off"
               autocapitalize="off" spellcheck="false" placeholder="e.g. (8-2)*(5-1)" />
        ${renderKeypad()}
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
    <div class="m24-head">
      <h1>🧮 Math 24 Sprint <span class="balancechip" id="pageBal">${coins(res.balance)}</span></h1>
    </div>
    <div class="m24-wrap">
      <div class="card m24-card zmresult">
        <div class="zmbig">${num(res.correct)} correct</div>
        <div class="zmreward${REDUCE ? "" : " pop"}">+${num(res.coins)} 🪙</div>
        <p class="muted">Balance: <b>${coins(res.balance)}</b></p>
        ${newBest}${bestLine}${rankLine}
        <button class="btn primary big" id="m24SprintStart">Play again</button>
      </div>
    </div>
    <div class="zmleaderboard" id="m24Leaderboard"></div>`;
  loadSprintLeaderboard();
}

// ── Sprint leaderboard ──
async function loadSprintLeaderboard() {
  const res = await getJSON("/api/v1/arcade/math24/sprint/leaderboard");
  if (!res || res._status || !res.top) { renderSprintLeaderboard({ top: [] }); return; }
  renderSprintLeaderboard(res);
}

function renderSprintLeaderboard(res) {
  const sec = $("m24Leaderboard");
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
  return `<div class="m24-modetoggle">
    <button class="btn ${state.mode === "practice" ? "primary" : "ghost"}" id="m24ModePractice">Practice</button>
    <button class="btn ${state.mode === "sprint" ? "primary" : "ghost"}" id="m24ModeSprint">⏱️ 120s Sprint</button>
  </div>`;
}

// ── Delegated events ──
app.addEventListener("click", (e) => {
  if (e.target.closest("#m24Submit")) return submitGuess();
  if (e.target.closest("#m24Skip")) return skipRound();
  if (e.target.closest("#m24Next")) return newRound();
  if (e.target.closest("#m24SprintStart")) return startSprint();
  if (e.target.closest("#m24ModePractice")) { state.mode = "practice"; return buildPage(); }
  if (e.target.closest("#m24ModeSprint")) { state.mode = "sprint"; return renderSprintIntro(); }
  const numTile = e.target.closest(".m24-card-num");
  if (numTile) return insertAtInput(numTile.dataset.n);
  const key = e.target.closest(".m24-key");
  if (key) {
    if (key.dataset.backspace) return backspaceInput();
    if (key.dataset.clear) return clearInput();
    if (key.dataset.insert != null) return insertAtInput(key.dataset.insert);
  }
});
app.addEventListener("keydown", (e) => {
  if (e.target.id === "m24Input" && e.key === "Enter") { e.preventDefault(); submitGuess(); }
  if (e.target.id === "m24SprintInput" && e.key === "Enter") { e.preventDefault(); advanceSprint(); }
});

function buildPage() {
  if (state.mode === "sprint") return renderSprintIntro();
  const signedOut = !state.me
    ? `<div class="card" style="max-width:520px;margin:0 auto 16px;text-align:center">
        <p class="muted" style="margin:0 0 10px">Sign in with Discord to play for coins.</p>
        <a class="btn" href="/api/v1/auth/discord/login">Sign in with Discord</a></div>`
    : "";
  app.innerHTML = `
    ${modeToggle()}
    <div class="m24-head">
      <h1>🧮 Math 24 <span class="balancechip" id="pageBal">${coins(state.balance)}</span></h1>
      <p>Use all 4 dealt numbers, with + − × ÷ and parentheses, to make 24. Correct solves pay coins (daily cap).</p>
    </div>
    ${signedOut}
    ${state.me ? `
    <div class="m24-wrap">
      <div class="card m24-card">
        <div class="m24-target"><span class="tlabel">Make</span><span class="tnum">24</span></div>
        <div class="m24-hand" id="m24Hand"></div>
        <input class="m24-input" id="m24Input" type="text" inputmode="text" autocomplete="off"
               autocapitalize="off" spellcheck="false" placeholder="e.g. (8-2)*(5-1)" />
        ${renderKeypad()}
        <div class="m24-result" id="m24Result"></div>
        <div class="m24-btns" id="m24Btns">
          <button class="btn primary big" id="m24Submit">Submit</button>
          <button class="btn ghost" id="m24Skip">Skip →</button>
        </div>
        <div class="m24-counter">Solved this session: <b id="m24Solved">0</b></div>
      </div>
    </div>` : ""}`;
  if (state.me) newRound();
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
// Mock mode (?mock=1): fake rounds + a local safe expression evaluator (no eval())
// so the page works offline for screenshot testing. Real endpoints win by default.
// ─────────────────────────────────────────────────────────────
const MOCK = new URLSearchParams(location.search).get("mock") === "1";

function mockJSON(url) {
  if (url.startsWith("/api/v1/hq/me"))
    return { authenticated: true, user: { id: "1", username: "davidj", avatar: null }, balance: 12500 };
  if (url.includes("/sprint/leaderboard"))
    return { game: "math24", duration: 120, top: [
      { rank: 1, name: "davidj", score: 9, runs: 4, me: true },
      { rank: 2, name: "steph", score: 7, runs: 2 },
      { rank: 3, name: "nova", score: 5, runs: 1 },
    ] };
  return {};
}

// Minimal safe expression evaluator (tokenize → shunting-yard → RPN), mirroring
// bot/cogs/math24.py's evaluator. No eval() — used only in mock mode for local grading.
function mockEvalExpr(expr) {
  const tokens = [];
  let i = 0;
  const allowed = "0123456789+-*/() ";
  while (i < expr.length) {
    const ch = expr[i];
    if (allowed.indexOf(ch) === -1) return null;
    if (ch === " ") { i++; continue; }
    if (/\d/.test(ch)) {
      let j = i;
      while (j < expr.length && /\d/.test(expr[j])) j++;
      tokens.push(expr.slice(i, j));
      i = j;
    } else {
      tokens.push(ch);
      i++;
    }
  }
  const nums = tokens.filter((t) => /^\d+$/.test(t)).map(Number);

  const prec = { "+": 1, "-": 1, "*": 2, "/": 2 };
  const output = [];
  const ops = [];
  let prev = null;
  for (const tok of tokens) {
    if (tok === "-" && (prev === null || prev === "(" || prec[prev] != null)) {
      output.push("0"); ops.push("-"); prev = tok; continue;
    }
    if (/^\d+$/.test(tok)) output.push(tok);
    else if (prec[tok] != null) {
      while (ops.length && ops[ops.length - 1] !== "(" && prec[ops[ops.length - 1]] >= prec[tok]) output.push(ops.pop());
      ops.push(tok);
    } else if (tok === "(") ops.push(tok);
    else if (tok === ")") {
      while (ops.length && ops[ops.length - 1] !== "(") output.push(ops.pop());
      if (!ops.length) return null;
      ops.pop();
    } else return null;
    prev = tok;
  }
  while (ops.length) {
    const top = ops.pop();
    if (top === "(") return null;
    output.push(top);
  }
  const stack = [];
  for (const tok of output) {
    if (prec[tok] != null) {
      if (stack.length < 2) return null;
      const b = stack.pop(), a = stack.pop();
      if (tok === "+") stack.push(a + b);
      else if (tok === "-") stack.push(a - b);
      else if (tok === "*") stack.push(a * b);
      else { if (Math.abs(b) < 1e-9) return null; stack.push(a / b); }
    } else stack.push(Number(tok));
  }
  if (stack.length !== 1) return null;
  return { value: stack[0], used: nums };
}
function mockValidate(expr, dealt) {
  const r = mockEvalExpr(expr);
  if (!r) return { ok: false, msg: "Invalid expression." };
  if (r.used.length !== 4) return { ok: false, msg: `Must use exactly 4 numbers (you used ${r.used.length}).` };
  const a = [...r.used].sort(), b = [...dealt].sort();
  if (JSON.stringify(a) !== JSON.stringify(b)) return { ok: false, msg: `Must use the dealt numbers ${dealt} exactly once each.` };
  if (Math.abs(r.value - 24) < 1e-9) return { ok: true, msg: "Correct!" };
  return { ok: false, msg: `Expression equals ${Number(r.value.toFixed(4))}, not 24.` };
}

const MOCK_BANK = [[4, 4, 10, 10], [3, 3, 8, 8], [1, 5, 5, 5], [2, 3, 4, 6], [6, 6, 6, 6], [1, 3, 4, 6]];
function mockDeal() { return MOCK_BANK[Math.floor(Math.random() * MOCK_BANK.length)].slice(); }

let _mockRound = null;
function mockM24(path, body) {
  if (path === "/new") {
    _mockRound = mockDeal();
    return { token: "mock", numbers: _mockRound };
  }
  // /guess
  const v = mockValidate(body.expr, _mockRound || []);
  if (v.ok) {
    const reward = 10;
    state.balance += reward;
    return { correct: true, reward, balance: state.balance };
  }
  return { correct: false, msg: v.msg };
}

function mockSprint(path, body) {
  if (path === "/start") {
    const problems = [];
    for (let i = 0; i < 20; i++) problems.push(mockDeal());
    mockSprint._problems = problems;
    return { token: "mock", problems, duration: 120 };
  }
  // /submit
  const answers = body.answers || [];
  const problems = mockSprint._problems || [];
  let correct = 0;
  problems.forEach((nums, i) => {
    const expr = answers[i];
    if (expr && mockValidate(expr, nums).ok) correct += 1;
  });
  const coinsWon = Math.min(correct, 10) * 10; // mirrors math24_win cap shape
  state.balance += coinsWon;
  mockSprint._best = Math.max(mockSprint._best || 0, correct);
  return {
    correct, coins: coinsWon, balance: state.balance,
    best: mockSprint._best, is_new_best: correct === mockSprint._best, rank: 1,
  };
}

main();
