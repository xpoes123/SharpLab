// SharpLab HQ — Stock Guess. Guess a stock's YTD % change for coins.
// Rounds POST to /api/v1/arcade/stockguess/* (session-cookie auth); a winning
// guess response carries the authoritative new balance, which we push back into
// the nav chip + on-page header.

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
const state = { me: null, balance: 0, wins: 0 };
const round = { token: null, ticker: null, company: null, solved: false, busy: false };

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

// ── UI element refs ──
const $ = (id) => document.getElementById(id);

function applyBalance(bal) {
  if (bal == null) return;
  state.balance = bal;
  renderNav(state.me && state.me.user);
  const pb = document.getElementById("pageBal");
  if (pb) pb.textContent = coins(bal);
}

function setBusy(busy) {
  round.busy = busy;
  document.querySelectorAll(".sgbtns .btn, .sgguess").forEach((el) => (el.disabled = busy));
}

const fmtPct = (n) => (n >= 0 ? "+" : "") + Number(n).toFixed(2) + "%";

// ── POST to a stockguess endpoint. Returns parsed JSON (with _status on error). ──
async function postSG(path, body) {
  if (MOCK) return mockSG(path, body);
  const r = await fetch("/api/v1/arcade/stockguess" + path, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok && j.error == null) {
    j.error = r.status === 401 ? "sign in to play" : `error ${r.status}`;
  }
  return j;
}

// ── Start a new round ──
async function newRound() {
  round.solved = false;
  round.token = null;
  const stage = $("sgStage");
  const ticker = $("sgTicker");
  const company = $("sgCompany");
  const reveal = $("sgReveal");
  const msg = $("sgMsg");
  const input = $("sgGuess");
  const btnRow = $("sgBtns");
  if (reveal) { reveal.innerHTML = ""; reveal.classList.remove("show"); }
  if (msg) { msg.textContent = "Enter your best guess (negatives allowed)."; msg.className = "sgmsg idle"; }
  if (input) input.value = "";
  // Restore the guessing controls
  if (btnRow) btnRow.innerHTML = `<button class="btn primary big" id="sgGuessBtn">Guess</button>`;
  if (ticker) ticker.textContent = "";
  if (company) company.textContent = "";
  if (stage) stage.classList.add("loading");
  setBusy(true);

  const res = await postSG("/new", {});
  if (res.error || res._status) {
    if (stage) stage.classList.remove("loading");
    setBusy(false);
    if (msg) { msg.textContent = "Couldn't load a stock."; msg.className = "sgmsg wrong"; }
    if (btnRow) btnRow.innerHTML = `<button class="btn primary big" id="sgRetry">Try again</button>`;
    return toast("❌ " + (res.error || "couldn't start a round"));
  }
  round.token = res.token;
  round.ticker = res.ticker;
  round.company = res.company;
  if (ticker) ticker.textContent = res.ticker || "???";
  if (company) company.textContent = res.company || "";
  if (stage) stage.classList.remove("loading");
  setBusy(false);
  if (input) input.focus();
}

// ── Reveal the outcome ──
function showReveal(res) {
  round.solved = true;
  const reveal = $("sgReveal");
  const btnRow = $("sgBtns");
  const msg = $("sgMsg");
  const dir = res.actual >= 0 ? "up" : "down";
  const verdict = res.close
    ? `<div class="sgverdict win">🎯 Nailed it — within 8 points!</div>
       ${res.reward ? `<div class="sgreward">+${num(res.reward)} 🪙</div>` : `<div class="sgcounter">Daily reward already claimed — no coins this time.</div>`}`
    : `<div class="sgverdict miss">Off by ${Number(res.delta).toFixed(2)} points</div>`;
  if (reveal) {
    reveal.classList.add("show");
    reveal.innerHTML =
      `<div class="sgactual ${dir}"><span class="lbl">${esc(res.company || res.ticker)} YTD</span>${fmtPct(res.actual)}</div>
       <div class="sgcompare"><span>Your guess: <b>${fmtPct(res.guess)}</b></span><span>Actual: <b>${fmtPct(res.actual)}</b></span></div>
       ${verdict}`;
  }
  if (msg) { msg.textContent = ""; msg.className = "sgmsg idle"; }
  if (btnRow) btnRow.innerHTML = `<button class="btn primary big" id="sgNext">Next stock →</button>`;
}

