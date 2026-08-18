// SharpLab HQ — Hi-Lo. Guess whether the next card is higher or lower than the
// shown card for coins. Rounds POST to /api/v1/casino/hilo/* (session-cookie
// auth); each resolved response carries the authoritative new balance, which we
// push into the nav chip + on-page header.

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
const round = { id: null, live: false, busy: false, card: null, rank: 0, odds: null, lastBet: 0 };

// ── POST to a hilo endpoint. Returns parsed JSON or {error}. ──
async function postHilo(path, body) {
  if (MOCK) return mockHilo(path, body);
  const r = await fetch("/api/v1/casino/hilo" + path, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  const j = await r.json().catch(() => ({}));
  if (r.ok) return j;
  return { error: j.error || (r.status === 401 ? "sign in to play" : `error ${r.status}`) };
}

function applyBalance(bal) {
  if (bal == null) return;
  state.balance = bal;
  renderNav(state.me && state.me.user);
  const pb = document.getElementById("pageBal");
  if (pb) pb.textContent = coins(bal);
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
const delay = (ms) => new Promise((r) => setTimeout(r, ms));

// ── Shared bet-input block (copied from casino.js) ──
function betInput() {
  return `<div class="betrow">
    <div class="betlbl">Bet</div>
    <div class="betline">
      <input class="betinput" type="number" min="1" step="1" value="100"
             id="bet-hl" inputmode="numeric" />
      <div class="chipbtns">
        <button class="chipbtn" data-amt="10">10</button>
        <button class="chipbtn" data-amt="100">100</button>
        <button class="chipbtn" data-amt="1000">1k</button>
        <button class="chipbtn" data-amt="max">Max</button>
      </div>
    </div>
  </div>`;
}
function readBet() {
  const el = document.getElementById("bet-hl");
  const v = Math.floor(Number(el && el.value));
  return Number.isFinite(v) ? v : 0;
}

// ── Card rendering ──
// Cards are strings like "A♠", "10♥", "K♦". Split into rank + suit; red for ♥♦.
function parseCard(card) {
  const s = String(card);
  const suit = s.slice(-1);
  const rank = s.slice(0, -1);
  const red = suit === "♥" || suit === "♦";
  return { rank, suit, red };
}
function cardFace(card) {
  const { rank, suit, red } = parseCard(card);
  return `<div class="hlcard${red ? " red" : ""}${REDUCE ? "" : " deal"}">
    <span class="corner tl">${esc(rank)}<i>${esc(suit)}</i></span>
    <span class="pip">${esc(suit)}</span>
    <span class="corner br">${esc(rank)}<i>${esc(suit)}</i></span>
  </div>`;
}
function cardBack() {
  return `<div class="hlcard back${REDUCE ? "" : " deal"}"><span class="backart"></span></div>`;
}

// ── UI refs ──
const $ = (id) => document.getElementById(id);

function setBusy(busy) {
  round.busy = busy;
  document.querySelectorAll(".hlactions .btn, .hlguess .btn").forEach((el) => (el.disabled = busy));
}

// ── Show the current card + higher/lower guess buttons ──
function renderLive(res) {
  round.id = res.round_id;
  round.live = true;
  round.card = res.card;
  round.rank = res.rank;
  round.odds = res.odds || {};
  $("hlCurCard").innerHTML = cardFace(res.card);
  $("hlNextCard").innerHTML = cardBack();
  $("hlBanner").className = "hlbanner";
  $("hlBanner").innerHTML = "";
  const hi = (res.odds && res.odds.higher) || { count: 0, mult: 0 };
  const lo = (res.odds && res.odds.lower) || { count: 0, mult: 0 };
  const hiDis = hi.count > 0 ? "" : " disabled";
  const loDis = lo.count > 0 ? "" : " disabled";
  $("hlActions").innerHTML = `<div class="hlguess">
    <button class="btn hlbtn hlhigher" id="hlHigher"${hiDis}>Higher ▲
      <span class="hlmult">×${num(hi.mult)}</span></button>
    <button class="btn hlbtn hllower" id="hlLower"${loDis}>Lower ▼
      <span class="hlmult">×${num(lo.mult)}</span></button>
  </div>`;
}

// ── Reveal the next card + win/lose banner ──
async function renderResolved(res, bet) {
  round.live = false;
  round.id = null;
  applyBalance(res.balance);
  $("hlNextCard").innerHTML = cardFace(res.card);
  const won = !!res.won;
  const payout = res.payout || 0;
  const staked = bet || 0;
  const line = won ? `+${coins(payout)}` : `-${coins(staked)}`;
  $("hlBanner").className = "hlbanner show " + (won ? "win" : "lose");
  $("hlBanner").innerHTML = `<span class="hlbannerlabel">${won ? "WIN" : "LOSE"}</span>
    <span class="hlbannerpay">${line}</span>`;
  $("hlActions").innerHTML = `<button class="btn primary big" id="hlNew">Deal again</button>`;
}

// ── Deal a fresh card ──
async function deal() {
  if (round.busy) return;
  const bet = readBet();
  if (bet < 1) return toast("Enter a bet of at least 1 coin");
  if (bet > Math.floor(state.balance)) return toast("You don't have enough coins for that bet");
  round.lastBet = bet;
  setBusy(true);
  const res = await postHilo("/deal", { bet });
  if (res.error) { setBusy(false); return toast("❌ " + res.error); }
  if (!REDUCE) await delay(150);
  renderLive(res);
  setBusy(false);
}

// ── Make a higher/lower guess ──
async function guess(direction) {
  if (round.busy || !round.live) return;
  setBusy(true);
  const res = await postHilo("/guess", { round_id: round.id, direction });
  if (res.error) { setBusy(false); return toast("❌ " + res.error); }
  if (!REDUCE) await delay(150);
  await renderResolved(res, round.lastBet);
  setBusy(false);
}

// ── Delegated events ──
app.addEventListener("click", (e) => {
  const chip = e.target.closest(".chipbtn");
  if (chip) {
    const inp = $("bet-hl");
    if (inp) inp.value = chip.dataset.amt === "max"
      ? Math.max(0, Math.floor(state.balance)) : chip.dataset.amt;
    return;
  }
  if (e.target.closest("#hlDeal")) return deal();
  if (e.target.closest("#hlNew")) return resetToDeal();
  if (e.target.closest("#hlHigher")) return guess("higher");
  if (e.target.closest("#hlLower")) return guess("lower");
});
app.addEventListener("keydown", (e) => {
  if (e.target.id === "bet-hl" && e.key === "Enter") { e.preventDefault(); deal(); }
});

// ── Reset the table to the pre-deal (Deal button) state ──
function resetToDeal() {
  round.live = false;
  round.id = null;
  $("hlCurCard").innerHTML = `<div class="hlcard empty"></div>`;
  $("hlNextCard").innerHTML = `<div class="hlcard empty"></div>`;
  $("hlBanner").className = "hlbanner";
  $("hlBanner").innerHTML = "";
  $("hlActions").innerHTML = `<button class="btn primary big" id="hlDeal">Deal</button>`;
}

function buildPage() {
  const signedOut = !state.me
    ? `<div class="card" style="margin-bottom:16px;text-align:center">
        <p class="muted" style="margin:0 0 10px">Sign in with Discord to play with your coins.</p>
        <a class="btn" href="/api/v1/auth/discord/login">Sign in with Discord</a></div>`
    : "";
  app.innerHTML = `
    <div class="hl-head">
      <h1>🔺 Hi-Lo <span class="balancechip" id="pageBal">${coins(state.balance)}</span></h1>
      <p>Guess whether the next card is higher or lower. A tie loses. Rarer calls pay more.</p>
    </div>
    ${signedOut}
    <div class="card hltable">
      <div class="hlcards">
        <div class="hlslot">
          <span class="hlslotlbl">Current</span>
          <div id="hlCurCard"><div class="hlcard empty"></div></div>
        </div>
        <span class="hlvs">vs</span>
        <div class="hlslot">
          <span class="hlslotlbl">Next</span>
          <div id="hlNextCard"><div class="hlcard empty"></div></div>
        </div>
      </div>
      <div class="hlbanner" id="hlBanner"></div>
    </div>
    <div class="card hlcontrols">
      ${betInput()}
      <div class="hlactions" id="hlActions">
        <button class="btn primary big" id="hlDeal">Deal</button>
      </div>
    </div>`;
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
// Mock mode (?mock=1): draws cards + computes odds/results locally
// (same rules as the backend) so the page can be screenshot-tested
// offline. Real endpoints win by default.
// ─────────────────────────────────────────────────────────────
const MOCK = new URLSearchParams(location.search).get("mock") === "1";

function mockJSON(url) {
  if (url.startsWith("/api/v1/hq/me"))
    return { authenticated: true, user: { id: "1", username: "davidj", avatar: null }, balance: 12500 };
  return {};
}

// Rank 2..14 (J=11, Q=12, K=13, A=14). Higher wins if next rank > current; a
// tie loses. mult = round(0.95 * 13 / count, 2) where count = winning ranks.
const MOCK_RANKS = [
  [2, "2"], [3, "3"], [4, "4"], [5, "5"], [6, "6"], [7, "7"], [8, "8"],
  [9, "9"], [10, "10"], [11, "J"], [12, "Q"], [13, "K"], [14, "A"],
];
const MOCK_SUITS = ["♠", "♥", "♦", "♣"];
function mockDraw() {
  const [rank, label] = MOCK_RANKS[Math.floor(Math.random() * MOCK_RANKS.length)];
  const suit = MOCK_SUITS[Math.floor(Math.random() * MOCK_SUITS.length)];
  return { rank, card: label + suit };
}
function mockMult(count) {
  return count > 0 ? Math.round((0.95 * 13 / count) * 100) / 100 : 0;
}
const mockRounds = {};
function mockHilo(path, body) {
  if (path === "/deal") {
    const bet = Math.floor(Number(body.bet) || 0);
    const rid = "mock-" + Date.now();
    const d = mockDraw();
    const higherCount = 14 - d.rank; // ranks strictly greater
    const lowerCount = d.rank - 2;   // ranks strictly lower
    mockRounds[rid] = { bet, rank: d.rank };
    return {
      round_id: rid, card: d.card, rank: d.rank,
      odds: {
        higher: { count: higherCount, mult: mockMult(higherCount) },
        lower: { count: lowerCount, mult: mockMult(lowerCount) },
      },
    };
  }
  // /guess
  const g = mockRounds[body.round_id];
  if (!g) return { error: "round not found" };
  const next = mockDraw();
  const dir = body.direction;
  const won = dir === "higher" ? next.rank > g.rank : next.rank < g.rank; // tie loses
  const count = dir === "higher" ? 14 - g.rank : g.rank - 2;
  const payout = won ? Math.round(g.bet * mockMult(count)) : 0;
  state.balance = Math.max(0, state.balance - g.bet + payout);
  delete mockRounds[body.round_id];
  return {
    card: next.card, rank: next.rank, prev: g.rank,
    won, direction: dir, payout, balance: state.balance,
  };
}

main();
