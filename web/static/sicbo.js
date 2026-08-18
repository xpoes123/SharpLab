// SharpLab HQ — Sic Bo (three-dice betting). Pick a bet type, stake coins, roll.
// SMALL (sum 4–10) and BIG (sum 11–17) pay 2× but lose on any triple (three of a
// kind). A chosen NUMBER 1–6 pays 2×/3×/4× if it shows on 1/2/3 of the dice.
// Each roll POSTs to /api/v1/casino/sicbo (session-cookie auth); the resolved
// response carries the authoritative new balance, pushed into the nav chip +
// on-page header.

const app = document.getElementById("app");
const navRight = document.getElementById("navRight");

// ── Helpers (copied verbatim from craps.js / casino.js) ──
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

// ── Nav (login / logout) — mirrors craps.js (reads state.balance) ──
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
const round = { busy: false, kind: "small", value: null };

// ── POST a Sic Bo roll. Returns parsed JSON or {error}. ──
async function postSicBo(body) {
  if (MOCK) return mockSicBo(body);
  const r = await fetch("/api/v1/casino/sicbo", {
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

// ── Toast (copied verbatim from craps.js) ──
function toast(msg) {
  const t = document.createElement("div");
  t.className = "cardtoast";
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 3200);
}

// ── Coins hub (copied verbatim from craps.js) ──
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

// ── Shared bet-input block (copied from craps.js) ──
function betInput() {
  return `<div class="betrow">
    <div class="betlbl">Bet</div>
    <div class="betline">
      <input class="betinput" type="number" min="1" step="1" value="100"
             id="bet-sb" inputmode="numeric" />
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
  const el = document.getElementById("bet-sb");
  const v = Math.floor(Number(el && el.value));
  return Number.isFinite(v) ? v : 0;
}

// ── Dice rendering (pip layouts copied from craps.js) ──
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
  return `<div class="sbdie${REDUCE ? "" : " roll"}" aria-label="die showing ${v}">${pips}</div>`;
}
function renderDice(dice) {
  return (dice || []).map(dieFace).join("");
}

// ── UI refs ──
const $ = (id) => document.getElementById(id);

function setBusy(busy) {
  round.busy = busy;
  const btn = $("sbRoll");
  if (btn) btn.disabled = busy;
  document.querySelectorAll(".sbtype").forEach((el) => (el.disabled = busy));
  const inp = $("bet-sb");
  if (inp) inp.disabled = busy;
}

// ── Bet-type selector ──
function selectKind(kind, value) {
  round.kind = kind;
  round.value = value == null ? null : Number(value);
  document.querySelectorAll(".sbtype").forEach((el) => {
    const on = el.dataset.kind === kind &&
      (kind !== "num" || Number(el.dataset.value) === round.value);
    el.classList.toggle("on", on);
    el.setAttribute("aria-pressed", on ? "true" : "false");
  });
}

// ── Show the dice + total for a roll ──
function showRoll(dice, total) {
  $("sbDice").innerHTML = renderDice(dice);
  $("sbTotal").innerHTML = `<span class="lbl">Total</span>${num(total)}`;
}

// ── What did the player wager, in words? ──
function betLabel() {
  if (round.kind === "small") return "SMALL (4–10)";
  if (round.kind === "big") return "BIG (11–17)";
  return `NUMBER ${round.value}`;
}

// ── Render the resolved outcome banner ──
function renderResolved(res) {
  const d = res.detail || {};
  applyBalance(res.balance);
  showRoll(d.dice, d.total);

  const won = !!res.won;
  const payout = res.payout || 0;
  const bet = res.bet || 0;
  const label = won ? "WIN" : "LOSE";
  const line = won ? `+${coins(payout - bet)}` : `-${coins(bet)}`;

  // Explain triple losses on Small/Big; count for number bets.
  let note = "";
  if (d.triple && (d.kind === "small" || d.kind === "big") && !won) {
    note = `<span class="sbnote">Triple ${num(d.dice && d.dice[0])} — ${d.kind === "small" ? "Small" : "Big"} loses on any triple</span>`;
  } else if (d.kind === "num") {
    const c = (d.dice || []).filter((x) => x === d.num).length;
    note = `<span class="sbnote">${num(d.num)} showed on ${c} ${c === 1 ? "die" : "dice"}${c ? ` — pays ${c + 1}×` : ""}</span>`;
  }

  $("sbBanner").className = "sbbanner show " + (won ? "win" : "lose");
  $("sbBanner").innerHTML = `<span class="sbbannerlabel">${label}</span>
    <span class="sbbannerpay">${line}</span>`;
  $("sbNote").innerHTML = note;
}

// ── Roll ──
async function roll() {
  if (round.busy) return;
  const bet = readBet();
  if (bet < 1) return toast("Enter a bet of at least 1 coin");
  if (bet > Math.floor(state.balance)) return toast("You don't have enough coins for that bet");
  const body = { bet, kind: round.kind };
  if (round.kind === "num") body.value = round.value;

  setBusy(true);
  $("sbBanner").className = "sbbanner";
  $("sbBanner").innerHTML = "";
  $("sbNote").innerHTML = "";
  const res = await postSicBo(body);
  if (res.error) { setBusy(false); return toast("❌ " + res.error); }
  // Show a tumbling roll before the reveal.
  if (!REDUCE) {
    showRoll(res.detail && res.detail.dice, res.detail && res.detail.total);
    await delay(520);
  }
  renderResolved(res);
  setBusy(false);
}

// ── Delegated events ──
app.addEventListener("click", (e) => {
  const chip = e.target.closest(".chipbtn");
  if (chip) {
    const inp = $("bet-sb");
    if (inp) inp.value = chip.dataset.amt === "max"
      ? Math.max(0, Math.floor(state.balance)) : chip.dataset.amt;
    return;
  }
  const type = e.target.closest(".sbtype");
  if (type) return selectKind(type.dataset.kind, type.dataset.value);
  if (e.target.closest("#sbRoll")) return roll();
});
app.addEventListener("keydown", (e) => {
  if (e.target.id === "bet-sb" && e.key === "Enter") { e.preventDefault(); roll(); }
});

function buildPage() {
  const signedOut = !state.me
    ? `<div class="card" style="margin-bottom:16px;text-align:center">
        <p class="muted" style="margin:0 0 10px">Sign in with Discord to play with your coins.</p>
        <a class="btn" href="/api/v1/auth/discord/login">Sign in with Discord</a></div>`
    : "";
  const numTiles = [1, 2, 3, 4, 5, 6].map(
    (n) => `<button class="sbtype sbnum" data-kind="num" data-value="${n}" aria-pressed="false">
      <span class="sbnumpip">${dieFace(n)}</span></button>`
  ).join("");
  app.innerHTML = `
    <div class="sb-head">
      <h1>🎲 Sic Bo <span class="balancechip" id="pageBal">${coins(state.balance)}</span></h1>
      <p>Three dice. Bet SMALL (sum 4–10) or BIG (11–17) — both pay 2× but lose on any triple. Or back a number 1–6: pays 2× / 3× / 4× if it lands on 1 / 2 / 3 dice.</p>
    </div>
    ${signedOut}
    <div class="card sbtable">
      <div class="sbdice" id="sbDice">
        <div class="sbdie empty"></div><div class="sbdie empty"></div><div class="sbdie empty"></div>
      </div>
      <div class="sbtotal" id="sbTotal"><span class="lbl">Total</span>—</div>
      <div class="sbbanner" id="sbBanner"></div>
      <div class="sbnote-wrap" id="sbNote"></div>
    </div>
    <div class="card sbcontrols">
      <div class="sbtypes">
        <div class="sbtypelbl">Bet type</div>
        <div class="sbtyperow">
          <button class="sbtype sbwide on" data-kind="small" aria-pressed="true">SMALL <span class="sbsub">4–10</span></button>
          <button class="sbtype sbwide" data-kind="big" aria-pressed="false">BIG <span class="sbsub">11–17</span></button>
        </div>
        <div class="sbnumlbl">Or a single number</div>
        <div class="sbnumrow">${numTiles}</div>
      </div>
      <div class="sbfooter">
        ${betInput()}
        <div class="sbaction">
          <button class="btn primary big" id="sbRoll">Roll</button>
        </div>
      </div>
    </div>`;
  selectKind(round.kind, round.value);
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
// Mock mode (?mock=1): roll three dice locally with the same
// payout rules so the page works offline (screenshot-testable).
// Real endpoint wins by default.
// ─────────────────────────────────────────────────────────────
const MOCK = new URLSearchParams(location.search).get("mock") === "1";

function mockJSON(url) {
  if (url.startsWith("/api/v1/hq/me"))
    return { authenticated: true, user: { id: "1", username: "davidj", avatar: null }, balance: 12500 };
  return {};
}

const d6 = () => 1 + Math.floor(Math.random() * 6);
function mockSicBo(body) {
  const bet = Math.floor(Number(body.bet) || 0);
  const kind = body.kind;
  const value = (body.value >= 1 && body.value <= 6) ? Number(body.value) : 1;
  const dice = [d6(), d6(), d6()];
  const total = dice[0] + dice[1] + dice[2];
  const triple = dice[0] === dice[1] && dice[1] === dice[2];
  let mult = 0;
  if (kind === "small") mult = (total >= 4 && total <= 10 && !triple) ? 2 : 0;
  else if (kind === "big") mult = (total >= 11 && total <= 17 && !triple) ? 2 : 0;
  else {
    const c = dice.filter((x) => x === value).length;
    mult = c ? c + 1 : 0;
  }
  const payout = bet * mult;
  state.balance = Math.max(0, state.balance - bet + payout);
  return {
    detail: { dice, total, triple, kind, num: value },
    payout, won: payout > bet, bet, balance: state.balance,
  };
}

main();
