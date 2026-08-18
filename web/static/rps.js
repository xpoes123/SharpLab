// SharpLab HQ — Rock Paper Scissors. Free 2-player online game over WebSockets.
// Web-native multiplayer: rooms are created straight from the browser (no Discord
// token), players share a 4-letter code, and simultaneous throws flow over a
// socket to /ws/rps/{room_id}. Best-of-5 (first to 3). No coins involved.

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

// ── Choices ──
const EMOJI = { rock: "🪨", paper: "📄", scissors: "✂️" };
const LABEL = { rock: "Rock", paper: "Paper", scissors: "Scissors" };
const CHOICES = ["rock", "paper", "scissors"];

// ── Game state ──
const $ = (id) => document.getElementById(id);
const game = {
  ws: null,
  roomId: null,
  code: null,
  name: "",
  you: null,        // "A" | "B"
  scores: { me: 0, opp: 0 },
  round: 1,
  revealed: false,
  yourChoice: null,
  oppChoice: null,
  oppThrown: false,
  lastResult: null, // null | "win" | "lose" | "tie"
  matchWinner: null, // null | "you" | "opp"
  players: [],
  _beat: false,      // brief pre-reveal beat in flight
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
  const res = await postJSON("/api/v1/rps/rooms", { name });
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
  const res = await postJSON(`/api/v1/rps/rooms/${code}/join`, { name });
  if (res._status === 404) return toast("❌ No room with that code.");
  if (res._status === 409) return toast("❌ That room is full.");
  if (res._status || !res.room_id) return toast("❌ Couldn't join the room.");
  game.name = name;
  game.code = code;
  connect(res.room_id);
}

function readName() {
  const el = $("rpsName");
  const name = ((el && el.value) || "").trim();
  if (!name) { if (el) el.focus(); toast("Enter a display name first."); return null; }
  return name.slice(0, 32);
}

