// SharpLab HQ — Casino: coinflip, dice, slots, roulette. Play inline for coins.
// Bets POST to /api/v1/casino/* (session-cookie auth); each response carries the
// authoritative new balance, which we push back into the nav chip + page header.

const app = document.getElementById("app");
const navRight = document.getElementById("navRight");

// ── Helpers (copied verbatim from cards.js) ──
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

// ── Nav (login / logout) — mirrors cards.js (reads state.balance) ──
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
const games = {
  coinflip: { side: "heads" },
  dice: { target: 50, direction: "over" },
  slots: {},
  roulette: { kind: "red", number: 7, dozen: 1 },
};

// ── POST to a casino endpoint. Returns {detail, payout, won, bet, balance} or {error}. ──
async function postCasino(path, body) {
  if (MOCK) return mockCasino(path, body);
  const r = await fetch("/api/v1/casino" + path, {
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

// ── Toast (copied verbatim from cards.js) ──
function toast(msg) {
  const t = document.createElement("div");
  t.className = "cardtoast";
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 3200);
}

// ── Coins hub (copied verbatim from cards.js) ──
const EARN_WAYS = [
  ["💬", "Daily chat reward", "500 coins for your first message in the server each day"],
  ["🎁", "Free daily pack", "Open one free card pack every day — pure upside"],
  ["🃏", "Complete a set", "One-time coin bonus for owning every card in a set"],
  ["♻️", "Quick-sell dupes", "Sell duplicate cards back for coins in Discord"],
  ["🎮", "Win casino games", "Win at /play in Discord — note: playing no longer hands out free coins"],
  ["🔄", "Trade cards", "Swap cards with other collectors to complete sets"],
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

// ── Shared bet-input block ──
function betInput(game) {
  return `<div class="betrow">
    <div class="betlbl">Bet</div>
    <div class="betline">
      <input class="betinput" type="number" min="1" step="1" value="100"
             id="bet-${game}" data-betfor="${game}" inputmode="numeric" />
      <div class="chipbtns">
        <button class="chipbtn" data-chip="${game}" data-amt="10">10</button>
        <button class="chipbtn" data-chip="${game}" data-amt="100">100</button>
        <button class="chipbtn" data-chip="${game}" data-amt="1000">1k</button>
        <button class="chipbtn" data-chip="${game}" data-amt="max">Max</button>
      </div>
    </div>
  </div>`;
}
function readBet(game) {
  const el = document.getElementById("bet-" + game);
  const v = Math.floor(Number(el && el.value));
  return Number.isFinite(v) ? v : 0;
}
function setResult(game, res) {
  const el = document.querySelector(`.result[data-result="${game}"]`);
  if (!el) return;
  const net = (res.payout || 0) - (res.bet || 0);
  if (res.won) {
    el.className = "result win";
    el.textContent = `Win  +${coins(net)}`;
  } else {
    el.className = "result loss";
    el.textContent = `Loss  -${coins(res.bet || 0)}`;
  }
}

// ═══════════════════ Coinflip ═══════════════════
function coinflipPanel() {
  return `<div class="card gcard">
    <div class="ghead"><h2><span class="gicon">🪙</span> Coinflip</h2><div class="gpay">Win pays <b>1.95×</b></div></div>
    <div class="coinstage"><div class="coin" id="coinFace">H</div></div>
    <div class="betgroup">
      <div class="betlbl">Call it</div>
      <div class="toggle" data-toggle="coinflip">
        <button data-val="heads" class="on">Heads</button>
        <button data-val="tails">Tails</button>
      </div>
    </div>
    ${betInput("coinflip")}
    <button class="btn primary playbtn" data-play="coinflip">Flip</button>
    <div class="result idle" data-result="coinflip">Pick a side and flip.</div>
  </div>`;
}
async function playCoinflip(btn) {
  const bet = readBet("coinflip");
  if (bet < 1) return toast("Enter a bet of at least 1 coin");
  btn.disabled = true;
  const coin = document.getElementById("coinFace");
  if (coin && !REDUCE) { coin.classList.remove("flipping"); void coin.offsetWidth; coin.classList.add("flipping"); }
  const [res] = await Promise.all([postCasino("/coinflip", { bet, side: games.coinflip.side }), delay(REDUCE ? 0 : 900)]);
  btn.disabled = false;
  if (res.error) return toast("❌ " + res.error);
  applyBalance(res.balance);
  const flip = res.detail && res.detail.flip;
  if (coin && flip) { coin.textContent = flip === "heads" ? "H" : "T"; coin.classList.remove("flipping"); }
  setResult("coinflip", res);
}

// ═══════════════════ Dice ═══════════════════
function diceOdds(target, direction) {
  const chance = direction === "over" ? 100 - target : target; // percent
  const mult = 0.98 * 100 / chance;
  return { chance, mult };
}
function dicePanel() {
  return `<div class="card gcard">
    <div class="ghead"><h2><span class="gicon">🎲</span> Dice</h2><div class="gpay">2% house edge</div></div>
    <div class="dicetrack" id="diceTrack">
      <div class="winzone" id="diceZone"></div>
      <div class="marker" id="diceMarker"></div>
      <div class="rolledlbl" id="diceRolled" style="left:50%;opacity:0">—</div>
    </div>
    <div class="betgroup">
      <input class="diceslider" id="diceSlider" type="range" min="2" max="98" step="1" value="50" />
      <div class="dicescale"><span>2</span><span>Target 50</span><span>98</span></div>
    </div>
    <div class="betgroup">
      <div class="betlbl">Roll</div>
      <div class="toggle" data-toggle="dice">
        <button data-val="over" class="on">Over</button>
        <button data-val="under">Under</button>
      </div>
    </div>
    <div class="dicereadout" id="diceReadout"></div>
    ${betInput("dice")}
    <button class="btn primary playbtn" data-play="dice">Roll</button>
    <div class="result idle" data-result="dice">Set a target, then roll.</div>
  </div>`;
}
function updateDice() {
  const { target, direction } = games.dice;
  const { chance, mult } = diceOdds(target, direction);
  const bet = readBet("dice");
  const zone = document.getElementById("diceZone");
  const marker = document.getElementById("diceMarker");
  const scale = document.querySelector(".dicescale span:nth-child(2)");
  if (scale) scale.textContent = "Target " + target;
  if (marker) marker.style.left = target + "%";
  if (zone) {
    if (direction === "over") { zone.style.left = target + "%"; zone.style.right = "0"; zone.style.width = "auto"; }
    else { zone.style.left = "0"; zone.style.right = "auto"; zone.style.width = target + "%"; }
  }
  const ro = document.getElementById("diceReadout");
  if (ro) ro.innerHTML =
    `<span>Win chance <b>${chance}%</b></span>
     <span>Multiplier <b>${mult.toFixed(2)}×</b></span>
     <span>Payout <b>${coins(bet * mult)}</b></span>`;
}
async function playDice(btn) {
  const bet = readBet("dice");
  if (bet < 1) return toast("Enter a bet of at least 1 coin");
  btn.disabled = true;
  const [res] = await Promise.all([
    postCasino("/dice", { bet, target: games.dice.target, direction: games.dice.direction }),
    delay(REDUCE ? 0 : 200),
  ]);
  btn.disabled = false;
  if (res.error) return toast("❌ " + res.error);
  applyBalance(res.balance);
  const roll = res.detail && res.detail.roll;
  const lbl = document.getElementById("diceRolled");
  if (lbl && roll != null) { lbl.style.left = roll + "%"; lbl.style.opacity = "1"; lbl.textContent = roll; }
  setResult("dice", res);
}

// ═══════════════════ Slots ═══════════════════
const SLOT_SYMBOLS = ["🍒", "🍋", "🔔", "⭐", "💎", "7️⃣", "🃏"];
function slotsPanel() {
  const reels = [0, 1, 2].map((i) => `<div class="reel" data-reel="${i}">❔</div>`).join("");
  return `<div class="card gcard">
    <div class="ghead"><h2><span class="gicon">🎰</span> Slots</h2><div class="gpay">3-of-a-kind pays big</div></div>
    <div class="reels" id="slotReels">${reels}</div>
    ${betInput("slots")}
    <button class="btn primary playbtn" data-play="slots">Spin</button>
    <div class="result idle" data-result="slots">Line up three to win.</div>
    <div class="subline" id="slotsSub"></div>
  </div>`;
}
async function playSlots(btn) {
  const bet = readBet("slots");
  if (bet < 1) return toast("Enter a bet of at least 1 coin");
  btn.disabled = true;
  const reelEls = [...document.querySelectorAll("#slotReels .reel")];
  reelEls.forEach((r) => r.classList.remove("win"));
  let spinner = null;
  if (!REDUCE) {
    reelEls.forEach((r) => r.classList.add("spin"));
    spinner = setInterval(() => {
      reelEls.forEach((r) => (r.textContent = SLOT_SYMBOLS[Math.floor(Math.random() * SLOT_SYMBOLS.length)]));
    }, 80);
  }
  const [res] = await Promise.all([postCasino("/slots", { bet }), delay(REDUCE ? 0 : 700)]);
  if (spinner) clearInterval(spinner);
  reelEls.forEach((r) => r.classList.remove("spin"));
  btn.disabled = false;
  if (res.error) return toast("❌ " + res.error);
  applyBalance(res.balance);
  const d = res.detail || {};
  const reels = d.reels || [];
  reelEls.forEach((r, i) => (r.textContent = reels[i] || "❔"));
  if (res.won) reelEls.forEach((r) => r.classList.add("win"));
  const sub = document.getElementById("slotsSub");
  if (sub) sub.innerHTML = res.won ? `Payout <b>${(d.mult || 0)}×</b> → <b>${coins(res.payout)}</b>` : "";
  setResult("slots", res);
}

// ═══════════════════ Roulette ═══════════════════
const ROUL_RED = new Set([1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36]);
const ROUL_KINDS = [
  ["red", "Red"], ["black", "Black"], ["even", "Even"], ["odd", "Odd"], ["low", "1-18"], ["high", "19-36"],
];
function roulettePanel() {
  const kindBtns = ROUL_KINDS.map(
    ([k, label]) => `<button class="kindbtn ${k}${games.roulette.kind === k ? " on" : ""}" data-kind="${k}">${label}</button>`
  ).join("");
  const dozBtns = [["1", "1st 12"], ["2", "2nd 12"], ["3", "3rd 12"]].map(
    ([v, label]) => `<button class="kindbtn${games.roulette.kind === "dozen" && String(games.roulette.dozen) === v ? " on" : ""}" data-kind="dozen" data-dozen="${v}">${label}</button>`
  ).join("");
  return `<div class="card gcard">
    <div class="ghead"><h2><span class="gicon">🎡</span> Roulette</h2><div class="gpay">Straight-up <b>36×</b></div></div>
    <div class="roulwheel"><div class="roulnum green" id="roulNum">0</div></div>
    <div class="betgroup">
      <div class="betlbl">Bet type</div>
      <div class="kindbtns">${kindBtns}</div>
      <div class="kindbtns">${dozBtns}</div>
      <div class="roulnumwrap">
        <button class="kindbtn${games.roulette.kind === "number" ? " on" : ""}" data-kind="number">Number</button>
        <input class="roulnumin" id="roulNumIn" type="number" min="0" max="36" step="1" value="${games.roulette.number}" />
        <span>0–36</span>
      </div>
    </div>
    ${betInput("roulette")}
    <button class="btn primary playbtn" data-play="roulette">Spin</button>
    <div class="result idle" data-result="roulette">Choose a bet, then spin.</div>
  </div>`;
}
function roulBody() {
  const g = games.roulette;
  const body = { kind: g.kind };
  if (g.kind === "number") body.value = Math.max(0, Math.min(36, Math.floor(Number(g.number) || 0)));
  else if (g.kind === "dozen") body.value = Number(g.dozen);
  return body;
}
async function playRoulette(btn) {
  const bet = readBet("roulette");
  if (bet < 1) return toast("Enter a bet of at least 1 coin");
  btn.disabled = true;
  const wheel = document.getElementById("roulNum");
  if (wheel && !REDUCE) { wheel.classList.remove("spin"); void wheel.offsetWidth; wheel.classList.add("spin"); }
  const [res] = await Promise.all([postCasino("/roulette", { bet, ...roulBody() }), delay(REDUCE ? 0 : 700)]);
  btn.disabled = false;
  if (res.error) return toast("❌ " + res.error);
  applyBalance(res.balance);
  const d = res.detail || {};
  if (wheel && d.n != null) {
    wheel.textContent = d.n;
    wheel.className = "roulnum " + (d.color || (d.n === 0 ? "green" : ROUL_RED.has(d.n) ? "red" : "black"));
  }
  setResult("roulette", res);
}

// ── Dispatch ──
const PLAY = { coinflip: playCoinflip, dice: playDice, slots: playSlots, roulette: playRoulette };

function buildPage() {
  const signedOut = !state.me
    ? `<div class="card" style="margin-bottom:16px;text-align:center">
        <p class="muted" style="margin:0 0 10px">Sign in with Discord to play with your coins.</p>
        <a class="btn" href="/api/v1/auth/discord/login">Sign in with Discord</a></div>`
    : "";
  app.innerHTML = `
    <div class="casino-head">
      <h1>🎰 Casino <span class="balancechip" id="pageBal">${coins(state.balance)}</span></h1>
      <p>Provably-styled coin games. Every wager is settled against your casino balance.</p>
    </div>
    ${signedOut}
    <div class="gamegrid">
      ${coinflipPanel()}
      ${dicePanel()}
      ${slotsPanel()}
      ${roulettePanel()}
    </div>`;
  updateDice();
}

// Delegated events
app.addEventListener("click", (e) => {
  const chip = e.target.closest(".chipbtn");
  if (chip) {
    const inp = document.getElementById("bet-" + chip.dataset.chip);
    if (inp) {
      inp.value = chip.dataset.amt === "max" ? Math.max(0, Math.floor(state.balance)) : chip.dataset.amt;
      if (chip.dataset.chip === "dice") updateDice();
    }
    return;
  }
  const tgl = e.target.closest(".toggle button");
  if (tgl) {
    const group = tgl.closest(".toggle").dataset.toggle;
    [...tgl.parentElement.children].forEach((b) => b.classList.toggle("on", b === tgl));
    if (group === "coinflip") games.coinflip.side = tgl.dataset.val;
    if (group === "dice") { games.dice.direction = tgl.dataset.val; updateDice(); }
    return;
  }
  const kind = e.target.closest(".kindbtn");
  if (kind) {
    games.roulette.kind = kind.dataset.kind;
    if (kind.dataset.dozen) games.roulette.dozen = Number(kind.dataset.dozen);
    document.querySelectorAll(".kindbtn").forEach((b) => {
      const on = b.dataset.kind === kind.dataset.kind &&
        (b.dataset.kind !== "dozen" || b.dataset.dozen === kind.dataset.dozen);
      b.classList.toggle("on", on);
    });
    return;
  }
  const play = e.target.closest(".playbtn");
  if (play) { PLAY[play.dataset.play](play); return; }
});

app.addEventListener("input", (e) => {
  if (e.target.id === "diceSlider") { games.dice.target = Number(e.target.value); updateDice(); return; }
  if (e.target.id === "roulNumIn") {
    games.roulette.number = e.target.value;
    if (games.roulette.kind === "number") return;
  }
  if (e.target.dataset && e.target.dataset.betfor === "dice") updateDice();
});
// Selecting the number field implies a straight-up bet.
app.addEventListener("focusin", (e) => {
  if (e.target.id === "roulNumIn") {
    games.roulette.kind = "number";
    document.querySelectorAll(".kindbtn").forEach((b) => b.classList.toggle("on", b.dataset.kind === "number"));
  }
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
// Mock mode (?mock=1): fake results without a backend so the page
// can be screenshot-tested offline. Real endpoints win by default.
// ─────────────────────────────────────────────────────────────
const MOCK = new URLSearchParams(location.search).get("mock") === "1";

function mockJSON(url) {
  if (url.startsWith("/api/v1/hq/me"))
    return { authenticated: true, user: { id: "1", username: "davidj", avatar: null }, balance: 12500 };
  return {};
}

function mockSlotResult() {
  const r = Math.random();
  if (r < 0.06) { const s = ["💎", "7️⃣", "🃏"][Math.floor(Math.random() * 3)]; return { reels: [s, s, s], mult: 30 }; }
  if (r < 0.16) { const s = SLOT_SYMBOLS[Math.floor(Math.random() * 4)]; return { reels: [s, s, s], mult: 6 }; }
  if (r < 0.30) {
    const p = ["💎", "7️⃣", "🃏"][Math.floor(Math.random() * 3)];
    const o = SLOT_SYMBOLS[Math.floor(Math.random() * 4)];
    return { reels: [p, p, o], mult: 2 };
  }
  const pick = () => SLOT_SYMBOLS[Math.floor(Math.random() * SLOT_SYMBOLS.length)];
  return { reels: [pick(), pick(), pick()], mult: 0 };
}

function mockCasino(path, body) {
  const bet = Math.floor(Number(body.bet) || 0);
  let detail = {}, won = false, payout = 0;
  if (path === "/coinflip") {
    const flip = Math.random() < 0.5 ? "heads" : "tails";
    won = flip === body.side;
    payout = won ? Math.round(bet * 1.95) : 0;
    detail = { flip, side: body.side };
  } else if (path === "/dice") {
    const roll = Math.floor(Math.random() * 100);
    won = body.direction === "over" ? roll > body.target : roll < body.target;
    const { chance, mult } = diceOdds(body.target, body.direction);
    payout = won ? Math.round(bet * mult) : 0;
    detail = { roll, target: body.target, direction: body.direction, win_chance: chance };
  } else if (path === "/slots") {
    const s = mockSlotResult();
    won = s.mult > 0;
    payout = Math.round(bet * s.mult);
    detail = s;
  } else if (path === "/roulette") {
    const n = Math.floor(Math.random() * 37);
    const color = n === 0 ? "green" : ROUL_RED.has(n) ? "red" : "black";
    const k = body.kind;
    if (k === "number") { won = n === body.value; payout = won ? bet * 36 : 0; }
    else if (k === "dozen") { won = n >= 1 && Math.ceil(n / 12) === body.value; payout = won ? bet * 3 : 0; }
    else {
      won = n !== 0 && (
        (k === "red" && color === "red") || (k === "black" && color === "black") ||
        (k === "even" && n % 2 === 0) || (k === "odd" && n % 2 === 1) ||
        (k === "low" && n <= 18) || (k === "high" && n >= 19));
      payout = won ? bet * 2 : 0;
    }
    detail = { n, color, kind: k, value: body.value };
  }
  state.balance = Math.max(0, state.balance - bet + payout);
  return { detail, payout, won, bet, balance: state.balance };
}

main();