// ── Submit a guess ──
async function submitGuess() {
  if (round.busy || round.solved || !round.token) return;
  const input = $("sgGuess");
  const raw = (input && input.value || "").trim();
  if (raw === "") { if (input) input.focus(); return; }
  const guess = Number(raw.replace("%", "").replace(/\s/g, ""));
  if (!Number.isFinite(guess)) {
    const msg = $("sgMsg");
    if (msg) { msg.textContent = "Enter a number like 12.5 or -8.3"; msg.className = "sgmsg wrong"; }
    if (input && !REDUCE) {
      input.classList.remove("shake"); void input.offsetWidth; input.classList.add("shake");
      input.addEventListener("animationend", () => input.classList.remove("shake"), { once: true });
    }
    if (input) input.focus();
    return;
  }
  setBusy(true);
  const res = await postSG("/guess", { token: round.token, guess });
  setBusy(false);
  if (res.error || res._status) {
    return toast("❌ " + (res.error || "something went wrong"));
  }
  if (res.close && res.reward) {
    state.wins += 1;
    const c = $("sgWins");
    if (c) c.textContent = num(state.wins);
  }
  applyBalance(res.balance);
  showReveal(res);
}

// ── Delegated events ──
app.addEventListener("click", (e) => {
  if (e.target.closest("#sgGuessBtn")) return submitGuess();
  if (e.target.closest("#sgNext")) return newRound();
  if (e.target.closest("#sgRetry")) return newRound();
});
app.addEventListener("keydown", (e) => {
  if (e.target.id === "sgGuess" && e.key === "Enter") { e.preventDefault(); submitGuess(); }
});

function buildPage() {
  const signedOut = !state.me
    ? `<div class="card" style="max-width:460px;margin:0 auto 16px;text-align:center">
        <p class="muted" style="margin:0 0 10px">Sign in with Discord to play for coins.</p>
        <a class="btn" href="/api/v1/auth/discord/login">Sign in with Discord</a></div>`
    : "";
  app.innerHTML = `
    <div class="sg-head">
      <h1>📈 Stock Guess <span class="balancechip" id="pageBal">${coins(state.balance)}</span></h1>
      <p>Guess a stock's year-to-date % change. Land within 8 points to win coins.</p>
    </div>
    ${signedOut}
    <div class="sg-wrap">
      <div class="card sgcard">
        <div class="sghint">Mystery stock</div>
        <div class="sgstage loading" id="sgStage">
          <div class="sgticker" id="sgTicker"></div>
          <div class="sgcompany" id="sgCompany"></div>
          <div class="sgprompt">What's its YTD % change?</div>
        </div>
        <div class="sgreveal" id="sgReveal"></div>
        <div class="sgguessrow">
          <input class="sgguess" id="sgGuess" type="text" inputmode="decimal" autocomplete="off"
                 autocapitalize="off" spellcheck="false" placeholder="e.g. +12.5 or -8.3" />
        </div>
        <div class="sgmsg idle" id="sgMsg">Enter your best guess (negatives allowed).</div>
        <div class="sgbtns" id="sgBtns">
          <button class="btn primary big" id="sgGuessBtn">Guess</button>
        </div>
        <div class="sgcounter">Wins this session: <b id="sgWins">0</b></div>
      </div>
    </div>`;
  newRound();
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
// Mock mode (?mock=1): fake rounds without a backend so the page
// can be screenshot-tested offline. Real endpoints win by default.
// ─────────────────────────────────────────────────────────────
const MOCK = new URLSearchParams(location.search).get("mock") === "1";

const MOCK_STOCKS = [
  { ticker: "AAPL", company: "Apple", actual: 14.32 },
  { ticker: "TSLA", company: "Tesla", actual: -22.75 },
];
let mockIdx = 0;

function mockJSON(url) {
  if (url.startsWith("/api/v1/hq/me"))
    return { authenticated: true, user: { id: "1", username: "davidj", avatar: null }, balance: 12500 };
  return {};
}

function mockSG(path, body) {
  const s = MOCK_STOCKS[mockIdx % MOCK_STOCKS.length];
  if (path === "/new") {
    return { token: "mock-token", ticker: s.ticker, company: s.company };
  }
  // /guess
  mockIdx += 1;
  const guess = Number(body && body.guess) || 0;
  const delta = Math.abs(guess - s.actual);
  const close = delta <= 8.0;
  const reward = close ? 150 : 0;
  if (reward) state.balance += reward;
  return {
    actual: s.actual, guess, delta, close, reward,
    balance: state.balance, ticker: s.ticker, company: s.company,
  };
}

main();
