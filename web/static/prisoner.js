// SharpLab HQ — Iterated Prisoner's Dilemma. Free 2-player online game over WebSockets.
// Web-native multiplayer: rooms are created straight from the browser (no Discord
// token), players share a 4-letter code, and simultaneous secret moves flow over a
// socket to /ws/prisoner/{room_id}. Iterated over 10 rounds — highest total wins.
// No coins involved.

const app = document.getElementById("app");
const navRight = document.getElementById("navRight");

// ── Helpers (copied verbatim from rps.js / casino.js) ──
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

// ── Moves ──
const EMOJI = { cooperate: "🤝", defect: "🔪" };
const LABEL = { cooperate: "Cooperate", defect: "Defect" };
const MOVES = ["cooperate", "defect"];

// ── Game state ──
const $ = (id) => document.getElementById(id);
const game = {
  ws: null,
  roomId: null,
  code: null,
  name: "",
  you: null,        // "A" | "B"
  totals: { me: 0, opp: 0 },
  round: 1,
  rounds: 10,
  revealed: false,
  yourMove: null,
  oppMove: null,
  oppChosen: false,
  last: { me: null, opp: null },
  history: [],
  matchWinner: null, // null | "you" | "opp" | "tie"
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
  const res = await postJSON("/api/v1/prisoner/rooms", { name });
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
  const res = await postJSON(`/api/v1/prisoner/rooms/${code}/join`, { name });
  if (res._status === 404) return toast("❌ No room with that code.");
  if (res._status === 409) return toast("❌ That room is full.");
  if (res._status || !res.room_id) return toast("❌ Couldn't join the room.");
  game.name = name;
  game.code = code;
  connect(res.room_id);
}

function readName() {
  const el = $("pdName");
  const name = ((el && el.value) || "").trim();
  if (!name) { if (el) el.focus(); toast("Enter a display name first."); return null; }
  return name.slice(0, 32);
}

