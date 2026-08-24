// SharpLab HQ — Valorant Agent Guess. Name the mystery agent from progressive clues.
// Rounds POST to /api/v1/arcade/valorant/* (session-cookie auth); each correct guess
// response carries the authoritative new balance, which we push back into the nav chip
// + on-page header. Clues reveal one at a time (on a wrong guess or a "Reveal a clue" tap).
//
// Also has a 120-second SPRINT mode (mirrors sequence.js / zetamac.js): /sprint/start hands
// back a batch of agents (clues only — answers stay server-side), one agent's full clue set
// shown at a time with a single input that advances on Enter, a countdown + live solved
// counter, and on timeout a /sprint/submit + a highest-correct-wins leaderboard.

const app = document.getElementById("app");
const navRight = document.getElementById("navRight");

// ── Helpers (copied verbatim from pokemon.js / casino.js) ──
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

// ── Nav (login / logout) — mirrors pokemon.js (reads state.balance) ──
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
const round = { token: null, clues: [], shown: 1, solved: false, busy: false };

// ── Toast (copied verbatim from pokemon.js) ──
function toast(msg) {
  const t = document.createElement("div");
  t.className = "cardtoast";
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 3200);
}

// ── Coins hub (copied verbatim from pokemon.js) ──
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

// ── Clue labels (index → heading) ──
const CLUE_LABELS = ["Clue 1 · Role", "Clue 2 · Origin", "Clue 3 · Abilities"];

function applyBalance(bal) {
  if (bal == null) return;
  state.balance = bal;
  renderNav(state.me && state.me.user);
  const pb = document.getElementById("pageBal");
  if (pb) pb.textContent = coins(bal);
}

function setBusy(busy) {
  round.busy = busy;
  document.querySelectorAll(".vbtns .btn, .vguess").forEach((el) => (el.disabled = busy));
}

