// Poké Hoops BATTLE (beta) — 3v3, first to 11, played like a Pokémon battle (engine: ./pokehoops/battle.js, vendored
// from the TV repo). Draft six, pick your three starters and the court, then every turn you and the bot choose at the
// same time and it resolves. Vs the bot it runs entirely in the browser. Vs a friend (openRoom) the server only relays:
// both phones post each turn's call, and once both are in each phone replays the game from the room seed.
import { newBattle, resolve, options, botPick, botSubs, sub, movesOf, shotChance, speedOf, weatherOf, SCHEMES, CAT, COURTS, WEATHER,
  STATUS_TXT, TO, ON, CLOCK, NO_BATTLE, canSubIn, bestEff } from "./pokehoops/battle.js";
import { seeded } from "./pokehoops/engine.js";
import { mon, LEGENDS } from "./pokehoops-draft.js";
import { profile, skills, typeEff, POOL } from "./pokehoops/ratings.js";

const ROSTER = 6;
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const sprite = (n) => `/pokehoops/sprites/${n}.png`;
const TYPE_COL = { normal: "#a8a77a", fire: "#ee8130", water: "#6390f0", electric: "#f7d02c", grass: "#7ac74c", ice: "#96d9d6",
  fighting: "#c22e28", poison: "#a33ea1", ground: "#e2bf65", flying: "#a98ff3", psychic: "#f95587", bug: "#a6b91a", rock: "#b6a136",
  ghost: "#735797", dragon: "#6f35fc", dark: "#705746", steel: "#b7b7ce", fairy: "#d685ad" };
const chip = (t) => `<span class="phtype" style="--tc:${TYPE_COL[t] || "#888"}">${t}</span>`;
const LEVELS = [{ name: "Rookie", temp: 0.25, draft: 0 }, { name: "Pro", temp: 0.08, draft: 0.6 }, { name: "All-Star", temp: 0.04, draft: 1, depth: 1 }];
const STAT_LAB = { shoot: "SHT", finish: "FIN", handle: "HND", perD: "PER-D", intD: "RIM-D", reb: "REB", spe: "SPE" };
const WX_ICON = { rain: "🌧", sun: "☀️", sand: "🌪" };
const ST_ICON = { slp: "💤", par: "⚡", cnf: "💫", psn: "☠️" };
const pref = (k, v) => { try { if (v !== undefined) localStorage.setItem("phb:" + k, JSON.stringify(v)); return JSON.parse(localStorage.getItem("phb:" + k)); } catch { return v ?? null; } };

let S = null, ctx = null;
const $ = (id) => document.getElementById(id);
const ME = () => S.room?.seat ?? 0, OP = () => 1 - ME();   // engine sides are seats; vs the bot you're side 0

export function start(c) {
  ctx = c;
  const seed = (Math.random() * 2 ** 32) >>> 0;
  S = { phase: "draft", seed, picks: [], starters: [], court: pref("court") || "clear", level: pref("level") ?? 1, hints: pref("hints") ?? false,
        log: [], record: pref("record") || [0, 0] };
  render();
}

// ── draft six: a Legendary first, then three of similar strength from the battle pool (no moveset-less Pokémon).
// Its own packs, not the season's: nothing here is replayed by the server. Seeded per pick, so re-renders agree. ──
const skillSum = (n) => Object.values(skills(n)).reduce((a, b) => a + b, 0);
const BATTLE_POOL = POOL.filter((n) => !NO_BATTLE.has(n));
function nextPack(seed, picks) {
  if (picks.length >= ROSTER) return null;
  const rng = seeded((seed + 7919 * picks.length) >>> 0);
  if (!picks.length) return rng.shuffle(LEGENDS.slice()).slice(0, 3);
  const pool = BATTLE_POOL.filter((n) => !picks.includes(n)), target = skillSum(pool[rng.int(pool.length)]);
  return rng.shuffle(pool.slice().sort((a, b) => Math.abs(skillSum(a) - target) - Math.abs(skillSum(b) - target)).slice(0, 8)).slice(0, 3);
}
function botDraft(rng, draftSkill) {
  const seed = (rng.float() * 2 ** 32) >>> 0, picks = [];
  while (picks.length < ROSTER) {
    const pack = nextPack(seed, picks).slice().sort((a, b) => skillSum(b) - skillSum(a));
    picks.push(rng.float() < draftSkill ? pack[0] : pack[rng.int(pack.length)]);
  }
  const starters = picks.map((n, i) => [i, skillSum(n)]).sort((a, b) => b[1] - a[1]).slice(0, ON).map(([i]) => i);
  return { picks, starters };
}

function render() {
  if (S.phase === "room") return;
  if (S.phase === "draft") return renderDraft();
  if (S.phase === "setup") return renderSetup();
  return renderBattle();
}

