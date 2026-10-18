// SharpLab HQ — Sports Sim. One page for all five sport sims: pick a sport,
// bet the moneyline on either side, watch an animated play-by-play, then the
// bet settles. Rounds POST to /api/v1/casino/sim/* (session-cookie auth). The
// /bet response carries the authoritative new balance + the play-by-play
// timeline (cumulative scores per event), which we animate before revealing
// the outcome. Idioms (me fetch, nav chip, toast, esc, mock mode) mirror
// threecardpoker.js.

const app = document.getElementById("app");
const navRight = document.getElementById("navRight");

// ── Helpers (copied from threecardpoker.js) ──
const num = (n) => (n == null ? "—" : Number(n).toLocaleString());
const coins = (n) => "🪙 " + num(Math.round(n || 0));
const esc = (s) =>
  String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const REDUCE = matchMedia("(prefers-reduced-motion: reduce)").matches;
const delay = (ms) => new Promise((r) => setTimeout(r, ms));
const $ = (id) => document.getElementById(id);

// NFL simcast helpers.
const ordinal = (n) => ["0th", "1st", "2nd", "3rd", "4th"][n] || n + "th";
const fmtSigned = (x) => (Number(x) > 0 ? "+" + x : String(x));
// Decimal odds → American string (for spread/total prices the backend gives as decimal).
function decToAmerican(dec) {
  if (!dec || dec <= 1) return "—";
  return dec >= 2 ? "+" + Math.round((dec - 1) * 100) : String(-Math.round(100 / (dec - 1)));
}

// Sport metadata (order + icons mirror web/sim.py SPORTS).
const SPORTS = [
  ["nba", "NBA", "🏀"],
  ["nfl", "NFL", "🏈"],
  ["mlb", "MLB", "⚾"],
  ["tennis", "Tennis", "🎾"],
  ["soccer", "Soccer", "⚽"],
];
const SPORT_ICON = Object.fromEntries(SPORTS.map(([id, , ic]) => [id, ic]));

async function getJSON(url) {
  if (MOCK) return mockJSON(url);
  const r = await fetch(url, { credentials: "include" });
  if (!r.ok) return { _status: r.status };
  return r.json();
}

// ── POST to a sim endpoint. Returns parsed JSON or {error}. ──
async function postSim(path, body) {
  if (MOCK) return mockSim(path, body);
  const r = await fetch("/api/v1/casino/sim" + path, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  const j = await r.json().catch(() => ({}));
  if (r.ok) return j;
  return { error: j.error || (r.status === 401 ? "sign in to play" : `error ${r.status}`) };
}

// ── State ──
const state = { me: null, balance: 0 };
const game = { busy: false, matchup: null, market: "ml", side: null, result: null };
const anim = { skip: false, done: false, finish: null };

// ── Pacing (NFL auto + manual only) ──
// Default is SLOW for the sweat: ~2.2s/play at 1×. ½×≈4.4s, 2×≈1.1s. Speed is
// persisted across games; pause/skip reset each game. paceWait() polls so that
// pause holds the loop and a mid-wait speed change takes effect immediately.
const BASE_PLAY_MS = 2200;
const pace = { speed: 1, paused: false, skip: false };
function resetPace() { pace.paused = false; pace.skip = false; }

async function paceWait() {
  let frac = 0;
  const step = 60;
  while (frac < 1) {
    if (pace.skip) return;
    if (!pace.paused) frac += step / (BASE_PLAY_MS / pace.speed);
    await delay(step);
  }
}

// Speed (½/1/2×) + Pause/Resume + Skip control bar, used by both NFL modes.
function controlsBar() {
  const spd = (v, l) =>
    `<button class="spdbtn${pace.speed === v ? " sel" : ""}" data-speed="${v}">${l}</button>`;
  return `<div class="simctrls" id="simCtrls">
    <div class="spdgroup" role="group" aria-label="Playback speed">
      ${spd(0.5, "½×")}${spd(1, "1×")}${spd(2, "2×")}
    </div>
    <button class="btn ghost" id="simPause">${pace.paused ? "▶ Resume" : "⏸ Pause"}</button>
    <button class="btn ghost" id="simSkip">Skip ▶▶</button>
  </div>`;
}

const fmtAmer = (x) =>
  x == null ? "—" : typeof x === "number" ? (x > 0 ? "+" + x : String(x)) : String(x);

// ── Nav (login / logout) — mirrors threecardpoker.js ──
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

function applyBalance(bal) {
  if (bal == null) return;
  state.balance = bal;
  renderNav(state.me && state.me.user);
  const pb = $("pageBal");
  if (pb) pb.textContent = coins(bal);
}

// ── Toast (copied from threecardpoker.js) ──
function toast(msg) {
  const t = document.createElement("div");
  t.className = "cardtoast";
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 3200);
}

// ── Coins hub (copied from threecardpoker.js) ──
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

// ── Shared page header ──
function pageHead(sub) {
  return `<div class="sim-head">
    <h1>🎮 Sports Sim <span class="balancechip" id="pageBal">${coins(state.balance)}</span></h1>
    <p>${esc(sub)}</p>
  </div>`;
}

// ═══════════════ Views ═══════════════

function signedOutView() {
  app.innerHTML =
    pageHead("Pick a sport, bet the moneyline, and watch it play out.") +
    `<div class="card" style="text-align:center;padding:26px 20px">
      <p class="muted" style="margin:0 0 12px">Sign in with Discord to play with your coins.</p>
      <a class="btn" href="/api/v1/auth/discord/login">Sign in with Discord</a>
    </div>`;
}

function pickerView() {
  game.matchup = null;
  game.side = null;
  game.result = null;
  const tiles = SPORTS.map(
    ([id, label, icon]) => `<button class="sporttile" data-sport="${id}">
      <span class="st-icon">${icon}</span>
      <span class="st-label">${esc(label)}</span>
    </button>`
  ).join("");
  app.innerHTML =
    pageHead("Choose a sport — we'll price a moneyline and simulate the game.") +
    `<div class="card">
       <div class="pickertip muted">Pick a sport to start a new game.</div>
       <div class="sportgrid">${tiles}</div>
     </div>`;
}

function matchupView() {
  const m = game.matchup;
  game.market = "ml";
  game.side = null;
  game.result = null;
  app.innerHTML =
    pageHead(`${esc(m.sport_label)} moneyline — pick a side and place your bet.`) +
    `<div class="card simbet">
       <div class="simbet-top">
         <button class="btn ghost" id="simBack">← Change sport</button>
         <span class="simsport">${SPORT_ICON[m.sport] || "🎮"} ${esc(m.sport_label)}</span>
       </div>
       <div class="matchup">
         <button class="teampick" data-side="away" id="pickAway">
           <div class="tp-tag">Away</div>
           <div class="tp-abbr">${esc(m.away.abbr)}</div>
           <div class="tp-name">${esc(m.away.name)}</div>
           <div class="tp-odds">${esc(m.away_american)}</div>
         </button>
         <div class="vs">@</div>
         <button class="teampick" data-side="home" id="pickHome">
           <div class="tp-tag">Home</div>
           <div class="tp-abbr">${esc(m.home.abbr)}</div>
           <div class="tp-name">${esc(m.home.name)}</div>
           <div class="tp-odds">${esc(m.home_american)}</div>
         </button>
       </div>
       <div class="betrow">
         <div class="betlbl">Stake</div>
         <div class="betline">
           <input class="betinput" type="number" min="1" step="1" value="100"
                  id="stake-sim" inputmode="numeric" />
           <div class="chipbtns">
             <button class="chipbtn" data-amt="10">10</button>
             <button class="chipbtn" data-amt="100">100</button>
             <button class="chipbtn" data-amt="1000">1k</button>
             <button class="chipbtn" data-amt="max">Max</button>
           </div>
         </div>
       </div>
       <div class="simactions">
         <button class="btn primary big" id="simPlace" disabled>Place bet</button>
       </div>
     </div>`;
  updateBetButton();
}

function readStake() {
  const el = $("stake-sim");
  const v = Math.floor(Number(el && el.value));
  return Number.isFinite(v) ? v : 0;
}

