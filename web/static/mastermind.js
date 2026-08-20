// SharpLab HQ — Mastermind. Crack a 4-peg / 6-color secret code in 10 guesses.
// Rounds POST to /api/v1/arcade/mastermind/* (session-cookie auth); each solve
// response carries the authoritative new balance, which we push into the nav
// chip + on-page header. The client tracks the guess count; on the 10th
// non-solve it calls /reveal to show the code.

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

// ── Game constants ──
const COLORS = ["red", "orange", "yellow", "green", "blue", "purple"];
const CODE_LEN = 4;
const MAX_GUESSES = 10;

// ── State ──
// mode: "timed" (5-code server-timed gauntlet + leaderboard) or "zen" (the
// original untimed single-code play). Timed is the default.
const state = { me: null, balance: 0, wins: 0, mode: "timed" };
const round = {
  token: null,
  guesses: [],       // [{guess:[...], black, white}]  — history for the CURRENT code
  current: [null, null, null, null],
  selColor: 0,       // palette selection index
  selSlot: 0,        // active slot for cycling
  over: false,
  busy: false,
};
// Timed-run state (parallel to `round`, which still holds the per-code board).
const run = { token: null, index: 0, target: 5, timer: null, startWall: 0 };

const $ = (id) => document.getElementById(id);

// mm:ss from milliseconds.
function fmtTime(ms) {
  const s = Math.max(0, Math.round((ms || 0) / 1000));
  return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
}

// Client-side visual timer (server time is authoritative on `done`).
function startTimer() {
  stopTimer();
  run.startWall = Date.now();
  const t = $("mTimer");
  if (t) { t.hidden = false; t.textContent = "0:00"; }
  run.timer = setInterval(() => {
    const el = $("mTimer");
    if (el) el.textContent = fmtTime(Date.now() - run.startWall);
  }, 250);
}
function stopTimer() {
  if (run.timer) { clearInterval(run.timer); run.timer = null; }
}
function setStatus(text) { const s = $("mStatus"); if (s) s.textContent = text; }

function applyBalance(bal) {
  if (bal == null) return;
  state.balance = bal;
  renderNav(state.me && state.me.user);
  const pb = $("pageBal");
  if (pb) pb.textContent = coins(bal);
}

