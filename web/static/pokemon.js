// SharpLab HQ — Who's That Pokémon? Guess the silhouette for coins.
// Rounds POST to /api/v1/arcade/pokemon/* (session-cookie auth); each correct
// guess response carries the authoritative new balance, which we push back into
// the nav chip + on-page header.
//
// Also has a 120-second SPRINT mode (mirrors zetamac.js / sequence.js): /sprint/start
// hands back a batch of sprite URLs (silhouette is a CSS effect the client applies —
// the answer stays server-side), one sprite shown at a time with a single input that
// advances on Enter, a countdown + live solved counter, and on timeout a /sprint/submit
// + a highest-correct-wins leaderboard.

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
const round = { token: null, gen: null, solved: false, busy: false };

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

// ── Type colors (Pokémon standard palette) ──
const TYPE_COLORS = {
  normal: "#9099a1", fire: "#ff9c54", water: "#4d90d5", electric: "#f3d23b",
  grass: "#63bb5b", ice: "#74cec0", fighting: "#ce4069", poison: "#ab6ac8",
  ground: "#d97746", flying: "#8fa8dd", psychic: "#f97176", bug: "#90c12c",
  rock: "#c7b78b", ghost: "#5269ac", dragon: "#0a6dc4", dark: "#5a5366",
  steel: "#5a8ea1", fairy: "#ec8fe6",
};
function typeChip(t) {
  const c = TYPE_COLORS[String(t).toLowerCase()] || "var(--panel2)";
  return `<span class="ptype" style="background:${c}">${esc(t)}</span>`;
}

function applyBalance(bal) {
  if (bal == null) return;
  state.balance = bal;
  renderNav(state.me && state.me.user);
  const pb = document.getElementById("pageBal");
  if (pb) pb.textContent = coins(bal);
}

function setBusy(busy) {
  round.busy = busy;
  document.querySelectorAll(".pbtns .btn, .pguess").forEach((el) => (el.disabled = busy));
}

