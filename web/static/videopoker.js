// SharpLab HQ — Video Poker (Jacks-or-Better). Deal 5 cards, hold the ones you
// want, draw to replace the rest, get paid on the resulting poker hand.
// Rounds POST to /api/v1/casino/videopoker/* (session-cookie auth); the /draw
// response carries the authoritative new balance, which we push into the nav
// chip + on-page header.

const app = document.getElementById("app");
const navRight = document.getElementById("navRight");

// ── Helpers (copied verbatim from casino.js / blackjack.js) ──
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
// phase: "deal" (nothing dealt), "draw" (5 cards up, choose holds), "done".
const round = { id: null, phase: "deal", busy: false, hand: [], hold: [false, false, false, false, false], lastBet: 0 };

// ── POST to a videopoker endpoint. Returns parsed JSON or {error}. ──
async function postVP(path, body) {
  if (MOCK) return mockVP(path, body);
  const r = await fetch("/api/v1/casino/videopoker" + path, {
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
             id="bet-vp" inputmode="numeric" />
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
  const el = document.getElementById("bet-vp");
  const v = Math.floor(Number(el && el.value));
  return Number.isFinite(v) ? v : 0;
}

// ── Paytable (per unit bet) ──
const PAYTABLE = [
  ["Royal Flush", 800],
  ["Straight Flush", 50],
  ["Four of a Kind", 25],
  ["Full House", 9],
  ["Flush", 6],
  ["Straight", 4],
  ["Three of a Kind", 3],
  ["Two Pair", 2],
  ["Jacks or Better", 1],
];

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
  return `<div class="vpcard${red ? " red" : ""}${REDUCE ? "" : " deal"}">
    <span class="corner tl">${esc(rank)}<i>${esc(suit)}</i></span>
    <span class="pip">${esc(suit)}</span>
    <span class="corner br">${esc(rank)}<i>${esc(suit)}</i></span>
  </div>`;
}
// Render all 5 slots (card + HOLD badge). `interactive` enables toggling.
function renderHand() {
  return round.hand.map((c, i) => {
    const held = round.hold[i];
    return `<div class="vpslot${held ? " held" : ""}" data-idx="${i}">
      ${cardFace(c)}
      <div class="holdbadge">Hold</div>
    </div>`;
  }).join("");
}

// ── UI refs ──
const $ = (id) => document.getElementById(id);

function setBusy(busy) {
  round.busy = busy;
  document.querySelectorAll(".vpactions .btn").forEach((el) => (el.disabled = busy));
}

// Highlight the winning row in the paytable reference panel (by label).
function markPaytable(label) {
  document.querySelectorAll(".ptrow").forEach((el) => {
    el.classList.toggle("on", label && el.dataset.name === label);
  });
}

// ── Render the 5 dealt cards (draw phase — cards clickable to hold) ──
function renderDealt() {
  round.phase = "draw";
  $("vpCards").innerHTML = renderHand();
  $("vpBanner").className = "vpbanner";
  $("vpBanner").innerHTML = "";
  $("vpHint").textContent = "Click cards to HOLD, then Draw.";
  markPaytable(null);
  $("vpActions").innerHTML = `<button class="btn primary big" id="vpDraw">Draw</button>`;
}

// ── Render the resolved hand + win banner ──
function renderResolved(res) {
  round.phase = "done";
  round.id = null;
  applyBalance(res.balance);
  $("vpCards").innerHTML = renderHand();
  const payout = res.payout || 0;
  const label = res.label || "No Win";
  const win = payout > 0;
  $("vpBanner").className = "vpbanner show " + (win ? "win" : "none");
  $("vpBanner").innerHTML = `<span class="vpbannerlabel">${esc(label)}</span>
    <span class="vpbannerpay">${win ? "+" + coins(payout) : "—"}</span>`;
  $("vpHint").textContent = "";
  markPaytable(win ? label : null);
  $("vpActions").innerHTML = `<button class="btn primary big" id="vpNew">Deal again</button>`;
}

// ── Deal a fresh hand ──
async function deal() {
  if (round.busy) return;
  const bet = readBet();
  if (bet < 1) return toast("Enter a bet of at least 1 coin");
  if (bet > Math.floor(state.balance)) return toast("You don't have enough coins for that bet");
  round.lastBet = bet;
  round.hold = [false, false, false, false, false];
  setBusy(true);
  const res = await postVP("/deal", { bet });
  if (res.error) { setBusy(false); return toast("❌ " + res.error); }
  round.id = res.round_id;
  round.hand = res.hand || [];
  if (!REDUCE) await delay(150);
  renderDealt();
  setBusy(false);
}

// ── Draw: replace the non-held cards ──
async function draw() {
  if (round.busy || round.phase !== "draw") return;
  setBusy(true);
  const res = await postVP("/draw", { round_id: round.id, hold: round.hold });
  if (res.error) { setBusy(false); return toast("❌ " + res.error); }
  round.hand = res.hand || [];
  round.hold = [false, false, false, false, false];
  if (!REDUCE) await delay(150);
  renderResolved(res);
  setBusy(false);
}

// ── Toggle a HOLD on a card (only during the draw phase) ──
function toggleHold(idx) {
  if (round.phase !== "draw" || round.busy) return;
  round.hold[idx] = !round.hold[idx];
  const slot = document.querySelector(`.vpslot[data-idx="${idx}"]`);
  if (slot) slot.classList.toggle("held", round.hold[idx]);
}

// ── Delegated events ──
app.addEventListener("click", (e) => {
  const chip = e.target.closest(".chipbtn");
  if (chip) {
    const inp = $("bet-vp");
    if (inp) inp.value = chip.dataset.amt === "max"
      ? Math.max(0, Math.floor(state.balance)) : chip.dataset.amt;
    return;
  }
  const slot = e.target.closest(".vpslot");
  if (slot) return toggleHold(Number(slot.dataset.idx));
  if (e.target.closest("#vpDeal")) return deal();
  if (e.target.closest("#vpNew")) return resetToDeal();
  if (e.target.closest("#vpDraw")) return draw();
});
app.addEventListener("keydown", (e) => {
  if (e.target.id === "bet-vp" && e.key === "Enter") { e.preventDefault(); deal(); }
});

// ── Reset the table to the pre-deal (Deal button) state ──
function resetToDeal() {
  round.phase = "deal";
  round.id = null;
  round.hand = [];
  round.hold = [false, false, false, false, false];
  $("vpCards").innerHTML =
    `<div class="vpslot"><div class="vpcard empty"></div><div class="holdbadge"></div></div>`.repeat(5);
  $("vpBanner").className = "vpbanner";
  $("vpBanner").innerHTML = "";
  $("vpHint").textContent = "Set a bet and deal five cards.";
  markPaytable(null);
  $("vpActions").innerHTML = `<button class="btn primary big" id="vpDeal">Deal</button>`;
}

function paytablePanel() {
  const rows = PAYTABLE.map(
    ([name, pay]) => `<div class="ptrow" data-name="${esc(name)}">
      <span class="ptname">${esc(name)}</span><span class="ptpay">${pay}×</span></div>`
  ).join("");
  return `<div class="card paytable">
    <h2>Paytable</h2>
    ${rows}
    <div class="ptfoot">Payouts are multiples of your bet. Pair must be Jacks or better to pay.</div>
  </div>`;
}

function buildPage() {
  const signedOut = !state.me
    ? `<div class="card" style="margin-bottom:16px;text-align:center">
        <p class="muted" style="margin:0 0 10px">Sign in with Discord to play with your coins.</p>
        <a class="btn" href="/api/v1/auth/discord/login">Sign in with Discord</a></div>`
    : "";
  app.innerHTML = `
    <div class="vp-head">
      <h1>🂡 Video Poker <span class="balancechip" id="pageBal">${coins(state.balance)}</span></h1>
      <p>Jacks or Better. Deal five, hold the keepers, draw for the best poker hand.</p>
    </div>
    ${signedOut}
    <div class="vp-layout">
      <div class="vp-main">
        <div class="card vptable">
          <div class="vpcards" id="vpCards">
            ${`<div class="vpslot"><div class="vpcard empty"></div><div class="holdbadge"></div></div>`.repeat(5)}
          </div>
          <div class="vpbanner" id="vpBanner"></div>
          <p class="vphint" id="vpHint">Set a bet and deal five cards.</p>
        </div>
        <div class="card vpcontrols">
          ${betInput()}
          <div class="vpactions" id="vpActions">
            <button class="btn primary big" id="vpDeal">Deal</button>
          </div>
        </div>
      </div>
      ${paytablePanel()}
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
// Mock mode (?mock=1): a client-side shuffled 52-card deck plus a
// local Jacks-or-Better evaluator so the page can be screenshot-
// tested offline. Real endpoints win by default.
// ─────────────────────────────────────────────────────────────
const MOCK = new URLSearchParams(location.search).get("mock") === "1";

function mockJSON(url) {
  if (url.startsWith("/api/v1/hq/me"))
    return { authenticated: true, user: { id: "1", username: "davidj", avatar: null }, balance: 12500 };
  return {};
}

const MOCK_RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"];
const MOCK_SUITS = ["♠", "♥", "♦", "♣"];
const RANK_VALUE = Object.fromEntries(MOCK_RANKS.map((r, i) => [r, i + 2])); // 2..14

function mockShuffle() {
  const deck = [];
  for (const r of MOCK_RANKS) for (const s of MOCK_SUITS) deck.push(r + s);
  for (let i = deck.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [deck[i], deck[j]] = [deck[j], deck[i]];
  }
  return deck;
}

// Evaluate a 5-card hand → { label, category, mult }. Jacks-or-Better rules.
function evaluate(hand) {
  const parsed = hand.map(parseCard);
  const vals = parsed.map((p) => RANK_VALUE[p.rank]).sort((a, b) => a - b);
  const suits = parsed.map((p) => p.suit);
  const flush = suits.every((s) => s === suits[0]);

  // Straight: 5 distinct consecutive ranks, or the wheel A-2-3-4-5.
  const uniq = [...new Set(vals)];
  let straight = false;
  if (uniq.length === 5) {
    if (vals[4] - vals[0] === 4) straight = true;
    // wheel: A(14),2,3,4,5
    if (vals[0] === 2 && vals[1] === 3 && vals[2] === 4 && vals[3] === 5 && vals[4] === 14) straight = true;
  }

  // Rank counts.
  const counts = {};
  for (const v of vals) counts[v] = (counts[v] || 0) + 1;
  const groups = Object.entries(counts).sort((a, b) => b[1] - a[1] || b[0] - a[0]);
  const shape = groups.map((g) => g[1]).join(""); // e.g. "41", "32", "311", "221"
  const isRoyal = flush && straight && vals[0] === 10;

  let label, mult;
  if (isRoyal) { label = "Royal Flush"; mult = 800; }
  else if (flush && straight) { label = "Straight Flush"; mult = 50; }
  else if (shape.startsWith("4")) { label = "Four of a Kind"; mult = 25; }
  else if (shape === "32") { label = "Full House"; mult = 9; }
  else if (flush) { label = "Flush"; mult = 6; }
  else if (straight) { label = "Straight"; mult = 4; }
  else if (shape.startsWith("3")) { label = "Three of a Kind"; mult = 3; }
  else if (shape === "221") { label = "Two Pair"; mult = 2; }
  else if (groups[0][1] === 2 && Number(groups[0][0]) >= 11) { label = "Jacks or Better"; mult = 1; }
  else { label = "No Win"; mult = 0; }
  return { label, category: label, mult };
}

const mockRounds = {};
function mockVP(path, body) {
  if (path === "/deal") {
    const bet = Math.floor(Number(body.bet) || 0);
    const deck = mockShuffle();
    const hand = deck.splice(0, 5);
    const rid = "mock-" + Date.now();
    mockRounds[rid] = { bet, deck };
    return { round_id: rid, hand };
  }
  // /draw
  const g = mockRounds[body.round_id];
  if (!g) return { error: "round not found" };
  const hold = body.hold || [];
  const hand = round.hand.map((c, i) => (hold[i] ? c : g.deck.pop()));
  const ev = evaluate(hand);
  const payout = ev.mult * g.bet;
  state.balance = Math.max(0, state.balance - g.bet + payout);
  delete mockRounds[body.round_id];
  return { hand, category: ev.category, label: ev.label, mult: ev.mult, payout, balance: state.balance };
}

main();
