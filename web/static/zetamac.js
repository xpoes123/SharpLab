// SharpLab HQ — Zetamac. A 120-second solo arithmetic sprint (add/sub/mul/div).
// POST /start returns a batch of pre-rendered problem strings ("12 + 34", "56 − 12",
// "8 × 7", "72 ÷ 9") + a signed token; answers are never sent to the client (server
// checks them at /submit). Solve one problem at a time — type the answer, hit Enter,
// it advances immediately (the server is the only one who knows if you were right).
// When the clock hits 0 we POST /submit with everything collected; the response
// carries correct count, coins, new balance, personal best, and leaderboard rank.
// Score = number correct → a HIGHEST-wins leaderboard, loaded on page load + after
// every run.

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
const state = { me: null, balance: 0 };

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

// ── Game constants + run state ──
const DEFAULT_SECONDS = 120;
const HOT_THRESHOLD = 15; // seconds remaining before the bar/label turn "hot"
const run = {
  active: false, problems: [], token: null, idx: 0, answers: [], solved: 0,
  seconds: DEFAULT_SECONDS, endAt: 0, raf: 0,
};
const $ = (id) => document.getElementById(id);

function applyBalance(bal) {
  if (bal == null) return;
  state.balance = bal;
  renderNav(state.me && state.me.user);
  const pb = $("pageBal");
  if (pb) pb.textContent = coins(bal);
}

