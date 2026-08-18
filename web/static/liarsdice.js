// SharpLab HQ — Liar's Dice. Free 2-player online game over WebSockets.
// Web-native multiplayer: rooms are created straight from the browser (no
// Discord token), players share a 4-letter code, and bids/challenges flow over a
// socket to /ws/liarsdice/{room_id}. No coins involved. 1s (aces) are wild.

const app = document.getElementById("app");
const navRight = document.getElementById("navRight");

// ── Helpers (copied verbatim from tictactoe.js) ──
const num = (n) => (n == null ? "—" : Number(n).toLocaleString());
const esc = (s) =>
  String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function getJSON(url) {
  const r = await fetch(url, { credentials: "include" });
  if (!r.ok) return { _status: r.status };
  return r.json();
}

// ── Nav (login / logout) — mirrors tictactoe.js (reads state.balance) ──
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

// ── Toast (copied verbatim from tictactoe.js) ──
function toast(msg) {
  const t = document.createElement("div");
  t.className = "cardtoast";
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 3200);
}

// ── Coins hub (copied verbatim from tictactoe.js) ──
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
  you: null,          // seat 0 | 1
  phase: "lobby",     // lobby | bidding | reveal | over
  yourDice: [],
  diceCounts: { me: 0, opp: 0 },
  currentBid: null,   // {quantity, face} | null
  turn: null,         // "me" | "opp" | null
  lastReveal: null,
  players: [],
  winner: null,       // null | "you" | "opp"
  sel: { quantity: 1, face: 2 },
  turnKey: "",
};

// ── Dice pip rendering ──
const PIPS = {
  1: [5], 2: [1, 9], 3: [1, 5, 9], 4: [1, 3, 7, 9],
  5: [1, 3, 5, 7, 9], 6: [1, 3, 4, 6, 7, 9],
};
function die(v, cls) {
  const on = PIPS[v] || [];
  let cells = "";
  for (let i = 1; i <= 9; i++) {
    cells += `<span class="ld-pip${on.includes(i) ? " on" : ""}"></span>`;
  }
  const wild = v === 1 ? " wild" : "";
  return `<div class="ld-die${wild}${cls ? " " + cls : ""}" title="${v === 1 ? "1 — wild" : v}">${cells}</div>`;
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
  const res = await postJSON("/api/v1/liarsdice/rooms", { name });
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
  const res = await postJSON(`/api/v1/liarsdice/rooms/${code}/join`, { name });
  if (res._status === 404) return toast("❌ No room with that code.");
  if (res._status === 409) return toast("❌ That room is full.");
  if (res._status || !res.room_id) return toast("❌ Couldn't join the room.");
  game.name = name;
  game.code = code;
  connect(res.room_id);
}

function readName() {
  const el = $("ldName");
  const name = ((el && el.value) || "").trim();
  if (!name) { if (el) el.focus(); toast("Enter a display name first."); return null; }
  return name.slice(0, 32);
}

