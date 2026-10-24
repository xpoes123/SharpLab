// SharpLab HQ — Poké Hoops. Draft five Gen 1 Pokémon (3 options a pick, a Legendary first), then the
// auto-lineup plays an 82-game season against the TV cartridge's league. 82-0 is the dream.
//
// The draft runs here from a server-issued seed (POST /start); every pack comes from that seed via
// pokehoops-draft.js, the same file the server replays to check the picks. POST /submit sends only
// the token + picks: the server verifies them, sims the season under a season seed it draws only
// now, records it, and returns the summary + season seed. We then replay that exact season locally
// (same vendored engine, same seeds) for the game-by-game reveal. ?s=<id> replays a shared season.
import { nextPack, playSeason, summarize, teamName, POS, mon, bestPos, bestLineup, ovr, LEGENDS } from "./pokehoops-draft.js";
import { profile, skills } from "./pokehoops/ratings.js";

const app = document.getElementById("app");
const navRight = document.getElementById("navRight");
const API = "/api/v1/arcade/pokehoops";
const MOCK = new URLSearchParams(location.search).get("mock") === "1";
const REDUCE = matchMedia("(prefers-reduced-motion: reduce)").matches;
const $ = (id) => document.getElementById(id);
const num = (n) => (n == null ? "—" : Number(n).toLocaleString());
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const sprite = (n) => `/pokehoops/sprites/${n}.png`;

const EFFECTS = {
  SHARPSHOOTER: ["Sharpshooter", "Hunts threes, +7% from deep"],
  SLASHER: ["Slasher", "Attacks the rim, +6% on drives"],
  POSTERIZER: ["Posterizer", "+6% at the rim and in the post"],
  LOCKDOWN: ["Lockdown", "−6% for whoever it guards on the perimeter"],
  RIM_PROTECTOR: ["Rim protector", "Swats shots at the rim"],
  GLASS_CLEANER: ["Glass cleaner", "Owns the boards, more second chances"],
  FLOOR_GENERAL: ["Floor general", "Fewer turnovers, more assists, lifts teammates"],
  PICKPOCKET: ["Pickpocket", "Jumps passing lanes, forces turnovers"],
  CLUTCH: ["Clutch", "+7% in the fourth quarter"],
  HEAT_CHECK: ["Heat check", "Hit one and it's +8% on the next"],
  INTIMIDATOR: ["Intimidator", "−5% for every shot at the rim"],
  HYPNOTIST: ["Hypnotist", "Puts ball-handlers to sleep"],
};
const TYPE_COL = {
  normal: "#a8a77a", fire: "#ee8130", water: "#6390f0", electric: "#f7d02c", grass: "#7ac74c", ice: "#96d9d6",
  fighting: "#c22e28", poison: "#a33ea1", ground: "#e2bf65", flying: "#a98ff3", psychic: "#f95587", bug: "#a6b91a",
  rock: "#b6a136", ghost: "#735797", dragon: "#6f35fc", dark: "#705746", steel: "#b7b7ce", fairy: "#d685ad",
};
const SKILLS = [["shoot", "SHT"], ["finish", "FIN"], ["handle", "HND"], ["perD", "PER‑D"], ["intD", "RIM‑D"], ["reb", "REB"]];

const state = { me: null, balance: 0, draft: null, busy: false, reveal: null };

