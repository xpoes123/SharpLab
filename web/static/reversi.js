// SharpLab HQ — Reversi (Othello). Free 2-player online game over WebSockets.
// Web-native multiplayer: rooms are created straight from the browser (no Discord
// token), players share a 4-letter code, and moves flow over a socket to
// /ws/reversi/{room_id}. No coins involved.

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
const $ = (id) => document.getElementById(id);
function freshBoard() {
  const b = Array.from({ length: 8 }, () => Array(8).fill(""));
  b[3][3] = "W"; b[3][4] = "B"; b[4][3] = "B"; b[4][4] = "W";
  return b;
}
const game = {
  ws: null,
  roomId: null,
  code: null,
  name: "",
  you: null,        // "B" | "W"
  board: freshBoard(),
  turn: "B",
  winner: null,     // null | "B" | "W" | "draw"
  skipped: null,    // disc auto-skipped last move | null
  scores: { B: 2, W: 2 },
  legal: [],        // [[r,c], ...] for the viewer whose turn it is
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
  const res = await postJSON("/api/v1/reversi/rooms", { name });
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
  const res = await postJSON(`/api/v1/reversi/rooms/${code}/join`, { name });
  if (res._status === 404) return toast("❌ No room with that code.");
  if (res._status === 409) return toast("❌ That room is full.");
  if (res._status || !res.room_id) return toast("❌ Couldn't join the room.");
  game.name = name;
  game.code = code;
  connect(res.room_id);
}

function readName() {
  const el = $("rvName");
  const name = ((el && el.value) || "").trim();
  if (!name) { if (el) el.focus(); toast("Enter a display name first."); return null; }
  return name.slice(0, 32);
}