function updateBetButton() {
  const btn = $("simPlace");
  if (!btn) return;
  const stake = readStake();
  const ok = !!game.side && stake >= 1 && stake <= Math.floor(state.balance) && !game.busy;
  btn.disabled = !ok;
}

// Select a market + side. Non-NFL picks carry only data-side (market stays "ml");
// NFL picks also carry data-market. Exactly one .teampick is selected at a time.
function onPick(el) {
  game.market = el.dataset.market || "ml";
  game.side = el.dataset.side;
  document.querySelectorAll(".teampick").forEach((x) => x.classList.toggle("sel", x === el));
  updateBetButton();
}

// ── Start a new game for a sport ──
// NFL offers a mode choice first (watch vs live-bet); everything else goes
// straight to the classic moneyline flow (POST /new).
async function newGame(sport) {
  resetPace();
  manual.active = false;
  if (sport === "nfl") return nflModeView();
  if (game.busy) return;
  game.busy = true;
  const res = await postSim("/new", { sport });
  game.busy = false;
  if (res.error) return toast("❌ " + res.error);
  game.matchup = res;
  matchupView();
}

// NFL mode selection: Auto (watch a pre-placed bet) or Manual (live betting).
function nflModeView() {
  game.matchup = null;
  game.side = null;
  game.result = null;
  app.innerHTML =
    pageHead("NFL — watch a bet play out slowly, or live-bet as the game swings.") +
    `<div class="card">
       <div class="simbet-top">
         <button class="btn ghost" id="simBack">← Change sport</button>
         <span class="simsport">🏈 NFL</span>
       </div>
       <div class="modegrid">
         <button class="modetile" id="modeAuto">
           <span class="mode-icon">▶</span>
           <span class="mode-title">Auto · watch</span>
           <span class="mode-desc">Place one pre-game bet, then sweat it out play-by-play.</span>
         </button>
         <button class="modetile" id="modeManual">
           <span class="mode-icon">🎮</span>
           <span class="mode-title">Manual · live bet</span>
           <span class="mode-desc">Odds move every play — hedge or pile on while it's live.</span>
         </button>
       </div>
     </div>`;
}

// Auto mode: classic pre-game bet flow, just animated slowly.
async function startAutoNfl() {
  if (game.busy) return;
  game.busy = true;
  const res = await postSim("/new", { sport: "nfl" });
  game.busy = false;
  if (res.error) return toast("❌ " + res.error);
  game.matchup = res;
  nflMatchupView();
}

// ── Place the bet (POST /bet) then animate ──
async function placeBet() {
  if (game.busy) return;
  const side = game.side;
  const stake = readStake();
  if (!side) return toast("Pick a side first");
  if (stake < 1) return toast("Enter a stake of at least 1 coin");
  if (stake > Math.floor(state.balance)) return toast("You don't have enough coins for that stake");
  game.busy = true;
  updateBetButton();
  const market = game.market || "ml";
  const res = await postSim("/bet", { token: game.matchup.token, market, side, stake });
  if (res.error) { game.busy = false; updateBetButton(); return toast("❌ " + res.error); }
  game.result = res;
  if (game.matchup.sport === "nfl") {
    nflAnimateView(res);
    runNflAnimation(res);
  } else {
    animateView(res);
    runAnimation(res);
  }
}

// ── Scoreboard + play-by-play view ──
function animateView(res) {
  const m = game.matchup;
  const betAway = res.side === "away";
  const betHome = res.side === "home";
  app.innerHTML =
    pageHead(`${esc(m.sport_label)} — you bet ${esc(res.side === "home" ? res.home_abbr : res.away_abbr)} for ${coins(res.stake)}.`) +
    `<div class="card simboard">
       <div class="board-clock" id="boardClock">Kickoff</div>
       <div class="scoreline">
         <div class="teamscore away${betAway ? " picked" : ""}">
           <div class="ts-abbr">${esc(res.away_abbr)}</div>
           <div class="ts-num" id="simAway">0</div>
           ${betAway ? `<div class="ts-pick">YOUR PICK</div>` : `<div class="ts-pick empty"></div>`}
         </div>
         <div class="ts-dash">–</div>
         <div class="teamscore home${betHome ? " picked" : ""}">
           <div class="ts-abbr">${esc(res.home_abbr)}</div>
           <div class="ts-num" id="simHome">0</div>
           ${betHome ? `<div class="ts-pick">YOUR PICK</div>` : `<div class="ts-pick empty"></div>`}
         </div>
       </div>
       <button class="btn ghost" id="simSkip">Skip ▶▶</button>
     </div>
     <div class="simbanner" id="simBanner"></div>
     <div class="simnote" id="simNote"></div>
     <div class="simactions" id="simEnd"></div>
     <div class="card simfeed"><div class="feedlist" id="simFeed"></div></div>`;
}

function flash(team) {
  const el = team === "home" ? $("simHome") : $("simAway");
  if (!el || REDUCE) return;
  el.classList.remove("flash");
  void el.offsetWidth; // reflow so the animation retriggers
  el.classList.add("flash");
}

function feedLine(res, ev) {
  const abbr = ev.team === "home" ? res.home_abbr : res.away_abbr;
  const row = document.createElement("div");
  row.className = "feeditem " + ev.team;
  row.innerHTML = `<span class="fi-clock">${esc(ev.clock)}</span>
    <span class="fi-abbr">${esc(abbr)}</span>
    <span class="fi-desc">${esc(ev.desc)}</span>`;
  const list = $("simFeed");
  if (list) list.insertBefore(row, list.firstChild);
}

function applyEvent(res, ev) {
  const bc = $("boardClock");
  if (bc) bc.textContent = ev.clock;
  const h = $("simHome"), a = $("simAway");
  if (h) h.textContent = ev.home;
  if (a) a.textContent = ev.away;
  flash(ev.team);
  feedLine(res, ev);
}

async function runAnimation(res) {
  anim.skip = false;
  anim.done = false;
  anim.finish = finishAnimation;
  const tl = res.timeline || [];

  if (REDUCE) {
    // No motion: dump the whole feed, then settle immediately.
    for (const ev of tl) applyEvent(res, ev);
    return finishAnimation(res);
  }
  if (!tl.length) return finishAnimation(res); // 0-0 game: straight to settle.

  // Cap the whole animation ~7s: shrink per-event delay for long timelines.
  const per = Math.min(700, Math.max(120, Math.floor(7000 / tl.length)));
  for (let i = 0; i < tl.length; i++) {
    if (anim.skip) break;
    applyEvent(res, tl[i]);
    await delay(per);
  }
  finishAnimation(res);
}

function skipAnimation() {
  if (!game.result || anim.done) return;
  anim.skip = true;
  pace.skip = true;
  (anim.finish || finishAnimation)(game.result);
}

function finishAnimation(res) {
  if (anim.done) return;
  anim.done = true;
  game.busy = false;

  // Snap to the final score + reveal.
  const h = $("simHome"), a = $("simAway");
  if (h) h.textContent = res.home_score;
  if (a) a.textContent = res.away_score;
  const bc = $("boardClock");
  if (bc) bc.textContent = "Final";
  const sk = $("simSkip");
  if (sk) sk.remove();

  document.querySelectorAll(".teamscore").forEach((el) => el.classList.remove("won", "lost"));
  const winEl = document.querySelector(".teamscore." + res.winner);
  const loseEl = document.querySelector(".teamscore." + (res.winner === "home" ? "away" : "home"));
  if (winEl) winEl.classList.add("won");
  if (loseEl) loseEl.classList.add("lost");

  applyBalance(res.balance);

  const b = $("simBanner");
  if (res.won) {
    b.className = "simbanner show win";
    b.innerHTML = `<span class="sb-label">YOU WON</span>
      <span class="sb-pay">Payout +${coins(res.payout)}</span>`;
  } else {
    b.className = "simbanner show lose";
    b.innerHTML = `<span class="sb-label">YOU LOST</span>
      <span class="sb-pay">−${coins(res.stake)}</span>`;
  }

  const winAbbr = res.winner === "home" ? res.home_abbr : res.away_abbr;
  const yourAbbr = res.side === "home" ? res.home_abbr : res.away_abbr;
  const note = $("simNote");
  if (note) {
    note.innerHTML = `<b>${esc(winAbbr)}</b> win ${num(res.away_score)}–${num(res.home_score)}
      (away–home). You bet <b>${esc(yourAbbr)}</b>. Balance: ${coins(res.balance)}.`;
  }

  const end = $("simEnd");
  if (end) {
    end.innerHTML = `<button class="btn primary" id="simAgain">New game</button>
      <button class="btn ghost" id="simPick">Change sport</button>`;
  }
}

