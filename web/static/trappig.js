// SharpLab HQ — Trap the Pig (free-play arcade). A purely client-side board:
// drop a fence each turn, the pig bolts one hex toward its nearest escape
// (shared engine in trappig_board.js, identical to the server AI). No coins, no
// server round — this is the practice/fun version linked from the /games grid.
// The Daily version (daily.html) uses the SAME board module against a server
// board and submits for rank.

const B = window.TrapPigBoard;
const app = document.getElementById("app");
const navRight = document.getElementById("navRight");

// ── Helpers (copied from threecardpoker.js) ──
const num = (n) => (n == null ? "—" : Number(n).toLocaleString());
const esc = (s) =>
  String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function getJSON(url) {
  try {
    const r = await fetch(url, { credentials: "include" });
    if (!r.ok) return { _status: r.status };
    return await r.json();
  } catch (_) {
    return { _status: 0 };
  }
}

const state = { me: null, balance: 0 };

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

// ── Free-play board config: medium-sized, mirrors the prototype ──
const ROWS = 11, COLS = 11;

// seeded RNG so a board is reproducible within a session (New board re-rolls)
function mulberry32(a) {
  return function () {
    a |= 0; a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
let rng = mulberry32(Date.now() & 0xffffff);

let board = { rows: ROWS, cols: COLS, pig: [5, 5], fences: new Set() };
let count = 0, started = false, t0 = 0, timer = null, over = false;

const $ = (id) => document.getElementById(id);
const fmt = (ms) => {
  const s = Math.floor(ms / 1000);
  return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
};

// Generate a fresh random board client-side (like the prototype: ~7-11 fences,
// none on the pig / its neighbours).
function newBoard() {
  const pig = [Math.floor(ROWS / 2), Math.floor(COLS / 2)];
  const fences = new Set();
  const block = Math.floor(rng() * 5) + 7;
  const forbid = new Set([
    B.key(pig[0], pig[1]),
    ...B.neighbours(pig[0], pig[1], ROWS, COLS).map(([r, c]) => B.key(r, c)),
  ]);
  let tries = 0;
  while (fences.size < block && tries < 400) {
    tries++;
    const r = Math.floor(rng() * ROWS), c = Math.floor(rng() * COLS);
    if (forbid.has(B.key(r, c))) continue;
    fences.add(B.key(r, c));
  }
  board = { rows: ROWS, cols: COLS, pig, fences };
  count = 0; started = false; over = false;
  if (timer) { clearInterval(timer); timer = null; }
  draw();
  $("fences").textContent = "0";
  $("time").textContent = "0:00";
  $("banner").className = "banner";
}

function draw() {
  B.renderInto($("board"), board, onPlace);
}

function onPlace(r, c) {
  if (over) return;
  const k = B.key(r, c);
  if (board.fences.has(k) || (board.pig[0] === r && board.pig[1] === c)) return;
  if (!started) {
    started = true;
    t0 = Date.now();
    timer = setInterval(() => { $("time").textContent = fmt(Date.now() - t0); }, 250);
  }
  board.fences.add(k);
  count++;
  $("fences").textContent = String(count);
  // Move the pig with the shared (server-identical) AI.
  const nxt = B.pigStep(board.pig, board.fences, ROWS, COLS);
  if (nxt === null) return win();
  board.pig = nxt;
  draw();
  if (B.isBorder(board.pig[0], board.pig[1], ROWS, COLS)) return escaped();
}

function stop() { if (timer) { clearInterval(timer); timer = null; } }
function elapsed() { return started ? Date.now() - t0 : 0; }

function win() {
  over = true; stop(); draw();
  banner("win", "🎉 Trapped!", "🟩".repeat(Math.min(count, 12)),
    `Penned the pig with ${count} fences · ${fmt(elapsed())}`);
}
function escaped() {
  over = true; stop(); draw();
  banner("lose", "🐷 It got away!", "🟥".repeat(Math.min(count, 12)),
    `Escaped after ${count} fences — fewer, tighter next time.`);
}
function banner(kind, title, grid, line) {
  const b = $("banner");
  b.className = "banner show " + kind;
  b.innerHTML = `<h2>${esc(title)}</h2><div class="grid2">${grid}</div><div class="line">${esc(line)}</div>`;
}

function buildPage() {
  app.innerHTML = `<div class="wrap">
    <div class="pig-head">
      <div class="eyebrow">SharpLab · Arcade</div>
      <h1>🐷 Trap the Pig</h1>
      <p>Drop a fence each turn. The pig bolts one hex toward its nearest way out.
         Wall it in using as few fences as you can. Free play — no coins.
         Want to compete? Try the <a href="/daily">Daily</a>.</p>
    </div>
    <div class="stats">
      <div class="stat-box"><div class="k">Fences</div><div class="v" id="fences">0</div></div>
      <div class="stat-box"><div class="k">Time</div><div class="v" id="time">0:00</div></div>
    </div>
    <div class="stage"><svg id="board"></svg></div>
    <div class="banner" id="banner"></div>
    <div class="actions">
      <button class="btn ghost" id="newBoard" style="flex:1">New board</button>
    </div>
  </div>`;
  $("newBoard").onclick = () => {
    rng = mulberry32((Math.floor(Math.random() * 1e9)) >>> 0);
    newBoard();
  };
  newBoard();
}

async function main() {
  const me = await getJSON("/api/v1/hq/me");
  const loggedIn = me && me.authenticated;
  state.me = loggedIn ? me : null;
  state.balance = loggedIn ? me.balance || 0 : 0;
  renderNav(loggedIn ? me.user : null);
  buildPage();
}

main();
