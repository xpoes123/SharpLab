// SharpLab HQ — Battleship. Free 2-player online game over WebSockets.
// Web-native multiplayer: rooms are created straight from the browser (no
// Discord token), players share a 4-letter code, and moves flow over a socket
// to /ws/battleship/{room_id}. No coins involved.

const app = document.getElementById("app");
const navRight = document.getElementById("navRight");

// ── Helpers (copied verbatim from tictactoe.js / casino.js) ──
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

// ── Constants ──
const N = 10;
// Must match the server's SHIP_DEFS order.
const FLEET = [
  ["Carrier", 5],
  ["Battleship", 4],
  ["Cruiser", 3],
  ["Submarine", 3],
  ["Destroyer", 2],
];

// ── Game state ──
const $ = (id) => document.getElementById(id);
const game = {
  ws: null,
  roomId: null,
  code: null,
  name: "",
  you: null,          // "P1" | "P2"
  phase: "lobby",
  turn: "P1",
  winner: null,
  youPlaced: false,
  players: [],
  yourBoard: emptyGrid(),
  yourSunk: [],
  enemyBoard: emptyGrid(),
  enemySunk: [],
  enemyShipsLeft: FLEET.length,
  lastSunk: null,
  // Local placement scratch (before Ready is sent).
  place: null,
};

function emptyGrid() {
  return Array.from({ length: N }, () => Array(N).fill(""));
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
  const res = await postJSON("/api/v1/battleship/rooms", { name });
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
  const res = await postJSON(`/api/v1/battleship/rooms/${code}/join`, { name });
  if (res._status === 404) return toast("❌ No room with that code.");
  if (res._status === 409) return toast("❌ That room is full.");
  if (res._status || !res.room_id) return toast("❌ Couldn't join the room.");
  game.name = name;
  game.code = code;
  connect(res.room_id);
}

function readName() {
  const el = $("bsName");
  const name = ((el && el.value) || "").trim();
  if (!name) { if (el) el.focus(); toast("Enter a display name first."); return null; }
  return name.slice(0, 32);
}

