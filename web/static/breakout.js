// SharpLab HQ — Breakout Blackjack. Bet Player / Dealer (even money); both hands auto-play
// (dealer hits soft 17). Optional 🚀 Breakout side bet on the TOTAL cards dealt across both
// hands. Bets POST to /api/v1/casino/breakout (session-cookie auth); each response carries the
// authoritative new balance, which we push back into the nav chip + page header.
// The hands are decided server-side — the card-by-card reveal here is purely cosmetic.

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
const game = { side: "player", busy: false };

// ── POST to the breakout endpoint. Returns {detail, payout, won, bet, balance} or {error}. ──
async function postBreakout(body) {
  if (MOCK) return mockBreakout(body);
  const r = await fetch("/api/v1/casino/breakout", {
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

// ── Bet-input block (copied from casino.js, parameterized default value) ──
function betInput(g, def) {
  return `<div class="betrow">
    <div class="betlbl">${g === "breakout" ? "🚀 Breakout side bet (optional)" : "Bet"}</div>
    <div class="betline">
      <input class="betinput" type="number" min="0" step="1" value="${def}"
             id="bet-${g}" data-betfor="${g}" inputmode="numeric" />
      <div class="chipbtns">
        <button class="chipbtn" data-chip="${g}" data-amt="10">10</button>
        <button class="chipbtn" data-chip="${g}" data-amt="100">100</button>
        <button class="chipbtn" data-chip="${g}" data-amt="1000">1k</button>
        <button class="chipbtn" data-chip="${g}" data-amt="max">Max</button>
      </div>
    </div>
  </div>`;
}
function readBet(g) {
  const el = document.getElementById("bet-" + g);
  const v = Math.floor(Number(el && el.value));
  return Number.isFinite(v) ? v : 0;
}

// ── Blackjack hand total (mirrors web/breakout.py _hand_total; aces drop 11→1) ──
function handTotal(cards) {
  let total = 0, aces = 0;
  for (const lbl of cards || []) {
    const rank = lbl.slice(0, -1);
    if (rank === "A") { total += 11; aces++; }
    else if (rank === "K" || rank === "Q" || rank === "J" || rank === "10") total += 10;
    else total += Number(rank);
  }
  while (total > 21 && aces) { total -= 10; aces--; }
  return total;
}

// ── Card rendering (♥/♦ are red) ──
const RED_SUITS = new Set(["♥", "♦"]);
function cardFace(label) {
  const suit = label.slice(-1);
  const red = RED_SUITS.has(suit) ? " red" : "";
  return `<div class="pcardface${red}">${esc(label)}</div>`;
}
// Render a hand showing only the first `shown` cards, with the running total of those cards.
function renderHand(which, cards, shown, isWin) {
  const el = document.getElementById(which + "Hand");
  if (!el) return;
  const slice = (cards || []).slice(0, shown);
  const total = slice.length ? handTotal(slice) : null;
  el.className = "hand" + (isWin ? " win" : "");
  const faces = slice.map(cardFace).join("");
  el.innerHTML = `<div class="handlbl">${which === "player" ? "Player" : "Dealer"}</div>
    <div class="handtotal">${total == null ? "—" : total}</div>
    <div class="handcards">${faces}</div>`;
}

const SIDES = [
  ["player", "Player", "2.00×"],
  ["dealer", "Dealer", "2.00×"],
];

// Breakout pay table: total cards -> win odds (X:1). Mirrors web/breakout.py.
const PAYTABLE = [
  ["7 cards", "1:1"],
  ["8 cards", "4:1"],
  ["9 cards", "8:1"],
  ["10 cards", "20:1"],
  ["11 cards", "75:1"],
  ["12+ cards", "150:1"],
];

function buildPage() {
  const signedOut = !state.me
    ? `<div class="card" style="max-width:560px;margin:0 auto 16px;text-align:center">
        <p class="muted" style="margin:0 0 10px">Sign in with Discord to play with your coins.</p>
        <a class="btn" href="/api/v1/auth/discord/login">Sign in with Discord</a></div>`
    : "";
  const sideBtns = SIDES.map(
    ([k, label, pay]) => `<button class="sidebtn${game.side === k ? " on" : ""}" data-side="${k}">
      <span class="sn">${label}</span><span class="sp">${pay}</span></button>`
  ).join("");
  const payRows = PAYTABLE.map(
    ([t, o]) => `<div class="porow"><span class="pot">${t}</span><span class="poo">${o}</span></div>`
  ).join("");
  app.innerHTML = `
    <div class="bac-head">
      <h1>🚀 Breakout Blackjack <span class="balancechip" id="pageBal">${coins(state.balance)}</span></h1>
      <p>Pick Player or Dealer — both hands auto-play (dealer hits soft 17) and the higher total wins at even money. A tie or double-bust pushes. Then chase the 🚀 Breakout side bet on how many cards hit the felt.</p>
    </div>
    ${signedOut}
    <div class="bac-wrap">
      <div class="card bcard">
        <div class="bhead"><h2><span class="gicon">🚀</span> Breakout Blackjack</h2>
          <div class="gpay">Player <b>2×</b> · Dealer <b>2×</b> · Breakout up to <b>150×</b></div></div>
        <div class="felt">
          <div class="hand" id="playerHand">
            <div class="handlbl">Player</div><div class="handtotal">—</div><div class="handcards"></div>
          </div>
          <div class="hand" id="dealerHand">
            <div class="handlbl">Dealer</div><div class="handtotal">—</div><div class="handcards"></div>
          </div>
        </div>
        <div class="paytable">
          <div class="potitle">🚀 Breakout — total cards dealt</div>
          <div class="porows">${payRows}</div>
          <div class="ponote">Fewer than 7 cards loses the side bet.</div>
        </div>
        <div class="sidegroup">
          <div class="sidelbl">Bet on</div>
          <div class="sidebtns bd-sides">${sideBtns}</div>
        </div>
        ${betInput("main", 100)}
        ${betInput("breakout", 0)}
        <button class="btn primary playbtn" id="dealBtn">Deal</button>
        <div class="result idle" id="bacResult">Pick a side and deal.</div>
      </div>
    </div>`;
}

async function deal() {
  if (game.busy) return;
  const bet = readBet("main");
  const bo = Math.max(0, readBet("breakout"));
  if (bet < 1) return toast("Enter a main bet of at least 1 coin");
  game.busy = true;
  const btn = document.getElementById("dealBtn");
  if (btn) btn.disabled = true;
  // Clear the felt while "dealing".
  renderHand("player", [], 0, false);
  renderHand("dealer", [], 0, false);
  const rEl = document.getElementById("bacResult");
  if (rEl) { rEl.className = "result idle"; rEl.textContent = "Dealing…"; }

  const res = await postBreakout({ bet, side: game.side, breakout: bo });
  if (res.error) {
    game.busy = false;
    if (btn) btn.disabled = false;
    if (rEl) { rEl.className = "result idle"; rEl.textContent = "Pick a side and deal."; }
    return toast("❌ " + res.error);
  }
  const d = res.detail || {};
  const player = d.player || [];
  const dealer = d.dealer || [];
  const winner = d.outcome;

  // ── Slow reveal: Player hand card-by-card, then Dealer, running totals update each flip. ──
  if (REDUCE) {
    renderHand("player", player, player.length, false);
    renderHand("dealer", dealer, dealer.length, false);
  } else {
    await delay(220);
    for (let i = 1; i <= player.length; i++) {
      renderHand("player", player, i, false);
      await delay(360);
    }
    for (let i = 1; i <= dealer.length; i++) {
      renderHand("dealer", dealer, i, false);
      await delay(360);
    }
    await delay(180);
  }
  // Final state: highlight the winning hand.
  renderHand("player", player, player.length, winner === "player");
  renderHand("dealer", dealer, dealer.length, winner === "dealer");
  applyBalance(res.balance);

  if (rEl) {
    const net = (res.payout || 0) - (res.bet || 0);
    const push = winner === "push";
    const outLabel = push ? "Push" : winner === "player" ? "Player wins" : "Dealer wins";
    const cardsLine = `${d.total_cards} cards dealt`;
    // Breakout side-bet line.
    let boLine = "";
    if (d.breakout_bet > 0) {
      if (d.breakout_odds > 0) {
        boLine = ` · 🚀 Breakout hit ${d.breakout_odds}:1 (+${coins(d.breakout_payout)})`;
      } else {
        boLine = ` · 🚀 Breakout missed (-${coins(d.breakout_bet)})`;
      }
    }
    let cls, head;
    if (net > 0) {
      cls = "win"; head = `${outLabel} — Win  +${coins(net)}`;
    } else if (net < 0) {
      cls = "loss"; head = `${outLabel} — Loss  ${coins(-net)}`;
    } else {
      cls = "push"; head = push ? `${outLabel} — stake returned` : `${outLabel} — even`;
    }
    rEl.className = "result " + cls;
    rEl.innerHTML = `${esc(head)}<span class="rsub">${esc(cardsLine)}${esc(boLine)}</span>`;
  }
  game.busy = false;
  if (btn) { btn.disabled = false; btn.textContent = "Deal again"; }
}

// ── Delegated events ──
app.addEventListener("click", (e) => {
  const chip = e.target.closest(".chipbtn");
  if (chip) {
    const inp = document.getElementById("bet-" + chip.dataset.chip);
    if (inp) inp.value = chip.dataset.amt === "max" ? Math.max(0, Math.floor(state.balance)) : chip.dataset.amt;
    return;
  }
  const side = e.target.closest(".sidebtn");
  if (side) {
    game.side = side.dataset.side;
    document.querySelectorAll(".sidebtn").forEach((b) => b.classList.toggle("on", b === side));
    return;
  }
  if (e.target.closest("#dealBtn")) deal();
});

async function main() {
  const me = await getJSON("/api/v1/hq/me");
  const loggedIn = me && me.authenticated;
  state.me = loggedIn ? me : null;
  state.balance = loggedIn ? (me.balance || 0) : 0;
  renderNav(loggedIn ? me.user : null);
  buildPage();
}

// ─────────────────────────────────────────────────────────────
// Mock mode (?mock=1): play out both hands client-side with the same
// H17 rules so the page works offline. Real endpoint wins by default.
// ─────────────────────────────────────────────────────────────
const MOCK = new URLSearchParams(location.search).get("mock") === "1";

function mockJSON(url) {
  if (url.startsWith("/api/v1/hq/me"))
    return { authenticated: true, user: { id: "1", username: "davidj", avatar: null }, balance: 12500 };
  return {};
}

const RANKS = [["A", 11], ["2", 2], ["3", 3], ["4", 4], ["5", 5], ["6", 6],
               ["7", 7], ["8", 8], ["9", 9], ["10", 10], ["J", 10], ["Q", 10], ["K", 10]];
const SUITS = ["♠", "♥", "♦", "♣"];
function mockDraw() {
  const [rank] = RANKS[Math.floor(Math.random() * RANKS.length)];
  return rank + SUITS[Math.floor(Math.random() * SUITS.length)];
}
function mockIsSoft(cards) {
  let total = 0, aces = 0;
  for (const lbl of cards) {
    if (lbl.slice(0, -1) === "A") { total += 11; aces++; }
    else if (["K", "Q", "J", "10"].includes(lbl.slice(0, -1))) total += 10;
    else total += Number(lbl.slice(0, -1));
  }
  while (total > 21 && aces) { total -= 10; aces--; }
  return aces > 0;
}
function mockPlayOut() {
  const hand = [mockDraw(), mockDraw()];
  while (true) {
    const t = handTotal(hand);
    if (t < 17 || (t === 17 && mockIsSoft(hand))) { hand.push(mockDraw()); continue; }
    break;
  }
  return hand;
}
function mockWinner(p, d) {
  const pb = p > 21, db = d > 21;
  if (pb && db) return "push";
  if (pb) return "dealer";
  if (db) return "player";
  if (p > d) return "player";
  if (d > p) return "dealer";
  return "push";
}
const MOCK_BO = { 7: 1, 8: 4, 9: 8, 10: 20, 11: 75 };
function mockBreakout(body) {
  const bet = Math.floor(Number(body.bet) || 0);
  const bo = Math.max(0, Math.floor(Number(body.breakout) || 0));
  const side = (body.side === "dealer") ? "dealer" : "player";
  const player = mockPlayOut(), dealer = mockPlayOut();
  const p = handTotal(player), dt = handTotal(dealer);
  const outcome = mockWinner(p, dt);
  const totalCards = player.length + dealer.length;
  const odds = totalCards >= 12 ? 150 : (MOCK_BO[totalCards] || 0);
  let mainPayout = 0;
  if (outcome === side) mainPayout = bet * 2;
  else if (outcome === "push") mainPayout = bet;
  const boPayout = (bo && odds) ? bo * (odds + 1) : 0;
  const payout = mainPayout + boPayout;
  const staked = bet + bo;
  state.balance = Math.max(0, state.balance - staked + payout);
  return {
    detail: {
      player, dealer, player_total: p, dealer_total: dt, outcome, side,
      total_cards: totalCards, breakout_odds: odds, breakout_bet: bo,
      breakout_payout: boPayout, main_payout: mainPayout,
    },
    payout, won: payout > staked, bet: staked, balance: state.balance,
  };
}

main();
