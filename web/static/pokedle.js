// SharpLab HQ — Pokédle. Guess the mystery Pokémon; each guess returns
// attribute feedback (Type1, Type2, Generation, Legendary). Rounds POST to
// /api/v1/arcade/pokedle/* (session-cookie auth); the winning-guess response
// carries the authoritative new balance, pushed into the nav chip + header.

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
const round = { token: null, over: false, busy: false, guesses: 0 };
const MAX_GUESSES = 8;

// ── Toast (copied verbatim from casino.js) ──
function toast(msg) {
  const t = document.createElement("div");
  t.className = "cardtoast";
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 3200);
}

// ── Coins hub (copied verbatim from casino.js / pokemon.js) ──
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

// ── Shared balance push (nav chip + on-page header) ──
function applyBalance(bal) {
  if (bal == null) return;
  state.balance = bal;
  renderNav(state.me && state.me.user);
  const pb = document.getElementById("pageBal");
  if (pb) pb.textContent = coins(bal);
}

const $ = (id) => document.getElementById(id);

function setBusy(busy) {
  round.busy = busy;
  document.querySelectorAll(".pdbtn, .pdguess").forEach((el) => (el.disabled = busy));
}

// ── POST to a pokedle endpoint. Returns parsed JSON (with _status on error). ──
async function postPokedle(path, body) {
  if (MOCK) return mockPokedle(path, body);
  const r = await fetch("/api/v1/arcade/pokedle" + path, {
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

// ── Render one attribute cell ──
function attrCell(label, val, match, extra) {
  const cls = match ? "hit" : "miss";
  const mark = match ? "✅" : "❌";
  return `<td class="pdcell ${cls}">
    <span class="pdmark">${mark}</span>
    <span class="pdval">${esc(val)}${extra || ""}</span></td>`;
}

// ── Append a guess row to the board ──
function addRow(fb) {
  const body = $("pdBody");
  if (!body) return;
  const g = fb.gen;
  const arrow = g.match ? "" : ` <span class="pddir">${g.dir === "up" ? "⬆️" : "⬇️"}</span>`;
  const leg = fb.legendary.val ? "Yes" : "No";
  const tr = document.createElement("tr");
  tr.className = "pdrow";
  tr.innerHTML = `
    <td class="pdnamecell">
      <img class="pdsprite" src="${esc(fb.sprite)}" alt="" loading="lazy" />
      <span class="pdname">${esc(fb.name)}</span>
    </td>
    ${attrCell("Type 1", fb.type1.val, fb.type1.match)}
    ${attrCell("Type 2", fb.type2.val, fb.type2.match)}
    ${attrCell("Gen", "Gen " + g.val, g.match, arrow)}
    ${attrCell("Legendary", leg, fb.legendary.match)}`;
  body.appendChild(tr);
  if (!REDUCE) {
    tr.classList.add("pdpop");
    tr.addEventListener("animationend", () => tr.classList.remove("pdpop"), { once: true });
  }
}

// ── Start a new round ──
async function newRound() {
  round.over = false;
  round.guesses = 0;
  const body = $("pdBody");
  const msg = $("pdMsg");
  const reveal = $("pdReveal");
  const input = $("pdGuess");
  const btnRow = $("pdBtns");
  if (body) body.innerHTML = "";
  if (reveal) reveal.innerHTML = "";
  if (msg) { msg.textContent = `Guess the mystery Pokémon — ${MAX_GUESSES} tries.`; msg.className = "pdmsg idle"; }
  if (input) input.value = "";
  if (btnRow) btnRow.innerHTML =
    `<button class="btn primary big pdbtn" id="pdGuessBtn">Guess</button>
     <button class="btn ghost pdbtn" id="pdGiveUp">Give up</button>`;
  updateCounter();
  setBusy(true);
  const res = await postPokedle("/new", {});
  setBusy(false);
  if (res.error || res._status) {
    return toast("❌ " + (res.error || "couldn't start a round"));
  }
  round.token = res.token;
  if (input) input.focus();
}

function updateCounter() {
  const c = $("pdTries");
  if (c) c.textContent = `${round.guesses} / ${MAX_GUESSES}`;
  const s = $("pdSolved");
  if (s) s.textContent = num(state.solved);
}

// ── End the round (solved or out of guesses / give up) ──
function endRound(res, solved) {
  round.over = true;
  const reveal = $("pdReveal");
  const msg = $("pdMsg");
  const btnRow = $("pdBtns");
  if (msg) msg.textContent = "";
  if (reveal) {
    const sprite = res.sprite ? `<img class="pdbigsprite" src="${esc(res.sprite)}" alt="" />` : "";
    const head = solved ? "🎉 Solved!" : "Answer";
    const rewardLine = solved && res.reward
      ? `<div class="pdreward">+${num(res.reward)} 🪙</div>`
      : (solved ? "" : `<div class="pddexline">No coins this time.</div>`);
    reveal.innerHTML = `<div class="pdrevcard ${solved ? "win" : "lose"}">
      ${sprite}
      <div class="pdrevname">${head}: <b>${esc(res.name || "???")}</b></div>
      ${rewardLine}</div>`;
  }
  if (btnRow) btnRow.innerHTML = `<button class="btn primary big pdbtn" id="pdNext">New game</button>`;
}

// ── Submit a guess ──
async function submitGuess() {
  if (round.busy || round.over) return;
  const input = $("pdGuess");
  const guess = (input && input.value || "").trim();
  if (!guess) { if (input) input.focus(); return; }
  setBusy(true);
  const res = await postPokedle("/guess", { token: round.token, guess });
  setBusy(false);
  if (res.error || res._status) {
    const m = $("pdMsg");
    if (res.error === "unknown Pokémon") {
      if (m) { m.textContent = "That's not a Pokémon — try again."; m.className = "pdmsg wrong"; }
      if (input && !REDUCE) {
        input.classList.remove("shake"); void input.offsetWidth; input.classList.add("shake");
        input.addEventListener("animationend", () => input.classList.remove("shake"), { once: true });
      }
      if (input) input.select();
      return;
    }
    return toast("❌ " + (res.error || "something went wrong"));
  }
  round.guesses += 1;
  addRow(res.feedback);
  updateCounter();
  if (input) { input.value = ""; input.focus(); }
  if (res.solved) {
    applyBalance(res.balance);
    state.solved += 1;
    updateCounter();
    endRound(res, true);
    return;
  }
  if (round.guesses >= MAX_GUESSES) {
    // Out of guesses — reveal the answer (no reward).
    const rev = await postPokedle("/reveal", { token: round.token });
    if (!rev.error && !rev._status) endRound(rev, false);
    else endRound({}, false);
    return;
  }
  const m = $("pdMsg");
  if (m) { m.textContent = `Keep going — ${MAX_GUESSES - round.guesses} left.`; m.className = "pdmsg idle"; }
}

// ── Give up ──
async function giveUp() {
  if (round.busy || round.over) return;
  setBusy(true);
  const res = await postPokedle("/reveal", { token: round.token });
  setBusy(false);
  if (res.error || res._status) {
    return toast("❌ " + (res.error || "something went wrong"));
  }
  endRound(res, false);
}

// ── Delegated events ──
app.addEventListener("click", (e) => {
  if (e.target.closest("#pdGuessBtn")) return submitGuess();
  if (e.target.closest("#pdGiveUp")) return giveUp();
  if (e.target.closest("#pdNext")) return newRound();
});
app.addEventListener("keydown", (e) => {
  if (e.target.id === "pdGuess" && e.key === "Enter") { e.preventDefault(); submitGuess(); }
});

async function loadNames() {
  const res = await getJSON("/api/v1/arcade/pokedle/names");
  const dl = $("pdNames");
  if (dl && res && Array.isArray(res.names)) {
    dl.innerHTML = res.names.map((n) => `<option value="${esc(n)}"></option>`).join("");
  }
}

function buildPage() {
  const signedOut = !state.me
    ? `<div class="card" style="max-width:520px;margin:0 auto 16px;text-align:center">
        <p class="muted" style="margin:0 0 10px">Sign in with Discord to play for coins.</p>
        <a class="btn" href="/api/v1/auth/discord/login">Sign in with Discord</a></div>`
    : "";
  app.innerHTML = `
    <div class="pd-head">
      <h1>🔍 Pokédle <span class="balancechip" id="pageBal">${coins(state.balance)}</span></h1>
      <p>Guess the mystery Pokémon. Each guess reveals ✅/❌ across Type 1, Type 2, Generation and Legendary.</p>
    </div>
    ${signedOut}
    <div class="pd-wrap">
      <div class="card pdcard">
        <div class="pdguessrow">
          <input class="pdguess" id="pdGuess" type="text" list="pdNames" autocomplete="off"
                 autocapitalize="off" spellcheck="false" placeholder="Type a Pokémon name…" />
          <datalist id="pdNames"></datalist>
        </div>
        <div class="pdmsg idle" id="pdMsg">Guess the mystery Pokémon — ${MAX_GUESSES} tries.</div>
        <div class="pdbtns" id="pdBtns">
          <button class="btn primary big pdbtn" id="pdGuessBtn">Guess</button>
          <button class="btn ghost pdbtn" id="pdGiveUp">Give up</button>
        </div>
        <div class="pdreveal" id="pdReveal"></div>
        <div class="pdtablewrap">
          <table class="pdtable">
            <thead><tr>
              <th>Pokémon</th><th>Type 1</th><th>Type 2</th><th>Gen</th><th>Legendary</th>
            </tr></thead>
            <tbody id="pdBody"></tbody>
          </table>
        </div>
        <div class="pdcounter">Tries: <b id="pdTries">0 / ${MAX_GUESSES}</b> · Solved this session: <b id="pdSolved">0</b></div>
      </div>
    </div>`;
  loadNames();
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
// Mock mode (?mock=1): fake but consistent rounds without a
// backend. Answer = Pikachu (Electric, Gen 1, non-legendary);
// accept "pikachu". Real endpoints win by default.
// ─────────────────────────────────────────────────────────────
const MOCK = new URLSearchParams(location.search).get("mock") === "1";

function mockJSON(url) {
  if (url.startsWith("/api/v1/hq/me"))
    return { authenticated: true, user: { id: "1", username: "davidj", avatar: null }, balance: 12500 };
  if (url.indexOf("/pokedle/names") !== -1)
    return { names: ["Pikachu", "Charizard", "Bulbasaur", "Gengar", "Snorlax", "Mewtwo", "Eevee"] };
  return {};
}

const SPRITE = (d) => `https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/other/official-artwork/${d}.png`;
// A tiny local dex so mock guesses give consistent feedback vs. Pikachu.
const MOCK_DEX = {
  pikachu:    { dex: 25, name: "Pikachu", t1: "Electric", t2: null, gen: 1, leg: false },
  charizard:  { dex: 6,  name: "Charizard", t1: "Fire", t2: "Flying", gen: 1, leg: false },
  bulbasaur:  { dex: 1,  name: "Bulbasaur", t1: "Grass", t2: "Poison", gen: 1, leg: false },
  gengar:     { dex: 94, name: "Gengar", t1: "Ghost", t2: "Poison", gen: 1, leg: false },
  snorlax:    { dex: 143, name: "Snorlax", t1: "Normal", t2: null, gen: 1, leg: false },
  mewtwo:     { dex: 150, name: "Mewtwo", t1: "Psychic", t2: null, gen: 1, leg: true },
  eevee:      { dex: 133, name: "Eevee", t1: "Normal", t2: null, gen: 1, leg: false },
  lucario:    { dex: 448, name: "Lucario", t1: "Fighting", t2: "Steel", gen: 4, leg: false },
};
const MOCK_ANSWER = MOCK_DEX.pikachu;

function mockPokedle(path, body) {
  if (path === "/new") return { token: "mock-token" };
  if (path === "/reveal") return { name: MOCK_ANSWER.name, sprite: SPRITE(MOCK_ANSWER.dex) };
  // /guess
  const key = String(body && body.guess || "").trim().toLowerCase();
  const g = MOCK_DEX[key];
  if (!g) return { error: "unknown Pokémon", _status: 400 };
  const a = MOCK_ANSWER;
  const dir = g.gen < a.gen ? "up" : g.gen > a.gen ? "down" : "";
  const fb = {
    name: g.name, sprite: SPRITE(g.dex),
    type1: { val: g.t1 || "—", match: g.t1 === a.t1 },
    type2: { val: g.t2 || "—", match: g.t2 === a.t2 },
    gen: { val: g.gen, match: g.gen === a.gen, dir },
    legendary: { val: g.leg, match: g.leg === a.leg },
  };
  const solved = key === "pikachu";
  const out = { feedback: fb, solved };
  if (solved) {
    const reward = 100;
    state.balance += reward;
    out.reward = reward; out.balance = state.balance;
    out.name = a.name; out.sprite = SPRITE(a.dex);
  }
  return out;
}

main();