// ── WebSocket ──
function connect(roomId) {
  game.roomId = roomId;
  try {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws/battleship/${roomId}`);
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
        const s = $("bsStatus");
        if (s) { s.textContent = "Disconnected."; s.className = "bs-status wait"; }
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
    const prevPhase = game.phase;
    const prevSunk = game.lastSunk;
    game.code = msg.code || game.code;
    game.phase = msg.phase || "lobby";
    game.turn = msg.turn;
    game.winner = msg.winner;
    game.you = msg.you;
    game.youPlaced = !!msg.you_placed;
    game.players = msg.players || [];
    game.yourBoard = grid10(msg.your_board);
    game.yourSunk = msg.your_sunk || [];
    game.enemyBoard = grid10(msg.enemy_board);
    game.enemySunk = msg.enemy_sunk || [];
    game.enemyShipsLeft = msg.enemy_ships_left != null ? msg.enemy_ships_left : FLEET.length;
    game.lastSunk = msg.sunk || null;
    // Drop local placement scratch once the server has our fleet.
    if (game.youPlaced) game.place = null;
    if (game.lastSunk && game.lastSunk !== prevSunk) toast("💥 Sunk their " + game.lastSunk + "!");
    renderGame();
    if (prevPhase !== "battle" && game.phase === "battle") toast("⚓ Battle stations — fire away!");
  }
}

function grid10(b) {
  if (!Array.isArray(b) || b.length !== N) return emptyGrid();
  return b.map((row) => (Array.isArray(row) && row.length === N ? row.slice() : Array(N).fill("")));
}

function sendFire(row, col) {
  if (!game.ws || game.ws.readyState !== WebSocket.OPEN) return;
  if (game.phase !== "battle" || game.winner) return;
  if (game.you !== game.turn) return;
  if (game.enemyBoard[row][col] !== "") return;
  game.ws.send(JSON.stringify({ type: "fire", row, col }));
}

function sendRematch() {
  if (!game.ws || game.ws.readyState !== WebSocket.OPEN) return;
  game.ws.send(JSON.stringify({ type: "rematch" }));
}

function sendAutoplace() {
  if (!game.ws || game.ws.readyState !== WebSocket.OPEN) return;
  game.ws.send(JSON.stringify({ type: "autoplace" }));
}

function sendPlace() {
  if (!game.ws || game.ws.readyState !== WebSocket.OPEN) return;
  if (!game.place) return;
  const ships = game.place.ships
    .filter((s) => s.placed)
    .map((s) => ({ row: s.row, col: s.col, len: s.len, dir: s.dir }));
  if (ships.length !== FLEET.length) return toast("Place all 5 ships first.");
  game.ws.send(JSON.stringify({ type: "place", ships }));
}

// ── Rendering: lobby ──
function buildLobby() {
  app.innerHTML = `
    <div class="bs-head">
      <h1>🚢 Battleship</h1>
      <p>Free 2-player online game. Create a room, share the code, and play.</p>
    </div>
    <div class="bs-wrap">
      <div class="card bs-lobby">
        <div class="bs-field">
          <label for="bsName">Display name</label>
          <input class="bs-input" id="bsName" type="text" maxlength="32" autocomplete="off"
                 spellcheck="false" placeholder="Your name" value="${esc(defaultName())}" />
        </div>
        <div class="bs-actions">
          <button class="btn primary big" id="bsCreate">Create room</button>
        </div>
        <div class="bs-sep">— or join a room —</div>
        <div class="bs-field">
          <label for="bsCode">Room code</label>
          <div class="bs-joinrow">
            <input class="bs-input code" id="bsCode" type="text" maxlength="4" autocomplete="off"
                   spellcheck="false" placeholder="ABCD" />
            <button class="btn" id="bsJoin">Join</button>
          </div>
        </div>
      </div>
    </div>`;
  game._codeInput = $("bsCode");
  $("bsCreate").addEventListener("click", createRoom);
  $("bsJoin").addEventListener("click", joinRoom);
  game._codeInput.addEventListener("keydown", (e) => { if (e.key === "Enter") joinRoom(); });
  game._codeInput.addEventListener("input", (e) => {
    e.target.value = e.target.value.toUpperCase().replace(/[^A-Z]/g, "").slice(0, 4);
  });
}

function defaultName() {
  if (state.me && state.me.user && state.me.user.username) return state.me.user.username;
  return "";
}

// ── Shared top strip (code + players + status) ──
function topStrip(statusText, statusClass) {
  return `
    <div class="bs-codebox">
      <div class="bs-codelabel">Room code</div>
      <div class="bs-code" id="bsCodeBox" title="Click to copy">${esc(game.code || "····")}</div>
      <span class="bs-copyhint">Share this code with a friend to play.</span>
    </div>
    <div class="bs-players">${playerChips()}</div>
    <div class="${statusClass}" id="bsStatus">${statusText}</div>`;
}

function playerChips() {
  const me = game.players.find((p) => p.symbol === game.you);
  const opp = game.players.find((p) => p.symbol !== game.you);
  return [me, opp].map((p) => {
    if (!p) return `<div class="bs-pchip"><span class="bs-pname muted">waiting…</span></div>`;
    const active = game.phase === "battle" && !game.winner && game.turn === p.symbol;
    const isMe = p.symbol === game.you;
    let tag = "";
    if (game.phase === "battle" || game.phase === "over") tag = "";
    else tag = p.placed ? `<span class="bs-pready">✓ ready</span>` : `<span class="bs-pwait">placing…</span>`;
    return `<div class="bs-pchip${active ? " active" : ""}">
      <span class="bs-pname">${esc(p.name)}</span>
      ${isMe ? `<span class="bs-pyou">(you)</span>` : ""}
      ${tag}
      <span class="bs-dot${p.connected ? " on" : ""}" title="${p.connected ? "connected" : "offline"}"></span>
    </div>`;
  }).join("");
}

// ── Placement scratch ──
function initPlace() {
  game.place = {
    ships: FLEET.map(([name, len]) => ({ name, len, placed: false, row: 0, col: 0, dir: "h" })),
    selected: 0,
    dir: "h",
  };
}

function localGrid() {
  // 10×10 of ship-index (or null) from locally placed ships.
  const g = Array.from({ length: N }, () => Array(N).fill(null));
  game.place.ships.forEach((s, idx) => {
    if (!s.placed) return;
    cellsFor(s.row, s.col, s.len, s.dir).forEach(([r, c]) => { g[r][c] = idx; });
  });
  return g;
}

function cellsFor(row, col, len, dir) {
  const out = [];
  for (let i = 0; i < len; i++) {
    const r = row + (dir === "v" ? i : 0);
    const c = col + (dir === "h" ? i : 0);
    out.push([r, c]);
  }
  return out;
}

function placementOk(cells, ignoreIdx) {
  const g = localGrid();
  for (const [r, c] of cells) {
    if (r < 0 || r >= N || c < 0 || c >= N) return false;
    if (g[r][c] != null && g[r][c] !== ignoreIdx) return false;
  }
  return true;
}

// ── Rendering: placement ──
function renderPlacement() {
  if (!game.place) initPlace();
  const allPlaced = game.place.ships.every((s) => s.placed);

  const tray = game.place.ships.map((s, idx) => {
    const pips = Array(s.len).fill(`<span class="pip"></span>`).join("");
    const cls = ["bs-ship"];
    if (idx === game.place.selected) cls.push("selected");
    if (s.placed) cls.push("placed");
    return `<div class="${cls.join(" ")}" data-ship="${idx}">
      <span class="pips">${pips}</span>
      <span class="bs-shipname">${esc(s.name)}</span>
      <span class="bs-shiplen">${s.len}</span>
    </div>`;
  }).join("");

  app.innerHTML = `
    <div class="bs-head"><h1>🚢 Battleship</h1></div>
    <div class="bs-wrap">
      <div class="card" style="display:flex;flex-direction:column;gap:var(--s-4)">
        ${topStrip("Place your fleet, then hit Ready.", "bs-status wait")}
        <div class="bs-place">
          <div class="bs-tray">${tray}</div>
          <div class="bs-placehint" id="bsHint">Pick a ship, hover the grid, click to drop it.</div>
          <div class="bs-grid" id="bsPlaceGrid"></div>
          <div class="bs-placebtns">
            <button class="btn" id="bsRotate">Rotate (${game.place.dir === "h" ? "horizontal" : "vertical"})</button>
            <button class="btn" id="bsAuto">Auto-place</button>
            <button class="btn primary" id="bsReady" ${allPlaced ? "" : "disabled"}>Ready</button>
          </div>
        </div>
      </div>
    </div>`;

  drawPlacementGrid();
  wireCommon();
  $("bsRotate").addEventListener("click", () => {
    game.place.dir = game.place.dir === "h" ? "v" : "h";
    renderPlacement();
  });
  $("bsAuto").addEventListener("click", sendAutoplace);
  $("bsReady").addEventListener("click", sendPlace);
  app.querySelectorAll(".bs-ship").forEach((el) => {
    el.addEventListener("click", () => {
      const idx = Number(el.dataset.ship);
      const s = game.place.ships[idx];
      if (s.placed) { s.placed = false; }  // click a placed ship to pick it back up
      game.place.selected = idx;
      renderPlacement();
    });
  });
}

function drawPlacementGrid() {
  const grid = $("bsPlaceGrid");
  const g = localGrid();
  let html = "";
  for (let r = 0; r < N; r++) {
    for (let c = 0; c < N; c++) {
      const ship = g[r][c] != null;
      html += `<div class="bs-cell${ship ? " ship" : ""}" data-r="${r}" data-c="${c}"></div>`;
    }
  }
  grid.innerHTML = html;

  const sel = game.place.ships[game.place.selected];
  const canPlaceSel = sel && !sel.placed;

  grid.addEventListener("mousemove", (e) => {
    const cell = e.target.closest(".bs-cell");
    clearPreview(grid);
    if (!cell || !canPlaceSel) return;
    const r = Number(cell.dataset.r), c = Number(cell.dataset.c);
    const cells = cellsFor(r, c, sel.len, game.place.dir);
    const ok = placementOk(cells, game.place.selected);
    cells.forEach(([rr, cc]) => {
      if (rr < 0 || rr >= N || cc < 0 || cc >= N) return;
      const el = grid.querySelector(`.bs-cell[data-r="${rr}"][data-c="${cc}"]`);
      if (el) el.classList.add(ok ? "preview-ok" : "preview-bad");
    });
  });
  grid.addEventListener("mouseleave", () => clearPreview(grid));
  grid.addEventListener("click", (e) => {
    const cell = e.target.closest(".bs-cell");
    if (!cell || !canPlaceSel) return;
    const r = Number(cell.dataset.r), c = Number(cell.dataset.c);
    const cells = cellsFor(r, c, sel.len, game.place.dir);
    if (!placementOk(cells, game.place.selected)) return toast("That doesn't fit there.");
    sel.row = r; sel.col = c; sel.dir = game.place.dir; sel.placed = true;
    // Advance to the next unplaced ship.
    const next = game.place.ships.findIndex((s) => !s.placed);
    if (next >= 0) game.place.selected = next;
    renderPlacement();
  });
}

function clearPreview(grid) {
  grid.querySelectorAll(".preview-ok, .preview-bad").forEach((el) => {
    el.classList.remove("preview-ok", "preview-bad");
  });
}

// ── Rendering: waiting (placed, opponent still placing) ──
function renderWaiting() {
  app.innerHTML = `
    <div class="bs-head"><h1>🚢 Battleship</h1></div>
    <div class="bs-wrap">
      <div class="card" style="display:flex;flex-direction:column;gap:var(--s-4)">
        ${topStrip("Fleet locked in — waiting for your opponent…", "bs-status wait")}
        <div class="bs-boardwrap" style="margin:0 auto">
          <div class="bs-boardtitle">Your fleet</div>
          ${fleetGridHTML()}
        </div>
      </div>
    </div>`;
  wireCommon();
}

// ── Rendering: battle / over ──
function renderBattle() {
  const over = game.phase === "over" || !!game.winner;
  let statusText, statusClass;
  if (over) {
    if (game.winner === game.you) { statusText = "You win! 🎉"; statusClass = "bs-status win"; }
    else { statusText = "You lose."; statusClass = "bs-status lose"; }
  } else if (game.turn === game.you) {
    statusText = "Your turn — fire!"; statusClass = "bs-status yours";
  } else {
    const opp = game.players.find((p) => p.symbol !== game.you);
    statusText = `${esc(opp ? opp.name : "Opponent")}'s turn`; statusClass = "bs-status theirs";
  }

  const banner = over
    ? `<div class="bs-banner bs-rematch"><button class="btn primary" id="bsRematch">Rematch</button></div>`
    : "";

  app.innerHTML = `
    <div class="bs-head"><h1>🚢 Battleship</h1></div>
    <div class="bs-wrap wide">
      <div class="card" style="display:flex;flex-direction:column;gap:var(--s-4)">
        ${topStrip(statusText, statusClass)}
        <div class="bs-boards">
          <div class="bs-boardwrap">
            <div class="bs-boardtitle">Enemy waters · <span class="cnt">${game.enemyShipsLeft}</span> ships left</div>
            ${enemyGridHTML(over)}
          </div>
          <div class="bs-boardwrap">
            <div class="bs-boardtitle">Your fleet</div>
            ${fleetGridHTML()}
          </div>
        </div>
        ${banner}
      </div>
    </div>`;

  wireCommon();
  const enemy = $("bsEnemyGrid");
  if (enemy) enemy.addEventListener("click", (e) => {
    const cell = e.target.closest(".bs-cell");
    if (!cell || cell.dataset.r === undefined) return;
    sendFire(Number(cell.dataset.r), Number(cell.dataset.c));
  });
  const rematch = $("bsRematch");
  if (rematch) rematch.addEventListener("click", sendRematch);
}