// ═══════════════ NFL simcast ═══════════════
// NFL differs from the other sports: three bet markets (moneyline / spread /
// total) and a full down-by-down `plays` array animated on a football field.
// Everything NFL lives in this block; the other four sports are untouched.

const MKT_LABEL = { ml: "Moneyline", spread: "Spread", total: "Total" };
// Ball spot 0..100 maps across the central play area; end zones take 8% each side.
const EZ_PCT = 8;
const ballLeft = (fieldpos) => EZ_PCT + (Math.max(0, Math.min(100, fieldpos)) / 100) * (100 - 2 * EZ_PCT);

// Bet-selection UI: three markets, each with two selectable picks.
function nflMatchupView() {
  const m = game.matchup;
  game.market = null;
  game.side = null;
  game.result = null;
  const sprDec = decToAmerican(m.spread_dec);
  const totDec = decToAmerican(m.total_dec);
  // spread is the HOME margin line (positive = home favored); home shows -spread, away +spread.
  const homeLine = fmtSigned(-m.spread);
  const awayLine = fmtSigned(m.spread);
  const pick = (market, side, big, sub) => `<button class="teampick nflpick" data-market="${market}" data-side="${side}">
      <div class="np-main">${esc(big)}</div>
      <div class="np-sub">${esc(sub)}</div>
    </button>`;
  app.innerHTML =
    pageHead(`${esc(m.sport_label)} — pick a market and side, then place your bet.`) +
    `<div class="card simbet">
       <div class="simbet-top">
         <button class="btn ghost" id="simBack">← Change sport</button>
         <span class="simsport">${SPORT_ICON[m.sport] || "🏈"} ${esc(m.sport_label)}</span>
       </div>
       <div class="matchup-mini">
         <span class="mm-away">${esc(m.away.abbr)}</span>
         <span class="mm-at">@</span>
         <span class="mm-home">${esc(m.home.abbr)}</span>
       </div>
       <div class="nfl-markets">
         <div class="mktgroup">
           <div class="mktlabel">Moneyline</div>
           <div class="mktpicks">
             ${pick("ml", "away", m.away.abbr, m.away_american)}
             ${pick("ml", "home", m.home.abbr, m.home_american)}
           </div>
         </div>
         <div class="mktgroup">
           <div class="mktlabel">Spread</div>
           <div class="mktpicks">
             ${pick("spread", "away", `${m.away.abbr} ${awayLine}`, sprDec)}
             ${pick("spread", "home", `${m.home.abbr} ${homeLine}`, sprDec)}
           </div>
         </div>
         <div class="mktgroup">
           <div class="mktlabel">Total ${esc(m.total)}</div>
           <div class="mktpicks">
             ${pick("total", "over", `Over ${m.total}`, totDec)}
             ${pick("total", "under", `Under ${m.total}`, totDec)}
           </div>
         </div>
       </div>
       <div class="betrow">
         <div class="betlbl">Stake</div>
         <div class="betline">
           <input class="betinput" type="number" min="1" step="1" value="100"
                  id="stake-sim" inputmode="numeric" />
           <div class="chipbtns">
             <button class="chipbtn" data-amt="10">10</button>
             <button class="chipbtn" data-amt="100">100</button>
             <button class="chipbtn" data-amt="1000">1k</button>
             <button class="chipbtn" data-amt="max">Max</button>
           </div>
         </div>
       </div>
       <div class="simactions">
         <button class="btn primary big" id="simPlace" disabled>Place bet</button>
       </div>
     </div>`;
  updateBetButton();
}

// Label describing the bet the user placed (for the subtitle + final note).
function nflBetLabel(res) {
  const mk = MKT_LABEL[res.market] || "Moneyline";
  if (res.market === "total") return `${mk} ${res.side === "over" ? "Over" : "Under"} ${res.line.total}`;
  const abbr = res.side === "home" ? res.home.abbr : res.away.abbr;
  if (res.market === "spread") {
    const line = res.side === "home" ? fmtSigned(-res.line.spread) : fmtSigned(res.line.spread);
    return `${mk} ${abbr} ${line}`;
  }
  return `${mk} ${abbr}`;
}

// Scoreboard + football field + play ticker.
function nflAnimateView(res) {
  nflPrev.home = 0;
  nflPrev.away = 0;
  app.innerHTML =
    pageHead(`NFL — you bet ${esc(nflBetLabel(res))} for ${coins(res.stake)}.`) +
    `<div class="card nflsim">
       <div class="nfl-score">
         <div class="nsc home"><span class="nsc-abbr">${esc(res.home.abbr)}</span><span class="nsc-num" id="nflHome">0</span></div>
         <div class="nsc-mid">
           <div class="nsc-q" id="nflQ">Kickoff</div>
           <div class="nsc-dd" id="nflDD">&nbsp;</div>
         </div>
         <div class="nsc away"><span class="nsc-abbr">${esc(res.away.abbr)}</span><span class="nsc-num" id="nflAway">0</span></div>
       </div>
       <div class="nfl-field">
         <div class="ez ez-home"><span>${esc(res.home.abbr)}</span></div>
         <div class="field-play"></div>
         <div class="ez ez-away"><span>${esc(res.away.abbr)}</span></div>
         <div class="ball" id="nflBall" style="left:${ballLeft(50)}%">🏈</div>
       </div>
       ${controlsBar()}
     </div>
     <div class="simbanner" id="simBanner"></div>
     <div class="simnote" id="simNote"></div>
     <div class="simactions" id="simEnd"></div>
     <div class="card simfeed"><div class="feedlist nflfeed" id="simFeed"></div></div>`;
}

const nflPrev = { home: 0, away: 0 };
const SCORING = new Set(["touchdown", "field_goal", "safety", "extra_point", "two_point_good"]);
const PLAYTYPE_LABEL = { kickoff: "Kickoff", xp: "Extra point", "2pt": "2-pt try", fg: "Field goal", punt: "Punt" };

function qLabel(p) {
  const q = p.q >= 5 ? "OT" : "Q" + p.q;
  return `${q} · ${p.clock}`;
}
function ddLabel(p) {
  const poss = `${p.off} ball`;
  if (p.down) return `${ordinal(p.down)} & ${p.togo} · ${poss}`;
  return `${PLAYTYPE_LABEL[p.type] || p.type} · ${poss}`;
}

function nflFlash(id) {
  const el = $(id);
  if (!el || REDUCE) return;
  el.classList.remove("flash");
  void el.offsetWidth;
  el.classList.add("flash");
}

function nflFeedLine(res, p) {
  const isHome = p.off === res.home.abbr;
  const row = document.createElement("div");
  row.className = "feeditem " + (isHome ? "home" : "away") + (SCORING.has(p.result) ? " score" : "");
  row.innerHTML = `<span class="fi-clock">${esc(qLabel(p))}</span>
    <span class="fi-abbr">${esc(p.off)}</span>
    <span class="fi-desc">${esc(p.desc)}</span>`;
  const list = $("simFeed");
  if (list) list.insertBefore(row, list.firstChild);
}

function applyNflPlay(res, p) {
  const ball = $("nflBall");
  if (ball) ball.style.left = ballLeft(p.fieldpos) + "%";
  const q = $("nflQ");
  if (q) q.textContent = qLabel(p);
  const dd = $("nflDD");
  if (dd) dd.textContent = ddLabel(p);
  const h = $("nflHome"), a = $("nflAway");
  if (h) h.textContent = p.home;
  if (a) a.textContent = p.away;
  if (p.home > nflPrev.home) nflFlash("nflHome");
  if (p.away > nflPrev.away) nflFlash("nflAway");
  nflPrev.home = p.home;
  nflPrev.away = p.away;
  nflFeedLine(res, p);
}