function moveChip(m) {
  return `<span class="pbmv" style="--tc:${TYPE_COL[m.type] || "#888"}"><b>${esc(m.name)}</b><small>${CAT[m.cat]}${m.sig ? " · signature" : ""}</small></span>`;
}
function card(n, i) {
  const k = skills(n), bars = Object.entries(STAT_LAB).filter(([s]) => s !== "spe").map(([s, lab]) => `<div class="phbar"><span>${lab}</span><i><b style="width:${Math.round(k[s] * 99)}%"></b></i><em>${Math.round(k[s] * 99)}</em></div>`).join("");
  return `<button class="phcard${LEGENDS.includes(n) ? " legend" : ""}" data-bpick="${n}" style="--d:${i * 70}ms">
    <div class="phspr"><img src="${sprite(n)}" alt="" width="96" height="96"></div>
    <div class="phinfo"><div class="phtop"><span class="phname">${esc(mon(n).name)}</span><span class="phplays">SPE ${mon(n).spe}</span></div>
      <div class="phtypes">${mon(n).types.map(chip).join("")}</div>
      <div class="pbmoves">${movesOf(n).map(moveChip).join("")}</div>
      <div class="phbars">${bars}</div></div></button>`;
}

function renderDraft() {
  const pack = S.picks.length < ROSTER ? nextPack(S.seed, S.picks) : null;
  if (!pack) { S.phase = "setup"; S.starters = [0, 1, 2]; render(); return scrollTo(0, 0); }
  ctx.app.innerHTML = `${ctx.head("⚔️ Battle · 3v3 to 11")}<div class="phdraft"><button class="btn ghost pbexit" id="pbExit">← Exit</button>
    <div class="phdrafthead"><div><div class="phstep">Pick ${S.picks.length + 1} of ${ROSTER}${S.picks.length ? "" : " · Legendary"}</div>
      <div class="phprompt">${S.picks.length ? "Take one" : "Choose your Legendary"}</div>
      <div class="phmuted">Three start, three come off the bench. Their four moves are what you'll call.</div></div>
      <div class="phslots">${Array.from({ length: ROSTER }, (_, i) => S.picks[i] ? `<div class="phslot full"><img src="${sprite(S.picks[i])}" alt=""></div>` : `<div class="phslot${i === S.picks.length ? " next" : ""}">${i + 1}</div>`).join("")}</div></div>
    <div class="phpack">${pack.map(card).join("")}</div>
    ${S.picks.length ? `<div class="phroster"><div class="phgroup">Your picks</div><div class="pbroster">${S.picks.map((n) => `<div class="pbmini"><img src="${sprite(n)}" alt=""><div><b>${esc(mon(n).name)}</b> ${mon(n).types.map(chip).join("")}<div class="pbmoves">${movesOf(n).map(moveChip).join("")}</div></div></div>`).join("")}</div></div>` : ""}
  </div>`;
}

function renderSetup() {
  const lvl = LEVELS.map((l, i) => `<button class="btn ghost${S.level === i ? " on" : ""}" data-blevel="${i}">${l.name}</button>`).join("");
  const courts = Object.entries(COURTS).map(([k, v]) => `<button class="btn ghost${S.court === k ? " on" : ""}" data-bcourt="${k}">${WX_ICON[k] || "🏟"} ${v}</button>`).join("");
  ctx.app.innerHTML = `${ctx.head("⚔️ Battle · 3v3 to 11")}<div class="card pbsetup"><button class="btn ghost pbexit" id="pbExit">← Exit</button>
    <div class="phstep">Your starting three · ${S.starters.length}/${ON} picked</div><div class="phmuted">Tap to choose three. The other three are your subs (swap at any dead ball; resting cures status but loses boosts).</div>
    <div class="pbroster">${S.picks.map((n, i) => `<button class="pbmini pick${S.starters.includes(i) ? " on" : ""}" data-bstart="${i}"><img src="${sprite(n)}" alt=""><div>${S.starters.includes(i) ? `<span class="pbbadge">Starter</span> ` : ""}<b>${esc(mon(n).name)}</b> ${mon(n).types.map(chip).join("")} <small>SPE ${mon(n).spe}</small><div class="pbmoves">${movesOf(n).map(moveChip).join("")}</div></div></button>`).join("")}</div>
    ${S.room ? `<div class="phmuted">Court: ${WX_ICON[S.court] || "🏟"} ${COURTS[S.court]}. ${esc(S.botName)} can't see your six until you both lock in.</div>` : `<div class="phgroup">Court</div><div class="phlevels">${courts}</div>
    <div class="phmuted">${S.court === "clear" ? "No weather. Weather moves still change it for a few turns." : esc(WEATHER[S.court])}</div>
    <div class="phgroup">Opponent</div><div class="phlevels">${lvl}</div>`}
    <label class="pbhint"><input type="checkbox" id="pbHints" ${S.hints ? "checked" : ""}> Show hints: type matchups and make % <small>(off = you need to know the chart)</small></label>
    <div class="phmuted pblegend">SHT deep shooting · FIN finishing at the rim · HND handles (passes, steals) · PER-D perimeter D · RIM-D rim protection · REB rebounding · SPE speed (who acts first)</div>
    <button class="btn big primary" id="pbGo" ${S.starters.length === ON ? "" : "disabled"}>${S.starters.length === ON ? (S.room ? "Lock in my six" : "Tip off") : `Pick ${ON - S.starters.length} more starter${ON - S.starters.length > 1 ? "s" : ""}`}</button>
  </div>`;
}

