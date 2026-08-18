// SharpLab HQ — Horse Race. Pick a horse, place a bet, watch the gallop, and
// win its multiplier if it crosses first. Rounds POST to
// /api/v1/casino/horserace (session-cookie auth); the response carries the
// authoritative new balance, which we push into the nav chip + on-page header.

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
const race = { picked: null, busy: false };

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
             id="bet-hr" inputmode="numeric" />
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
  const el = document.getElementById("bet-hr");
  const v = Math.floor(Number(el && el.value));
  return Number.isFinite(v) ? v : 0;
}

// ── The horses (fixed roster; index is the wire value) ──
const HORSES = [
  { name: "Thunderbolt", odds: 2.2 },
  { name: "Sea Biscuit", odds: 3.5 },
  { name: "Night Fury", odds: 5.5 },
  { name: "Lucky Star", odds: 9.0 },
  { name: "Dark Horse", odds: 15.0 },
  { name: "Moonshot", odds: 30.0 },
];

const $ = (id) => document.getElementById(id);

// ── POST a race. Returns parsed JSON or {error}. ──
async function postRace(body) {
  if (MOCK) return mockRace(body);
  const r = await fetch("/api/v1/casino/horserace", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  const j = await r.json().catch(() => ({}));
  if (r.ok) return j;
  return { error: j.error || (r.status === 401 ? "sign in to play" : `error ${r.status}`) };
}

// ── Selection ──
function pickHorse(i) {
  if (race.busy) return;
  race.picked = i;
  document.querySelectorAll(".hrhorse").forEach((el, idx) => {
    el.classList.toggle("picked", idx === i);
    el.setAttribute("aria-checked", idx === i ? "true" : "false");
  });
  const btn = $("hrRace");
  if (btn) btn.disabled = false;
}

function setBusy(busy) {
  race.busy = busy;
  document.querySelectorAll(".betinput, .chipbtn").forEach((el) => (el.disabled = busy));
  const btn = $("hrRace");
  if (btn) btn.disabled = busy || race.picked == null;
}

// ── Animate the horses to the finish; the given winner crosses first. ──
async function gallop(winner) {
  const lanes = HORSES.map((_, i) => $("horse-" + i));
  if (REDUCE) {
    lanes.forEach((el, i) => { if (el) el.style.left = `${i === winner ? 90 : 60 + Math.random() * 12}%`; });
    return;
  }
  // Give everyone a randomized finishing spread, but force the winner to lead.
  const DUR = 1500;
  const targets = HORSES.map((_, i) => (i === winner ? 90 : 58 + Math.random() * 20));
  lanes.forEach((el) => { if (el) { el.style.transition = `left ${DUR}ms cubic-bezier(.25,.6,.4,1)`; el.classList.add("running"); } });
  // next frame so the transition applies
  await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
  lanes.forEach((el, i) => { if (el) el.style.left = `${targets[i]}%`; });
  await delay(DUR + 60);
  lanes.forEach((el) => { if (el) el.classList.remove("running"); });
}

// ── Run a race ──
async function runRace() {
  if (race.busy) return;
  const horse = race.picked;
  if (horse == null) return toast("Pick a horse first");
  const bet = readBet();
  if (bet < 1) return toast("Enter a bet of at least 1 coin");
  if (bet > Math.floor(state.balance)) return toast("You don't have enough coins for that bet");
  setBusy(true);
  $("hrBanner").className = "hrbanner";
  $("hrBanner").innerHTML = "";
  $("hrRace").textContent = "Racing…";

  const res = await postRace({ bet, horse });
  if (res.error) { setBusy(false); $("hrRace").textContent = "Race!"; return toast("❌ " + res.error); }

  const winner = res.detail && typeof res.detail.winner === "number" ? res.detail.winner : horse;
  await gallop(winner);

  // Mark lanes: winner + your pick.
  document.querySelectorAll(".hrlane").forEach((el, i) => {
    el.classList.toggle("winner", i === winner);
    el.classList.toggle("mypick", i === horse);
  });

  applyBalance(res.balance);

  const won = !!res.won;
  const staked = res.bet != null ? res.bet : bet;
  const payout = res.payout || 0;
  const winName = HORSES[winner] ? HORSES[winner].name : "?";
  const line = won ? `+${coins(payout)}` : `-${coins(staked)}`;
  $("hrBanner").className = "hrbanner show " + (won ? "win" : "lose");
  $("hrBanner").innerHTML =
    `<span class="hrbannerlabel">${won ? "WIN" : "LOSE"}</span>
     <span class="hrbannerwin">🏆 ${esc(winName)}</span>
     <span class="hrbannerpay">${line}</span>`;

  $("hrActions").innerHTML = `<button class="btn primary big" id="hrAgain">Race again</button>`;
  race.busy = false;
  document.querySelectorAll(".betinput, .chipbtn").forEach((el) => (el.disabled = false));
}

// ── Reset to the pre-race, ready-to-pick state ──
function resetRace() {
  race.picked = null;
  buildTable();
}

// ── Delegated events ──
app.addEventListener("click", (e) => {
  const chip = e.target.closest(".chipbtn");
  if (chip) {
    const inp = $("bet-hr");
    if (inp) inp.value = chip.dataset.amt === "max"
      ? Math.max(0, Math.floor(state.balance)) : chip.dataset.amt;
    return;
  }
  const horse = e.target.closest(".hrhorse");
  if (horse) return pickHorse(Number(horse.dataset.i));
  if (e.target.closest("#hrRace")) return runRace();
  if (e.target.closest("#hrAgain")) return resetRace();
});
app.addEventListener("keydown", (e) => {
  if (e.target.classList.contains("hrhorse") && (e.key === "Enter" || e.key === " ")) {
    e.preventDefault(); pickHorse(Number(e.target.dataset.i));
  }
});

// ── Build the race table (roster + track) ──
function buildTable() {
  const roster = HORSES.map((h, i) => `
    <div class="hrhorse" role="radio" tabindex="0" aria-checked="false" data-i="${i}">
      <span class="hremoji">🐎</span>
      <span class="hrname">${esc(h.name)}</span>
      <span class="hrmult">×${h.odds}</span>
    </div>`).join("");

  const lanes = HORSES.map((h, i) => `
    <div class="hrlane" data-i="${i}">
      <span class="hrlanename">${esc(h.name)}</span>
      <div class="hrtrack"><span class="hrrunner" id="horse-${i}">🐎</span></div>
      <span class="hrlanemult">×${h.odds}</span>
    </div>`).join("");

  $("hrTable").innerHTML = `
    <div class="hrroster" role="radiogroup" aria-label="Pick a horse">${roster}</div>
    <div class="hrbanner" id="hrBanner"></div>
    <div class="hrtrackwrap">
      ${lanes}
      <div class="hrfinish" aria-hidden="true"></div>
    </div>`;

  $("hrActions").innerHTML = `<button class="btn primary big" id="hrRace" disabled>Race!</button>`;
}

function buildPage() {
  const signedOut = !state.me
    ? `<div class="card" style="margin-bottom:16px;text-align:center">
        <p class="muted" style="margin:0 0 10px">Sign in with Discord to play with your coins.</p>
        <a class="btn" href="/api/v1/auth/discord/login">Sign in with Discord</a></div>`
    : "";
  app.innerHTML = `
    <div class="hr-head">
      <h1>🐎 Horse Race <span class="balancechip" id="pageBal">${coins(state.balance)}</span></h1>
      <p>Pick a horse, set your stake, and win its multiplier if it crosses first.</p>
    </div>
    ${signedOut}
    <div class="card hrtablecard" id="hrTable"></div>
    <div class="card hrcontrols">
      ${betInput()}
      <div class="hractions" id="hrActions"></div>
    </div>`;
  buildTable();
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
// Mock mode (?mock=1): pick a winner locally, weighted toward the
// favorites, and pay odds×bet so the page is screenshot-testable
// offline. Real endpoint wins by default.
// ─────────────────────────────────────────────────────────────
const MOCK = new URLSearchParams(location.search).get("mock") === "1";

function mockJSON(url) {
  if (url.startsWith("/api/v1/hq/me"))
    return { authenticated: true, user: { id: "1", username: "davidj", avatar: null }, balance: 12500 };
  return {};
}

function mockRace(body) {
  const bet = Math.floor(Number(body.bet) || 0);
  const horse = Number(body.horse) || 0;
  const weights = [40, 25, 16, 10, 6, 3];
  const total = weights.reduce((a, b) => a + b, 0);
  let roll = Math.random() * total, winner = 0;
  for (let i = 0; i < weights.length; i++) { roll -= weights[i]; if (roll <= 0) { winner = i; break; } }
  const won = winner === horse;
  const payout = won ? Math.round(HORSES[horse].odds * bet) : 0;
  state.balance = Math.max(0, state.balance - bet + payout);
  return {
    detail: { winner, picked: horse, horses: HORSES.map((h) => ({ name: h.name, odds: h.odds })) },
    payout, won, bet, balance: state.balance,
  };
}

main();
