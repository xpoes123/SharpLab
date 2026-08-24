// SharpLab HQ — Math Sprint. A 60-second solo arithmetic drill. POST /start to get a
// batch of 200 problems (operands only) + a signed token; solve as many as you can before
// the timer runs out, then POST /submit — the server recounts correctness and awards
// 2 coins per correct answer (daily-capped). The submit response carries the authoritative
// new balance, which we push into the nav chip + on-page header.
// Score = number correct → a HIGHEST-wins leaderboard, loaded on page load + after every run.

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
const RUN_SECONDS = 60;
const run = {
  active: false, problems: [], token: null, idx: 0, answers: [], solved: 0,
  endAt: 0, raf: 0, timer: 0,
};
const $ = (id) => document.getElementById(id);

function applyBalance(bal) {
  if (bal == null) return;
  state.balance = bal;
  renderNav(state.me && state.me.user);
  const pb = $("pageBal");
  if (pb) pb.textContent = coins(bal);
}

// ── POST to a mathsprint endpoint. Returns parsed JSON (with _status on error). ──
async function postSprint(path, body) {
  if (MOCK) return mockSprint(path, body);
  const r = await fetch("/api/v1/arcade/mathsprint" + path, {
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
  const btn = $("msStart");
  if (btn) btn.disabled = true;
  const res = await postSprint("/start", {});
  if (res.error || res._status) {
    if (btn) btn.disabled = false;
    return toast("❌ " + (res.error || "couldn't start"));
  }
  run.active = true;
  run.problems = res.problems || [];
  run.token = res.token;
  run.idx = 0;
  run.answers = [];
  run.solved = 0;
  run.endAt = performance.now() + RUN_SECONDS * 1000;
  renderPlaying();
  tick();
  showProblem();
}

// ── Countdown loop (rAF for the bar, interval for the seconds label) ──
function tick() {
  const remaining = Math.max(0, run.endAt - performance.now());
  const frac = remaining / (RUN_SECONDS * 1000);
  const bar = $("msBar");
  if (bar) bar.style.transform = `scaleX(${frac})`;
  const secs = $("msSecs");
  if (secs) secs.textContent = Math.ceil(remaining / 1000) + "s";
  if (remaining <= 0) { finishRun(); return; }
  run.raf = requestAnimationFrame(tick);
}

// ── Show the current problem ──
function showProblem() {
  const p = run.problems[run.idx];
  const q = $("msQ");
  const input = $("msInput");
  if (!p) { finishRun(); return; }
  if (q) q.textContent = `${p.a} ${p.op} ${p.b} = ?`;
  if (input) { input.value = ""; input.focus(); }
  const prog = $("msProg");
  if (prog) prog.textContent = `${run.idx + 1} / ${run.problems.length}`;
}

// ── Commit the typed answer + advance ──
function advance() {
  if (!run.active) return;
  const input = $("msInput");
  const raw = (input && input.value || "").trim();
  if (raw === "" || raw === "-") return; // nothing to commit yet
  run.answers[run.idx] = parseInt(raw, 10);
  run.solved += 1;
  const sc = $("msSolved");
  if (sc) sc.textContent = num(run.solved);
  run.idx += 1;
  if (run.idx >= run.problems.length) { finishRun(); return; }
  showProblem();
}

// Auto-advance the instant the typed value is CORRECT (real Zetamac feel) — not
// merely once it's long enough (which advanced on wrong same-length answers).
function onInput() {
  const p = run.problems[run.idx];
  if (!p) return;
  const input = $("msInput");
  const raw = (input && input.value || "").trim();
  if (raw === "" || raw === "-") return;
  if (parseInt(raw, 10) === computeLocal(p)) advance();
}
function computeLocal(p) {
  if (p.op === "+") return p.a + p.b;
  if (p.op === "-") return p.a - p.b;
  return p.a * p.b;
}

// ── Finish + submit ──
async function finishRun() {
  if (!run.active) return;
  run.active = false;
  if (run.raf) cancelAnimationFrame(run.raf);
  const res = await postSprint("/submit", { token: run.token, answers: run.answers });
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
    <div class="ms-head">
      <h1>➗ Math Sprint <span class="balancechip" id="pageBal">${coins(state.balance)}</span></h1>
      <p>Solve as many problems as you can in 60 seconds. Each correct answer pays 2 🪙.</p>
    </div>
    ${signedOut}
    <div class="ms-wrap">
      <div class="card mscard">
        <div class="msbig">⏱️ 60</div>
        <p class="muted">Just type each answer — it advances the instant you're right. No Enter needed. Ready?</p>
        <button class="btn primary big" id="msStart"${state.me ? "" : " disabled"}>Start (60s)</button>
      </div>
    </div>
    <div class="msleaderboard" id="msLeaderboard"></div>`;
  loadLeaderboard();
}

function renderPlaying() {
  app.innerHTML = `
    <div class="ms-head">
      <h1>➗ Math Sprint <span class="balancechip" id="pageBal">${coins(state.balance)}</span></h1>
    </div>
    <div class="ms-wrap">
      <div class="card mscard">
        <div class="mstimebar"><div class="mstimefill" id="msBar"></div></div>
        <div class="msmeta">
          <span class="mssecs" id="msSecs">60s</span>
          <span class="msprog" id="msProg">1 / ${run.problems.length}</span>
          <span class="mssolved">Solved: <b id="msSolved">0</b></span>
        </div>
        <div class="msq" id="msQ"></div>
        <input class="msinput" id="msInput" type="text" inputmode="numeric" autocomplete="off"
               autocapitalize="off" spellcheck="false" placeholder="?" />
      </div>
    </div>`;
}

function renderResult(res) {
  const newBest = res.is_new_best
    ? `<div class="msnewbest${REDUCE ? "" : " pop"}">🏆 new best!</div>`
    : "";
  const bestLine = res.best != null
    ? `<div class="msbest">Your best: <b>${num(res.best)}</b></div>` : "";
  const rankLine = res.rank != null
    ? `<div class="msrank">#${num(res.rank)} on the board</div>` : "";
  app.innerHTML = `
    <div class="ms-head">
      <h1>➗ Math Sprint <span class="balancechip" id="pageBal">${coins(state.balance)}</span></h1>
    </div>
    <div class="ms-wrap">
      <div class="card mscard msresult">
        <div class="msbig">${num(res.correct)} correct</div>
        <div class="msreward${REDUCE ? "" : " pop"}">+${num(res.coins)} 🪙</div>
        <p class="muted">Balance: <b>${coins(res.balance)}</b></p>
        ${newBest}${bestLine}${rankLine}
        <button class="btn primary big" id="msStart">Play again</button>
      </div>
    </div>
    <div class="msleaderboard" id="msLeaderboard"></div>`;
}

// ── Leaderboard ──
async function loadLeaderboard() {
  const res = await getJSON("/api/v1/arcade/mathsprint/leaderboard");
  if (!res || res._status || !res.top) { renderLeaderboard({ top: [] }); return; }
  renderLeaderboard(res);
}

function renderLeaderboard(res) {
  const sec = $("msLeaderboard");
  if (!sec) return;
  const top = res.top || [];
  if (!top.length) {
    sec.innerHTML = `<div class="mslbhead">🏁 Best of 60 seconds</div>
      <div class="mslbempty">No runs yet — be the first!</div>`;
    return;
  }
  const rows = top.map((r) => {
    const runs = r.runs != null ? `<span class="mslbruns">${num(r.runs)} run${r.runs === 1 ? "" : "s"}</span>` : "";
    return `<div class="mslbrow${r.me ? " me" : ""}">
      <span class="mslbrank">#${num(r.rank)}</span>
      <span class="mslbname">${esc(r.name)}</span>
      ${runs}
      <span class="mslbscore">${num(r.score)}</span>
    </div>`;
  }).join("");
  sec.innerHTML = `<div class="mslbhead">🏁 Best of 60 seconds</div>
    <div class="mslblist">${rows}</div>`;
}

// ── Delegated events ──
app.addEventListener("click", (e) => {
  if (e.target.closest("#msStart")) startRun();
});
app.addEventListener("keydown", (e) => {
  if (e.target.id === "msInput" && e.key === "Enter") { e.preventDefault(); advance(); }
});
app.addEventListener("input", (e) => {
  if (e.target.id === "msInput") onInput();
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
    return { game: "mathsprint", top: [
      { rank: 1, name: "davidj", score: 44, runs: 4, me: true },
      { rank: 2, name: "steph", score: 39, runs: 2 },
      { rank: 3, name: "nova", score: 31, runs: 1 },
    ] };
  return {};
}

function mockSprint(path, body) {
  if (path === "/start") {
    const ops = ["+", "-", "×"];
    const problems = [];
    for (let i = 0; i < 200; i++) {
      const op = ops[Math.floor(Math.random() * ops.length)];
      let a = 2 + Math.floor(Math.random() * 11);
      let b = 2 + Math.floor(Math.random() * 11);
      if (op === "-" && b > a) [a, b] = [b, a];
      problems.push({ a, op, b });
    }
    mockSprint._answers = problems.map(computeLocal);
    return { token: "mock", problems };
  }
  // /submit
  const ans = body.answers || [];
  const correct = (mockSprint._answers || []).reduce(
    (n, a, i) => n + (ans[i] === a ? 1 : 0), 0);
  const coinsWon = correct * 2;
  state.balance += coinsWon;
  mockSprint._best = Math.max(mockSprint._best || 0, correct);
  return {
    correct, total: (mockSprint._answers || []).length, coins: coinsWon, balance: state.balance,
    best: mockSprint._best, is_new_best: correct === mockSprint._best, rank: 1,
  };
}

main();