// ── WebSocket ──
function connect(roomId) {
  game.roomId = roomId;
  try {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws/liarsdice/${roomId}`);
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
        const s = $("ldStatus");
        if (s) { s.textContent = "Disconnected."; s.className = "ld-status wait"; }
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
    game.phase = msg.phase || "lobby";
    game.yourDice = Array.isArray(msg.your_dice) ? msg.your_dice : [];
    game.diceCounts = msg.dice_counts || { me: 0, opp: 0 };
    game.currentBid = msg.current_bid || null;
    game.turn = msg.turn;
    game.lastReveal = msg.last_reveal || null;
    game.you = msg.you;
    game.players = msg.players || [];
    game.winner = msg.winner;
    syncSelection();
    renderGame();
  }
}

// Reset the bid builder to the minimum legal bid whenever it becomes my turn or
// the standing bid changes — but leave the user's in-progress pick alone within
// the same turn so the steppers don't jump under them.
function syncSelection() {
  const key = `${game.phase}|${game.turn}|${game.currentBid ? game.currentBid.quantity + "-" + game.currentBid.face : "none"}`;
  if (key !== game.turnKey) {
    game.turnKey = key;
    game.sel = minLegalBid();
  }
}

function totalDice() {
  return (game.diceCounts.me || 0) + (game.diceCounts.opp || 0);
}

function minLegalBid() {
  const cur = game.currentBid;
  const total = Math.max(1, totalDice());
  if (!cur) return { quantity: 1, face: 2 };
  if (cur.face < 6) return { quantity: cur.quantity, face: cur.face + 1 };
  return { quantity: Math.min(cur.quantity + 1, total), face: 1 };
}

function isLegalBid(sel) {
  if (!sel || sel.face < 1 || sel.face > 6 || sel.quantity < 1) return false;
  if (sel.quantity > Math.max(1, totalDice())) return false;
  const cur = game.currentBid;
  if (!cur) return true;
  return sel.quantity > cur.quantity || (sel.quantity === cur.quantity && sel.face > cur.face);
}

function sendBid() {
  if (!game.ws || game.ws.readyState !== WebSocket.OPEN) return;
  if (game.phase !== "bidding" || game.turn !== "me") return;
  if (!isLegalBid(game.sel)) return toast("That bid doesn't beat the current bid.");
  game.ws.send(JSON.stringify({ type: "bid", quantity: game.sel.quantity, face: game.sel.face }));
}

function sendChallenge() {
  if (!game.ws || game.ws.readyState !== WebSocket.OPEN) return;
  if (game.phase !== "bidding" || game.turn !== "me") return;
  if (!game.currentBid) return;
  game.ws.send(JSON.stringify({ type: "challenge" }));
}

function sendRematch() {
  if (!game.ws || game.ws.readyState !== WebSocket.OPEN) return;
  game.ws.send(JSON.stringify({ type: "rematch" }));
}

// ── Rendering: lobby ──
function buildLobby() {
  app.innerHTML = `
    <div class="ld-head">
      <h1>🎲 Liar's Dice</h1>
      <p>Free 2-player bluffing game. Create a room, share the code, and call each other's bluffs. Ones are wild.</p>
    </div>
    <div class="ld-wrap">
      <div class="card ld-lobby">
        <div class="ld-field">
          <label for="ldName">Display name</label>
          <input class="ld-input" id="ldName" type="text" maxlength="32" autocomplete="off"
                 spellcheck="false" placeholder="Your name" value="${esc(defaultName())}" />
        </div>
        <div class="ld-actions">
          <button class="btn primary big" id="ldCreate">Create room</button>
        </div>
        <div class="ld-sep">— or join a room —</div>
        <div class="ld-field">
          <label for="ldCode">Room code</label>
          <div class="ld-joinrow">
            <input class="ld-input code" id="ldCode" type="text" maxlength="4" autocomplete="off"
                   spellcheck="false" placeholder="ABCD" />
            <button class="btn" id="ldJoin">Join</button>
          </div>
        </div>
      </div>
    </div>`;
  game._codeInput = $("ldCode");
  $("ldCreate").addEventListener("click", createRoom);
  $("ldJoin").addEventListener("click", joinRoom);
  game._codeInput.addEventListener("keydown", (e) => { if (e.key === "Enter") joinRoom(); });
  game._codeInput.addEventListener("input", (e) => {
    e.target.value = e.target.value.toUpperCase().replace(/[^A-Z]/g, "").slice(0, 4);
  });
}

function defaultName() {
  if (state.me && state.me.user && state.me.user.username) return state.me.user.username;
  return "";
}

// ── Rendering: game ──
function renderGame() {
  const twoPlayers = game.players.length >= 2;
  const me = game.players.find((p) => p.seat === game.you);
  const opp = game.players.find((p) => p.seat !== game.you);

  let statusText = "", statusClass = "ld-status wait";
  if (!twoPlayers) {
    statusText = "Waiting for opponent…";
  } else if (game.phase === "over") {
    if (game.winner === "you") { statusText = "You win the match! 🎉"; statusClass = "ld-status win"; }
    else { statusText = "You're out of dice — you lose."; statusClass = "ld-status lose"; }
  } else if (game.phase === "reveal") {
    statusText = "Reveal!";
    statusClass = "ld-status reveal";
  } else if (game.turn === "me") {
    statusText = "Your turn — bid or call liar";
    statusClass = "ld-status yours";
  } else {
    statusText = `${esc(opp ? opp.name : "Opponent")}'s turn…`;
    statusClass = "ld-status theirs";
  }

  const pchip = (p, isMe) => {
    if (!p) {
      return `<div class="ld-pchip"><span class="ld-pname muted">waiting…</span></div>`;
    }
    const active = twoPlayers && game.phase === "bidding" &&
      ((isMe && game.turn === "me") || (!isMe && game.turn === "opp"));
    const count = p.dice_count;
    const minis = Array.from({ length: count }, () => `<span class="ld-mini"></span>`).join("") ||
      `<span class="ld-out">out</span>`;
    return `<div class="ld-pchip${active ? " active" : ""}${isMe ? " me" : ""}">
      <span class="ld-pname">${esc(p.name)}</span>
      ${isMe ? `<span class="ld-pyou">(you)</span>` : ""}
      <span class="ld-count" title="${count} dice left">${minis}</span>
      <span class="ld-dot${p.connected ? " on" : ""}" title="${p.connected ? "connected" : "offline"}"></span>
    </div>`;
  };

  const bidLine = game.currentBid
    ? `<div class="ld-curbid">Current bid: <b>${game.currentBid.quantity}×</b> ${die(game.currentBid.face, "sm")}
        <span class="muted">(at least ${game.currentBid.quantity} ${faceWord(game.currentBid.face)})</span></div>`
    : `<div class="ld-curbid muted">No bid yet${game.turn === "me" && game.phase === "bidding" ? " — you open." : "."}</div>`;

  const yourDiceHtml = game.yourDice.length
    ? game.yourDice.map((v) => die(v, "big ld-roll-in")).join("")
    : `<span class="muted">no dice</span>`;

  const controls = (twoPlayers && game.phase === "bidding" && game.turn === "me")
    ? bidBuilder()
    : "";

  const revealPanel = (game.phase === "reveal" && game.lastReveal)
    ? revealHtml(game.lastReveal)
    : "";

  const banner = game.phase === "over"
    ? `<div class="ld-banner"><button class="btn primary" id="ldRematch">Rematch</button></div>`
    : "";

  app.innerHTML = `
    <div class="ld-head"><h1>🎲 Liar's Dice</h1></div>
    <div class="ld-wrap wide">
      <div class="card ld-table">
        <div class="ld-codebox">
          <div class="ld-codelabel">Room code</div>
          <div class="ld-code" id="ldCodeBox" title="Click to copy">${esc(game.code || "····")}</div>
          <span class="ld-copyhint">Share this code with a friend to play.</span>
        </div>
        <div class="ld-players">${pchip(me, true)}${pchip(opp, false)}</div>
        <div class="${statusClass}" id="ldStatus">${statusText}</div>
        ${bidLine}
        ${revealPanel}
        <div class="ld-yourdice-wrap">
          <div class="ld-label">Your dice</div>
          <div class="ld-yourdice">${yourDiceHtml}</div>
        </div>
        ${controls}
        ${banner}
      </div>
    </div>`;

  const codeBox = $("ldCodeBox");
  if (codeBox) codeBox.addEventListener("click", copyCode);
  const rematch = $("ldRematch");
  if (rematch) rematch.addEventListener("click", sendRematch);
  wireBuilder();
}