async function runNflAnimation(res) {
  anim.skip = false;
  anim.done = false;
  anim.finish = nflFinish;
  resetPace();
  const plays = res.plays || [];

  if (REDUCE) {
    for (const p of plays) applyNflPlay(res, p);
    return nflFinish(res);
  }
  if (!plays.length) return nflFinish(res);

  // Slow sweat: one play every ~2.2s at 1× (speed/pause/skip via paceWait).
  for (let i = 0; i < plays.length; i++) {
    applyNflPlay(res, plays[i]);
    await paceWait();
    if (pace.skip || anim.skip) break;
  }
  nflFinish(res);
}

function nflFinish(res) {
  if (anim.done) return;
  anim.done = true;
  game.busy = false;

  const h = $("nflHome"), a = $("nflAway");
  if (h) h.textContent = res.home_score;
  if (a) a.textContent = res.away_score;
  const ball = $("nflBall");
  const last = (res.plays || [])[(res.plays || []).length - 1];
  if (ball && last) ball.style.left = ballLeft(last.fieldpos) + "%";
  const q = $("nflQ");
  if (q) q.textContent = "Final";
  const dd = $("nflDD");
  if (dd) dd.innerHTML = "&nbsp;";
  const sk = $("simSkip");
  if (sk) sk.remove();

  document.querySelectorAll(".nsc").forEach((el) => el.classList.remove("won", "lost"));
  if (res.winner === "home" || res.winner === "away") {
    const win = document.querySelector(".nsc." + res.winner);
    const lose = document.querySelector(".nsc." + (res.winner === "home" ? "away" : "home"));
    if (win) win.classList.add("won");
    if (lose) lose.classList.add("lost");
  }

  applyBalance(res.balance);

  const b = $("simBanner");
  if (res.push) {
    b.className = "simbanner show push";
    b.innerHTML = `<span class="sb-label">PUSH</span>
      <span class="sb-pay">Refund ${coins(res.payout || res.stake)}</span>`;
  } else if (res.won) {
    b.className = "simbanner show win";
    b.innerHTML = `<span class="sb-label">YOU WON</span>
      <span class="sb-pay">Payout +${coins(res.payout)}</span>`;
  } else {
    b.className = "simbanner show lose";
    b.innerHTML = `<span class="sb-label">YOU LOST</span>
      <span class="sb-pay">−${coins(res.stake)}</span>`;
  }

  const winAbbr = res.winner === "tie" ? "Tie" : res.winner === "home" ? res.home.abbr : res.away.abbr;
  const note = $("simNote");
  if (note) {
    const score = `${esc(res.away.abbr)} ${num(res.away_score)} – ${num(res.home_score)} ${esc(res.home.abbr)}`;
    const result = res.winner === "tie" ? `<b>Tie</b>` : `<b>${esc(winAbbr)}</b> win`;
    note.innerHTML = `${result} ${score}. You bet <b>${esc(nflBetLabel(res))}</b>. Balance: ${coins(res.balance)}.`;
  }

  const end = $("simEnd");
  if (end) {
    end.innerHTML = `<button class="btn primary" id="simAgain">New game</button>
      <button class="btn ghost" id="simPick">Change sport</button>`;
  }
}

// ═══════════════ NFL manual mode (live in-game betting) ═══════════════
// Pull-based: the server reveals one play at a time (it holds the future) and
// re-prices three markets each play. The user can place live bets whenever a
// side isn't suspended. Reuses the simcast field/ball/ticker from auto mode.

const manual = {
  active: false, gid: null, home: null, away: null, odds: null, done: false,
  bets: [], selMarket: null, selSide: null, loopToken: 0,
};

function cellFor(odds, market, side) {
  if (!odds) return null;
  const m = odds[market];
  return m ? m[side] : null;
}

// Label for a market+side using a line source ({spread:{line},total:{line}} or full odds).
function liveBetLabel(market, side, odds) {
  const H = manual.home.abbr, A = manual.away.abbr;
  if (market === "ml") return (side === "home" ? H : A) + " ML";
  if (market === "spread") {
    const line = odds && odds.spread ? odds.spread.line : 0;
    return side === "home" ? `${H} ${fmtSigned(-line)}` : `${A} ${fmtSigned(line)}`;
  }
  const line = odds && odds.total ? odds.total.line : "";
  return side === "over" ? `Over ${line}` : `Under ${line}`;
}

async function startManualNfl() {
  if (game.busy) return;
  game.busy = true;
  resetPace();
  const res = await postSim("/start", { sport: "nfl" });
  game.busy = false;
  if (res.error) return toast("❌ " + res.error);
  Object.assign(manual, {
    active: true, gid: res.gid, home: res.home, away: res.away, odds: res.odds,
    done: false, bets: [], selMarket: null, selSide: null,
  });
  game.matchup = { sport: "nfl" }; // lets "New game" / "Change sport" route correctly
  nflPrev.home = 0;
  nflPrev.away = 0;
  manualView(res);
  manualLoop();
}

function manualView(res) {
  app.innerHTML =
    pageHead("NFL live — odds move every play. Pause the action to place a live bet.") +
    `<div class="card nflsim">
       <div class="nfl-score">
         <div class="nsc home"><span class="nsc-abbr">${esc(res.home.abbr)}</span><span class="nsc-num" id="nflHome">0</span></div>
         <div class="nsc-mid">
           <div class="nsc-q" id="nflQ">Kickoff</div>
           <div class="nsc-dd" id="nflDD">&nbsp;</div>
         </div>
         <div class="nsc away"><span class="nsc-abbr">${esc(res.away.abbr)}</span><span class="nsc-num" id="nflAway">0</span></div>
       </div>
       <div class="nfl-field">
         <div class="ez ez-home"><span>${esc(res.home.abbr)}</span></div>
         <div class="field-play"></div>
         <div class="ez ez-away"><span>${esc(res.away.abbr)}</span></div>
         <div class="ball" id="nflBall" style="left:${ballLeft(50)}%">🏈</div>
       </div>
       ${controlsBar()}
     </div>
     <div class="card liveodds" id="liveOdds"></div>
     <div class="card livebet" id="liveBetCard">
       <div class="lb-sel" id="lbSel">Pick a market above to live-bet.</div>
       <div class="betline">
         <input class="betinput" type="number" min="1" step="1" value="100"
                id="stake-live" inputmode="numeric" />
         <div class="chipbtns">
           <button class="chipbtn" data-amt="100">100</button>
           <button class="chipbtn" data-amt="1000">1k</button>
           <button class="chipbtn" data-amt="5000">5k</button>
           <button class="chipbtn" data-amt="max">Max</button>
         </div>
         <button class="btn primary" id="liveBetBtn" disabled>Live bet</button>
       </div>
       <div class="lb-cap muted">Live bets are capped at 🪙 25,000 each.</div>
     </div>
     <div class="simbanner" id="simBanner"></div>
     <div class="simnote" id="simNote"></div>
     <div class="simactions" id="simEnd"></div>
     <div class="card liveodds" id="liveBets"></div>
     <div class="card simfeed"><div class="feedlist nflfeed" id="simFeed"></div></div>`;
  renderLiveOdds(res.odds);
  renderMyBets();
  updateLiveBetBtn();
}

// One bettable (or suspended) odds cell. Suspended sides have no data-market,
// are a <div>, and show "SUSPENDED" — so they can't be selected.
function oddsBtn(market, side, label, cell) {
  if (!cell)
    return `<div class="liveopt susp"><span class="lo-lbl">${esc(label)}</span>
      <span class="lo-susp">SUSPENDED</span></div>`;
  const sel = manual.selMarket === market && manual.selSide === side ? " sel" : "";
  return `<button class="liveopt${sel}" data-market="${market}" data-side="${side}">
    <span class="lo-lbl">${esc(label)}</span>
    <span class="lo-odds">${esc(fmtAmer(cell.american))}</span></button>`;
}

