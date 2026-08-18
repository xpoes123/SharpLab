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
const state = { me: null, balance: 0, wins: 0 };
const round = {
  token: null,
  guesses: [],       // [{guess:[...], black, white}]
  current: [null, null, null, null],
  selColor: 0,       // palette selection index
  selSlot: 0,        // active slot for cycling
  over: false,
  busy: false,
};

const $ = (id) => document.getElementById(id);

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
  setMsg("Out of guesses — the code was:", "lose");
  showEndPanel(codeHTML);
}

function showEndPanel(extraHTML) {
  const status = $("mStatus");
  if (status) status.textContent = round.over ? "Round over" : "";
  const btns = $("mBtns");
  if (btns) btns.innerHTML = `<button class="btn primary big" id="mNew">New game</button>`;
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

// ── Delegated events ──
app.addEventListener("click", (e) => {
  if (round.busy) return;
  const sw = e.target.closest(".mswatch");
  if (sw) return pickColor(Number(sw.dataset.color));
  const sl = e.target.closest(".mslot");
  if (sl) return pickSlot(Number(sl.dataset.slot));
  if (e.target.closest("#mSubmit")) return submitGuess();
  if (e.target.closest("#mClear")) return clearGuess();
  if (e.target.closest("#mNew")) return newRound();
});
document.addEventListener("keydown", (e) => {
  if (round.over || round.busy) return;
  if (e.key === "Enter") { e.preventDefault(); return submitGuess(); }
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
    ${signedOut}
    <div class="mm-wrap">
      <div class="card mcard">
        <div class="mstatus" id="mStatus">10 guesses</div>
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
// Mock mode (?mock=1): a local secret code + the same feedback
// logic offline, so the page can be screenshot-tested without a
// backend. Real endpoints win by default.
// ─────────────────────────────────────────────────────────────
const MOCK = new URLSearchParams(location.search).get("mock") === "1";
let MOCK_CODE = null;

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
  return {};
}

function mockMM(path, body) {
  if (path === "/new") {
    MOCK_CODE = Array.from({ length: CODE_LEN }, () => COLORS[Math.floor(Math.random() * COLORS.length)]);
    return { token: "mock-token" };
  }
  if (path === "/reveal") return { code: MOCK_CODE || COLORS.slice(0, CODE_LEN) };
  // /guess
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
