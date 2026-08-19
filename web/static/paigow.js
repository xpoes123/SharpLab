// SharpLab HQ — Pai Gow Poker. Bet (plus an optional Fortune side bet), get
// seven cards, then split them into a 5-card "high" hand and a 2-card "low"
// hand — the high hand must OUTRANK the low hand or it's a foul. You beat the
// dealer only by winning BOTH hands; win one / lose one is a push. Rounds POST
// to /api/v1/casino/paigow/* (session-cookie auth); the settled response
// carries the authoritative new balance, which we push into the nav chip +
// on-page header. Special card: the joker is the string "JK", rendered 🃏.

const app = document.getElementById("app");
const navRight = document.getElementById("navRight");

// ── Helpers (copied verbatim from threecardpoker.js / casino.js) ──
const num = (n) => (n == null ? "—" : Number(n).toLocaleString());
const coins = (n) => "🪙 " + num(Math.round(n || 0));
const esc = (s) =>
  String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function getJSON(url) {
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
// live round: seven dealt cards, the server's suggested split, and the player's
// current pick of the two low-hand card indices.
const round = { id: null, live: false, busy: false, cards: [], balBefore: 0, sel: [] };

// ── POST to a pai-gow endpoint. Returns parsed JSON or {error}. ──
async function postPG(path, body) {
  const r = await fetch("/api/v1/casino/paigow" + path, {
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

// ── Card rendering (copied from threecardpoker.js, joker-aware) ──
// Cards are strings like "A♠", "10♥", "K♦". The joker is the literal "JK",
// rendered as 🃏. Split real cards into rank + suit; red for ♥♦.
function parseCard(card) {
  const s = String(card);
  if (s === "JK") return { joker: true, rank: "🃏", suit: "", red: false };
  const suit = s.slice(-1);
  const rank = s.slice(0, -1);
  const red = suit === "♥" || suit === "♦";
  return { joker: false, rank, suit, red };
}
function cardFace(card, opts) {
  const o = opts || {};
  const { joker, rank, suit, red } = parseCard(card);
  const anim = REDUCE ? "" : " " + (o.anim || "deal");
  const cls = [
    "pgcard",
    red ? "red" : "",
    joker ? "joker" : "",
    o.selected ? "sel" : "",
    o.selectable ? "selectable" : "",
    anim.trim(),
  ].filter(Boolean).join(" ");
  const attr = o.index != null ? ` data-idx="${o.index}"` : "";
  const badge = o.selected ? `<span class="pgbadge">LOW</span>` : "";
  if (joker) {
    return `<div class="${cls}"${attr}>
      <span class="pip jk">🃏</span>${badge}</div>`;
  }
  return `<div class="${cls}"${attr}>
    <span class="corner tl">${esc(rank)}<i>${esc(suit)}</i></span>
    <span class="pip">${esc(suit)}</span>
    <span class="corner br">${esc(rank)}<i>${esc(suit)}</i></span>${badge}</div>`;
}
function cardBack() {
  return `<div class="pgcard back${REDUCE ? "" : " deal"}"><span class="backart"></span></div>`;
}
function renderHand(cards, anim) {
  return (cards || []).map((c) => cardFace(c, { anim })).join("");
}

// ── UI refs ──
const $ = (id) => document.getElementById(id);

function setBusy(busy) {
  round.busy = busy;
  document.querySelectorAll(".pgactions .btn").forEach((el) => (el.disabled = busy));
  syncSetButton();
}

// ── Bet / Fortune inputs (copied from casino.js bet input) ──
function betInputs() {
  return `<div class="betgroup">
    <div class="betrow">
      <div class="betlbl">Bet</div>
      <div class="betline">
        <input class="betinput" type="number" min="1" step="1" value="100"
               id="bet-pg" inputmode="numeric" />
        <div class="chipbtns">
          <button class="chipbtn" data-amt="10">10</button>
          <button class="chipbtn" data-amt="100">100</button>
          <button class="chipbtn" data-amt="1000">1k</button>
          <button class="chipbtn" data-amt="max">Max</button>
        </div>
      </div>
    </div>
    <div class="betrow">
      <div class="betlbl">Fortune side bet <span class="opt">optional</span></div>
      <div class="betline">
        <input class="betinput" type="number" min="0" step="1" value="0"
               id="fortune-pg" inputmode="numeric" />
      </div>
    </div>
  </div>`;
}
function readBet() {
  const el = $("bet-pg");
  const v = Math.floor(Number(el && el.value));
  return Number.isFinite(v) ? v : 0;
}
function readFortune() {
  const el = $("fortune-pg");
  const v = Math.floor(Number(el && el.value));
  return Number.isFinite(v) && v > 0 ? v : 0;
}

// ── Deal a fresh hand ──
async function deal() {
  if (round.busy) return;
  const bet = readBet();
  const fortune = readFortune();
  if (bet < 1) return toast("Enter a bet of at least 1 coin");
  if (bet + fortune > Math.floor(state.balance))
    return toast("You don't have enough coins for that bet");
  round.balBefore = state.balance;
  setBusy(true);
  const res = await postPG("/deal", { bet, fortune });
  if (res.error) { setBusy(false); return toast("❌ " + res.error); }
  applyBalance(state.balance - (bet + fortune)); // optimistic debit; settle confirms
  if (!REDUCE) await delay(150);
  renderDealt(res);
  setBusy(false);
}

// ── Render the seven dealt cards + the setting UI ──
function renderDealt(res) {
  round.id = res.round_id;
  round.live = true;
  round.cards = res.cards || [];
  // Pre-select the server's suggested low hand so "Set Hand" works out of the box.
  round.sel = indicesForCards(res.suggest_low || [], round.cards);

  $("pgDealerWrap").className = "pgrow dealerrow facedown";
  $("pgDealerCards").innerHTML = cardBack() + cardBack() + cardBack() + cardBack() + cardBack() + cardBack() + cardBack();
  $("pgDealerHi").innerHTML = "";
  $("pgDealerLo").innerHTML = "";
  $("pgBanner").className = "pgbanner";
  $("pgBanner").innerHTML = "";
  $("pgNote").innerHTML = "";
  $("pgSetHint").textContent =
    "Suggested split is pre-selected (the house way). Click cards to pick your own 2-card low hand, or keep it.";
  $("pgActions").innerHTML =
    `<button class="btn primary" id="pgSet">Set Hand</button>
     <button class="btn ghost" id="pgHouse">House Way</button>`;
  renderSetTable();
}

// Map a list of card strings to their indices in the dealt seven (first match).
function indicesForCards(cards, pool) {
  const used = new Set();
  const out = [];
  for (const c of cards) {
    for (let i = 0; i < pool.length; i++) {
      if (!used.has(i) && pool[i] === c) { used.add(i); out.push(i); break; }
    }
  }
  return out;
}

// Render the seven selectable cards + the live high/low split preview.
function renderSetTable() {
  const sel = round.sel;
  $("pgPlayerCards").style.display = "";
  const cardsHTML = round.cards
    .map((c, i) => cardFace(c, { index: i, selected: sel.includes(i), selectable: true, anim: "deal" }))
    .join("");
  $("pgPlayerCards").innerHTML = cardsHTML;

  const lowCards = sel.map((i) => round.cards[i]);
  const highCards = round.cards.filter((_, i) => !sel.includes(i));
  $("pgSplitHi").innerHTML = highCards.length
    ? highCards.map((c) => cardFace(c, { anim: "none" })).join("")
    : `<span class="pgempty">— pick your 2 low cards —</span>`;
  $("pgSplitLo").innerHTML = lowCards.length
    ? lowCards.map((c) => cardFace(c, { anim: "none", selected: true })).join("")
    : `<span class="pgempty">0 / 2 selected</span>`;
  $("pgSplitLoLbl").textContent = `Low hand (${sel.length}/2)`;
  syncSetButton();
}

function syncSetButton() {
  const btn = $("pgSet");
  if (btn) btn.disabled = round.busy || round.sel.length !== 2;
}

// Toggle a card in/out of the low-hand selection (cap at 2).
function toggleCard(idx) {
  if (!round.live || round.busy) return;
  const pos = round.sel.indexOf(idx);
  if (pos >= 0) round.sel.splice(pos, 1);
  else {
    if (round.sel.length >= 2) round.sel.shift(); // drop oldest so the click still lands
    round.sel.push(idx);
  }
  renderSetTable();
}

// ── Submit the split. lowCards=null → house way; else the two selected cards. ──
async function submitSet(useHouseWay) {
  if (round.busy || !round.live) return;
  let low = null;
  if (!useHouseWay) {
    if (round.sel.length !== 2) return toast("Pick exactly 2 cards for your low hand");
    low = round.sel.map((i) => round.cards[i]);
  }
  setBusy(true);
  const res = await postPG("/set", { round_id: round.id, low });
  if (res.error) {
    // Foul or bad input — the round is still live server-side, let them re-pick.
    setBusy(false);
    return toast("❌ " + res.error);
  }
  if (!REDUCE) await delay(160);
  await renderResolved(res);
  setBusy(false);
}

// ── Render the settled hand: both players' high/low, marks, outcome, fortune ──
const OUTCOME_META = {
  win:  { cls: "win",  label: "WIN" },
  lose: { cls: "lose", label: "LOSE" },
  push: { cls: "push", label: "PUSH" },
};

async function renderResolved(res) {
  round.live = false;
  round.id = null;
  const net = Math.round((res.balance != null ? res.balance : round.balBefore) - round.balBefore);
  applyBalance(res.balance);

  // Per-hand win marks (you must win BOTH to win the round). Ties go to dealer.
  // We can't re-rank client-side reliably (joker), so infer marks from outcome +
  // hand names when unambiguous; otherwise show a neutral matchup.
  const pHi = res.player_high || [], pLo = res.player_low || [];
  const dHi = res.dealer_high || [], dLo = res.dealer_low || [];

  // Reveal dealer (flip-in) over the seven-card row → two split hands.
  $("pgDealerWrap").className = "pgrow dealerrow";
  $("pgDealerCards").innerHTML = "";
  $("pgDealerHi").innerHTML =
    `<div class="pgsplitlbl">High <b>${esc(res.dealer_high_name || "")}</b></div>
     <div class="pgcards">${dHi.map((c) => cardFace(c, { anim: "flip" })).join("")}</div>`;
  $("pgDealerLo").innerHTML =
    `<div class="pgsplitlbl">Low <b>${esc(res.dealer_low_name || "")}</b></div>
     <div class="pgcards">${dLo.map((c) => cardFace(c, { anim: "flip" })).join("")}</div>`;

  // Player split (the hands the server actually used); hide the seven-card row.
  $("pgPlayerCards").innerHTML = "";
  $("pgPlayerCards").style.display = "none";
  $("pgSplitHiLbl").innerHTML = `High <b>${esc(res.player_high_name || "")}</b>`;
  $("pgSplitLoLbl").innerHTML = `Low <b>${esc(res.player_low_name || "")}</b>`;
  $("pgSplitHi").innerHTML = pHi.map((c) => cardFace(c, { anim: "none" })).join("");
  $("pgSplitLo").innerHTML = pLo.map((c) => cardFace(c, { anim: "none" })).join("");
  $("pgSetHint").textContent = "";

  const meta = OUTCOME_META[res.outcome] || { cls: "push", label: String(res.outcome || "").toUpperCase() };
  let line;
  if (net > 0) line = `+${coins(net)}`;
  else if (net < 0) line = `-${coins(-net)}`;
  else line = "Even";
  $("pgBanner").className = "pgbanner show " + meta.cls;
  $("pgBanner").innerHTML = `<span class="pgbannerlabel">${meta.label}</span>
    <span class="pgbannerpay">${line}</span>`;

  // Matchup + fortune note.
  const notes = [];
  notes.push(
    `<span class="matchup"><b>High:</b> ${esc(res.player_high_name || "?")} vs ${esc(res.dealer_high_name || "?")}` +
    ` &nbsp;·&nbsp; <b>Low:</b> ${esc(res.player_low_name || "?")} vs ${esc(res.dealer_low_name || "?")}</span>`
  );
  if (res.outcome === "win") notes.push(`You won <b>both</b> hands — pays even money (no commission).`);
  else if (res.outcome === "push") notes.push(`Split decision (won one, lost one) — it's a <b>push</b>.`);
  else notes.push(`Dealer took both hands.`);
  if (res.fortune_win && res.fortune_win > 0) {
    notes.push(`<span class="fortunehit">🎰 Fortune hit — ${esc(res.fortune_label || "bonus")}: +${num(res.fortune_win)} 🪙</span>`);
  } else if (res.fortune_label) {
    notes.push(`<span class="muted">Fortune: ${esc(res.fortune_label)}</span>`);
  }
  $("pgNote").innerHTML = notes.join("<br>");

  $("pgActions").innerHTML = `<button class="btn primary" id="pgNew">New hand</button>`;
}

// ── Reset to the pre-deal state ──
function resetToDeal() {
  round.live = false;
  round.id = null;
  round.cards = [];
  round.sel = [];
  $("pgDealerWrap").className = "pgrow dealerrow empty";
  $("pgDealerCards").innerHTML = emptySlots(7);
  $("pgDealerHi").innerHTML = "";
  $("pgDealerLo").innerHTML = "";
  $("pgPlayerCards").innerHTML = emptySlots(7);
  $("pgPlayerCards").style.display = "";
  $("pgSplitHiLbl").textContent = "High hand (5)";
  $("pgSplitLoLbl").textContent = "Low hand (2)";
  $("pgSplitHi").innerHTML = `<span class="pgempty">deal to begin</span>`;
  $("pgSplitLo").innerHTML = `<span class="pgempty">deal to begin</span>`;
  $("pgBanner").className = "pgbanner";
  $("pgBanner").innerHTML = "";
  $("pgNote").innerHTML = "";
  $("pgSetHint").textContent = "";
  $("pgActions").innerHTML = `<button class="btn primary big" id="pgDeal">Deal</button>`;
}
function emptySlots(n) {
  return Array.from({ length: n }, () => `<div class="pgcard empty"></div>`).join("");
}

// ── Delegated events ──
app.addEventListener("click", (e) => {
  const chip = e.target.closest(".chipbtn");
  if (chip) {
    const inp = $("bet-pg");
    if (inp) inp.value = chip.dataset.amt === "max"
      ? Math.max(0, Math.floor(state.balance)) : chip.dataset.amt;
    return;
  }
  const card = e.target.closest(".pgcard.selectable");
  if (card && card.dataset.idx != null) return toggleCard(Number(card.dataset.idx));
  if (e.target.closest("#pgDeal")) return deal();
  if (e.target.closest("#pgNew")) return resetToDeal();
  if (e.target.closest("#pgSet")) return submitSet(false);
  if (e.target.closest("#pgHouse")) return submitSet(true);
});
app.addEventListener("keydown", (e) => {
  if ((e.target.id === "bet-pg" || e.target.id === "fortune-pg") && e.key === "Enter") {
    e.preventDefault(); deal();
  }
});

function buildPage() {
  const signedOut = !state.me
    ? `<div class="card" style="margin-bottom:16px;text-align:center">
        <p class="muted" style="margin:0 0 10px">Sign in with Discord to play with your coins.</p>
        <a class="btn" href="/api/v1/auth/discord/login">Sign in with Discord</a></div>`
    : "";
  app.innerHTML = `
    <div class="pg-head">
      <h1>🀄 Pai Gow Poker <span class="balancechip" id="pageBal">${coins(state.balance)}</span></h1>
      <p>Seven cards. Split them into a five-card high hand and a two-card low hand, then beat the dealer on <b>both</b>.</p>
    </div>
    ${signedOut}
    <div class="card pgtable">
      <div class="pgrow dealerrow empty" id="pgDealerWrap">
        <div class="pgrowhead"><span class="pgwho">Dealer</span></div>
        <div class="pgcards" id="pgDealerCards">${emptySlots(7)}</div>
        <div class="pgsplitpair">
          <div class="pgsplit" id="pgDealerHi"></div>
          <div class="pgsplit" id="pgDealerLo"></div>
        </div>
      </div>
      <div class="pgbanner" id="pgBanner"></div>
      <div class="pgnote" id="pgNote"></div>
      <div class="pgrow playerrow">
        <div class="pgrowhead"><span class="pgwho">You</span>
          <span class="pgsethint" id="pgSetHint"></span></div>
        <div class="pgcards selectrow" id="pgPlayerCards">${emptySlots(7)}</div>
        <div class="pgsplitpair">
          <div class="pgsplit"><div class="pgsplitlbl" id="pgSplitHiLbl">High hand (5)</div>
            <div class="pgcards" id="pgSplitHi"><span class="pgempty">deal to begin</span></div></div>
          <div class="pgsplit"><div class="pgsplitlbl" id="pgSplitLoLbl">Low hand (2)</div>
            <div class="pgcards" id="pgSplitLo"><span class="pgempty">deal to begin</span></div></div>
        </div>
      </div>
    </div>
    <div class="card pgcontrols">
      ${betInputs()}
      <div class="pgactions" id="pgActions">
        <button class="btn primary big" id="pgDeal">Deal</button>
      </div>
    </div>
    <div class="card pgrules">
      <h3>How it works</h3>
      <ul>
        <li>You're dealt <b>7 cards</b>. Split them into a five-card <b>high</b> hand and a two-card <b>low</b> hand.</li>
        <li>The high hand must <b>outrank</b> the low hand — otherwise it's a <b>foul</b> and you'll re-pick.</li>
        <li>To win you must beat the dealer on <b>both</b> hands (pays 1:1, no commission). Win one and lose one is a <b>push</b>.</li>
        <li>Ties (<b>copies</b>) go to the dealer. If you lose both but the dealer's high hand is only ace-high with no pair, it's pushed.</li>
        <li>Use <b>House Way</b> to auto-split, or click two cards to set the low hand yourself, then <b>Set Hand</b>.</li>
        <li>The deck has one <b>joker</b> (🃏) — it plays as an ace or completes straights and flushes.</li>
      </ul>
      <p class="pgfortune"><b>Fortune side bet</b> (optional) pays on your seven cards regardless of the main result —
        big hands like a seven-card straight flush, five aces or a royal flush hit the pay table.</p>
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

main();
