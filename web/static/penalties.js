// SharpLab HQ — Penalty Shootout. Free 2-player online game over WebSockets.
// Web-native multiplayer: rooms are created straight from the browser (no Discord
// token), players share a 4-letter code, and simultaneous shoot/dive picks flow
// over a socket to /ws/penalties/{room_id}. Best-of-5 (5 kicks each, sudden
// death if tied). Roles alternate every kick. No coins involved.

const app = document.getElementById("app");
const navRight = document.getElementById("navRight");

// ── Helpers (copied verbatim from casino.js / rps.js) ──
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

// ── Directions ──
const DIRS = ["left", "center", "right"];
const DIR_LABEL = { left: "Left", center: "Center", right: "Right" };

// ── Game state ──
const $ = (id) => document.getElementById(id);
const game = {
  ws: null,
  roomId: null,
  code: null,
  name: "",
  you: null,           // "A" | "B"
  scores: { me: 0, opp: 0 },
  kick: 1,
  yourRole: "shoot",   // "shoot" | "keep" for the current kick
  awaiting: "both",    // "you" | "opp" | "both"
  last: null,          // {shooter_dir,keeper_dir,goal} | null
  history: [],
  matchWinner: null,   // null | "you" | "opp"
  players: [],
  _pending: false,     // I've clicked a pick this kick (local lock)
  _reveal: null,       // {shot,dive,goal} being animated, or null
  _histLen: 0,         // history length last rendered (to detect new kicks)
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
  const res = await postJSON("/api/v1/penalties/rooms", { name });
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
  const res = await postJSON(`/api/v1/penalties/rooms/${code}/join`, { name });
  if (res._status === 404) return toast("❌ No room with that code.");
  if (res._status === 409) return toast("❌ That room is full.");
  if (res._status || !res.room_id) return toast("❌ Couldn't join the room.");
  game.name = name;
  game.code = code;
  connect(res.room_id);
}

function readName() {
  const el = $("penName");
  const name = ((el && el.value) || "").trim();
  if (!name) { if (el) el.focus(); toast("Enter a display name first."); return null; }
  return name.slice(0, 32);
}