function renderLiveOdds(odds) {
  const el = $("liveOdds");
  if (!el) return;
  manual.odds = odds;
  if (!odds) { el.innerHTML = `<div class="lo-title">Game over — markets closed</div>`; return; }
  // Drop a selection that just got suspended.
  if (manual.selMarket && !cellFor(odds, manual.selMarket, manual.selSide)) {
    manual.selMarket = null;
    manual.selSide = null;
  }
  const H = manual.home.abbr, A = manual.away.abbr;
  const spr = odds.spread || {}, tot = odds.total || {};
  const ml = odds.ml || {};
  el.innerHTML = `
    <div class="lo-title">Live odds</div>
    <div class="lo-market">
      <div class="lo-mlbl">Moneyline</div>
      <div class="lo-opts">
        ${oddsBtn("ml", "away", A, ml.away)}
        ${oddsBtn("ml", "home", H, ml.home)}
      </div>
    </div>
    <div class="lo-market">
      <div class="lo-mlbl">Spread</div>
      <div class="lo-opts">
        ${oddsBtn("spread", "away", `${A} ${fmtSigned(spr.line)}`, spr.away)}
        ${oddsBtn("spread", "home", `${H} ${fmtSigned(-spr.line)}`, spr.home)}
      </div>
    </div>
    <div class="lo-market">
      <div class="lo-mlbl">Total${tot.line != null ? " " + esc(String(tot.line)) : ""}</div>
      <div class="lo-opts">
        ${oddsBtn("total", "over", `Over ${tot.line}`, tot.over)}
        ${oddsBtn("total", "under", `Under ${tot.line}`, tot.under)}
      </div>
    </div>`;
}

function selectLiveOpt(el) {
  if (!el.dataset.market) return;
  manual.selMarket = el.dataset.market;
  manual.selSide = el.dataset.side;
  document.querySelectorAll(".liveopt").forEach((x) => x.classList.toggle("sel", x === el));
  updateLiveBetBtn();
}

function updateLiveBetBtn() {
  const btn = $("liveBetBtn");
  if (!btn) return;
  const el = $("stake-live");
  const stake = Math.floor(Number(el && el.value));
  const ok =
    manual.active && !manual.done && !!manual.selMarket && stake >= 1 &&
    stake <= Math.floor(state.balance) && stake <= 25000 && !game.busy;
  btn.disabled = !ok;
  const sel = $("lbSel");
  if (sel) {
    if (!manual.selMarket) {
      sel.textContent = "Pick a market above to live-bet.";
    } else {
      const cell = cellFor(manual.odds, manual.selMarket, manual.selSide);
      sel.innerHTML = `Betting <b>${esc(liveBetLabel(manual.selMarket, manual.selSide, manual.odds))}</b>` +
        (cell ? ` @ <b>${esc(fmtAmer(cell.american))}</b>` : "");
    }
  }
}

async function placeLiveBet() {
  if (!manual.active || manual.done || !manual.selMarket || game.busy) return;
  const el = $("stake-live");
  const stake = Math.floor(Number(el && el.value));
  if (!(stake >= 1)) return toast("Enter a stake of at least 1 coin");
  if (stake > 25000) return toast("Live bets are capped at 25,000");
  if (stake > Math.floor(state.balance)) return toast("You don't have enough coins");
  const market = manual.selMarket, side = manual.selSide;
  game.busy = true;
  updateLiveBetBtn();
  const r = await postSim("/livebet", { gid: manual.gid, market, side, stake });
  game.busy = false;
  if (r.error) { updateLiveBetBtn(); return toast("❌ " + r.error); }
  manual.bets.push({
    market: r.market, side: r.side, dec: r.dec, american: r.american, stake: r.stake,
    label: liveBetLabel(r.market, r.side, manual.odds),
  });
  applyBalance(r.balance);
  renderMyBets();
  updateLiveBetBtn();
  toast(`✅ Live bet placed: ${liveBetLabel(market, side, manual.odds)}`);
}

function renderMyBets() {
  const el = $("liveBets");
  if (!el || manual.done) return;
  if (!manual.bets.length) {
    el.innerHTML = `<div class="lb-title">My live bets</div>
      <div class="lb-empty muted">No live bets yet. Pick a market and stake to bet while it plays.</div>`;
    return;
  }
  const rows = manual.bets
    .map((b) => `<div class="mybet">
      <span class="mb-lbl">${esc(b.label)}</span>
      <span class="mb-odds">${esc(fmtAmer(b.american))}</span>
      <span class="mb-stake">${coins(b.stake)}</span>
      <span class="mb-status live">LIVE</span>
    </div>`)
    .join("");
  el.innerHTML = `<div class="lb-title">My live bets (${manual.bets.length})</div>${rows}`;
}

function settledBetLabel(b, line, i) {
  if (manual.bets[i]) return manual.bets[i].label;
  return liveBetLabel(b.market, b.side, line ? { spread: { line: line.spread }, total: { line: line.total } } : null);
}

function renderSettledBets(bets, line) {
  const el = $("liveBets");
  if (!el) return;
  if (!bets || !bets.length) {
    el.innerHTML = `<div class="lb-title">My live bets</div>
      <div class="lb-empty muted">You didn't place any live bets this game.</div>`;
    return;
  }
  let staked = 0, ret = 0;
  const rows = bets
    .map((b, i) => {
      staked += b.stake;
      ret += b.payout || 0;
      const st = b.push ? "push" : b.won ? "won" : "lost";
      const pay = b.push
        ? `Refund ${coins(b.payout || b.stake)}`
        : b.won ? `+${coins(b.payout)}` : `−${coins(b.stake)}`;
      return `<div class="mybet ${st}">
        <span class="mb-lbl">${esc(settledBetLabel(b, line, i))}</span>
        <span class="mb-odds">${esc(fmtAmer(b.american != null ? b.american : decToAmerican(b.dec)))}</span>
        <span class="mb-stake">${coins(b.stake)}</span>
        <span class="mb-status ${st}">${st.toUpperCase()}</span>
        <span class="mb-pay">${pay}</span>
      </div>`;
    })
    .join("");
  const net = ret - staked;
  el.innerHTML = `<div class="lb-title">My live bets — settled</div>${rows}
    <div class="lb-net">Net
      <b class="${net >= 0 ? "pos" : "neg"}">${net >= 0 ? "+" : "−"}${coins(Math.abs(net))}</b>
      · Balance <b>${coins(state.balance)}</b></div>`;
}

async function manualLoop() {
  const token = ++manual.loopToken;
  while (!manual.done && manual.loopToken === token) {
    await paceWait();
    if (manual.loopToken !== token) return;
    if (game.busy) { await delay(80); continue; } // a /livebet is in flight — yield
    const r = await postSim("/advance", { gid: manual.gid });
    if (manual.loopToken !== token) return;
    if (r.error) { toast("❌ " + r.error); return; }
    if (r.done) return manualSettle(r);
    if (r.play) applyNflPlay(manual, r.play);
    renderLiveOdds(r.odds);
    updateLiveBetBtn();
  }
}

function manualSkip() {
  if (!manual.active || manual.done) return;
  pace.skip = true; // paceWait returns instantly → loop drains to the end fast
}

