// SharpLab HQ — Tennis Sim. Bet the moneyline of a simulated tennis match for
// coins. /new signs the matchup + true p1 win prob into a token; /bet decodes it,
// debits the stake, draws the winner (CSPRNG weighted on the true prob so the
// priced payout is exact), pays out, and returns the authoritative new balance
// which we push to the nav chip + on-page header. Tennis flavor: sets, best-of-3.

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

// ── Nav (login / logout) — mirrors blackjack.js (reads state.balance) ──
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
const game = { token: null, p1: null, p2: null, ml: null,
               side: null, busy: false, resolved: false };

// ── POST to a tennissim endpoint. Returns parsed JSON or {error}. ──
async function postSim(path, body) {
  if (MOCK) return mockSim(path, body);
  const r = await fetch("/api/v1/casino/tennissim" + path, {
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

// ── Shared bet-input block (copied from casino.js / blackjack.js) ──
function betInput() {
  return `<div class="betrow">
    <div class="betlbl">Bet</div>
    <div class="betline">
      <input class="betinput" type="number" min="1" step="1" value="100"
             id="bet-sim" inputmode="numeric" />
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
  const el = document.getElementById("bet-sim");
  const v = Math.floor(Number(el && el.value));
  return Number.isFinite(v) ? v : 0;
}

const $ = (id) => document.getElementById(id);

function setBusy(busy) {
  game.busy = busy;
  document.querySelectorAll("#app .btn, #app .mlpick").forEach((el) => (el.disabled = busy));
}

// ── Load a fresh matchup ──
async function newGame() {
  if (game.busy) return;
  setBusy(true);
  const res = await postSim("/new", {});
  if (res.error) { setBusy(false); return toast("❌ " + res.error); }
  game.token = res.token;
  game.p1 = res.p1;
  game.p2 = res.p2;
  game.ml = {
    p1_odds: res.p1_odds, p2_odds: res.p2_odds,
    p1_mult: res.p1_mult, p2_mult: res.p2_mult,
    p1_prob: res.p1_prob,
  };
  game.side = null;
  game.resolved = false;
  renderMatchup();
  setBusy(false);
}

// ── Matchup / betting view ──
function renderMatchup() {
  const p1 = game.p1, p2 = game.p2, ml = game.ml;
  const p1Prob = ml.p1_prob, p2Prob = 1 - p1Prob;
  const pick = (side, player, odds, mult, prob) => `
    <button class="mlpick" data-side="${side}" aria-pressed="false">
      <span class="mlabbr">${esc(player.abbr)}</span>
      <span class="mlname">${esc(player.name)}</span>
      <span class="mlodds">${esc(odds)}</span>
      <span class="mlmeta">${Math.round(prob * 100)}% win · ${mult.toFixed(2)}x</span>
    </button>`;
  $("simArena").innerHTML = `
    <div class="matchup">
      ${pick("p1", p1, ml.p1_odds, ml.p1_mult, p1Prob)}
      <div class="atsep">vs</div>
      ${pick("p2", p2, ml.p2_odds, ml.p2_mult, p2Prob)}
    </div>`;
  $("simBanner").className = "simbanner";
  $("simBanner").innerHTML = "";
  $("simActions").innerHTML = `
    ${betInput()}
    <button class="btn primary big" id="simBet" disabled>Pick a side to bet</button>`;
}

function selectSide(side) {
  if (game.busy || game.resolved) return;
  game.side = side;
  document.querySelectorAll(".mlpick").forEach((el) => {
    const on = el.dataset.side === side;
    el.classList.toggle("sel", on);
    el.setAttribute("aria-pressed", on ? "true" : "false");
  });
  const btn = $("simBet");
  if (btn) { btn.disabled = false; btn.textContent = "Place bet & sim"; }
}

// ── Place the bet + simulate ──
async function placeBet() {
  if (game.busy || game.resolved) return;
  if (!game.side) return toast("Pick a player first");
  const bet = readBet();
  if (bet < 1) return toast("Enter a bet of at least 1 coin");
  if (bet > Math.floor(state.balance)) return toast("You don't have enough coins for that bet");
  setBusy(true);

  // "Simulating…" beat before revealing the result.
  $("simBanner").className = "simbanner sim show";
  $("simBanner").innerHTML = `<span class="simdots">Playing the match${REDUCE ? "" : "<i>.</i><i>.</i><i>.</i>"}</span>`;
  if (!REDUCE) await delay(1100);

  const res = await postSim("/bet", { token: game.token, side: game.side, stake: bet });
  if (res.error) {
    setBusy(false);
    game.busy = false;
    $("simBanner").className = "simbanner";
    $("simBanner").innerHTML = "";
    // Re-enable the pick buttons/bet button after a soft error (e.g. expired token).
    document.querySelectorAll("#app .btn, #app .mlpick").forEach((el) => (el.disabled = false));
    if ($("simBet")) $("simBet").disabled = !game.side;
    return toast("❌ " + res.error);
  }
  renderResult(res);
  setBusy(false);
}

// ── Final score + win/lose reveal ──
function renderResult(res) {
  game.resolved = true;
  applyBalance(res.balance);

  const p1Won = res.winner === "p1";
  document.querySelectorAll(".mlpick").forEach((el) => {
    el.classList.toggle("winner", el.dataset.side === res.winner);
    el.classList.toggle("loser", el.dataset.side !== res.winner);
    el.classList.remove("sel");
  });

  $("simArena").insertAdjacentHTML("beforeend", `
    <div class="finalscore${REDUCE ? "" : " pop"}">
      <div class="fteam${p1Won ? " win" : ""}">
        <span class="fabbr">${esc(res.p1_abbr)}</span>
        <span class="fpts">${num(res.p1_sets)}</span>
      </div>
      <div class="fdash">—</div>
      <div class="fteam${p1Won ? "" : " win"}">
        <span class="fpts">${num(res.p2_sets)}</span>
        <span class="fabbr">${esc(res.p2_abbr)}</span>
      </div>
    </div>
    <div class="fruns">Final · sets</div>`);

  const net = res.payout - res.stake;
  if (res.won) {
    $("simBanner").className = "simbanner show win";
    $("simBanner").innerHTML = `<span class="bnlabel">WIN</span>
      <span class="bnpay">+${coins(net > 0 ? net : res.payout)}</span>`;
  } else {
    $("simBanner").className = "simbanner show lose";
    $("simBanner").innerHTML = `<span class="bnlabel">LOSE</span>
      <span class="bnpay">-${coins(res.stake)}</span>`;
  }
  $("simActions").innerHTML = `<button class="btn primary big" id="simNew">New match</button>`;
}

function buildPage() {
  const signedOut = !state.me
    ? `<div class="card" style="margin-bottom:16px;text-align:center">
        <p class="muted" style="margin:0 0 10px">Sign in with Discord to play with your coins.</p>
        <a class="btn" href="/api/v1/auth/discord/login">Sign in with Discord</a></div>`
    : "";
  app.innerHTML = `
    <div class="sim-head">
      <h1>🎾 Tennis Sim <span class="balancechip" id="pageBal">${coins(state.balance)}</span></h1>
      <p>Pick a moneyline, then sim the match. Winners are paid at the true-probability price.</p>
    </div>
    ${signedOut}
    <div class="card simtable">
      <div class="simarena" id="simArena">
        <div class="hero"><p class="muted">Loading matchup…</p></div>
      </div>
      <div class="simbanner" id="simBanner"></div>
    </div>
    <div class="card simcontrols" id="simActions"></div>`;
}

// ── Delegated events ──
app.addEventListener("click", (e) => {
  const chip = e.target.closest(".chipbtn");
  if (chip) {
    const inp = $("bet-sim");
    if (inp) inp.value = chip.dataset.amt === "max"
      ? Math.max(0, Math.floor(state.balance)) : chip.dataset.amt;
    return;
  }
  const pick = e.target.closest(".mlpick");
  if (pick) return selectSide(pick.dataset.side);
  if (e.target.closest("#simBet")) return placeBet();
  if (e.target.closest("#simNew")) return newGame();
});
app.addEventListener("keydown", (e) => {
  if (e.target.id === "bet-sim" && e.key === "Enter") { e.preventDefault(); placeBet(); }
});

async function main() {
  const me = await getJSON("/api/v1/hq/me");
  const loggedIn = me && me.authenticated;
  state.me = loggedIn ? me : null;
  state.balance = loggedIn ? (me.balance || 0) : 0;
  renderNav(loggedIn ? me.user : null);
  buildPage();
  if (loggedIn) await newGame();  // signed-out users see the sign-in panel, no matchup fetch
}

// ─────────────────────────────────────────────────────────────
// Mock mode (?mock=1): a self-contained matchup + the SAME weighted-winner and
// payout logic offline, so the page can be screenshot-tested without the server.
// Real endpoints win by default.
// ─────────────────────────────────────────────────────────────
const MOCK = new URLSearchParams(location.search).get("mock") === "1";

function mockJSON(url) {
  if (url.startsWith("/api/v1/hq/me"))
    return { authenticated: true, user: { id: "1", username: "davidj", avatar: null }, balance: 12500 };
  return {};
}

const MOCK_PLAYERS = [
  ["Sinner", "Jannik Sinner"], ["Alcaraz", "Carlos Alcaraz"], ["Djokovic", "Novak Djokovic"],
  ["Zverev", "Alexander Zverev"], ["Medvedev", "Daniil Medvedev"], ["Fritz", "Taylor Fritz"],
  ["Rublev", "Andrey Rublev"], ["Rune", "Holger Rune"],
];
function probToAmerican(prob) {
  if (prob >= 0.5) return String(-Math.round((prob / (1 - prob)) * 100));
  return "+" + Math.round(((1 - prob) / prob) * 100);
}
let mockGame = null;
function mockSim(path, body) {
  if (path === "/new") {
    const idx = Math.floor(Math.random() * MOCK_PLAYERS.length);
    let j = Math.floor(Math.random() * MOCK_PLAYERS.length);
    while (j === idx) j = Math.floor(Math.random() * MOCK_PLAYERS.length);
    const p1 = MOCK_PLAYERS[idx], p2 = MOCK_PLAYERS[j];
    const p1Prob = 0.35 + Math.random() * 0.3; // 0.35–0.65
    mockGame = { p1Prob, p1, p2 };
    return {
      token: "mock", p1: { abbr: p1[0], name: p1[1] }, p2: { abbr: p2[0], name: p2[1] },
      p1_prob: p1Prob,
      p1_odds: probToAmerican(p1Prob), p2_odds: probToAmerican(1 - p1Prob),
      p1_mult: 1 / p1Prob, p2_mult: 1 / (1 - p1Prob),
    };
  }
  // /bet — same weighted draw + payout logic as the server.
  const g = mockGame;
  const winner = Math.random() < g.p1Prob ? "p1" : "p2";
  const side = body.side === "p1" ? "p1" : "p2";
  // Flavor sets: winner takes 2, loser gets 0 or 1.
  let p1Sets, p2Sets;
  if (winner === "p1") { p1Sets = 2; p2Sets = Math.floor(Math.random() * 2); }
  else { p2Sets = 2; p1Sets = Math.floor(Math.random() * 2); }
  const stake = Math.floor(Number(body.stake) || 0);
  const won = side === winner;
  const mult = side === "p1" ? 1 / g.p1Prob : 1 / (1 - g.p1Prob);
  const payout = won ? Math.floor(stake * mult) : 0;
  state.balance = Math.max(0, state.balance - stake + payout);
  return { p1_abbr: g.p1[0], p2_abbr: g.p2[0], p1_sets: p1Sets, p2_sets: p2Sets,
           winner, winner_abbr: winner === "p1" ? g.p1[0] : g.p2[0],
           side, won, payout, stake, balance: state.balance };
}

main();
