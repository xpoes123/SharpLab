// SharpLab HQ — Ultimate Texas Hold'em. Post an ante + matching blind (auto),
// optionally a Trips side bet, then play the street decision tree against the
// house: preflop you may Bet 4× / 3× or Check; on the flop Bet 2× or Check; on
// the river Bet 1× or Fold. Rounds POST to /api/v1/casino/uth/* (session-cookie
// auth). The settle response carries the authoritative new balance, which we
// push into the nav chip + on-page header. Card rendering + coins hub + toast
// are copied verbatim from threecardpoker.js so this page is self-contained.

const app = document.getElementById("app");
const navRight = document.getElementById("navRight");

// ── Helpers (copied verbatim from threecardpoker.js) ──
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

// ── Nav (login / logout) — mirrors threecardpoker.js (reads state.balance) ──
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
const round = { id: null, live: false, busy: false, phase: null, ante: 0, trips: 0, balBefore: 0 };

// ── POST to a UTH endpoint. Returns parsed JSON or {error}. ──
async function postUTH(path, body) {
  if (MOCK) return mockUTH(path, body);
  const r = await fetch("/api/v1/casino/uth" + path, {
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

// ── Toast (copied verbatim from threecardpoker.js) ──
function toast(msg) {
  const t = document.createElement("div");
  t.className = "cardtoast";
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 3200);
}

// ── Coins hub (copied verbatim from threecardpoker.js) ──
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

// ── Bet-input block (ante + optional trips side bet) ──
function betInputs() {
  return `<div class="betrow">
      <div class="betlbl">Ante <span class="betsub">(blind matches automatically)</span></div>
      <div class="betline">
        <input class="betinput" type="number" min="1" max="50000" step="1" value="100"
               id="ante-uth" inputmode="numeric" />
        <div class="chipbtns">
          <button class="chipbtn" data-amt="10">10</button>
          <button class="chipbtn" data-amt="100">100</button>
          <button class="chipbtn" data-amt="1000">1k</button>
          <button class="chipbtn" data-amt="max">Max</button>
        </div>
      </div>
    </div>
    <div class="betrow">
      <div class="betlbl">Trips side bet <span class="betsub">(optional)</span></div>
      <div class="betline">
        <input class="betinput" type="number" min="0" max="50000" step="1" value="0"
               id="trips-uth" inputmode="numeric" />
      </div>
    </div>`;
}
function readAnte() {
  const el = document.getElementById("ante-uth");
  const v = Math.floor(Number(el && el.value));
  return Number.isFinite(v) ? v : 0;
}
function readTrips() {
  const el = document.getElementById("trips-uth");
  const v = Math.floor(Number(el && el.value));
  return Number.isFinite(v) && v > 0 ? v : 0;
}

// ── Card rendering (copied from threecardpoker.js) ──
// Cards are strings like "A♠", "10♥", "K♦". Split into rank + suit; red ♥♦.
function parseCard(card) {
  const s = String(card);
  const suit = s.slice(-1);
  const rank = s.slice(0, -1);
  const red = suit === "♥" || suit === "♦";
  return { rank, suit, red };
}
function cardFace(card, cls) {
  const { rank, suit, red } = parseCard(card);
  const anim = REDUCE ? "" : " " + (cls || "deal");
  return `<div class="uthcard${red ? " red" : ""}${anim}">
    <span class="corner tl">${esc(rank)}<i>${esc(suit)}</i></span>
    <span class="pip">${esc(suit)}</span>
    <span class="corner br">${esc(rank)}<i>${esc(suit)}</i></span>
  </div>`;
}
function cardBack() {
  return `<div class="uthcard back${REDUCE ? "" : " deal"}"><span class="backart"></span></div>`;
}
function emptySlot() {
  return `<div class="uthcard empty"></div>`;
}
function renderHand(cards, cls) {
  return cards.map((c) => cardFace(c, cls)).join("");
}
// Community: `cards` are the revealed ones; pad up to 5 with face-down backs.
function renderCommunity(cards, cls) {
  const shown = (cards || []).map((c) => cardFace(c, cls));
  while (shown.length < 5) shown.push(cardBack());
  return shown.join("");
}

// ── UI refs ──
const $ = (id) => document.getElementById(id);

function setBusy(busy) {
  round.busy = busy;
  document.querySelectorAll(".uthactions .btn").forEach((el) => (el.disabled = busy));
  document.querySelectorAll("#ante-uth, #trips-uth, .chipbtn").forEach((el) => (el.disabled = busy || round.live));
}

// ── Actions per phase ──
function actionsFor(phase) {
  if (phase === "preflop")
    return `<button class="btn primary" id="uthBet4">Bet 4×</button>
            <button class="btn" id="uthBet3">Bet 3×</button>
            <button class="btn ghost" id="uthCheck">Check</button>`;
  if (phase === "flop")
    return `<button class="btn primary" id="uthBet2">Bet 2×</button>
            <button class="btn ghost" id="uthCheck">Check</button>`;
  // turn / river — no check allowed
  return `<button class="btn primary" id="uthBet1">Bet 1×</button>
          <button class="btn danger" id="uthFold">Fold</button>`;
}
const PHASE_HINT = {
  preflop: "Bet 4× (or 3×) with a strong hand, or Check to see the flop for free.",
  flop: "Bet 2× if the flop helped, or Check to see the river.",
  turn: "Last decision — Bet 1× to stay in, or Fold and surrender your ante & blind.",
};

// ── Render the dealt / current street state ──
function renderStreet(phase, community) {
  round.phase = phase;
  round.live = true;
  $("uthDealerRank").className = "uthrank hidden";
  $("uthCommunity").innerHTML = renderCommunity(community);
  $("uthBanner").className = "uthbanner";
  $("uthBanner").innerHTML = "";
  $("uthNote").innerHTML = PHASE_HINT[phase] || "";
  $("uthActions").innerHTML = actionsFor(phase);
}

async function renderResolved(res) {
  round.live = false;
  const net = Math.round((res.balance != null ? res.balance : round.balBefore) - round.balBefore);
  applyBalance(res.balance);
  round.id = null;

  // Reveal everything: hole, full board, dealer.
  if (res.hole) $("uthPlayerCards").innerHTML = renderHand(res.hole);
  if (res.community) $("uthCommunity").innerHTML = renderCommunity(res.community, "flip");
  $("uthDealerCards").innerHTML = renderHand(res.dealer || [], "flip");

  // Hand-name pills.
  if (res.player_hand) {
    const prk = $("uthPlayerRank");
    prk.textContent = res.player_hand; prk.className = "uthrank";
  }
  if (res.dealer_hand) {
    const drk = $("uthDealerRank");
    const q = res.dealer_qualifies ? "" : " · didn't qualify";
    drk.textContent = res.dealer_hand + q; drk.className = "uthrank";
  }

  // Banner: WIN / LOSE / PUSH / FOLD driven by net coin change.
  let cls, label;
  if (res.folded) { cls = "lose"; label = "FOLD"; }
  else if (net > 0) { cls = "win"; label = "WIN"; }
  else if (net < 0) { cls = "lose"; label = "LOSE"; }
  else { cls = "push"; label = "PUSH"; }
  let money;
  if (net > 0) money = `+${coins(net)}`;
  else if (net < 0) money = `-${coins(-net)}`;
  else money = "Even";
  $("uthBanner").className = "uthbanner show " + cls;
  $("uthBanner").innerHTML = `<span class="uthbannerlabel">${label}</span>
    <span class="uthbannerpay">${money}</span>`;

  // Matchup + per-bet result lines.
  const parts = [];
  if (res.player_hand && res.dealer_hand) {
    parts.push(`You: <b>${esc(res.player_hand)}</b> · Dealer: <b>${esc(res.dealer_hand)}</b>` +
      (res.dealer_qualifies === false ? ` <span class="noqual">(no qualify)</span>` : ""));
  }
  const lines = (res.lines || []).map((l) => `<span class="resline">${esc(l)}</span>`).join("");
  if (lines) parts.push(`<div class="reslines">${lines}</div>`);
  if (res.payout > 0) parts.push(`<span class="bonus">Returned ${num(res.payout)} 🪙</span>`);
  $("uthNote").innerHTML = parts.join("<br>");

  $("uthActions").innerHTML = `<button class="btn primary" id="uthNew">New hand</button>`;
}

// ── Deal a fresh hand ──
async function deal() {
  if (round.busy) return;
  const ante = readAnte();
  const trips = readTrips();
  if (ante < 1) return toast("Enter an ante of at least 1 coin");
  if (ante > 50000) return toast("Ante can be at most 50,000");
  const upfront = ante * 2 + trips; // ante + blind (=ante) + trips
  if (upfront > Math.floor(state.balance))
    return toast("Not enough coins — you need ante×2 + trips up front");
  round.ante = ante;
  round.trips = trips;
  round.balBefore = state.balance;
  setBusy(true);
  const res = await postUTH("/deal", { ante, trips });
  if (res.error) { setBusy(false); return toast("❌ " + res.error); }
  round.id = res.round_id;
  $("uthPlayerCards").innerHTML = renderHand(res.hole || []);
  $("uthPlayerRank").className = "uthrank hidden";
  $("uthDealerCards").innerHTML = cardBack() + cardBack();
  if (!REDUCE) await delay(150);
  renderStreet("preflop", []);
  setBusy(false);
}

// ── Place the Play bet (goes to showdown → settle) ──
async function placeBet(mult) {
  if (round.busy || !round.live) return;
  setBusy(true);
  const res = await postUTH("/action", { round_id: round.id, action: "bet", mult });
  if (res.error) { setBusy(false); return toast("❌ " + res.error); }
  if (!REDUCE) await delay(160);
  await renderResolved(res);
  setBusy(false);
}

// ── Check to advance a street WITHOUT betting ──
async function check() {
  if (round.busy || !round.live) return;
  setBusy(true);
  const res = await postUTH("/action", { round_id: round.id, action: "check" });
  if (res.error) { setBusy(false); return toast("❌ " + res.error); }
  if (res.done) { await renderResolved(res); setBusy(false); return; }
  if (!REDUCE) await delay(120);
  renderStreet(res.phase, res.community || []);
  setBusy(false);
}

// ── Fold on the river ──
async function fold() {
  if (round.busy || !round.live) return;
  setBusy(true);
  const res = await postUTH("/action", { round_id: round.id, action: "fold" });
  if (res.error) { setBusy(false); return toast("❌ " + res.error); }
  if (!REDUCE) await delay(120);
  await renderResolved(res);
  setBusy(false);
}

// ── Delegated events ──
app.addEventListener("click", (e) => {
  const chip = e.target.closest(".chipbtn");
  if (chip) {
    const inp = $("ante-uth");
    if (inp) inp.value = chip.dataset.amt === "max"
      ? Math.max(0, Math.min(50000, Math.floor(state.balance / 2))) : chip.dataset.amt;
    return;
  }
  if (e.target.closest("#uthDeal")) return deal();
  if (e.target.closest("#uthNew")) return resetToDeal();
  if (e.target.closest("#uthBet4")) return placeBet(4);
  if (e.target.closest("#uthBet3")) return placeBet(3);
  if (e.target.closest("#uthBet2")) return placeBet(2);
  if (e.target.closest("#uthBet1")) return placeBet(1);
  if (e.target.closest("#uthCheck")) return check();
  if (e.target.closest("#uthFold")) return fold();
});
app.addEventListener("keydown", (e) => {
  if ((e.target.id === "ante-uth" || e.target.id === "trips-uth") && e.key === "Enter") {
    e.preventDefault(); deal();
  }
});

// ── Reset the table to the pre-deal (Deal button) state ──
function resetToDeal() {
  round.live = false;
  round.id = null;
  round.phase = null;
  $("uthDealerCards").innerHTML = emptySlot() + emptySlot();
  $("uthDealerRank").className = "uthrank hidden";
  $("uthCommunity").innerHTML = emptySlot() + emptySlot() + emptySlot() + emptySlot() + emptySlot();
  $("uthPlayerCards").innerHTML = emptySlot() + emptySlot();
  $("uthPlayerRank").className = "uthrank hidden";
  $("uthBanner").className = "uthbanner";
  $("uthBanner").innerHTML = "";
  $("uthNote").innerHTML = "";
  $("uthActions").innerHTML = `<button class="btn primary big" id="uthDeal">Deal</button>`;
  setBusy(false);
}

function buildPage() {
  const signedOut = !state.me
    ? `<div class="card" style="margin-bottom:16px;text-align:center">
        <p class="muted" style="margin:0 0 10px">Sign in with Discord to play with your coins.</p>
        <a class="btn" href="/api/v1/auth/discord/login">Sign in with Discord</a></div>`
    : "";
  app.innerHTML = `
    <div class="uth-head">
      <h1>🃏 Ultimate Texas Hold'em <span class="balancechip" id="pageBal">${coins(state.balance)}</span></h1>
      <p>Post an ante and matching blind, then play the board against the house. Bet big early or check to see more cards — the earlier you bet, the more you can put down.</p>
    </div>
    ${signedOut}
    <div class="card uthtable">
      <div class="uthrow">
        <div class="uthrowhead"><span class="uthwho">Dealer</span>
          <span class="uthrank hidden" id="uthDealerRank"></span></div>
        <div class="uthcards" id="uthDealerCards">${emptySlot() + emptySlot()}</div>
      </div>
      <div class="uthrow uthboard">
        <div class="uthrowhead"><span class="uthwho">Board</span></div>
        <div class="uthcards" id="uthCommunity">${emptySlot() + emptySlot() + emptySlot() + emptySlot() + emptySlot()}</div>
      </div>
      <div class="uthbanner" id="uthBanner"></div>
      <div class="uthnote" id="uthNote"></div>
      <div class="uthrow">
        <div class="uthrowhead"><span class="uthwho">You</span>
          <span class="uthrank hidden" id="uthPlayerRank"></span></div>
        <div class="uthcards" id="uthPlayerCards">${emptySlot() + emptySlot()}</div>
      </div>
    </div>
    <div class="card uthcontrols">
      ${betInputs()}
      <div class="uthactions" id="uthActions">
        <button class="btn primary big" id="uthDeal">Deal</button>
      </div>
    </div>
    <div class="card uthrules">
      <h3>How it works</h3>
      <ul>
        <li>Ante + <b>Blind</b> (auto-matched to the ante) are posted up front. Best 5-card hand from your 2 hole cards + 5 community cards wins.</li>
        <li><b>Preflop</b>: Bet 4× or 3× your ante, or <b>Check</b> for free to see the flop.</li>
        <li><b>Flop</b> (3 cards): Bet 2×, or <b>Check</b> to see the river.</li>
        <li><b>River</b> (5 cards): Bet 1×, or <b>Fold</b> and lose your ante & blind.</li>
        <li>Dealer <b>qualifies</b> with a pair or better. If it doesn't, the ante pushes. The Play bet always pays even money on a win. The Blind pays a bonus for premium hands (below).</li>
      </ul>
      <div class="uthpaywrap">
        <table class="uthpay">
          <caption>Blind bonus — on a winning hand</caption>
          <thead><tr><th>Hand</th><th class="pay">Pays</th></tr></thead>
          <tbody>
            <tr><td>Royal Flush</td><td class="pay">500×</td></tr>
            <tr><td>Straight Flush</td><td class="pay">50×</td></tr>
            <tr><td>Four of a Kind</td><td class="pay">10×</td></tr>
            <tr><td>Full House</td><td class="pay">3×</td></tr>
            <tr><td>Flush</td><td class="pay">3:2</td></tr>
            <tr><td>Straight</td><td class="pay">1×</td></tr>
            <tr><td>Less</td><td class="pay">push</td></tr>
          </tbody>
        </table>
        <table class="uthpay">
          <caption>Trips side bet — pays on your hand</caption>
          <thead><tr><th>Hand</th><th class="pay">Pays</th></tr></thead>
          <tbody>
            <tr><td>Royal Flush</td><td class="pay">50×</td></tr>
            <tr><td>Straight Flush</td><td class="pay">40×</td></tr>
            <tr><td>Four of a Kind</td><td class="pay">30×</td></tr>
            <tr><td>Full House</td><td class="pay">8×</td></tr>
            <tr><td>Flush</td><td class="pay">6×</td></tr>
            <tr><td>Straight</td><td class="pay">5×</td></tr>
            <tr><td>Three of a Kind</td><td class="pay">3×</td></tr>
            <tr><td>Less</td><td class="pay">loss</td></tr>
          </tbody>
        </table>
      </div>
    </div>`;
  setBusy(false);
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
// Mock mode (?mock=1): a client-side shoe so the page can be
// exercised offline through the full street decision tree. Hand
// naming/outcome is randomised (not a real 7-card evaluator) —
// the real endpoints win by default.
// ─────────────────────────────────────────────────────────────
const MOCK = new URLSearchParams(location.search).get("mock") === "1";

function mockJSON(url) {
  if (url.startsWith("/api/v1/hq/me"))
    return { authenticated: true, user: { id: "1", username: "davidj", avatar: null }, balance: 12500 };
  return {};
}

const MOCK_RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"];
const MOCK_SUITS = ["♠", "♥", "♦", "♣"];
const MOCK_NAMES = ["High Card", "Pair", "Two Pair", "Three of a Kind", "Straight", "Flush", "Full House"];
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
function mockSettle(g, folded) {
  const ante = g.ante, blind = g.ante, trips = g.trips, play = folded ? 0 : g.play;
  let credit = 0;
  const lines = [];
  const win = !folded && Math.random() > 0.5;
  const tie = false;
  if (folded) { lines.push("Folded — ante & blind lost"); }
  else if (win) {
    credit += play * 2 + ante * 2 + blind;
    lines.push(`Play: win +${play}`, `Ante: win +${ante}`, "Blind: push");
  } else {
    lines.push("Play: loss", "Ante: loss", "Blind: loss");
  }
  if (trips > 0) lines.push("Trips: loss");
  state.balance = Math.max(0, state.balance + credit);
  return {
    done: true, payout: credit, balance: state.balance, lines,
    hole: g.hole, dealer: g.dealer, community: g.community,
    player_hand: MOCK_NAMES[Math.floor(Math.random() * MOCK_NAMES.length)],
    dealer_hand: MOCK_NAMES[Math.floor(Math.random() * MOCK_NAMES.length)],
    dealer_qualifies: Math.random() > 0.3, folded: !!folded,
  };
}
function mockUTH(path, body) {
  if (path === "/deal") {
    const ante = Math.floor(Number(body.ante) || 0);
    const trips = Math.floor(Number(body.trips) || 0);
    const rid = "mock-" + Date.now();
    const hole = [mockDraw(), mockDraw()];
    const dealer = [mockDraw(), mockDraw()];
    const community = [mockDraw(), mockDraw(), mockDraw(), mockDraw(), mockDraw()];
    state.balance = Math.max(0, state.balance - (ante * 2 + trips));
    mockRounds[rid] = { ante, trips, hole, dealer, community, play: 0, phase: "preflop" };
    return { round_id: rid, phase: "preflop", hole, ante, trips };
  }
  const g = mockRounds[body.round_id];
  if (!g) return { error: "no active hand" };
  if (body.action === "bet") {
    const mult = g.phase === "preflop" ? (body.mult === 3 ? 3 : 4) : g.phase === "flop" ? 2 : 1;
    g.play = g.ante * mult;
    state.balance = Math.max(0, state.balance - g.play);
    delete mockRounds[body.round_id];
    return mockSettle(g, false);
  }
  if (body.action === "check") {
    if (g.phase === "turn") return { error: "must bet or fold on the river" };
    g.phase = g.phase === "preflop" ? "flop" : "turn";
    const reveal = g.phase === "flop" ? 3 : 5;
    return { round_id: body.round_id, phase: g.phase, community: g.community.slice(0, reveal) };
  }
  if (body.action === "fold") {
    delete mockRounds[body.round_id];
    return mockSettle(g, true);
  }
  return { error: "unknown action" };
}

main();