function tipOff() {
  const rng = seeded((Math.random() * 2 ** 32) >>> 0), L = LEVELS[S.level], bot = botDraft(rng, L.draft);
  const order = [...S.starters, ...S.picks.map((_, i) => i).filter((i) => !S.starters.includes(i))];
  S.rng = rng; S.phase = "battle"; S.log = []; S.sel = null; S.botName = `${L.name} bot`;
  S.B = newBattle([order.map((i) => S.picks[i]), bot.picks], [[0, 1, 2], bot.starters], rng, S.court);
  S.pts = [{}, {}];
  S.last = [{ t: `Tip-off on a ${COURTS[S.court].toLowerCase()} court. ${S.B.off === 0 ? "You get" : "They get"} the ball first.`, c: "q" }];
  S.log.unshift(S.last);
  render(); scrollTo(0, 0);
}

// ── the battle ──
const nm = (side, ri) => esc(mon(S.B.rosters[side][ri]).name);
const onNm = (side, slot) => nm(side, S.B.on[side][slot]);
function effWord(e) { return e >= 2 ? "It's super effective!" : e === 0 ? "It barely affects the defender…" : e < 1 ? "It's not very effective…" : ""; }

function narrate(ev) {
  const B = S.B, lines = [];
  const side = (s) => (s === ME() ? "You" : esc(S.botName));
  for (const e of ev) {
    if (e.k === "pick") {
      const o = B.off, describe = (s, p) => {
        if (p.kind === "scheme") return `${SCHEMES[p.scheme].name}`;
        if (p.kind === "pass") return `${onNm(s, p.slot)} passes to ${onNm(s, p.to)}`;
        if (p.kind === "heave") return `${onNm(s, p.slot)} heaves it`;
        if (p.kind === "iso") return `${onNm(s, p.slot)} calls for ${onNm(1 - s, p.to)} on an iso`;
        if (p.kind === "hold") return "holds";
        const m = movesOf(B.rosters[s][B.on[s][p.slot]])[p.mi];
        return `${onNm(s, p.slot)} uses <b>${esc(m.name)}</b>${["deep", "rim"].includes(m.cat) ? "" : ` <small>(${CAT[m.cat]})</small>`}${p.to !== undefined ? ` → ${onNm(s, p.to)}` : ""}`;
      };
      lines.push({ t: `<span class="pbo">${side(o)}:</span> ${describe(o, e.off)} &nbsp;·&nbsp; <span class="pbd">${side(1 - o)}:</span> ${describe(1 - o, e.def)}`, c: "pick" });
    } else if (e.k === "shot") {
      const who = onNm(B.off, e.slot), w = effWord(e.eff);
      lines.push({ t: `${w ? `<i>${w}</i> ` : ""}${e.made ? `<b>${who} scores!</b> +${e.pts}${e.andOne ? " (and one!)" : ""}` : e.ft ? `${who} misses but draws the foul — <b>hits the free throw</b> +${e.pts}` : `${who} misses${e.fouled ? " (fouled — misses the free throw)" : ""}.`}`, c: e.scored ? "big" : "dim" });
    } else if (e.k === "pass") lines.push({ t: `${onNm(B.off, e.to)} catches it${e.gain >= 2 ? " wide open" : e.gain ? " with room" : ", covered"}.`, c: "" });
    else if (e.k === "screen") lines.push({ t: e.switched ? `They switch the screen — mismatch!` : `The screen frees ${onNm(B.off, B.ball)}${e.gain >= 2 ? " completely" : ""}.`, c: "" });
    else if (e.k === "iso") lines.push({ t: `Clear out! ${onNm(B.off, e.slot)} gets ${onNm(1 - B.off, e.g)} one-on-one${e.eff >= 2 ? " — a type mismatch!" : ""}${e.gain ? " and has room" : ""}.`, c: e.eff >= 2 ? "call" : "" });
    else if (e.k === "fastBreak") lines.push({ t: `<b>Fast break!</b> ${onNm(e.side, e.slot)} is out in front. 💨`, c: "call" });
    else if (e.k === "confusedSelf") lines.push({ t: `${onNm(B.off, e.slot)} is confused… it hurt itself in its confusion!`, c: "dim" });
    else if (e.k === "turnover") lines.push({ t: e.why === "steal" ? `<b>Stolen!</b>` : e.why === "confused" ? `${onNm(B.off, e.slot)} is confused… throws it away!` : "Turnover.", c: "neg" });
    else if (e.k === "oreb") lines.push({ t: `Offensive rebound, ${onNm(B.off, e.slot)}! Shot clock reset.`, c: "" });
    else if (e.k === "dreb") lines.push({ t: `Rebound ${onNm(1 - B.off, e.slot)}.`, c: "dim" });
    else if (e.k === "status") lines.push({ t: `${onNm(e.tSide, e.tSlot)} is ${STATUS_TXT[e.st]}! ${ST_ICON[e.st]}`, c: "call" });
    else if (e.k === "statusMiss") lines.push({ t: e.immune ? `${onNm(e.tSide, e.tSlot)} is immune!` : e.failed ? `${esc(e.move)}… but it failed! (already ${STATUS_TXT[S.B.status[e.tSide][S.B.on[e.tSide][e.tSlot]]?.k] || "statused"})` : `${esc(e.move)} missed!`, c: "dim" });
    else if (e.k === "weather") lines.push({ t: `${WX_ICON[e.wx]} ${esc(WEATHER[e.wx])}.`, c: "call" });
    else if (e.k === "weatherEnd") lines.push({ t: `The ${e.wx === "sand" ? "sandstorm" : e.wx === "rain" ? "rain" : "sun"} fades.`, c: "dim" });
    else if (e.k === "boost" || e.k === "debuff") {
      const tgt = e.k === "boost" ? onNm(e.side, e.slot) : onNm(e.tSide, e.tSlot), got = Object.entries(e.got);
      lines.push({ t: got.length ? `${tgt}'s ${got.map(([s, v]) => `${STAT_LAB[s]} ${v > 0 ? "rose" : "fell"}${Math.abs(v) >= 2 ? " sharply" : ""}`).join(", ")}!` : `${tgt}'s stats won't go any ${e.k === "boost" ? "higher" : "lower"}!`, c: "call" });
    } else if (e.k === "asleep") lines.push({ t: `${onNm(B.off, e.slot)} is fast asleep. 💤`, c: "dim" });
    else if (e.k === "fullyPar") lines.push({ t: `${onNm(B.off, e.slot)} is paralyzed! It can't move!`, c: "dim" });
    else if (e.k === "violation") lines.push({ t: "Shot-clock violation!", c: "neg" });
    else if (e.k === "cured") lines.push({ t: `${nm(e.side, e.ri)} ${e.st === "slp" ? "woke up" : "snapped out of it"}.`, c: "dim" });
  }
  return lines;
}

