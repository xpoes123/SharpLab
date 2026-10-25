// SharpLab HQ — Poké Hoops. Draft eight Gen 1–2 Pokémon (3 options a pick, a Legendary first), then coach:
// five starters at PG..C + three reserves, each backing up a spot. Then an 82-game season against the TV
// cartridge's league. 82-0 is the dream. Overall ratings are never shown — skills, positions, types, moves only.
//
// The draft runs here from a server-issued seed (POST /start); every pack comes from that seed via
// pokehoops-draft.js, the same file the server replays to check the picks. POST /submit sends only
// the token + picks + team: the server verifies them, sims the season under a season seed it draws only
// now, records it, and returns the summary + season seed. We then replay that exact season locally
// (same vendored engine, same seeds) for the game-by-game reveal. ?s=<id> replays a shared season.
import { nextPack, playSeason, summarize, teamName, defaultTeam, PICKS, POS, mon, bestPos, LEGENDS } from "./pokehoops-draft.js";
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

function pickSlots(picks) {
  return `<div class="phslots">${Array.from({ length: PICKS }, (_, i) => i).map((i) => {
    const n = picks[i];
    return n ? `<div class="phslot full"><img src="${sprite(n)}" alt="${esc(mon(n).name)}" title="${esc(mon(n).name)}"></div>`
             : `<div class="phslot${i === picks.length ? " next" : ""}">${i === 0 ? "★" : i + 1}</div>`;
  }).join("")}</div>`;
}

const head = (sub = "") => `<div class="phhead"><h1>🏀 Poké Hoops</h1>${sub ? `<p>${sub}</p>` : ""}</div>`;

// ── Views ────────────────────────────────────────────────────────────────────
function renderIntro() {
  state.draft = null;
  const signedOut = !state.me ? `<p class="muted">Sign in with Discord to draft — your best season goes on the board.</p>
    <a class="btn big" href="/api/v1/auth/discord/login">Sign in with Discord</a>` : "";
  app.innerHTML = `${head("Draft eight Pokémon. Coach the lineup. Play 82 games against the league. Go 82–0.")}
    <div class="card phintro">
      <div class="phlegends">${LEGENDS.map((n) => `<img src="${sprite(n)}" alt="">`).join("")}</div>
      <ol class="phrules">
        <li><b>Pick 1</b> is a Legendary — three of the eleven are on offer.</li>
        <li><b>Picks 2–8</b>: three options each, take one. Skills, type and signature move all matter.</li>
        <li><b>You coach:</b> set your five at PG · SG · SF · PF · C and pick which spot each of your three reserves backs up.</li>
        <li>Then play an <b>82-game season</b>.</li>
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
  if (!pack) { d.team = d.team || defaultTeam(d.picks); d.sel = null; return renderLineup(); }
  const i = d.picks.length;
  app.innerHTML = `${head()}
    <div class="phdraft">
      <div class="phdrafthead">
        <div><div class="phstep">Pick ${i + 1} of ${PICKS}${i === 0 ? " · Legendary" : ""}</div>
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
  app.innerHTML = `${head()}
    <div class="card phlineup">
      <div><div class="phstep">You're the coach</div>
        <div class="phprompt">Set your lineup</div>
        <div class="phmuted">${hint}</div></div>
      <div class="phgroup">Starting five</div>
      <div class="phrows">${[0, 1, 2, 3, 4].map((k) => coachRow(t, k, d.sel)).join("")}</div>
      <div class="phgroup">Bench <small>subs in mid-quarter behind the spot it backs up · two at one spot split the minutes</small></div>
      <div class="phrows">${[5, 6, 7].map((k) => coachRow(t, k, d.sel)).join("")}</div>
      <button class="btn big primary" id="phPlay">Lock in &amp; play the season</button>
    </div>`;
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
  state.busy = true;
  const btn = $("phPlay");
  if (btn) { btn.disabled = true; btn.textContent = "Simming the season…"; }
  const res = await post("/submit", { token: d.token, picks: d.picks, team: d.team });
  state.busy = false;
  if (res.error) {
    toast("❌ " + res.error);
    if (/expired|illegal/.test(res.error)) return renderIntro();
    if (btn) { btn.disabled = false; btn.textContent = "Lock in & play the season"; }
    return;
  }
  if (res.balance != null) { state.balance = res.balance; renderNav(state.me?.user); }
  reveal(d.seed, d.picks, d.team, res, { mine: true });
}

