// Poké Hoops BATTLE (beta) — 3v3, first to 11, played like a Pokémon battle (engine: ./pokehoops/battle.js, vendored
// from the TV repo). Draft six, pick your three starters and the court, then every turn you and the bot choose at the
// same time and it resolves. Runs entirely in the browser: nothing's at stake, so no server.
import { newBattle, resolve, options, botPick, botSubs, sub, movesOf, shotChance, speedOf, weatherOf, SCHEMES, CAT, COURTS, WEATHER,
  STATUS_TXT, TO, ON, CLOCK, NO_BATTLE } from "./pokehoops/battle.js";
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
const LEVELS = [{ name: "Rookie", temp: 0.25, draft: 0 }, { name: "Pro", temp: 0.08, draft: 0.6 }, { name: "All-Star", temp: 0.04, draft: 1 }];
const STAT_LAB = { shoot: "SHT", finish: "FIN", handle: "HND", perD: "PER-D", intD: "RIM-D", reb: "REB", spe: "SPE" };
const WX_ICON = { rain: "🌧", sun: "☀️", sand: "🌪" };
const ST_ICON = { slp: "💤", par: "⚡", cnf: "💫", psn: "☠️" };
const pref = (k, v) => { try { if (v !== undefined) localStorage.setItem("phb:" + k, JSON.stringify(v)); return JSON.parse(localStorage.getItem("phb:" + k)); } catch { return v ?? null; } };