// one turn: resolve, narrate (after any `pre` lines, e.g. subs), log, tally scorers
function step(offPick, defPick, pre = []) {
  const before = S.B;
  const { B: next, ev } = resolve(before, offPick, defPick, S.rng);
  const lines = [...pre, ...narrate(ev)];   // narrate with the pre-turn floor (names by slot), then advance
  S.B = next;
  if (next.winner < 0 && (next.off !== before.off || next.dead)) lines.push({ t: `${next.score[ME()]}–${next.score[OP()]} · ${next.off === ME() ? "your ball" : "their ball"}`, c: "q" });
  for (const e of ev) if (e.k === "shot" && e.scored) { const ri = before.on[before.off][e.slot]; S.pts[before.off][ri] = (S.pts[before.off][ri] || 0) + e.pts; }
  S.log.unshift(lines);
  S.log.length = Math.min(S.log.length, 60);
  S.last = lines;
  if (next.winner >= 0) S.record[next.winner === ME() ? 0 : 1]++;
}
const scrollLast = () => document.querySelector(".pblast")?.scrollIntoView({ block: "nearest", behavior: "smooth" });

function act(myPick) {
  if (S.room) return roomMove(myPick);
  const B = S.B, botP = botPick(B, 1, S.rng, LEVELS[S.level].temp, LEVELS[S.level].depth || 0);
  step(B.off === 0 ? myPick : botP, B.off === 0 ? botP : myPick);
  if (S.B.dead && S.B.winner < 0) S.B = botSubs(S.B, 1);
  if (S.B.winner >= 0) pref("record", S.record);
  S.sel = null;
  render(); scrollLast();
}

function playerCard(side, slot) {
  const B = S.B, ri = B.on[side][slot], n = B.rosters[side][ri], st = B.status[side][ri], stg = B.stage[side][ri], off = side === B.off;
  const ball = off && B.ball === slot, guard = off ? B.guard[slot] : B.guard.indexOf(slot);
  const stages = Object.entries(stg).filter(([, v]) => v).map(([k, v]) => `<span class="${v > 0 ? "up" : "dn"}">${STAT_LAB[k]} ${v > 0 ? "+" : ""}${v}</span>`).join("");
  return `<div class="pbp${ball ? " ball" : ""}${side === ME() && S.sel === slot ? " sel" : ""}" ${side === ME() && B.dead && B.winner < 0 && !S.room?.waiting ? `data-bcourtslot="${slot}"` : ""}>
    ${ball ? `<span class="pbball">🏀</span>` : ""}<img src="${sprite(n)}" alt="">
    <div class="pbpn"><b>${esc(mon(n).name)}</b>${st ? ` ${ST_ICON[st.k]}` : ""}</div>
    <div class="pbtypes">${mon(n).types.map(chip).join("")}</div>
    <div class="pbstam" title="stamina ${Math.round(B.stam[side][ri])}%"><i style="width:${B.stam[side][ri]}%"></i></div>
    ${stages ? `<div class="pbstg">${stages}</div>` : ""}
    ${off ? `<div class="pbopen" title="how open: each ● makes the next shot better">open ${"●".repeat(B.open[slot])}${"○".repeat(3 - B.open[slot])}</div>` : ""}
    <small class="pbguard">${off ? `guarded by ${onNm(1 - side, guard)}` : `on ${onNm(1 - side, guard)}`}</small>
  </div>`;
}

