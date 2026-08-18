// SharpLab HQ — Three Card Poker. Ante up, see your three cards, then Play
// (match the ante) or Fold (surrender it). Rounds POST to
// /api/v1/casino/threecardpoker/* (session-cookie auth); the resolved response
// carries the authoritative new balance, which we push into the nav chip +
// on-page header. Quirk: a straight BEATS a flush in three-card poker.

const app = document.getElementById("app");
const navRight = document.getElementById("navRight");

// ── Helpers (copied verbatim from blackjack.js / casino.js) ──
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
const round = { id: null, live: false, busy: false, ante: 0, balBefore: 0 };

// ── POST to a three-card-poker endpoint. Returns parsed JSON or {error}. ──
async function postTCP(path, body) {
  if (MOCK) return mockTCP(path, body);
  const r = await fetch("/api/v1/casino/threecardpoker" + path, {
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

// ── Ante-input block (copied from casino.js bet input) ──
function anteInput() {
  return `<div class="betrow">
    <div class="betlbl">Ante</div>
    <div class="betline">
      <input class="betinput" type="number" min="1" step="1" value="100"
             id="ante-tcp" inputmode="numeric" />
      <div class="chipbtns">
        <button class="chipbtn" data-amt="10">10</button>
        <button class="chipbtn" data-amt="100">100</button>
        <button class="chipbtn" data-amt="1000">1k</button>
        <button class="chipbtn" data-amt="max">Max</button>
      </div>
    </div>
  </div>`;
}
function readAnte() {
  const el = document.getElementById("ante-tcp");
  const v = Math.floor(Number(el && el.value));
  return Number.isFinite(v) ? v : 0;
}

// ── Card rendering (copied from blackjack.js) ──
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
  return `<div class="tcpcard${red ? " red" : ""}${anim}">
    <span class="corner tl">${esc(rank)}<i>${esc(suit)}</i></span>
    <span class="pip">${esc(suit)}</span>
    <span class="corner br">${esc(rank)}<i>${esc(suit)}</i></span>
  </div>`;
}
function cardBack() {
  return `<div class="tcpcard back${REDUCE ? "" : " deal"}"><span class="backart"></span></div>`;
}
function renderHand(cards, cls) {
  return cards.map((c) => cardFace(c, cls)).join("");
}

// ── Hand-rank display labels ──
const RANK_LABELS = {
  straight_flush: "Straight Flush",
  trips: "Three of a Kind",
  straight: "Straight",
  flush: "Flush",
  pair: "Pair",
  high: "High Card",
};
const rankLabel = (r) => RANK_LABELS[r] || (r ? String(r) : "");

// ── UI refs ──
const $ = (id) => document.getElementById(id);

function setBusy(busy) {
  round.busy = busy;
  document.querySelectorAll(".tcpactions .btn").forEach((el) => (el.disabled = busy));
}

// ── Render the dealt (pre-decision) table state ──
function renderDealt(res) {
  round.id = res.round_id;
  round.live = true;
  $("tcpPlayerCards").innerHTML = renderHand(res.player || []);
  const rk = $("tcpPlayerRank");
  rk.textContent = rankLabel(res.player_rank);
  rk.className = "tcprank" + (res.player_rank ? "" : " hidden");
  // Dealer stays face-down until the decision.
  $("tcpDealerCards").innerHTML = cardBack() + cardBack() + cardBack();
  $("tcpDealerRank").className = "tcprank hidden";
  $("tcpBanner").className = "tcpbanner";
  $("tcpBanner").innerHTML = "";
  $("tcpNote").innerHTML = "";
  $("tcpActions").innerHTML =
    `<button class="btn primary" id="tcpPlay">Play</button>
     <button class="btn ghost" id="tcpFold">Fold</button>`;
}

const RESULT_META = {
  win:                { cls: "win",  label: "WIN" },
  lose:               { cls: "lose", label: "LOSE" },
  fold:               { cls: "lose", label: "FOLD" },
  push:               { cls: "push", label: "PUSH" },
  dealer_no_qualify:  { cls: "win",  label: "DEALER OUT" },
};

async function renderResolved(res) {
  round.live = false;
  const net = Math.round((res.balance != null ? res.balance : round.balBefore) - round.balBefore);
  applyBalance(res.balance);
  round.id = null;

  // Reveal the dealer's full hand (flip-in).
  $("tcpDealerCards").innerHTML = renderHand(res.dealer || [], "flip");
  const drk = $("tcpDealerRank");
  if (res.dealer_rank) { drk.textContent = rankLabel(res.dealer_rank); drk.className = "tcprank"; }
  else { drk.className = "tcprank hidden"; }
  if (res.player) $("tcpPlayerCards").innerHTML = renderHand(res.player);
  if (res.player_rank) {
    const prk = $("tcpPlayerRank");
    prk.textContent = rankLabel(res.player_rank); prk.className = "tcprank";
  }

  const meta = RESULT_META[res.result] || { cls: "push", label: String(res.result || "").toUpperCase() };
  let line;
  if (net > 0) line = `+${coins(net)}`;
  else if (net < 0) line = `-${coins(-net)}`;
  else line = "Even";
  $("tcpBanner").className = "tcpbanner show " + meta.cls;
  $("tcpBanner").innerHTML = `<span class="tcpbannerlabel">${meta.label}</span>
    <span class="tcpbannerpay">${line}</span>`;

  // Explanatory note: dealer-qualify status, ante bonus, hand matchup.
  const notes = [];
  if (res.result === "fold") {
    notes.push("You folded — ante forfeited.");
  } else if (res.result === "dealer_no_qualify" || res.dealer_qualifies === false) {
    notes.push(`<span class="noqual">Dealer didn't qualify</span> — ante pays 1:1, Play bet pushes.`);
  } else if (res.player_rank && res.dealer_rank) {
    notes.push(`You: <b>${esc(rankLabel(res.player_rank))}</b> · Dealer: <b>${esc(rankLabel(res.dealer_rank))}</b>`);
  }
  if (res.ante_bonus && res.ante_bonus > 0) {
    notes.push(`<span class="bonus">Ante bonus +${num(res.ante_bonus)} 🪙</span>`);
  }
  $("tcpNote").innerHTML = notes.join("<br>");

  $("tcpActions").innerHTML = `<button class="btn primary" id="tcpNew">New hand</button>`;
}

// ── Deal a fresh hand ──
async function deal() {
  if (round.busy) return;
  const ante = readAnte();
  if (ante < 1) return toast("Enter an ante of at least 1 coin");
  if (ante > Math.floor(state.balance)) return toast("You don't have enough coins for that ante");
  round.ante = ante;
  round.balBefore = state.balance;
  setBusy(true);
  const res = await postTCP("/deal", { ante });
  if (res.error) { setBusy(false); return toast("❌ " + res.error); }
  if (res.done) { await renderResolved(res); setBusy(false); return; }
  if (!REDUCE) await delay(150);
  renderDealt(res);
  setBusy(false);
}

// ── Play or Fold the live hand ──
async function decide(action) {
  if (round.busy || !round.live) return;
  setBusy(true);
  const res = await postTCP("/play", { round_id: round.id, action });
  if (res.error) { setBusy(false); return toast("❌ " + res.error); }
  if (!REDUCE) await delay(160);
  await renderResolved(res);
  setBusy(false);
}

// ── Delegated events ──
app.addEventListener("click", (e) => {
  const chip = e.target.closest(".chipbtn");
  if (chip) {
    const inp = $("ante-tcp");
    if (inp) inp.value = chip.dataset.amt === "max"
      ? Math.max(0, Math.floor(state.balance)) : chip.dataset.amt;
    return;
  }
  if (e.target.closest("#tcpDeal")) return deal();
  if (e.target.closest("#tcpNew")) return resetToDeal();
  if (e.target.closest("#tcpPlay")) return decide("play");
  if (e.target.closest("#tcpFold")) return decide("fold");
});
app.addEventListener("keydown", (e) => {
  if (e.target.id === "ante-tcp" && e.key === "Enter") { e.preventDefault(); deal(); }
});

// ── Reset the table to the pre-deal (Deal button) state ──
function resetToDeal() {
  round.live = false;
  round.id = null;
  $("tcpDealerCards").innerHTML = `<div class="tcpcard empty"></div><div class="tcpcard empty"></div><div class="tcpcard empty"></div>`;
  $("tcpDealerRank").className = "tcprank hidden";
  $("tcpPlayerCards").innerHTML = `<div class="tcpcard empty"></div><div class="tcpcard empty"></div><div class="tcpcard empty"></div>`;
  $("tcpPlayerRank").className = "tcprank hidden";
  $("tcpBanner").className = "tcpbanner";
  $("tcpBanner").innerHTML = "";
  $("tcpNote").innerHTML = "";
  $("tcpActions").innerHTML = `<button class="btn primary big" id="tcpDeal">Deal</button>`;
}

function buildPage() {
  const signedOut = !state.me
    ? `<div class="card" style="margin-bottom:16px;text-align:center">
        <p class="muted" style="margin:0 0 10px">Sign in with Discord to play with your coins.</p>
        <a class="btn" href="/api/v1/auth/discord/login">Sign in with Discord</a></div>`
    : "";
  app.innerHTML = `
    <div class="tcp-head">
      <h1>🂡 Three Card Poker <span class="balancechip" id="pageBal">${coins(state.balance)}</span></h1>
      <p>Ante up, look at your three cards, then Play or Fold. Remember — a straight beats a flush here.</p>
    </div>
    ${signedOut}
    <div class="card tcptable">
      <div class="tcprow">
        <div class="tcprowhead"><span class="tcpwho">Dealer</span>
          <span class="tcprank hidden" id="tcpDealerRank"></span></div>
        <div class="tcpcards" id="tcpDealerCards">
          <div class="tcpcard empty"></div><div class="tcpcard empty"></div><div class="tcpcard empty"></div>
        </div>
      </div>
      <div class="tcpbanner" id="tcpBanner"></div>
      <div class="tcpnote" id="tcpNote"></div>
      <div class="tcprow">
        <div class="tcprowhead"><span class="tcpwho">You</span>
          <span class="tcprank hidden" id="tcpPlayerRank"></span></div>
        <div class="tcpcards" id="tcpPlayerCards">
          <div class="tcpcard empty"></div><div class="tcpcard empty"></div><div class="tcpcard empty"></div>
        </div>
      </div>
    </div>
    <div class="card tcpcontrols">
      ${anteInput()}
      <div class="tcpactions" id="tcpActions">
        <button class="btn primary big" id="tcpDeal">Deal</button>
      </div>
    </div>
    <div class="card tcprules">
      <h3>How it works</h3>
      <ul>
        <li>Post an <b>ante</b> to start. You see your three cards, the dealer's stay face-down.</li>
        <li><b>Play</b> matches your ante with an equal Play bet; <b>Fold</b> forfeits the ante.</li>
        <li>Dealer <b>qualifies</b> on Queen-high or better. If the dealer doesn't qualify, the ante pays 1:1 and the Play bet pushes.</li>
        <li>Beat a qualifying dealer and both ante and Play pay 1:1. A <b>straight beats a flush</b> (three-card quirk).</li>
      </ul>
      <table class="tcppay">
        <caption>Ante bonus — paid regardless of the dealer</caption>
        <thead><tr><th>Hand</th><th class="pay">Bonus</th></tr></thead>
        <tbody>
          <tr><td>Straight flush</td><td class="pay">+5×</td></tr>
          <tr><td>Three of a kind</td><td class="pay">+4×</td></tr>
          <tr><td>Straight</td><td class="pay">+1×</td></tr>
        </tbody>
      </table>
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
// Mock mode (?mock=1): a client-side shoe + three-card hand ranker
// so the page can be exercised offline. Real endpoints win by default.
// Ranking order (high→low): straight flush > trips > straight > flush
// > pair > high card — i.e. a STRAIGHT BEATS A FLUSH.
// ─────────────────────────────────────────────────────────────
const MOCK = new URLSearchParams(location.search).get("mock") === "1";

function mockJSON(url) {
  if (url.startsWith("/api/v1/hq/me"))
    return { authenticated: true, user: { id: "1", username: "davidj", avatar: null }, balance: 12500 };
  return {};
}

const MOCK_RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"];
const MOCK_SUITS = ["♠", "♥", "♦", "♣"];
const RANK_VALUE = { J: 11, Q: 12, K: 13, A: 14 };
const rankValue = (r) => RANK_VALUE[r] || Number(r) || 0;
const CAT_NAMES = { 6: "straight_flush", 5: "trips", 4: "straight", 3: "flush", 2: "pair", 1: "high" };

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

// Rank a 3-card hand → { cat, catName, tiebreak[] } for comparison.
function handKey(cards) {
  const vals = cards.map((c) => rankValue(parseCard(c).rank)).sort((a, b) => b - a); // desc
  const suits = cards.map((c) => parseCard(c).suit);
  const flush = suits.every((s) => s === suits[0]);
  const asc = [...vals].sort((a, b) => a - b);
  let straight = false, straightHigh = 0;
  if (asc[0] + 1 === asc[1] && asc[1] + 1 === asc[2]) { straight = true; straightHigh = asc[2]; }
  if (asc[0] === 2 && asc[1] === 3 && asc[2] === 14) { straight = true; straightHigh = 3; } // A-2-3 (ace low)
  const counts = {};
  vals.forEach((v) => (counts[v] = (counts[v] || 0) + 1));
  const byCount = Object.entries(counts)
    .map(([v, c]) => [c, Number(v)])
    .sort((a, b) => b[0] - a[0] || b[1] - a[1]);
  const trips = byCount[0][0] === 3;
  const pair = byCount[0][0] === 2;
  let cat, tiebreak;
  if (straight && flush) { cat = 6; tiebreak = [straightHigh]; }
  else if (trips) { cat = 5; tiebreak = [byCount[0][1]]; }
  else if (straight) { cat = 4; tiebreak = [straightHigh]; }
  else if (flush) { cat = 3; tiebreak = vals; }
  else if (pair) { cat = 2; tiebreak = [byCount[0][1], byCount[1][1]]; }
  else { cat = 1; tiebreak = vals; }
  return { cat, catName: CAT_NAMES[cat], tiebreak, high: vals[0] };
}
function compareKey(a, b) {
  if (a.cat !== b.cat) return a.cat - b.cat;
  for (let i = 0; i < Math.max(a.tiebreak.length, b.tiebreak.length); i++) {
    const d = (a.tiebreak[i] || 0) - (b.tiebreak[i] || 0);
    if (d) return d;
  }
  return 0;
}
// Dealer qualifies on Queen-high (high card ≥ Q) or any made hand.
function dealerQualifies(k) { return k.cat > 1 || k.high >= 12; }

const mockRounds = {};
function mockTCP(path, body) {
  if (path === "/deal") {
    const ante = Math.floor(Number(body.ante) || 0);
    const rid = "mock-" + Date.now();
    const player = [mockDraw(), mockDraw(), mockDraw()];
    const dealer = [mockDraw(), mockDraw(), mockDraw()];
    mockRounds[rid] = { ante, player, dealer };
    return { round_id: rid, player, ante, player_rank: handKey(player).catName };
  }
  // /play
  const g = mockRounds[body.round_id];
  if (!g) return { error: "round not found" };
  const ante = g.ante;
  const pk = handKey(g.player), dk = handKey(g.dealer);
  const bonusMult = { straight_flush: 5, trips: 4, straight: 1 }[pk.catName] || 0;

  if (body.action === "fold") {
    state.balance = Math.max(0, state.balance - ante);
    delete mockRounds[body.round_id];
    return { done: true, result: "fold", payout: 0, balance: state.balance,
      dealer: g.dealer, player: g.player, player_rank: pk.catName, dealer_rank: dk.catName };
  }

  // Play — matches the ante with an equal Play bet.
  const bonus = bonusMult * ante;               // paid regardless of the dealer
  const dq = dealerQualifies(dk);
  let result, net;
  if (!dq) { result = "dealer_no_qualify"; net = ante + bonus; }        // ante 1:1, play pushes
  else {
    const cmp = compareKey(pk, dk);
    if (cmp > 0) { result = "win"; net = 2 * ante + bonus; }            // ante + play both 1:1
    else if (cmp < 0) { result = "lose"; net = -2 * ante + bonus; }     // both lost, bonus still paid
    else { result = "push"; net = bonus; }
  }
  state.balance = Math.max(0, state.balance + net);
  delete mockRounds[body.round_id];
  return { done: true, result, payout: net > 0 ? net : 0, balance: state.balance,
    dealer: g.dealer, player: g.player, player_rank: pk.catName, dealer_rank: dk.catName,
    dealer_qualifies: dq, ante_bonus: bonus };
}

main();
