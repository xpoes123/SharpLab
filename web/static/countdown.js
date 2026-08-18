// SharpLab HQ — Countdown (Numbers Round). Build an arithmetic expression from a
// subset of six numbers to hit the target for coins. Rounds POST to
// /api/v1/arcade/countdown/* (session-cookie auth); the /solve response carries the
// authoritative new balance on an exact hit, which we push into the nav chip + header.

const app = document.getElementById("app");
const navRight = document.getElementById("navRight");

// ── Helpers (copied verbatim from casino.js / pokemon.js) ──
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
const state = { me: null, balance: 0, solved: 0 };
const round = { token: null, numbers: [], target: null, done: false, busy: false };

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
  document.querySelectorAll(".cd-btns .btn, .cd-input").forEach((el) => (el.disabled = busy));
}

// ── POST to a countdown endpoint. Returns parsed JSON (with _status on error). ──
async function postCD(path, body) {
  if (MOCK) return mockCD(path, body);
  const r = await fetch("/api/v1/arcade/countdown" + path, {
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

// ── Confetti-ish burst (respects reduced-motion) ──
function celebrate() {
  if (REDUCE) return;
  const layer = document.createElement("div");
  layer.className = "cd-confetti";
  const colors = ["#7aa2f7", "#bb9af7", "#9ece6a", "#e0af68", "#f7768e"];
  for (let i = 0; i < 28; i++) {
    const b = document.createElement("i");
    b.style.left = Math.random() * 100 + "%";
    b.style.background = colors[i % colors.length];
    b.style.animationDelay = Math.random() * 0.2 + "s";
    b.style.transform = `rotate(${Math.random() * 360}deg)`;
    layer.appendChild(b);
  }
  document.body.appendChild(layer);
  setTimeout(() => layer.remove(), 1600);
}

function renderTiles() {
  return round.numbers
    .map((n) => `<button type="button" class="cd-tile${n >= 25 ? " large" : ""}" data-val="${n}">${n}</button>`)
    .join("");
}

// ── Start a new round ──
async function newRound() {
  round.done = false;
  setBusy(true);
  const res = await postCD("/new", {});
  if (res.error || res._status) {
    setBusy(false);
    return toast("❌ " + (res.error || "couldn't start a round"));
  }
  round.token = res.token;
  round.numbers = res.numbers || [];
  round.target = res.target;
  const tiles = $("cdTiles");
  const target = $("cdTarget");
  const input = $("cdInput");
  const result = $("cdResult");
  const btns = $("cdBtns");
  if (tiles) tiles.innerHTML = renderTiles();
  if (target) target.textContent = num(res.target);
  if (input) { input.value = ""; input.disabled = false; }
  if (result) { result.innerHTML = ""; result.className = "cd-result"; }
  if (btns) btns.innerHTML =
    `<button class="btn primary big" id="cdSubmit">Submit</button>
     <button class="btn ghost" id="cdGiveUp">Give up</button>`;
  setBusy(false);
  if (input) input.focus();
}

// ── Submit an expression ──
async function submitExpr() {
  if (round.busy || round.done) return;
  const input = $("cdInput");
  const expr = (input && input.value || "").trim();
  if (!expr) { if (input) input.focus(); return; }
  setBusy(true);
  const res = await postCD("/solve", { token: round.token, expression: expr });
  setBusy(false);
  const result = $("cdResult");
  if (res.error && res.valid === undefined && (res._status || res.error === "sign in to play")) {
    return toast("❌ " + res.error);
  }
  if (res.valid === false) {
    if (result) { result.className = "cd-result bad"; result.innerHTML = `⚠️ ${esc(res.error || "invalid expression")}`; }
    toast("❌ " + (res.error || "invalid expression"));
    if (input && !REDUCE) {
      input.classList.remove("shake"); void input.offsetWidth; input.classList.add("shake");
      input.addEventListener("animationend", () => input.classList.remove("shake"), { once: true });
    }
    return;
  }
  if (res.exact) {
    round.done = true;
    state.solved += 1;
    const sc = $("cdSolved");
    if (sc) sc.textContent = num(state.solved);
    if (res.balance != null) applyBalance(res.balance);
    const rw = res.reward ? ` <span class="cd-reward">+${num(res.reward)} 🪙</span>` : "";
    if (result) {
      result.className = "cd-result hit";
      result.innerHTML = `🎯 <b>${num(res.value)}</b> — exact!${rw}`;
    }
    celebrate();
    const btns = $("cdBtns");
    if (btns) btns.innerHTML = `<button class="btn primary big" id="cdNext">New numbers →</button>`;
    if (input) input.disabled = true;
    return;
  }
  // Valid but not the target.
  if (result) {
    result.className = "cd-result off";
    result.innerHTML = `= <b>${num(res.value)}</b> · off by <b>${num(res.delta)}</b>`;
  }
}

// ── Give up ──
async function giveUp() {
  if (round.busy || round.done) return;
  setBusy(true);
  const res = await postCD("/reveal", { token: round.token });
  setBusy(false);
  if (res.error || res._status) return toast("❌ " + (res.error || "something went wrong"));
  round.done = true;
  const result = $("cdResult");
  if (result) {
    result.className = "cd-result off";
    result.innerHTML = esc(res.note || "No solution shown.");
  }
  const btns = $("cdBtns");
  if (btns) btns.innerHTML = `<button class="btn primary big" id="cdNext">New numbers →</button>`;
  const input = $("cdInput");
  if (input) input.disabled = true;
}

// ── Delegated events ──
app.addEventListener("click", (e) => {
  const tile = e.target.closest(".cd-tile");
  if (tile) {
    const input = $("cdInput");
    if (input && !input.disabled) {
      input.value += input.value && !/[\s(]$/.test(input.value) ? " " + tile.dataset.val : tile.dataset.val;
      input.focus();
    }
    return;
  }
  const op = e.target.closest(".cd-op");
  if (op) {
    const input = $("cdInput");
    if (input && !input.disabled) { input.value += op.dataset.op; input.focus(); }
    return;
  }
  if (e.target.closest("#cdSubmit")) return submitExpr();
  if (e.target.closest("#cdGiveUp")) return giveUp();
  if (e.target.closest("#cdNext")) return newRound();
  if (e.target.closest("#cdClear")) { const i = $("cdInput"); if (i && !i.disabled) { i.value = ""; i.focus(); } return; }
});
app.addEventListener("keydown", (e) => {
  if (e.target.id === "cdInput" && e.key === "Enter") { e.preventDefault(); submitExpr(); }
});

function buildPage() {
  const signedOut = !state.me
    ? `<div class="card" style="max-width:520px;margin:0 auto 16px;text-align:center">
        <p class="muted" style="margin:0 0 10px">Sign in with Discord to play for coins.</p>
        <a class="btn" href="/api/v1/auth/discord/login">Sign in with Discord</a></div>`
    : "";
  const ops = ["+", "−", "×", "÷", "(", ")"].map((sym, i) => {
    const real = ["+", "-", "*", "/", "(", ")"][i];
    return `<button type="button" class="cd-op" data-op="${real}">${sym}</button>`;
  }).join("");
  app.innerHTML = `
    <div class="cd-head">
      <h1>🔢 Countdown <span class="balancechip" id="pageBal">${coins(state.balance)}</span></h1>
      <p>Use any subset of the six numbers with + − × ÷ and parentheses to reach the target.
         Whole numbers only — no fractions. Nail it exactly for coins.</p>
    </div>
    ${signedOut}
    <div class="cd-wrap">
      <div class="card cd-card">
        <div class="cd-targetbox"><span class="cd-tlabel">Target</span><span class="cd-target" id="cdTarget">—</span></div>
        <div class="cd-tiles" id="cdTiles"></div>
        <div class="cd-oprow">${ops}<button type="button" class="cd-op danger" id="cdClear">clear</button></div>
        <input class="cd-input" id="cdInput" type="text" inputmode="text" autocomplete="off"
               autocapitalize="off" spellcheck="false" placeholder="e.g. (100 + 25) * 3 - 4" />
        <div class="cd-result" id="cdResult"></div>
        <div class="cd-btns" id="cdBtns">
          <button class="btn primary big" id="cdSubmit">Submit</button>
          <button class="btn ghost" id="cdGiveUp">Give up</button>
        </div>
        <div class="cd-counter">Solved this session: <b id="cdSolved">0</b></div>
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
// Mock mode (?mock=1): fake rounds + a LOCAL integer-only expression
// evaluator (same rules as the server) so the page works offline for
// screenshot testing. Real endpoints win by default.
// ─────────────────────────────────────────────────────────────
const MOCK = new URLSearchParams(location.search).get("mock") === "1";

function mockJSON(url) {
  if (url.startsWith("/api/v1/hq/me"))
    return { authenticated: true, user: { id: "1", username: "davidj", avatar: null }, balance: 12500 };
  return {};
}

// Local integer-only evaluator (hand-written tokenizer + shunting-yard — NO eval).
function mockEval(expr, numbers) {
  const toks = [];
  let i = 0;
  while (i < expr.length) {
    const c = expr[i];
    if (/\s/.test(c)) { i++; continue; }
    if (/\d/.test(c)) { let j = i; while (j < expr.length && /\d/.test(expr[j])) j++; toks.push(parseInt(expr.slice(i, j), 10)); i = j; continue; }
    if ("+-*/()".includes(c)) { toks.push(c); i++; continue; }
    throw new Error("illegal character");
  }
  if (!toks.length) throw new Error("empty expression");
  const prec = { "+": 1, "-": 1, "*": 2, "/": 2 };
  const out = [], st = [];
  let prev = null;
  for (const t of toks) {
    if (typeof t === "number") {
      if (prev === "num" || prev === ")") throw new Error("missing operator");
      out.push(t); prev = "num";
    } else if ("+-*/".includes(t)) {
      if (prev === null || prev === "op" || prev === "(") throw new Error("misplaced operator");
      while (st.length && "+-*/".includes(st[st.length - 1]) && prec[st[st.length - 1]] >= prec[t]) out.push(st.pop());
      st.push(t); prev = "op";
    } else if (t === "(") {
      if (prev === "num" || prev === ")") throw new Error("missing operator");
      st.push(t); prev = "(";
    } else {
      if (prev !== "num" && prev !== ")") throw new Error("misplaced parenthesis");
      while (st.length && st[st.length - 1] !== "(") out.push(st.pop());
      if (!st.length) throw new Error("unbalanced parentheses");
      st.pop(); prev = ")";
    }
  }
  if (prev === "op" || prev === "(") throw new Error("incomplete expression");
  while (st.length) { const op = st.pop(); if (op === "(") throw new Error("unbalanced parentheses"); out.push(op); }
  const vs = [], used = [];
  for (const t of out) {
    if (typeof t === "number") { vs.push(t); used.push(t); continue; }
    if (vs.length < 2) throw new Error("malformed expression");
    const b = vs.pop(), a = vs.pop();
    if (t === "+") vs.push(a + b);
    else if (t === "-") vs.push(a - b);
    else if (t === "*") vs.push(a * b);
    else { if (b === 0 || a % b !== 0) throw new Error("division must be exact"); vs.push(a / b); }
  }
  if (vs.length !== 1) throw new Error("malformed expression");
  const avail = {};
  for (const x of numbers) avail[x] = (avail[x] || 0) + 1;
  for (const x of used) { if (!avail[x]) throw new Error(`number ${x} is not available`); avail[x]--; }
  return vs[0];
}

const MOCK_NUMBERS = [100, 75, 25, 6, 3, 2];
const MOCK_TARGET = 452;
function mockCD(path, body) {
  if (path === "/new") return { token: "mock", numbers: MOCK_NUMBERS, target: MOCK_TARGET };
  if (path === "/reveal") return { numbers: MOCK_NUMBERS, target: MOCK_TARGET, note: "No solution shown — reach the target yourself next time." };
  // /solve
  let value;
  try { value = mockEval(String((body && body.expression) || ""), MOCK_NUMBERS); }
  catch (e) { return { valid: false, error: e.message }; }
  const exact = value === MOCK_TARGET;
  const out = { value, target: MOCK_TARGET, valid: true, exact, delta: Math.abs(value - MOCK_TARGET) };
  if (exact) { const reward = 30; state.balance += reward; out.reward = reward; out.balance = state.balance; }
  return out;
}

main();