function optionButtons() {
  const B = S.B, opts = options(B, ME()), mine = B.off === ME();
  if (mine) {
    const h = B.ball, groups = {};
    for (const op of opts) {
      const slot = op.kind === "pass" || op.kind === "heave" ? h : op.slot ?? h;
      (groups[slot] ||= []).push(op);
    }
    return Object.entries(groups).sort(([a], [b]) => (Number(a) === h ? -1 : Number(b) === h ? 1 : a - b)).map(([slot, ops]) =>
      `<div class="pbgrp"><div class="phgroup">${Number(slot) === h ? `🏀 ${onNm(ME(), h)}` : onNm(ME(), slot)}</div><div class="pbopts">${ops.map(optBtn).join("")}</div></div>`).join("");
  }
  const schemes = opts.filter((o) => o.kind === "scheme"), moves = opts.filter((o) => o.kind === "move");
  return `<div class="pbgrp"><div class="phgroup">Defense</div><div class="pbopts">${schemes.map(optBtn).join("")}</div></div>
    ${moves.length ? `<div class="pbgrp"><div class="phgroup">Or a defender's move <small>(you play man-to-man that turn)</small></div><div class="pbopts">${moves.map(optBtn).join("")}</div></div>` : ""}`;
}

function optBtn(op) {
  const B = S.B;
  if (op.kind === "scheme") return `<button class="pbopt" data-bopt="${op.id}"><b>${SCHEMES[op.scheme].name}</b><small>${SCHEMES[op.scheme].does}</small></button>`;
  if (op.kind === "pass") return `<button class="pbopt" data-bopt="${op.id}"><b>Pass → ${onNm(ME(), op.to)}</b><small>basic pass</small></button>`;
  if (op.kind === "heave") return `<button class="pbopt" data-bopt="${op.id}"><b>Heave it</b><small>out of shot PP</small></button>`;
  if (op.kind === "hold") return `<button class="pbopt" data-bopt="${op.id}"><b>Hold</b></button>`;
  if (op.kind === "iso") {
    const dn = B.rosters[OP()][B.on[OP()][op.to]], best = S.hints ? Math.max(...movesOf(B.rosters[ME()][B.on[ME()][B.ball]]).filter((m) => m.cat === "deep" || m.cat === "rim").map((m) => typeEff(m.type, dn))) : 1;
    return `<button class="pbopt iso" data-bopt="${op.id}"><b>Isolate vs ${onNm(OP(), op.to)}</b><small>${mon(dn).types.join("/")} · clear out, take that matchup</small>${best >= 2 ? `<em class="pos">your shots are super effective</em>` : best < 1 ? `<em class="neg">bad matchup</em>` : ""}</button>`;
  }
  const side = ME(), ri = B.on[side][op.slot], m = movesOf(B.rosters[side][ri])[op.mi], pp = B.pp[side][ri][op.mi];
  let hint = "";
  if (S.hints && (m.cat === "deep" || m.cat === "rim")) {
    const e = typeEff(m.type, B.rosters[OP()][B.on[OP()][B.guard[B.ball]]]);
    const pct = (sc) => Math.round(100 * shotChance(B, m, sc));
    hint = `<em>${pct("man")}% vs man</em> ` + (e >= 2 ? `<em class="pos">super effective</em>` : e === 0 ? `<em class="neg">barely affects (−15%)</em>` : e < 1 ? `<em class="neg">not very effective</em>` : "");
  }
  return `<button class="pbopt mv" style="--tc:${TYPE_COL[m.type] || "#888"}" data-bopt="${op.id}"><b>${esc(m.name)}${op.to !== undefined ? ` → ${onNm(ME(), op.to)}` : ""}</b>
    <small>${m.type} · ${CAT[m.cat]}${m.boost ? ` (${Object.entries(m.boost).map(([k, v]) => `${STAT_LAB[k]} ${v > 0 ? "+" : ""}${v}`).join(" ")})` : m.drop ? ` (${Object.entries(m.drop).map(([k, v]) => `${STAT_LAB[k]} ${v}`).join(" ")})` : m.st ? ` (${STATUS_TXT[m.st]})` : ""} · PP ${pp}/${m.pp}</small>${hint}</button>`;
}

