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

// which side the defensive scheme favours against this kind of play (mirrors the engine's VS table, in words)
const READ = {
  deep: { press: [-1, "the press runs at the shooter"], pack: [1, "packing the paint leaves the deep shot open"], deny: [1, "denying lanes leaves room outside"] },
  rim: { press: [1, "the drive gets past the press"], pack: [-1, "that's a drive into a packed paint"], deny: [1, "denying passes leaves the lane open"] },
  pass: { press: [1, "a quick pass beats the press"], deny: [-1, "they're jumping the passing lanes (steal risk)"] },
  screen: { press: [1, "a screen beats the press"], switch: [-1, "switching kills the screen"] },
  iso: { deny: [1, "lane-jumping defenders can't help on an iso"], switch: [1, "switching hands over the matchup"] },
};
function readOf(B, p, scheme) {
  const cat = p.kind === "move" ? movesOf(B.rosters[B.off][B.on[B.off][p.slot ?? B.ball]])[p.mi].cat : p.kind;
  return READ[cat]?.[scheme];
}

function narrate(ev) {
  const B = S.B, lines = [];
  const side = (s) => (s === ME() ? "You" : esc(S.botName));
  for (const e of ev) {
    const n0 = lines.length;
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
      const r = readOf(B, e.off, e.scheme);
      if (r) lines.push({ t: `📋 ${r[0] > 0 ? "Offense" : "Defense"} wins the call: ${r[1]}.`, c: (r[0] > 0) === (o === ME()) ? "pos read" : "neg read" });
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
    for (let i = n0; i < lines.length; i++) lines[i].e = e;
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
  return { before, lines };
}
const scrollLast = () => document.querySelector(".pblast")?.scrollIntoView({ block: "nearest", behavior: "smooth" });

function act(myPick) {
  if (S.room) return roomMove(myPick);
  const B = S.B, botP = botPick(B, 1, S.rng, LEVELS[S.level].temp, LEVELS[S.level].depth || 0);
  const turn = step(B.off === 0 ? myPick : botP, B.off === 0 ? botP : myPick);
  if (S.B.dead && S.B.winner < 0) S.B = botSubs(S.B, 1);
  if (S.B.winner >= 0) pref("record", S.record);
  S.sel = null;
  play(turn);
}

// ── turn replay: redraw the floor as it was, then play each narrated line with its effect on the court ──
const wait = (ms) => new Promise((r) => { if (S.skip) return r(); S.wake = r; setTimeout(r, ms); });
const PACE = { pick: 1000, shot: 1300, iso: 1000, weather: 900, pass: 750, oreb: 700, dreb: 700, turnover: 800 };
async function play({ before, lines }) {
  const run = S, after = S.B, last = S.last;
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) { render(); return scrollLast(); }
  S.playing = true; S.skip = false; S.B = before; S.last = [];
  render();
  const court = document.querySelector(".pbfloor"), box = document.querySelector(".pblast"), sb = document.querySelector(".phsb");
  scrollBy({ top: court.getBoundingClientRect().top - (sb ? sb.getBoundingClientRect().height + 8 : 8), behavior: "smooth" });   // whole floor below the sticky scoreboard
  const P = { court, o: before.off, B: before, guard: [...before.guard], score: [...before.score] };
  fly(court, cardEl(court, P.o, before.ball), cardEl(court, P.o, before.ball), 1);
  for (const l of lines) {
    if (S !== run || S.skip || !court.isConnected) break;
    const div = document.createElement("div");
    div.className = `${l.c} pbin`; div.innerHTML = l.t; box.append(div);
    fx(l.e, P);
    await wait(l.e ? PACE[l.e.k] || 650 : 450);
  }
  if (S !== run) return;   // left mid-replay
  S.playing = false; S.B = after; S.last = last;
  render(); scrollLast();
}
const cardEl = (court, side, slot) => court.querySelector(`[data-s="${side}${slot}"]`);
function center(court, el) {
  const r0 = court.getBoundingClientRect(), r = el.getBoundingClientRect();
  return [r.left - r0.left + r.width / 2, r.top - r0.top + r.height / 2];
}
function fly(court, a, b, ms = 600, h = 30) {   // the ball stays where it lands until the next flight
  if (!a || !b) return;
  court.querySelector(".pbfly")?.remove();
  const [x0, y0] = center(court, a), [x1, y1] = center(court, b), ball = document.createElement("span"), t = (x, y) => `translate(${x}px, ${y}px) translate(-50%, -50%)`;
  ball.className = "pbfly"; ball.textContent = "🏀"; court.append(ball);
  ball.animate([{ transform: t(x0, y0) }, { transform: t((x0 + x1) / 2, Math.min(y0, y1) - h) }, { transform: t(x1, y1) }], { duration: ms, easing: "ease-in-out", fill: "forwards" });
}
function pop(court, el, html, cls = "", style = "") {   // a label that floats up off a card
  if (!el) return;
  const [x, y] = center(court, el), f = document.createElement("div");
  f.className = `pbfloat ${cls}`; f.innerHTML = html; f.style.cssText = `left:${x}px;top:${y - 20}px;${style}`;
  court.append(f);
  const k = (o, dy, sc) => ({ opacity: o, transform: `translate(-50%, ${dy}px) scale(${sc})` });
  f.animate([k(0, 6, 0.7), k(1, -6, 1.08), k(1, -10, 1), k(0, -34, 1)], { duration: 1400, easing: "ease-out" }).finished.then(() => f.remove());
}
const shake = (el, px = 6) => el?.animate([0, -px, px, -px / 2, px / 2, 0].map((x) => (el.classList.contains("pbtok") ? T(x) : { transform: `translateX(${x}px)` })), { duration: 420 });
const glow = (el, col, ms = 800) => el?.animate([{ boxShadow: `0 0 0 0 ${col}` }, { boxShadow: `0 0 22px 6px ${col}` }, { boxShadow: `0 0 0 0 ${col}` }], { duration: ms });
const T = (dx = 0, dy = 0, sc = 1) => ({ transform: `translate(-50%, -50%) translate(${dx}px, ${dy}px) scale(${sc})` });
const lunge = (el) => el?.animate([T(), T(0, -6, 1.15), T()], { duration: 500, easing: "ease-out" });
function slide(court, el, to, ms = 700, back = true) {   // run a token toward another token (or a point) and, by default, back
  if (!el || !to) return;
  const [x0, y0] = center(court, el), [x1, y1] = Array.isArray(to) ? to : center(court, to), dx = x1 - x0, dy = y1 - y0;
  el.animate(back ? [T(), T(dx, dy), T(dx, dy), T()] : [T(), T(dx, dy)], { duration: ms, easing: "ease-in-out", fill: back ? "none" : "forwards" });
}
const PACK = [[38, 30], [50, 38], [62, 30]];
function formation({ court, o, B }, scheme) {
  const d = 1 - o, w = court.clientWidth, h = court.clientHeight, c = (s, sl) => cardEl(court, s, sl);
  const toward = (a, b, k) => { const [x0, y0] = center(court, a), [x1, y1] = center(court, b); return [x0 + (x1 - x0) * k, y0 + (y1 - y0) * k]; };
  for (let j = 0; j < 3; j++) {
    const el = c(d, j), man = c(o, B.guard.indexOf(j));
    if (!el) continue;
    if (scheme === "pack") slide(court, el, [PACK[j][0] * w / 100, PACK[j][1] * h / 100], 600, false);
    else if (scheme === "press" && man) slide(court, el, toward(el, man, 0.75), 500, false);
    else if (scheme === "deny" && man && B.guard.indexOf(j) !== B.ball) slide(court, el, toward(man, c(o, B.ball), 0.4), 600, false);
    else if (scheme === "switch") pop(court, el, "↔", "sch");
  }
}
function fx(e, P) {
  if (!e) return;
  const { court, o, B } = P, d = 1 - o, c = (s, sl) => cardEl(court, s, sl), hoop = court.querySelector(".pbhoop");
  const callout = (side, p) => {   // "Kingler used Crabhammer!" over the card, in the move's type colour
    if (p.kind === "scheme") { formation(P, p.scheme); return pop(court, hoop, esc(SCHEMES[p.scheme].name), "sch"); }
    const slot = p.slot ?? B.ball, el = c(side, slot);
    lunge(el);
    if (p.kind !== "move") return;
    const m = movesOf(B.rosters[side][B.on[side][slot]])[p.mi], col = TYPE_COL[m.type] || "#888";
    pop(court, el, esc(m.name), "mv", `--tc:${col}`); glow(el, col);
  };
  if (e.k === "pick") { callout(o, e.off); setTimeout(() => court.isConnected && callout(d, e.def), 350); }
  else if (e.k === "pass") fly(court, c(o, e.from), c(o, e.to), 600, 26);
  else if (e.k === "screen") { const g = c(d, P.guard[B.ball]); slide(court, c(o, e.slot), g, 1000); glow(g, "#7aa2f7"); pop(court, c(o, e.slot), "🛡 screen"); }
  else if (e.k === "iso") {
    // clear out: everyone else drifts to the far corners, the called-for defender switches onto the ball
    const w = court.clientWidth;
    for (let s = 0; s < 3; s++) if (s !== e.slot) { const dx = (SPOT[s][0] < 50 ? -1 : SPOT[s][0] > 50 ? 1 : 0) * w * 0.1, dy = s === 1 ? 20 : -40; c(o, s)?.animate([T(), T(dx, dy)], { duration: 700, fill: "forwards" }); }
    for (let j = 0; j < 3; j++) if (j !== e.g) c(d, j)?.animate([T(), T(0, -30)], { duration: 700, fill: "forwards" });
    slide(court, c(d, e.g), c(o, e.slot), 700, false);
    if (e.eff >= 2) { glow(c(d, e.g), "#f7d02c"); pop(court, c(d, e.g), "mismatch!", "sch"); }
    P.guard[e.slot] = e.g;
  } else if (e.k === "shot") {
    const shooter = c(o, e.slot), def = c(d, P.guard[e.slot]);
    if (e.cat === "rim") slide(court, shooter, hoop, 900); else lunge(shooter);   // drive vs pull-up
    fly(court, shooter, hoop, 650, e.cat === "deep" ? 70 : 30);
    if (e.eff >= 2) { shake(def, 8); glow(def, TYPE_COL[e.type] || "#fff", 600); }
    else if (e.eff < 1) pop(court, def, e.eff === 0 ? "barely…" : "not very effective", "dim");
    setTimeout(() => {
      if (!court.isConnected) return;
      if (!e.scored) return shake(hoop, 4);
      P.score[o] += e.pts;
      hoop.animate([{ transform: "scale(1)" }, { transform: "scale(1.5)" }, { transform: "scale(1)" }], { duration: 450 });
      glow(hoop, "#9ece6a", 600);
      pop(court, shooter, `+${e.pts}`, "good big");
      const sb = document.querySelectorAll(".phsb > div > b"), el = sb[o === ME() ? 0 : 1];
      if (el) { el.textContent = P.score[o]; el.animate([{ transform: "scale(1)" }, { transform: "scale(1.6)", color: "#9ece6a" }, { transform: "scale(1)" }], { duration: 600 }); }
    }, 650);
  } else if (e.k === "oreb") { slide(court, c(o, e.slot), hoop, 700); fly(court, hoop, c(o, e.slot), 500, 20); pop(court, c(o, e.slot), "board!", "good"); }
  else if (e.k === "dreb") { slide(court, c(d, e.slot), hoop, 700); fly(court, hoop, c(d, e.slot), 500, 20); }
  else if (e.k === "turnover") {
    const to = c(d, e.by >= 0 ? e.by : P.guard[e.slot]);
    if (e.why === "steal") { slide(court, to, c(o, e.slot), 500); fly(court, c(o, e.slot), to, 400, 10); pop(court, to, "Stolen!", "bad"); } else shake(c(o, e.slot));
  } else if (e.k === "fastBreak") { slide(court, c(e.side, e.slot), [court.clientWidth / 2, court.clientHeight * 0.95], 700); pop(court, c(e.side, e.slot), "💨 fast break"); }
  else if (e.k === "status") { shake(c(e.tSide, e.tSlot), 4); pop(court, c(e.tSide, e.tSlot), ST_ICON[e.st], "big"); }
  else if (e.k === "statusMiss") pop(court, c(e.tSide, e.tSlot), e.immune ? "immune" : e.failed ? "failed" : "missed", "dim");
  else if (e.k === "boost") pop(court, c(e.side, e.slot), Object.entries(e.got).map(([k, v]) => `${STAT_LAB[k]} ${v > 0 ? "▲".repeat(v) : "▼".repeat(-v)}`).join(" ") || "max!", "up");
  else if (e.k === "debuff") { shake(c(e.tSide, e.tSlot), 3); pop(court, c(e.tSide, e.tSlot), Object.entries(e.got).map(([k, v]) => `${STAT_LAB[k]} ${"▼".repeat(-v)}`).join(" ") || "min!", "bad"); }
  else if (e.k === "weather") court.animate([{ boxShadow: "inset 0 0 0 0 transparent" }, { boxShadow: `inset 0 0 60px 10px ${{ sun: "#ff9e64", rain: "#7aa2f7", sand: "#e0af68" }[e.wx]}` }, { boxShadow: "inset 0 0 0 0 transparent" }], { duration: 1200 });
  else if (e.k === "asleep") pop(court, c(o, e.slot), "💤", "big");
  else if (e.k === "fullyPar") { shake(c(o, e.slot), 3); pop(court, c(o, e.slot), "⚡", "big"); }
  else if (e.k === "confusedSelf") { shake(c(o, e.slot)); pop(court, c(o, e.slot), "💫", "big"); }
  else if (e.k === "violation") { shake(document.querySelector(".pbclock")); pop(court, hoop, "⏱ shot clock!", "bad"); }
}

