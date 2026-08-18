// SharpLab HQ — Guess the Flag. Name the country from its flag emoji for coins.
// Rounds POST to /api/v1/arcade/geo/* (session-cookie auth); each correct guess
// response carries the authoritative new balance, which we push back into the
// nav chip + on-page header.

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
const state = { me: null, balance: 0, correct: 0 };
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

// ── Delegated events ──
app.addEventListener("click", (e) => {
  if (e.target.closest("#gGuessBtn")) return submitGuess();
  if (e.target.closest("#gHint")) return getHint();
  if (e.target.closest("#gGiveUp")) return giveUp();
  if (e.target.closest("#gNext")) return newRound();
});
app.addEventListener("keydown", (e) => {
  if (e.target.id === "gGuess" && e.key === "Enter") { e.preventDefault(); submitGuess(); }
});

function buildPage() {
  const signedOut = !state.me
    ? `<div class="card" style="max-width:460px;margin:0 auto 16px;text-align:center">
        <p class="muted" style="margin:0 0 10px">Sign in with Discord to play for coins.</p>
        <a class="btn" href="/api/v1/auth/discord/login">Sign in with Discord</a></div>`
    : "";
  app.innerHTML = `
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

main();