function renderBattle() {
  const B = S.B, wx = weatherOf(B), over = B.winner >= 0, mineBall = B.off === ME(), me = ME(), op = OP(), R = S.room;
  const clock = Array.from({ length: CLOCK }, (_, i) => `<i class="${i < B.clock ? "on" : ""}"></i>`).join("");
  const bench = (side) => S.B.rosters[side].map((n, ri) => [n, ri]).filter(([, ri]) => !B.on[side].includes(ri));
  ctx.app.innerHTML = `${ctx.head()}<div class="card pbbattle"><button class="btn ghost pbexit" id="pbExit">← Exit</button>
    <div class="phsb"><div><small>You</small><b>${B.score[me]}</b></div>
      <div class="phclock">to ${TO}${wx ? ` · ${WX_ICON[wx]}` : ""}<small>${over ? "FINAL" : B.dead ? "dead ball" : `${mineBall ? "your ball" : "on defense"}`}</small>${over ? "" : `<span class="pbclock" title="shot clock: actions left">${clock}</span><small>shot clock</small>`}</div>
      <div><small>${esc(S.botName)}</small><b>${B.score[op]}</b></div></div>
    ${wx ? `<div class="phmuted pbwx">${WX_ICON[wx]} ${esc(WEATHER[wx])}${B.weather ? ` (${B.weather.t} turns)` : " (the court)"}</div>` : ""}
    <div class="pbcourt"><div class="pbside">${esc(S.botName)}</div><div class="pbrow them">${[0, 1, 2].map((s) => playerCard(op, s)).join("")}</div>
      <div class="pbside">You</div><div class="pbrow us">${[0, 1, 2].map((s) => playerCard(me, s)).join("")}</div></div>
    ${S.last?.length ? `<div class="pblast">${S.last.map((l) => `<div class="${l.c}">${l.t}</div>`).join("")}</div>` : ""}
    ${over ? `<div class="phgrade ${B.winner === me ? "great" : "bad"} pop">${B.winner === me ? "You win" : `${esc(S.botName)} wins`} ${Math.max(...B.score)}–${Math.min(...B.score)}</div>
        <div class="pbrecap">${[me, op].map((sd) => `<div><div class="phgroup">${sd === op ? esc(S.botName) : "You"}</div>${Object.entries(S.pts[sd]).sort((a, b) => b[1] - a[1]).map(([ri, p]) => `<div class="pbscorer"><img src="${sprite(B.rosters[sd][ri])}" alt=""> ${nm(sd, ri)} <b>${p}</b></div>`).join("") || `<div class="phmuted">no points</div>`}</div>`).join("")}</div>
        <div class="phmuted">${R ? `You ${S.record[0]}–${S.record[1]} ${esc(S.botName)} in this room.` : `You're ${S.record[0]}–${S.record[1]} in battles.`}</div>
        <div class="phactions">${R ? `<button class="btn primary" id="pbRematch">Rematch (same six)</button>` : `<button class="btn primary" id="pbAgain">Rematch (same six)</button><button class="btn ghost" id="pbNew">New draft</button>`}</div>`
      : R?.waiting ? `<div class="pbwait">Your call is in. Waiting for ${esc(S.botName)}…</div>`
      : `${B.dead ? `<details class="pbsubs"${S.sel != null ? " open" : ""}><summary>🔁 Dead ball: make a sub? <small>tap a player on the floor, then who comes in</small></summary>
          <div class="pbroster">${bench(me).map(([n, ri]) => `<button class="pbmini pick" data-bbench="${ri}" ${canSubIn(B, me, ri) ? "" : "disabled title=\"just came off: back next dead ball\""}><img src="${sprite(n)}" alt=""><div><b>${esc(mon(n).name)}</b> ${mon(n).types.map(chip).join("")}<div class="pbstam"><i style="width:${B.stam[me][ri]}%"></i></div></div></button>`).join("")}</div></details>` : ""}
        <div class="pbchoose"><div class="phstep">${mineBall ? "Your call on offense" : "Your call on defense"}${R?.view?.opp_pending ? ` <small class="pbready">· ${esc(S.botName)} has called it</small>` : ""}</div>${optionButtons()}</div>`}
    <details class="pblog"${S.open?.log ? " open" : ""}><summary data-bfold="log">Play-by-play</summary>${S.log.map((turn) => `<div class="pbturn">${turn.map((l) => `<div class="${l.c}">${l.t}</div>`).join("")}</div>`).join("")}</details>
    <details class="pbhelp"${S.open?.help ? " open" : ""}><summary data-bfold="help">How it works</summary><ul>
      <li>Both coaches pick at the same time. Offense: shoot (rim = 1, deep = 2), pass, or a teammate's screen / status / boost / weather move. Defense: a scheme, or a defender's move.</li>
      <li>Deep beats <b>Pack the paint</b>; the rim beats <b>Deny</b>; passes and screens beat the <b>Press</b>; <b>Deny</b> steals passes; <b>Switch</b> kills screens but can leave a mismatch.</li>
      <li><b>Isolate</b> clears out and calls for the defender you want: hunt a type mismatch or a tired, debuffed defender. After a steal or defensive board the faster team can get a <b>fast break</b> (a head start on openness).</li>
      <li>Open (●) makes the next shot better; a pass move's power and type decide how open the catch is. Shot clock: ${CLOCK} actions; an offensive board resets it.</li>
      <li>Type matchups vs the defender count on every shot (and STAB). A defender's status or debuff move lands before the handler acts if the defender is faster. Rain powers Water and weakens Fire, sun the reverse; sandstorm wears down everyone not Rock/Ground/Steel.</li>
      <li>Rim attacks can draw a foul: and-one on a make, a free throw on a miss. The press doubles your own defenders' stamina drain.</li>
      <li>Tired legs miss. Subs only at dead balls (after a make or a violation): resting cures status but resets boosts, and whoever comes off sits until the next dead ball. A defender's slow move still lands after the play and carries over.</li></ul></details>
  </div>`;
}