function playerCard(side, slot) {
  const B = S.B, ri = B.on[side][slot], n = B.rosters[side][ri], st = B.status[side][ri], stg = B.stage[side][ri], off = side === B.off;
  const ball = off && B.ball === slot, guard = off ? B.guard[slot] : B.guard.indexOf(slot);
  const stages = Object.entries(stg).filter(([, v]) => v).map(([k, v]) => `<span class="${v > 0 ? "up" : "dn"}">${STAT_LAB[k]} ${v > 0 ? "+" : ""}${v}</span>`).join("");
  return `<div class="pbp${ball ? " ball" : ""}${side === ME() && S.sel === slot ? " sel" : ""}" ${side === ME() && B.dead && B.winner < 0 && !S.room?.waiting && !S.playing ? `data-bcourtslot="${slot}"` : ""}>
    ${ball ? `<span class="pbball">🏀</span>` : ""}<img src="${sprite(n)}" alt="">
    <div class="pbpn"><b>${esc(mon(n).name)}</b>${st ? ` ${ST_ICON[st.k]}` : ""}</div>
    <div class="pbtypes">${mon(n).types.map(chip).join("")}</div>
    <div class="pbstam" title="stamina ${Math.round(B.stam[side][ri])}%"><i style="width:${B.stam[side][ri]}%"></i></div>
    ${stages ? `<div class="pbstg">${stages}</div>` : ""}
    ${off ? `<div class="pbopen" title="how open: each ● makes the next shot better">open ${"●".repeat(B.open[slot])}${"○".repeat(3 - B.open[slot])}</div>` : ""}
    <small class="pbguard">${off ? `guarded by ${onNm(1 - side, guard)}` : `on ${onNm(1 - side, guard)}`}</small>
  </div>`;
}