function manualSettle(final) {
  manual.done = true;
  manual.active = false;
  manual.loopToken++; // stop the loop
  game.busy = false;

  if (final.play) applyNflPlay(manual, final.play);
  const h = $("nflHome"), a = $("nflAway");
  if (h) h.textContent = final.home_score;
  if (a) a.textContent = final.away_score;
  const q = $("nflQ");
  if (q) q.textContent = "Final";
  const dd = $("nflDD");
  if (dd) dd.innerHTML = "&nbsp;";

  const ctrls = $("simCtrls");
  if (ctrls) ctrls.remove();
  const betCard = $("liveBetCard");
  if (betCard) betCard.remove();

  document.querySelectorAll(".nsc").forEach((el) => el.classList.remove("won", "lost"));
  if (final.winner === "home" || final.winner === "away") {
    const win = document.querySelector(".nsc." + final.winner);
    const lose = document.querySelector(".nsc." + (final.winner === "home" ? "away" : "home"));
    if (win) win.classList.add("won");
    if (lose) lose.classList.add("lost");
  }

  applyBalance(final.balance);
  renderLiveOdds(null);
  renderSettledBets(final.bets, final.line);

  const bets = final.bets || [];
  const staked = bets.reduce((s, b) => s + b.stake, 0);
  const ret = bets.reduce((s, b) => s + (b.payout || 0), 0);
  const net = ret - staked;
  const b = $("simBanner");
  if (b) {
    if (!bets.length) {
      b.className = "simbanner show push";
      b.innerHTML = `<span class="sb-label">GAME OVER</span>
        <span class="sb-pay">No live bets placed</span>`;
    } else if (net > 0) {
      b.className = "simbanner show win";
      b.innerHTML = `<span class="sb-label">YOU WON</span>
        <span class="sb-pay">Net +${coins(net)}</span>`;
    } else if (net < 0) {
      b.className = "simbanner show lose";
      b.innerHTML = `<span class="sb-label">YOU LOST</span>
        <span class="sb-pay">Net −${coins(Math.abs(net))}</span>`;
    } else {
      b.className = "simbanner show push";
      b.innerHTML = `<span class="sb-label">BROKE EVEN</span>
        <span class="sb-pay">Net ${coins(0)}</span>`;
    }
  }

  const winAbbr = final.winner === "tie" ? "Tie" : final.winner === "home" ? final.home.abbr : final.away.abbr;
  const note = $("simNote");
  if (note) {
    const score = `${esc(final.away.abbr)} ${num(final.away_score)} – ${num(final.home_score)} ${esc(final.home.abbr)}`;
    const result = final.winner === "tie" ? `<b>Tie</b>` : `<b>${esc(winAbbr)}</b> win`;
    note.innerHTML = `${result} ${score}. Balance: ${coins(state.balance)}.`;
  }

  const end = $("simEnd");
  if (end) {
    end.innerHTML = `<button class="btn primary" id="simAgain">New game</button>
      <button class="btn ghost" id="simPick">Change sport</button>`;
  }
}

// ═══════════════ Events ═══════════════

app.addEventListener("click", (e) => {
  const tile = e.target.closest(".sporttile");
  if (tile) return newGame(tile.dataset.sport);

  const pick = e.target.closest(".teampick");
  if (pick) return onPick(pick);

  const chip = e.target.closest(".chipbtn");
  if (chip) {
    // Target whichever stake input is on the page (pre-game or live).
    const inp = (chip.closest(".betline") || document).querySelector(".betinput");
    if (inp) inp.value = chip.dataset.amt === "max"
      ? Math.max(0, Math.floor(state.balance)) : chip.dataset.amt;
    updateBetButton();
    updateLiveBetBtn();
    return;
  }

  // Mode selection (NFL).
  if (e.target.closest("#modeAuto")) return startAutoNfl();
  if (e.target.closest("#modeManual")) return startManualNfl();

  // Playback speed.
  const spd = e.target.closest(".spdbtn");
  if (spd) {
    pace.speed = Number(spd.dataset.speed);
    document.querySelectorAll(".spdbtn").forEach((b) => b.classList.toggle("sel", b === spd));
    return;
  }
  // Pause / resume.
  const pauseBtn = e.target.closest("#simPause");
  if (pauseBtn) {
    pace.paused = !pace.paused;
    pauseBtn.textContent = pace.paused ? "▶ Resume" : "⏸ Pause";
    return;
  }

  // Live-odds selection + live bet (manual mode).
  const opt = e.target.closest(".liveopt");
  if (opt) return selectLiveOpt(opt);
  if (e.target.closest("#liveBetBtn")) return placeLiveBet();

  if (e.target.closest("#simBack")) return pickerView();
  if (e.target.closest("#simPlace")) return placeBet();
  if (e.target.closest("#simSkip")) return manual.active ? manualSkip() : skipAnimation();
  if (e.target.closest("#simAgain")) return newGame(game.matchup && game.matchup.sport);
  if (e.target.closest("#simPick")) return pickerView();
});

app.addEventListener("input", (e) => {
  if (e.target.id === "stake-sim") updateBetButton();
  if (e.target.id === "stake-live") updateLiveBetBtn();
});
app.addEventListener("keydown", (e) => {
  if (e.target.id === "stake-sim" && e.key === "Enter") { e.preventDefault(); placeBet(); }
  if (e.target.id === "stake-live" && e.key === "Enter") { e.preventDefault(); placeLiveBet(); }
});

async function main() {
  const me = await getJSON("/api/v1/hq/me");
  const loggedIn = me && me.authenticated;
  state.me = loggedIn ? me : null;
  state.balance = loggedIn ? (me.balance || 0) : 0;
  renderNav(loggedIn ? me.user : null);
  if (!loggedIn) return signedOutView();
  pickerView();
}

// ─────────────────────────────────────────────────────────────
// Mock mode (?mock=1): fabricate a matchup + timeline so the page
// can be screenshotted offline. Real endpoints win by default.
// ─────────────────────────────────────────────────────────────
const MOCK = new URLSearchParams(location.search).get("mock") === "1";

function mockJSON(url) {
  if (url.startsWith("/api/v1/hq/me"))
    return { authenticated: true, user: { id: "1", username: "davidj", avatar: null }, balance: 12500 };
  return {};
}

const MOCK_TEAMS = {
  nba: { label: "NBA", home: ["Los Angeles Lakers", "LAL"], away: ["Boston Celtics", "BOS"] },
  nfl: { label: "NFL", home: ["Kansas City Chiefs", "KC"], away: ["Philadelphia Eagles", "PHI"] },
  mlb: { label: "MLB", home: ["Los Angeles Dodgers", "LAD"], away: ["New York Yankees", "NYY"] },
  tennis: { label: "Tennis", home: ["Carlos Alcaraz", "ALC"], away: ["Jannik Sinner", "SIN"] },
  soccer: { label: "Soccer", home: ["Arsenal", "ARS"], away: ["Chelsea", "CHE"] },
};
const MOCK_CFG = {
  nba: { clocks: ["Q1", "Q2", "Q3", "Q4"], hi: [112, 104], inc: [2, 3], d: { 2: "layup", 3: "three-pointer" } },
  nfl: { clocks: ["Q1", "Q2", "Q3", "Q4"], hi: [27, 20], inc: [7, 3], d: { 7: "touchdown + XP", 3: "field goal" } },
  mlb: { clocks: ["Top 2", "Bot 4", "Top 6", "Bot 8"], hi: [5, 3], inc: [1, 2], d: { 1: "RBI single", 2: "2-run double" } },
  soccer: { clocks: ["23'", "48'", "71'", "88'"], hi: [2, 1], inc: [1], d: { 1: "GOAL!" } },
  tennis: { clocks: ["Set 1", "Set 2", "Set 3"], hi: [2, 1], inc: [1], d: { 1: "wins the set 6-4" } },
};
let mockGame = null;

function mockDecToAmerican(dec) {
  if (dec <= 1) return "—";
  if (dec >= 2) return "+" + Math.round((dec - 1) * 100);
  return String(-Math.round(100 / (dec - 1)));
}

