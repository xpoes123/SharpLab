// SharpLab HQ — Crapless Craps. Roll the dice for coins.
// Come-out roll: ONLY a 7 wins instantly (2×). NOTHING craps out — every other
// total (2, 3, 4, 5, 6, 8, 9, 10, 11, 12) becomes the POINT. Then keep rolling
// until the point repeats (win 2×) or a 7 shows (seven out, lose). Rounds POST
// to /api/v1/casino/crapless/* (session-cookie auth); each resolved response
// carries the authoritative new balance, which we push into the nav chip +
// on-page header.

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
const round = { id: null, live: false, busy: false, point: null, bet: 0 };

// ── POST to a crapless endpoint. Returns parsed JSON or {error}. ──
async function postCraps(path, body) {
  if (MOCK) return mockCraps(path, body);
  const r = await fetch("/api/v1/casino/crapless" + path, {
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
             id="bet-cr" inputmode="numeric" />
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
  const el = document.getElementById("bet-cr");
  const v = Math.floor(Number(el && el.value));
  return Number.isFinite(v) ? v : 0;
}

// ── Dice rendering ──
// Pip layouts for a face value 1–6, as grid-slot class names.
const PIP_LAYOUT = {
  1: ["c"],
  2: ["nw", "se"],
  3: ["nw", "c", "se"],
  4: ["nw", "ne", "sw", "se"],
  5: ["nw", "ne", "c", "sw", "se"],
  6: ["nw", "ne", "w", "e", "sw", "se"],
};
function dieFace(v) {
  const pips = (PIP_LAYOUT[v] || []).map((s) => `<span class="pip ${s}"></span>`).join("");
  return `<div class="crdie${REDUCE ? "" : " roll"}" aria-label="die showing ${v}">${pips}</div>`;
}
function renderDice(dice) {
  return (dice || []).map(dieFace).join("");
}

// ── UI refs ──
const $ = (id) => document.getElementById(id);

function setBusy(busy) {
  round.busy = busy;
  document.querySelectorAll(".craction .btn").forEach((el) => (el.disabled = busy));
}

// ── Render the dice + total for the latest roll ──
function showRoll(dice, total) {
  $("crDice").innerHTML = renderDice(dice);
  $("crTotal").innerHTML = `<span class="lbl">Total</span>${num(total)}`;
}

// ── Come-out point set: keep rolling ──
function renderPoint(res) {
  round.id = res.round_id;
  round.live = true;
  round.point = res.point;
  showRoll(res.dice, res.total);
  $("crPhase").className = "crphase point";
  $("crPhase").innerHTML = `Point <span class="crpoint">POINT: ${num(res.point)}</span>`;
  $("crBanner").className = "crbanner";
  $("crBanner").innerHTML = "";
  $("crAction").innerHTML = `<button class="btn primary big" id="crRoll">Roll</button>`;
}

// ── Terminal result: win / lose ──
function renderResolved(res, staked) {
  round.live = false;
  round.id = null;
  round.point = null;
  applyBalance(res.balance);
  showRoll(res.dice, res.total);
  $("crPhase").className = "crphase";
  $("crPhase").innerHTML = `<span class="crpoint">Round over</span>`;

  const win = res.result === "win";
  const RESULT_META = {
    "win": { cls: "win", label: "WIN" },
    "seven-out": { cls: "lose", label: "SEVEN OUT" },
  };
  const meta = RESULT_META[res.result] || { cls: win ? "win" : "lose", label: String(res.result || "").toUpperCase() };
  const payout = res.payout || 0;
  const line = win ? `+${coins(payout - staked > 0 ? payout - staked : payout)}` : `-${coins(staked)}`;
  $("crBanner").className = "crbanner show " + meta.cls;
  $("crBanner").innerHTML = `<span class="crbannerlabel">${meta.label}</span>
    <span class="crbannerpay">${line}</span>`;
  $("crAction").innerHTML = `<button class="btn primary big" id="crNew">New round</button>`;
}

// ── Come-out roll ──
async function comeout() {
  if (round.busy) return;
  const bet = readBet();
  if (bet < 1) return toast("Enter a bet of at least 1 coin");
  if (bet > Math.floor(state.balance)) return toast("You don't have enough coins for that bet");
  round.bet = bet;
  setBusy(true);
  const res = await postCraps("/comeout", { bet });
  if (res.error) { setBusy(false); return toast("❌ " + res.error); }
  if (!REDUCE) await delay(480);
  if (res.done) renderResolved(res, bet);
  else renderPoint(res);
  setBusy(false);
}

// ── Roll toward the point ──
async function roll() {
  if (round.busy || !round.live) return;
  setBusy(true);
  const res = await postCraps("/roll", { round_id: round.id });
  if (res.error) { setBusy(false); return toast("❌ " + res.error); }
  if (!REDUCE) await delay(480);
  if (res.done) {
    renderResolved(res, round.bet);
  } else {
    // Still rolling toward the point — refresh dice, keep the Roll button.
    round.point = res.point;
    showRoll(res.dice, res.total);
    $("crPhase").innerHTML = `Point <span class="crpoint">POINT: ${num(res.point)}</span>`;
  }
  setBusy(false);
}

// ── Reset to the pre-roll (come-out) state ──
function resetToComeout() {
  round.live = false;
  round.id = null;
  round.point = null;
  $("crDice").innerHTML = `<div class="crdie empty"></div><div class="crdie empty"></div>`;
  $("crTotal").innerHTML = `<span class="lbl">Total</span>—`;
  $("crPhase").className = "crphase";
  $("crPhase").innerHTML = `Come-out roll`;
  $("crBanner").className = "crbanner";
  $("crBanner").innerHTML = "";
  $("crAction").innerHTML = `<button class="btn primary big" id="crComeout">Roll (come-out)</button>`;
}

// ── Delegated events ──
app.addEventListener("click", (e) => {
  const chip = e.target.closest(".chipbtn");
  if (chip) {
    const inp = $("bet-cr");
    if (inp) inp.value = chip.dataset.amt === "max"
      ? Math.max(0, Math.floor(state.balance)) : chip.dataset.amt;
    return;
  }
  if (e.target.closest("#crComeout")) return comeout();
  if (e.target.closest("#crRoll")) return roll();
  if (e.target.closest("#crNew")) return resetToComeout();
});
app.addEventListener("keydown", (e) => {
  if (e.target.id === "bet-cr" && e.key === "Enter") { e.preventDefault(); comeout(); }
});

function buildPage() {
  const signedOut = !state.me
    ? `<div class="card" style="margin-bottom:16px;text-align:center">
        <p class="muted" style="margin:0 0 10px">Sign in with Discord to play with your coins.</p>
        <a class="btn" href="/api/v1/auth/discord/login">Sign in with Discord</a></div>`
    : "";
  app.innerHTML = `
    <div class="cr-head">
      <h1>🎲 Crapless Craps <span class="balancechip" id="pageBal">${coins(state.balance)}</span></h1>
      <p>Crapless — nothing craps out on the come-out; only a 7 wins, everything else sets the point (including 2, 3, 11, 12). Then roll the point again before a 7. Pays 2×.</p>
    </div>
    ${signedOut}
    <div class="card crtable">
      <div class="crphase" id="crPhase">Come-out roll</div>
      <div class="crdice" id="crDice">
        <div class="crdie empty"></div><div class="crdie empty"></div>
      </div>
      <div class="crtotal" id="crTotal"><span class="lbl">Total</span>—</div>
      <div class="crbanner" id="crBanner"></div>
    </div>
    <div class="card crcontrols">
      ${betInput()}
      <div class="craction" id="crAction">
        <button class="btn primary big" id="crComeout">Roll (come-out)</button>
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
// Mock mode (?mock=1): roll two dice locally and drive the same
// come-out/point state machine offline so the page can be
// screenshot-tested. Real endpoints win by default.
// Crapless rules: come-out 7 wins, every other total sets the point.
// ─────────────────────────────────────────────────────────────
const MOCK = new URLSearchParams(location.search).get("mock") === "1";

function mockJSON(url) {
  if (url.startsWith("/api/v1/hq/me"))
    return { authenticated: true, user: { id: "1", username: "davidj", avatar: null }, balance: 12500 };
  return {};
}

const d6 = () => 1 + Math.floor(Math.random() * 6);
function rollPair() {
  const dice = [d6(), d6()];
  return { dice, total: dice[0] + dice[1] };
}
const mockRounds = {};
function mockCraps(path, body) {
  if (path === "/comeout") {
    const bet = Math.floor(Number(body.bet) || 0);
    const { dice, total } = rollPair();
    if (total === 7) {
      const payout = bet * 2;
      state.balance = Math.max(0, state.balance - bet + payout);
      return { done: true, dice, total, result: "win", payout, balance: state.balance };
    }
    // Crapless: any non-7 total (2–12) sets the point — nothing craps out.
    const rid = "mock-" + Date.now();
    mockRounds[rid] = { bet, point: total };
    return { done: false, round_id: rid, dice, total, point: total };
  }
  // /roll
  const g = mockRounds[body.round_id];
  if (!g) return { error: "round not found" };
  const { dice, total } = rollPair();
  if (total === g.point) {
    const payout = g.bet * 2;
    state.balance = Math.max(0, state.balance - g.bet + payout);
    delete mockRounds[body.round_id];
    return { done: true, dice, total, point: g.point, result: "win", payout, balance: state.balance };
  }
  if (total === 7) {
    state.balance = Math.max(0, state.balance - g.bet);
    delete mockRounds[body.round_id];
    return { done: true, dice, total, point: g.point, result: "seven-out", payout: 0, balance: state.balance };
  }
  return { done: false, dice, total, point: g.point };
}

main();