const SPOT = [[17, 50], [50, 74], [83, 50]], HOOP = [50, 13];
function floor() {
  const B = S.B, o = B.off, d = 1 - o, at = [], mine = (side) => (side === ME() ? "me" : "op");
  const tok = (side, slot, [x, y]) => {
    const ri = B.on[side][slot], n = B.rosters[side][ri], st = B.status[side][ri], ball = side === o && slot === B.ball;
    const stg = Object.entries(B.stage[side][ri]).filter(([, v]) => v).map(([k, v]) => `<em class="${v > 0 ? "up" : "dn"}">${STAT_LAB[k]}${v > 0 ? "▲" : "▼"}${Math.abs(v) > 1 ? Math.abs(v) : ""}</em>`).join("");
    return `<div class="pbtok ${mine(side)}${ball ? " ball" : ""}" data-s="${side}${slot}" style="left:${x}%;top:${y}%">
      <img src="${sprite(n)}" alt="">${st ? `<i class="pbst">${ST_ICON[st.k]}</i>` : ""}${ball ? `<span class="pbball">🏀</span>` : ""}<b>${esc(mon(n).name)}</b>${stg}</div>`;
  };
  for (let s = 0; s < 3; s++) at.push(tok(o, s, SPOT[s]));
  const used = {};
  for (let j = 0; j < 3; j++) {
    const man = B.guard.indexOf(j);
    let pos = [50, 30];   // not on anyone (after a switch): sits in the paint
    if (man >= 0) { const [x, y] = SPOT[man], k = 0.36 + 0.1 * B.open[man]; pos = [x + (HOOP[0] - x) * k, y + (HOOP[1] - y) * k]; }
    const key = pos.join(); if (used[key]) pos = [pos[0] + 9, pos[1]]; used[key] = 1;
    at.push(tok(d, j, pos));
  }
  return `<div class="pbfloor"><svg viewBox="0 0 100 90" preserveAspectRatio="none" aria-hidden="true">
      <rect x="35" y="0" width="30" height="38" class="paint"/><circle cx="50" cy="38" r="10"/><path d="M6 0 V18 A44 44 0 0 0 94 18 V0"/>
      <line x1="42" y1="6" x2="58" y2="6" class="board"/></svg>
    <div class="pbhoop" style="left:${HOOP[0]}%;top:${HOOP[1]}%"></div>
    <span class="pbtag" style="left:4%;top:94%">${o === ME() ? "You attack ↑" : `${esc(S.botName)} attack ↑`}</span>${at.join("")}</div>`;
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
  ctx.app.innerHTML = `${ctx.head()}<div class="card pbbattle${S.playing ? " pbplaying" : ""}"><button class="btn ghost pbexit" id="pbExit">← Exit</button>
    <div class="phsb"><div><small>You</small><b>${B.score[me]}</b></div>
      <div class="phclock">to ${TO}${wx ? ` · ${WX_ICON[wx]}` : ""}<small>${over ? "FINAL" : B.dead ? "dead ball" : `${mineBall ? "your ball" : "on defense"}`}</small>${over ? "" : `<span class="pbclock" title="shot clock: actions left">${clock}</span><small>shot clock</small>`}</div>
      <div><small>${esc(S.botName)}</small><b>${B.score[op]}</b></div></div>
    ${wx ? `<div class="phmuted pbwx">${WX_ICON[wx]} ${esc(WEATHER[wx])}${B.weather ? ` (${B.weather.t} turns)` : " (the court)"}</div>` : ""}
    ${floor()}
    <details class="pbcards"${S.open?.cards || B.dead ? " open" : ""}><summary data-bfold="cards">Players: stamina, boosts, matchups</summary><div class="pbcourt"><div class="pbside">${esc(S.botName)}</div><div class="pbrow them">${[0, 1, 2].map((s) => playerCard(op, s)).join("")}</div>
      <div class="pbside">You</div><div class="pbrow us">${[0, 1, 2].map((s) => playerCard(me, s)).join("")}</div></div></details>
    ${S.playing ? `<div class="pblast"></div><button class="btn ghost pbskip" id="pbSkip">Skip ⏩</button>` : S.last?.length ? `<div class="pblast">${S.last.map((l) => `<div class="${l.c}">${l.t}</div>`).join("")}</div>` : ""}
    ${S.playing ? "" : over ? `<div class="phgrade ${B.winner === me ? "great" : "bad"} pop">${B.winner === me ? "You win" : `${esc(S.botName)} wins`} ${Math.max(...B.score)}–${Math.min(...B.score)}</div>
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
  if (S.playing) { if (t("#pbSkip, .pbfloor, .pblast")) { S.skip = true; S.wake?.(); for (const a of document.querySelectorAll(".pbfloor *")) a.getAnimations().forEach((x) => x.finish()); } if (!t("[data-bfold]")) return true; }
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
  if (S.playing) return;
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
  let moved = 0, turn1 = null;
  const live = R.loaded; R.loaded = true;
  while (R.applied < v.turns.length) {
    const turn = v.turns[R.applied++], pre = [];
    let B = R.base;
    for (const sd of [0, 1]) for (const [slot, ri] of turn[sd].subs) {
      const nb = sub(B, sd, slot, ri);
      if (nb) { pre.push({ t: `Sub (${sd === ME() ? "you" : esc(S.botName)}): ${esc(mon(B.rosters[sd][ri]).name)} in for ${esc(mon(B.rosters[sd][B.on[sd][slot]]).name)}.`, c: "to" }); B = nb; }
    }
    S.B = B;
    turn1 = step(turn[B.off].pick, turn[1 - B.off].pick, pre);
    R.base = S.B; moved++;
  }
  if (moved) { R.mySubs = []; S.sel = null; }
  if (!R.mySubs.length) S.B = R.base;
  R.waiting = v.pending;
  if (moved === 1 && live) return play(turn1);
  render();
  if (moved) scrollLast();
}

function roomMsg(html) {
  ctx.app.innerHTML = `${ctx.head("⚔️ Battle · two phones")}<div class="card pbsetup"><button class="btn ghost pbexit" id="pbExit">← Leave</button>
    <div class="phstep">Room ${esc(S.room.code)}</div>${html.startsWith("<") ? html : `<p>${html}</p>`}</div>`;
}