function mockSim(path, body) {
  if (path === "/start") return mockManStart(body);
  if (path === "/advance") return mockManAdvance(body);
  if (path === "/livebet") return mockManLivebet(body);
  if (path === "/new") {
    const sport = MOCK_TEAMS[body.sport] ? body.sport : "nba";
    const t = MOCK_TEAMS[sport];
    const homeProb = 0.5 + (Math.random() * 0.3 - 0.1); // ~0.4..0.7
    const homeDec = Math.round((1 / homeProb) * 0.95 * 1000) / 1000;
    const awayDec = Math.round((1 / (1 - homeProb)) * 0.95 * 1000) / 1000;
    mockGame = {
      token: "mock", sport, sport_label: t.label,
      home: { abbr: t.home[1], name: t.home[0] }, away: { abbr: t.away[1], name: t.away[0] },
      home_prob: homeProb, home_dec: homeDec, away_dec: awayDec,
      home_american: mockDecToAmerican(homeDec), away_american: mockDecToAmerican(awayDec),
    };
    if (sport === "nfl") {
      // spread = home margin line (positive = home favored); total = O/U line.
      mockGame.spread = (Math.round((homeProb - 0.5) * 40) / 2) || 1; // ~ -4..+8 in 0.5 steps
      mockGame.total = 44.5;
      mockGame.spread_dec = 1.909;
      mockGame.total_dec = 1.909;
    }
    return mockGame;
  }
  // /bet
  const g = mockGame || mockSim("/new", { sport: "nba" });
  if (g.sport === "nfl") return mockNfl(g, body);
  const stake = Math.floor(Number(body.stake) || 0);
  const winner = Math.random() < g.home_prob ? "home" : "away";
  const built = mockBuild(g.sport, winner);
  const won = body.side === winner;
  const dec = body.side === "home" ? g.home_dec : g.away_dec;
  const payout = won ? Math.round(stake * dec) : 0;
  state.balance = Math.max(0, state.balance - stake + payout);
  return {
    sport: g.sport, home_abbr: g.home.abbr, away_abbr: g.away.abbr,
    home_score: built.home_score, away_score: built.away_score, timeline: built.timeline,
    winner, side: body.side, won, payout, stake, balance: state.balance,
  };
}

function mockBuild(sport, winner) {
  const cfg = MOCK_CFG[sport];
  const hf = winner === "home" ? cfg.hi[0] : cfg.hi[1];
  const af = winner === "home" ? cfg.hi[1] : cfg.hi[0];
  let h = 0, a = 0, i = 0;
  const events = [];
  const push = (team) => {
    const target = team === "home" ? hf : af;
    const cur = team === "home" ? h : a;
    if (cur >= target) return;
    let step = cfg.inc[Math.floor(Math.random() * cfg.inc.length)];
    if (cur + step > target) step = target - cur;
    if (team === "home") h += step; else a += step;
    const clock = cfg.clocks[Math.min(cfg.clocks.length - 1, Math.floor((i * cfg.clocks.length) / 12))];
    const desc = cfg.d[step] || cfg.d[cfg.inc[0]];
    events.push({ clock, team, home: h, away: a, desc });
    i++;
  };
  while ((h < hf || a < af) && i < 40) {
    if (h < hf) push("home");
    if (a < af) push("away");
  }
  return { home_score: hf, away_score: af, timeline: events };
}

// ── NFL mock: fabricate a short plausible drive-by-drive game. ──
function mockNfl(g, body) {
  const stake = Math.floor(Number(body.stake) || 0);
  const market = body.market || "ml";
  const side = body.side;
  const winner = Math.random() < g.home_prob ? "home" : "away";
  const built = mockNflBuild(g, winner);
  const margin = built.home_score - built.away_score; // home - away
  const totalPts = built.home_score + built.away_score;

  let won = false, push = false, dec = 1.909;
  if (market === "ml") {
    dec = side === "home" ? g.home_dec : g.away_dec;
    if (built.winner === "tie") push = true;
    else won = side === built.winner;
  } else if (market === "spread") {
    const adj = margin - g.spread; // >0 home covers, <0 away covers, ==0 push
    if (adj === 0) push = true;
    else won = side === "home" ? adj > 0 : adj < 0;
  } else {
    // total
    if (totalPts === g.total) push = true;
    else won = side === "over" ? totalPts > g.total : totalPts < g.total;
  }
  const payout = push ? stake : won ? Math.round(stake * dec) : 0;
  state.balance = Math.max(0, state.balance - stake + payout);
  return {
    sport: "nfl",
    home: { abbr: g.home.abbr, name: g.home.name },
    away: { abbr: g.away.abbr, name: g.away.name },
    home_score: built.home_score, away_score: built.away_score, winner: built.winner,
    market, side, line: { spread: g.spread, total: g.total },
    won, push, payout, stake, balance: state.balance,
    plays: built.plays, drives: built.drives,
  };
}

function mockNflBuild(g, winner) {
  const HOME = g.home.abbr, AWAY = g.away.abbr;
  // Target a handful of scoring drives so the demo game is short.
  const homeScores = winner === "home" ? [7, 7, 3] : [7, 3];
  const awayScores = winner === "home" ? [7, 3] : [7, 7, 3];
  const plays = [];
  let h = 0, a = 0;
  const clocks = ["14:02", "09:41", "03:18", "11:55", "05:07", "12:30", "06:44", "01:22"];
  let ci = 0, q = 1;
  const nextClock = () => {
    const c = clocks[Math.min(ci, clocks.length - 1)];
    q = Math.min(4, 1 + Math.floor(ci / 2));
    ci++;
    return c;
  };
  // Alternate possessions; each drive = a couple of plays then a score.
  const drives = [];
  const queue = [];
  const maxLen = Math.max(homeScores.length, awayScores.length);
  for (let i = 0; i < maxLen; i++) {
    if (i < awayScores.length) queue.push(["away", awayScores[i]]);
    if (i < homeScores.length) queue.push(["home", homeScores[i]]);
  }
  for (const [off, pts] of queue) {
    const offAbbr = off === "home" ? HOME : AWAY;
    const defAbbr = off === "home" ? AWAY : HOME;
    // offense drives toward the OTHER end zone (home attacks fieldpos 100, away attacks 0).
    const dir = off === "home" ? 1 : -1;
    let spot = off === "home" ? 25 : 75;
    const clock = nextClock();
    const driveStart = spot;
    // a run + a pass, then the score.
    plays.push(mkPlay(q, clock, offAbbr, defAbbr, 1, 10, spot, "run", 5, "gain", `${offAbbr} run +5`, h, a));
    spot += dir * 20;
    plays.push(mkPlay(q, clock, offAbbr, defAbbr, 2, 5, spot, "pass", 18, "first_down", `${offAbbr} pass +18 (1st down)`, h, a));
    spot += dir * 18;
    spot = Math.max(2, Math.min(98, spot));
    const endSpot = off === "home" ? 100 : 0;
    if (pts === 7) {
      if (off === "home") h += 6; else a += 6;
      plays.push(mkPlay(q, clock, offAbbr, defAbbr, 1, null, endSpot, "pass", 0, "touchdown", `${offAbbr} TOUCHDOWN`, h, a));
      if (off === "home") h += 1; else a += 1;
      plays.push(mkPlay(q, clock, offAbbr, defAbbr, null, null, endSpot, "xp", 0, "extra_point", `${offAbbr} extra point good`, h, a));
      drives.push({ off: offAbbr, start: driveStart, end: endSpot, plays: 3, result: "touchdown", points: 7, q });
    } else {
      if (off === "home") h += 3; else a += 3;
      plays.push(mkPlay(q, clock, offAbbr, defAbbr, 4, 3, spot, "fg", 0, "field_goal", `${offAbbr} field goal good`, h, a));
      drives.push({ off: offAbbr, start: driveStart, end: spot, plays: 3, result: "field_goal", points: 3, q });
    }
  }
  const realWinner = h > a ? "home" : a > h ? "away" : "tie";
  return { home_score: h, away_score: a, winner: realWinner, plays, drives };
}

function mkPlay(q, clock, off, def, down, togo, fieldpos, type, yards, result, desc, home, away) {
  return { q, clock, off, def, down, togo, fieldpos, type, yards, result, desc, home, away };
}

// ── NFL manual mock: pull-based live game ──
// Pre-generate ~40 plays with evolving scores; price odds on demand from the
// current margin + game clock (occasionally suspending a side). /livebet records
// the bet at the live price and deducts stake; settle credits payouts.
let mockMan = null;