function faceWord(f) {
  return f === 1 ? "ones (wild)" : { 2: "twos", 3: "threes", 4: "fours", 5: "fives", 6: "sixes" }[f] || (f + "s");
}

function bidBuilder() {
  const sel = game.sel;
  const legal = isLegalBid(sel);
  const canChallenge = !!game.currentBid;
  const faces = [1, 2, 3, 4, 5, 6].map((f) => {
    const test = { quantity: sel.quantity, face: f };
    const allowed = isLegalBid(test) || f === sel.face;
    return `<button class="ld-facebtn${sel.face === f ? " sel" : ""}" data-face="${f}"
      ${allowed ? "" : "disabled"}>${die(f, "sm")}</button>`;
  }).join("");
  return `<div class="ld-builder">
    <div class="ld-builder-row">
      <div class="ld-stepper">
        <div class="ld-label">Quantity</div>
        <div class="ld-stepctrl">
          <button class="ld-step" id="ldQtyDown" aria-label="Fewer">−</button>
          <span class="ld-qty" id="ldQty">${sel.quantity}</span>
          <button class="ld-step" id="ldQtyUp" aria-label="More">+</button>
        </div>
      </div>
      <div class="ld-facepick">
        <div class="ld-label">Face</div>
        <div class="ld-faces">${faces}</div>
      </div>
    </div>
    <div class="ld-builder-actions">
      <button class="btn primary big" id="ldBid" ${legal ? "" : "disabled"}>
        Bid ${sel.quantity} × ${sel.face === 1 ? "①" : sel.face}
      </button>
      <button class="btn danger" id="ldChallenge" ${canChallenge ? "" : "disabled"}>Challenge (call liar)</button>
    </div>
  </div>`;
}