// ── WebSocket ──
function connect(roomId) {
  game.roomId = roomId;
  try {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws/penalties/${roomId}`);
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
        const s = $("penStatus");
        if (s) { s.textContent = "Disconnected."; s.className = "pen-status wait"; }
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

  const newHist = (msg.history || []).length;
  const kickResolved = newHist > game._histLen;

  game.code = msg.code || game.code;
  game.kick = msg.kick || 1;
  game.yourRole = msg.your_role || "shoot";
  game.awaiting = msg.awaiting || "both";
  game.scores = msg.scores || { me: 0, opp: 0 };
  game.last = msg.last || null;
  game.history = msg.history || [];
  game.matchWinner = msg.match_winner || null;
  game.players = msg.players || [];
  game.you = msg.you;
  game._pending = false;  // fresh state clears local lock

  if (kickResolved && game.last && !REDUCE) {
    // Play a short reveal beat: ball flies to the shot, keeper dives, then GOAL/SAVE.
    game._reveal = { shot: game.last.shooter_dir, dive: game.last.keeper_dir, goal: game.last.goal };
    game._histLen = newHist;
    renderGame();
    // Kick off the ball/keeper transition on the next frame.
    requestAnimationFrame(() => {
      const arena = document.querySelector(".pen-goal");
      if (arena) arena.classList.add("shot");
    });
    setTimeout(() => { game._reveal = null; renderGame(); }, 1500);
  } else {
    game._reveal = null;
    game._histLen = newHist;
    renderGame();
  }
}

function sendPick(dir) {
  if (!game.ws || game.ws.readyState !== WebSocket.OPEN) return;
  if (game.matchWinner) return;
  if (game.players.length < 2) return toast("Waiting for an opponent to join.");
  if (game._reveal) return;                 // mid-animation
  if (game.awaiting === "opp") return;       // already submitted this kick
  if (game._pending) return;                 // local lock
  if (!DIRS.includes(dir)) return;
  game._pending = true;
  const type = game.yourRole === "shoot" ? "shoot" : "dive";
  game.ws.send(JSON.stringify({ type, dir }));
  renderGame();
}

function sendRematch() {
  if (!game.ws || game.ws.readyState !== WebSocket.OPEN) return;
  game.ws.send(JSON.stringify({ type: "rematch" }));
}

// ── Rendering ──
function buildLobby() {
  app.innerHTML = `
    <div class="pen-head">
      <h1>🥅 Penalty Shootout ⚽</h1>
      <p>Free 2-player online game — 5 kicks each, sudden death if tied. Create a room, share the code, and play.</p>
    </div>
    <div class="pen-wrap">
      <div class="card pen-lobby">
        <div class="pen-field">
          <label for="penName">Display name</label>
          <input class="pen-input" id="penName" type="text" maxlength="32" autocomplete="off"
                 spellcheck="false" placeholder="Your name" value="${esc(defaultName())}" />
        </div>
        <div class="pen-actions">
          <button class="btn primary big" id="penCreate">Create room</button>
        </div>
        <div class="pen-sep">— or join a room —</div>
        <div class="pen-field">
          <label for="penCode">Room code</label>
          <div class="pen-joinrow">
            <input class="pen-input code" id="penCode" type="text" maxlength="4" autocomplete="off"
                   spellcheck="false" placeholder="ABCD" />
            <button class="btn" id="penJoin">Join</button>
          </div>
        </div>
      </div>
    </div>`;
  game._codeInput = $("penCode");
  $("penCreate").addEventListener("click", createRoom);
  $("penJoin").addEventListener("click", joinRoom);
  game._codeInput.addEventListener("keydown", (e) => { if (e.key === "Enter") joinRoom(); });
  game._codeInput.addEventListener("input", (e) => {
    e.target.value = e.target.value.toUpperCase().replace(/[^A-Z]/g, "").slice(0, 4);
  });
}

function defaultName() {
  if (state.me && state.me.user && state.me.user.username) return state.me.user.username;
  return "";
}

// Build a row of 5+ result pips for one player's kicks so far.
function pips(who) {
  const mine = game.history.filter((h) => h.shooter === who);
  const slots = [];
  const count = Math.max(5, mine.length);
  for (let i = 0; i < count; i++) {
    if (i < mine.length) {
      slots.push(`<span class="pen-pip ${mine[i].goal ? "goal" : "miss"}">${mine[i].goal ? "⚽" : "❌"}</span>`);
    } else {
      slots.push(`<span class="pen-pip empty">·</span>`);
    }
  }
  return slots.join("");
}

function ballPos() {
  // During the reveal, ball sits at the shot zone; keeper at the dive zone.
  return game._reveal ? game._reveal.shot : "center";
}

function renderGame() {
  const twoPlayers = game.players.length >= 2;
  const me = game.players.find((p) => p.slot === game.you);
  const opp = game.players.find((p) => p.slot !== game.you);

  const revealing = !!game._reveal;
  const iActed = game.awaiting === "opp" || game._pending;
  const isShooter = game.yourRole === "shoot";

  // Status line.
  let statusText = "", statusClass = "pen-status wait";
  if (!twoPlayers) {
    statusText = "Waiting for opponent…";
  } else if (game.matchWinner === "you") {
    statusText = "You win the shootout! 🎉"; statusClass = "pen-status win";
  } else if (game.matchWinner === "opp") {
    statusText = "You lost the shootout."; statusClass = "pen-status lose";
  } else if (revealing) {
    statusText = game._reveal.goal ? "GOAL! ⚽" : "SAVED! 🧤";
    statusClass = game._reveal.goal ? "pen-status win" : "pen-status lose";
  } else if (iActed) {
    statusText = "Waiting for opponent…"; statusClass = "pen-status wait";
  } else if (isShooter) {
    statusText = "You're shooting — pick a corner"; statusClass = "pen-status yours";
  } else {
    statusText = "You're in goal — pick a dive"; statusClass = "pen-status yours";
  }

  const scoreMe = me ? me.goals : game.scores.me;
  const scoreOpp = opp ? opp.goals : game.scores.opp;

  const pchip = (p, isMe) => {
    if (!p) return `<div class="pen-pchip">
      <span class="pen-pname muted">waiting…</span>
      <span class="pen-goals">0</span>
    </div>`;
    const active = !game.matchWinner && twoPlayers;
    return `<div class="pen-pchip${active ? " active" : ""}${isMe ? " me" : ""}">
      <span class="pen-pname">${esc(p.name)}</span>
      ${isMe ? `<span class="pen-pyou">(you)</span>` : ""}
      <span class="pen-dot${p.connected ? " on" : ""}" title="${p.connected ? "connected" : "offline"}"></span>
      <span class="pen-goals">${p.goals}</span>
    </div>`;
  };

  // Kick line: whose kick + your role.
  let kickLine = "";
  if (twoPlayers && !game.matchWinner) {
    const roundNo = game.kick > 10 ? `Sudden death · kick ${game.kick}` : `Kick ${game.kick} of 10`;
    const roleTxt = isShooter
      ? `<b class="pen-role shoot">⚽ You shoot</b>, ${esc(opp ? opp.name : "opponent")} keeps`
      : `${esc(opp ? opp.name : "opponent")} shoots, <b class="pen-role keep">🧤 you keep</b>`;
    kickLine = `<div class="pen-kickline">${roundNo} — ${roleTxt}</div>`;
  }

  // The goal graphic with three zones + ball + keeper.
  const bp = ballPos();
  const kp = revealing ? game._reveal.dive : "center";
  const zones = DIRS.map((d) =>
    `<div class="pen-zone" data-zone="${d}"><span class="pen-zone-label">${DIR_LABEL[d]}</span></div>`
  ).join("");
  const goalGraphic = `
    <div class="pen-goal${revealing ? (game._reveal.goal ? " goal" : " save") : ""}"
         data-shot="${bp}" data-dive="${kp}">
      <div class="pen-net">${zones}</div>
      <div class="pen-keeper pos-${kp}">🧤</div>
      <div class="pen-ball pos-${bp}${revealing ? " live" : ""}">⚽</div>
      ${revealing ? `<div class="pen-verdict ${game._reveal.goal ? "goal" : "save"}">${game._reveal.goal ? "GOAL" : "SAVE"}</div>` : ""}
    </div>`;

  // Action buttons: shoot corners or dive corners. Hidden once acted / revealing.
  const locked = !twoPlayers || game.matchWinner || iActed || revealing;
  const verb = isShooter ? "Shoot" : "Dive";
  const icon = isShooter ? "⚽" : "🧤";
  const actions = DIRS.map((d) =>
    `<button class="pen-choice${isShooter ? " shoot" : " keep"}" data-dir="${d}"
       ${locked ? "disabled" : ""} title="${verb} ${DIR_LABEL[d]}">
        <span class="pen-choice-icon">${icon}</span>
        <span class="pen-choice-label">${verb} ${DIR_LABEL[d]}</span>
      </button>`
  ).join("");

  const banner = game.matchWinner
    ? `<div class="pen-banner"><button class="btn primary" id="penRematch">Rematch</button></div>`
    : "";

  app.innerHTML = `
    <div class="pen-head">
      <h1>🥅 Penalty Shootout ⚽</h1>
    </div>
    <div class="pen-wrap">
      <div class="card" style="display:flex;flex-direction:column;gap:var(--s-4)">
        <div class="pen-codebox">
          <div class="pen-codelabel">Room code</div>
          <div class="pen-code" id="penCodeBox" title="Click to copy">${esc(game.code || "····")}</div>
          <span class="pen-copyhint">Share this code with a friend to play. 5 kicks each.</span>
        </div>
        <div class="pen-players">${pchip(me, true)}<span class="pen-vs">${scoreMe} – ${scoreOpp}</span>${pchip(opp, false)}</div>
        <div class="pen-scoreboard">
          <div class="pen-sbrow"><span class="pen-sblabel">You</span><span class="pen-pips">${pips("you")}</span></div>
          <div class="pen-sbrow"><span class="pen-sblabel">${esc(opp ? opp.name : "Opp")}</span><span class="pen-pips">${pips("opp")}</span></div>
        </div>
        ${goalGraphic}
        ${kickLine}
        <div class="${statusClass}" id="penStatus">${statusText}</div>
        <div class="pen-choices">${actions}</div>
        ${banner}
      </div>
    </div>`;

  const choicesEl = app.querySelector(".pen-choices");
  choicesEl.addEventListener("click", (e) => {
    const btn = e.target.closest(".pen-choice");
    if (!btn || btn.disabled) return;
    sendPick(btn.dataset.dir);
  });
  const codeBox = $("penCodeBox");
  if (codeBox) codeBox.addEventListener("click", copyCode);
  const rematch = $("penRematch");
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