function sunkSet(cells) {
  const s = new Set();
  (cells || []).forEach(([r, c]) => s.add(r + "," + c));
  return s;
}

function fleetGridHTML() {
  const sunk = sunkSet(game.yourSunk);
  let html = `<div class="bs-grid" id="bsFleetGrid">`;
  for (let r = 0; r < N; r++) {
    for (let c = 0; c < N; c++) {
      const v = game.yourBoard[r][c];
      const isSunk = sunk.has(r + "," + c);
      let cls = "bs-cell", inner = "";
      if (v === "hit") { cls += isSunk ? " sunk" : " hit"; inner = `<span class="pin">✕</span>`; }
      else if (v === "miss") { cls += " miss"; inner = `<span class="pin">•</span>`; }
      else if (v === "ship") { cls += " ship"; }
      html += `<div class="${cls}">${inner}</div>`;
    }
  }
  return html + `</div>`;
}

function enemyGridHTML(revealOver) {
  const myTurn = game.phase === "battle" && !game.winner && game.turn === game.you;
  const sunk = sunkSet(game.enemySunk);
  let html = `<div class="bs-grid${myTurn ? " firable" : ""}" id="bsEnemyGrid">`;
  for (let r = 0; r < N; r++) {
    for (let c = 0; c < N; c++) {
      const v = game.enemyBoard[r][c];
      const isSunk = sunk.has(r + "," + c);
      let cls = "bs-cell", inner = "";
      if (v === "hit") { cls += isSunk ? " sunk" : " hit"; inner = `<span class="pin">✕</span>`; }
      else if (v === "miss") { cls += " miss"; inner = `<span class="pin">•</span>`; }
      else if (myTurn) { cls += " fire"; }
      html += `<div class="${cls}" data-r="${r}" data-c="${c}">${inner}</div>`;
    }
  }
  return html + `</div>`;
}

function wireCommon() {
  const codeBox = $("bsCodeBox");
  if (codeBox) codeBox.addEventListener("click", copyCode);
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

// ── Router ──
function renderGame() {
  if (game.phase === "battle" || game.phase === "over") return renderBattle();
  // lobby / placement
  if (game.youPlaced) return renderWaiting();
  return renderPlacement();
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
