// SharpLab HQ — Connect Four. Free 2-player online game over WebSockets.
// Proves the web-native multiplayer pattern: rooms are created straight from the
// browser (no Discord token), players share a 4-letter code, and moves flow over
// a socket to /ws/connect4/{room_id}. No coins involved.

const app = document.getElementById("app");
const navRight = document.getElementById("navRight");

// ── Helpers (copied verbatim from casino.js / tictactoe.js) ──
const num = (n) => (n == null ? "—" : Number(n).toLocaleString());
const esc = (s) =>
  String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function getJSON(url) {
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

// ── Game state ──
const COLS = 7, ROWS = 6;
const $ = (id) => document.getElementById(id);
const game = {
  ws: null,
  roomId: null,
  code: null,
  name: "",
  you: null,       // "R" | "Y"
  board: emptyBoard(),
  turn: "R",
  winner: null,    // null | "R" | "Y" | "draw"
  winningCells: null,
  players: [],
  hoverCol: null,
};

function emptyBoard() {
  return Array.from({ length: ROWS }, () => Array.from({ length: COLS }, () => ""));
}

// ── REST: create / join a room ──
async function postJSON(url, body) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) j._status = r.status;
  return j;
}

async function createRoom() {
  const name = readName();
  if (!name) return;
  const res = await postJSON("/api/v1/connect4/rooms", { name });
  if (res._status || !res.room_id) return toast("❌ Couldn't create a room.");
  game.name = name;
  game.code = res.code;
  connect(res.room_id);
}

async function joinRoom() {
  const name = readName();
  if (!name) return;
  const code = ((game._codeInput && game._codeInput.value) || "").trim().toUpperCase();
  if (code.length !== 4) return toast("Enter the 4-letter room code.");
  const res = await postJSON(`/api/v1/connect4/rooms/${code}/join`, { name });
  if (res._status === 404) return toast("❌ No room with that code.");
  if (res._status === 409) return toast("❌ That room is full.");
  if (res._status || !res.room_id) return toast("❌ Couldn't join the room.");
  game.name = name;
  game.code = code;
  connect(res.room_id);
}

function readName() {
  const el = $("c4Name");
  const name = ((el && el.value) || "").trim();
  if (!name) { if (el) el.focus(); toast("Enter a display name first."); return null; }
  return name.slice(0, 32);
}

