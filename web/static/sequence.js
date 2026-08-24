// SharpLab HQ — Sequence (guess the next number). A short integer sequence is shown
// minus its final term; the player names the next term. Rounds POST to
// /api/v1/arcade/sequence/* (session-cookie auth). A correct /guess response carries the
// authoritative new balance, which we push into the nav chip + header. The same round
// stays live on a wrong guess so the player can retry.
//
// Also has a 120-second SPRINT mode (mirrors zetamac.js): /sprint/start hands back a batch
// of puzzles (terms only — answers stay server-side), one sequence shown at a time with a
// single input that advances on Enter, a countdown + live solved counter, and on timeout a
// /sprint/submit + a highest-correct-wins leaderboard.

const app = document.getElementById("app");
const navRight = document.getElementById("navRight");

// ── Helpers (copied verbatim from countdown.js) ──
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

// ── Nav (login / logout) — mirrors countdown.js (reads state.balance) ──
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
const round = { token: null, terms: [], done: false, busy: false, attempts: 0 };

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
  document.querySelectorAll(".sq-btns .btn, .sq-input").forEach((el) => (el.disabled = busy));
}

// ── POST to a sequence endpoint. Returns parsed JSON (with _status on error). ──
async function postSQ(path, body) {
  if (MOCK) return mockSQ(path, body);
  const r = await fetch("/api/v1/arcade/sequence" + path, {
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
  layer.className = "sq-confetti";
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

// ── Render the visible terms as tiles, then a trailing "?" tile to guess. ──
function renderTiles(terms) {
  const shown = terms
    .map((n) => `<div class="sq-tile">${esc(n)}</div>`)
    .join('<div class="sq-arrow">→</div>');
  const arrow = terms.length ? '<div class="sq-arrow">→</div>' : "";
  return `${shown}${arrow}<div class="sq-tile mystery" id="sqMystery">?</div>`;
}

// ── Start a new (practice) round ──
async function newRound() {
  round.done = false;
  round.attempts = 0;
  setBusy(true);
  const res = await postSQ("/new", {});
  if (res.error || res._status) {
    setBusy(false);
    return toast("❌ " + (res.error || "couldn't start a round"));
  }
  round.token = res.token;
  round.terms = res.terms || [];
  const tiles = $("sqTiles");
  const input = $("sqInput");
  const result = $("sqResult");
  const btns = $("sqBtns");
  if (tiles) tiles.innerHTML = renderTiles(round.terms);
  if (input) { input.value = ""; input.disabled = false; }
  if (result) { result.innerHTML = ""; result.className = "sq-result"; }
  if (btns) btns.innerHTML =
    `<button class="btn primary big" id="sqSubmit">Guess</button>
     <button class="btn ghost" id="sqGiveUp">Give up</button>`;
  setBusy(false);
  if (input) input.focus();
}

// ── Reveal the mystery tile with a value, styled by kind (hit / off). ──
function fillMystery(value, kind) {
  const m = $("sqMystery");
  if (!m) return;
  m.textContent = num(value);
  m.classList.remove("mystery");
  m.classList.add("revealed", kind);
}

// ── Submit a guess (practice mode) ──
async function submitGuess() {
  if (round.busy || round.done) return;
  const input = $("sqInput");
  const raw = (input && input.value || "").trim();
  if (raw === "" || !/^-?\d+$/.test(raw)) {
    if (input && !REDUCE) {
      input.classList.remove("shake"); void input.offsetWidth; input.classList.add("shake");
      input.addEventListener("animationend", () => input.classList.remove("shake"), { once: true });
    }
    if (input) input.focus();
    return;
  }
  const guess = parseInt(raw, 10);
  setBusy(true);
  const res = await postSQ("/guess", { token: round.token, guess });
  setBusy(false);
  const result = $("sqResult");
  if (res.error || res._status) return toast("❌ " + (res.error || "something went wrong"));

  if (res.correct) {
    round.done = true;
    state.solved += 1;
    const sc = $("sqSolved");
    if (sc) sc.textContent = num(state.solved);
    if (res.balance != null) applyBalance(res.balance);
    fillMystery(res.answer, "hit");
    const reward = res.reward || 0;
    const rewardTxt = reward
      ? `<span class="sq-reward">+${num(reward)} 🪙</span>`
      : `<span class="sq-cap">daily cap reached — no coins this time</span>`;
    if (result) {
      result.className = "sq-result hit";
      result.innerHTML = `✅ <b>${esc(res.name || "Correct")}</b> ${rewardTxt}`;
    }
    celebrate();
    const btns = $("sqBtns");
    if (btns) btns.innerHTML = `<button class="btn primary big" id="sqNext">Next sequence →</button>`;
    if (input) input.disabled = true;
    return;
  }

  // Wrong → keep the round live, flash the input, nudge to retry.
  round.attempts += 1;
  if (result) {
    result.className = "sq-result bad";
    const tries = round.attempts === 1 ? "" : ` (${num(round.attempts)} tries)`;
    result.innerHTML = `⚠️ Not quite — try again${tries}`;
  }
  if (input && !REDUCE) {
    input.classList.remove("shake"); void input.offsetWidth; input.classList.add("shake");
    input.addEventListener("animationend", () => input.classList.remove("shake"), { once: true });
  }
  if (input) { input.select(); input.focus(); }
}

// ── Give up (practice mode) ──
async function giveUp() {
  if (round.busy || round.done) return;
  setBusy(true);
  const res = await postSQ("/reveal", { token: round.token });
  setBusy(false);
  if (res.error || res._status) return toast("❌ " + (res.error || "something went wrong"));
  round.done = true;
  fillMystery(res.answer, "off");
  const result = $("sqResult");
  if (result) {
    result.className = "sq-result off";
    result.innerHTML = `The answer was <b>${num(res.answer)}</b> — ${esc(res.name || "")}`;
  }
  const btns = $("sqBtns");
  if (btns) btns.innerHTML = `<button class="btn primary big" id="sqNext">Next sequence →</button>`;
  const input = $("sqInput");
  if (input) input.disabled = true;
}

// ═══════════════════════════════════════════════════════════════════════════
// Sprint mode — 120s batch run (modeled on zetamac.js)
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
  const r = await fetch("/api/v1/arcade/sequence/sprint" + path, {
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
  const btn = $("sqSprintStart");
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
  const bar = $("sqBar");
  if (bar) { bar.style.transform = `scaleX(${frac})`; bar.classList.toggle("hot", hot); }
  const secs = $("sqSecs");
  if (secs) { secs.textContent = Math.ceil(remaining / 1000) + "s"; secs.classList.toggle("hot", hot); }
  if (remaining <= 0) { finishSprint(); return; }
  sprint.raf = requestAnimationFrame(sprintTick);
}

// ── Show the current sequence ──
function showSprintProblem() {
  const terms = sprint.problems[sprint.idx];
  const tiles = $("sqSprintTiles");
  const input = $("sqSprintInput");
  if (!terms) { finishSprint(); return; }
  if (tiles) tiles.innerHTML = renderTiles(terms);
  if (input) { input.value = ""; input.focus(); }
}

// ── Commit the typed answer + advance (Enter only) ──
function advanceSprint() {
  if (!sprint.active) return;
  const input = $("sqSprintInput");
  const raw = (input && input.value || "").trim();
  if (raw === "" || raw === "-" || !/^-?\d+$/.test(raw)) return; // nothing valid to commit yet
  sprint.answers[sprint.idx] = parseInt(raw, 10);
  sprint.solved += 1;
  const sc = $("sqSprintSolved");
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
    <div class="sq-head">
      <h1>🔢 Sequence Sprint <span class="balancechip" id="pageBal">${coins(state.balance)}</span></h1>
      <p>Solve as many "guess the next number" sequences as you can in 120 seconds.</p>
    </div>
    ${signedOut}
    <div class="sq-wrap">
      <div class="card sq-card">
        <div class="zmbig">⏱️ 120</div>
        <p class="muted">Type the next number and hit Enter to lock it in and move on. Ready?</p>
        <button class="btn primary big" id="sqSprintStart"${state.me ? "" : " disabled"}>Start (120s)</button>
      </div>
    </div>
    <div class="zmleaderboard" id="sqLeaderboard"></div>`;
  loadSprintLeaderboard();
}

function renderSprintPlaying() {
  app.innerHTML = `
    <div class="sq-head">
      <h1>🔢 Sequence Sprint <span class="balancechip" id="pageBal">${coins(state.balance)}</span></h1>
    </div>
    <div class="sq-wrap">
      <div class="card sq-card">
        <div class="zmtimebar"><div class="zmtimefill" id="sqBar"></div></div>
        <div class="zmmeta">
          <span class="zmsecs" id="sqSecs">${sprint.seconds}s</span>
          <span class="zmsolved">Solved: <b id="sqSprintSolved">0</b></span>
        </div>
        <div class="sq-tiles" id="sqSprintTiles"></div>
        <input class="sq-input" id="sqSprintInput" type="text" inputmode="numeric" autocomplete="off"
               autocapitalize="off" spellcheck="false" placeholder="next number…" />
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
    <div class="sq-head">
      <h1>🔢 Sequence Sprint <span class="balancechip" id="pageBal">${coins(res.balance)}</span></h1>
    </div>
    <div class="sq-wrap">
      <div class="card sq-card zmresult">
        <div class="zmbig">${num(res.correct)} correct</div>
        <div class="zmreward${REDUCE ? "" : " pop"}">+${num(res.coins)} 🪙</div>
        <p class="muted">Balance: <b>${coins(res.balance)}</b></p>
        ${newBest}${bestLine}${rankLine}
        <button class="btn primary big" id="sqSprintStart">Play again</button>
      </div>
    </div>
    <div class="zmleaderboard" id="sqLeaderboard"></div>`;
  loadSprintLeaderboard();
}

// ── Sprint leaderboard ──
async function loadSprintLeaderboard() {
  const res = await getJSON("/api/v1/arcade/sequence/sprint/leaderboard");
  if (!res || res._status || !res.top) { renderSprintLeaderboard({ top: [] }); return; }
  renderSprintLeaderboard(res);
}

function renderSprintLeaderboard(res) {
  const sec = $("sqLeaderboard");
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
  return `<div class="sq-modetoggle">
    <button class="btn ${state.mode === "practice" ? "primary" : "ghost"}" id="sqModePractice">Practice</button>
    <button class="btn ${state.mode === "sprint" ? "primary" : "ghost"}" id="sqModeSprint">⏱️ 120s Sprint</button>
  </div>`;
}

// ── Delegated events ──
app.addEventListener("click", (e) => {
  if (e.target.closest("#sqSubmit")) return submitGuess();
  if (e.target.closest("#sqGiveUp")) return giveUp();
  if (e.target.closest("#sqNext")) return newRound();
  if (e.target.closest("#sqSprintStart")) return startSprint();
  if (e.target.closest("#sqModePractice")) { state.mode = "practice"; return buildPage(); }
  if (e.target.closest("#sqModeSprint")) { state.mode = "sprint"; return renderSprintIntro(); }
});
app.addEventListener("keydown", (e) => {
  if (e.target.id === "sqInput" && e.key === "Enter") { e.preventDefault(); submitGuess(); }
  if (e.target.id === "sqSprintInput" && e.key === "Enter") { e.preventDefault(); advanceSprint(); }
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
    <div class="sq-head">
      <h1>🔢 Sequence <span class="balancechip" id="pageBal">${coins(state.balance)}</span></h1>
      <p>Spot the pattern and name the next number. Correct guesses pay coins (daily cap).</p>
    </div>
    ${signedOut}
    ${state.me ? `
    <div class="sq-wrap">
      <div class="card sq-card">
        <div class="sq-tiles" id="sqTiles"></div>
        <input class="sq-input" id="sqInput" type="text" inputmode="numeric"
               autocomplete="off" autocapitalize="off" spellcheck="false"
               placeholder="next number…" />
        <div class="sq-result" id="sqResult"></div>
        <div class="sq-btns" id="sqBtns">
          <button class="btn primary big" id="sqSubmit">Guess</button>
          <button class="btn ghost" id="sqGiveUp">Give up</button>
        </div>
        <div class="sq-counter">Solved this session: <b id="sqSolved">0</b></div>
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
// Mock mode (?mock=1): fake rounds + a local arithmetic sequence so the
// page works offline for screenshot testing. Real endpoints win by default.
// ─────────────────────────────────────────────────────────────
const MOCK = new URLSearchParams(location.search).get("mock") === "1";

function mockJSON(url) {
  if (url.startsWith("/api/v1/hq/me"))
    return { authenticated: true, user: { id: "1", username: "davidj", avatar: null }, balance: 12500 };
  if (url.includes("/sprint/leaderboard"))
    return { game: "sequence", duration: 120, top: [
      { rank: 1, name: "davidj", score: 22, runs: 4, me: true },
      { rank: 2, name: "steph", score: 18, runs: 2 },
      { rank: 3, name: "nova", score: 15, runs: 1 },
    ] };
  return {};
}

const MOCK_TERMS = [3, 7, 11, 15, 19];
const MOCK_ANSWER = 23;
const MOCK_NAME = "Arithmetic +4";
const MOCK_BANK = [
  [[3, 7, 11, 15, 19], 23],
  [[2, 6, 18, 54], 162],
  [[1, 4, 9, 16, 25], 36],
  [[1, 1, 2, 3, 5, 8], 13],
  [[1, 2, 4, 8, 16, 32], 64],
];
function mockSQ(path, body) {
  if (path === "/new") return { token: "mock", terms: MOCK_TERMS };
  if (path === "/reveal") return { answer: MOCK_ANSWER, name: MOCK_NAME };
  // /guess
  if (Number((body && body.guess)) === MOCK_ANSWER) {
    const reward = 30;
    state.balance += reward;
    return { correct: true, answer: MOCK_ANSWER, name: MOCK_NAME, reward, balance: state.balance };
  }
  return { correct: false };
}

function mockSprint(path, body) {
  if (path === "/start") {
    const problems = [];
    const answers = [];
    for (let i = 0; i < 120; i++) {
      const [terms, ans] = MOCK_BANK[Math.floor(Math.random() * MOCK_BANK.length)];
      problems.push(terms.slice());
      answers.push(ans);
    }
    mockSprint._answers = answers;
    return { token: "mock", problems, duration: 120 };
  }
  // /submit
  const ans = body.answers || [];
  const correct = (mockSprint._answers || []).reduce(
    (n, a, i) => n + (i < ans.length && ans[i] === a ? 1 : 0), 0);
  const coinsWon = Math.min(correct, 10) * 20; // mirrors sequence_win cap (10 rewarded/day)
  state.balance += coinsWon;
  mockSprint._best = Math.max(mockSprint._best || 0, correct);
  return {
    correct, coins: coinsWon, balance: state.balance,
    best: mockSprint._best, is_new_best: correct === mockSprint._best, rank: 1,
  };
}

main();