// ── WebSocket ──
function connect(roomId) {
  game.roomId = roomId;
  try {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws/reversi/${roomId}`);
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
        const s = $("rvStatus");
        if (s) { s.textContent = "Disconnected."; s.className = "rv-status wait"; }
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
    game.skipped = msg.skipped || null;
    game.scores = msg.scores || game.scores;
    game.legal = Array.isArray(msg.legal_moves) ? msg.legal_moves : [];
    game.players = msg.players || [];
    game.you = msg.you;
    renderGame();
    if (game.skipped) {
      const who = game.skipped === game.you ? "You have" : "Opponent has";
      toast(`⏭️ ${who} no legal move — turn skipped.`);
    }
  }
}

function isLegal(row, col) {
  return game.legal.some(([r, c]) => r === row && c === col);
}

function sendMove(row, col) {
  if (!game.ws || game.ws.readyState !== WebSocket.OPEN) return;
  if (game.winner) return;
  if (game.you !== game.turn) return;
  if (game.players.length < 2) return;
  if (game.board[row][col] !== "") return;
  if (!isLegal(row, col)) return;
  game.ws.send(JSON.stringify({ type: "move", row, col }));
}

function sendRematch() {
  if (!game.ws || game.ws.readyState !== WebSocket.OPEN) return;
  game.ws.send(JSON.stringify({ type: "rematch" }));
}

// ── Rendering ──
function buildLobby() {
  app.innerHTML = `
    <div class="rv-head">
      <h1>⚫ Reversi ⚪</h1>
      <p>Free 2-player online Othello. Create a room, share the code, and play.</p>
    </div>
    <div class="rv-wrap">
      <div class="card rv-lobby">
        <div class="rv-field">
          <label for="rvName">Display name</label>
          <input class="rv-input" id="rvName" type="text" maxlength="32" autocomplete="off"
                 spellcheck="false" placeholder="Your name" value="${esc(defaultName())}" />
        </div>
        <div class="rv-actions">
          <button class="btn primary big" id="rvCreate">Create room</button>
        </div>
        <div class="rv-sep">— or join a room —</div>
        <div class="rv-field">
          <label for="rvCode">Room code</label>
          <div class="rv-joinrow">
            <input class="rv-input code" id="rvCode" type="text" maxlength="4" autocomplete="off"
                   spellcheck="false" placeholder="ABCD" />
            <button class="btn" id="rvJoin">Join</button>
          </div>
        </div>
      </div>
    </div>`;
  game._codeInput = $("rvCode");
  $("rvCreate").addEventListener("click", createRoom);
  $("rvJoin").addEventListener("click", joinRoom);
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
  const me = game.players.find((p) => p.disc === game.you);
  const opp = game.players.find((p) => p.disc !== game.you);

  let statusText = "", statusClass = "rv-status wait";
  if (!twoPlayers) {
    statusText = "Waiting for opponent…";
  } else if (game.winner === "draw") {
    statusText = "It's a draw.";
    statusClass = "rv-status draw";
  } else if (game.winner) {
    if (game.winner === game.you) { statusText = "You win! 🎉"; statusClass = "rv-status win"; }
    else { statusText = "You lose."; statusClass = "rv-status lose"; }
  } else if (game.turn === game.you) {
    statusText = "Your turn";
    statusClass = "rv-status yours";
  } else {
    statusText = `${esc(opp ? opp.name : "Opponent")}'s turn`;
    statusClass = "rv-status theirs";
  }

  const locked = !twoPlayers || game.winner || game.turn !== game.you;

  let cells = "";
  for (let r = 0; r < 8; r++) {
    for (let c = 0; c < 8; c++) {
      const v = game.board[r] && game.board[r][c] ? game.board[r][c] : "";
      const playable = !locked && isLegal(r, c);
      const disc = v
        ? `<span class="rv-disc ${v === "B" ? "b" : "w"} rv-disc-in"></span>`
        : (playable ? `<span class="rv-hint"></span>` : "");
      cells += `<div class="rv-cell${playable ? " playable" : ""}" data-row="${r}" data-col="${c}">${disc}</div>`;
    }
  }

  const youDisc = game.you === "B"
    ? `<span class="rv-disc b inline"></span> Black`
    : (game.you === "W" ? `<span class="rv-disc w inline"></span> White` : "—");

  const scoreBoard = `
    <div class="rv-scores">
      <div class="rv-score${game.turn === "B" && !game.winner && twoPlayers ? " active" : ""}">
        <span class="rv-disc b inline"></span>
        <span class="rv-scorenum">${num(game.scores.B)}</span>
        <span class="rv-scorelbl">Black</span>
      </div>
      <div class="rv-scoresep">vs</div>
      <div class="rv-score${game.turn === "W" && !game.winner && twoPlayers ? " active" : ""}">
        <span class="rv-disc w inline"></span>
        <span class="rv-scorenum">${num(game.scores.W)}</span>
        <span class="rv-scorelbl">White</span>
      </div>
    </div>`;

  const pchip = (p) => {
    if (!p) return `<div class="rv-pchip"><span class="rv-disc ${game.you === "B" ? "w" : "b"} inline"></span><span class="rv-pname muted">waiting…</span></div>`;
    const active = !game.winner && twoPlayers && game.turn === p.disc;
    const isMe = p.disc === game.you;
    return `<div class="rv-pchip${active ? " active" : ""}${isMe ? " me" : ""}">
      <span class="rv-disc ${p.disc === "B" ? "b" : "w"} inline"></span>
      <span class="rv-pname">${esc(p.name)}</span>
      ${isMe ? `<span class="rv-pyou">(you)</span>` : ""}
      <span class="rv-dot${p.connected ? " on" : ""}" title="${p.connected ? "connected" : "offline"}"></span>
    </div>`;
  };

  const banner = game.winner
    ? `<div class="rv-banner rv-rematch"><button class="btn primary" id="rvRematch">Rematch</button></div>`
    : "";

  app.innerHTML = `
    <div class="rv-head">
      <h1>⚫ Reversi ⚪</h1>
    </div>
    <div class="rv-wrap wide">
      <div class="card" style="display:flex;flex-direction:column;gap:var(--s-4)">
        <div class="rv-codebox">
          <div class="rv-codelabel">Room code</div>
          <div class="rv-code" id="rvCodeBox" title="Click to copy">${esc(game.code || "····")}</div>
          <span class="rv-copyhint">Share this code with a friend to play.</span>
        </div>
        <div class="rv-players">${pchip(me)}${pchip(opp)}</div>
        <div class="rv-youare">You are ${youDisc}</div>
        ${scoreBoard}
        <div class="${statusClass}" id="rvStatus">${statusText}</div>
        <div class="rv-board${locked ? " locked" : ""}" id="rvBoard">${cells}</div>
        ${banner}
      </div>
    </div>`;

  const board = $("rvBoard");
  board.addEventListener("click", (e) => {
    const cellEl = e.target.closest(".rv-cell");
    if (!cellEl) return;
    const r = Number(cellEl.dataset.row);
    const c = Number(cellEl.dataset.col);
    if (Number.isInteger(r) && Number.isInteger(c)) sendMove(r, c);
  });
  const codeBox = $("rvCodeBox");
  if (codeBox) codeBox.addEventListener("click", copyCode);
  const rematch = $("rvRematch");
  if (rematch) rematch.addEventListener("click", sendRematch);
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