export function handle(e) {
  if (!S) return false;
  const t = (sel) => e.target.closest(sel);
  let el;
  if ((el = t("[data-bfold]"))) { (S.open ||= {})[el.dataset.bfold] = !el.parentElement.open; return false; }   // remember folds across re-renders
  if ((el = t("[data-bpick]")) && S.phase === "draft") { S.picks.push(Number(el.dataset.bpick)); if (S.room) roomPicks(S.picks); render(); return true; }
  if ((el = t("[data-bstart]"))) { const i = Number(el.dataset.bstart); S.starters = S.starters.includes(i) ? S.starters.filter((x) => x !== i) : S.starters.length < ON ? [...S.starters, i] : S.starters; render(); return true; }
  if ((el = t("[data-bcourt]"))) { S.court = el.dataset.bcourt; pref("court", S.court); render(); return true; }
  if ((el = t("[data-blevel]"))) { S.level = Number(el.dataset.blevel); pref("level", S.level); render(); return true; }
  if (t("#pbHints")) { S.hints = $("pbHints").checked; pref("hints", S.hints); return true; }
  if (t("#pbGo") && S.starters.length === ON) { S.room ? lockRoster() : tipOff(); return true; }
  if ((el = t("[data-bcourtpick]"))) { pref("court", el.dataset.bcourtpick); renderJoin(""); return true; }
  if ((el = t("#pbJoin"))) { openRoom(ctx, el.dataset.code); return true; }
  if ((el = t("#pbCopy"))) { const url = el.dataset.link; (navigator.share ? navigator.share({ title: "Poké Hoops Battle", url }) : navigator.clipboard.writeText(url).then(() => ctx.toast("Link copied"))).catch(() => {}); return true; }
  if (t("#pbRematch")) { roomPost("rematch", { turn: S.room.applied }); return true; }
  if ((el = t("[data-bopt]")) && S.phase === "battle" && S.B.winner < 0) { act(el.dataset.bopt); return true; }
  if ((el = t("[data-bcourtslot]"))) { S.sel = S.sel === Number(el.dataset.bcourtslot) ? null : Number(el.dataset.bcourtslot); render(); return true; }
  if ((el = t("[data-bbench]")) && S.B?.dead) {
    if (S.sel == null) { ctx.toast("Tap the player coming off first"); return true; }
    const ri = Number(el.dataset.bbench), nb = sub(S.B, ME(), S.sel, ri);
    if (nb && S.room) { S.room.mySubs.push([S.sel, ri]); S.B = nb; }   // sent with this turn's call, narrated when the turn lands
    else if (nb) { S.log.unshift([{ t: `Sub: ${nm(0, ri)} in for ${onNm(0, S.sel)}.`, c: "to" }]); S.B = nb; }
    S.sel = null; render(); return true;
  }
  if (t("#pbAgain")) { tipOff(); return true; }
  if (t("#pbNew")) { start(ctx); return true; }
  return false;
}

export const active = () => !!S;
/** Leaving a live room tells the other phone; false = the coach changed their mind. */
export function leaving() {
  const ph = S?.room?.view?.phase;
  return !S?.room || !ph || ph === "lobby" || ph === "left" || confirm(`Leave the battle? ${S.botName || "Your opponent"} will see you left.`);
}
export function stop() {
  const R = S?.room;
  if (R) {
    clearTimeout(R.timer);
    if (R.view?.phase !== "left") ctx.post(`/battle/${R.code}/leave`, {}, { "X-Duel-Key": R.key });
    roomKey(R.code, null, true); history.replaceState(null, "", location.pathname + location.search.replace(/[?&]battle=\w+/, "").replace(/^&/, "?"));
  }
  S = null;
}

// ── rooms: two phones, one battle. Seat key in localStorage (a refresh drops you back in), draft picks too. ──
const roomKey = (code, key, drop) => { try { if (drop) { localStorage.removeItem("phbattle:" + code); localStorage.removeItem("phbattle-picks:" + code); return null; } if (key) localStorage.setItem("phbattle:" + code, key); return localStorage.getItem("phbattle:" + code); } catch { return key || null; } };
const roomPicks = (picks) => { try { if (picks) localStorage.setItem("phbattle-picks:" + S.room.code, JSON.stringify(picks)); return JSON.parse(localStorage.getItem("phbattle-picks:" + S.room.code)) || []; } catch { return picks || []; } };

export async function openRoom(c, code) {
  ctx = c; clearTimeout(S?.room?.timer);
  code = (code || "").toUpperCase();
  let key = code ? roomKey(code) : null;
  if (!key) {
    const name = $("pbName")?.value.trim() || "";
    if (!$("pbJoin") || (!ctx.signedIn() && !name)) { S = { phase: "room", room: null }; return renderJoin(code); }
    const res = await ctx.post(code ? `/battle/${code}/join` : "/battle", { name, court: pref("court") || "clear" });
    if (res.error) return ctx.toast("❌ " + res.error);
    code = res.code; key = roomKey(code, res.key);
  }
  S = { phase: "room", room: { code, key, seat: null, view: null, sig: "", game: -1, applied: 0, mySubs: [] }, hints: pref("hints") ?? false,
        log: [], record: [0, 0], picks: [], starters: [], court: "clear", botName: "Opponent" };
  history.replaceState(null, "", `?battle=${code}${/mock=1/.test(location.search) ? "&mock=1" : ""}`);
  poll();
}

function renderJoin(code) {
  const court = pref("court") || "clear";
  ctx.app.innerHTML = `${ctx.head("⚔️ Battle a friend")}<div class="card pbsetup"><button class="btn ghost pbexit" id="pbExit">← Back</button>
    <div class="phstep">${code ? `Join room ${esc(code)}` : "Start a room"}</div>
    <p class="phmuted">Two phones, one game: you each draft six, then call every play at the same time. No sign-in needed.</p>
    ${ctx.signedIn() ? "" : `<input id="pbName" class="phinput" maxlength="20" placeholder="Your name" autocomplete="nickname">`}
    ${code ? "" : `<div class="phgroup">Court</div><div class="phlevels">${Object.entries(COURTS).map(([k, v]) => `<button class="btn ghost${court === k ? " on" : ""}" data-bcourtpick="${k}">${WX_ICON[k] || "🏟"} ${v}</button>`).join("")}</div>`}
    <button class="btn big primary" id="pbJoin" data-code="${esc(code)}">${code ? "Join" : "Create room"}</button></div>`;
}

