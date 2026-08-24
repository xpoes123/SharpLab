// SharpLab HQ — Guess the Flag. Name the country from its flag emoji for coins.
// Rounds POST to /api/v1/arcade/geo/* (session-cookie auth); each correct guess
// response carries the authoritative new balance, which we push back into the
// nav chip + on-page header.
//
// Also has a 120-second SPRINT mode (mirrors sequence.js/zetamac.js): /sprint/start
// hands back a batch of flags (answers stay server-side), one flag shown at a time with
// a single text input that advances on Enter, a countdown + live solved counter, and on
// timeout a /sprint/submit + a highest-correct-wins leaderboard.

const app = document.getElementById("app");
const navRight = document.getElementById("navRight");

// ── Helpers (copied verbatim from casino.js) ──
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
const state = { me: null, balance: 0, correct: 0, mode: "practice" };
const round = { token: null, flag: null, solved: false, busy: false, hinted: false };

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
  document.querySelectorAll(".gbtns .btn, .gguess").forEach((el) => (el.disabled = busy));
}

// ── POST to a geo endpoint. Returns parsed JSON (with _status on error). ──
async function postGeo(path, body) {
  if (MOCK) return mockGeo(path, body);
  const r = await fetch("/api/v1/arcade/geo" + path, {
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

// ── Start a new round ──
async function newRound() {
  round.solved = false;
  round.hinted = false;
  const flag = $("gFlag");
  const reveal = $("gReveal");
  const msg = $("gMsg");
  const input = $("gGuess");
  const btnRow = $("gBtns");
  if (reveal) reveal.innerHTML = "";
  if (msg) { msg.textContent = "Which country flies this flag?"; msg.className = "ghintmsg idle"; }
  if (input) { input.value = ""; }
  if (btnRow) btnRow.innerHTML =
    `<button class="btn primary big" id="gGuessBtn">Guess</button>
     <button class="btn ghost" id="gHint">Hint</button>
     <button class="btn ghost" id="gGiveUp">Give up</button>`;
  if (flag) { flag.textContent = "…"; flag.classList.remove("revealed"); }
  setBusy(true);

  const res = await postGeo("/new", {});
  setBusy(false);
  if (res.error || res._status) {
    return toast("❌ " + (res.error || "couldn't start a round"));
  }
  round.token = res.token;
  round.flag = res.flag;
  if (flag) flag.textContent = res.flag || "🏳️";
  if (input) input.focus();
}

// ── Show the solved/revealed state ──
function showReveal(res, solved) {
  round.solved = true;
  const flag = $("gFlag");
  const reveal = $("gReveal");
  const btnRow = $("gBtns");
  const msg = $("gMsg");
  if (flag) flag.classList.add("revealed");
  const rewardLine = solved && res.reward
    ? `<div class="greward">+${num(res.reward)} 🪙</div>`
    : (solved ? "" : `<div class="gcapline">No coins — better luck next time.</div>`);
  if (reveal) reveal.innerHTML =
    `<div class="gname">${esc(res.name || "???")}</div>
     ${rewardLine}
     <div class="gcapline">Capital: <b>${esc(res.capital || "—")}</b></div>`;
  if (msg) { msg.textContent = ""; msg.className = "ghintmsg idle"; }
  if (btnRow) btnRow.innerHTML = `<button class="btn primary big" id="gNext">Next →</button>`;
}

// ── Submit a guess ──
async function submitGuess() {
  if (round.busy || round.solved) return;
  const input = $("gGuess");
  const guess = (input && input.value || "").trim();
  if (!guess) { if (input) input.focus(); return; }
  setBusy(true);
  const res = await postGeo("/guess", { token: round.token, guess });
  setBusy(false);
  if (res.error || res._status) {
    return toast("❌ " + (res.error || "something went wrong"));
  }
  if (res.correct) {
    applyBalance(res.balance);
    state.correct += 1;
    const c = $("gCorrect");
    if (c) c.textContent = num(state.correct);
    showReveal(res, true);
    return;
  }
  // Wrong guess — shake + retry, keep the round going.
  const msg = $("gMsg");
  if (msg) { msg.textContent = "Not quite — try again"; msg.className = "ghintmsg wrong"; }
  if (input && !REDUCE) {
    input.classList.remove("shake"); void input.offsetWidth; input.classList.add("shake");
    input.addEventListener("animationend", () => input.classList.remove("shake"), { once: true });
  }
  if (input) { input.focus(); input.select(); }
}

// ── Hint (free) ──
async function getHint() {
  if (round.busy || round.solved) return;
  setBusy(true);
  const res = await postGeo("/hint", { token: round.token });
  setBusy(false);
  if (res.error || res._status) {
    return toast("❌ " + (res.error || "something went wrong"));
  }
  round.hinted = true;
  const msg = $("gMsg");
  const hint = res.capital
    ? `Capital: ${res.capital}`
    : (res.first_letter ? `Starts with “${res.first_letter}”` : "No hint available");
  if (msg) { msg.textContent = hint; msg.className = "ghintmsg hint"; }
  const input = $("gGuess");
  if (input) input.focus();
}

// ── Give up ──
async function giveUp() {
  if (round.busy || round.solved) return;
  setBusy(true);
  const res = await postGeo("/reveal", { token: round.token });
  setBusy(false);
  if (res.error || res._status) {
    return toast("❌ " + (res.error || "something went wrong"));
  }
  showReveal(res, false);
}

// ═══════════════════════════════════════════════════════════════════════════
// Sprint mode — 120s batch run (modeled on sequence.js / zetamac.js)
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
  const r = await fetch("/api/v1/arcade/geo/sprint" + path, {
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
  const btn = $("gSprintStart");
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
  const bar = $("gBar");
  if (bar) { bar.style.transform = `scaleX(${frac})`; bar.classList.toggle("hot", hot); }
  const secs = $("gSecs");
  if (secs) { secs.textContent = Math.ceil(remaining / 1000) + "s"; secs.classList.toggle("hot", hot); }
  if (remaining <= 0) { finishSprint(); return; }
  sprint.raf = requestAnimationFrame(sprintTick);
}

// ── Show the current flag ──
function showSprintProblem() {
  const problem = sprint.problems[sprint.idx];
  const flag = $("gSprintFlag");
  const input = $("gSprintInput");
  if (!problem) { finishSprint(); return; }
  if (flag) flag.textContent = problem.flag || "🏳️";
  if (input) { input.value = ""; input.focus(); }
}

// ── Commit the typed answer + advance (Enter only) ──
function advanceSprint() {
  if (!sprint.active) return;
  const input = $("gSprintInput");
  const raw = (input && input.value || "").trim();
  if (raw === "") return; // nothing to commit yet
  sprint.answers[sprint.idx] = raw;
  sprint.solved += 1;
  const sc = $("gSprintSolved");
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
    <div class="geo-head">
      <h1>🌍 Flag Sprint <span class="balancechip" id="pageBal">${coins(state.balance)}</span></h1>
      <p>Name as many flags as you can in 120 seconds.</p>
    </div>
    ${signedOut}
    <div class="geo-wrap">
      <div class="card gcard">
        <div class="zmbig">⏱️ 120</div>
        <p class="muted">Type the country name and hit Enter to lock it in and move on. Ready?</p>
        <button class="btn primary big" id="gSprintStart"${state.me ? "" : " disabled"}>Start (120s)</button>
      </div>
    </div>
    <div class="zmleaderboard" id="gLeaderboard"></div>`;
  loadSprintLeaderboard();
}

function renderSprintPlaying() {
  app.innerHTML = `
    <div class="geo-head">
      <h1>🌍 Flag Sprint <span class="balancechip" id="pageBal">${coins(state.balance)}</span></h1>
    </div>
    <div class="geo-wrap">
      <div class="card gcard">
        <div class="zmtimebar"><div class="zmtimefill" id="gBar"></div></div>
        <div class="zmmeta">
          <span class="zmsecs" id="gSecs">${sprint.seconds}s</span>
          <span class="zmsolved">Solved: <b id="gSprintSolved">0</b></span>
        </div>
        <div class="gstage"><div class="gflag" id="gSprintFlag">…</div></div>
        <input class="gguess" id="gSprintInput" type="text" autocomplete="off" autocapitalize="off"
               spellcheck="false" placeholder="Type a country name…" />
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
    <div class="geo-head">
      <h1>🌍 Flag Sprint <span class="balancechip" id="pageBal">${coins(res.balance)}</span></h1>
    </div>
    <div class="geo-wrap">
      <div class="card gcard zmresult">
        <div class="zmbig">${num(res.correct)} correct</div>
        <div class="zmreward${REDUCE ? "" : " pop"}">+${num(res.coins)} 🪙</div>
        <p class="muted">Balance: <b>${coins(res.balance)}</b></p>
        ${newBest}${bestLine}${rankLine}
        <button class="btn primary big" id="gSprintStart">Play again</button>
      </div>
    </div>
    <div class="zmleaderboard" id="gLeaderboard"></div>`;
  loadSprintLeaderboard();
}

// ── Sprint leaderboard ──
async function loadSprintLeaderboard() {
  const res = await getJSON("/api/v1/arcade/geo/sprint/leaderboard");
  if (!res || res._status || !res.top) { renderSprintLeaderboard({ top: [] }); return; }
  renderSprintLeaderboard(res);
}

function renderSprintLeaderboard(res) {
  const sec = $("gLeaderboard");
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
  return `<div class="geo-modetoggle">
    <button class="btn ${state.mode === "practice" ? "primary" : "ghost"}" id="gModePractice">Practice</button>
    <button class="btn ${state.mode === "sprint" ? "primary" : "ghost"}" id="gModeSprint">⏱️ 120s Sprint</button>
  </div>`;
}

// ── Delegated events ──
app.addEventListener("click", (e) => {
  if (e.target.closest("#gGuessBtn")) return submitGuess();
  if (e.target.closest("#gHint")) return getHint();
  if (e.target.closest("#gGiveUp")) return giveUp();
  if (e.target.closest("#gNext")) return newRound();
  if (e.target.closest("#gSprintStart")) return startSprint();
  if (e.target.closest("#gModePractice")) { state.mode = "practice"; return buildPage(); }
  if (e.target.closest("#gModeSprint")) { state.mode = "sprint"; return renderSprintIntro(); }
});
app.addEventListener("keydown", (e) => {
  if (e.target.id === "gGuess" && e.key === "Enter") { e.preventDefault(); submitGuess(); }
  if (e.target.id === "gSprintInput" && e.key === "Enter") { e.preventDefault(); advanceSprint(); }
});

function buildPage() {
  if (state.mode === "sprint") return renderSprintIntro();
  const signedOut = !state.me
    ? `<div class="card" style="max-width:460px;margin:0 auto 16px;text-align:center">
        <p class="muted" style="margin:0 0 10px">Sign in with Discord to play for coins.</p>
        <a class="btn" href="/api/v1/auth/discord/login">Sign in with Discord</a></div>`
    : "";
  app.innerHTML = `
    ${modeToggle()}
    <div class="geo-head">
      <h1>🌍 Guess the Flag <span class="balancechip" id="pageBal">${coins(state.balance)}</span></h1>
      <p>Name the country from its flag to win coins. Close spellings and common names count.</p>
    </div>
    ${signedOut}
    <div class="geo-wrap">
      <div class="card gcard">
        <div class="ghint">Which country?</div>
        <div class="gstage">
          <div class="gflag" id="gFlag" aria-label="Mystery country flag">…</div>
        </div>
        <div class="greveal" id="gReveal"></div>
        <div class="gguessrow">
          <input class="gguess" id="gGuess" type="text" autocomplete="off" autocapitalize="off"
                 spellcheck="false" placeholder="Type a country name…" />
        </div>
        <div class="ghintmsg idle" id="gMsg">Which country flies this flag?</div>
        <div class="gbtns" id="gBtns">
          <button class="btn primary big" id="gGuessBtn">Guess</button>
          <button class="btn ghost" id="gHint">Hint</button>
          <button class="btn ghost" id="gGiveUp">Give up</button>
        </div>
        <div class="gcounter">Correct this session: <b id="gCorrect">0</b></div>
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
// Mock mode (?mock=1): fake rounds without a backend so the page
// can be screenshot-tested offline. Real endpoints win by default.
// ─────────────────────────────────────────────────────────────
const MOCK = new URLSearchParams(location.search).get("mock") === "1";

function mockJSON(url) {
  if (url.startsWith("/api/v1/hq/me"))
    return { authenticated: true, user: { id: "1", username: "davidj", avatar: null }, balance: 12500 };
  if (url.includes("/sprint/leaderboard"))
    return { game: "geo", duration: 120, top: [
      { rank: 1, name: "davidj", score: 41, runs: 3, me: true },
      { rank: 2, name: "steph", score: 33, runs: 2 },
      { rank: 3, name: "nova", score: 28, runs: 1 },
    ] };
  return {};
}

function mockGeo(path, body) {
  if (path === "/new") {
    return { token: "mock-token", flag: "🇫🇷" };
  }
  const answer = { name: "France", capital: "Paris" };
  if (path === "/hint") return { capital: "Paris", first_letter: "F" };
  if (path === "/reveal") return { correct: false, gaveup: true, ...answer };
  // /guess
  const g = String(body && body.guess || "").trim().toLowerCase();
  if (g === "france") {
    const reward = 15;
    state.balance += reward;
    return { correct: true, reward, balance: state.balance, ...answer };
  }
  return { correct: false };
}

const MOCK_FLAG_BANK = [
  ["🇫🇷", "france"], ["🇩🇪", "germany"], ["🇯🇵", "japan"], ["🇧🇷", "brazil"],
  ["🇦🇺", "australia"], ["🇨🇦", "canada"], ["🇮🇹", "italy"], ["🇪🇸", "spain"],
];
function mockSprint(path, body) {
  if (path === "/start") {
    const problems = [];
    const answers = [];
    for (let i = 0; i < 80; i++) {
      const [flag, name] = MOCK_FLAG_BANK[Math.floor(Math.random() * MOCK_FLAG_BANK.length)];
      problems.push({ flag });
      answers.push(name);
    }
    mockSprint._answers = answers;
    return { token: "mock", problems, duration: 120 };
  }
  // /submit
  const ans = body.answers || [];
  const correct = (mockSprint._answers || []).reduce(
    (n, a, i) => n + (i < ans.length && String(ans[i] || "").trim().toLowerCase() === a ? 1 : 0), 0);
  const coinsWon = Math.min(correct, 10) * 15; // mirrors geo_guess cap (10 rewarded/day)
  state.balance += coinsWon;
  mockSprint._best = Math.max(mockSprint._best || 0, correct);
  return {
    correct, coins: coinsWon, balance: state.balance,
    best: mockSprint._best, is_new_best: correct === mockSprint._best, rank: 1,
  };
}

main();