// Replay the season locally from the seeds the server used, and animate it game by game.
function reveal(seed, picks, team, server, { mine = false, owner = null } = {}) {
  const s = playSeason(seed, picks, team, server.season_seed);
  const local = summarize(s.team, s);
  if (server.strip && server.strip !== local.strip) console.warn("pokehoops: local replay differs from server", server, local);
  const sum = { ...local, ...server, games: s.games };
  state.reveal = { sum, mine, owner, timer: 0, g: 0, done: false };
  app.innerHTML = `${head()}
    <div class="card phseason">
      <div class="phstep">${owner ? `${esc(owner)}'s season` : "The season"}</div>
      <div class="phrec"><span id="phW">0</span><i>–</i><span id="phL">0</span></div>
      <div class="phticker" id="phTick">Tip-off…</div>
      <div class="phstrip" id="phStrip">${sum.games.map(() => "<b></b>").join("")}</div>
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
      <div class="phstat"><span>Top scorer</span><div class="phts"><img src="${sprite(ts.n)}" alt=""><div><b>${esc(ts.name)}</b> <small>${ts.bench ? "off the bench" : ts.pos}</small><br>team-high in ${ts.led} games · season-high ${ts.high} pts</div></div></div>
      <div class="phstat"><span>Best game</span><b>${bg ? `G${bg.g}: ${bg.us}–${bg.them}` : "—"}</b><small>${bg ? `vs ${esc(bg.opp_name)}` : "no wins"}</small></div>
      <div class="phstat"><span>Longest streak</span><b>${s.streak}</b><small>straight wins</small></div>
      <div class="phstat"><span>Points</span><b>${s.ppg}</b><small>allowed ${s.oppg}</small></div>
    </div>
    <div class="phactions">
      ${s.id && r.mine ? `<button class="btn primary" id="phPost">Post to #bot</button>` : ""}
      ${s.id ? `<button class="btn ghost" id="phShare">Copy link</button>` : ""}
      ${state.me ? `<button class="btn${s.id ? " ghost" : " primary"}" id="phAgain">${r.mine ? "Draft again" : "Draft your own"}</button>`
                 : `<a class="btn" href="/api/v1/auth/discord/login">Sign in to draft your own</a>`}
    </div>`;
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
  reveal(res.seed, res.picks, res.team, { ...res, id: res.id }, { owner: res.name });
}

// ── Events ───────────────────────────────────────────────────────────────────
app.addEventListener("click", (e) => {
  const card = e.target.closest(".phcard");
  if (card) return pick(Number(card.dataset.n));
  const bk = e.target.closest(".phbk");
  if (bk && state.draft?.team) { state.draft.team.backs[Number(bk.dataset.bk)] = Number(bk.dataset.s); return renderLineup(); }
  const row = e.target.closest(".phcrow");
  if (row) return tapSlot(Number(row.dataset.k));
  if (e.target.closest("#phStart")) return startDraft();
  if (e.target.closest("#phPlay")) return playIt();
  if (e.target.closest("#phSkip")) return skip();
  if (e.target.closest("#phShare")) return share();
  const pb = e.target.closest("#phPost");
  if (pb) return postToDiscord(pb);
  const tb = e.target.closest(".phlbtab");
  if (tb) return loadLeaderboard(tb.dataset.period);
  if (e.target.closest("#phAgain")) { history.replaceState(null, "", location.pathname + (MOCK ? "?mock=1" : "")); return startDraft(); }
  const lb = e.target.closest(".phlbrow");
  if (lb) { e.preventDefault(); history.replaceState(null, "", `?s=${lb.dataset.sid}${MOCK ? "&mock=1" : ""}`); clearTimeout(state.reveal?.timer); openShared(lb.dataset.sid); scrollTo(0, 0); }
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
    return row ? { ...row, name: row.name || "davidj" } : { _status: 404, error: "no such season" };
  }
  if (url.includes("/share/")) return { ok: true };
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
