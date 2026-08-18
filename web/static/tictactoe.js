// SharpLab HQ — Tic-Tac-Toe. Free 2-player online game over WebSockets.
// Proves the web-native multiplayer pattern: rooms are created straight from the
// browser (no Discord token), players share a 4-letter code, and moves flow over
// a socket to /ws/tictactoe/{room_id}. No coins involved.

const app = document.getElementById("app");
const navRight = document.getElementById("navRight");

// ── Helpers (copied verbatim from casino.js / pokemon.js) ──
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
const $ = (id) => document.getElementById(id);
const game = {
  ws: null,
  roomId: null,
  code: null,
  name: "",
  you: null,       // "X" | "O"
  board: [null, null, null, null, null, null, null, null, null],
  turn: "X",
  winner: null,    // null | "X" | "O" | "draw"
  players: [],
};

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
  const res = await postJSON("/api/v1/tictactoe/rooms", { name });
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
  const res = await postJSON(`/api/v1/tictactoe/rooms/${code}/join`, { name });
  if (res._status === 404) return toast("❌ No room with that code.");
  if (res._status === 409) return toast("❌ That room is full.");
  if (res._status || !res.room_id) return toast("❌ Couldn't join the room.");
  game.name = name;
  game.code = code;
  connect(res.room_id);
}

function readName() {
  const el = $("tttName");
  const name = ((el && el.value) || "").trim();
  if (!name) { if (el) el.focus(); toast("Enter a display name first."); return null; }
  return name.slice(0, 32);
}