// ── POST to a pokemon endpoint. Returns parsed JSON (with _status on error). ──
async function postPokemon(path, body) {
  if (MOCK) return mockPokemon(path, body);
  const r = await fetch("/api/v1/arcade/pokemon" + path, {
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

// ── Start a new round ──
async function newRound() {
  round.solved = false;
  const stage = $("pStage");
  const sprite = $("pSprite");
  const reveal = $("pReveal");
  const hint = $("pHint");
  const msg = $("pMsg");
  const input = $("pGuess");
  const btnRow = $("pBtns");
  if (reveal) reveal.innerHTML = "";
  if (msg) { msg.textContent = "Guess the Pokémon!"; msg.className = "phintmsg idle"; }
  if (input) { input.value = ""; }
  // Restore the guessing controls
  if (btnRow) btnRow.innerHTML =
    `<button class="btn primary big" id="pGuessBtn">Guess</button>
     <button class="btn ghost" id="pGiveUp">Give up</button>`;
  if (stage) stage.classList.add("loading");
  if (sprite) { sprite.classList.remove("revealed"); sprite.style.visibility = "hidden"; }
  setBusy(true);

  const res = await postPokemon("/new", {});
  if (res.error || res._status) {
    if (stage) stage.classList.remove("loading");
    setBusy(false);
    return toast("❌ " + (res.error || "couldn't start a round"));
  }
  round.token = res.token;
  round.gen = res.gen;
  if (hint) hint.textContent = res.gen ? `Generation ${res.gen}` : "Who's that Pokémon?";
  if (sprite && res.sprite) {
    sprite.onload = () => { if (stage) stage.classList.remove("loading"); sprite.style.visibility = "visible"; };
    sprite.onerror = () => { if (stage) stage.classList.remove("loading"); sprite.style.visibility = "visible"; };
    sprite.src = res.sprite;
  }
  setBusy(false);
  if (input) input.focus();
}

// ── Show the solved/revealed state ──
function showReveal(res, solved) {
  round.solved = true;
  const sprite = $("pSprite");
  const reveal = $("pReveal");
  const btnRow = $("pBtns");
  const msg = $("pMsg");
  if (sprite && res.sprite) sprite.src = res.sprite;
  if (sprite) sprite.classList.add("revealed");
  const leg = res.legendary ? ` <span class="legmark" title="Legendary">✨</span>` : "";
  const types = (res.types || []).map(typeChip).join("");
  const rewardLine = solved && res.reward
    ? `<div class="preward">+${num(res.reward)} 🪙</div>`
    : (solved ? "" : `<div class="pdexline">No coins — better luck next time.</div>`);
  if (reveal) reveal.innerHTML =
    `<div class="pname">${esc(res.name || "???")}${leg}</div>
     <div class="ptypes">${types}</div>
     ${rewardLine}
     <div class="pdexline">#${num(res.dex)} · Gen ${esc(res.gen)}</div>`;
  if (msg) { msg.textContent = ""; msg.className = "phintmsg idle"; }
  if (btnRow) btnRow.innerHTML = `<button class="btn primary big" id="pNext">Next →</button>`;
}

// ── Submit a guess ──
async function submitGuess() {
  if (round.busy || round.solved) return;
  const input = $("pGuess");
  const guess = (input && input.value || "").trim();
  if (!guess) { if (input) input.focus(); return; }
  setBusy(true);
  const res = await postPokemon("/guess", { token: round.token, guess });
  setBusy(false);
  if (res.error || res._status) {
    return toast("❌ " + (res.error || "something went wrong"));
  }
  if (res.correct) {
    applyBalance(res.balance);
    state.correct += 1;
    const c = $("pCorrect");
    if (c) c.textContent = num(state.correct);
    showReveal(res, true);
    return;
  }
  // Wrong guess — shake + hint, keep the round going.
  const msg = $("pMsg");
  if (msg) { msg.textContent = "Not quite — try again"; msg.className = "phintmsg wrong"; }
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
  const res = await postPokemon("/reveal", { token: round.token, guess: "" });
  setBusy(false);
  if (res.error || res._status) {
    return toast("❌ " + (res.error || "something went wrong"));
  }
  showReveal(res, false);
}

// ── Mode toggle (practice ⇄ sprint) ──
function modeToggle() {
  return `<div class="poke-modetoggle">
    <button class="btn ${state.mode === "practice" ? "primary" : "ghost"}" id="pModePractice">Practice</button>
    <button class="btn ${state.mode === "sprint" ? "primary" : "ghost"}" id="pModeSprint">⏱️ 120s Sprint</button>
  </div>`;
}

// ── Delegated events ──
app.addEventListener("click", (e) => {
  if (e.target.closest("#pGuessBtn")) return submitGuess();
  if (e.target.closest("#pGiveUp")) return giveUp();
  if (e.target.closest("#pNext")) return newRound();
  if (e.target.closest("#pSprintStart")) return startSprint();
  if (e.target.closest("#pModePractice")) { state.mode = "practice"; return buildPage(); }
  if (e.target.closest("#pModeSprint")) { state.mode = "sprint"; return renderSprintIntro(); }
});
app.addEventListener("keydown", (e) => {
  if (e.target.id === "pGuess" && e.key === "Enter") { e.preventDefault(); submitGuess(); }
  if (e.target.id === "pSprintInput" && e.key === "Enter") { e.preventDefault(); advanceSprint(); }
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
    <div class="poke-head">
      <h1>❓ Who's That Pokémon? <span class="balancechip" id="pageBal">${coins(state.balance)}</span></h1>
      <p>Name the silhouette to win coins. Some Pokémon are worth more than others.</p>
    </div>
    ${signedOut}
    ${state.me ? `
    <div class="poke-wrap">
      <div class="card pcard">
        <div class="phint" id="pHint">Who's that Pokémon?</div>
        <div class="pstage loading" id="pStage">
          <img class="psprite" id="pSprite" alt="Mystery Pokémon silhouette" style="visibility:hidden" />
        </div>
        <div class="preveal" id="pReveal"></div>
        <div class="pguessrow">
          <input class="pguess" id="pGuess" type="text" autocomplete="off" autocapitalize="off"
                 spellcheck="false" placeholder="Type a Pokémon name…" />
        </div>
        <div class="phintmsg idle" id="pMsg">Guess the Pokémon!</div>
        <div class="pbtns" id="pBtns">
          <button class="btn primary big" id="pGuessBtn">Guess</button>
          <button class="btn ghost" id="pGiveUp">Give up</button>
        </div>
        <div class="pcounter">Correct this session: <b id="pCorrect">0</b></div>
      </div>
    </div>` : ""}`;
  if (state.me) newRound();
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
  const r = await fetch("/api/v1/arcade/pokemon/sprint" + path, {
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
  const btn = $("pSprintStart");
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
  const bar = $("pBar");
  if (bar) { bar.style.transform = `scaleX(${frac})`; bar.classList.toggle("hot", hot); }
  const secs = $("pSecs");
  if (secs) { secs.textContent = Math.ceil(remaining / 1000) + "s"; secs.classList.toggle("hot", hot); }
  if (remaining <= 0) { finishSprint(); return; }
  sprint.raf = requestAnimationFrame(sprintTick);
}

// ── Show the current silhouette ──
function showSprintProblem() {
  const problem = sprint.problems[sprint.idx];
  const img = $("pSprintImg");
  const input = $("pSprintInput");
  if (!problem) { finishSprint(); return; }
  if (img) { img.src = problem.image; img.style.visibility = "hidden"; }
  if (img) { img.onload = () => { img.style.visibility = "visible"; }; img.onerror = () => { img.style.visibility = "visible"; }; }
  if (input) { input.value = ""; input.focus(); }
}

// ── Commit the typed answer + advance (Enter only) ──
function advanceSprint() {
  if (!sprint.active) return;
  const input = $("pSprintInput");
  const raw = (input && input.value || "").trim();
  if (!raw) return; // nothing to commit yet — skip requires an explicit action, just leave blank on timeout
  sprint.answers[sprint.idx] = raw;
  sprint.solved += 1;
  const sc = $("pSprintSolved");
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
    <div class="poke-head">
      <h1>❓ Pokémon Sprint <span class="balancechip" id="pageBal">${coins(state.balance)}</span></h1>
      <p>Name as many silhouettes as you can in 120 seconds.</p>
    </div>
    ${signedOut}
    <div class="poke-wrap">
      <div class="card pcard">
        <div class="zmbig">⏱️ 120</div>
        <p class="muted">Type the Pokémon's name and hit Enter to lock it in and move on. Ready?</p>
        <button class="btn primary big" id="pSprintStart"${state.me ? "" : " disabled"}>Start (120s)</button>
      </div>
    </div>
    <div class="zmleaderboard" id="pLeaderboard"></div>`;
  loadSprintLeaderboard();
}

function renderSprintPlaying() {
  app.innerHTML = `
    <div class="poke-head">
      <h1>❓ Pokémon Sprint <span class="balancechip" id="pageBal">${coins(state.balance)}</span></h1>
    </div>
    <div class="poke-wrap">
      <div class="card pcard">
        <div class="zmtimebar"><div class="zmtimefill" id="pBar"></div></div>
        <div class="zmmeta">
          <span class="zmsecs" id="pSecs">${sprint.seconds}s</span>
          <span class="zmsolved">Solved: <b id="pSprintSolved">0</b></span>
        </div>
        <div class="pstage" id="pSprintStage">
          <img class="psprite" id="pSprintImg" alt="Mystery Pokémon silhouette" style="visibility:hidden" />
        </div>
        <input class="pguess" id="pSprintInput" type="text" autocomplete="off"
               autocapitalize="off" spellcheck="false" placeholder="Type a Pokémon name…" />
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
    <div class="poke-head">
      <h1>❓ Pokémon Sprint <span class="balancechip" id="pageBal">${coins(res.balance)}</span></h1>
    </div>
    <div class="poke-wrap">
      <div class="card pcard zmresult">
        <div class="zmbig">${num(res.correct)} correct</div>
        <div class="zmreward${REDUCE ? "" : " pop"}">+${num(res.coins)} 🪙</div>
        <p class="muted">Balance: <b>${coins(res.balance)}</b></p>
        ${newBest}${bestLine}${rankLine}
        <button class="btn primary big" id="pSprintStart">Play again</button>
      </div>
    </div>
    <div class="zmleaderboard" id="pLeaderboard"></div>`;
  loadSprintLeaderboard();
}

// ── Sprint leaderboard ──
async function loadSprintLeaderboard() {
  const res = await getJSON("/api/v1/arcade/pokemon/sprint/leaderboard");
  if (!res || res._status || !res.top) { renderSprintLeaderboard({ top: [] }); return; }
  renderSprintLeaderboard(res);
}

function renderSprintLeaderboard(res) {
  const sec = $("pLeaderboard");
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
    return { game: "whosthat", duration: 120, top: [
      { rank: 1, name: "davidj", score: 19, runs: 3, me: true },
      { rank: 2, name: "steph", score: 14, runs: 2 },
      { rank: 3, name: "nova", score: 11, runs: 1 },
    ] };
  return {};
}

const MOCK_SPRITE = "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/other/official-artwork/25.png";
function mockPokemon(path, body) {
  if (path === "/new") {
    return { token: "mock-token", sprite: MOCK_SPRITE, gen: 1 };
  }
  const solved = {
    dex: 25, name: "pikachu", types: ["electric"], gen: 1, legendary: false, sprite: MOCK_SPRITE,
  };
  if (path === "/reveal") return { correct: false, gaveup: true, ...solved };
  // /guess
  const g = String(body && body.guess || "").trim().toLowerCase();
  if (g === "pikachu") {
    const reward = 120;
    state.balance += reward;
    return { correct: true, reward, balance: state.balance, ...solved };
  }
  return { correct: false };
}

const MOCK_SPRINT_NAMES = ["pikachu", "charmander", "squirtle", "bulbasaur", "eevee"];
function mockSprint(path, body) {
  if (path === "/start") {
    const problems = [];
    for (let i = 0; i < 60; i++) problems.push({ image: MOCK_SPRITE });
    mockSprint._names = Array.from({ length: 60 }, () =>
      MOCK_SPRINT_NAMES[Math.floor(Math.random() * MOCK_SPRINT_NAMES.length)]);
    return { token: "mock", problems, duration: 120 };
  }
  // /submit
  const ans = body.answers || [];
  const names = mockSprint._names || [];
  const correct = names.reduce(
    (n, name, i) => n + (i < ans.length && String(ans[i]).trim().toLowerCase() === name ? 1 : 0), 0);
  const coinsWon = Math.min(correct, 10) * 20; // mirrors pokemon_guess cap (10 rewarded/day)
  state.balance += coinsWon;
  mockSprint._best = Math.max(mockSprint._best || 0, correct);
  return {
    correct, coins: coinsWon, balance: state.balance,
    best: mockSprint._best, is_new_best: correct === mockSprint._best, rank: 1,
  };
}

main();