// ── POST to a valorant endpoint. Returns parsed JSON (with _status on error). ──
async function postVal(path, body) {
  if (MOCK) return mockVal(path, body);
  const r = await fetch("/api/v1/arcade/valorant" + path, {
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

// ── UI element refs ──
const $ = (id) => document.getElementById(id);

// ── Render the currently-revealed clues ──
function renderClues() {
  const wrap = $("vClues");
  if (!wrap) return;
  const total = round.clues.length;
  const shown = Math.min(round.shown, total);
  wrap.innerHTML = round.clues.slice(0, shown).map((c, i) => {
    const fresh = i === shown - 1 && !REDUCE ? " fresh" : "";
    return `<div class="vclue${fresh}">
      <div class="vcluelabel">${esc(CLUE_LABELS[i] || "Clue " + (i + 1))}</div>
      <div class="vcluetext">${esc(c)}</div></div>`;
  }).join("");
  const more = $("vReveal");
  if (more) {
    const left = total - shown;
    more.disabled = round.busy || round.solved || left <= 0;
    more.textContent = left > 0 ? `Reveal a clue (${left} left)` : "No more clues";
  }
}

// ── Start a new round ──
async function newRound() {
  round.solved = false;
  round.shown = 1;
  const reveal = $("vResult");
  const msg = $("vMsg");
  const input = $("vGuess");
  const btnRow = $("vBtns");
  if (reveal) reveal.innerHTML = "";
  if (msg) { msg.textContent = "Who's this agent?"; msg.className = "vhintmsg idle"; }
  if (input) { input.value = ""; }
  if (btnRow) btnRow.innerHTML =
    `<button class="btn primary big" id="vGuessBtn">Guess</button>
     <button class="btn ghost" id="vGiveUp">Give up</button>`;
  setBusy(true);

  const res = await postVal("/new", {});
  if (res.error || res._status) {
    setBusy(false);
    return toast("❌ " + (res.error || "couldn't start a round"));
  }
  round.token = res.token;
  round.clues = res.clues || [];
  renderClues();
  setBusy(false);
  if (input) input.focus();
}

// ── Reveal one more clue (button) ──
function revealClue() {
  if (round.busy || round.solved) return;
  if (round.shown < round.clues.length) {
    round.shown += 1;
    renderClues();
  }
}

// ── Show the solved/revealed state ──
function showResult(name, solved, reward) {
  round.solved = true;
  round.shown = round.clues.length; // drop all clues on the table
  renderClues();
  const reveal = $("vResult");
  const btnRow = $("vBtns");
  const msg = $("vMsg");
  const rewardLine = solved && reward
    ? `<div class="vreward">+${num(reward)} 🪙</div>`
    : (solved ? "" : `<div class="vgiveup">No coins — you gave up.</div>`);
  if (reveal) reveal.innerHTML =
    `<div class="vname ${solved ? "win" : "lose"}">${esc(name || "???")}</div>
     ${rewardLine}`;
  if (msg) { msg.textContent = ""; msg.className = "vhintmsg idle"; }
  if (btnRow) btnRow.innerHTML = `<button class="btn primary big" id="vNext">Next agent →</button>`;
}

// ── Submit a guess ──
async function submitGuess() {
  if (round.busy || round.solved) return;
  const input = $("vGuess");
  const guess = (input && input.value || "").trim();
  if (!guess) { if (input) input.focus(); return; }
  setBusy(true);
  const res = await postVal("/guess", { token: round.token, guess });
  setBusy(false);
  if (res.error || res._status) {
    return toast("❌ " + (res.error || "something went wrong"));
  }
  if (res.correct) {
    applyBalance(res.balance);
    state.correct += 1;
    const c = $("vCorrect");
    if (c) c.textContent = num(state.correct);
    showResult(res.name, true, res.reward);
    return;
  }
  // Wrong guess — shake, reveal the next clue, keep the round going.
  const msg = $("vMsg");
  const revealed = round.shown < round.clues.length;
  if (revealed) round.shown += 1;
  renderClues();
  if (msg) {
    msg.textContent = revealed ? "Not quite — here's another clue" : "Not quite — try again";
    msg.className = "vhintmsg wrong";
  }
  if (input && !REDUCE) {
    input.classList.remove("shake"); void input.offsetWidth; input.classList.add("shake");
    input.addEventListener("animationend", () => input.classList.remove("shake"), { once: true });
  }
  if (input) { input.focus(); input.select(); }
}

// ── Give up ──
async function giveUp() {
  if (round.busy || round.solved) return;
  setBusy(true);
  const res = await postVal("/reveal", { token: round.token });
  setBusy(false);
  if (res.error || res._status) {
    return toast("❌ " + (res.error || "something went wrong"));
  }
  showResult(res.name, false, 0);
}

// ── Mode toggle (practice ⇄ sprint) ──
function modeToggle() {
  return `<div class="val-modetoggle">
    <button class="btn ${state.mode === "practice" ? "primary" : "ghost"}" id="vModePractice">Practice</button>
    <button class="btn ${state.mode === "sprint" ? "primary" : "ghost"}" id="vModeSprint">⏱️ 120s Sprint</button>
  </div>`;
}

// ── Delegated events ──
app.addEventListener("click", (e) => {
  if (e.target.closest("#vGuessBtn")) return submitGuess();
  if (e.target.closest("#vGiveUp")) return giveUp();
  if (e.target.closest("#vNext")) return newRound();
  if (e.target.closest("#vReveal")) return revealClue();
  if (e.target.closest("#vSprintStart")) return startSprint();
  if (e.target.closest("#vModePractice")) { state.mode = "practice"; return buildPage(); }
  if (e.target.closest("#vModeSprint")) { state.mode = "sprint"; return renderSprintIntro(); }
});
app.addEventListener("keydown", (e) => {
  if (e.target.id === "vGuess" && e.key === "Enter") { e.preventDefault(); submitGuess(); }
  if (e.target.id === "vSprintInput" && e.key === "Enter") { e.preventDefault(); advanceSprint(); }
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
    <div class="val-head">
      <h1>🎯 Valorant Agent Guess <span class="balancechip" id="pageBal">${coins(state.balance)}</span></h1>
      <p>Name the mystery agent from the clues. Fewer clues, same reward — but a correct guess is a correct guess.</p>
    </div>
    ${signedOut}
    <div class="val-wrap">
      <div class="card vcard">
        <div class="vphint">Mystery agent</div>
        <div class="vclues" id="vClues"></div>
        <div class="vresult" id="vResult"></div>
        <div class="vguessrow">
          <input class="vguess" id="vGuess" type="text" autocomplete="off" autocapitalize="off"
                 spellcheck="false" placeholder="Type an agent name…" />
        </div>
        <div class="vhintmsg idle" id="vMsg">Who's this agent?</div>
        <button class="btn ghost vrevealbtn" id="vReveal">Reveal a clue</button>
        <div class="vbtns" id="vBtns">
          <button class="btn primary big" id="vGuessBtn">Guess</button>
          <button class="btn ghost" id="vGiveUp">Give up</button>
        </div>
        <div class="vcounter">Correct this session: <b id="vCorrect">0</b></div>
      </div>
    </div>`;
  newRound();
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
  const r = await fetch("/api/v1/arcade/valorant/sprint" + path, {
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
  const btn = $("vSprintStart");
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
  const bar = $("vBar");
  if (bar) { bar.style.transform = `scaleX(${frac})`; bar.classList.toggle("hot", hot); }
  const secs = $("vSecs");
  if (secs) { secs.textContent = Math.ceil(remaining / 1000) + "s"; secs.classList.toggle("hot", hot); }
  if (remaining <= 0) { finishSprint(); return; }
  sprint.raf = requestAnimationFrame(sprintTick);
}

// ── Show the current agent's clues ──
function showSprintProblem() {
  const problem = sprint.problems[sprint.idx];
  const wrap = $("vSprintClues");
  const input = $("vSprintInput");
  if (!problem) { finishSprint(); return; }
  if (wrap) {
    wrap.innerHTML = (problem.clues || []).map((c, i) =>
      `<div class="vclue"><div class="vcluelabel">${esc(CLUE_LABELS[i] || "Clue " + (i + 1))}</div>
       <div class="vcluetext">${esc(c)}</div></div>`).join("");
  }
  if (input) { input.value = ""; input.focus(); }
}

// ── Commit the typed answer + advance (Enter only) ──
function advanceSprint() {
  if (!sprint.active) return;
  const input = $("vSprintInput");
  const raw = (input && input.value || "").trim();
  if (!raw) return; // nothing typed yet — don't burn the problem
  sprint.answers[sprint.idx] = raw;
  sprint.solved += 1;
  const sc = $("vSprintSolved");
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
    <div class="val-head">
      <h1>🎯 Valorant Sprint <span class="balancechip" id="pageBal">${coins(state.balance)}</span></h1>
      <p>Name as many mystery agents as you can from their clues in 120 seconds.</p>
    </div>
    ${signedOut}
    <div class="val-wrap">
      <div class="card vcard">
        <div class="zmbig">⏱️ 120</div>
        <p class="muted">Type the agent's name and hit Enter to lock it in and move on. Ready?</p>
        <button class="btn primary big" id="vSprintStart"${state.me ? "" : " disabled"}>Start (120s)</button>
      </div>
    </div>
    <div class="zmleaderboard" id="vLeaderboard"></div>`;
  loadSprintLeaderboard();
}

function renderSprintPlaying() {
  app.innerHTML = `
    <div class="val-head">
      <h1>🎯 Valorant Sprint <span class="balancechip" id="pageBal">${coins(state.balance)}</span></h1>
    </div>
    <div class="val-wrap">
      <div class="card vcard">
        <div class="zmtimebar"><div class="zmtimefill" id="vBar"></div></div>
        <div class="zmmeta">
          <span class="zmsecs" id="vSecs">${sprint.seconds}s</span>
          <span class="zmsolved">Solved: <b id="vSprintSolved">0</b></span>
        </div>
        <div class="vclues" id="vSprintClues"></div>
        <input class="vguess" id="vSprintInput" type="text" autocomplete="off"
               autocapitalize="off" spellcheck="false" placeholder="Type an agent name…" />
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
    <div class="val-head">
      <h1>🎯 Valorant Sprint <span class="balancechip" id="pageBal">${coins(res.balance)}</span></h1>
    </div>
    <div class="val-wrap">
      <div class="card vcard zmresult">
        <div class="zmbig">${num(res.correct)} correct</div>
        <div class="zmreward${REDUCE ? "" : " pop"}">+${num(res.coins)} 🪙</div>
        <p class="muted">Balance: <b>${coins(res.balance)}</b></p>
        ${newBest}${bestLine}${rankLine}
        <button class="btn primary big" id="vSprintStart">Play again</button>
      </div>
    </div>
    <div class="zmleaderboard" id="vLeaderboard"></div>`;
  loadSprintLeaderboard();
}

// ── Sprint leaderboard ──
async function loadSprintLeaderboard() {
  const res = await getJSON("/api/v1/arcade/valorant/sprint/leaderboard");
  if (!res || res._status || !res.top) { renderSprintLeaderboard({ top: [] }); return; }
  renderSprintLeaderboard(res);
}

function renderSprintLeaderboard(res) {
  const sec = $("vLeaderboard");
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
    return { game: "valorant", duration: 120, top: [
      { rank: 1, name: "davidj", score: 19, runs: 3, me: true },
      { rank: 2, name: "steph", score: 14, runs: 2 },
      { rank: 3, name: "nova", score: 11, runs: 1 },
    ] };
  return {};
}

const MOCK_AGENTS = [
  {
    name: "Jett",
    clues: [
      "Role: Duelist",
      "Origin: South Korea",
      "Ability profile: A wind-powered duelist who can dash forward, updraft vertically, and float in the air. Her ultimate summons five throwing knives that reset on kills.",
    ],
  },
  {
    name: "Sova",
    clues: [
      "Role: Initiator",
      "Origin: Russia",
      "Ability profile: A master tracker who uses a bow to fire recon bolts that reveal enemies, deploys an owl drone, and fires wall-piercing energy blasts with his ultimate.",
    ],
  },
];
let mockPick = MOCK_AGENTS[0];
function mockVal(path, body) {
  if (path === "/new") {
    mockPick = MOCK_AGENTS[Math.floor(Math.random() * MOCK_AGENTS.length)];
    return { token: "mock-token", clues: mockPick.clues };
  }
  if (path === "/reveal") return { name: mockPick.name };
  // /guess
  const g = String(body && body.guess || "").trim().toLowerCase();
  if (g === mockPick.name.toLowerCase()) {
    const reward = 120;
    state.balance += reward;
    return { correct: true, name: mockPick.name, reward, balance: state.balance };
  }
  return { correct: false };
}

function mockSprint(path, body) {
  if (path === "/start") {
    const problems = [];
    for (let i = 0; i < 50; i++) {
      const pick = MOCK_AGENTS[Math.floor(Math.random() * MOCK_AGENTS.length)];
      problems.push({ clues: pick.clues, _name: pick.name });
    }
    mockSprint._problems = problems;
    return { token: "mock", problems: problems.map((p) => ({ clues: p.clues })), duration: 120 };
  }
  // /submit
  const ans = body.answers || [];
  const problems = mockSprint._problems || [];
  const correct = problems.reduce(
    (n, p, i) => n + (i < ans.length && String(ans[i] || "").trim().toLowerCase() === p._name.toLowerCase() ? 1 : 0), 0);
  const coinsWon = Math.min(correct, 10) * 30; // mirrors valorant_guess cap (10 rewarded/day)
  state.balance += coinsWon;
  mockSprint._best = Math.max(mockSprint._best || 0, correct);
  return {
    correct, coins: coinsWon, balance: state.balance,
    best: mockSprint._best, is_new_best: correct === mockSprint._best, rank: 1,
  };
}

main();