// ── WebSocket ──
function connect(roomId) {
  game.roomId = roomId;
  try {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws/tictactoe/${roomId}`);
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
        const s = $("tttStatus");
        if (s) { s.textContent = "Disconnected."; s.className = "ttt-status wait"; }
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
    game.players = msg.players || [];
    game.you = msg.you;
    renderGame();
  }
}

function sendMove(cell) {
  if (!game.ws || game.ws.readyState !== WebSocket.OPEN) return;
  if (game.winner) return;
  if (game.you !== game.turn) return;
  if (game.players.length < 2) return;
  if (game.board[cell] != null) return;
  game.ws.send(JSON.stringify({ type: "move", cell }));
}

function sendRematch() {
  if (!game.ws || game.ws.readyState !== WebSocket.OPEN) return;
  game.ws.send(JSON.stringify({ type: "rematch" }));
}

// ── Rendering ──
function buildLobby() {
  app.innerHTML = `
    <div class="ttt-head">
      <h1>⭕ Tic-Tac-Toe ❌</h1>
      <p>Free 2-player online game. Create a room, share the code, and play.</p>
    </div>
    <div class="ttt-wrap">
      <div class="card ttt-lobby">
        <div class="ttt-field">
          <label for="tttName">Display name</label>
          <input class="ttt-input" id="tttName" type="text" maxlength="32" autocomplete="off"
                 spellcheck="false" placeholder="Your name" value="${esc(defaultName())}" />
        </div>
        <div class="ttt-actions">
          <button class="btn primary big" id="tttCreate">Create room</button>
        </div>
        <div class="ttt-sep">— or join a room —</div>
        <div class="ttt-field">
          <label for="tttCode">Room code</label>
          <div class="ttt-joinrow">
            <input class="ttt-input code" id="tttCode" type="text" maxlength="4" autocomplete="off"
                   spellcheck="false" placeholder="ABCD" />
            <button class="btn" id="tttJoin">Join</button>
          </div>
        </div>
      </div>
    </div>`;
  game._codeInput = $("tttCode");
  $("tttCreate").addEventListener("click", createRoom);
  $("tttJoin").addEventListener("click", joinRoom);
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

  let statusText = "", statusClass = "ttt-status wait";
  if (!twoPlayers) {
    statusText = "Waiting for opponent…";
  } else if (game.winner === "draw") {
    statusText = "It's a draw.";
    statusClass = "ttt-status draw";
  } else if (game.winner) {
    if (game.winner === game.you) { statusText = "You win! 🎉"; statusClass = "ttt-status win"; }
    else { statusText = "You lose."; statusClass = "ttt-status lose"; }
  } else if (game.turn === game.you) {
    statusText = "Your turn";
    statusClass = "ttt-status yours";
  } else {
    statusText = `${esc(opp ? opp.name : "Opponent")}'s turn`;
    statusClass = "ttt-status theirs";
  }

  const winLine = game.winner && game.winner !== "draw" ? winningLine(game.board) : null;
  const locked = !twoPlayers || game.winner || game.turn !== game.you;

  const cells = game.board.map((v, i) => {
    const playable = !locked && v == null;
    const inWin = winLine && winLine.includes(i);
    const mark = v ? `<span class="${v === "X" ? "mx" : "mo"} ttt-mark-in">${v}</span>` : "";
    return `<div class="ttt-cell${playable ? " playable" : ""}${inWin ? " win" : ""}" data-cell="${i}">${mark}</div>`;
  }).join("");

  const pchip = (p) => {
    if (!p) return `<div class="ttt-pchip"><span class="ttt-mark ${game.you === "X" ? "o" : "x"}">?</span><span class="ttt-pname muted">waiting…</span></div>`;
    const active = !game.winner && twoPlayers && game.turn === p.symbol;
    const isMe = p.symbol === game.you;
    return `<div class="ttt-pchip${active ? " active" : ""}${isMe ? " me" : ""}">
      <span class="ttt-mark ${p.symbol === "X" ? "x" : "o"}">${p.symbol}</span>
      <span class="ttt-pname">${esc(p.name)}</span>
      ${isMe ? `<span class="ttt-pyou">(you)</span>` : ""}
      <span class="ttt-dot${p.connected ? " on" : ""}" title="${p.connected ? "connected" : "offline"}"></span>
    </div>`;
  };

  const banner = game.winner
    ? `<div class="ttt-banner ttt-rematch"><button class="btn primary" id="tttRematch">Rematch</button></div>`
    : "";

  app.innerHTML = `
    <div class="ttt-head">
      <h1>⭕ Tic-Tac-Toe ❌</h1>
    </div>
    <div class="ttt-wrap">
      <div class="card" style="display:flex;flex-direction:column;gap:var(--s-4)">
        <div class="ttt-codebox">
          <div class="ttt-codelabel">Room code</div>
          <div class="ttt-code" id="tttCodeBox" title="Click to copy">${esc(game.code || "····")}</div>
          <span class="ttt-copyhint">Share this code with a friend to play.</span>
        </div>
        <div class="ttt-players">${pchip(me)}${pchip(opp)}</div>
        <div class="${statusClass}" id="tttStatus">${statusText}</div>
        <div class="ttt-board${locked ? " locked" : ""}" id="tttBoard">${cells}</div>
        ${banner}
      </div>
    </div>`;

  const board = $("tttBoard");
  board.addEventListener("click", (e) => {
    const cellEl = e.target.closest(".ttt-cell");
    if (!cellEl) return;
    const i = Number(cellEl.dataset.cell);
    if (Number.isInteger(i)) sendMove(i);
  });
  const codeBox = $("tttCodeBox");
  if (codeBox) codeBox.addEventListener("click", copyCode);
  const rematch = $("tttRematch");
  if (rematch) rematch.addEventListener("click", sendRematch);
}

function winningLine(board) {
  const lines = [
    [0, 1, 2], [3, 4, 5], [6, 7, 8],
    [0, 3, 6], [1, 4, 7], [2, 5, 8],
    [0, 4, 8], [2, 4, 6],
  ];
  for (const l of lines) {
    const [a, b, c] = l;
    if (board[a] && board[a] === board[b] && board[a] === board[c]) return l;
  }
  return null;
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