// ── POST to a zetamac endpoint. Returns parsed JSON (with _status on error). ──
async function postZM(path, body) {
  if (MOCK) return mockZM(path, body);
  const r = await fetch("/api/v1/arcade/zetamac" + path, {
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

// ── Start a run ──
async function startRun() {
  if (run.active) return;
  const btn = $("zmStart");
  if (btn) btn.disabled = true;
  const res = await postZM("/start", {});
  if (res.error || res._status) {
    if (btn) btn.disabled = false;
    return toast("❌ " + (res.error || "couldn't start"));
  }
  run.active = true;
  run.problems = res.problems || [];
  run.token = res.token;
  run.seconds = res.duration || DEFAULT_SECONDS;
  run.idx = 0;
  run.answers = [];
  run.solved = 0;
  run.endAt = performance.now() + run.seconds * 1000;
  renderPlaying();
  tick();
  showProblem();
}

// ── Countdown loop (rAF for the bar, interval-free — just paint each frame) ──
function tick() {
  const remaining = Math.max(0, run.endAt - performance.now());
  const frac = remaining / (run.seconds * 1000);
  const hot = remaining <= HOT_THRESHOLD * 1000;
  const bar = $("zmBar");
  if (bar) { bar.style.transform = `scaleX(${frac})`; bar.classList.toggle("hot", hot); }
  const secs = $("zmSecs");
  if (secs) { secs.textContent = Math.ceil(remaining / 1000) + "s"; secs.classList.toggle("hot", hot); }
  if (remaining <= 0) { finishRun(); return; }
  run.raf = requestAnimationFrame(tick);
}

// ── Show the current problem ──
function showProblem() {
  const p = run.problems[run.idx];
  const q = $("zmQ");
  const input = $("zmInput");
  if (!p) { finishRun(); return; }
  if (q) q.textContent = `${p} = ?`;
  if (input) { input.value = ""; input.focus(); }
}

// ── Commit the typed answer + advance (Enter only — the client never knows
// whether an answer is right, so there's no auto-advance-by-length here). ──
function advance() {
  if (!run.active) return;
  const input = $("zmInput");
  const raw = (input && input.value || "").trim();
  if (raw === "" || raw === "-") return; // nothing to commit yet
  run.answers[run.idx] = parseInt(raw, 10);
  run.solved += 1;
  const sc = $("zmSolved");
  if (sc) sc.textContent = num(run.solved);
  run.idx += 1;
  if (run.idx >= run.problems.length) { finishRun(); return; }
  showProblem();
}

// ── Finish + submit ──
async function finishRun() {
  if (!run.active) return;
  run.active = false;
  if (run.raf) cancelAnimationFrame(run.raf);
  const res = await postZM("/submit", { token: run.token, answers: run.answers });
  if (res.error || res._status) {
    renderIntro();
    return toast("❌ " + (res.error || "couldn't submit run"));
  }
  applyBalance(res.balance);
  renderResult(res);
  loadLeaderboard();
}

// ── Views ──
function renderIntro() {
  const signedOut = !state.me
    ? `<div class="card" style="max-width:460px;margin:0 auto 16px;text-align:center">
        <p class="muted" style="margin:0 0 10px">Sign in with Discord to play for coins.</p>
        <a class="btn" href="/api/v1/auth/discord/login">Sign in with Discord</a></div>`
    : "";
  app.innerHTML = `
    <div class="zm-head">
      <h1>🧮 Zetamac <span class="balancechip" id="pageBal">${coins(state.balance)}</span></h1>
      <p>Solve as many +, −, ×, ÷ problems as you can in 120 seconds. Each correct answer pays 2 🪙.</p>
    </div>
    ${signedOut}
    <div class="zm-wrap">
      <div class="card zmcard">
        <div class="zmbig">⏱️ 120</div>
        <p class="muted">Type the answer and hit Enter to lock it in and move on. Ready?</p>
        <button class="btn primary big" id="zmStart"${state.me ? "" : " disabled"}>Start (120s)</button>
      </div>
    </div>
    <div class="zmleaderboard" id="zmLeaderboard"></div>`;
  loadLeaderboard();
}

function renderPlaying() {
  app.innerHTML = `
    <div class="zm-head">
      <h1>🧮 Zetamac <span class="balancechip" id="pageBal">${coins(state.balance)}</span></h1>
    </div>
    <div class="zm-wrap">
      <div class="card zmcard">
        <div class="zmtimebar"><div class="zmtimefill" id="zmBar"></div></div>
        <div class="zmmeta">
          <span class="zmsecs" id="zmSecs">${run.seconds}s</span>
          <span class="zmsolved">Solved: <b id="zmSolved">0</b></span>
        </div>
        <div class="zmq" id="zmQ"></div>
        <input class="zminput" id="zmInput" type="text" inputmode="numeric" autocomplete="off"
               autocapitalize="off" spellcheck="false" placeholder="?" />
      </div>
    </div>`;
}

function renderResult(res) {
  const newBest = res.is_new_best
    ? `<div class="zmnewbest${REDUCE ? "" : " pop"}">🏆 new best!</div>`
    : "";
  const bestLine = res.best != null
    ? `<div class="zmbest">Your best: <b>${num(res.best)}</b></div>` : "";
  const rankLine = res.rank != null
    ? `<div class="zmrank">#${num(res.rank)} on the board</div>` : "";
  app.innerHTML = `
    <div class="zm-head">
      <h1>🧮 Zetamac <span class="balancechip" id="pageBal">${coins(state.balance)}</span></h1>
    </div>
    <div class="zm-wrap">
      <div class="card zmcard zmresult">
        <div class="zmbig">${num(res.correct)} correct</div>
        <div class="zmreward${REDUCE ? "" : " pop"}">+${num(res.coins)} 🪙</div>
        <p class="muted">Balance: <b>${coins(res.balance)}</b></p>
        ${newBest}${bestLine}${rankLine}
        <button class="btn primary big" id="zmStart">Play again</button>
      </div>
    </div>
    <div class="zmleaderboard" id="zmLeaderboard"></div>`;
}

// ── Leaderboard ──
async function loadLeaderboard() {
  const res = await getJSON("/api/v1/arcade/zetamac/leaderboard");
  if (!res || res._status || !res.top) { renderLeaderboard({ top: [] }); return; }
  renderLeaderboard(res);
}

function renderLeaderboard(res) {
  const sec = $("zmLeaderboard");
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

// ── Delegated events ──
app.addEventListener("click", (e) => {
  if (e.target.closest("#zmStart")) startRun();
});
app.addEventListener("keydown", (e) => {
  if (e.target.id === "zmInput" && e.key === "Enter") { e.preventDefault(); advance(); }
});

async function main() {
  const me = await getJSON("/api/v1/hq/me");
  const loggedIn = me && me.authenticated;
  state.me = loggedIn ? me : null;
  state.balance = loggedIn ? (me.balance || 0) : 0;
  renderNav(loggedIn ? me.user : null);
  renderIntro();
}

// ─────────────────────────────────────────────────────────────
// Mock mode (?mock=1): local problems + local scoring (~2 coins
// each) so the page works offline. Real endpoints win by default.
// ─────────────────────────────────────────────────────────────
const MOCK = new URLSearchParams(location.search).get("mock") === "1";

function mockJSON(url) {
  if (url.startsWith("/api/v1/hq/me"))
    return { authenticated: true, user: { id: "1", username: "davidj", avatar: null }, balance: 12500 };
  if (url.includes("/leaderboard"))
    return { game: "zetamac", duration: 120, top: [
      { rank: 1, name: "davidj", score: 61, runs: 4, me: true },
      { rank: 2, name: "steph", score: 54, runs: 2 },
      { rank: 3, name: "nova", score: 47, runs: 1 },
    ] };
  return {};
}

function mockZM(path, body) {
  if (path === "/start") {
    const kinds = ["add", "sub", "mul", "div"];
    const problems = [];
    const answers = [];
    for (let i = 0; i < 300; i++) {
      const kind = kinds[Math.floor(Math.random() * kinds.length)];
      if (kind === "add") {
        const a = 2 + Math.floor(Math.random() * 99), b = 2 + Math.floor(Math.random() * 99);
        problems.push(`${a} + ${b}`); answers.push(a + b);
      } else if (kind === "sub") {
        const a = 2 + Math.floor(Math.random() * 99), b = 2 + Math.floor(Math.random() * 99);
        problems.push(`${a + b} − ${b}`); answers.push(a);
      } else if (kind === "mul") {
        const a = 2 + Math.floor(Math.random() * 11), b = 2 + Math.floor(Math.random() * 99);
        problems.push(`${a} × ${b}`); answers.push(a * b);
      } else {
        const a = 2 + Math.floor(Math.random() * 11), b = 2 + Math.floor(Math.random() * 99);
        problems.push(`${a * b} ÷ ${b}`); answers.push(a);
      }
    }
    mockZM._answers = answers;
    return { token: "mock", problems, duration: 120 };
  }
  // /submit
  const ans = body.answers || [];
  const correct = (mockZM._answers || []).reduce(
    (n, a, i) => n + (i < ans.length && ans[i] === a ? 1 : 0), 0);
  const coinsWon = correct * 2;
  state.balance += coinsWon;
  mockZM._best = Math.max(mockZM._best || 0, correct);
  return {
    correct, coins: coinsWon, balance: state.balance,
    best: mockZM._best, is_new_best: correct === mockZM._best, rank: 1,
  };
}

main();