// ── WebSocket ──
function connect(roomId) {
  game.roomId = roomId;
  try {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws/prisoner/${roomId}`);
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
        const s = $("pdStatus");
        if (s) { s.textContent = "Disconnected."; s.className = "pd-status wait"; }
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

  game.code = msg.code || game.code;
  game.round = msg.round || 1;
  game.rounds = msg.rounds || 10;
  game.revealed = !!msg.revealed;
  game.totals = msg.totals || { me: 0, opp: 0 };
  game.yourMove = msg.your_move || null;
  game.oppMove = msg.opp_move || null;
  game.oppChosen = !!msg.opp_chosen;
  game.last = msg.last || { me: null, opp: null };
  game.history = Array.isArray(msg.history) ? msg.history : [];
  game.matchWinner = msg.match_winner || null;
  game.players = msg.players || [];
  game.you = msg.you;

  renderGame();
}

function sendChoice(move) {
  if (!game.ws || game.ws.readyState !== WebSocket.OPEN) return;
  if (game.matchWinner) return;
  if (game.players.length < 2) return toast("Waiting for an opponent to join.");
  // Can pick when starting a fresh round, or when the last round is revealed
  // (which begins the next round). Block while waiting on the opponent.
  if (game.yourMove != null && !game.revealed) return;
  game.ws.send(JSON.stringify({ type: "choice", move }));
}

function sendRematch() {
  if (!game.ws || game.ws.readyState !== WebSocket.OPEN) return;
  game.ws.send(JSON.stringify({ type: "rematch" }));
}

// ── Rendering ──
function buildLobby() {
  app.innerHTML = `
    <div class="pd-head">
      <h1>🤝 Prisoner's Dilemma 🔪</h1>
      <p>Free 2-player online game — 10 rounds. Create a room, share the code, and play.</p>
    </div>
    <div class="pd-wrap">
      <div class="card pd-lobby">
        <div class="pd-rules">
          Each round you both <b>secretly</b> choose to <b>Cooperate</b> or <b>Defect</b>.
          Points score by the classic matrix; after 10 rounds the higher total wins.
          <div class="pd-payoff">
            <div class="pd-payoff-cell">Both cooperate<b>+3 / +3</b></div>
            <div class="pd-payoff-cell">Both defect<b>+1 / +1</b></div>
            <div class="pd-payoff-cell">You defect, they cooperate<b>+5 / +0</b></div>
            <div class="pd-payoff-cell">You cooperate, they defect<b>+0 / +5</b></div>
          </div>
        </div>
        <div class="pd-field">
          <label for="pdName">Display name</label>
          <input class="pd-input" id="pdName" type="text" maxlength="32" autocomplete="off"
                 spellcheck="false" placeholder="Your name" value="${esc(defaultName())}" />
        </div>
        <div class="pd-actions">
          <button class="btn primary big" id="pdCreate">Create room</button>
        </div>
        <div class="pd-sep">— or join a room —</div>
        <div class="pd-field">
          <label for="pdCode">Room code</label>
          <div class="pd-joinrow">
            <input class="pd-input code" id="pdCode" type="text" maxlength="4" autocomplete="off"
                   spellcheck="false" placeholder="ABCD" />
            <button class="btn" id="pdJoin">Join</button>
          </div>
        </div>
      </div>
    </div>`;
  game._codeInput = $("pdCode");
  $("pdCreate").addEventListener("click", createRoom);
  $("pdJoin").addEventListener("click", joinRoom);
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

  const showReveal = game.revealed;
  const waiting = game.yourMove != null && !game.revealed; // picked, awaiting opp

  // Status line.
  let statusText = "", statusClass = "pd-status wait";
  if (!twoPlayers) {
    statusText = "Waiting for opponent…";
  } else if (game.matchWinner === "you") {
    statusText = "You win the match! 🎉"; statusClass = "pd-status win";
  } else if (game.matchWinner === "opp") {
    statusText = "You lost the match."; statusClass = "pd-status lose";
  } else if (game.matchWinner === "tie") {
    statusText = "Dead tie — nobody wins."; statusClass = "pd-status draw";
  } else if (showReveal) {
    const gm = game.last.me, go = game.last.opp;
    if (gm && go) {
      const verb = (m) => (m === "cooperate" ? "cooperated" : "defected");
      statusText = `You ${verb(gm)}, they ${verb(go)}.`;
      statusClass = "pd-status yours";
    } else {
      statusText = "Round scored — choose again.";
    }
  } else if (waiting) {
    statusText = "Waiting for opponent…"; statusClass = "pd-status wait";
  } else {
    statusText = "Make your choice"; statusClass = "pd-status yours";
  }

  // Faces: your side + opponent side.
  const yourMove = (showReveal || waiting) ? game.yourMove : null;
  const yourFace = faceHtml(yourMove, showReveal || waiting, false);
  const oppReveal = showReveal ? game.oppMove : null;
  const oppFace = faceHtml(oppReveal, showReveal, !showReveal && game.oppChosen);

  const lastGainMe = (showReveal && game.history.length) ? game.history[game.history.length - 1].gain_me : null;
  const lastGainOpp = (showReveal && game.history.length) ? game.history[game.history.length - 1].gain_opp : null;

  const totMe = me ? me.total : game.totals.me;
  const totOpp = opp ? opp.total : game.totals.opp;

  const pchip = (p, isMe) => {
    if (!p) return `<div class="pd-pchip">
      <span class="pd-pname muted">waiting…</span>
      <span class="pd-score">0</span>
    </div>`;
    const active = !game.matchWinner && twoPlayers;
    const lead = twoPlayers && p.total > (isMe ? totOpp : totMe);
    return `<div class="pd-pchip${active ? " active" : ""}${lead ? " lead" : ""}">
      <span class="pd-pname">${esc(p.name)}</span>
      ${isMe ? `<span class="pd-pyou">(you)</span>` : ""}
      <span class="pd-dot${p.connected ? " on" : ""}" title="${p.connected ? "connected" : "offline"}"></span>
      <span class="pd-score">${p.total}</span>
    </div>`;
  };

  const buttonsLocked = !twoPlayers || game.matchWinner || waiting;
  const buttons = MOVES.map((m) => {
    const chosen = game.yourMove === m && (waiting || showReveal);
    return `<button class="pd-choice ${m}${chosen ? " chosen" : ""}" data-move="${m}"
      ${buttonsLocked ? "disabled" : ""} title="${LABEL[m]}">
        <span class="pd-choice-emoji">${EMOJI[m]}</span>
        <span class="pd-choice-label">${LABEL[m]}</span>
      </button>`;
  }).join("");

  const banner = game.matchWinner
    ? `<div class="pd-banner"><button class="btn primary" id="pdRematch">Rematch</button></div>`
    : "";

  app.innerHTML = `
    <div class="pd-head">
      <h1>🤝 Prisoner's Dilemma 🔪</h1>
    </div>
    <div class="pd-wrap">
      <div class="card" style="display:flex;flex-direction:column;gap:var(--s-4)">
        <div class="pd-codebox">
          <div class="pd-codelabel">Room code</div>
          <div class="pd-code" id="pdCodeBox" title="Click to copy">${esc(game.code || "····")}</div>
          <span class="pd-copyhint">Share this code with a friend to play. 10 rounds.</span>
        </div>
        <div class="pd-round">Round <b>${Math.min(game.round, game.rounds)}</b> / ${game.rounds}</div>
        <div class="pd-players">${pchip(me, true)}<span class="pd-vs">vs</span>${pchip(opp, false)}</div>
        <div class="pd-arena">
          <div class="pd-side">
            <div class="pd-face${showReveal ? " reveal " + (yourMove || "") : ""}">${yourFace}</div>
            <div class="pd-side-label">You</div>
            <div class="pd-gain ${lastGainMe ? "pos" : "zero"}">${lastGainMe != null ? "+" + lastGainMe : ""}</div>
          </div>
          <div class="pd-arena-vs">${showReveal ? "⚖️" : "⋯"}</div>
          <div class="pd-side">
            <div class="pd-face${showReveal ? " reveal " + (oppReveal || "") : ""}">${oppFace}</div>
            <div class="pd-side-label">${esc(opp ? opp.name : "Opponent")}</div>
            <div class="pd-gain ${lastGainOpp ? "pos" : "zero"}">${lastGainOpp != null ? "+" + lastGainOpp : ""}</div>
          </div>
        </div>
        <div class="${statusClass}" id="pdStatus">${statusText}</div>
        <div class="pd-choices">${buttons}</div>
        ${historyStrip()}
        ${banner}
      </div>
    </div>`;

  const choicesEl = app.querySelector(".pd-choices");
  choicesEl.addEventListener("click", (e) => {
    const btn = e.target.closest(".pd-choice");
    if (!btn || btn.disabled) return;
    sendChoice(btn.dataset.move);
  });
  const codeBox = $("pdCodeBox");
  if (codeBox) codeBox.addEventListener("click", copyCode);
  const rematch = $("pdRematch");
  if (rematch) rematch.addEventListener("click", sendRematch);
}

// A face: shows the move emoji when `reveal` is true, a hidden fist when the
// player has picked but it's still secret, and a placeholder otherwise.
function faceHtml(move, reveal, chosenHidden) {
  if (reveal && move) return `<span class="pd-face-in">${EMOJI[move]}</span>`;
  if (chosenHidden || (reveal && !move)) return `<span class="pd-face-hidden">✊</span>`;
  return `<span class="pd-face-empty">·</span>`;
}

function historyStrip() {
  if (!game.history.length) return "";
  const cells = game.history.map((h, i) => `<div class="pd-hcell" title="R${i + 1}">
      <span class="pd-hn">${i + 1}</span>
      <span class="pd-hmoves">${EMOJI[h.me] || "·"}${EMOJI[h.opp] || "·"}</span>
      <span class="pd-hpts">${h.gain_me}/${h.gain_opp}</span>
    </div>`).join("");
  return `<div class="pd-history">
    <div class="pd-history-head">History — you / opp</div>
    <div class="pd-hstrip">${cells}</div>
  </div>`;
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