function wireBuilder() {
  const up = $("ldQtyUp"), down = $("ldQtyDown");
  const total = Math.max(1, totalDice());
  if (up) up.addEventListener("click", () => {
    game.sel.quantity = Math.min(total, game.sel.quantity + 1);
    renderGame();
  });
  if (down) down.addEventListener("click", () => {
    game.sel.quantity = Math.max(1, game.sel.quantity - 1);
    renderGame();
  });
  document.querySelectorAll(".ld-facebtn").forEach((b) => {
    b.addEventListener("click", () => {
      if (b.disabled) return;
      game.sel.face = Number(b.dataset.face);
      renderGame();
    });
  });
  const bid = $("ldBid");
  if (bid) bid.addEventListener("click", sendBid);
  const ch = $("ldChallenge");
  if (ch) ch.addEventListener("click", sendChallenge);
}

function revealHtml(r) {
  const bid = r.bid || {};
  const stood = (r.count || 0) >= (bid.quantity || 0);
  const loserName = r.loser === "me" ? "You" : "Opponent";
  const meRow = (r.all_dice.me || []).map((v) => die(v, "sm" + (matches(v, bid.face) ? " hit" : ""))).join("");
  const oppRow = (r.all_dice.opp || []).map((v) => die(v, "sm" + (matches(v, bid.face) ? " hit" : ""))).join("");
  return `<div class="ld-reveal">
    <div class="ld-reveal-title">Bid was <b>${bid.quantity}× ${faceWord(bid.face)}</b> — actual count:
      <span class="${stood ? "ld-good" : "ld-bad"}">${r.count}</span></div>
    <div class="ld-reveal-verdict ${stood ? "ld-good" : "ld-bad"}">
      ${stood ? "Bid was GOOD ✓" : "Bluff! Bid failed ✗"} — ${esc(loserName)} lose${r.loser === "me" ? "" : "s"} a die.
    </div>
    <div class="ld-reveal-hands">
      <div class="ld-hand"><div class="ld-label">Your dice</div><div class="ld-handdice">${meRow}</div></div>
      <div class="ld-hand"><div class="ld-label">Opponent's dice</div><div class="ld-handdice">${oppRow}</div></div>
    </div>
  </div>`;
}

function matches(v, face) {
  return v === face || (face !== 1 && v === 1);
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
