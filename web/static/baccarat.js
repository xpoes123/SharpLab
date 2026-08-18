// SharpLab HQ — Baccarat (Punto Banco). Bet Player / Banker / Tie, deal one hand.
// Bets POST to /api/v1/casino/baccarat (session-cookie auth); each response carries the
// authoritative new balance, which we push back into the nav chip + page header.

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

// ── POST to the baccarat endpoint. Returns {detail, payout, won, bet, balance} or {error}. ──
async function postBaccarat(body) {
  if (MOCK) return mockBaccarat(body);
  const r = await fetch("/api/v1/casino/baccarat/baccarat", {
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

// ── Bet-input block (copied from casino.js) ──
function betInput(g) {
  return `<div class="betrow">
    <div class="betlbl">Bet</div>
    <div class="betline">
      <input class="betinput" type="number" min="1" step="1" value="100"
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

// ── Card rendering (♥/♦ are red) ──
const RED_SUITS = new Set(["♥", "♦"]);
function cardFace(label) {
  const suit = label.slice(-1);
  const red = RED_SUITS.has(suit) ? " red" : "";
  return `<div class="pcardface${red}">${esc(label)}</div>`;
}
function renderHand(which, cards, total, isWin) {
  const el = document.getElementById(which + "Hand");
  if (!el) return;
  el.className = "hand" + (isWin ? " win" : "");
  const faces = (cards || []).map(cardFace).join("");
  el.innerHTML = `<div class="handlbl">${which === "player" ? "Player" : "Banker"}</div>
    <div class="handtotal">${total == null ? "—" : total}</div>
    <div class="handcards">${faces}</div>`;
}

const SIDES = [
  ["player", "Player", "2.00×"],
  ["banker", "Banker", "1.95×"],
  ["tie", "Tie", "9.00×"],
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
  app.innerHTML = `
    <div class="bac-head">
      <h1>🃏 Baccarat <span class="balancechip" id="pageBal">${coins(state.balance)}</span></h1>
      <p>Punto Banco. Bet on Player, Banker, or Tie — closest to 9 wins. Ties push Player/Banker bets.</p>
    </div>
    ${signedOut}
    <div class="bac-wrap">
      <div class="card bcard">
        <div class="bhead"><h2><span class="gicon">🃏</span> Baccarat</h2>
          <div class="gpay">Banker <b>1.95×</b> · Player <b>2×</b> · Tie <b>9×</b></div></div>
        <div class="felt">
          <div class="hand" id="playerHand">
            <div class="handlbl">Player</div><div class="handtotal">—</div><div class="handcards"></div>
          </div>
          <div class="hand" id="bankerHand">
            <div class="handlbl">Banker</div><div class="handtotal">—</div><div class="handcards"></div>
          </div>
        </div>
        <div class="sidegroup">
          <div class="sidelbl">Bet on</div>
          <div class="sidebtns">${sideBtns}</div>
        </div>
        ${betInput("baccarat")}
        <button class="btn primary playbtn" id="dealBtn">Deal</button>
        <div class="result idle" id="bacResult">Pick a side and deal.</div>
      </div>
    </div>`;
}

async function deal() {
  if (game.busy) return;
  const bet = readBet("baccarat");
  if (bet < 1) return toast("Enter a bet of at least 1 coin");
  game.busy = true;
  const btn = document.getElementById("dealBtn");
  if (btn) btn.disabled = true;
  // Clear the felt while "dealing".
  renderHand("player", [], null, false);
  renderHand("banker", [], null, false);
  const res = await postBaccarat({ bet, side: game.side });
  if (res.error) {
    game.busy = false;
    if (btn) btn.disabled = false;
    return toast("❌ " + res.error);
  }
  const d = res.detail || {};
  if (!REDUCE) await delay(250);
  const winner = d.outcome;
  renderHand("player", d.player, d.player_total, winner === "player");
  renderHand("banker", d.banker, d.banker_total, winner === "banker");
  applyBalance(res.balance);

  const rEl = document.getElementById("bacResult");
  if (rEl) {
    const net = (res.payout || 0) - (res.bet || 0);
    const push = winner === "tie" && (game.side === "player" || game.side === "banker");
    const outLabel = winner === "tie" ? "Tie" : winner === "player" ? "Player wins" : "Banker wins";
    if (push) {
      rEl.className = "result push";
      rEl.textContent = `${outLabel} — push, stake returned`;
    } else if (res.won || net > 0) {
      rEl.className = "result win";
      rEl.textContent = `${outLabel} — Win  +${coins(net)}`;
    } else {
      rEl.className = "result loss";
      rEl.textContent = `${outLabel} — Loss  -${coins(res.bet || 0)}`;
    }
  }
  game.busy = false;
  if (btn) btn.disabled = false;
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
// Mock mode (?mock=1): run the same Punto Banco drawing rules
// client-side so the page works offline. Real endpoint wins by default.
// ─────────────────────────────────────────────────────────────
const MOCK = new URLSearchParams(location.search).get("mock") === "1";

function mockJSON(url) {
  if (url.startsWith("/api/v1/hq/me"))
    return { authenticated: true, user: { id: "1", username: "davidj", avatar: null }, balance: 12500 };
  return {};
}

const RANKS = [["A", 1], ["2", 2], ["3", 3], ["4", 4], ["5", 5], ["6", 6],
               ["7", 7], ["8", 8], ["9", 9], ["10", 0], ["J", 0], ["Q", 0], ["K", 0]];
const SUITS = ["♠", "♥", "♦", "♣"];
function mockDraw() {
  const [rank, value] = RANKS[Math.floor(Math.random() * RANKS.length)];
  return [rank + SUITS[Math.floor(Math.random() * SUITS.length)], value];
}
function mockTotal(cards) { return cards.reduce((a, c) => a + c[1], 0) % 10; }
function mockBankerDraws(bTotal, pThird) {
  if (pThird == null) return bTotal <= 5;
  if (bTotal <= 2) return true;
  if (bTotal === 3) return pThird !== 8;
  if (bTotal === 4) return pThird >= 2 && pThird <= 7;
  if (bTotal === 5) return pThird >= 4 && pThird <= 7;
  if (bTotal === 6) return pThird >= 6 && pThird <= 7;
  return false;
}
function mockDeal() {
  const player = [mockDraw(), mockDraw()];
  const banker = [mockDraw(), mockDraw()];
  let p = mockTotal(player), b = mockTotal(banker);
  if (p < 8 && b < 8) {
    let pThird = null;
    if (p <= 5) { const c = mockDraw(); player.push(c); pThird = c[1]; }
    if (mockBankerDraws(mockTotal(banker), pThird)) banker.push(mockDraw());
  }
  p = mockTotal(player); b = mockTotal(banker);
  const outcome = p > b ? "player" : b > p ? "banker" : "tie";
  return { player, banker, p, b, outcome };
}
const MOCK_PAY = { player: 2.0, banker: 1.95, tie: 9.0 };
function mockBaccarat(body) {
  const bet = Math.floor(Number(body.bet) || 0);
  const side = MOCK_PAY[body.side] ? body.side : "player";
  const h = mockDeal();
  let payout = 0;
  if (h.outcome === "tie" && (side === "player" || side === "banker")) payout = bet;
  else if (h.outcome === side) payout = Math.round(bet * MOCK_PAY[side]);
  state.balance = Math.max(0, state.balance - bet + payout);
  return {
    detail: {
      player: h.player.map((c) => c[0]), banker: h.banker.map((c) => c[0]),
      player_total: h.p, banker_total: h.b, outcome: h.outcome, side,
    },
    payout, won: payout > bet, bet, balance: state.balance,
  };
}

main();