// ── WebSocket ──
function connect(roomId) {
  game.roomId = roomId;
  try {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws/rps/${roomId}`);
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
        const s = $("rpsStatus");
        if (s) { s.textContent = "Disconnected."; s.className = "rps-status wait"; }
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
  if (msg.type !== "state") return;

  // Detect the moment the round resolves so we can play a short reveal beat.
  const wasWaiting = game.yourChoice != null && !game.revealed;
  const nowRevealed = !!msg.revealed;
  const justRevealed = nowRevealed && !game.revealed && wasWaiting;

  game.code = msg.code || game.code;
  game.round = msg.round || 1;
  game.revealed = nowRevealed;
  game.scores = msg.scores || { me: 0, opp: 0 };
  game.yourChoice = msg.your_choice || null;
  game.oppChoice = msg.opp_choice || null;
  game.oppThrown = !!msg.opp_thrown;
  game.lastResult = msg.last_result || null;
  game.matchWinner = msg.match_winner || null;
  game.players = msg.players || [];
  game.you = msg.you;

  if (justRevealed && !REDUCE) {
    // Beat: hold both hands hidden ("shoot!") for a moment, then reveal.
    game._beat = true;
    renderGame();
    setTimeout(() => { game._beat = false; renderGame(); }, 650);
  } else {
    game._beat = false;
    renderGame();
  }
}

function sendThrow(choice) {
  if (!game.ws || game.ws.readyState !== WebSocket.OPEN) return;
  if (game.matchWinner) return;
  if (game.players.length < 2) return toast("Waiting for an opponent to join.");
  // Can throw when starting a fresh round, or when the last round is revealed
  // (which begins the next round). Block while waiting on the opponent.
  if (game.yourChoice != null && !game.revealed) return;
  game.ws.send(JSON.stringify({ type: "throw", choice }));
}

function sendRematch() {
  if (!game.ws || game.ws.readyState !== WebSocket.OPEN) return;
  game.ws.send(JSON.stringify({ type: "rematch" }));
}

// ── Rendering ──
function buildLobby() {
  app.innerHTML = `
    <div class="rps-head">
      <h1>🪨 Rock Paper Scissors ✂️</h1>
      <p>Free 2-player online game — best of 5. Create a room, share the code, and play.</p>
    </div>
    <div class="rps-wrap">
      <div class="card rps-lobby">
        <div class="rps-field">
          <label for="rpsName">Display name</label>
          <input class="rps-input" id="rpsName" type="text" maxlength="32" autocomplete="off"
                 spellcheck="false" placeholder="Your name" value="${esc(defaultName())}" />
        </div>
        <div class="rps-actions">
          <button class="btn primary big" id="rpsCreate">Create room</button>
        </div>
        <div class="rps-sep">— or join a room —</div>
        <div class="rps-field">
          <label for="rpsCode">Room code</label>
          <div class="rps-joinrow">
            <input class="rps-input code" id="rpsCode" type="text" maxlength="4" autocomplete="off"
                   spellcheck="false" placeholder="ABCD" />
            <button class="btn" id="rpsJoin">Join</button>
          </div>
        </div>
      </div>
    </div>`;
  game._codeInput = $("rpsCode");
  $("rpsCreate").addEventListener("click", createRoom);
  $("rpsJoin").addEventListener("click", joinRoom);
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
  const me = game.players.find((p) => p.slot === game.you);
  const opp = game.players.find((p) => p.slot !== game.you);

  const beating = game._beat;            // in the reveal beat → hide hands
  const showReveal = game.revealed && !beating;
  const waiting = game.yourChoice != null && !game.revealed; // thrown, awaiting opp

  // Status line.
  let statusText = "", statusClass = "rps-status wait";
  if (!twoPlayers) {
    statusText = "Waiting for opponent…";
  } else if (game.matchWinner === "you") {
    statusText = "You win the match! 🎉"; statusClass = "rps-status win";
  } else if (game.matchWinner === "opp") {
    statusText = "You lost the match."; statusClass = "rps-status lose";
  } else if (beating) {
    statusText = "Rock… Paper… Scissors… Shoot!"; statusClass = "rps-status shoot";
  } else if (showReveal) {
    if (game.lastResult === "win") { statusText = "You win the round! 🎉"; statusClass = "rps-status win"; }
    else if (game.lastResult === "lose") { statusText = "You lose the round."; statusClass = "rps-status lose"; }
    else { statusText = "Tie — throw again."; statusClass = "rps-status draw"; }
  } else if (waiting) {
    statusText = "Waiting for opponent…"; statusClass = "rps-status wait";
  } else {
    statusText = "Make your throw"; statusClass = "rps-status yours";
  }

  // Hands: your side + opponent side.
  const yourHand = handFace(game.yourChoice, showReveal || waiting, false);
  let oppReveal = null;
  if (showReveal) oppReveal = game.oppChoice;
  const oppHand = handFace(oppReveal, showReveal, !showReveal && game.oppThrown);

  const scoreMe = me ? me.score : game.scores.me;
  const scoreOpp = opp ? opp.score : game.scores.opp;

  const pchip = (p, isMe) => {
    if (!p) return `<div class="rps-pchip">
      <span class="rps-pname muted">waiting…</span>
      <span class="rps-score">0</span>
    </div>`;
    const active = !game.matchWinner && twoPlayers;
    return `<div class="rps-pchip${active ? " active" : ""}${isMe ? " me" : ""}">
      <span class="rps-pname">${esc(p.name)}</span>
      ${isMe ? `<span class="rps-pyou">(you)</span>` : ""}
      <span class="rps-dot${p.connected ? " on" : ""}" title="${p.connected ? "connected" : "offline"}"></span>
      <span class="rps-score">${p.score}</span>
    </div>`;
  };

  const buttonsLocked = !twoPlayers || game.matchWinner || waiting || beating;
  const buttons = CHOICES.map((c) => {
    const chosen = game.yourChoice === c && (waiting || showReveal);
    return `<button class="rps-choice${chosen ? " chosen" : ""}" data-choice="${c}"
      ${buttonsLocked ? "disabled" : ""} title="${LABEL[c]}">
        <span class="rps-choice-emoji">${EMOJI[c]}</span>
        <span class="rps-choice-label">${LABEL[c]}</span>
      </button>`;
  }).join("");

  const banner = game.matchWinner
    ? `<div class="rps-banner"><button class="btn primary" id="rpsRematch">Rematch</button></div>`
    : "";

  app.innerHTML = `
    <div class="rps-head">
      <h1>🪨 Rock Paper Scissors ✂️</h1>
    </div>
    <div class="rps-wrap">
      <div class="card" style="display:flex;flex-direction:column;gap:var(--s-4)">
        <div class="rps-codebox">
          <div class="rps-codelabel">Room code</div>
          <div class="rps-code" id="rpsCodeBox" title="Click to copy">${esc(game.code || "····")}</div>
          <span class="rps-copyhint">Share this code with a friend to play. Best of 5.</span>
        </div>
        <div class="rps-players">${pchip(me, true)}<span class="rps-vs">vs</span>${pchip(opp, false)}</div>
        <div class="rps-arena">
          <div class="rps-side">
            <div class="rps-hand${showReveal ? " reveal" : ""}">${yourHand}</div>
            <div class="rps-side-label">You</div>
          </div>
          <div class="rps-arena-vs">${showReveal ? resultGlyph(game.lastResult) : "⚔️"}</div>
          <div class="rps-side">
            <div class="rps-hand${showReveal ? " reveal" : ""}">${oppHand}</div>
            <div class="rps-side-label">${esc(opp ? opp.name : "Opponent")}</div>
          </div>
        </div>
        <div class="${statusClass}" id="rpsStatus">${statusText}</div>
        <div class="rps-choices">${buttons}</div>
        ${banner}
      </div>
    </div>`;

  const choicesEl = app.querySelector(".rps-choices");
  choicesEl.addEventListener("click", (e) => {
    const btn = e.target.closest(".rps-choice");
    if (!btn || btn.disabled) return;
    sendThrow(btn.dataset.choice);
  });
  const codeBox = $("rpsCodeBox");
  if (codeBox) codeBox.addEventListener("click", copyCode);
  const rematch = $("rpsRematch");
  if (rematch) rematch.addEventListener("click", sendRematch);
}

// A hand face: shows the emoji when `reveal` is true, "✋ locked in" when the
// player has thrown but it's still hidden, and a placeholder otherwise.
function handFace(choice, reveal, thrownHidden) {
  if (reveal && choice) return `<span class="rps-hand-in">${EMOJI[choice]}</span>`;
  if (thrownHidden || (reveal && !choice)) return `<span class="rps-hand-hidden">✊</span>`;
  return `<span class="rps-hand-empty">·</span>`;
}

function resultGlyph(r) {
  if (r === "win") return "✅";
  if (r === "lose") return "❌";
  return "🤝";
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
