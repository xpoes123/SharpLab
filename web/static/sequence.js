// SharpLab HQ — Sequence (guess the next number). A short integer sequence is shown
// minus its final term; the player names the next term. Rounds POST to
// /api/v1/arcade/sequence/* (session-cookie auth). A correct /guess response carries the
// authoritative new balance, which we push into the nav chip + header. The same round
// stays live on a wrong guess so the player can retry.

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
const state = { me: null, balance: 0, solved: 0 };
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
function renderTiles() {
  const shown = round.terms
    .map((n) => `<div class="sq-tile">${esc(n)}</div>`)
    .join('<div class="sq-arrow">→</div>');
  const arrow = round.terms.length ? '<div class="sq-arrow">→</div>' : "";
  return `${shown}${arrow}<div class="sq-tile mystery" id="sqMystery">?</div>`;
}

// ── Start a new round ──
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
  if (tiles) tiles.innerHTML = renderTiles();
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

// ── Submit a guess ──
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

// ── Give up ──
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

// ── Delegated events ──
app.addEventListener("click", (e) => {
  if (e.target.closest("#sqSubmit")) return submitGuess();
  if (e.target.closest("#sqGiveUp")) return giveUp();
  if (e.target.closest("#sqNext")) return newRound();
});
app.addEventListener("keydown", (e) => {
  if (e.target.id === "sqInput" && e.key === "Enter") { e.preventDefault(); submitGuess(); }
});

function buildPage() {
  const signedOut = !state.me
    ? `<div class="card" style="max-width:520px;margin:0 auto 16px;text-align:center">
        <p class="muted" style="margin:0 0 10px">Sign in with Discord to play for coins.</p>
        <a class="btn" href="/api/v1/auth/discord/login">Sign in with Discord</a></div>`
    : "";
  app.innerHTML = `
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
  return {};
}

const MOCK_TERMS = [3, 7, 11, 15, 19];
const MOCK_ANSWER = 23;
const MOCK_NAME = "Arithmetic +4";
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

main();