// ── POST to a mastermind endpoint. Returns parsed JSON (with _status on error). ──
async function postMM(path, body) {
  if (MOCK) return mockMM(path, body);
  const r = await fetch("/api/v1/arcade/mastermind" + path, {
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

// ── Rendering ──
function pegHTML(color) {
  return `<span class="peg ${color ? esc(color) : "empty"}"></span>`;
}

function feedbackHTML(black, white) {
  const dots = [];
  for (let i = 0; i < black; i++) dots.push(`<span class="fbdot black"></span>`);
  for (let i = 0; i < white; i++) dots.push(`<span class="fbdot white"></span>`);
  while (dots.length < CODE_LEN) dots.push(`<span class="fbdot miss"></span>`);
  return `<div class="mrowfb">${dots.join("")}<span class="fbtext">${black}●&nbsp;${white}○</span></div>`;
}

function renderBoard() {
  const rows = [];
  round.guesses.forEach((g, i) => {
    const solved = g.black === CODE_LEN;
    const pegs = g.guess.map(pegHTML).join("");
    rows.push(`<div class="mrow${solved ? " solvedrow" : ""}">
      <span class="mrownum">${i + 1}</span>
      <div class="mrowpegs">${pegs}</div>
      ${feedbackHTML(g.black, g.white)}
    </div>`);
  });
  // pad remaining rows (only when the round is still going)
  if (!round.over) {
    for (let i = round.guesses.length; i < MAX_GUESSES; i++) {
      rows.push(`<div class="mrow pending">
        <span class="mrownum">${i + 1}</span>
        <div class="mrowpegs">${pegHTML(null).repeat(CODE_LEN)}</div>
      </div>`);
    }
  }
  const board = $("mBoard");
  if (board) board.innerHTML = rows.join("");
}

function renderCurrent() {
  const slots = $("mSlots");
  if (slots) {
    slots.innerHTML = round.current.map((c, i) =>
      `<button class="mslot${i === round.selSlot ? " sel" : ""}" data-slot="${i}" aria-label="Slot ${i + 1}">${pegHTML(c)}</button>`
    ).join("");
  }
  const pal = $("mPalette");
  if (pal) {
    pal.innerHTML = COLORS.map((c, i) =>
      `<button class="mswatch${i === round.selColor ? " sel" : ""}" data-color="${i}" title="${c}" aria-label="${c}">${pegHTML(c)}</button>`
    ).join("");
  }
}

function setMsg(text, cls) {
  const m = $("mMsg");
  if (m) { m.textContent = text; m.className = "mmsg " + (cls || "idle"); }
}

function setBusy(busy) {
  round.busy = busy;
  document.querySelectorAll(".mbtns .btn, .mswatch, .mslot").forEach((el) => (el.disabled = busy));
}

// ── Interactions ──
function pickColor(i) {
  round.selColor = i;
  // paint the currently-selected slot with this color
  round.current[round.selSlot] = COLORS[i];
  // advance to next empty slot for fast entry
  const next = round.current.findIndex((c) => c == null);
  if (next !== -1) round.selSlot = next;
  renderCurrent();
}

function pickSlot(i) {
  if (round.current[i] == null) {
    // empty slot: drop the selected palette color
    round.current[i] = COLORS[round.selColor];
    round.selSlot = i;
  } else {
    // filled slot: cycle to the next color
    const cur = COLORS.indexOf(round.current[i]);
    round.current[i] = COLORS[(cur + 1) % COLORS.length];
    round.selSlot = i;
  }
  renderCurrent();
}

function clearGuess() {
  round.current = [null, null, null, null];
  round.selSlot = 0;
  renderCurrent();
}

async function submitGuess() {
  if (round.busy || round.over) return;
  if (round.current.some((c) => c == null)) {
    setMsg("Fill all 4 slots first", "lose");
    return;
  }
  const guess = round.current.slice();
  setBusy(true);
  const res = await postMM("/guess", { token: round.token, guess });
  setBusy(false);
  if (res.error || res._status) {
    return toast("❌ " + (res.error || "something went wrong"));
  }
  round.guesses.push({ guess, black: res.black, white: res.white });
  clearGuess();
  renderBoard();

  if (res.solved) return finishWin(res);
  if (round.guesses.length >= MAX_GUESSES) return finishLose();

  setMsg(`${res.black}● exact · ${res.white}○ misplaced — ${MAX_GUESSES - round.guesses.length} guesses left`, "idle");
}

function finishWin(res) {
  round.over = true;
  applyBalance(res.balance);
  state.wins += 1;
  const w = $("mWins");
  if (w) w.textContent = num(state.wins);
  renderBoard();
  setStatus("Round over");
  setMsg(`🎉 Cracked it in ${round.guesses.length}!`, "win");
  const reward = res.reward || 0;
  showEndPanel(reward
    ? `<div class="mreward${REDUCE ? "" : " pop"}">+${num(reward)} 🪙</div>`
    : `<div class="mcounter">No coins this time — daily cap reached.</div>`);
}

async function finishLose() {
  round.over = true;
  setBusy(true);
  const res = await postMM("/reveal", { token: round.token });
  setBusy(false);
  const codeHTML = (res.code && !res.error)
    ? `<div class="mrow"><span class="mrownum">✔</span><div class="mrowpegs">${res.code.map(pegHTML).join("")}</div></div>`
    : "";
  renderBoard();
  setStatus("Round over");
  setMsg("Out of guesses — the code was:", "lose");
  showEndPanel(codeHTML);
}

function showEndPanel(extraHTML, btnLabel) {
  const btns = $("mBtns");
  if (btns) btns.innerHTML = `<button class="btn primary big" id="mNew">${esc(btnLabel || "New game")}</button>`;
  const end = $("mEnd");
  if (end) end.innerHTML = extraHTML || "";
  // hide the input controls
  const cur = $("mCurrent");
  if (cur) cur.style.display = "none";
}

// ── Start a new round ──
async function newRound() {
  round.guesses = [];
  round.current = [null, null, null, null];
  round.selColor = 0;
  round.selSlot = 0;
  round.over = false;
  const end = $("mEnd");
  if (end) end.innerHTML = "";
  const cur = $("mCurrent");
  if (cur) cur.style.display = "";
  const status = $("mStatus");
  if (status) status.textContent = "10 guesses";
  const btns = $("mBtns");
  if (btns) btns.innerHTML =
    `<button class="btn primary big" id="mSubmit">Submit</button>
     <button class="btn ghost" id="mClear">Clear</button>`;
  setMsg("Pick 4 colors, then Submit", "idle");
  setBusy(true);
  const res = await postMM("/new", {});
  setBusy(false);
  if (res.error || res._status) {
    return toast("❌ " + (res.error || "couldn't start a round"));
  }
  round.token = res.token;
  renderBoard();
  renderCurrent();
}

// ═══════════════════════════════════════════════════════════════════════════
// Timed mode — a server-timed 5-code gauntlet. Reuses the same board/guess
// rendering as Zen; only the request flow and end panels differ.
// ═══════════════════════════════════════════════════════════════════════════

function resetBoardControls() {
  round.guesses = [];
  round.current = [null, null, null, null];
  round.selColor = 0;
  round.selSlot = 0;
  round.over = false;
  const end = $("mEnd");
  if (end) end.innerHTML = "";
  const cur = $("mCurrent");
  if (cur) cur.style.display = "";
  const btns = $("mBtns");
  if (btns) btns.innerHTML =
    `<button class="btn primary big" id="mSubmit">Submit</button>
     <button class="btn ghost" id="mClear">Clear</button>`;
}

// 401 on a timed endpoint → prompt the player to sign in.
function timedSignInPrompt() {
  stopTimer();
  round.over = true;
  setStatus("Timed mode");
  setMsg("Sign in with Discord to play Timed mode.", "lose");
  const cur = $("mCurrent");
  if (cur) cur.style.display = "none";
  const end = $("mEnd");
  if (end) end.innerHTML = "";
  const btns = $("mBtns");
  if (btns) btns.innerHTML =
    `<a class="btn primary big" href="/api/v1/auth/discord/login">Sign in with Discord</a>`;
}

async function startRun() {
  resetBoardControls();
  setMsg("Pick 4 colors, then Submit — go fast!", "idle");
  setStatus("Starting…");
  setBusy(true);
  const res = await postMM("/run/start", {});
  setBusy(false);
  if (res._status === 401 || /sign in/i.test(res.error || "")) return timedSignInPrompt();
  if (res.error || res._status) return toast("❌ " + (res.error || "couldn't start a run"));
  run.token = res.run_token;
  run.index = res.index || 0;
  run.target = res.target || 5;
  setStatus(`Code ${run.index + 1} of ${run.target}`);
  startTimer();
  renderBoard();
  renderCurrent();
}

async function submitGuessTimed() {
  if (round.busy || round.over) return;
  if (round.current.some((c) => c == null)) {
    setMsg("Fill all 4 slots first", "lose");
    return;
  }
  const guess = round.current.slice();
  setBusy(true);
  const res = await postMM("/run/guess", { run_token: run.token, guess });
  if (res._status === 401 || /sign in/i.test(res.error || "")) { setBusy(false); return timedSignInPrompt(); }
  if (res.error || res._status) { setBusy(false); return toast("❌ " + (res.error || "something went wrong")); }

  round.guesses.push({ guess, black: res.black, white: res.white });
  clearGuess();
  renderBoard();

  // Blew the guess budget on this code — run ends with no time.
  if (res.done && res.dnf) { setBusy(false); return finishRunDNF(res); }

  // Cracked the current code.
  if (res.code_solved) {
    if (res.done) { setBusy(false); return finishRun(res); }
    // More codes remain — briefly show the solve, then advance to a fresh grid.
    setMsg("Code cracked! On to the next…", "win");
    setTimeout(() => {
      round.guesses = [];
      round.current = [null, null, null, null];
      round.selSlot = 0;
      run.index = res.index;
      setStatus(`Code ${run.index + 1} of ${run.target}`);
      setMsg("Next code — keep going!", "idle");
      renderBoard();
      renderCurrent();
      setBusy(false);
    }, REDUCE ? 250 : 850);
    return;
  }

  // Wrong guess, still going.
  setBusy(false);
  const left = res.guesses_left != null ? res.guesses_left : (MAX_GUESSES - round.guesses.length);
  setMsg(`${res.black}● exact · ${res.white}○ misplaced — ${left} left`, "idle");
}

function finishRun(res) {
  round.over = true;
  stopTimer();
  applyBalance(res.balance);
  renderBoard();
  const t = $("mTimer");
  if (t && res.elapsed_ms != null) t.textContent = fmtTime(res.elapsed_ms);
  setStatus("Run complete");
  setMsg(`🎉 Solved all ${run.target} codes!`, "win");
  const timeStr = fmtTime(res.elapsed_ms);
  const rankLine = res.rank != null ? `<div class="mrank">#${num(res.rank)} on the board</div>` : "";
  const bestLine = res.is_new_best
    ? `<div class="mbest">🏆 new best!</div>`
    : (res.best_ms != null ? `<div class="mcounter">Your best: ${fmtTime(res.best_ms)}</div>` : "");
  const reward = res.reward || 0;
  const rewardLine = reward ? `<div class="mreward${REDUCE ? "" : " pop"}">+${num(reward)} 🪙</div>` : "";
  showEndPanel(
    `<div class="mrunresult">
      <div class="mruntime">Solved ${run.target} in ${timeStr}!</div>
      ${rankLine}${bestLine}${rewardLine}
    </div>`,
    "New run");
  loadLeaderboard();
}

function finishRunDNF(res) {
  round.over = true;
  stopTimer();
  renderBoard();
  setStatus("Run over");
  setMsg("Out of guesses — the code was:", "lose");
  const codeHTML = res.code
    ? `<div class="mrow"><span class="mrownum">✔</span><div class="mrowpegs">${res.code.map(pegHTML).join("")}</div></div>`
    : "";
  showEndPanel(
    `<div class="mrunresult">${codeHTML}<div class="mcounter">No time recorded — start a fresh run.</div></div>`,
    "New run");
}

// ── Leaderboard (timed only) ──
async function loadLeaderboard() {
  const sec = $("mLeaderboard");
  if (!sec) return;
  const res = await getJSON("/api/v1/arcade/mastermind/leaderboard");
  if (!res || res._status || !res.top) { sec.innerHTML = ""; return; }
  renderLeaderboard(res);
}

function renderLeaderboard(res) {
  const sec = $("mLeaderboard");
  if (!sec) return;
  const top = res.top || [];
  if (!top.length) {
    sec.innerHTML = `<div class="mlbhead">🏁 Fastest 5-code runs</div>
      <div class="mlbempty">No runs yet — be the first!</div>`;
    return;
  }
  const rows = top.map((r) => {
    const time = r.time != null ? esc(r.time) : fmtTime(r.best_ms);
    const runs = r.runs != null ? `<span class="mlbruns">${num(r.runs)} run${r.runs === 1 ? "" : "s"}</span>` : "";
    return `<div class="mlbrow${r.me ? " me" : ""}">
      <span class="mlbrank">#${num(r.rank)}</span>
      <span class="mlbname">${esc(r.name)}</span>
      ${runs}
      <span class="mlbtime">${time}</span>
    </div>`;
  }).join("");
  sec.innerHTML = `<div class="mlbhead">🏁 Fastest 5-code runs</div>
    <div class="mlblist">${rows}</div>`;
}

// ── Mode dispatch ──
function doSubmit() { return state.mode === "timed" ? submitGuessTimed() : submitGuess(); }
function doNew() { return state.mode === "timed" ? startRun() : newRound(); }

function applyModeChrome() {
  const t = $("mTimer");
  if (t) { t.hidden = state.mode !== "timed"; t.textContent = "0:00"; }
  const lb = $("mLeaderboard");
  if (lb) lb.hidden = state.mode !== "timed";
  document.querySelectorAll(".mmode").forEach((b) =>
    b.classList.toggle("sel", b.dataset.mode === state.mode));
}

function startMode() {
  stopTimer();
  applyModeChrome();
  if (state.mode === "timed") { startRun(); loadLeaderboard(); }
  else { newRound(); }
}

function setMode(mode) {
  if (mode === state.mode || round.busy) return;
  state.mode = mode;
  startMode();
}

// ── Delegated events ──
app.addEventListener("click", (e) => {
  const mp = e.target.closest(".mmode");
  if (mp) return setMode(mp.dataset.mode);
  if (round.busy) return;
  const sw = e.target.closest(".mswatch");
  if (sw) return pickColor(Number(sw.dataset.color));
  const sl = e.target.closest(".mslot");
  if (sl) return pickSlot(Number(sl.dataset.slot));
  if (e.target.closest("#mSubmit")) return doSubmit();
  if (e.target.closest("#mClear")) return clearGuess();
  if (e.target.closest("#mNew")) return doNew();
});
document.addEventListener("keydown", (e) => {
  if (round.over || round.busy) return;
  if (e.key === "Enter") { e.preventDefault(); return doSubmit(); }
  const n = Number(e.key);
  if (n >= 1 && n <= COLORS.length) { e.preventDefault(); return pickColor(n - 1); }
  if (e.key === "Backspace") { e.preventDefault(); return clearGuess(); }
});

function buildPage() {
  const signedOut = !state.me
    ? `<div class="card" style="max-width:460px;margin:0 auto 16px;text-align:center">
        <p class="muted" style="margin:0 0 10px">Sign in with Discord to play for coins.</p>
        <a class="btn" href="/api/v1/auth/discord/login">Sign in with Discord</a></div>`
    : "";
  app.innerHTML = `
    <div class="mm-head">
      <h1>🎯 Mastermind <span class="balancechip" id="pageBal">${coins(state.balance)}</span></h1>
      <p>Crack the secret code — 4 pegs, 6 colors, repeats allowed. 10 guesses.
      ● = right color &amp; spot · ○ = right color, wrong spot.</p>
    </div>
    <div class="mmodes" id="mModes">
      <button class="mmode${state.mode === "timed" ? " sel" : ""}" data-mode="timed">⏱ Timed</button>
      <button class="mmode${state.mode === "zen" ? " sel" : ""}" data-mode="zen">🧘 Zen</button>
    </div>
    ${signedOut}
    <div class="mm-wrap">
      <div class="card mcard">
        <div class="mtopbar">
          <div class="mstatus" id="mStatus">10 guesses</div>
          <div class="mtimer" id="mTimer" hidden>0:00</div>
        </div>
        <div class="mboard" id="mBoard"></div>
        <div class="mmsg idle" id="mMsg">Pick 4 colors, then Submit</div>
        <div class="mcurrent" id="mCurrent">
          <div class="mslots" id="mSlots"></div>
          <div class="mpalette" id="mPalette"></div>
        </div>
        <div class="mend" id="mEnd"></div>
        <div class="mbtns" id="mBtns">
          <button class="btn primary big" id="mSubmit">Submit</button>
          <button class="btn ghost" id="mClear">Clear</button>
        </div>
        <div class="mcounter">Solved this session: <b id="mWins">0</b></div>
      </div>
    </div>
    <div class="mleaderboard" id="mLeaderboard" hidden></div>`;
  startMode();
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
// Mock mode (?mock=1): a local secret code + the same feedback
// logic offline, so the page can be screenshot-tested without a
// backend. Real endpoints win by default.
// ─────────────────────────────────────────────────────────────
const MOCK = new URLSearchParams(location.search).get("mock") === "1";
let MOCK_CODE = null;
let MOCK_RUN = null;

function mockScore(secret, guess) {
  let black = 0;
  for (let i = 0; i < CODE_LEN; i++) if (secret[i] === guess[i]) black++;
  let white = 0;
  for (const color of COLORS) {
    let inS = 0, inG = 0;
    for (let i = 0; i < CODE_LEN; i++) {
      if (secret[i] === color && guess[i] !== color) inS++;
      if (guess[i] === color && secret[i] !== color) inG++;
    }
    white += Math.min(inS, inG);
  }
  return { black, white };
}

function mockJSON(url) {
  if (url.startsWith("/api/v1/hq/me"))
    return { authenticated: true, user: { id: "1", username: "davidj", avatar: null }, balance: 12500 };
  if (url.includes("/leaderboard"))
    return { game: "mastermind", target: 5, top: [
      { rank: 1, name: "davidj", time: "1:42", best_ms: 102000, runs: 4, me: true },
      { rank: 2, name: "steph", time: "2:05", best_ms: 125000, runs: 2 },
      { rank: 3, name: "nova", time: "2:38", best_ms: 158000, runs: 1 },
    ] };
  return {};
}

function randomCode() {
  return Array.from({ length: CODE_LEN }, () => COLORS[Math.floor(Math.random() * COLORS.length)]);
}

function mockMM(path, body) {
  if (path === "/new") {
    MOCK_CODE = randomCode();
    return { token: "mock-token" };
  }
  if (path === "/reveal") return { code: MOCK_CODE || COLORS.slice(0, CODE_LEN) };
  if (path === "/run/start") {
    MOCK_RUN = { codes: Array.from({ length: 5 }, randomCode), index: 0, guesses: 0, start: Date.now() };
    return { run_token: "mock-run", target: 5, index: 0, max_guesses: MAX_GUESSES };
  }
  if (path === "/run/guess") {
    const r = MOCK_RUN;
    const secret = r.codes[r.index];
    const { black, white } = mockScore(secret, body.guess);
    r.guesses += 1;
    if (black === CODE_LEN) {
      if (r.index >= 4) {
        const elapsed = Date.now() - r.start;
        const reward = 300;
        state.balance += reward;
        return { black, white, code_solved: true, done: true, elapsed_ms: elapsed,
          best_ms: elapsed, is_new_best: true, rank: 1, reward, balance: state.balance,
          index: r.index, target: 5 };
      }
      r.index += 1; r.guesses = 0;
      return { black, white, code_solved: true, index: r.index, target: 5 };
    }
    if (r.guesses >= MAX_GUESSES) return { black, white, done: true, dnf: true, code: secret };
    return { black, white, guesses_left: MAX_GUESSES - r.guesses };
  }
  // /guess (Zen)
  const { black, white } = mockScore(MOCK_CODE, body.guess);
  const solved = black === CODE_LEN;
  if (solved) {
    const reward = 60;
    state.balance += reward;
    return { black, white, solved, reward, balance: state.balance };
  }
  return { black, white, solved };
}

main();