let S = null, ctx = null;
const $ = (id) => document.getElementById(id);

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
  if (!pack) { S.phase = "setup"; S.starters = [0, 1, 2]; return render(); }
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
    <div class="phstep">Your starting three</div><div class="phmuted">Tap to choose three. The other three are your subs (swap at any dead ball; resting cures status but loses boosts).</div>
    <div class="pbroster">${S.picks.map((n, i) => `<button class="pbmini pick${S.starters.includes(i) ? " on" : ""}" data-bstart="${i}"><img src="${sprite(n)}" alt=""><div><b>${esc(mon(n).name)}</b> ${mon(n).types.map(chip).join("")} <small>SPE ${mon(n).spe}</small><div class="pbmoves">${movesOf(n).map(moveChip).join("")}</div></div></button>`).join("")}</div>
    <div class="phgroup">Court</div><div class="phlevels">${courts}</div>
    <div class="phmuted">${S.court === "clear" ? "No weather. Weather moves still change it for a few turns." : esc(WEATHER[S.court])}</div>
    <div class="phgroup">Opponent</div><div class="phlevels">${lvl}</div>
    <label class="pbhint"><input type="checkbox" id="pbHints" ${S.hints ? "checked" : ""}> Show type-effectiveness hints <small>(off = you need to know the chart)</small></label>
    <button class="btn big primary" id="pbGo" ${S.starters.length === ON ? "" : "disabled"}>Tip off</button>
  </div>`;
}

function tipOff() {
  const rng = seeded((Math.random() * 2 ** 32) >>> 0), L = LEVELS[S.level], bot = botDraft(rng, L.draft);
  const order = [...S.starters, ...S.picks.map((_, i) => i).filter((i) => !S.starters.includes(i))];
  S.rng = rng; S.phase = "battle"; S.log = []; S.sel = null; S.botName = `${L.name} bot`;
  S.B = newBattle([order.map((i) => S.picks[i]), bot.picks], [[0, 1, 2], bot.starters], rng, S.court);
  S.log.unshift({ t: `Tip-off on a ${COURTS[S.court].toLowerCase()} court. ${S.B.off === 0 ? "You" : "They"} get the ball first.`, c: "q" });
  render(); scrollTo(0, 0);
}

// ── the battle ──
const nm = (side, ri) => esc(mon(S.B.rosters[side][ri]).name);
const onNm = (side, slot) => nm(side, S.B.on[side][slot]);
function effWord(e) { return e >= 2 ? "It's super effective!" : e === 0 ? "It doesn't affect the defender…" : e < 1 ? "It's not very effective…" : ""; }

function narrate(ev) {
  const B = S.B, lines = [];
  const side = (s) => (s === 0 ? "You" : esc(S.botName));
  for (const e of ev) {
    if (e.k === "pick") {
      const o = B.off, describe = (s, p) => {
        if (p.kind === "scheme") return `${SCHEMES[p.scheme].name}`;
        if (p.kind === "pass") return `${onNm(s, p.slot)} passes to ${onNm(s, p.to)}`;
        if (p.kind === "heave") return `${onNm(s, p.slot)} heaves it`;
        if (p.kind === "hold") return "holds";
        const m = movesOf(B.rosters[s][B.on[s][p.slot]])[p.mi];
        return `${onNm(s, p.slot)} uses <b>${esc(m.name)}</b>${p.to !== undefined ? ` → ${onNm(s, p.to)}` : ""}`;
      };
      lines.push({ t: `<span class="pbo">${side(o)}:</span> ${describe(o, e.off)} &nbsp;·&nbsp; <span class="pbd">${side(1 - o)}:</span> ${describe(1 - o, e.def)}`, c: "pick" });
    } else if (e.k === "shot") {
      const who = onNm(B.off, e.slot), w = effWord(e.eff);
      lines.push({ t: `${w ? `<i>${w}</i> ` : ""}${e.made ? `<b>${who} scores!</b> +${e.pts}${e.andOne ? " (and one!)" : e.ft ? " (free throw)" : ""}` : `${who} misses${e.fouled ? " (fouled — misses the free throw)" : ""}.`}`, c: e.made ? "big" : "dim" });
    } else if (e.k === "pass") lines.push({ t: `${onNm(B.off, e.to)} catches it${e.gain >= 2 ? " wide open" : e.gain ? " with room" : ", covered"}.`, c: "" });
    else if (e.k === "screen") lines.push({ t: e.switched ? `They switch the screen — mismatch!` : `The screen frees ${onNm(B.off, B.ball)}${e.gain >= 2 ? " completely" : ""}.`, c: "" });
    else if (e.k === "turnover") lines.push({ t: e.why === "steal" ? `<b>Stolen!</b>` : e.why === "confused" ? `${onNm(B.off, e.slot)} is confused… throws it away!` : "Turnover.", c: "neg" });
    else if (e.k === "oreb") lines.push({ t: `Offensive rebound, ${onNm(B.off, e.slot)}! Shot clock reset.`, c: "" });
    else if (e.k === "dreb") lines.push({ t: `Rebound ${onNm(1 - B.off, e.slot)}.`, c: "dim" });
    else if (e.k === "status") lines.push({ t: `${onNm(e.tSide, e.tSlot)} is ${STATUS_TXT[e.st]}! ${ST_ICON[e.st]}`, c: "call" });
    else if (e.k === "statusMiss") lines.push({ t: e.immune ? `${onNm(e.tSide, e.tSlot)} is immune!` : `${esc(e.move)} missed!`, c: "dim" });
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

function act(myPick) {
  const B = S.B, me = 0, botP = botPick(B, 1, S.rng, LEVELS[S.level].temp);
  const offPick = B.off === me ? myPick : botP, defPick = B.off === me ? botP : myPick;
  const before = B;
  const { B: next, ev } = resolve(B, offPick, defPick, S.rng);
  S.B = before;   // narrate with the pre-turn floor (names by slot), then advance
  const lines = narrate(ev);
  S.B = next;
  if (next.off !== before.off || next.dead) lines.push({ t: `${next.score[0]}–${next.score[1]} · ${next.off === 0 ? "your ball" : "their ball"}`, c: "q" });
  S.log.unshift(...lines.reverse());
  S.log.length = Math.min(S.log.length, 80);
  S.last = lines.slice().reverse();
  if (S.B.dead && S.B.winner < 0) S.B = botSubs(S.B, 1);
  if (S.B.winner >= 0) { S.record[S.B.winner === 0 ? 0 : 1]++; pref("record", S.record); }
  S.sel = null;
  render();
}

function playerCard(side, slot) {
  const B = S.B, ri = B.on[side][slot], n = B.rosters[side][ri], st = B.status[side][ri], stg = B.stage[side][ri], off = side === B.off;
  const ball = off && B.ball === slot, guard = off ? B.guard[slot] : B.guard.indexOf(slot);
  const stages = Object.entries(stg).filter(([, v]) => v).map(([k, v]) => `<span class="${v > 0 ? "up" : "dn"}">${STAT_LAB[k]} ${v > 0 ? "+" : ""}${v}</span>`).join("");
  return `<div class="pbp${ball ? " ball" : ""}${side === 0 && S.sel === slot ? " sel" : ""}" ${side === 0 && B.dead && B.winner < 0 ? `data-bcourtslot="${slot}"` : ""}>
    ${ball ? `<span class="pbball">🏀</span>` : ""}<img src="${sprite(n)}" alt="">
    <div class="pbpn"><b>${esc(mon(n).name)}</b>${st ? ` ${ST_ICON[st.k]}` : ""}</div>
    <div class="pbtypes">${mon(n).types.map(chip).join("")}</div>
    <div class="pbstam"><i style="width:${B.stam[side][ri]}%"></i></div>
    ${stages ? `<div class="pbstg">${stages}</div>` : ""}
    ${off ? `<div class="pbopen">${"●".repeat(B.open[slot])}${"○".repeat(3 - B.open[slot])}</div>` : ""}
    <small class="pbguard">${off ? `guarded by ${onNm(1 - side, guard)}` : `on ${onNm(1 - side, guard)}`}</small>
  </div>`;
}

function optionButtons() {
  const B = S.B, opts = options(B, 0), mine = B.off === 0;
  if (mine) {
    const h = B.ball, groups = {};
    for (const op of opts) {
      const slot = op.kind === "pass" || op.kind === "heave" ? h : op.slot ?? h;
      (groups[slot] ||= []).push(op);
    }
    return Object.entries(groups).sort(([a], [b]) => (Number(a) === h ? -1 : Number(b) === h ? 1 : a - b)).map(([slot, ops]) =>
      `<div class="pbgrp"><div class="phgroup">${Number(slot) === h ? `🏀 ${onNm(0, h)}` : onNm(0, slot)}</div><div class="pbopts">${ops.map(optBtn).join("")}</div></div>`).join("");
  }
  const schemes = opts.filter((o) => o.kind === "scheme"), moves = opts.filter((o) => o.kind === "move");
  return `<div class="pbgrp"><div class="phgroup">Defense</div><div class="pbopts">${schemes.map(optBtn).join("")}</div></div>
    ${moves.length ? `<div class="pbgrp"><div class="phgroup">Or a defender's move <small>(you play man-to-man that turn)</small></div><div class="pbopts">${moves.map(optBtn).join("")}</div></div>` : ""}`;
}

function optBtn(op) {
  const B = S.B;
  if (op.kind === "scheme") return `<button class="pbopt" data-bopt="${op.id}"><b>${SCHEMES[op.scheme].name}</b><small>${SCHEMES[op.scheme].does}</small></button>`;
  if (op.kind === "pass") return `<button class="pbopt" data-bopt="${op.id}"><b>Pass → ${onNm(0, op.to)}</b><small>basic pass</small></button>`;
  if (op.kind === "heave") return `<button class="pbopt" data-bopt="${op.id}"><b>Heave it</b><small>out of shot PP</small></button>`;
  if (op.kind === "hold") return `<button class="pbopt" data-bopt="${op.id}"><b>Hold</b></button>`;
  const side = B.off === 0 ? 0 : 0, ri = B.on[side][op.slot], m = movesOf(B.rosters[side][ri])[op.mi], pp = B.pp[side][ri][op.mi];
  let hint = "";
  if (S.hints && (m.cat === "deep" || m.cat === "rim")) {
    const e = typeEff(m.type, B.rosters[1][B.on[1][B.guard[B.ball]]]);
    hint = e >= 2 ? `<em class="pos">super effective</em>` : e === 0 ? `<em class="neg">no effect</em>` : e < 1 ? `<em class="neg">not very effective</em>` : "";
  }
  return `<button class="pbopt mv" style="--tc:${TYPE_COL[m.type] || "#888"}" data-bopt="${op.id}"><b>${esc(m.name)}${op.to !== undefined ? ` → ${onNm(0, op.to)}` : ""}</b>
    <small>${m.type} · ${CAT[m.cat]}${m.boost ? ` (${Object.entries(m.boost).map(([k, v]) => `${STAT_LAB[k]} ${v > 0 ? "+" : ""}${v}`).join(" ")})` : m.drop ? ` (${Object.entries(m.drop).map(([k, v]) => `${STAT_LAB[k]} ${v}`).join(" ")})` : m.st ? ` (${STATUS_TXT[m.st]})` : ""} · PP ${pp}/${m.pp}</small>${hint}</button>`;
}

function renderBattle() {
  const B = S.B, wx = weatherOf(B), over = B.winner >= 0, mineBall = B.off === 0;
  const clock = Array.from({ length: CLOCK }, (_, i) => `<i class="${i < B.clock ? "on" : ""}"></i>`).join("");
  const bench = (side) => S.B.rosters[side].map((n, ri) => [n, ri]).filter(([, ri]) => !B.on[side].includes(ri));
  ctx.app.innerHTML = `${ctx.head()}<div class="card pbbattle"><button class="btn ghost pbexit" id="pbExit">← Exit</button>
    <div class="phsb"><div><small>You</small><b>${B.score[0]}</b></div>
      <div class="phclock">to ${TO}${wx ? ` · ${WX_ICON[wx]}` : ""}<small>${over ? "FINAL" : B.dead ? "dead ball" : `${mineBall ? "your ball" : "on defense"}`}</small>${over ? "" : `<span class="pbclock" title="shot clock">${clock}</span>`}</div>
      <div><small>${esc(S.botName)}</small><b>${B.score[1]}</b></div></div>
    ${wx ? `<div class="phmuted pbwx">${WX_ICON[wx]} ${esc(WEATHER[wx])}${B.weather ? ` (${B.weather.t} turns)` : " (the court)"}</div>` : ""}
    <div class="pbcourt"><div class="pbrow them">${[0, 1, 2].map((s) => playerCard(1, s)).join("")}</div>
      <div class="pbrow us">${[0, 1, 2].map((s) => playerCard(0, s)).join("")}</div></div>
    ${S.last?.length ? `<div class="pblast">${S.last.map((l) => `<div class="${l.c}">${l.t}</div>`).join("")}</div>` : ""}
    ${over ? `<div class="phgrade ${B.winner === 0 ? "great" : "bad"} pop">${B.winner === 0 ? "You win" : `${esc(S.botName)} wins`} ${Math.max(...B.score)}–${Math.min(...B.score)}</div>
        <div class="phmuted">You're ${S.record[0]}–${S.record[1]} in battles.</div>
        <div class="phactions"><button class="btn primary" id="pbAgain">Rematch</button><button class="btn ghost" id="pbNew">New draft</button></div>`
      : `${B.dead ? `<details class="pbsubs"${S.sel != null ? " open" : ""}><summary>🔁 Dead ball: make a sub? <small>tap a player on the floor, then who comes in</small></summary>
          <div class="pbroster">${bench(0).map(([n, ri]) => `<button class="pbmini pick" data-bbench="${ri}"><img src="${sprite(n)}" alt=""><div><b>${esc(mon(n).name)}</b> ${mon(n).types.map(chip).join("")}<div class="pbstam"><i style="width:${B.stam[0][ri]}%"></i></div></div></button>`).join("")}</div></details>` : ""}
        <div class="pbchoose"><div class="phstep">${mineBall ? "Your call on offense" : "Your call on defense"}</div>${optionButtons()}</div>`}
    <details class="pblog"><summary>Play-by-play</summary>${S.log.map((l) => `<div class="${l.c}">${l.t}</div>`).join("")}</details>
    <details class="pbhelp"><summary>How it works</summary><ul>
      <li>Both coaches pick at the same time. Offense: shoot (rim = 1, deep = 2), pass, or a teammate's screen / status / boost / weather move. Defense: a scheme, or a defender's move.</li>
      <li>Deep beats <b>Pack the paint</b>; the rim beats <b>Deny</b>; passes and screens beat the <b>Press</b>; <b>Deny</b> steals passes; <b>Switch</b> kills screens but can leave a mismatch.</li>
      <li>Open (●) makes the next shot better. Shot clock: ${CLOCK} actions; an offensive board resets it.</li>
      <li>Type matchups vs the defender count on every shot (and STAB). Faster Pokémon act first with status moves. Weather boosts Water or Fire; sandstorm wears down everyone not Rock/Ground/Steel.</li>
      <li>Tired legs miss. Subs only at dead balls: resting cures status but resets boosts.</li></ul></details>
  </div>`;
}

export function handle(e) {
  if (!S) return false;
  const t = (sel) => e.target.closest(sel);
  let el;
  if ((el = t("[data-bpick]")) && S.phase === "draft") { S.picks.push(Number(el.dataset.bpick)); render(); return true; }
  if ((el = t("[data-bstart]"))) { const i = Number(el.dataset.bstart); S.starters = S.starters.includes(i) ? S.starters.filter((x) => x !== i) : S.starters.length < ON ? [...S.starters, i] : S.starters; render(); return true; }
  if ((el = t("[data-bcourt]"))) { S.court = el.dataset.bcourt; pref("court", S.court); render(); return true; }
  if ((el = t("[data-blevel]"))) { S.level = Number(el.dataset.blevel); pref("level", S.level); render(); return true; }
  if (t("#pbHints")) { S.hints = $("pbHints").checked; pref("hints", S.hints); return true; }
  if (t("#pbGo") && S.starters.length === ON) { tipOff(); return true; }
  if ((el = t("[data-bopt]")) && S.phase === "battle" && S.B.winner < 0) { act(el.dataset.bopt); return true; }
  if ((el = t("[data-bcourtslot]"))) { S.sel = S.sel === Number(el.dataset.bcourtslot) ? null : Number(el.dataset.bcourtslot); render(); return true; }
  if ((el = t("[data-bbench]")) && S.B?.dead) {
    if (S.sel == null) { ctx.toast("Tap the player coming off first"); return true; }
    const nb = sub(S.B, 0, S.sel, Number(el.dataset.bbench));
    if (nb) { S.log.unshift({ t: `Sub: ${nm(0, Number(el.dataset.bbench))} in for ${onNm(0, S.sel)}.`, c: "to" }); S.B = nb; }
    S.sel = null; render(); return true;
  }
  if (t("#pbAgain")) { tipOff(); return true; }
  if (t("#pbNew")) { start(ctx); return true; }
  return false;
}

export const active = () => !!S;
export function stop() { S = null; }