// ── IO ───────────────────────────────────────────────────────────────────────
async function getJSON(url) {
  if (MOCK) return mock(url);
  try {
    const r = await fetch(url, { credentials: "include" });
    if (!r.ok) return { _status: r.status, ...(await r.json().catch(() => ({}))) };
    return r.json();
  } catch { return { _status: 0, error: "offline" }; }
}
async function post(path, body) {
  if (MOCK) return mock(API + path, body);
  try {
    const r = await fetch(API + path, { method: "POST", credentials: "include",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok && j.error == null) j.error = r.status === 401 ? "sign in to play" : `error ${r.status}`;
    return j;
  } catch { return { error: "offline — check your connection" }; }
}
function toast(msg) {
  const t = document.createElement("div");
  t.className = "cardtoast"; t.textContent = msg;
  document.body.appendChild(t); setTimeout(() => t.remove(), 3200);
}
function renderNav(user) {
  if (!user) { navRight.innerHTML = `<a class="btn" href="/api/v1/auth/discord/login">Sign in with Discord</a>`; return; }
  const av = user.avatar ? `https://cdn.discordapp.com/avatars/${user.id}/${user.avatar}.png` : null;
  navRight.innerHTML = `<div class="userbar">
    ${av ? `<img class="avatar" src="${av}" alt="">` : `<div class="avatar"></div>`}
    <span class="phuser">${esc(user.username)}</span>
    <span class="coinschip" title="Casino coins">🪙 ${num(state.balance)}</span>
    <a class="btn ghost" href="/api/v1/auth/logout">Sign out</a></div>`;
}

// ── Pieces ───────────────────────────────────────────────────────────────────
const typeChips = (n) => mon(n).types.map((t) => `<span class="phtype" style="--tc:${TYPE_COL[t] || "#888"}">${t}</span>`).join("");
const fxLabel = (n) => { const e = profile(n)?.effect; return e && EFFECTS[e] ? EFFECTS[e] : null; };

function optionCard(n, i) {
  const m = mon(n), b = bestPos(n), p = profile(n) || {}, k = skills(n), fx = fxLabel(n);
  const bars = SKILLS.map(([key, lab]) => {
    const v = Math.round(k[key] * 99);
    return `<div class="phbar"><span>${lab}</span><i><b style="width:${v}%"></b></i><em>${v}</em></div>`;
  }).join("");
  return `<button class="phcard${LEGENDS.includes(n) ? " legend" : ""}" data-n="${n}" style="--d:${i * 70}ms">
    <div class="phspr"><img src="${sprite(n)}" alt="" width="96" height="96"></div>
    <div class="phinfo">
      <div class="phtop"><span class="phname">${esc(m.name)}</span><span class="phovr"><small>${b.pos}</small>${b.ovr}</span></div>
      <div class="phtypes">${typeChips(n)}</div>
      <div class="pharch">${esc(p.archetype || "")}</div>
      ${fx ? `<div class="phmove"><b>${esc(p.move)}</b> · <span class="phfx" title="${esc(fx[1])}">${esc(fx[0])}</span><div class="phfxd">${esc(fx[1])}</div></div>` : ""}
      <div class="phbars">${bars}</div>
      <div class="phblurb">${esc(p.blurb || "")}</div>
    </div>
  </button>`;
}

function pickSlots(picks) {
  return `<div class="phslots">${[0, 1, 2, 3, 4].map((i) => {
    const n = picks[i];
    return n ? `<div class="phslot full"><img src="${sprite(n)}" alt="${esc(mon(n).name)}" title="${esc(mon(n).name)}"></div>`
             : `<div class="phslot${i === picks.length ? " next" : ""}">${i === 0 ? "★" : i + 1}</div>`;
  }).join("")}</div>`;
}

function lineupRows(lineup) {
  return lineup.map((n, i) => {
    const p = profile(n) || {}, fx = fxLabel(n);
    return `<div class="phrow" style="--d:${i * 80}ms">
      <span class="phrpos">${POS[i]}</span>
      <img src="${sprite(n)}" alt="" width="56" height="56">
      <div class="phrmain"><div class="phrname">${esc(mon(n).name)} ${typeChips(n)}</div>
        <div class="phrsub">${esc(p.archetype || "")}${fx ? ` · <b>${esc(p.move)}</b>: ${esc(fx[0])}` : ""}</div></div>
      <span class="phrovr">${ovr(n, POS[i])}</span>
    </div>`;
  }).join("");
}

const head = (sub = "") => `<div class="phhead"><h1>🏀 Poké Hoops</h1>${sub ? `<p>${sub}</p>` : ""}</div>`;

// ── Views ────────────────────────────────────────────────────────────────────
function renderIntro() {
  state.draft = null;
  const signedOut = !state.me ? `<p class="muted">Sign in with Discord to draft — your best season goes on the board.</p>
    <a class="btn big" href="/api/v1/auth/discord/login">Sign in with Discord</a>` : "";
  app.innerHTML = `${head("Draft five Gen 1 Pokémon. Play 82 games against the league. Go 82–0.")}
    <div class="card phintro">
      <div class="phlegends">${LEGENDS.map((n) => `<img src="${sprite(n)}" alt="">`).join("")}</div>
      <ol class="phrules">
        <li><b>Pick 1</b> is a Legendary — three of the five are on offer.</li>
        <li><b>Picks 2–5</b>: three options each, take one. Ratings, type and signature move all matter.</li>
        <li>Your five slot into PG · SG · SF · PF · C automatically, then play an <b>82-game season</b>.</li>
      </ol>
      ${state.me ? `<button class="btn big primary" id="phStart">Start the draft</button>` : signedOut}
      <p class="phfine">Same engine as the TV cartridge. The server re-checks every pick and sims the season itself.</p>
    </div>
    <div class="phlb" id="phLb"></div>`;
  loadLeaderboard();
}

async function startDraft() {
  if (state.busy) return;
  state.busy = true;
  const res = await post("/start", {});
  state.busy = false;
  if (res.error) return toast("❌ " + res.error);
  state.draft = { token: res.token, seed: res.seed, picks: [] };
  renderDraft();
}

function renderDraft() {
  const d = state.draft, pack = nextPack(d.seed, d.picks);
  if (!pack) return renderLineup();
  const i = d.picks.length;
  app.innerHTML = `${head()}
    <div class="phdraft">
      <div class="phdrafthead">
        <div><div class="phstep">Pick ${i + 1} of 5${i === 0 ? " · Legendary" : ""}</div>
          <div class="phprompt">${i === 0 ? "Choose your franchise Legendary" : "Take one"}</div></div>
        ${pickSlots(d.picks)}
      </div>
      <div class="phpack">${pack.map(optionCard).join("")}</div>
    </div>`;
}

function pick(n) {
  const d = state.draft;
  if (!d || !nextPack(d.seed, d.picks)?.includes(n)) return;
  d.picks.push(n);
  renderDraft();
}

function renderLineup() {
  const d = state.draft, lineup = bestLineup(d.picks), team = Math.round(lineup.reduce((a, n, i) => a + ovr(n, POS[i]), 0) / 5);
  app.innerHTML = `${head()}
    <div class="card phlineup">
      <div class="phstep">Your starting five <span class="phteam">Team OVR ${team}</span></div>
      <div class="phrows">${lineupRows(lineup)}</div>
      <button class="btn big primary" id="phPlay">Play the 82-game season</button>
    </div>`;
}

async function playIt() {
  if (state.busy) return;
  const d = state.draft;
  state.busy = true;
  const btn = $("phPlay");
  if (btn) { btn.disabled = true; btn.textContent = "Simming the season…"; }
  const res = await post("/submit", { token: d.token, picks: d.picks });
  state.busy = false;
  if (res.error) {
    toast("❌ " + res.error);
    if (/expired|illegal/.test(res.error)) return renderIntro();
    if (btn) { btn.disabled = false; btn.textContent = "Play the 82-game season"; }
    return;
  }
  if (res.balance != null) { state.balance = res.balance; renderNav(state.me?.user); }
  reveal(d.seed, d.picks, res, { mine: true });
}

// Replay the season locally from the seeds the server used, and animate it game by game.
function reveal(seed, picks, server, { mine = false, owner = null } = {}) {
  const s = playSeason(seed, picks, server.season_seed);
  const local = summarize(s.lineup, s);
  if (server.strip && server.strip !== local.strip) console.warn("pokehoops: local replay differs from server", server, local);
  const sum = { ...local, ...server, games: s.games };
  state.reveal = { sum, mine, owner, timer: 0, g: 0, done: false };
  app.innerHTML = `${head()}
    <div class="card phseason">
      <div class="phstep">${owner ? `${esc(owner)}'s season` : "The season"}</div>
      <div class="phrec"><span id="phW">0</span><i>–</i><span id="phL">0</span></div>
      <div class="phticker" id="phTick">Tip-off…</div>
      <div class="phstrip" id="phStrip">${sum.games.map(() => "<b></b>").join("")}</div>
      <div class="phfive">${s.lineup.map((n, i) => `<img src="${sprite(n)}" alt="${esc(mon(n).name)}" title="${POS[i]} ${esc(mon(n).name)}">`).join("")}</div>
      <button class="btn ghost" id="phSkip">Skip to the end ⏭</button>
      <div id="phFinal"></div>
    </div>
    <div class="phlb" id="phLb"></div>`;
  step();
}

function step() {
  const r = state.reveal;
  if (!r || r.done) return;
  const g = r.sum.games[r.g], cells = $("phStrip")?.children;
  if (!g || !cells) return;
  cells[r.g].className = g.win ? "w" : "l";
  r.g++;
  const w = r.sum.games.slice(0, r.g).filter((x) => x.win).length;
  $("phW").textContent = w; $("phL").textContent = r.g - w;
  const perfect = w === r.g;
  $("phTick").innerHTML = `G${r.g} · ${g.home ? "vs" : "@"} ${esc(teamName(g.opp))} · <b class="${g.win ? "pos" : "neg"}">${g.win ? "W" : "L"} ${g.us}–${g.them}</b>`
    + (perfect && r.g >= 60 ? ` <span class="phhot">🔥 still perfect</span>` : "");
  $("phTick").classList.toggle("hot", perfect && r.g >= 60);
  if (r.g >= r.sum.games.length) return finish();
  // brisk through the season, then slow down if a perfect run is on the line
  const delay = REDUCE ? 0 : perfect && r.g >= 72 ? 260 : perfect && r.g >= 60 ? 110 : 42;
  r.timer = setTimeout(step, delay);
}

function skip() {
  const r = state.reveal;
  if (!r || r.done) return;
  clearTimeout(r.timer);
  const cells = $("phStrip").children;
  r.sum.games.forEach((g, i) => { cells[i].className = g.win ? "w" : "l"; });
  r.g = r.sum.games.length;
  $("phW").textContent = r.sum.wins; $("phL").textContent = r.sum.losses;
  finish();
}

function finish() {
  const r = state.reveal; r.done = true;
  const s = r.sum, skipBtn = $("phSkip");
  if (skipBtn) skipBtn.remove();
  const tick = $("phTick"); tick.classList.remove("hot");
  tick.textContent = s.wins === 82 ? "PERFECT SEASON" : `Final: ${s.wins}–${s.losses}`;
  const gradeCls = s.wins === 82 ? "perfect" : s.wins >= 64 ? "great" : s.wins >= 50 ? "good" : s.wins >= 35 ? "meh" : "bad";
  const bg = s.best_game, ts = s.top_scorer;
  const extras = r.mine ? `
      ${s.is_new_best ? `<div class="phnew${REDUCE ? "" : " pop"}">🏆 New personal best${s.prev_best != null ? ` (was ${s.prev_best}–${82 - s.prev_best})` : ""}</div>`
                      : s.prev_best != null ? `<div class="phmuted">Your best: ${s.prev_best}–${82 - s.prev_best}</div>` : ""}
      ${s.rank ? `<div class="phrank">#${num(s.rank)} on the board</div>` : ""}
      ${s.coins ? `<div class="phcoins">+${num(s.coins)} 🪙</div>` : ""}` : "";
  $("phFinal").innerHTML = `
    <div class="phgrade ${gradeCls}${REDUCE ? "" : " pop"}">${esc(s.grade)}</div>
    ${extras}
    <div class="phstats">
      <div class="phstat"><span>Top scorer</span><div class="phts"><img src="${sprite(ts.n)}" alt=""><div><b>${esc(ts.name)}</b> <small>${ts.pos}</small><br>team-high in ${ts.led} games · season-high ${ts.high} pts</div></div></div>
      <div class="phstat"><span>Best game</span><b>${bg ? `G${bg.g}: ${bg.us}–${bg.them}` : "—"}</b><small>${bg ? `vs ${esc(bg.opp_name)}` : "no wins"}</small></div>
      <div class="phstat"><span>Longest streak</span><b>${s.streak}</b><small>straight wins</small></div>
      <div class="phstat"><span>Points</span><b>${s.ppg}</b><small>allowed ${s.oppg}</small></div>
    </div>
    <div class="phactions">
      ${s.id ? `<button class="btn primary" id="phShare">Share result</button>` : ""}
      ${state.me ? `<button class="btn${s.id ? " ghost" : " primary"}" id="phAgain">${r.mine ? "Draft again" : "Draft your own"}</button>`
                 : `<a class="btn" href="/api/v1/auth/discord/login">Sign in to draft your own</a>`}
    </div>`;
  loadLeaderboard();
}

function shareText(s) {
  const five = s.lineup.map((n) => mon(n).name).join(" · ");
  return `🏀 Poké Hoops — ${s.wins}–${s.losses} (${s.grade})\n${five}\n${location.origin}/pokehoops.html?s=${s.id}`;
}
async function share() {
  const s = state.reveal?.sum;
  if (!s?.id) return;
  const text = shareText(s);
  if (navigator.share && matchMedia("(pointer: coarse)").matches) {
    try { await navigator.share({ text }); return; } catch { /* cancelled → fall through to copy */ }
  }
  try { await navigator.clipboard.writeText(text); toast("📋 Copied — paste it anywhere"); }
  catch { prompt("Copy your result:", text); }
}

// ── Leaderboard ──────────────────────────────────────────────────────────────
async function loadLeaderboard() {
  const res = await getJSON(API + "/leaderboard");
  const el = $("phLb");
  if (!el) return;
  const top = (res && res.top) || [];
  const rows = top.map((r) => `<a class="phlbrow${r.me ? " me" : ""}" href="?s=${r.id}" data-sid="${r.id}">
      <span class="phlbrank">#${r.rank}</span>
      <span class="phlbname">${esc(r.name)}</span>
      <span class="phlbfive">${r.lineup.map((n) => `<img src="${sprite(n)}" alt="" title="${esc(mon(n).name)}">`).join("")}</span>
      <span class="phlbrec">${r.wins}–${r.losses}</span>
      <span class="phlbgrade">${esc(r.grade)}</span>
    </a>`).join("");
  el.innerHTML = `<div class="phlbhead">🏆 Best seasons</div>
    ${top.length ? `<div class="phlblist">${rows}</div>` : `<div class="phlbempty">No seasons yet — be the first to chase 82–0.</div>`}`;
}

async function openShared(id) {
  const res = await getJSON(`${API}/season/${encodeURIComponent(id)}`);
  if (!res || res.error || res._status) { toast("That season link doesn't exist"); history.replaceState(null, "", location.pathname + (MOCK ? "?mock=1" : "")); return renderIntro(); }
  reveal(res.seed, res.picks, { ...res, id: res.id }, { owner: res.name });
}

// ── Events ───────────────────────────────────────────────────────────────────
app.addEventListener("click", (e) => {
  const card = e.target.closest(".phcard");
  if (card) return pick(Number(card.dataset.n));
  if (e.target.closest("#phStart")) return startDraft();
  if (e.target.closest("#phPlay")) return playIt();
  if (e.target.closest("#phSkip")) return skip();
  if (e.target.closest("#phShare")) return share();
  if (e.target.closest("#phAgain")) { history.replaceState(null, "", location.pathname + (MOCK ? "?mock=1" : "")); return startDraft(); }
  const lb = e.target.closest(".phlbrow");
  if (lb) { e.preventDefault(); history.replaceState(null, "", `?s=${lb.dataset.sid}${MOCK ? "&mock=1" : ""}`); clearTimeout(state.reveal?.timer); openShared(lb.dataset.sid); scrollTo(0, 0); }
});

async function main() {
  const me = await getJSON("/api/v1/hq/me");
  state.me = me && me.authenticated ? me : null;
  state.balance = state.me ? me.balance || 0 : 0;
  renderNav(state.me && state.me.user);
  const sid = new URLSearchParams(location.search).get("s");
  if (sid) return openShared(sid);
  renderIntro();
}

// ── Mock mode (?mock=1): local seeds + local season so the page runs without the API ──
const mockDb = { seasons: [], best: null };
function mock(url, body) {
  if (url.startsWith("/api/v1/hq/me")) return { authenticated: true, user: { id: "1", username: "davidj", avatar: null }, balance: 12500 };
  const rnd = () => Math.floor(Math.random() * 2 ** 32);
  if (url.endsWith("/start")) return { token: "mock", seed: rnd() };
  if (url.endsWith("/submit")) {
    const ss = rnd(), s = playSeason(state.draft.seed, body.picks, ss), sum = summarize(s.lineup, s);
    const prev = mockDb.best;
    const row = { ...sum, id: mockDb.seasons.length + 1, seed: state.draft.seed, season_seed: ss, picks: body.picks };
    mockDb.seasons.push(row);
    if (!prev || sum.wins > prev.wins) mockDb.best = row;
    state.balance += 25;
    return { ...row, prev_best: prev ? prev.wins : null, is_new_best: !prev || sum.wins > prev.wins, rank: 2, coins: 25, balance: state.balance };
  }
  if (url.includes("/season/")) {
    const id = Number(url.split("/").pop()), row = mockDb.seasons.find((x) => x.id === id) || MOCK_LB[id - 101];
    return row ? { ...row, name: row.name || "davidj" } : { _status: 404, error: "no such season" };
  }
  if (url.endsWith("/leaderboard")) {
    const mine = mockDb.best ? [{ ...mockDb.best, name: "davidj", me: true }] : [];
    const top = [...MOCK_LB, ...mine].sort((a, b) => b.wins - a.wins).map((r, i) => ({ ...r, rank: i + 1 }));
    return { game: "pokehoops", top };
  }
  return {};
}
const MOCK_LB = ["steph", "nova", "rohan"].map((name, i) => {   // legal mock drafts: always take the best-rated option
  const seed = 11 * (i + 1), picks = [];
  for (let p; (p = nextPack(seed, picks)); ) picks.push(p.slice().sort((a, b) => bestPos(b).ovr - bestPos(a).ovr)[i % 2]);
  const s = playSeason(seed, picks, 7 + i);
  return { id: 101 + i, name, seed, picks, season_seed: 7 + i, ...summarize(s.lineup, s) };
});

main();