function mockManStart() {
  const t = MOCK_TEAMS.nfl;
  const baseProb = 0.5 + (Math.random() * 0.3 - 0.1); // ~0.4..0.7 home
  const plays = mockManGen(t.home[1], t.away[1]);
  mockMan = {
    gid: "mock-" + Date.now(),
    home: { abbr: t.home[1], name: t.home[0] },
    away: { abbr: t.away[1], name: t.away[0] },
    baseProb, plays, cursor: 0, total: plays.length,
    pregameTotal: 44.5, bets: [], balance: state.balance,
  };
  return {
    gid: mockMan.gid, home: mockMan.home, away: mockMan.away,
    odds: mockManOdds(0), cursor: 0, done: false, total_plays: plays.length,
  };
}

function mockManGen(HOME, AWAY) {
  const plays = [];
  let h = 0, a = 0, q = 1, driveInQ = 0;
  let off = Math.random() < 0.5 ? "home" : "away";
  const clocks = ["14:30", "11:12", "07:48", "04:33", "01:05"];
  const outcomes = ["td", "fg", "punt", "punt", "td", "fg", "turnover"];
  while (plays.length < 38 && q <= 4) {
    const offAbbr = off === "home" ? HOME : AWAY;
    const defAbbr = off === "home" ? AWAY : HOME;
    const dir = off === "home" ? 1 : -1;
    let spot = off === "home" ? 25 : 75;
    const clock = clocks[Math.min(driveInQ, clocks.length - 1)];
    const res = outcomes[Math.floor(Math.random() * outcomes.length)];
    const setup = 2 + Math.floor(Math.random() * 3); // 2-4 plays before the result
    for (let k = 0; k < setup; k++) {
      const yd = Math.floor(Math.random() * 13) - 2; // -2..10
      spot = Math.max(3, Math.min(97, spot + dir * yd));
      const kind = Math.random() < 0.5 ? "run" : "pass";
      const togo = Math.max(1, 10 - Math.max(0, yd));
      plays.push(mkPlay(q, clock, offAbbr, defAbbr, (k % 4) + 1, togo, spot, kind, yd,
        yd >= 10 ? "first_down" : "gain", `${offAbbr} ${kind} ${yd >= 0 ? "+" : ""}${yd}`, h, a));
    }
    const endSpot = off === "home" ? 100 : 0;
    if (res === "td") {
      if (off === "home") h += 6; else a += 6;
      plays.push(mkPlay(q, clock, offAbbr, defAbbr, null, null, endSpot, "pass", 0, "touchdown", `${offAbbr} TOUCHDOWN`, h, a));
      if (off === "home") h += 1; else a += 1;
      plays.push(mkPlay(q, clock, offAbbr, defAbbr, null, null, endSpot, "xp", 0, "extra_point", `${offAbbr} extra point good`, h, a));
    } else if (res === "fg") {
      if (off === "home") h += 3; else a += 3;
      plays.push(mkPlay(q, clock, offAbbr, defAbbr, 4, null, spot, "fg", 0, "field_goal", `${offAbbr} field goal good`, h, a));
    } else if (res === "punt") {
      plays.push(mkPlay(q, clock, offAbbr, defAbbr, 4, null, spot, "punt", 0, "punt", `${offAbbr} punt`, h, a));
    } else {
      plays.push(mkPlay(q, clock, offAbbr, defAbbr, 3, null, spot, "pass", 0, "turnover", `${offAbbr} turnover on downs`, h, a));
    }
    off = off === "home" ? "away" : "home";
    if (++driveInQ >= 3) { driveInQ = 0; q++; }
  }
  return plays;
}

function mockCell(prob) {
  const p = Math.max(0.03, Math.min(0.97, prob));
  const dec = Math.round((1 / p) * 0.95 * 1000) / 1000;
  return { dec, american: mockDecToAmerican(dec) };
}

// Odds after `cursor` plays revealed (cursor 0 = pregame, 0-0).
function mockManOdds(cursor) {
  const m = mockMan;
  const pl = cursor > 0 ? m.plays[cursor - 1] : null;
  const h = pl ? pl.home : 0, a = pl ? pl.away : 0;
  const margin = h - a; // home − away
  const elapsed = Math.min(1, cursor / m.total);
  // Blend pregame prob (fading) with a margin/time read; small noise so lines move.
  let p = m.baseProb * (1 - elapsed) + (0.5 + 0.5 * Math.tanh(margin / 8)) * elapsed;
  p = Math.max(0.04, Math.min(0.96, p + margin * 0.004 + (Math.random() - 0.5) * 0.02));

  let mlHome = mockCell(p), mlAway = mockCell(1 - p);
  // Suspend the favorite's ML when a team pulls well ahead; suspend both right
  // after a score (markets reset) — shows the SUSPENDED state in the panel.
  if (margin >= 14) mlHome = null;
  else if (margin <= -14) mlAway = null;
  if (pl && SCORING.has(pl.result)) { mlHome = null; mlAway = null; }

  const expMargin = margin * 0.2 + (m.baseProb - 0.5) * 12 * (1 - elapsed);
  const sprLine = Math.round(expMargin * 2) / 2;
  const suspSpr = Math.random() < 0.07;
  const totLine = Math.round(((h + a) + m.pregameTotal * (1 - elapsed)) * 2) / 2;
  const juice = { dec: 1.909, american: -110 };

  return {
    ml: { home: mlHome, away: mlAway },
    spread: { line: sprLine, home: suspSpr ? null : juice, away: suspSpr ? null : juice },
    total: { line: totLine, over: juice, under: juice },
  };
}

function mockManAdvance() {
  const m = mockMan;
  if (!m) return { error: "no game" };
  if (m.cursor >= m.plays.length) return mockManSettle();
  const play = m.plays[m.cursor];
  m.cursor++;
  return {
    play, cursor: m.cursor, done: false,
    home_score: play.home, away_score: play.away, odds: mockManOdds(m.cursor),
  };
}

function mockManLivebet(body) {
  const m = mockMan;
  if (!m) return { error: "no game" };
  if (m.cursor >= m.plays.length) return { error: "game over" };
  const stake = Math.floor(Number(body.stake) || 0);
  if (stake < 1) return { error: "enter a stake" };
  if (stake > 25000) return { error: "live stake capped at 25,000" };
  if (stake > Math.floor(m.balance)) return { error: "not enough coins" };
  const odds = mockManOdds(m.cursor);
  const cell = cellFor(odds, body.market, body.side);
  if (!cell) return { error: "that market is suspended right now" };
  m.balance -= stake;
  state.balance = m.balance;
  m.bets.push({
    market: body.market, side: body.side, stake, dec: cell.dec, american: cell.american,
    takenLine: body.market === "spread" ? odds.spread.line : null,
    takenTotal: body.market === "total" ? odds.total.line : null,
  });
  return { accepted: true, market: body.market, side: body.side, dec: cell.dec, american: cell.american, stake, balance: state.balance };
}

function mockManSettle() {
  const m = mockMan;
  const last = m.plays[m.plays.length - 1];
  const h = last.home, a = last.away;
  const winner = h > a ? "home" : a > h ? "away" : "tie";
  const margin = h - a, totalPts = h + a;
  const bets = m.bets.map((b) => {
    let won = false, push = false;
    if (b.market === "ml") {
      if (winner === "tie") push = true; else won = b.side === winner;
    } else if (b.market === "spread") {
      const adj = margin - b.takenLine; // >0 home covers, <0 away covers
      if (adj === 0) push = true; else won = b.side === "home" ? adj > 0 : adj < 0;
    } else {
      if (totalPts === b.takenTotal) push = true;
      else won = b.side === "over" ? totalPts > b.takenTotal : totalPts < b.takenTotal;
    }
    const payout = push ? b.stake : won ? Math.round(b.stake * b.dec) : 0;
    return { market: b.market, side: b.side, stake: b.stake, dec: b.dec, american: b.american, won, push, payout };
  });
  m.balance += bets.reduce((s, b) => s + (b.payout || 0), 0);
  state.balance = Math.max(0, m.balance);
  return {
    done: true, settled: true, home: m.home, away: m.away,
    home_score: h, away_score: a, winner,
    line: { spread: margin, total: totalPts }, bets, balance: state.balance,
    play: last, cursor: m.cursor,
  };
}

main();
