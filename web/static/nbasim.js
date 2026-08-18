// SharpLab HQ — NBA Sim. Bet the moneyline of a simulated NBA game for coins.
// /new signs the matchup + odds into a token; /bet decodes it, debits the stake,
// draws the winner (CSPRNG weighted on the true prob so the 5% edge is exact),
// pays out, and returns the authoritative new balance which we push to the nav
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
const game = { token: null, home: null, away: null, ml: null, spread: 0, total: 0,
               side: null, busy: false, resolved: false };

// ── POST to an nbasim endpoint. Returns parsed JSON or {error}. ──
async function postSim(path, body) {
  if (MOCK) return mockSim(path, body);
  const r = await fetch("/api/v1/casino/nbasim" + path, {
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
const fmtSpread = (s) => (s > 0 ? "-" + s : "+" + Math.abs(s)); // home line

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
  game.home = res.home;
  game.away = res.away;
  game.ml = res.moneyline;
  game.spread = res.spread;
  game.total = res.total;
  game.side = null;
  game.resolved = false;
  renderMatchup();
  setBusy(false);
}

// ── Matchup / betting view ──
function renderMatchup() {
  const h = game.home, a = game.away, ml = game.ml;
  const pick = (side, team, odds) => `
    <button class="mlpick" data-side="${side}" aria-pressed="false">
      <span class="mlabbr">${esc(team.abbr)}</span>
      <span class="mlname">${esc(team.name)}</span>
      <span class="mlodds">${esc(odds)}</span>
    </button>`;
  $("simArena").innerHTML = `
    <div class="matchup">
      ${pick("away", a, ml.away_american)}
      <div class="atsep">@</div>
      ${pick("home", h, ml.home_american)}
    </div>
    <div class="siminfo">
      <div class="infochip"><span>Spread</span><b>${esc(h.abbr)} ${fmtSpread(game.spread)}</b></div>
      <div class="infochip"><span>Total</span><b>${num(game.total)}</b></div>
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
  if (!game.side) return toast("Pick a team first");
  const bet = readBet();
  if (bet < 1) return toast("Enter a bet of at least 1 coin");
  if (bet > Math.floor(state.balance)) return toast("You don't have enough coins for that bet");
  setBusy(true);

  // "Simulating…" beat before revealing the result.
  $("simBanner").className = "simbanner sim show";
  $("simBanner").innerHTML = `<span class="simdots">Simulating${REDUCE ? "" : "<i>.</i><i>.</i><i>.</i>"}</span>`;
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

  const homeWon = res.winner === "home";
  document.querySelectorAll(".mlpick").forEach((el) => {
    el.classList.toggle("winner", el.dataset.side === res.winner);
    el.classList.toggle("loser", el.dataset.side !== res.winner);
    el.classList.remove("sel");
  });

  $("simArena").insertAdjacentHTML("beforeend", `
    <div class="finalscore${REDUCE ? "" : " pop"}">
      <div class="fteam${homeWon ? "" : " win"}">
        <span class="fabbr">${esc(res.away_abbr)}</span>
        <span class="fpts">${num(res.away_score)}</span>
      </div>
      <div class="fdash">—</div>
      <div class="fteam${homeWon ? " win" : ""}">
        <span class="fpts">${num(res.home_score)}</span>
        <span class="fabbr">${esc(res.home_abbr)}</span>
      </div>
    </div>`);

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
  $("simActions").innerHTML = `<button class="btn primary big" id="simNew">New game</button>`;
}

function buildPage() {
  const signedOut = !state.me
    ? `<div class="card" style="margin-bottom:16px;text-align:center">
        <p class="muted" style="margin:0 0 10px">Sign in with Discord to play with your coins.</p>
        <a class="btn" href="/api/v1/auth/discord/login">Sign in with Discord</a></div>`
    : "";
  app.innerHTML = `
    <div class="sim-head">
      <h1>🏀 NBA Sim <span class="balancechip" id="pageBal">${coins(state.balance)}</span></h1>
      <p>Pick a moneyline, then sim the game. Odds carry a 5% house edge.</p>
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
  await newGame();
}

// ─────────────────────────────────────────────────────────────
// Mock mode (?mock=1): a self-contained matchup + the SAME weighted-winner and
// payout logic offline, so the page can be screenshot-tested without the server.
// Real endpoints win by default.
// ─────────────────────────────────────────────────────────────
const MOCK = new URLSearchParams(location.search).get("mock") === "1";
const VIG = 0.95;

function mockJSON(url) {
  if (url.startsWith("/api/v1/hq/me"))
    return { authenticated: true, user: { id: "1", username: "davidj", avatar: null }, balance: 12500 };
  return {};
}

const MOCK_TEAMS = [
  ["OKC", "Thunder"], ["BOS", "Celtics"], ["DEN", "Nuggets"], ["NYK", "Knicks"],
  ["LAL", "Lakers"], ["MIL", "Bucks"], ["MIA", "Heat"], ["GSW", "Warriors"],
];
function decToAmerican(dec) {
  if (dec <= 1) return "—";
  if (dec >= 2) return "+" + Math.round((dec - 1) * 100);
  return String(-Math.round(100 / (dec - 1)));
}
let mockGame = null;
function mockSim(path, body) {
  if (path === "/new") {
    const idx = Math.floor(Math.random() * MOCK_TEAMS.length);
    let j = Math.floor(Math.random() * MOCK_TEAMS.length);
    while (j === idx) j = Math.floor(Math.random() * MOCK_TEAMS.length);
    const home = MOCK_TEAMS[idx], away = MOCK_TEAMS[j];
    const homeProb = 0.35 + Math.random() * 0.3; // 0.35–0.65
    const spread = (Math.round((homeProb - 0.5) * 40) || 0) + (homeProb >= 0.5 ? 0.5 : -0.5);
    const total = Math.round((215 + Math.random() * 20) * 2) / 2;
    const homeDec = Math.round((1 / homeProb) * VIG * 1000) / 1000;
    const awayDec = Math.round((1 / (1 - homeProb)) * VIG * 1000) / 1000;
    mockGame = { homeProb, spread, total, home, away, homeDec, awayDec };
    return {
      token: "mock", home: { abbr: home[0], name: home[1] }, away: { abbr: away[0], name: away[1] },
      moneyline: { home_dec: homeDec, away_dec: awayDec,
                   home_american: decToAmerican(homeDec), away_american: decToAmerican(awayDec) },
      spread, total,
    };
  }
  // /bet — same weighted draw + payout logic as the server.
  const g = mockGame;
  const winner = Math.random() < g.homeProb ? "home" : "away";
  const side = body.side === "home" ? "home" : "away";
  const half = g.total / 2;
  let hs = Math.max(70, Math.round(half + g.spread / 2 + (Math.floor(Math.random() * 13) - 6)));
  let as = Math.max(70, Math.round(half - g.spread / 2 + (Math.floor(Math.random() * 13) - 6)));
  if (winner === "home" && hs <= as) hs = as + 1 + Math.floor(Math.random() * 6);
  if (winner === "away" && as <= hs) as = hs + 1 + Math.floor(Math.random() * 6);
  const stake = Math.floor(Number(body.stake) || 0);
  const won = side === winner;
  const dec = side === "home" ? g.homeDec : g.awayDec;
  const payout = won ? Math.round(stake * dec) : 0;
  state.balance = Math.max(0, state.balance - stake + payout);
  return { home_abbr: g.home[0], away_abbr: g.away[0], home_score: hs, away_score: as,
           winner, side, won, payout, stake, balance: state.balance };
}

main();