async function poll() {
  const R = S?.room;
  if (!R) return;
  clearTimeout(R.timer);
  const v = await ctx.get(`/battle/${R.code}`, { "X-Duel-Key": R.key });
  if (S?.room !== R) return;
  if (v._status === 403 || v._status === 404) { roomKey(R.code, null, true); ctx.toast(v.error || "That room has closed"); S = null; return ctx.exit(); }
  if (!v._status && typeof v.phase === "string") roomView(v);
  if (S?.room === R && R.view?.phase !== "left") R.timer = setTimeout(poll, document.hidden ? 4000 : 1000);
}

async function roomPost(path, body) {
  const R = S.room, v = await ctx.post(`/battle/${R.code}/${path}`, body, { "X-Duel-Key": R.key });
  if (S?.room !== R) return;
  if (v.error) { R.waiting = false; ctx.toast("❌ " + v.error); return render(); }
  roomView(v);
}
function roomMove(pick) {
  const R = S.room;
  if (R.waiting) return;
  R.waiting = true; render();
  roomPost("move", { turn: R.applied, pick, subs: R.mySubs });
}
function lockRoster() {
  const order = [...S.starters, ...S.picks.map((_, i) => i).filter((i) => !S.starters.includes(i))];
  roomPost("roster", { picks: order.map((i) => S.picks[i]) });
}

function roomView(v) {
  const R = S.room, sig = JSON.stringify([v.phase, v.game, v.turns.length, v.pending, v.opp_pending, v.opp_locked, !!v.roster, v.left, v.names]);
  if (sig === R.sig) return;
  R.sig = sig; R.view = v; R.seat = v.seat;
  S.botName = v.names[1 - v.seat] || "Opponent"; S.court = v.court;
  if (v.phase === "left") return roomMsg(`${esc(v.names[v.left])} left the battle.`);
  if (v.phase === "lobby") {
    const link = `${location.origin}${location.pathname}?battle=${v.code}`;
    return roomMsg(`<div class="pbcode">${v.code}</div><p class="phmuted">Send your opponent this link (or the code). Court: ${WX_ICON[v.court] || "🏟"} ${COURTS[v.court]}.</p>
      <div class="phactions"><button class="btn primary" id="pbCopy" data-link="${esc(link)}">Share invite link</button></div><p class="phmuted">Waiting for someone to join…</p>`);
  }
  if (v.phase === "draft") {
    if (v.roster) { S.phase = "room"; return roomMsg(`Your six are locked in. Waiting for ${esc(S.botName)} to finish drafting…`); }
    if (S.phase === "room") {     // first look at the draft: packs off the room seed, different per seat
      S.seed = (v.seed ^ Math.imul(v.seat + 1, 0x9e3779b1)) >>> 0; S.picks = roomPicks(); S.starters = []; S.phase = "draft";
      render(); scrollTo(0, 0);
    } else if (v.opp_locked) ctx.toast(`${S.botName} has locked in their six`);
    return;
  }
  // battle: (re)start on a new game, then replay any turns we haven't applied
  if (R.game !== v.game) {
    R.game = v.game; R.applied = 0; R.mySubs = []; R.base = null;
    S.rng = seeded(v.seed); S.phase = "battle"; S.log = []; S.sel = null; S.pts = [{}, {}];
    S.B = R.base = newBattle(v.rosters, [[0, 1, 2], [0, 1, 2]], S.rng, v.court);
    S.last = [{ t: `Tip-off vs ${esc(S.botName)} on a ${COURTS[v.court].toLowerCase()} court. ${S.B.off === ME() ? "You get" : "They get"} the ball first.`, c: "q" }];
    S.log.unshift(S.last);
    scrollTo(0, 0);
  }
  let moved = false;
  while (R.applied < v.turns.length) {
    const turn = v.turns[R.applied++], pre = [];
    let B = R.base;
    for (const sd of [0, 1]) for (const [slot, ri] of turn[sd].subs) {
      const nb = sub(B, sd, slot, ri);
      if (nb) { pre.push({ t: `Sub (${sd === ME() ? "you" : esc(S.botName)}): ${esc(mon(B.rosters[sd][ri]).name)} in for ${esc(mon(B.rosters[sd][B.on[sd][slot]]).name)}.`, c: "to" }); B = nb; }
    }
    S.B = B;
    step(turn[B.off].pick, turn[1 - B.off].pick, pre);
    R.base = S.B; moved = true;
  }
  if (moved) { R.mySubs = []; S.sel = null; }
  if (!R.mySubs.length) S.B = R.base;
  R.waiting = v.pending;
  render();
  if (moved) scrollLast();
}

function roomMsg(html) {
  ctx.app.innerHTML = `${ctx.head("⚔️ Battle · two phones")}<div class="card pbsetup"><button class="btn ghost pbexit" id="pbExit">← Leave</button>
    <div class="phstep">Room ${esc(S.room.code)}</div>${html.startsWith("<") ? html : `<p>${html}</p>`}</div>`;
}
