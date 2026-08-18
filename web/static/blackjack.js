// SharpLab HQ — Blackjack. Play a hand of 21 against the dealer for coins.
// Rounds POST to /api/v1/casino/blackjack/* (session-cookie auth); each resolved
// response carries the authoritative new balance, which we push into the nav
// chip + on-page header.

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
const round = { id: null, live: false, busy: false, canDouble: false };

// ── POST to a blackjack endpoint. Returns parsed JSON or {error}. ──
async function postBlackjack(path, body) {
  if (MOCK) return mockBlackjack(path, body);
  const r = await fetch("/api/v1/casino/blackjack" + path, {
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
             id="bet-bj" inputmode="numeric" />
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
  const el = document.getElementById("bet-bj");
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
  return `<div class="bjcard${red ? " red" : ""}${REDUCE ? "" : " deal"}">
    <span class="corner tl">${esc(rank)}<i>${esc(suit)}</i></span>
    <span class="pip">${esc(suit)}</span>
    <span class="corner br">${esc(rank)}<i>${esc(suit)}</i></span>
  </div>`;
}
function cardBack() {
  return `<div class="bjcard back${REDUCE ? "" : " deal"}"><span class="backart"></span></div>`;
}
function renderHand(cards) {
  return cards.map(cardFace).join("");
}
// Best blackjack total for a set of cards (aces soft-adjusted). Used to show the
// dealer's up-card value while the hand is live.
function cardsTotal(cards) {
  let total = 0, aces = 0;
  for (const c of cards) {
    const r = parseCard(c).rank;
    if (r === "A") { total += 11; aces++; }
    else if (["K", "Q", "J", "10"].includes(r)) total += 10;
    else total += Number(r) || 0;
  }
  while (total > 21 && aces > 0) { total -= 10; aces--; }
  return total;
}

// ── UI refs ──
const $ = (id) => document.getElementById(id);

function setBusy(busy) {
  round.busy = busy;
  document.querySelectorAll(".bjactions .btn").forEach((el) => (el.disabled = busy));
}

// ── Render the current table state ──
function renderLive(res) {
  round.id = res.round_id;
  round.live = true;
  round.canDouble = !!res.can_double;
  const up = res.dealer_up || [];
  $("bjDealerCards").innerHTML = renderHand(up) + cardBack();
  $("bjDealerTotal").textContent = up.length ? cardsTotal(up) + " +" : "?";
  $("bjPlayerCards").innerHTML = renderHand(res.player || []);
  $("bjPlayerTotal").textContent = num(res.player_total);
  $("bjBanner").className = "bjbanner";
  $("bjBanner").innerHTML = "";
  const doubleBtn = round.canDouble
    ? `<button class="btn" id="bjDouble">Double</button>` : "";
  $("bjActions").innerHTML =
    `<button class="btn primary" id="bjHit">Hit</button>
     <button class="btn" id="bjStand">Stand</button>
     ${doubleBtn}`;
}

const RESULT_META = {
  win:       { cls: "win",  label: "WIN" },
  blackjack: { cls: "gold", label: "BLACKJACK" },
  push:      { cls: "push", label: "PUSH" },
  lose:      { cls: "lose", label: "LOSE" },
  bust:      { cls: "lose", label: "BUST" },
};

async function renderResolved(res, bet) {
  round.live = false;
  round.id = null;
  applyBalance(res.balance);
  // Reveal the dealer's full hand.
  const dealerBox = $("bjDealerCards");
  if (dealerBox) {
    dealerBox.innerHTML = renderHand(res.dealer || []);
  }
  $("bjDealerTotal").textContent = num(res.dealer_total);
  $("bjPlayerCards").innerHTML = renderHand(res.player || []);
  $("bjPlayerTotal").textContent = num(res.player_total);

  const meta = RESULT_META[res.result] || { cls: "push", label: String(res.result || "").toUpperCase() };
  const payout = res.payout || 0;
  const staked = bet || 0;
  let line;
  if (res.result === "push") {
    line = `Push — your ${coins(staked)} is returned`;
  } else if (payout > 0) {
    const net = payout - staked;
    line = `+${coins(net > 0 ? net : payout)}`;
  } else {
    line = `-${coins(staked)}`;
  }
  $("bjBanner").className = "bjbanner show " + meta.cls;
  $("bjBanner").innerHTML = `<span class="bjbannerlabel">${meta.label}</span>
    <span class="bjbannerpay">${line}</span>`;
  $("bjActions").innerHTML = `<button class="btn primary" id="bjNew">New hand</button>`;
}

// ── Deal a fresh hand ──
async function deal() {
  if (round.busy) return;
  const bet = readBet();
  if (bet < 1) return toast("Enter a bet of at least 1 coin");
  if (bet > Math.floor(state.balance)) return toast("You don't have enough coins for that bet");
  round.lastBet = bet;
  setBusy(true);
  const res = await postBlackjack("/deal", { bet });
  if (res.error) { setBusy(false); return toast("❌ " + res.error); }
  if (!REDUCE) await delay(150);
  if (res.done) {
    await renderResolved(res, bet);
  } else {
    renderLive(res);
  }
  setBusy(false);
}

// ── Take an action on the live hand ──
async function act(action) {
  if (round.busy || !round.live) return;
  setBusy(true);
  const res = await postBlackjack("/action", { round_id: round.id, action });
  if (res.error) { setBusy(false); return toast("❌ " + res.error); }
  if (!REDUCE) await delay(120);
  if (res.done) {
    await renderResolved(res, round.lastBet);
  } else {
    renderLive(res);
  }
  setBusy(false);
}

// ── Delegated events ──
app.addEventListener("click", (e) => {
  const chip = e.target.closest(".chipbtn");
  if (chip) {
    const inp = $("bet-bj");
    if (inp) inp.value = chip.dataset.amt === "max"
      ? Math.max(0, Math.floor(state.balance)) : chip.dataset.amt;
    return;
  }
  if (e.target.closest("#bjDeal")) return deal();
  if (e.target.closest("#bjNew")) return resetToDeal();
  if (e.target.closest("#bjHit")) return act("hit");
  if (e.target.closest("#bjStand")) return act("stand");
  if (e.target.closest("#bjDouble")) return act("double");
});
app.addEventListener("keydown", (e) => {
  if (e.target.id === "bet-bj" && e.key === "Enter") { e.preventDefault(); deal(); }
});

// ── Reset the table to the pre-deal (Deal button) state ──
function resetToDeal() {
  round.live = false;
  round.id = null;
  $("bjDealerCards").innerHTML = `<div class="bjcard empty"></div><div class="bjcard empty"></div>`;
  $("bjDealerTotal").textContent = "—";
  $("bjPlayerCards").innerHTML = `<div class="bjcard empty"></div><div class="bjcard empty"></div>`;
  $("bjPlayerTotal").textContent = "—";
  $("bjBanner").className = "bjbanner";
  $("bjBanner").innerHTML = "";
  $("bjActions").innerHTML = `<button class="btn primary big" id="bjDeal">Deal</button>`;
}

function buildPage() {
  const signedOut = !state.me
    ? `<div class="card" style="margin-bottom:16px;text-align:center">
        <p class="muted" style="margin:0 0 10px">Sign in with Discord to play with your coins.</p>
        <a class="btn" href="/api/v1/auth/discord/login">Sign in with Discord</a></div>`
    : "";
  app.innerHTML = `
    <div class="bj-head">
      <h1>🃏 Blackjack <span class="balancechip" id="pageBal">${coins(state.balance)}</span></h1>
      <p>Beat the dealer to 21 without busting. Blackjack pays 3:2.</p>
    </div>
    ${signedOut}
    <div class="card bjtable">
      <div class="bjrow">
        <div class="bjrowhead"><span class="bjwho">Dealer</span>
          <span class="bjtotal" id="bjDealerTotal">—</span></div>
        <div class="bjcards" id="bjDealerCards">
          <div class="bjcard empty"></div><div class="bjcard empty"></div>
        </div>
      </div>
      <div class="bjbanner" id="bjBanner"></div>
      <div class="bjrow">
        <div class="bjrowhead"><span class="bjwho">You</span>
          <span class="bjtotal" id="bjPlayerTotal">—</span></div>
        <div class="bjcards" id="bjPlayerCards">
          <div class="bjcard empty"></div><div class="bjcard empty"></div>
        </div>
      </div>
    </div>
    <div class="card bjcontrols">
      ${betInput()}
      <div class="bjactions" id="bjActions">
        <button class="btn primary big" id="bjDeal">Deal</button>
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
// Mock mode (?mock=1): a simple client-side shoe so the page can
// be screenshot-tested offline. Real endpoints win by default.
// ─────────────────────────────────────────────────────────────
const MOCK = new URLSearchParams(location.search).get("mock") === "1";

function mockJSON(url) {
  if (url.startsWith("/api/v1/hq/me"))
    return { authenticated: true, user: { id: "1", username: "davidj", avatar: null }, balance: 12500 };
  return {};
}

// A tiny in-memory blackjack engine for offline demos.
const MOCK_RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"];
const MOCK_SUITS = ["♠", "♥", "♦", "♣"];
let mockShoe = null;
function mockDraw() {
  if (!mockShoe || !mockShoe.length) {
    mockShoe = [];
    for (const r of MOCK_RANKS) for (const s of MOCK_SUITS) mockShoe.push(r + s);
    for (let i = mockShoe.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [mockShoe[i], mockShoe[j]] = [mockShoe[j], mockShoe[i]];
    }
  }
  return mockShoe.pop();
}
const mockRounds = {};
function mockResolve(rid, doubled) {
  const g = mockRounds[rid];
  while (cardsTotal(g.dealer) < 17) g.dealer.push(mockDraw());
  const pt = cardsTotal(g.player), dt = cardsTotal(g.dealer);
  const stake = g.bet * (doubled ? 2 : 1);
  let result, payout;
  if (pt > 21) { result = "bust"; payout = 0; }
  else if (dt > 21 || pt > dt) { result = "win"; payout = stake * 2; }
  else if (pt < dt) { result = "lose"; payout = 0; }
  else { result = "push"; payout = stake; }
  state.balance = Math.max(0, state.balance - stake + payout);
  delete mockRounds[rid];
  return { done: true, result, payout, balance: state.balance,
    dealer: g.dealer, dealer_total: dt, player: g.player, player_total: pt };
}
function mockBlackjack(path, body) {
  if (path === "/deal") {
    const bet = Math.floor(Number(body.bet) || 0);
    const rid = "mock-" + Date.now();
    const player = [mockDraw(), mockDraw()];
    const dealer = [mockDraw(), mockDraw()];
    mockRounds[rid] = { bet, player, dealer };
    const pt = cardsTotal(player);
    if (pt === 21) {
      // Natural blackjack — resolve immediately (2.5×).
      const dt = cardsTotal(dealer);
      const payout = dt === 21 ? bet : Math.round(bet * 2.5);
      const result = dt === 21 ? "push" : "blackjack";
      state.balance = Math.max(0, state.balance - bet + payout);
      delete mockRounds[rid];
      return { done: true, result, payout, balance: state.balance,
        dealer, dealer_total: dt, player, player_total: pt };
    }
    return { round_id: rid, done: false, player, player_total: pt,
      dealer_up: [dealer[0]], can_double: true };
  }
  // /action
  const g = mockRounds[body.round_id];
  if (!g) return { error: "round not found" };
  if (body.action === "hit") {
    g.player.push(mockDraw());
    const pt = cardsTotal(g.player);
    if (pt >= 21) return mockResolve(body.round_id, false);
    return { round_id: body.round_id, done: false, player: g.player,
      player_total: pt, dealer_up: [g.dealer[0]], can_double: false };
  }
  if (body.action === "double") {
    g.player.push(mockDraw());
    return mockResolve(body.round_id, true);
  }
  // stand
  return mockResolve(body.round_id, false);
}

main();
