// SharpLab HQ — solo Wordle. Guess the 5-letter word in 6 tries for coins.
// Rounds POST to /api/v1/arcade/wordle/* (session-cookie auth); the solve
// response carries the authoritative new balance, which we push back into the
// nav chip + on-page header.

const app = document.getElementById("app");
const navRight = document.getElementById("navRight");

// ── Helpers (copied verbatim from pokemon.js) ──
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
const state = { me: null, balance: 0, solved: 0 };

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

const $ = (id) => document.getElementById(id);

function applyBalance(bal) {
  if (bal == null) return;
  state.balance = bal;
  renderNav(state.me && state.me.user);
  const pb = $("pageBal");
  if (pb) pb.textContent = coins(bal);
}

// ── POST to a wordle endpoint. Returns parsed JSON (with _status on error). ──
async function postWordle(path, body) {
  if (MOCK) return mockWordle(path, body);
  const r = await fetch("/api/v1/arcade/wordle" + path, {
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

// ── Game constants + per-round state ──
const ROWS = 6, COLS = 5;
const KEY_ROWS = ["QWERTYUIOP", "ASDFGHJKL", "↵ZXCVBNM⌫"];
const round = { token: null, row: 0, col: 0, guesses: [], over: false, busy: false };
const keyState = {}; // letter -> best status seen (correct > present > absent)

function tileAt(r, c) { return document.querySelector(`.wtile[data-r="${r}"][data-c="${c}"]`); }

function setKeyState(letter, status) {
  const rank = { absent: 0, present: 1, correct: 2 };
  const cur = keyState[letter];
  if (cur && rank[cur] >= rank[status]) return;
  keyState[letter] = status;
  document.querySelectorAll(`.wkey[data-k="${letter}"]`).forEach((el) => {
    el.classList.remove("correct", "present", "absent");
    el.classList.add(status);
  });
}

function setMsg(text, cls) {
  const m = $("wMsg");
  if (m) { m.textContent = text || ""; m.className = "wmsg" + (cls ? " " + cls : ""); }
}

function setBusy(busy) {
  round.busy = busy;
  document.querySelectorAll(".wkey").forEach((el) => (el.disabled = busy || round.over));
}

// ── Build a fresh grid + keyboard ──
function renderBoard() {
  let grid = "";
  for (let r = 0; r < ROWS; r++) {
    let row = "";
    for (let c = 0; c < COLS; c++) row += `<div class="wtile" data-r="${r}" data-c="${c}"></div>`;
    grid += `<div class="wrow" data-r="${r}">${row}</div>`;
  }
  const kb = KEY_ROWS.map((rw) => {
    const keys = [...rw].map((ch) => {
      if (ch === "↵") return `<button class="wkey wide" data-k="ENTER">Enter</button>`;
      if (ch === "⌫") return `<button class="wkey wide" data-k="BACK">⌫</button>`;
      return `<button class="wkey" data-k="${ch}">${ch}</button>`;
    }).join("");
    return `<div class="wkrow">${keys}</div>`;
  }).join("");
  return { grid, kb };
}

function newRound() {
  round.token = null; round.row = 0; round.col = 0; round.guesses = [];
  round.over = false;
  for (const k in keyState) delete keyState[k];

  const { grid, kb } = renderBoard();
  $("wGrid").innerHTML = grid;
  $("wKeyboard").innerHTML = kb;
  setMsg("Guess the word — 6 tries.", "");
  $("wReward").innerHTML = "";
  $("wNew").hidden = true;
  setBusy(true);

  postWordle("/new", {}).then((res) => {
    setBusy(false);
    if (res.error || res._status) return toast("❌ " + (res.error || "couldn't start a round"));
    round.token = res.token;
  });
}

// ── Type / delete letters ──
function pushLetter(ch) {
  if (round.over || round.busy || round.col >= COLS) return;
  const t = tileAt(round.row, round.col);
  if (!t) return;
  t.textContent = ch;
  t.classList.add("filled");
  if (!REDUCE) {
    t.classList.remove("pop"); void t.offsetWidth; t.classList.add("pop");
    t.addEventListener("animationend", () => t.classList.remove("pop"), { once: true });
    setTimeout(() => t.classList.remove("pop"), 140);
  }
  round.col++;
}

function popLetter() {
  if (round.over || round.busy || round.col <= 0) return;
  round.col--;
  const t = tileAt(round.row, round.col);
  if (t) { t.textContent = ""; t.classList.remove("filled"); }
}

function shakeRow() {
  const row = document.querySelector(`.wrow[data-r="${round.row}"]`);
  if (!row || REDUCE) return;
  row.classList.remove("shake"); void row.offsetWidth; row.classList.add("shake");
  row.addEventListener("animationend", () => row.classList.remove("shake"), { once: true });
}

function paintRow(r, result) {
  for (let c = 0; c < COLS; c++) {
    const t = tileAt(r, c);
    if (!t) continue;
    t.classList.remove("filled");
    t.classList.add(result[c]);
    setKeyState(t.textContent.toUpperCase(), result[c]);
  }
}

// ── Submit the current row ──
async function submitRow() {
  if (round.over || round.busy) return;
  if (round.col < COLS) { shakeRow(); setMsg("Not enough letters", "lose"); return; }
  if (!round.token) { toast("❌ round not ready"); return; }

  let guess = "";
  for (let c = 0; c < COLS; c++) guess += (tileAt(round.row, c).textContent || "");
  guess = guess.toUpperCase();

  setBusy(true);
  const res = await postWordle("/guess", { token: round.token, guess });
  setBusy(false);

  if (res.error === "not a word") { shakeRow(); setMsg("Not in word list", "lose"); return; }
  if (res.error || res._status) { toast("❌ " + (res.error || "something went wrong")); return; }

  paintRow(round.row, res.result);
  round.guesses.push(guess);

  if (res.solved) {
    round.over = true;
    state.solved += 1;
    const sc = $("wSolved"); if (sc) sc.textContent = num(state.solved);
    applyBalance(res.balance);
    setMsg("Solved it! 🎉", "win");
    if (res.reward) $("wReward").innerHTML = `<div class="wreward">+${num(res.reward)} 🪙</div>`;
    endRound();
    return;
  }

  setMsg("", "");
  round.row++;
  round.col = 0;
  if (round.row >= ROWS) {
    // 6th miss — reveal the answer, no reward.
    const rev = await postWordle("/reveal", { token: round.token });
    round.over = true;
    const answer = (rev && rev.answer) || "?????";
    setMsg(`The word was ${esc(answer.toUpperCase())}`, "lose");
    endRound();
  }
}

function endRound() {
  setBusy(true); // disables keyboard (round.over keeps it off)
  const nb = $("wNew");
  if (nb) nb.hidden = false;
}

// ── Input: physical keyboard + on-screen keys ──
function handleKey(k) {
  if (k === "ENTER") return submitRow();
  if (k === "BACK") return popLetter();
  if (/^[A-Z]$/.test(k)) return pushLetter(k);
}

document.addEventListener("keydown", (e) => {
  if (round.over) return;
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.key === "Enter") { e.preventDefault(); return handleKey("ENTER"); }
  if (e.key === "Backspace") { e.preventDefault(); return handleKey("BACK"); }
  const ch = e.key.toUpperCase();
  if (/^[A-Z]$/.test(ch) && ch.length === 1) { e.preventDefault(); handleKey(ch); }
});

app.addEventListener("click", (e) => {
  const key = e.target.closest(".wkey");
  if (key) return handleKey(key.getAttribute("data-k"));
  if (e.target.closest("#wNew")) return newRound();
});

function buildPage() {
  const signedOut = !state.me
    ? `<div class="card" style="max-width:420px;margin:0 auto 16px;text-align:center">
        <p class="muted" style="margin:0 0 10px">Sign in with Discord to play for coins.</p>
        <a class="btn" href="/api/v1/auth/discord/login">Sign in with Discord</a></div>`
    : "";
  app.innerHTML = `
    <div class="w-head">
      <h1>🟩 Wordle <span class="balancechip" id="pageBal">${coins(state.balance)}</span></h1>
      <p>Guess the hidden 5-letter word in six tries. Solve it to win coins.</p>
    </div>
    ${signedOut}
    <div class="w-wrap">
      <div class="card wcard">
        <div class="wgrid" id="wGrid"></div>
        <div class="wmsg" id="wMsg">Guess the word — 6 tries.</div>
        <div id="wReward"></div>
        <div class="wkeyboard" id="wKeyboard"></div>
        <button class="btn primary wnewbtn" id="wNew" hidden>New game</button>
        <div class="wcounter">Solved this session: <b id="wSolved">0</b></div>
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
// Mock mode (?mock=1): fixed answer "CRANE", no backend, so the
// page can be screenshot-tested offline. Real endpoints win by default.
// ─────────────────────────────────────────────────────────────
const MOCK = new URLSearchParams(location.search).get("mock") === "1";
const MOCK_ANSWER = "CRANE";
const MOCK_ALLOWED = new Set(["CRANE", "SLATE", "AUDIO", "ADIEU", "ROATE", "TRAIN", "MOUSE", "PLANT"]);

function mockJSON(url) {
  if (url.startsWith("/api/v1/hq/me"))
    return { authenticated: true, user: { id: "1", username: "davidj", avatar: null }, balance: 12500 };
  return {};
}

function mockScore(guess, secret) {
  const res = new Array(5).fill("absent");
  const rem = [...secret];
  for (let i = 0; i < 5; i++) if (guess[i] === secret[i]) { res[i] = "correct"; rem[i] = ""; }
  for (let i = 0; i < 5; i++) {
    if (res[i] === "correct") continue;
    const idx = rem.indexOf(guess[i]);
    if (idx !== -1) { res[i] = "present"; rem[idx] = ""; }
  }
  return res;
}

function mockWordle(path, body) {
  if (path === "/new") return { token: "mock-token" };
  if (path === "/reveal") return { answer: MOCK_ANSWER };
  const guess = String(body && body.guess || "").toUpperCase();
  if (guess.length !== 5 || !MOCK_ALLOWED.has(guess)) return { _status: 400, error: "not a word" };
  const result = mockScore(guess, MOCK_ANSWER);
  const solved = guess === MOCK_ANSWER;
  if (!solved) return { result, solved: false };
  const reward = 80;
  state.balance += reward;
  return { result, solved: true, reward, balance: state.balance, answer: MOCK_ANSWER };
}

main();
