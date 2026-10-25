// SharpLab HQ — Poké Hoops. Draft eight Gen 1–2 Pokémon (3 options a pick, a Legendary first), then coach:
// five starters at PG..C + three reserves, each backing up a spot. Then an 82-game season against the TV
// cartridge's league. 82-0 is the dream. Overall ratings are never shown — skills, positions, types, moves only.
//
// The draft runs here from a server-issued seed (POST /start); every pack comes from that seed via
// pokehoops-draft.js, the same file the server replays to check the picks. POST /submit sends only
// the token + picks + team: the server verifies them, sims the season under a season seed it draws only
// now, records it, and returns the summary + season seed. We then replay that exact season locally
// (same vendored engine, same seeds) for the game-by-game reveal. ?s=<id> replays a shared season.
// Two-player: any recorded season's team can be challenged — draft your own eight, then a best-of-seven against
// it (POST /submit with vs=<season id>), simmed server-side the same way. ?x=<id> replays a shared series.
import { nextPack, matchLine, seasonGame, playSeason, playSeries, bestOf7, duelReplay, SERIES_WINS, summarize, teamName, defaultTeam, PICKS, POS, mon, bestPos, LEGENDS, LEAGUE, LEAGUE_BY_STRENGTH } from "./pokehoops-draft.js";
import { profile, skills } from "./pokehoops/ratings.js";
import { members, asTeam, seeded, newGame, quarterDone, gameDone, nextQuarter, rollPoss, playBeat, preplay, botCall, queueCall,
  queueTimeout, callStatus, CALLS } from "./pokehoops/engine.js";

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
async function getJSON(url, headers = {}) {
  if (MOCK) return mock(url);
  try {
    const r = await fetch(url, { credentials: "include", headers });
    if (!r.ok) return { _status: r.status, ...(await r.json().catch(() => ({}))) };
    return r.json();
  } catch { return { _status: 0, error: "offline" }; }
}
async function post(path, body, headers = {}) {
  if (MOCK) return mock(API + path, body);
  try {
    const r = await fetch(API + path, { method: "POST", credentials: "include",
      headers: { "Content-Type": "application/json", ...headers }, body: JSON.stringify(body || {}) });
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
  if (state.me?.guest) {
    navRight.innerHTML = `<div class="userbar"><span class="phuser">${esc(user.username)} <small class="phmuted">guest</small></span>
      <button class="btn ghost" id="phGuestOut">Sign out</button></div>`;
    return;
  }
  const av = user.avatar ? `https://cdn.discordapp.com/avatars/${user.id}/${user.avatar}.png` : null;
  navRight.innerHTML = `<div class="userbar">
    ${av ? `<img class="avatar" src="${av}" alt="">` : `<div class="avatar"></div>`}
    <span class="phuser">${esc(user.username)}</span>
    <span class="coinschip" title="Casino coins">🪙 ${num(state.balance)}</span>
    <a class="btn ghost" href="/api/v1/auth/logout">Sign out</a></div>`;
}

// ── Pieces ───────────────────────────────────────────────────────────────────
const typeChips = (n) => mon(n).types.map((t) => `<span class="phtype" style="--tc:${TYPE_COL[t] || "#888"}">${t}</span>`).join("");
const plays = (n) => (profile(n)?.pos || [bestPos(n).pos]).join("/");
const fits = (n, spot) => (profile(n)?.pos || [bestPos(n).pos]).includes(POS[spot]);
const fxLabel = (n) => { const e = profile(n)?.effect; return e && EFFECTS[e] ? EFFECTS[e] : null; };

function optionCard(n, i) {
  const m = mon(n), p = profile(n) || {}, k = skills(n), fx = fxLabel(n);
  const bars = SKILLS.map(([key, lab]) => {
    const v = Math.round(k[key] * 99);
    return `<div class="phbar"><span>${lab}</span><i><b style="width:${v}%"></b></i><em>${v}</em></div>`;
  }).join("");
  return `<button class="phcard${LEGENDS.includes(n) ? " legend" : ""}" data-n="${n}" style="--d:${i * 70}ms">
    <div class="phspr"><img src="${sprite(n)}" alt="" width="96" height="96"></div>
    <div class="phinfo">
      <div class="phtop"><span class="phname">${esc(m.name)}</span><span class="phplays">plays ${plays(n)}</span></div>
      <div class="phtypes">${typeChips(n)}</div>
      <div class="pharch">${esc(p.archetype || "")}</div>
      ${fx ? `<div class="phmove"><b>${esc(p.move)}</b> · <span class="phfx" title="${esc(fx[1])}">${esc(fx[0])}</span><div class="phfxd">${esc(fx[1])}</div></div>` : ""}
      <div class="phbars">${bars}</div>
      <div class="phblurb">${esc(p.blurb || "")}</div>
    </div>
  </button>`;
}

// The roster so far, under the pack: who you have, where they play, what they do — and which spots are still open.
function pickRow(n) {
  const p = profile(n) || {}, k = skills(n), fx = fxLabel(n);
  const mini = SKILLS.map(([key, lab]) => `<span title="${lab} ${Math.round(k[key] * 99)}"><i style="height:${Math.max(8, Math.round(k[key] * 100))}%"></i><small>${lab.replace("‑D", "")}</small></span>`).join("");
  return `<div class="phcrow phpick"><img src="${sprite(n)}" alt="" width="44" height="44">
    <div class="phrmain"><div class="phrname">${esc(mon(n).name)} ${typeChips(n)}</div>
      <div class="phrsub">plays ${plays(n)}${fx ? ` · <b>${esc(p.move)}</b>: ${esc(fx[0])}` : ""}</div></div>
    <div class="phmini">${mini}</div></div>`;
}
function rosterPanel(picks, title = "Your picks") {
  if (!picks.length) return "";
  const cover = POS.map((ps) => `<span class="${picks.some((n) => (profile(n)?.pos || [bestPos(n).pos]).includes(ps)) ? "fit" : "off"}">${ps}</span>`).join("");
  return `<div class="phroster"><div class="phgroup">${title} <small>${picks.length}/${PICKS}</small><span class="phcover" title="Positions someone can play">${cover}</span></div>
    <div class="phrows">${picks.map(pickRow).join("")}</div></div>`;
}

function pickSlots(picks) {
  return `<div class="phslots">${Array.from({ length: PICKS }, (_, i) => i).map((i) => {
    const n = picks[i];
    return n ? `<div class="phslot full"><img src="${sprite(n)}" alt="${esc(mon(n).name)}" title="${esc(mon(n).name)}"></div>`
             : `<div class="phslot${i === picks.length ? " next" : ""}">${i === 0 ? "★" : i + 1}</div>`;
  }).join("")}</div>`;
}

const head = (sub = "") => `<div class="phhead"><h1>🏀 Poké Hoops</h1>${sub ? `<p>${sub}</p>` : ""}</div>`;

// The solo / challenge draft survives a refresh: kept in sessionStorage (per tab), dropped once played or abandoned.
const DKEY = "phdraft";
function saveDraft() {
  const d = state.draft;
  try { if (d && !d.duel) sessionStorage.setItem(DKEY, JSON.stringify({ ...d, sel: null, vsLine: null, uid: state.me?.user?.id })); else sessionStorage.removeItem(DKEY); } catch { /* fine */ }
}
function loadDraft() {
  try { const d = JSON.parse(sessionStorage.getItem(DKEY) || "null"); return d?.token && d.uid === state.me?.user?.id ? d : null; } catch { return null; }
}
// Ask the server to keep the draft's token alive; if it's gone (TTL, restart) the picks stay, only the season is off.
const touchDraft = (d) => d?.token && !d.expired && post("/touch", { token: d.token }).then((r) => {
  if (!r.error || state.draft !== d || !/expired/.test(r.error)) return;
  d.expired = true; saveDraft();
  if (!state.live && !battle?.active() && ($("phPlay") || document.querySelector(".phpack"))) renderDraft();
});

// ── Views ────────────────────────────────────────────────────────────────────
function renderIntro() {
  state.draft = null; saveDraft();
  const signedOut = !state.me ? `<p class="muted">Play as a guest (just a name; your seasons still go on the board), or sign in with Discord to earn and bet coins.</p>
    <div class="phactions"><input id="phGuestName" class="phinput" maxlength="20" placeholder="Your name" autocomplete="nickname" aria-label="Guest name">
      <button class="btn big primary" id="phGuest">Play as guest</button></div>
    <a class="btn ghost" href="/api/v1/auth/discord/login">Sign in with Discord</a>` : "";
  app.innerHTML = `${head("Draft Pokémon. Coach them. Beat a bot, a friend, or the whole league.")}
    <div class="card phduelcta pbcta">
      <div class="phstep">⚔️ New · Battle (beta)</div>
      <p class="phmuted">The new game: 3v3, first to 11, played like a Pokémon battle. Every turn you and the opponent pick at once: shots, passes, screens, status moves, stat boosts, weather. Draft six, rotate three.</p>
      <div class="phactions"><button class="btn primary" id="pbStart">Battle a bot</button></div>
    </div>
    <div class="card phintro">
      <div class="phstep">🏆 82-game season</div>
      <div class="phlegends">${LEGENDS.map((n) => `<img src="${sprite(n)}" alt="">`).join("")}</div>
      <ol class="phrules">
        <li><b>Pick 1</b> is a Legendary — three of the eleven are on offer.</li>
        <li><b>Picks 2–8</b>: three options each, take one. Skills, type and signature move all matter.</li>
        <li><b>You coach:</b> set your five at PG · SG · SF · PF · C and pick which spot each of your three reserves backs up.</li>
        <li>Then play an <b>82-game season</b>.</li>
        <li><b>Two-player:</b> open anyone's season (the board below, or a link a friend sends) and hit <b>⚔️ Challenge</b> — draft your own eight and play their team, best of 7.</li>
      </ol>
      ${state.me ? `<button class="btn big primary" id="phStart">Start the draft</button>` : signedOut}
      <p class="phfine">Same engine as the TV cartridge. The server re-checks every pick and sims the season itself.</p>
    </div>
    <div class="card phduelcta">
      <div class="phstep">🆚 Live duel · two phones, one table</div>
      <p class="phmuted">Draft at the same time from separate packs, coach your lineups, see the line, bet it, then play a best-of-7. No sign-in needed to play (betting coins needs one).</p>
      ${state.me ? "" : `<input id="phName" class="phinput" maxlength="20" placeholder="Your name" autocomplete="nickname">`}
      <div class="phactions"><button class="btn primary" id="phDuelNew">Start a room</button>
        <input id="phCode" class="phinput code" maxlength="5" placeholder="CODE" autocapitalize="characters" aria-label="Room code"><button class="btn ghost" id="phDuelJoin">Join</button></div>
    </div>
    <div class="phlb" id="phLb"></div>`;
  loadLeaderboard();
}

async function startDraft(vs = null) {
  if (state.busy) return;
  state.busy = true;
  clearTimeout(state.reveal?.timer);
  const res = await post("/start", {});
  state.busy = false;
  if (res.error) return toast("❌ " + res.error);
  state.draft = { token: res.token, seed: res.seed, picks: [], vs };   // vs: {id, name, team} when challenging
  scrollTo(0, 0);
  renderDraft();
}

function renderDraft() {
  const d = state.draft, pack = nextPack(d.seed, d.picks);
  saveDraft();
  if (!pack) { d.team = d.team || defaultTeam(d.picks); d.sel = null; return renderLineup(); }
  const i = d.picks.length;
  app.innerHTML = `${head()}
    <div class="phdraft">
      <div class="phdrafthead">
        <div><div class="phstep">Pick ${i + 1} of ${PICKS}${i === 0 ? " · Legendary" : ""}</div>
          <div class="phprompt">${i === 0 ? "Choose your franchise Legendary" : "Take one"}</div>
          ${d.vs ? `<div class="phmuted">⚔️ Drafting to take on ${esc(d.vs.name)}'s team</div>` : ""}
          ${d.expired ? `<div class="phmuted">⌛ This draft timed out on the server (bot games only now). <button class="btn ghost" id="phRedraft">Start a new one</button></div>` : ""}</div>
        ${pickSlots(d.picks)}
      </div>
      <div class="phpack">${pack.map(optionCard).join("")}</div>
      ${rosterPanel(d.picks)}
    </div>`;
}

function pick(n) {
  const d = state.draft;
  if (!d || !nextPack(d.seed, d.picks)?.includes(n)) return;
  d.picks.push(n);
  renderDraft();
}

// ── Coaching: tap two players (or drag one onto another) to swap; each reserve picks a spot to back up ──
// Roster slots 0-4 are the starters (PG..C), 5-7 the bench; a bench slot keeps its "backs up" choice on a swap.
const slotGet = (t, k) => (k < 5 ? t.five[k] : t.bench[k - 5]);
const slotSet = (t, k, n) => { if (k < 5) t.five[k] = n; else t.bench[k - 5] = n; };
function swapSlots(a, b) {
  const t = state.draft.team, na = slotGet(t, a);
  slotSet(t, a, slotGet(t, b)); slotSet(t, b, na);
}

function coachRow(t, k, sel) {
  const n = slotGet(t, k), p = profile(n) || {}, sk = skills(n), fx = fxLabel(n);
  const spot = k < 5 ? k : t.backs[k - 5], fit = fits(n, spot);
  const mini = SKILLS.map(([key, lab]) => `<span title="${lab} ${Math.round(sk[key] * 99)}"><i style="height:${Math.max(8, Math.round(sk[key] * 100))}%"></i><small>${lab.replace("‑D", "")}</small></span>`).join("");
  const backs = k < 5 ? "" : `<div class="phbacks" role="group" aria-label="${esc(mon(n).name)} backs up">
      <span>backs up</span>${POS.map((ps, s) => `<button class="phbk${t.backs[k - 5] === s ? " on" : ""}" data-bk="${k - 5}" data-s="${s}">${ps}</button>`).join("")}</div>`;
  return `<div class="phcrow${sel === k ? " sel" : ""}${k >= 5 ? " bench" : ""}" data-k="${k}" draggable="true" tabindex="0" role="button" aria-pressed="${sel === k}">
      <span class="phrpos">${k < 5 ? POS[k] : "B" + (k - 4)}</span>
      <img src="${sprite(n)}" alt="" width="52" height="52">
      <div class="phrmain">
        <div class="phrname">${esc(mon(n).name)} ${typeChips(n)}</div>
        <div class="phrsub"><span class="${fit ? "fit" : "off"}">plays ${plays(n)}</span>${fx ? ` · <b>${esc(p.move)}</b>: ${esc(fx[0])}` : ""}</div>
      </div>
      <div class="phmini">${mini}</div>
      ${backs}
    </div>`;
}

function renderLineup() {
  const d = state.draft, t = d.team;
  const hint = d.sel == null ? "Tap a player, then another, to swap them (or drag one onto another)."
                             : `Now tap who ${esc(mon(slotGet(t, d.sel)).name)} swaps with.`;
  if (d.vs) vsLineSoon(d);
  saveDraft();
  app.innerHTML = `${head()}
    <div class="card phlineup">
      <div><div class="phstep">You're the coach</div>
        <div class="phprompt">Set your lineup</div>
        <div class="phmuted">${hint}</div></div>
      <div class="phgroup">Starting five</div>
      <div class="phrows">${[0, 1, 2, 3, 4].map((k) => coachRow(t, k, d.sel)).join("")}</div>
      <div class="phgroup">Bench <small>subs in mid-quarter behind the spot it backs up · two at one spot split the minutes</small></div>
      <div class="phrows">${[5, 6, 7].map((k) => coachRow(t, k, d.sel)).join("")}</div>
      ${d.vs ? `<div class="phgroup">vs ${esc(d.vs.name)}</div>${teamStrip(d.vs.team, true)}<div id="phVsLine" class="phvsline">${vsLineHTML(d)}</div>` : ""}
      ${d.duel ? `<p class="phmuted" id="phOppStat">${oppLineupStat()}</p>${leaveBtn()}` : ""}
      ${d.expired ? `<p class="phmuted">⌛ This draft timed out on the server, so it can't play ${d.vs ? "the series" : "a season"} any more. Bot games below still work.</p>
        <button class="btn big primary" id="phRedraft">Start a new draft</button>`
        : `<button class="btn big primary" id="phPlay">${playLabel(d)}</button>`}
      ${!d.vs && !d.duel ? `<div class="phgroup">Or play one game first <small>you coach it live: call plays, call timeouts. Doesn't use up the draft.</small></div>
        <div class="phlevels">${BOT_LEVELS.map((l, i) => `<button class="btn ghost" data-bot="${i}">🎮 vs ${l.name}</button>`).join("")}</div>` : ""}
    </div>`;
}

const oppLineupStat = () => { const v = state.duel?.view, n = esc(v?.names[1 - v.seat] || "");
  return v?.opp_locked ? `${n} has locked their lineup ✓` : `${n} is setting their lineup…`; };
const playLabel = (d) => (d.duel ? "Lock in my lineup" : d.vs ? `Lock in &amp; play ${esc(d.vs.name)} · best of 7` : "Lock in &amp; play the season");
const nameCap = (named, n) => (named ? `<small>${esc(mon(n).name)}</small>` : "");
const teamStrip = (t, named = false) => `<div class="phfive${named ? " named" : ""}">${t.five.map((n, i) => `<figure><img src="${sprite(n)}" alt="${esc(mon(n).name)}" title="${POS[i]} ${esc(mon(n).name)}"><figcaption>${POS[i]}${nameCap(named, n)}</figcaption></figure>`).join("")}
  ${t.bench.map((n, j) => `<figure class="phbenchfig"><img src="${sprite(n)}" alt="" title="${esc(mon(n).name)} (bench)"><figcaption>${named && t.backs ? `B·${POS[t.backs[j]]}` : "B"}${nameCap(named, n)}</figcaption></figure>`).join("")}</div>`;

// Challenge lineup: the engine's line on this exact matchup, recomputed (debounced, small n so a phone keeps up) as you coach
const vsKey = (d) => JSON.stringify([d.team, d.vs.team]);
function vsLineHTML(d) {
  const c = d.vsLine;
  if (!c || c.key !== vsKey(d)) return `<span class="phmuted">📈 Working out the line…</span>`;
  const names = [state.me?.user?.username || "You", d.vs.name], l = c.line, fav = l.series >= 0.5 ? 0 : 1;
  return `<details class="phlinewrap"><summary>Pregame line: ${esc(names[fav])} ${american(Math.max(l.series, 1 - l.series))} · you win ${pct(l.p)} of games</summary>${lineCard(l, names, [d.team, d.vs.team])}</details>`;
}
function vsLineSoon(d) {
  clearTimeout(state.vsTimer);
  if (d.vsLine?.key === vsKey(d)) return;
  state.vsTimer = setTimeout(() => {
    if (state.draft !== d || !d.vs) return;
    d.vsLine = { key: vsKey(d), line: matchLine(d.team, d.vs.team, 400) };
    const el = $("phVsLine"), open = el?.querySelector("details")?.open;
    if (el) { el.innerHTML = vsLineHTML(d); if (open) el.querySelector("details").open = true; }
  }, 400);
}

function tapSlot(k) {
  const d = state.draft;
  if (d.sel == null) d.sel = k;
  else { if (d.sel !== k) swapSlots(d.sel, k); d.sel = null; }
  renderLineup();
}

async function playIt() {
  if (state.busy) return;
  const d = state.draft;
  if (d.duel) { const b = $("phPlay"); if (b) { b.disabled = true; b.textContent = "Locking in…"; } return duelPost("team", { team: d.team }).then(() => { if ($("phPlay")) { $("phPlay").disabled = false; $("phPlay").textContent = playLabel(d); } }); }
  state.busy = true;
  const btn = $("phPlay");
  if (btn) { btn.disabled = true; btn.textContent = d.vs ? "Simming the series…" : "Simming the season…"; }
  const res = await post("/submit", { token: d.token, picks: d.picks, team: d.team, vs: d.vs?.id ?? null });
  state.busy = false;
  if (res.error) {
    toast("❌ " + res.error);
    if (/expired/.test(res.error)) { d.expired = true; return renderLineup(); }   // keep the picks: bot games still work
    if (/illegal/.test(res.error)) return renderIntro();
    if (btn) { btn.disabled = false; btn.innerHTML = playLabel(d); }
    return;
  }
  state.draft = null; saveDraft();
  if (res.id) history.replaceState(null, "", `?${d.vs ? "x" : "s"}=${res.id}${MOCK ? "&mock=1" : ""}`);
  if (d.vs) return revealSeries({ ...res, name: state.me?.user?.username || "You", opp_name: d.vs.name, opp_team: d.vs.team }, true);
  if (res.balance != null) { state.balance = res.balance; renderNav(state.me?.user); }
  reveal(d.seed, d.picks, d.team, res, { mine: true });
}

// Replay the season locally from the seeds the server used, and animate it game by game.
function reveal(seed, picks, team, server, { mine = false, owner = null, me = false } = {}) {
  const s = playSeason(seed, picks, team, server.season_seed);
  const local = summarize(s.team, s);
  if (server.strip && server.strip !== local.strip) console.warn("pokehoops: local replay differs from server", server, local);
  const sum = { ...local, ...server, games: s.games };
  state.reveal = { sum, mine: mine || me, posted: mine, owner, timer: 0, g: 0, done: false, team: s.team, seasonSeed: server.season_seed };
  app.innerHTML = `${head()}
    <div class="card phseason">
      <div class="phstep">${owner ? `${esc(owner)}'s season` : "The season"}</div>
      <div class="phrec"><span id="phW">0</span><i>–</i><span id="phL">0</span></div>
      <div class="phticker" id="phTick">Tip-off…</div>
      <div class="phstrip" id="phStrip">${sum.games.map((_, k) => `<b data-sg="${k}"></b>`).join("")}</div>
      <div id="phDetail"></div>
      <div class="phfive">${s.team.five.map((n, i) => `<figure><img src="${sprite(n)}" alt="${esc(mon(n).name)}" title="${POS[i]} ${esc(mon(n).name)}"><figcaption>${POS[i]}</figcaption></figure>`).join("")}</div>
      <div class="phbench">${s.team.bench.map((n, j) => `<figure><img src="${sprite(n)}" alt="" title="${esc(mon(n).name)} backs up ${POS[s.team.backs[j]]}"><figcaption>${esc(mon(n).name)} · backs ${POS[s.team.backs[j]]}</figcaption></figure>`).join("")}</div>
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
  if (r.skip) return r.skip();
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
      ${s.is_new_best ? `<div class="phnew${REDUCE ? "" : " pop"}">🏆 New personal best${s.prev_best != null ? ` (was ${s.prev_best}–${82 - s.prev_best})` : ""}</div>
                         ${s.rank ? `<div class="phrank">#${num(s.rank)} on the board</div>` : ""}`
                      : s.prev_best != null ? `<div class="phmuted">Your best is still ${s.prev_best}–${82 - s.prev_best}${s.rank ? ` · it's #${num(s.rank)} on the board` : ""}</div>` : ""}
      ${s.coins ? `<div class="phcoins">+${num(s.coins)} 🪙</div>` : ""}` : "";
  $("phFinal").innerHTML = `
    <div class="phgrade ${gradeCls}${REDUCE ? "" : " pop"}">${esc(s.grade)}</div>
    ${extras}
    <div class="phstats">
      <div class="phstat"><span>Top scorer</span><div class="phts"><img src="${sprite(ts.n)}" alt=""><div><b>${esc(ts.name)}</b> <small>${ts.bench ? "off the bench" : ts.pos}</small><br>team-high in ${ts.led} games · season-high ${ts.high} pts</div></div></div>
      <div class="phstat"><span>Best game</span><b>${bg ? `G${bg.g}: ${bg.us}–${bg.them}` : "—"}</b><small>${bg ? `vs ${esc(bg.opp_name)}` : "no wins"}</small></div>
      <div class="phstat"><span>Longest streak</span><b>${s.streak}</b><small>straight wins</small></div>
      <div class="phstat"><span>Points</span><b>${s.ppg}</b><small>allowed ${s.oppg}</small></div>
    </div>
    <div class="phactions">
      ${s.id && r.posted && !state.me?.guest ? `<button class="btn primary" id="phPost">Post to #bot</button>` : ""}
      ${s.id && state.me && !r.mine ? `<button class="btn primary" id="phChallenge">⚔️ Challenge this team</button>` : ""}
      ${s.id ? `<button class="btn ghost" id="phShare">Copy link</button>` : ""}
      ${state.me ? `<button class="btn${s.id ? " ghost" : " primary"}" id="phAgain">${r.mine ? "Draft again" : "Draft your own"}</button>`
                 : `<a class="btn" href="/api/v1/auth/discord/login">Sign in to draft your own</a>`}
    </div>
    ${s.id && r.mine ? `<p class="phmuted">Two-player: send a friend the link — they draft their own eight and take this team on, best of 7.</p>` : ""}
    ${(s.challenges || []).length ? `<div class="phgroup">Challenges</div><div class="phchals">${s.challenges.map((c) =>
      `<a href="?x=${c.id}" data-xid="${c.id}" class="phchal ${c.wins > c.losses ? "neg" : "pos"}">${esc(c.name)} ${c.wins > c.losses ? "beat it" : "lost"} ${c.wins}–${c.losses}</a>`).join("")}</div>` : ""}`;
  loadLeaderboard();
}

function shareText(s) {
  const five = s.team.five.map((n, i) => `${POS[i]} ${mon(n).name}`).join(" · ");
  const bench = s.team.bench.map((n, j) => `${mon(n).name} (backs ${POS[s.team.backs[j]]})`).join(" · ");
  return `🏀 Poké Hoops — ${s.wins}–${s.losses} (${s.grade})\n${five}\nBench: ${bench}\n${location.origin}/pokehoops.html?s=${s.id}`;
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

async function postToDiscord(btn) {
  const s = state.reveal?.sum;
  if (!s?.id || btn.disabled) return;
  btn.disabled = true; btn.textContent = "Posting…";
  const res = await post(`/share/${s.id}`);
  const ok = res && (res.ok || res.error === "already posted");
  btn.textContent = ok ? "Posted ✓" : "Post to #bot";
  btn.disabled = ok;
  toast(ok ? "🏀 Posted to #bot" : res?.error || "Couldn't post — try again");
}

// ── Leaderboard ──────────────────────────────────────────────────────────────
async function loadLeaderboard(period = state.lbPeriod || "all") {
  state.lbPeriod = period;
  const res = await getJSON(`${API}/leaderboard?period=${period}`);
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
  const tab = (p, label) => `<button class="phlbtab${p === period ? " on" : ""}" data-period="${p}">${label}</button>`;
  el.innerHTML = `<div class="phlbhead">🏆 Best seasons <span class="phlbtabs">${tab("day", "Today")}${tab("all", "All-time")}</span></div>
    ${top.length ? `<div class="phlblist">${rows}</div>` : `<div class="phlbempty">${period === "day" ? "Nobody's played today" : "No seasons yet"} — be the first to chase 82–0.</div>`}`;
}

async function openShared(id) {
  const res = await getJSON(`${API}/season/${encodeURIComponent(id)}`);
  if (!res || res.error || res._status) { toast(res?._status === 410 ? "That's an old-rules season — it can't be replayed" : "That season link doesn't exist"); history.replaceState(null, "", location.pathname + (MOCK ? "?mock=1" : "")); return renderIntro(); }
  reveal(res.seed, res.picks, res.team, { ...res, id: res.id }, { owner: res.name, me: !!res.me });
}

// ── Head-to-head: replay the best-of-seven from its seeds, one game at a time ──
function revealSeries(server, mine = false) {
  const x = playSeries(server.seed, server.picks, server.team, server.opp_team, server.series_seed, true);
  if (x.wins !== server.wins || x.losses !== server.losses) console.warn("pokehoops: local series replay differs from server", server, x);
  showSeries({
    names: [server.name, server.opp_name], teams: [x.team, x.opp], games: x.games, wins: [x.wins, x.losses],
    line: server.line || null, seat: mine ? 0 : null,
    actions: () => `${server.id ? `<button class="btn ghost" id="phXShare">Copy link</button>` : ""}
      ${state.me && server.vs ? `<button class="btn primary" id="phRematch" data-vs="${server.vs}">${mine ? "Run it back" : "Take this team on yourself"}</button>` : ""}
      ${server.vs ? `<a class="btn ghost" href="?s=${server.vs}">See ${esc(server.opp_name)}'s season</a>` : ""}`,
    share: server.id ? { url: `${location.origin}/pokehoops.html?x=${server.id}` } : null,
  });
}

// ── The line: what the engine's Monte Carlo makes of this exact matchup ──
const american = (p) => (p >= 0.5 ? `−${Math.round((100 * p) / (1 - p))}` : `+${Math.round((100 * (1 - p)) / p)}`);
const americanDec = (d) => (d <= 1 ? "OFF" : d >= 2 ? `+${Math.round((d - 1) * 100)}` : `−${Math.round(100 / (d - 1))}`);
const pct = (p, dp = 0) => `${(100 * p).toFixed(dp)}%`;

function lineCard(line, names, teams, prices = null) {
  if (!line) return "";
  const sp = line.spread, fav = sp >= 0 ? 0 : 1, sides = [line.series, 1 - line.series];
  const rows = ["4-0", "4-1", "4-2", "4-3", "3-4", "2-4", "1-4", "0-4"].map((k) => {
    const v = line.exact[k] || 0, a = k.startsWith("4");
    return `<div class="phex ${a ? "a" : "b"}"><span>${k.replace("-", "–")}</span><i><b style="width:${Math.min(100, (v / 0.45) * 100)}%"></b></i><em>${pct(v)}</em></div>`;
  }).join("");
  const mvp = line.mvp.map((m) => `<div class="phmvp"><img src="${sprite(m.dex)}" alt=""><span>${esc(mon(m.dex).name)} <small>${esc(names[m.side])}</small></span><em>${pct(m.p)}</em></div>`).join("");
  return `<div class="phline">
    <div class="phlinehead">📈 The line <small>${line.n.toLocaleString()} sims of this exact matchup · game win ${pct(line.p, 1)} ± ${pct(1.96 * line.se, 1)}</small></div>
    <table class="phodds"><thead><tr><th></th><th>Series</th><th>Fair</th>${prices ? "<th>Book</th>" : ""}<th>Spread</th></tr></thead><tbody>
      ${[0, 1].map((s) => `<tr><td><b>${esc(names[s])}</b></td><td>${pct(sides[s])}</td><td>${american(sides[s])}</td>${prices ? `<td class="phbook">${americanDec(prices[s])}</td>` : ""}<td>${s === fav ? `−${Math.abs(sp)}` : `+${Math.abs(sp)}`}</td></tr>`).join("")}
    </tbody></table>
    <div class="phlinemeta">Total (per game) <b>${line.total}</b>${prices ? ` · book holds 4.5%` : ""}</div>
    <div class="phlinecols"><div><div class="phgroup">Exact result</div>${rows}</div><div><div class="phgroup">Top scorer, any game</div>${mvp}</div></div>
  </div>`;
}

// ── A best-of-seven, game by game; tap a game for its box score, score flow and big plays ──
function showSeries(o) {
  const ms = o.teams.map(members), games = o.games;
  state.reveal = { series: o, timer: 0, g: 0, done: false };
  const you = (s) => (o.seat === s ? ` <small class="phyou">you</small>` : "");
  app.innerHTML = `${head()}
    <div class="card phseason phseries">
      <div class="phstep">Best of ${2 * SERIES_WINS - 1}</div>
      <div class="phvs"><div><b>${esc(o.names[0])}</b>${you(0)}${teamStrip(o.teams[0])}</div><i>vs</i><div><b>${esc(o.names[1])}</b>${you(1)}${teamStrip(o.teams[1])}</div></div>
      ${o.line ? `<details class="phlinewrap"${o.openLine ? " open" : ""}><summary>Pregame line: ${esc(o.names[o.line.series >= 0.5 ? 0 : 1])} ${american(Math.max(o.line.series, 1 - o.line.series))}</summary>${lineCard(o.line, o.names, o.teams, o.prices)}</details>` : ""}
      <div class="phrec"><span id="phW">0</span><i>–</i><span id="phL">0</span></div>
      <div class="phgames" id="phGames"></div>
      <div id="phDetail"></div>
      <button class="btn ghost" id="phSkip">Skip to the end ⏭</button>
      <div id="phFinal"></div>
    </div>`;
  const cell = (g, k) => `<button class="phgame ${g.win ? "w" : "l"}" data-g="${k}"><span>G${k + 1}${g.periods > 4 ? ` · ${g.periods > 5 ? g.periods - 4 : ""}OT` : ""}</span><b>${g.us}–${g.them}</b>
    <small>${esc(mon(ms[0][g.top[0].i]).name)} ${g.top[0].pts} · ${esc(mon(ms[1][g.top[1].i]).name)} ${g.top[1].pts}</small><em>box score ›</em></button>`;
  const tally = (k) => { const w = games.slice(0, k).filter((g) => g.win).length; $("phW").textContent = w; $("phL").textContent = k - w; };
  const end = () => {
    const r = state.reveal; r.done = true; $("phSkip")?.remove();
    const w = o.wins[0] > o.wins[1] ? 0 : 1, res = `${Math.max(...o.wins)}–${Math.min(...o.wins)}`;
    const lineNote = o.line ? (() => {
      const pw = w === 0 ? o.line.series : 1 - o.line.series, exact = o.line.exact[`${o.wins[0]}-${o.wins[1]}`];
      return `<div class="phmuted">The model had ${esc(o.names[w])} at ${pct(pw)} to win it (${american(pw)}); exactly ${res} was a ${pct(exact)} shot.${pw < 0.35 ? " 🚨 Upset." : ""}</div>`;
    })() : "";
    const bets = (o.bets || []).map((b, s) => b && `<div class="phbet ${b.payout ? "pos" : "neg"}">${esc(o.names[s])} bet ${num(b.stake)} on ${esc(o.names[b.side])} at ${americanDec(b.price)} → ${b.payout ? `won ${num(b.payout - b.stake)} 🪙` : "lost"}</div>`).filter(Boolean).join("");
    $("phFinal").innerHTML = `<div class="phgrade ${o.seat == null || o.seat === w ? "great" : "bad"}${REDUCE ? "" : " pop"}">${esc(o.names[w])} wins ${res}</div>
      ${lineNote}${bets}<div class="phactions">${o.actions ? o.actions() : ""}</div>`;
    if (o.onEnd) o.onEnd();
  };
  const tick = () => {
    const r = state.reveal;
    if (r?.series !== o || r.done) return;
    $("phGames").insertAdjacentHTML("beforeend", cell(games[r.g], r.g));
    tally(++r.g);
    if (r.g >= games.length) return end();
    r.timer = setTimeout(tick, REDUCE ? 0 : 1100);
  };
  state.reveal.skip = () => { const r = state.reveal; clearTimeout(r.timer); for (; r.g < games.length; r.g++) $("phGames").insertAdjacentHTML("beforeend", cell(games[r.g], r.g)); tally(r.g); end(); };
  tick();
}

const PLAY_TXT = { dunk: "throws it down 💥", three: "drills a three", block: "sends it back 🚫", steal: "picks his pocket", and1: "scores through contact — and one", clutch: "hits a clutch bucket 🧊" };
function gameDetail(k, scroll = true, o = state.reveal?.series) {
  const g = o?.games[k];
  if (!g) return;
  const ms = o.teams.map(members), qn = (q) => (q <= 4 ? `Q${q}` : `OT${q > 5 ? q - 4 : ""}`);
  const box = (s) => `<div class="phbox"><div class="phgroup">${esc(o.names[s])} <b>${s ? g.them : g.us}</b></div><div class="phboxscroll"><table>
    <thead><tr><th></th><th>PTS</th><th>REB</th><th>AST</th><th>STL</th><th>BLK</th><th>FG</th><th>3P</th><th>FT</th></tr></thead><tbody>
    ${ms[s].map((n, i) => { const b = g.box[`${s}:${i}`]; return `<tr class="${i >= 5 ? "bn" : ""}"><td><img src="${sprite(n)}" alt="">${esc(mon(n).name)} <small>${i < 5 ? POS[i] : "B"}</small></td><td><b>${b.pts}</b></td><td>${b.reb}</td><td>${b.ast}</td><td>${b.stl}</td><td>${b.blk}</td><td>${b.fgm}-${b.fga}</td><td>${b.tpm}-${b.tpa}</td><td>${b.ftm}-${b.fta}</td></tr>`; }).join("")}
    </tbody></table></div></div>`;
  // score margin after every possession (side 0 up = above the line)
  const W = 600, H = 150, f = g.flow, max = Math.max(10, ...f.map(([a, b]) => Math.abs(a - b)));
  const xy = f.map(([a, b], i) => [((i / (f.length - 1)) * W).toFixed(1), (H / 2 - ((a - b) / max) * (H / 2 - 6)).toFixed(1)]);
  const path = `M0,${H / 2} ` + xy.map(([x, y]) => `L${x},${y}`).join(" ") + ` L${W},${H / 2} Z`;
  const qs = f.map((x, i) => (i && x[2] !== f[i - 1][2] ? `<line x1="${xy[i][0]}" x2="${xy[i][0]}" y1="0" y2="${H}" class="q"/>` : "")).join("");
  const chart = `<svg class="phflow" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Score margin through the game">
    <clipPath id="phab"><rect width="${W}" height="${H / 2}"/></clipPath><clipPath id="phbe"><rect y="${H / 2}" width="${W}" height="${H / 2}"/></clipPath>
    ${qs}<line x1="0" x2="${W}" y1="${H / 2}" y2="${H / 2}" class="z"/>
    <path d="${path}" class="a" clip-path="url(#phab)"/><path d="${path}" class="b" clip-path="url(#phbe)"/></svg>
    <div class="phflowkey"><span class="a">▲ ${esc(o.names[0])}</span><span>biggest lead ${Math.max(0, ...f.map(([a, b]) => a - b))} · ${Math.max(0, ...f.map(([a, b]) => b - a))}</span><span class="b">▼ ${esc(o.names[1])}</span></div>`;
  // the big plays: every dunk, block and clutch bucket, plus fourth-quarter threes
  const big = g.plays.filter((p) => p[3] !== "three" && p[3] !== "steal" || p[0] >= 4).slice(-14);
  const plays = big.map(([q, side, n, what, a, b]) => `<li class="s${side}"><span>${qn(q)}</span><img src="${sprite(n)}" alt=""><span><b>${esc(mon(n).name)}</b> ${PLAY_TXT[what]}</span><em>${a}–${b}</em></li>`).join("");
  $("phDetail").innerHTML = `<div class="phdetail"><div class="phdetailhead"><b>Game ${k + 1}</b> · ${esc(o.names[0])} ${g.us}–${g.them} ${esc(o.names[1])}<button class="btn ghost" id="phDetailX" aria-label="Close">✕</button></div>
    ${chart}${box(0)}${box(1)}${plays ? `<div class="phgroup">Big plays</div><ul class="phplays">${plays}</ul>` : ""}</div>`;
  document.querySelectorAll(".phgame").forEach((el) => el.classList.toggle("on", Number(el.dataset.g) === k));
  if (scroll) $("phDetail").scrollIntoView({ behavior: REDUCE ? "auto" : "smooth", block: "start" });
}

// ── Live duel ────────────────────────────────────────────────────────────────
// The server holds the room; each phone polls it once a second and re-renders only when something changed.
// Our seat key lives in localStorage, so a refresh (or a phone lock) drops you straight back into your room.
const duelKey = (code, key) => { try { if (key) localStorage.setItem("phduel:" + code, key); return localStorage.getItem("phduel:" + code); } catch { return key || null; } };

async function duelOpen(code, create = false) {
  const name = $("phName")?.value.trim() || state.duelName || "";
  code = (code || "").toUpperCase();
  let key = create ? null : duelKey(code);
  if (!key) {
    if (!create) {   // a third visitor learns the room is full (or gone) before being asked for a name
      const pk = await getJSON(`${API}/duel/${encodeURIComponent(code)}/peek`);
      if (pk._status === 404) return renderDuelGone("That room has closed — check the code.");
      if (pk.open === false && !state.me) return renderDuelGone(`Room ${code} is full — ${pk.host} already has an opponent.`);
    }
    if (!state.me && !name) { state.duelName = ""; return renderDuelJoin(code, create); }
    const res = await post(create ? "/duel" : `/duel/${code}/join`, { name });
    if (res.error) return /full/.test(res.error) ? renderDuelGone(`Room ${code} is full.`) : toast("❌ " + res.error);
    code = res.code; key = duelKey(code, res.key);
  }
  clearTimeout(state.reveal?.timer); state.reveal = null;
  state.duel = { code, key, sig: "", view: null };
  state.draft = null;
  history.replaceState(null, "", `?duel=${code}${MOCK ? "&mock=1" : ""}`);
  duelPoll();
}

function renderDuelGone(msg) {
  history.replaceState(null, "", location.pathname + (MOCK ? "?mock=1" : ""));
  app.innerHTML = `${head()}<div class="card phlobby"><div class="phstep">Live duel</div><p>${esc(msg)}</p>
    <div class="phactions"><button class="btn primary" id="phDuelNew">Start your own room</button><button class="btn ghost" id="phHome">Home</button></div></div>`;
}

function renderDuelJoin(code, create) {
  app.innerHTML = `${head()}<div class="card phduelcta">
    <div class="phstep">${create ? "Start a room" : `Join room ${esc(code)}`}</div>
    <input id="phName" class="phinput" maxlength="20" placeholder="Your name" autocomplete="nickname" autofocus>
    <div class="phactions"><button class="btn primary" id="${create ? "phDuelNew" : "phDuelGo"}" data-code="${esc(code)}">Let's go</button>
      <a class="btn ghost" href="/api/v1/auth/discord/login">Sign in instead (to bet coins)</a></div></div>`;
}

// "Reconnecting…" pill, outside #app so re-renders don't wipe it
function netPill(on) {
  let el = $("phNet");
  if (!el && on) { el = document.createElement("div"); el.id = "phNet"; el.className = "phnet"; el.textContent = "Reconnecting…"; document.body.appendChild(el); }
  if (el) el.hidden = !on;
}

const duelHeaders = (d) => ({ "X-Duel-Key": d.key });

function duelForget(d) {
  try { localStorage.removeItem("phduel:" + d.code); } catch { /* fine */ }
  clearTimeout(d.timer); netPill(false);
  state.duel = null; state.draft = null;
}

async function duelPoll() {
  const d = state.duel;
  if (!d) return;
  clearTimeout(d.timer);
  let v = null;
  try {
    v = await getJSON(`${API}/duel/${d.code}`, duelHeaders(d));
    if (state.duel !== d) return;
    if (v._status === 403 || v._status === 404) {
      duelForget(d); toast(v.error || "That room has closed");
      history.replaceState(null, "", location.pathname + (MOCK ? "?mock=1" : ""));
      return renderIntro();
    }
    const good = !v._status && !v.error && typeof v.phase === "string";   // a flaky-wifi error never becomes the view
    netPill(!good);
    if (good) duelRender(v);
  } catch (e) {
    console.warn("pokehoops: duel poll", e);
  } finally {
    const phase = v && !v._status ? v.phase : d.view?.phase;
    if (state.duel === d && phase !== "done" && phase !== "left") d.timer = setTimeout(duelPoll, document.hidden ? 4000 : 1000);
  }
}

async function duelPost(path, body = {}) {
  const d = state.duel;
  if (!d || state.busy) return;
  state.busy = true;
  const v = await post(`/duel/${d.code}/${path}`, body, duelHeaders(d));
  state.busy = false;
  if (v.error) return toast("❌ " + v.error);
  if (state.duel === d && typeof v.phase === "string") duelRender(v);
}

async function duelLeave() {
  const d = state.duel, v = d?.view;
  if (!d) return renderIntro();
  if (v && v.phase !== "lobby" && v.phase !== "done" && v.phase !== "left"
      && !confirm(`Leave the duel? ${v.names[1 - v.seat] || "Your opponent"} will see you left. No coins are taken.`)) return;
  if (v?.phase !== "left" && v?.phase !== "done") await post(`/duel/${d.code}/leave`, {}, duelHeaders(d));
  duelForget(d);
  history.replaceState(null, "", location.pathname + (MOCK ? "?mock=1" : ""));
  renderIntro();
}

const leaveBtn = () => `<button class="btn ghost phduelleave" id="phDuelLeave">Leave room</button>`;

function duelRender(v) {
  const d = state.duel, prev = d.view;
  const sig = JSON.stringify([v.phase, v.picks, v.pending, v.opp_pending, v.team, v.opp_locked, v.ready, v.bets, v.result, v.names, v.left]);
  if (sig === d.sig) return;
  d.sig = sig; d.view = v;
  const me = v.seat, op = 1 - me, them = esc(v.names[op] || "your opponent");
  if (prev && prev.picks[op].length < v.picks[op].length && v.phase !== "lobby") {
    const n = v.picks[op][v.picks[op].length - 1];
    toast(`${v.names[op]} took ${mon(n).name}`);
  }
  if (v.phase === "left") {
    const who = v.left === me ? "You" : esc(v.names[v.left] || "Your opponent");
    duelForget(d);
    history.replaceState(null, "", location.pathname + (MOCK ? "?mock=1" : ""));
    app.innerHTML = `${head()}<div class="card phlobby"><div class="phstep">Room ${esc(v.code)} closed</div>
      <p><b>${who}</b> left the duel. No coins changed hands.</p>
      <div class="phactions"><button class="btn primary" id="phDuelNew">New room</button><button class="btn ghost" id="phHome">Home</button></div></div>`;
    return;
  }
  if (v.phase === "lobby") {
    const link = `${location.origin}/pokehoops.html?duel=${v.code}`;
    app.innerHTML = `${head()}<div class="card phlobby">
      <div class="phstep">Room</div><div class="phcodebig">${esc(v.code)}</div>
      <p class="phmuted">Have your opponent open <b>${esc(location.host)}/pokehoops.html</b> and enter the code, or send them the link.</p>
      <div class="phactions"><button class="btn primary" id="phDuelLink" data-link="${esc(link)}">Share link</button></div>
      <p class="phwait">Waiting for an opponent<span class="phdots"></span></p>${leaveBtn()}</div>`;
    return;
  }
  if (v.phase === "draft") {
    const k = v.picks[me].length, pack = duelReplay(v.seed, v.picks).rounds[k]?.[me] || [];
    const status = v.opp_pending ? `<span class="phok">${them} has picked ✓</span>` : `<span class="phwait">${them} is choosing<span class="phdots"></span></span>`;
    const body = v.pending
      ? `<div class="phlocked"><img src="${sprite(v.pending)}" alt="" width="96" height="96"><div><b>${esc(mon(v.pending).name)}</b> locked in<br>${status}</div></div>`
      : `<div class="phpack">${pack.map(optionCard).join("")}</div>`;
    app.innerHTML = `${head()}<div class="phdraft">
      <div class="phdrafthead">
        <div><div class="phstep">Round ${k + 1} of ${PICKS}${k === 0 ? " · Legendary" : ""} · picks reveal together</div>
          <div class="phprompt">${v.pending ? "Waiting on the reveal" : k === 0 ? "Choose your franchise Legendary" : "Take one"}</div>
          ${v.pending ? "" : `<div class="phmuted">${status}</div>`}</div>
        <div class="phduelslots"><div><small>${esc(v.names[me])}</small>${pickSlots(v.picks[me])}</div><div><small>${them}</small>${pickSlots(v.picks[op])}</div></div>
      </div>${body}
      ${rosterPanel(v.picks[me])}
      ${v.picks[op].length ? `<details class="phroster"><summary>${them}'s picks</summary>${rosterPanel(v.picks[op], `${them}'s picks`)}</details>` : ""}
      ${leaveBtn()}</div>`;
    return;
  }
  if (v.phase === "lineup") {
    if (!v.team) {
      if (!state.draft?.duel) state.draft = { duel: true, picks: v.picks[me], team: defaultTeam(v.picks[me]), sel: null };
      if (!prev || prev.phase !== "lineup" || prev.team) renderLineup();
      const el = $("phOppStat");
      if (el) el.innerHTML = oppLineupStat();
      return;
    }
    app.innerHTML = `${head()}<div class="card phlobby"><div class="phstep">Lineup locked</div>${teamStrip(v.team)}
      <p class="phwait">${them} is setting their lineup<span class="phdots"></span></p>
      <div class="phgroup">${them}'s eight</div>${teamStrip({ five: v.picks[op].slice(0, 5), bench: v.picks[op].slice(5) })}${leaveBtn()}</div>`;
    return;
  }
  const teams = me === 0 ? [v.team, v.opp_team] : [v.opp_team, v.team];
  if (v.phase === "tip") {
    const mine = v.bets[me], oppBet = v.bets[op];
    const sideBtn = (s) => `<button class="phside${(state.betSide ?? me) === s ? " on" : ""}" data-side="${s}"><b>${esc(v.names[s])}</b><span>${americanDec(v.prices[s])}</span></button>`;
    const betUI = v.ready[me]
      ? `<p class="phok">You're locked in${mine ? ` — ${num(mine.stake)} on ${esc(v.names[mine.side])} at ${americanDec(mine.price)}` : ""}.</p>`
      : v.can_bet ? `<div class="phbetbox"><div class="phgroup">Bet the series <small>balance ${num(state.balance)} 🪙</small></div>
          <div class="phsides">${sideBtn(0)}${sideBtn(1)}</div>
          <div class="phactions"><input id="phStake" class="phinput" type="number" min="1" max="5000" inputmode="numeric" placeholder="Stake" value="${state.betStake || ""}">
          <button class="btn primary" id="phBet">Lock bet &amp; tip off</button><button class="btn ghost" id="phNoBet">No bet, tip off</button></div>
          <div id="phPayout" class="phmuted"></div></div>`
        : `<div class="phactions"><button class="btn primary" id="phNoBet">Ready, tip off</button></div><p class="phfine">Sign in to bet coins on it.</p>`;
    app.innerHTML = `${head()}<div class="card phseason phtip">
      <div class="phstep">Tip-off · best of ${2 * SERIES_WINS - 1}</div>
      <div class="phvs"><div><b>${esc(v.names[0])}</b>${teamStrip(teams[0])}</div><i>vs</i><div><b>${esc(v.names[1])}</b>${teamStrip(teams[1])}</div></div>
      ${lineCard(v.line, v.names, teams, v.prices)}
      ${betUI}
      <p class="phmuted">${v.ready[op] ? `${them} is ready${oppBet ? ` (${num(oppBet.stake)} on ${esc(v.names[oppBet.side])})` : ""} ✓` : `${them} is looking at the line<span class="phdots"></span>`}</p>
      ${v.ready[me] ? "" : leaveBtn()}
    </div>`;
    payoutHint();
    return;
  }
  // done: replay the series from its seeds (with box scores) and reveal it
  const x = bestOf7(teams[0], teams[1], v.result.series_seed, true);
  if (x.wins !== v.result.wins[0]) console.warn("pokehoops: local duel replay differs from server", v.result, x);
  if (v.bets[me]?.balance != null) { state.balance = v.bets[me].balance; renderNav(state.me?.user); }
  showSeries({ names: v.names, teams, games: x.games, wins: v.result.wins, line: v.line, prices: v.prices, bets: v.bets, seat: me,
    share: { url: `${location.origin}/pokehoops.html?d=${v.result.id}` },
    actions: () => `<button class="btn ghost" id="phXShare">Copy link</button><button class="btn primary" id="phDuelNew">New room</button>` });
}

function payoutHint() {
  const v = state.duel?.view, el = $("phPayout"), stake = Number($("phStake")?.value || 0);
  if (!v || !el) return;
  const side = state.betSide ?? v.seat;
  el.textContent = stake > 0 ? `${num(stake)} on ${v.names[side]} pays ${num(Math.floor(stake * v.prices[side]))} if they win the series` : "";
}

async function openDuelRecord(id) {
  const d = await getJSON(`${API}/duels/${encodeURIComponent(id)}`);
  if (!d || d.error || d._status) { toast("That duel link doesn't exist"); history.replaceState(null, "", location.pathname); return renderIntro(); }
  const x = bestOf7(d.teams[0], d.teams[1], d.series_seed, true);
  showSeries({ names: d.names, teams: d.teams, games: x.games, wins: d.wins, line: d.line, bets: d.bets, seat: null,
    share: { url: location.href }, actions: () => `<button class="btn ghost" id="phXShare">Copy link</button><button class="btn primary" id="phDuelNew">Start your own duel</button>` });
}

// ── One game vs a bot, coached live ──────────────────────────────────────────
// Runs entirely here (nothing's at stake, so no server): the engine's newGame/rollPoss loop, one possession per tick,
// with the TV's coach calls. Your playbook is your eight's signature moves (three calls a game, +1 per OT); the bot
// coaches with the engine's botCall. Opponents are league teams, bucketed by their starters' strength.
const BOT_LEVELS = [{ name: "Rookie", band: [0, 0.34] }, { name: "Pro", band: [0.34, 0.67] }, { name: "All-Star", band: [0.67, 1] }];
const CALL_SAYS = { HYPNOTIST: "{X} puts {T} to sleep!", LOCKDOWN: "{X} locks onto {T}!", SHARPSHOOTER: "Plays for {X}: let it fly!", SLASHER: "{X} is attacking the rim!",
  POSTERIZER: "Feed {X} inside!", HEAT_CHECK: "Green light for {X}!", CLUTCH: "{X} wants the ball!", FLOOR_GENERAL: "{X} settles everybody down.",
  GLASS_CLEANER: "{X} crashes the glass!", RIM_PROTECTOR: "{X} walls off the paint!", INTIMIDATOR: "{X} stares down the paint!", PICKPOCKET: "Full-court press, led by {X}!" };
const WHY = { used: "used", queued: "next trip", limit: "no calls left", busy: "one at a time", late: "Q4 only" };
const qLabel = (G) => (G.ot ? `OT${G.ot > 1 ? G.ot : ""}` : `Q${G.q}`);
const clock = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
const botRecord = (delta) => { try { const r = JSON.parse(localStorage.getItem("phbot") || "[0,0]"); if (delta) { r[delta > 0 ? 0 : 1]++; localStorage.setItem("phbot", JSON.stringify(r)); } return r; } catch { return [0, 0]; } };

function startBotGame(level) {
  const d = state.draft, b = BOT_LEVELS[level], lo = Math.floor(b.band[0] * LEAGUE.length), hi = Math.floor(b.band[1] * LEAGUE.length);
  const opp = LEAGUE_BY_STRENGTH[lo + Math.floor(Math.random() * (hi - lo))];
  const teams = [asTeam(JSON.parse(JSON.stringify(d.team))), LEAGUE[opp]], rng = seeded((Math.random() * 2 ** 32) >>> 0);
  clearTimeout(state.live?.timer); touchDraft(d);
  state.live = { G: newGame(rng), teams, rng, level, names: [state.me?.user?.username || "You", teamName(opp)], feed: [], flow: [[0, 0, 1]], plays: [],
                 speed: 1, timer: 0, done: false };
  renderLive(); scrollTo(0, 0);
  state.live.timer = setTimeout(liveStep, 900);
}

function liveName(L, team, spot) { return mon(members(L.teams[team])[L.G.on[team][spot]]).name; }
function liveSay(L, b, what, pre) {
  const G = L.G, nm = (t, s) => esc(liveName(L, t, s)), coach = (t) => esc(L.names[t]);
  if (b.k === "call") {
    const X = esc(mon(members(L.teams[b.team])[b.ri]).name), T = esc(mon(members(L.teams[1 - b.team])[b.target]).name);
    const type = b.eff >= 2 ? ` It's super effective on ${T}!` : b.eff === 0 ? ` It has no effect on ${T}…` : b.eff < 1 ? ` Not very effective on ${T}…` : "";
    return [`📣 <b>${coach(b.team)} calls ${esc(b.move)}!</b> ${b.subFor >= 0 ? `${X} checks in. ` : ""}${CALL_SAYS[b.fx].replace("{X}", X).replace("{T}", T)}${b.run ? ` That ends the ${b.run[0]}-${b.run[1]} run.` : ""}${type}`, "call"];
  }
  if (b.k === "timeout") return [`⏱ Timeout, ${coach(b.team)}${b.run && b.run[0] && pre?.run?.team === 1 - b.team ? ` — stops a ${b.run[0]}-${b.run[1]} run` : ""}.`, "to"];   // engine logs the run, not whose
  if (b.k === "break" && !b.press) return [`${nm(b.team, b.who)} out on the break!`, ""];
  if (what === "made") {
    const how = b.dunk ? "THROWS IT DOWN! 💥" : b.kind === "three" ? "drains a three" : b.kind === "post" ? "scores in the post" : b.kind === "drive" ? "finishes at the rim" : "hits the jumper";
    return [`<b>${nm(b.team, b.who)}</b> ${how}${b.ast >= 0 ? ` <small>(${nm(b.team, b.ast)} with the dime)</small>` : ""}${b.late ? " at the buzzer of the shot clock" : ""}`, b.dunk || b.kind === "three" ? "big" : ""];
  }
  if (what === "block") return [`🚫 <b>${nm(1 - b.team, b.block)}</b> SENDS IT BACK on ${nm(b.team, b.who)}!`, "big"];
  if (what === "miss" && b.kind === "three") return [`${nm(b.team, b.who)} misses from deep`, "dim"];
  if (what === "miss") return [`${nm(b.team, b.who)} misses`, "dim"];
  if (what === "oreb") return [`${nm(b.team, b.who)} with the offensive board`, ""];
  if (what === "steal") return [`<b>${nm(1 - b.team, b.by)}</b> picks ${nm(b.team, b.who)}'s pocket!`, "big"];
  if (what === "turnover") return [b.violation ? `Shot-clock violation on ${nm(b.team, b.who)}.` : `${nm(b.team, b.who)} turns it over.`, "dim"];
  if (what === "foul") return [b.intentional ? `${coach(1 - b.team)} fouls on purpose.` : `Foul on ${nm(1 - b.team, b.by)}.`, "dim"];
  if (what === "ft") return [b.andOne ? (b.made ? `AND ONE! ${nm(b.team, b.who)} completes the three-point play.` : `${nm(b.team, b.who)} misses the and-one.`) : `${nm(b.team, b.who)} hits ${b.made} of ${b.n} at the line.`, b.andOne && b.made ? "big" : "dim"];
  return null;
}

function liveStep() {
  const L = state.live;
  if (!L || L.done || state.live !== L) return;
  const G = L.G;
  let pause = 0;
  if (quarterDone(G)) {
    if (gameDone(G)) return liveEnd();
    L.feed.unshift([`— End of ${qLabel(G)}: ${esc(L.names[0])} ${G.score[0]}, ${esc(L.names[1])} ${G.score[1]} —`, "q", ""]);
    nextQuarter(G); pause = 1400;
  } else {
    const ri = botCall(G, L.teams, 1);
    if (ri >= 0) queueCall(G, L.teams, 1, ri);
    const pre = preplay(G), stamp = `${qLabel(G)} ${clock(pre.secLeft)}`;
    let dur = 0;
    for (const b of rollPoss(G, L.teams, L.rng, pre)) {
      const what = playBeat(G, b), line = liveSay(L, b, what, pre);
      dur += b.d;
      if (line) L.feed.unshift([line[0], line[1], `${stamp} · ${G.score[0]}–${G.score[1]}`, what === "block" || what === "steal" || what === "foul" ? 1 - b.team : b.team]);
      const q = G.q + G.ot, sc = [G.score[0], G.score[1]];
      if (what === "made" && (b.dunk || b.kind === "three" || b.foul >= 0)) L.plays.push([q, b.team, members(L.teams[b.team])[G.on[b.team][b.who]], b.dunk ? "dunk" : b.foul >= 0 ? "and1" : "three", ...sc]);
      else if (what === "block") L.plays.push([q, 1 - b.team, members(L.teams[1 - b.team])[G.on[1 - b.team][b.block]], "block", ...sc]);
      else if (what === "steal") L.plays.push([q, 1 - b.team, members(L.teams[1 - b.team])[G.on[1 - b.team][b.by]], "steal", ...sc]);
    }
    L.flow.push([G.score[0], G.score[1], G.q + G.ot]);
    pause = Math.max(220, dur * 300);
  }
  L.feed.length = Math.min(L.feed.length, 60);
  renderLive();
  if (L.speed >= 99) return liveStep();
  L.timer = setTimeout(liveStep, pause / L.speed);
}

function renderLive() {
  const L = state.live, G = L.G, C = G.calls, pre = preplay(G);
  const box = $("phLive") || (() => { app.innerHTML = `${head()}<div class="card phlive" id="phLive"></div>`; return $("phLive"); })();
  const onFloor = (t) => G.on[t].map((ri) => { const n = members(L.teams[t])[ri]; return `<img src="${sprite(n)}" alt="${esc(mon(n).name)}" title="${esc(mon(n).name)}">`; }).join("");
  const act = (t) => C.active[t] ? `<span class="phact">${esc(C.active[t].move)} · ${C.active[t].left} left</span>` : C.queue[t] >= 0 ? `<span class="phact">call queued</span>` : "";
  const book = members(L.teams[0]).map((n, ri) => {
    const st = callStatus(G, L.teams, 0, ri);
    if (st.why === "none") return "";
    const fx = EFFECTS[st.fx], eff = st.eff >= 2 ? `<b class="pos">super effective</b>` : st.eff === 0 ? `<b class="neg">no effect</b>` : st.eff < 1 ? `<b class="neg">not very effective</b>` : "";
    return `<button class="phcall${st.ok ? "" : " off"}${st.why === "queued" ? " q" : ""}" data-call="${ri}" ${st.ok ? "" : "disabled"}>
      <img src="${sprite(n)}" alt=""><span><b>${esc(st.move)}</b><small>${esc(fx ? fx[1] : "")}</small><small>${st.bench ? "subs in · " : ""}${eff}${st.ok ? "" : WHY[st.why] || ""}</small></span></button>`;
  }).join("");
  const over = L.done, run = G.ctx.run, ownRun = run.team === 0 && run.pts - run.opp >= 5;
  box.innerHTML = `
    <div class="phsb">
      <div class="s0"><small>${esc(L.names[0])}</small><b>${G.score[0]}</b></div>
      <div class="phclock">${over ? "FINAL" : `${qLabel(G)} ${clock(pre.secLeft)}`}<small>TO ${G.ctx.tos[0]} · calls ${C.left[0]} &nbsp;|&nbsp; calls ${C.left[1]} · TO ${G.ctx.tos[1]}</small></div>
      <div class="s1"><small>${esc(L.names[1])}</small><b>${G.score[1]}</b></div>
    </div>
    <div class="phfloor"><div>${onFloor(0)}${act(0)}</div><div>${onFloor(1)}${act(1)}</div></div>
    ${over ? `<div id="phLiveEnd"></div>` : `<div class="phgroup">Your playbook <small>one call at a time · starts next possession</small></div>
    <div class="phbook">${book}</div>
    <div class="phactions"><button class="btn ghost${ownRun ? " warn" : ""}" id="phTO" ${G.ctx.tos[0] > 0 && !C.timeout[0] ? "" : "disabled"}>⏱ Timeout${C.timeout[0] ? " (next dead ball)" : ownRun ? ` (ends your own ${run.pts}-${run.opp} run)` : ""}</button>
      <button class="btn ghost${L.speed === 1 ? " on" : ""}" data-speed="1">1×</button><button class="btn ghost${L.speed === 3 ? " on" : ""}" data-speed="3">3×</button>
      <button class="btn ghost" data-speed="99">Sim to end ⏭</button><button class="btn ghost" id="phLeave">Leave game</button></div>`}
    <ul class="phfeed">${L.feed.slice(0, over ? 12 : 30).map(([t, c, st, side]) => `<li class="${c}${side != null ? ` s${side}` : ""}">${st ? `<span>${st}${side != null ? ` · ${esc(L.names[side])}` : ""}</span>` : ""}${t}</li>`).join("")}</ul>`;
  if (over) liveEndPanel();
}

function liveEnd() {
  const L = state.live, G = L.G;
  L.done = true;
  const won = G.score[0] > G.score[1];
  L.record = botRecord(won ? 1 : -1);
  const g = { us: G.score[0], them: G.score[1], win: won, box: G.box, flow: L.flow, plays: L.plays, periods: G.q + G.ot };
  state.reveal = { series: { names: L.names, teams: L.teams, games: [g] }, done: true };
  renderLive();
}

function liveEndPanel() {
  const L = state.live, G = L.G, won = G.score[0] > G.score[1];
  $("phLiveEnd").innerHTML = `<div class="phgrade ${won ? "great" : "bad"}${REDUCE ? "" : " pop"}">${won ? "You win" : `${esc(L.names[1])} wins`} ${Math.max(...G.score)}–${Math.min(...G.score)}${G.ot ? ` (${G.ot > 1 ? G.ot : ""}OT)` : ""}</div>
    <div class="phmuted">You're ${L.record[0]}–${L.record[1]} against bots.</div>
    <div class="phactions"><button class="btn ghost" data-bot="${L.level}">Rematch (new ${BOT_LEVELS[L.level].name} team)</button>
      <button class="btn ghost" id="phBackLineup">Change lineup</button>
      ${state.draft?.token && !state.draft.expired ? `<button class="btn primary" id="phPlayFromLive">Play the season with this team</button>` : ""}</div>
    <div id="phDetail"></div>`;
  gameDetail(0, false);
}

async function openSeries(id) {
  const res = await getJSON(`${API}/series/${encodeURIComponent(id)}`);
  if (!res || res.error || res._status) { toast("That series link doesn't exist"); history.replaceState(null, "", location.pathname + (MOCK ? "?mock=1" : "")); return renderIntro(); }
  revealSeries(res);
}

async function challenge(vs) {
  const res = await getJSON(`${API}/season/${encodeURIComponent(vs)}`);
  if (!res || res.error || res._status) return toast("That team can't be challenged");
  if (res.me) return toast("That's your own team — send the link to a friend");
  history.replaceState(null, "", location.pathname + (MOCK ? "?mock=1" : ""));
  startDraft({ id: res.id, name: res.name, team: res.team });
}

// ── Events ───────────────────────────────────────────────────────────────────
let battle = null;   // the battle UI module, loaded on first use
async function startBattle() {
  battle ||= await import("./pokehoops-battle-ui.js");
  clearTimeout(state.reveal?.timer); clearTimeout(state.live?.timer); state.live = null; state.duel = null;
  battle.start({ app, head, toast, exit: () => { battle.stop(); renderIntro(); } });
  scrollTo(0, 0);
}

app.addEventListener("click", (e) => {
  if (e.target.closest("#pbStart")) return startBattle();
  if (e.target.closest("#pbExit")) { battle?.stop(); return renderIntro(); }
  if (battle?.active() && battle.handle(e)) return;
  const card = e.target.closest(".phcard");
  if (card) return state.duel?.view?.phase === "draft" ? duelPost("pick", { n: Number(card.dataset.n) }) : pick(Number(card.dataset.n));
  const bk = e.target.closest(".phbk");
  if (bk && state.draft?.team) { state.draft.team.backs[Number(bk.dataset.bk)] = Number(bk.dataset.s); return renderLineup(); }
  const row = e.target.closest(".phcrow");
  if (row) return tapSlot(Number(row.dataset.k));
  if (e.target.closest("#phStart")) return startDraft();
  const bot = e.target.closest("[data-bot]");
  if (bot) return startBotGame(Number(bot.dataset.bot));
  if (e.target.closest("#phRedraft")) return startDraft(state.draft?.vs || null);
  const call = e.target.closest(".phcall");
  if (call && state.live && !state.live.done) { if (queueCall(state.live.G, state.live.teams, 0, Number(call.dataset.call))) { toast("📣 Called — it starts next possession"); renderLive(); } return; }
  if (e.target.closest("#phTO") && state.live) { if (queueTimeout(state.live.G, 0)) renderLive(); return; }
  const sp = e.target.closest("[data-speed]");
  if (sp && state.live && !state.live.done) {   // reschedule, don't step: a tap must never skip a possession
    const L = state.live, v = Number(sp.dataset.speed);
    if (v === L.speed) return;
    L.speed = v; clearTimeout(L.timer);
    if (v >= 99) return liveStep();
    renderLive(); L.timer = setTimeout(liveStep, 600 / v); return;
  }
  if (e.target.closest("#phBackLineup") || e.target.closest("#phLeave") || e.target.closest("#phPlayFromLive")) {
    clearTimeout(state.live?.timer); state.live = null; state.reveal = null;
    if (e.target.closest("#phPlayFromLive")) return playIt();
    return renderLineup();
  }
  if (e.target.closest("#phGuest")) return playAsGuest();
  if (e.target.closest("#phPlay")) return playIt();
  if (e.target.closest("#phSkip")) return skip();
  if (e.target.closest("#phShare")) return share();
  if (e.target.closest("#phChallenge")) return challenge(state.reveal.sum.id);
  const rm = e.target.closest("#phRematch");
  if (rm) return challenge(Number(rm.dataset.vs));
  if (e.target.closest("#phXShare")) {
    const o = state.reveal.series, w = o.wins[0] > o.wins[1] ? 0 : 1;
    const text = `🏀 Poké Hoops — ${o.names[w]} beat ${o.names[1 - w]} ${Math.max(...o.wins)}–${Math.min(...o.wins)}\n${o.share.url}`;
    return navigator.clipboard.writeText(text).then(() => toast("📋 Copied — paste it anywhere"), () => prompt("Copy the series:", text));
  }
  const sg = e.target.closest("[data-sg]"), r = state.reveal;
  if (sg && r?.sum && Number(sg.dataset.sg) < r.g) {   // a season game already revealed: replay it logged for its box score
    const k = Number(sg.dataset.sg), g = seasonGame(r.team, r.seasonSeed, k), games = [];
    games[k] = g;
    gameDetail(k, true, { names: [r.owner || state.me?.user?.username || "You", teamName(g.opp)], teams: [r.team, LEAGUE[g.opp]], games });
    return document.querySelectorAll("[data-sg]").forEach((el) => el.classList.toggle("on", el === sg));
  }
  const gm = e.target.closest(".phgame");
  if (gm) return gameDetail(Number(gm.dataset.g));
  if (e.target.closest("#phDetailX")) { $("phDetail").innerHTML = ""; return document.querySelectorAll(".phgame.on, [data-sg].on").forEach((el) => el.classList.remove("on")); }
  if (e.target.closest("#phDuelLeave")) return duelLeave();
  if (e.target.closest("#phHome")) return renderIntro();
  if (e.target.closest("#phDuelNew")) { if (state.duel) duelForget(state.duel); return duelOpen("", true); }
  if (e.target.closest("#phDuelJoin")) { const c = $("phCode")?.value.trim(); return c ? duelOpen(c) : toast("Enter the room code"); }
  const go = e.target.closest("#phDuelGo");
  if (go) return duelOpen(go.dataset.code);
  const dl = e.target.closest("#phDuelLink");
  if (dl) {
    const link = dl.dataset.link;
    if (navigator.share && matchMedia("(pointer: coarse)").matches) return navigator.share({ text: "Poké Hoops duel — join me:", url: link }).catch(() => {});
    return navigator.clipboard.writeText(link).then(() => toast("📋 Link copied"), () => prompt("Send this link:", link));
  }
  const side = e.target.closest(".phside");
  if (side) { state.betSide = Number(side.dataset.side); document.querySelectorAll(".phside").forEach((b) => b.classList.toggle("on", b === side)); return payoutHint(); }
  if (e.target.closest("#phBet")) {
    const stake = Math.floor(Number($("phStake")?.value || 0)), v = state.duel?.view;
    if (!(stake >= 1)) return toast("Enter a stake");
    return duelPost("ready", { side: state.betSide ?? v.seat, stake });
  }
  if (e.target.closest("#phNoBet")) return duelPost("ready");
  const xl = e.target.closest(".phchal");
  if (xl) { e.preventDefault(); history.replaceState(null, "", `?x=${xl.dataset.xid}${MOCK ? "&mock=1" : ""}`); clearTimeout(state.reveal?.timer); scrollTo(0, 0); return openSeries(xl.dataset.xid); }
  const pb = e.target.closest("#phPost");
  if (pb) return postToDiscord(pb);
  const tb = e.target.closest(".phlbtab");
  if (tb) return loadLeaderboard(tb.dataset.period);
  if (e.target.closest("#phAgain")) { history.replaceState(null, "", location.pathname + (MOCK ? "?mock=1" : "")); return startDraft(); }
  const lb = e.target.closest(".phlbrow");
  if (lb) { e.preventDefault(); history.replaceState(null, "", `?s=${lb.dataset.sid}${MOCK ? "&mock=1" : ""}`); clearTimeout(state.reveal?.timer); openShared(lb.dataset.sid); scrollTo(0, 0); }
});

app.addEventListener("input", (e) => {
  if (e.target.id === "phStake") { state.betStake = e.target.value; payoutHint(); }
  if (e.target.id === "phName") state.duelName = e.target.value.trim();
});
app.addEventListener("keydown", (e) => {
  const row = e.target.closest?.(".phcrow");
  if (row && (e.key === "Enter" || e.key === " ") && e.target === row) { e.preventDefault(); tapSlot(Number(row.dataset.k)); }
});
// desktop drag-and-drop: drop one player onto another to swap them
app.addEventListener("dragstart", (e) => {
  const row = e.target.closest?.(".phcrow");
  if (row) { e.dataTransfer.setData("text/plain", row.dataset.k); e.dataTransfer.effectAllowed = "move"; }
});
app.addEventListener("dragover", (e) => { if (e.target.closest?.(".phcrow")) e.preventDefault(); });
app.addEventListener("drop", (e) => {
  const row = e.target.closest?.(".phcrow"), raw = e.dataTransfer.getData("text/plain");
  if (!row || raw === "" || !state.draft?.team) return;
  e.preventDefault();
  const from = Number(raw), to = Number(row.dataset.k);
  if (from !== to) swapSlots(from, to);
  state.draft.sel = null; renderLineup();
});

// Guest mode: the server's ph_guest cookie ("<token>:<name>") is the identity; we only read the name off it.
function guestMe() {
  const m = document.cookie.match(/(?:^|; )ph_guest=([^;]*)/);
  if (!m) return null;
  const v = decodeURIComponent(m[1]).replace(/^"|"$/g, ""), name = v.slice(v.indexOf(":") + 1);
  return name ? { guest: true, authenticated: false, user: { id: "guest", username: name, avatar: null } } : null;
}

async function playAsGuest() {
  const name = $("phGuestName")?.value.trim();
  if (!name) return toast("Enter a name");
  const res = await post("/guest", { name });
  if (res.error) return toast("❌ " + res.error);
  state.me = MOCK ? { guest: true, user: { id: "guest", username: res.name, avatar: null } } : guestMe();
  if (!state.me) return toast("Couldn't save your guest name — are cookies blocked?");
  renderNav(state.me.user);
  renderIntro();
}

navRight.addEventListener("click", (e) => {
  if (!e.target.closest("#phGuestOut")) return;
  document.cookie = "ph_guest=; Max-Age=0; path=/";
  state.me = null; renderNav(null); renderIntro();
});

async function main() {
  const me = await getJSON("/api/v1/hq/me");
  state.me = me && me.authenticated ? me : guestMe();
  state.balance = state.me && !state.me.guest ? me.balance || 0 : 0;
  renderNav(state.me && state.me.user);
  const qs = new URLSearchParams(location.search);
  if (qs.get("duel")) return duelOpen(qs.get("duel"));
  if (qs.get("d")) return openDuelRecord(qs.get("d"));
  if (qs.get("x")) return openSeries(qs.get("x"));
  if (qs.get("s")) return openShared(qs.get("s"));
  const saved = loadDraft();
  if (saved) { state.draft = saved; touchDraft(saved); return renderDraft(); }
  renderIntro();
}

// ── Mock mode (?mock=1): local seeds + local season so the page runs without the API ──
const mockDb = { seasons: [], best: null, series: [] };
function mock(url, body) {
  if (url.startsWith("/api/v1/hq/me")) return { authenticated: true, user: { id: "1", username: "davidj", avatar: null }, balance: 12500 };
  const rnd = () => Math.floor(Math.random() * 2 ** 32);
  if (url.endsWith("/start")) return { token: "mock", seed: rnd() };
  if (url.endsWith("/submit") && body.vs != null) {
    const ss = rnd(), opp = [...mockDb.seasons, ...MOCK_LB].find((r) => r.id === body.vs);
    const x = playSeries(state.draft.seed, body.picks, body.team, opp.team, ss);
    const row = { id: mockDb.series.length + 1, vs: body.vs, seed: state.draft.seed, series_seed: ss, picks: body.picks, team: x.team,
                  wins: x.wins, losses: x.losses, name: "davidj", opp_name: opp.name || "davidj", opp_team: opp.team };
    mockDb.series.push(row);
    return row;
  }
  if (url.includes("/series/")) return mockDb.series.find((x) => x.id === Number(url.split("/").pop())) || { _status: 404, error: "no such series" };
  if (url.endsWith("/submit")) {
    const ss = rnd(), s = playSeason(state.draft.seed, body.picks, body.team, ss), sum = summarize(s.team, s);
    const prev = mockDb.best;
    const row = { ...sum, id: mockDb.seasons.length + 1, seed: state.draft.seed, season_seed: ss, picks: body.picks };
    mockDb.seasons.push(row);
    if (!prev || sum.wins > prev.wins) mockDb.best = row;
    state.balance += 25;
    return { ...row, prev_best: prev ? prev.wins : null, is_new_best: !prev || sum.wins > prev.wins, rank: 2, coins: 25, balance: state.balance };
  }
  if (url.includes("/season/")) {
    const id = Number(url.split("/").pop()), row = mockDb.seasons.find((x) => x.id === id) || MOCK_LB[id - 101];
    if (id === 999) return { _status: 410, error: "old-rules season" };
    return row ? { ...row, name: row.name || "davidj", me: !row.name, challenges: mockDb.series.filter((x) => x.vs === id).map((x) => ({ id: x.id, name: x.name, wins: x.wins, losses: x.losses })) } : { _status: 404, error: "no such season" };
  }
  if (url.includes("/share/")) return { ok: true };
  if (url.endsWith("/guest")) return { name: body.name };
  if (url.includes("/leaderboard")) {
    const mine = mockDb.best ? [{ ...mockDb.best, name: "davidj", me: true }] : [];
    const top = [...MOCK_LB, ...mine].sort((a, b) => b.wins - a.wins).map((r, i) => ({ ...r, rank: i + 1 }));
    return { game: "pokehoops", top };
  }
  return {};
}
const MOCK_LB = ["steph", "nova", "rohan"].map((name, i) => {   // legal mock drafts, draft-order lineups
  const seed = 11 * (i + 1), picks = [];
  for (let p; (p = nextPack(seed, picks)); ) picks.push(p[i % 3]);
  const s = playSeason(seed, picks, defaultTeam(picks), 7 + i);
  return { id: 101 + i, name, seed, picks, season_seed: 7 + i, ...summarize(s.team, s) };
});

main();