// ── WebSocket ──
function connect(roomId) {
  game.roomId = roomId;
  try {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws/connect4/${roomId}`);
    game.ws = ws;
    ws.onopen = () => ws.send(JSON.stringify({ type: "identify", name: game.name }));
    ws.onmessage = (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch { return; }
      handleMessage(msg);
    };
    ws.onerror = () => toast("⚠️ Connection error.");
    ws.onclose = () => {
      if (game.ws === ws) {
        game.ws = null;
        const s = $("c4Status");
        if (s) { s.textContent = "Disconnected."; s.className = "c4-status wait"; }
      }
    };
  } catch {
    toast("⚠️ Could not open a connection.");
  }
}

function handleMessage(msg) {
  if (!msg || typeof msg !== "object") return;
  if (msg.type === "error") return toast("⚠️ " + (msg.message || "error"));
  if (msg.type === "pong") return;
  if (msg.type === "state") {
    game.code = msg.code || game.code;
    game.board = Array.isArray(msg.board) ? msg.board : game.board;
    game.turn = msg.turn;
    game.winner = msg.winner;
    game.winningCells = Array.isArray(msg.winning_cells) ? msg.winning_cells : null;
    game.players = msg.players || [];
    game.you = msg.you;
    renderGame();
  }
}

function sendMove(col) {
  if (!game.ws || game.ws.readyState !== WebSocket.OPEN) return;
  if (game.winner) return;
  if (game.you !== game.turn) return;
  if (game.players.length < 2) return;
  if (game.board[0][col] !== "") return; // column full
  game.ws.send(JSON.stringify({ type: "move", col }));
}

function sendRematch() {
  if (!game.ws || game.ws.readyState !== WebSocket.OPEN) return;
  game.ws.send(JSON.stringify({ type: "rematch" }));
}

// ── Rendering ──
function buildLobby() {
  app.innerHTML = `
    <div class="c4-head">
      <h1>🔴 Connect Four 🟡</h1>
      <p>Free 2-player online game. Create a room, share the code, and play.</p>
    </div>
    <div class="c4-wrap">
      <div class="card c4-lobby">
        <div class="c4-field">
          <label for="c4Name">Display name</label>
          <input class="c4-input" id="c4Name" type="text" maxlength="32" autocomplete="off"
                 spellcheck="false" placeholder="Your name" value="${esc(defaultName())}" />
        </div>
        <div class="c4-actions">
          <button class="btn primary big" id="c4Create">Create room</button>
        </div>
        <div class="c4-sep">— or join a room —</div>
        <div class="c4-field">
          <label for="c4Code">Room code</label>
          <div class="c4-joinrow">
            <input class="c4-input code" id="c4Code" type="text" maxlength="4" autocomplete="off"
                   spellcheck="false" placeholder="ABCD" />
            <button class="btn" id="c4Join">Join</button>
          </div>
        </div>
      </div>
    </div>`;
  game._codeInput = $("c4Code");
  $("c4Create").addEventListener("click", createRoom);
  $("c4Join").addEventListener("click", joinRoom);
  game._codeInput.addEventListener("keydown", (e) => { if (e.key === "Enter") joinRoom(); });
  game._codeInput.addEventListener("input", (e) => {
    e.target.value = e.target.value.toUpperCase().replace(/[^A-Z]/g, "").slice(0, 4);
  });
}

function defaultName() {
  if (state.me && state.me.user && state.me.user.username) return state.me.user.username;
  return "";
}

function renderGame() {
  const twoPlayers = game.players.length >= 2;
  const me = game.players.find((p) => p.symbol === game.you);
  const opp = game.players.find((p) => p.symbol !== game.you);

  let statusText = "", statusClass = "c4-status wait";
  if (!twoPlayers) {
    statusText = "Waiting for opponent…";
  } else if (game.winner === "draw") {
    statusText = "It's a draw.";
    statusClass = "c4-status draw";
  } else if (game.winner) {
    if (game.winner === game.you) { statusText = "You win! 🎉"; statusClass = "c4-status win"; }
    else { statusText = "You lose."; statusClass = "c4-status lose"; }
  } else if (game.turn === game.you) {
    statusText = "Your turn";
    statusClass = "c4-status yours";
  } else {
    statusText = `${esc(opp ? opp.name : "Opponent")}'s turn`;
    statusClass = "c4-status theirs";
  }

  const locked = !twoPlayers || game.winner || game.turn !== game.you;
  const winSet = new Set((game.winningCells || []).map(([r, c]) => `${r},${c}`));

  // Build the board: ROWS rows × COLS columns of circular slots.
  let cellsHtml = "";
  for (let r = 0; r < ROWS; r++) {
    for (let c = 0; c < COLS; c++) {
      const v = (game.board[r] && game.board[r][c]) || "";
      const cls = v === "R" ? " r" : v === "Y" ? " y" : "";
      const inWin = winSet.has(`${r},${c}`);
      const drop = v ? ` c4-drop-in` : "";
      cellsHtml += `<div class="c4-cell${cls}${inWin ? " win" : ""}${drop}" data-col="${c}" data-row="${r}"></div>`;
    }
  }

  // Preview row (one hover-disc slot per column, above the board).
  let previewHtml = "";
  for (let c = 0; c < COLS; c++) {
    const canDrop = !locked && game.board[0][c] === "";
    previewHtml += `<div class="c4-prev${canDrop ? " open" : ""}" data-col="${c}"></div>`;
  }

  const pchip = (p, fallbackSym) => {
    if (!p) return `<div class="c4-pchip"><span class="c4-disc ${fallbackSym === "R" ? "r" : "y"}"></span><span class="c4-pname muted">waiting…</span></div>`;
    const active = !game.winner && twoPlayers && game.turn === p.symbol;
    const isMe = p.symbol === game.you;
    return `<div class="c4-pchip${active ? " active" : ""}${isMe ? " me" : ""}">
      <span class="c4-disc ${p.symbol === "R" ? "r" : "y"}"></span>
      <span class="c4-pname">${esc(p.name)}</span>
      ${isMe ? `<span class="c4-pyou">(you)</span>` : ""}
      <span class="c4-dot${p.connected ? " on" : ""}" title="${p.connected ? "connected" : "offline"}"></span>
    </div>`;
  };

  const banner = game.winner
    ? `<div class="c4-banner c4-rematch"><button class="btn primary" id="c4Rematch">Rematch</button></div>`
    : "";

  const meSym = game.you || "R";
  const oppSym = meSym === "R" ? "Y" : "R";

  app.innerHTML = `
    <div class="c4-head">
      <h1>🔴 Connect Four 🟡</h1>
    </div>
    <div class="c4-wrap">
      <div class="card" style="display:flex;flex-direction:column;gap:var(--s-4)">
        <div class="c4-codebox">
          <div class="c4-codelabel">Room code</div>
          <div class="c4-code" id="c4CodeBox" title="Click to copy">${esc(game.code || "····")}</div>
          <span class="c4-copyhint">Share this code with a friend to play.</span>
        </div>
        <div class="c4-players">${pchip(me, meSym)}${pchip(opp, oppSym)}</div>
        <div class="${statusClass}" id="c4Status">${statusText}</div>
        <div class="c4-boardwrap${locked ? " locked" : ""}">
          <div class="c4-preview" id="c4Preview" style="grid-template-columns:repeat(${COLS},1fr)">${previewHtml}</div>
          <div class="c4-board" id="c4Board" style="grid-template-columns:repeat(${COLS},1fr)">${cellsHtml}</div>
        </div>
        ${banner}
      </div>
    </div>`;

  const board = $("c4Board");
  const preview = $("c4Preview");
  board.addEventListener("click", (e) => {
    const cellEl = e.target.closest(".c4-cell");
    if (!cellEl) return;
    const c = Number(cellEl.dataset.col);
    if (Number.isInteger(c)) sendMove(c);
  });
  board.addEventListener("mouseover", (e) => {
    const cellEl = e.target.closest(".c4-cell");
    if (!cellEl) return;
    setHover(Number(cellEl.dataset.col), locked, meSym);
  });
  board.addEventListener("mouseleave", () => setHover(null, locked, meSym));

  const codeBox = $("c4CodeBox");
  if (codeBox) codeBox.addEventListener("click", copyCode);
  const rematch = $("c4Rematch");
  if (rematch) rematch.addEventListener("click", sendRematch);
}

function setHover(col, locked, meSym) {
  const preview = $("c4Preview");
  if (!preview) return;
  for (const el of preview.children) {
    el.classList.remove("show", "r", "y");
  }
  if (col == null || locked) return;
  if (game.board[0][col] !== "") return; // column full
  const el = preview.children[col];
  if (el) { el.classList.add("show", meSym === "R" ? "r" : "y"); }
}

function copyCode() {
  if (!game.code) return;
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(game.code).then(
      () => toast("📋 Code copied: " + game.code),
      () => toast("Code: " + game.code)
    );
  } else {
    toast("Code: " + game.code);
  }
}

async function main() {
  const me = await getJSON("/api/v1/hq/me");
  const loggedIn = me && me.authenticated;
  state.me = loggedIn ? me : null;
  state.balance = loggedIn ? (me.balance || 0) : 0;
  renderNav(loggedIn ? me.user : null);
  buildLobby();
}

main();
