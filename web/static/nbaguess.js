// SharpLab HQ — NBA Player Guess. Name the mystery NBA player from progressive
// clues (career team history + a headline stat) for coins. Rounds POST to
// /api/v1/arcade/nbaguess/* (session-cookie auth); each correct guess response
// carries the authoritative new balance, which we push back into the nav chip +
// on-page header. Clues arrive all at once but reveal one at a time — a wrong
// guess (or the "Reveal a clue" button) uncovers the next one.

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
const round = { token: null, clues: [], shown: 0, solved: false, busy: false };

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

// ── UI element refs ──
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
  document.querySelectorAll(".nbtns .btn, .nguess").forEach((el) => (el.disabled = busy));
}

// ── POST to an nbaguess endpoint. Returns parsed JSON (with _status on error). ──
async function postNba(path, body) {
  if (MOCK) return mockNba(path, body);
  const r = await fetch("/api/v1/arcade/nbaguess" + path, {
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

// ── Render the revealed clues + the pending count ──
function renderClues() {
  const list = $("nClues");
  if (!list) return;
  const items = round.clues.slice(0, round.shown).map(
    (c, i) => `<li class="nclue${i === round.shown - 1 ? " fresh" : ""}">
      <span class="nclue-n">${i + 1}</span><span class="nclue-t">${esc(c)}</span></li>`
  ).join("");
  list.innerHTML = items;
  const left = round.clues.length - round.shown;
  const more = $("nMore");
  if (more) {
    if (left > 0) { more.textContent = `Reveal a clue (${left} left)`; more.disabled = round.busy; more.style.display = ""; }
    else { more.style.display = "none"; }
  }
}

// ── Reveal the next clue (returns false if none left) ──
function revealNextClue() {
  if (round.shown >= round.clues.length) return false;
  round.shown += 1;
  renderClues();
  return true;
}

// ── Start a new round ──
async function newRound() {
  round.solved = false;
  const reveal = $("nReveal");
  const msg = $("nMsg");
  const input = $("nGuess");
  const btnRow = $("nBtns");
  if (reveal) reveal.innerHTML = "";
  if (msg) { msg.textContent = "Who is it? Reveal clues if you're stuck."; msg.className = "nhintmsg idle"; }
  if (input) input.value = "";
  if (btnRow) btnRow.innerHTML =
    `<button class="btn primary big" id="nGuessBtn">Guess</button>
     <button class="btn ghost" id="nGiveUp">Give up</button>`;
  const cluesList = $("nClues");
  if (cluesList) cluesList.innerHTML = "";
  setBusy(true);

  const res = await postNba("/new", {});
  if (res.error || res._status) {
    setBusy(false);
    return toast("❌ " + (res.error || "couldn't start a round"));
  }
  round.token = res.token;
  round.clues = res.clues || [];
  round.shown = 0;
  revealNextClue();  // show the first clue immediately
  setBusy(false);
  if (input) input.focus();
}

// ── Show the solved/revealed state ──
function showReveal(res, solved) {
  round.solved = true;
  const reveal = $("nReveal");
  const btnRow = $("nBtns");
  const msg = $("nMsg");
  // Reveal every remaining clue on game end.
  round.shown = round.clues.length;
  renderClues();
  const rewardLine = solved && res.reward
    ? `<div class="nreward">+${num(res.reward)} 🪙</div>`
    : (solved ? "" : `<div class="ndexline">No coins — better luck next time.</div>`);
  if (reveal) reveal.innerHTML =
    `<div class="nresult ${solved ? "win" : "lose"}">${solved ? "✅ Correct!" : "🏳️ Gave up"}</div>
     <div class="nname">${esc(res.name || "???")}</div>
     ${rewardLine}`;
  if (msg) { msg.textContent = ""; msg.className = "nhintmsg idle"; }
  if (btnRow) btnRow.innerHTML = `<button class="btn primary big" id="nNext">Next player →</button>`;
}

// ── Submit a guess ──
async function submitGuess() {
  if (round.busy || round.solved) return;
  const input = $("nGuess");
  const guess = (input && input.value || "").trim();
  if (!guess) { if (input) input.focus(); return; }
  setBusy(true);
  const res = await postNba("/guess", { token: round.token, guess });
  setBusy(false);
  if (res.error || res._status) {
    return toast("❌ " + (res.error || "something went wrong"));
  }
  if (res.correct) {
    applyBalance(res.balance);
    state.correct += 1;
    const c = $("nCorrect");
    if (c) c.textContent = num(state.correct);
    showReveal(res, true);
    return;
  }
  // Wrong guess — shake, reveal the next clue, keep the round going.
  const revealed = revealNextClue();
  const msg = $("nMsg");
  if (msg) {
    msg.textContent = revealed ? "Not quite — here's another clue" : "Not quite — that's every clue!";
    msg.className = "nhintmsg wrong";
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
  const res = await postNba("/reveal", { token: round.token, guess: "" });
  setBusy(false);
  if (res.error || res._status) {
    return toast("❌ " + (res.error || "something went wrong"));
  }
  showReveal(res, false);
}

// ── Delegated events ──
app.addEventListener("click", (e) => {
  if (e.target.closest("#nGuessBtn")) return submitGuess();
  if (e.target.closest("#nGiveUp")) return giveUp();
  if (e.target.closest("#nNext")) return newRound();
  if (e.target.closest("#nMore")) return revealNextClue();
});
app.addEventListener("keydown", (e) => {
  if (e.target.id === "nGuess" && e.key === "Enter") { e.preventDefault(); submitGuess(); }
});

function buildPage() {
  const signedOut = !state.me
    ? `<div class="card" style="max-width:460px;margin:0 auto 16px;text-align:center">
        <p class="muted" style="margin:0 0 10px">Sign in with Discord to play for coins.</p>
        <a class="btn" href="/api/v1/auth/discord/login">Sign in with Discord</a></div>`
    : "";
  app.innerHTML = `
    <div class="nba-head">
      <h1>🏀 NBA Player Guess <span class="balancechip" id="pageBal">${coins(state.balance)}</span></h1>
      <p>Name the mystery player from their career clues. Fewer clues used, sharper the guess.</p>
    </div>
    ${signedOut}
    <div class="nba-wrap">
      <div class="card ncard">
        <div class="nhint">Mystery Player</div>
        <ul class="nclues" id="nClues"></ul>
        <button class="btn ghost nmore" id="nMore" style="display:none">Reveal a clue</button>
        <div class="nreveal" id="nReveal"></div>
        <div class="nguessrow">
          <input class="nguess" id="nGuess" type="text" autocomplete="off" autocapitalize="words"
                 spellcheck="false" placeholder="Type a player name…" />
        </div>
        <div class="nhintmsg idle" id="nMsg">Who is it? Reveal clues if you're stuck.</div>
        <div class="nbtns" id="nBtns">
          <button class="btn primary big" id="nGuessBtn">Guess</button>
          <button class="btn ghost" id="nGiveUp">Give up</button>
        </div>
        <div class="ncounter">Correct this session: <b id="nCorrect">0</b></div>
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

const MOCK_PLAYERS = [
  {
    name: "LeBron James",
    clues: [
      "Played for 3 franchises between 2003 and 2026.",
      "Suited up for the Cavaliers (2003–2010).",
      "Suited up for the Heat (2010–2014).",
      "Suited up for the Cavaliers (2014–2018).",
      "Suited up for the Lakers (2018–2026).",
      "Career averages: 26.8 PPG, 7.5 RPG, 7.4 APG over 1622 games.",
    ],
  },
  {
    name: "Stephen Curry",
    clues: [
      "Played for 1 franchise between 2009 and 2026.",
      "Suited up for the Warriors (2009–2026).",
      "Career averages: 24.8 PPG, 4.7 RPG, 6.3 APG over 1069 games.",
    ],
  },
];
let _mockIdx = 0;
function mockNba(path, body) {
  if (path === "/new") {
    _mockIdx = Math.floor(Math.random() * MOCK_PLAYERS.length);
    return { token: "mock-token", clues: MOCK_PLAYERS[_mockIdx].clues };
  }
  const answer = MOCK_PLAYERS[_mockIdx].name;
  if (path === "/reveal") return { correct: false, gaveup: true, name: answer };
  // /guess — accept the exact full name (case-insensitive).
  const g = String(body && body.guess || "").trim().toLowerCase();
  if (g === answer.toLowerCase()) {
    const reward = 15;
    state.balance += reward;
    return { correct: true, name: answer, reward, balance: state.balance };
  }
  return { correct: false };
}

main();
