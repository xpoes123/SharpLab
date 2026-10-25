// Poké Hoops battle: half-court, first to 11, played like a Pokémon battle. Every turn BOTH coaches pick at once,
// then it resolves. Offense: the ball-handler's shot or pass, or a teammate's screen / status / weather move. Defense:
// a scheme (press, pack the paint, deny, switch) or a defender's status / weather move. Soft rock-paper-scissors —
// deep beats pack, rim beats deny, passes beat the press, deny steals passes — with skills, types (+ STAB), weather,
// status, stamina and PP deciding by how much. 3 on the floor out of 8; subs only at dead balls; tired legs miss.
// A shot at the rim is worth 1, a deep shot 2 (pickup rules). Shot clock: 4 actions; an offensive board resets it to 3.
// State is plain JSON; resolve() takes an rng so a seed replays a whole game.
import { mon, skills, profile, effectOf, typeEff } from "./ratings.js";
import { MOVE_DEX, MOVESETS } from "./movesets.js";

export const TO = 11, ON = 3, CLOCK = 4, OREB_CLOCK = 3, MAX_OPEN = 3;
export const SCHEMES = {
  man: { name: "Man-to-man", does: "Straight up. No weakness, no edge." },
  press: { name: "Press the ball", does: "Smothers shots and screens off the dribble. A quick pass beats it." },
  pack: { name: "Pack the paint", does: "Walls off the rim. Leaves the deep shot open." },
  deny: { name: "Deny passes", does: "Jumps passing lanes for steals. Leaves the drive open." },
  switch: { name: "Switch everything", does: "Kills screens, but can leave a mismatch." },
};
// shot make-chance swing by scheme, and how open a pass / screen leaves the receiver
const VS = {
  deep: { man: 0, press: -0.14, pack: 0.1, deny: 0.04, switch: 0 },
  rim: { man: 0, press: 0.05, pack: -0.17, deny: 0.09, switch: 0 },
  pass: { man: 1, press: 2, pack: 1, deny: 0, switch: 1 },
  screen: { man: 1, press: 2, pack: 1, deny: 1, switch: 0 },
};
const FOUL = { man: 0.14, press: 0.22, pack: 0.12, deny: 0.16, switch: 0.14 };   // fouled attacking the rim, by scheme
const UTIL = new Set(["status", "weather", "boost", "debuff"]);
export const CAT = { deep: "deep shot · 2", rim: "at the rim · 1", pass: "pass", screen: "screen", status: "status", weather: "weather", boost: "boost", debuff: "debuff" };

// moves by type: [deep, rim, pass, screen]
const MOVES = {
  normal: ["Hyper Beam", "Body Slam", "Swift", "Protect"], fire: ["Fire Blast", "Flame Wheel", "Ember", "Protect"],
  water: ["Hydro Pump", "Waterfall", "Bubble", "Withdraw"], electric: ["Thunder", "Thunder Punch", "Thundershock", "Light Screen"],
  grass: ["Solar Beam", "Vine Whip", "Razor Leaf", "Protect"], ice: ["Blizzard", "Ice Punch", "Powder Snow", "Protect"],
  fighting: ["Dynamic Punch", "Cross Chop", "Karate Chop", "Detect"], poison: ["Sludge Bomb", "Sludge", "Acid", "Acid Armor"],
  ground: ["Bonemerang", "Earthquake", "Mud-Slap", "Protect"], flying: ["Sky Attack", "Drill Peck", "Gust", "Protect"],
  psychic: ["Psychic", "Psybeam", "Confusion", "Reflect"], bug: ["Pin Missile", "Megahorn", "Fury Cutter", "Harden"],
  rock: ["Rock Slide", "Rollout", "Rock Throw", "Harden"], ghost: ["Shadow Ball", "Lick", "Night Shade", "Protect"],
  dragon: ["Dragon Breath", "Outrage", "Twister", "Protect"], dark: ["Feint Attack", "Crunch", "Pursuit", "Protect"],
  steel: ["Steel Wing", "Iron Tail", "Metal Claw", "Defense Curl"], fairy: ["Moonblast", "Play Rough", "Fairy Wind", "Protect"],
};
const STATUS = { electric: ["Thunder Wave", "par"], grass: ["Sleep Powder", "slp"], psychic: ["Hypnosis", "slp"], ghost: ["Confuse Ray", "cnf"],
  normal: ["Sing", "slp"], poison: ["Poison Powder", "psn"], bug: ["Poison Sting", "psn"], fairy: ["Sweet Kiss", "cnf"], dragon: ["Glare", "par"] };
const WEATHER_MOVE = { water: ["Rain Dance", "rain"], fire: ["Sunny Day", "sun"], rock: ["Sandstorm", "sand"], ground: ["Sandstorm", "sand"] };
export const WEATHER = { rain: "Rain: Water moves hit harder, Fire fizzles", sun: "Harsh sun: Fire moves blaze, Water evaporates",
  sand: "Sandstorm: everyone not Rock, Ground or Steel tires faster" };
// stat stages (−3..+3, like the games): boosts stay while the Pokémon stays on the floor; a sub resets them
export const STAGE_MAX = 3, STATS = ["shoot", "finish", "handle", "perD", "intD", "reb", "spe"];
const stageMult = (st) => (st >= 0 ? 1 + 0.15 * st : 1 / (1 + 0.15 * -st));
const FALLBACK_BOOST = { shoot: ["Growth", { shoot: 1 }], finish: ["Swords Dance", { finish: 2 }], handle: ["Agility", { spe: 2, handle: 1 }],
  perD: ["Amnesia", { perD: 2 }], intD: ["Barrier", { intD: 2 }], reb: ["Defense Curl", { intD: 1 }] };
export const COURTS = { clear: "Clear skies", rain: "Rain", sun: "Harsh sun", sand: "Sandstorm" };
export const STATUS_TXT = { slp: "asleep", par: "paralyzed", cnf: "confused", psn: "poisoned" };
const SIG_CAT = { SHARPSHOOTER: "deep", HEAT_CHECK: "deep", SLASHER: "rim", POSTERIZER: "rim", FLOOR_GENERAL: "pass", CLUTCH: "deep",
  GLASS_CLEANER: "screen", RIM_PROTECTOR: "screen", INTIMIDATOR: "screen" };
const PP = { deep: 6, rim: 6, pass: 5, screen: 5, status: 3, weather: 2 };

/** A Pokémon's four battle moves: {name, type, cat, pp, sig?, st?, wx?}. Two shots, a setup, and a status / weather / setup. */
const MOVE_CACHE = {};
export function movesOf(n) { return (MOVE_CACHE[n] ||= MOVESETS[n] ? realMoves(n) : buildMoves(n)); }
/** Pokémon that can't legally learn four moves in Gen 1–2 (their sets pad with Struggle): not drafted in battles. */
export const NO_BATTLE = new Set(Object.keys(MOVESETS).filter((n) => MOVESETS[n].includes("Struggle")).map(Number));
const norm = (x) => String(x || "").toLowerCase().replace(/[^a-z0-9]/g, "");
// the curated Gen 1–2 set (movesets.js); the profile's signature move, when it's in the set, gets the signature bump
function realMoves(n) {
  const sig = norm(profile(n)?.move);
  return MOVESETS[n].map((name) => ({ name, ...MOVE_DEX[name], ...(norm(name) === sig ? { sig: true } : {}) }));
}
function buildMoves(n) {
  const m = mon(n), k = skills(n), [t1, t2 = t1] = m.types, p = profile(n), out = [];
  const mv = (type, cat, extra = {}) => ({ name: MOVES[type][["deep", "rim", "pass", "screen"].indexOf(cat)], type, cat, pp: PP[cat], ...extra });
  const first = k.shoot >= k.finish ? "deep" : "rim", second = first === "deep" ? "rim" : "deep";
  out.push(mv(t1, first), mv(t2 !== t1 ? t2 : t1, second));
  const setup = k.handle >= Math.max(k.intD, k.reb) ? "pass" : "screen";
  out.push(mv(t1, setup));
  const st = STATUS[t1] || STATUS[t2], wx = WEATHER_MOVE[t1] || WEATHER_MOVE[t2];
  if (st) out.push({ name: st[0], type: STATUS[t1] ? t1 : t2, cat: "status", pp: PP.status, st: st[1] });
  else if (wx) out.push({ name: wx[0], type: WEATHER_MOVE[t1] ? t1 : t2, cat: "weather", pp: PP.weather, wx: wx[1] });
  else { const best = Object.keys(FALLBACK_BOOST).sort((a, b) => k[b] - k[a])[0], [name, boost] = FALLBACK_BOOST[best]; out.push({ name, type: "normal", cat: "boost", pp: 3, boost }); }
  // the signature move renames (and powers up) its category's move
  const sc = SIG_CAT[p?.effect], i = sc ? out.findIndex((x) => x.cat === sc) : -1;
  if (i >= 0 && p.move) {
    out[i] = { ...out[i], name: p.move, sig: true };
    out.forEach((x, j) => { if (j !== i && x.name === p.move) out[j] = { ...x, name: x.cat === "pass" ? "Swift" : x.cat === "screen" ? "Protect" : MOVES.normal[["deep", "rim"].indexOf(x.cat)], type: x.cat === "pass" || x.cat === "screen" ? x.type : "normal" }; });
  }
  return out;
}

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const clone = (x) => JSON.parse(JSON.stringify(x));

/** A new game. rosters: [[8 dex], [8 dex]]; starters: [[3 roster idx], [3 roster idx]]. Side 0 gets the ball first unless rng flips. */
export function newBattle(rosters, starters, rng, court = "clear") {
  return {
    court, stage: rosters.map((r) => r.map(() => ({}))),
    rosters, on: starters.map((s) => s.slice(0, ON)), score: [0, 0], off: rng.int(2), ball: 0, clock: CLOCK, open: [0, 0, 0],
    guard: [0, 1, 2], dead: true, turn: 0, winner: -1, weather: null, hot: [-1, -1], streak: [0, 0],
    stam: rosters.map((r) => r.map(() => 100)), pp: rosters.map((r) => r.map((n) => movesOf(n).map((m) => m.pp))),
    status: rosters.map((r) => r.map(() => null)),
  };
}

const who = (B, side, slot) => B.on[side][slot];
const dex = (B, side, slot) => B.rosters[side][who(B, side, slot)];
const legs = (B, side, ri) => 0.72 + 0.28 * (B.stam[side][ri] / 100);   // tired legs: skills scale to 72% when empty
function sk(B, side, slot) {
  const ri = who(B, side, slot), k = skills(B.rosters[side][ri]), s = B.status[side][ri], f = legs(B, side, ri) * (s?.k === "par" ? 0.85 : 1)
    * (s?.k === "slp" ? 0.35 : s?.k === "cnf" ? 0.8 : 1), stg = B.stage?.[side][ri] || {};
  return Object.fromEntries(Object.entries(k).map(([key, v]) => [key, Math.min(1.2, v * f * stageMult(stg[key] || 0))]));
}
/** Speed for turn order: base Speed × stage, halved when paralyzed. */
export function speedOf(B, side, slot) {
  const ri = who(B, side, slot);
  return mon(B.rosters[side][ri]).spe * stageMult(B.stage?.[side][ri]?.spe || 0) * (B.status[side][ri]?.k === "par" ? 0.5 : 1);
}
/** The weather in play: a weather move's (for a few turns), else the court's. */
export const weatherOf = (B) => B.weather?.k || (B.court !== "clear" ? B.court : null);

/** Everything a side may pick this turn: [{id, kind, slot, mi?, to?, scheme?}] (ids are stable strings). */
export function options(B, side) {
  const out = [];
  if (B.winner >= 0) return out;
  const usable = (slot, mi) => B.pp[side][who(B, side, slot)][mi] > 0;
  if (side === B.off) {
    const h = B.ball, hStat = B.status[side][who(B, side, h)];
    movesOf(dex(B, side, h)).forEach((m, mi) => {
      if (hStat?.k === "slp" || !usable(h, mi)) return;
      if (m.cat === "deep" || m.cat === "rim") out.push({ id: `m${h}.${mi}`, kind: "move", slot: h, mi });
      if (m.cat === "pass") for (let t = 0; t < ON; t++) if (t !== h) out.push({ id: `m${h}.${mi}>${t}`, kind: "move", slot: h, mi, to: t });
    });
    if (hStat?.k !== "slp") for (let t = 0; t < ON; t++) if (t !== h) out.push({ id: `p>${t}`, kind: "pass", slot: h, to: t });
    for (let s = 0; s < ON; s++) {
      if (B.status[side][who(B, side, s)]?.k === "slp") continue;
      movesOf(dex(B, side, s)).forEach((m, mi) => {
        if (!usable(s, mi)) return;
        if ((m.cat === "screen" && s !== h) || UTIL.has(m.cat)) out.push({ id: `m${s}.${mi}`, kind: "move", slot: s, mi });
      });
    }
    if (!out.some((o) => o.kind === "move" && ["deep", "rim"].includes(movesOf(dex(B, side, o.slot))[o.mi].cat)) && hStat?.k !== "slp")
      out.push({ id: "heave", kind: "heave", slot: h });   // out of shot PP: a plain heave
    if (!out.length) out.push({ id: "hold", kind: "hold" });
  } else {
    for (const k of Object.keys(SCHEMES)) out.push({ id: `s.${k}`, kind: "scheme", scheme: k });
    for (let s = 0; s < ON; s++) {
      if (B.status[side][who(B, side, s)]?.k === "slp") continue;
      movesOf(dex(B, side, s)).forEach((m, mi) => {
        if (usable(s, mi) && UTIL.has(m.cat)) out.push({ id: `m${s}.${mi}`, kind: "move", slot: s, mi });
      });
    }
  }
  return out;
}

/** Make-chance for the handler's shot move vs a scheme (also what the bots reason with). */
export function shotChance(B, mv, scheme, extra = 0) {
  const o = B.off, d = 1 - o, h = B.ball, g = B.guard[h], K = sk(B, o, h), D = sk(B, d, g);
  const n = dex(B, o, h), fx = effectOf(n), dfx = effectOf(dex(B, d, g)), deep = mv.cat === "deep";
  let p = deep ? 0.265 : 0.54;
  if (deep) p += 0.55 * (K.shoot - D.perD);
  else { const help = Math.max(...[0, 1, 2].map((s) => sk(B, d, s).intD)); p += 0.55 * (K.finish - (0.6 * D.intD + 0.4 * help)); }
  p += (deep ? 0.065 : 0.09) * B.open[h] + (VS[mv.cat]?.[scheme] || 0) + extra;
  const e = typeEff(mv.type, dex(B, d, g));
  p += e >= 2 ? 0.08 : e === 0 ? -0.15 : e < 1 ? -0.05 : 0;
  if (mon(n).types.includes(mv.type)) p += 0.03;                                       // STAB
  if (mv.sig) p += 0.03;
  if (mv.pow) p += mv.pow;
  if (weatherOf(B)) {
    const w = weatherOf(B);
    if ((w === "rain" && mv.type === "water") || (w === "sun" && mv.type === "fire")) p += 0.07;
    if ((w === "rain" && mv.type === "fire") || (w === "sun" && mv.type === "water")) p -= 0.07;
  }
  if (fx === "SHARPSHOOTER" && deep) p += 0.06;
  if ((fx === "SLASHER" || fx === "POSTERIZER") && !deep) p += 0.06;
  if (fx === "CLUTCH" && Math.max(...B.score) >= TO - 3) p += 0.08;
  if (fx === "HEAT_CHECK" && B.hot[o] === who(B, o, h)) p += 0.08;
  if (dfx === "LOCKDOWN" && deep) p -= 0.06;
  if (!deep && [0, 1, 2].some((s) => ["INTIMIDATOR", "RIM_PROTECTOR"].includes(effectOf(dex(B, d, s))))) p -= 0.04;
  if (B.status[o][who(B, o, h)]?.k === "par") p -= 0.05;
  return clamp(p, 0.06, 0.9);
}

/** Resolve one turn: offense pick + defense pick (option ids or objects). Returns {B, ev: [events]} without mutating B. */
export function resolve(B0, offPick, defPick, rng) {
  const B = clone(B0), o = B.off, d = 1 - o, ev = [];
  const find = (side, p) => (typeof p === "string" ? options(B0, side).find((x) => x.id === p) : p) || options(B0, side)[0];
  const op = find(o, offPick), dp = find(d, defPick);
  const scheme = dp.kind === "scheme" ? dp.scheme : "man";
  const say = (k, x = {}) => ev.push({ k, ...x });
  B.turn++; B.dead = false;
  if (op.kind === "move") B.pp[o][who(B, o, op.slot)][op.mi]--;
  if (dp.kind === "move") B.pp[d][who(B, d, dp.slot)][dp.mi]--;
  say("pick", { off: op, def: dp, scheme });

  // speed: a defender's status move on the handler can land before the handler acts
  const hRi = who(B, o, B.ball), spe = (side, slot) => speedOf(B, side, slot);
  let handlerStopped = false;
  const statusHit = (side, slot, mv, tSide, tSlot) => {
    const tRi = who(B, tSide, tSlot), tn = B.rosters[tSide][tRi], immune = (mv.st === "par" && mon(tn).types.includes("ground")) || (mv.st === "psn" && mon(tn).types.some((t) => t === "poison" || t === "steel"));
    const acc = effectOf(dex(B, side, slot)) === "HYPNOTIST" ? 1 : 0.75;
    if (B.status[tSide][tRi] || immune || rng.float() >= acc) { say("statusMiss", { side, slot, move: mv.name, tSide, tSlot, immune }); return false; }
    B.status[tSide][tRi] = { k: mv.st, t: mv.st === "slp" ? 2 : mv.st === "psn" ? 99 : 3 };
    say("status", { side, slot, move: mv.name, st: mv.st, tSide, tSlot });
    return true;
  };
  const defMove = dp.kind === "move" ? movesOf(dex(B, d, dp.slot))[dp.mi] : null;
  const defFirst = (defMove?.cat === "status" || defMove?.cat === "debuff") && spe(d, dp.slot) > spe(o, B.ball);
  const defAct = () => {
    if (!defMove) return;
    if (defMove.cat === "weather") { B.weather = { k: defMove.wx, t: 6 }; say("weather", { side: d, slot: dp.slot, move: defMove.name, wx: defMove.wx }); }
    else if (defMove.cat === "boost") stageUp(B, d, dp.slot, defMove, say);
    else if (defMove.cat === "debuff") stageDown(B, d, dp.slot, defMove, o, B.ball, say);
    else if (statusHit(d, dp.slot, defMove, o, B.ball) && ["slp", "par"].includes(defMove.st)) handlerStopped = defFirst;
  };
  if (defFirst) defAct();

  // the offense's action (status on the actor can stop it)
  const actorSlot = op.kind === "hold" ? B.ball : op.slot, aRi = who(B, o, actorSlot), aSt = B.status[o][aRi];
  let ended = false;
  const turnover = (why, by = -1) => { say("turnover", { why, slot: B.ball, by }); flip(B, false); ended = true; };
  if (handlerStopped && actorSlot === B.ball && B.status[o][hRi]?.k === "slp") say("asleep", { slot: B.ball });
  else if (aSt?.k === "par" && rng.float() < 0.25) say("fullyPar", { slot: actorSlot });
  else if (aSt?.k === "cnf" && rng.float() < 0.33) {
    if (actorSlot === B.ball) turnover("confused"); else say("confusedSelf", { slot: actorSlot });
  } else if (op.kind === "hold") say("hold", { slot: B.ball });
  else {
    const mv = op.kind === "move" ? movesOf(dex(B, o, op.slot))[op.mi] : op.kind === "heave" ? { name: "Heave", type: "normal", cat: "deep" } : { name: "Pass", type: "normal", cat: "pass" };
    if (mv.cat === "deep" || mv.cat === "rim") {
      const p = shotChance(B, mv, scheme, op.kind === "heave" ? -0.12 : 0), made = rng.float() < p;
      const fouled = mv.cat === "rim" && rng.float() < FOUL[scheme], ft = fouled && !made && rng.float() < 0.7;
      const pts = mv.cat === "deep" ? 2 : 1 + (fouled && made ? 1 : 0) + (ft ? 1 : 0);
      const eff = typeEff(mv.type, dex(B, d, B.guard[B.ball]));
      say("shot", { slot: B.ball, move: mv.name, cat: mv.cat, type: mv.type, made: made || ft, pts, eff, vs: scheme, fouled, ft, andOne: fouled && made });
      B.stam[o][hRi] -= 9;
      if (made || ft) {
        B.score[o] += pts; B.hot[o] = hRi;
        if (B.score[o] >= TO) { B.winner = o; say("win", { side: o }); }
        flip(B, true); ended = true;
      } else {
        B.hot[o] = -1;
        const oR = Math.max(...[0, 1, 2].map((s) => sk(B, o, s).reb)), dR = Math.max(...[0, 1, 2].map((s) => sk(B, d, s).reb));
        const glass = (side) => ([0, 1, 2].some((s) => effectOf(dex(B, side, s)) === "GLASS_CLEANER") ? 0.08 : 0);
        const pOreb = clamp(0.24 + 0.5 * (oR - dR) + glass(o) - glass(d) + (scheme === "pack" ? -0.07 : 0) + (mv.cat === "deep" ? 0.03 : 0), 0.08, 0.5);
        if (rng.float() < pOreb) {
          const r = weighted(rng, [0, 1, 2].map((s) => sk(B, o, s).reb ** 2));
          say("oreb", { slot: r }); B.ball = r; B.clock = OREB_CLOCK; B.open = [0, 0, 0]; B.open[r] = 1; B.guard = [0, 1, 2];
        } else { say("dreb", { slot: weighted(rng, [0, 1, 2].map((s) => sk(B, d, s).reb ** 2)) }); flip(B, false); ended = true; }
      }
    } else if (mv.cat === "pass") {
      const plus = op.kind === "move" ? 1 : 0, fg = [0, 1, 2].some((s) => effectOf(dex(B, o, s)) === "FLOOR_GENERAL") ? 1 : 0;
      const pick = [0, 1, 2].some((s) => effectOf(dex(B, d, s)) === "PICKPOCKET") ? 0.08 : 0;
      const steal = scheme === "deny" ? clamp(0.32 - 0.25 * sk(B, o, B.ball).handle + pick, 0.08, 0.4) : 0.02 + pick / 2;
      B.stam[o][hRi] -= 4;
      if (rng.float() < steal) turnover("steal", B.guard[op.to]);
      else {
        const gain = VS.pass[scheme] + plus + fg;
        say("pass", { from: B.ball, to: op.to, move: mv.name, gain });
        B.ball = op.to; B.open[op.to] = clamp(B.open[op.to] + gain, 0, MAX_OPEN);
      }
    } else if (mv.cat === "screen") {
      const gain = VS.screen[scheme], steal = scheme === "press" ? 0.04 : 0;
      B.stam[o][aRi] -= 6;
      if (rng.float() < steal) turnover("steal", B.guard[B.ball]);
      else {
        say("screen", { slot: op.slot, move: mv.name, gain, switched: scheme === "switch" });
        B.open[B.ball] = clamp(B.open[B.ball] + gain, 0, MAX_OPEN);
        if (scheme === "switch") { const g = B.guard[B.ball]; B.guard[B.ball] = B.guard[op.slot]; B.guard[op.slot] = g; }   // mismatch
      }
    } else if (mv.cat === "status") statusHit(o, op.slot, mv, d, B.guard[B.ball]);
    else if (mv.cat === "weather") { B.weather = { k: mv.wx, t: 6 }; say("weather", { side: o, slot: op.slot, move: mv.name, wx: mv.wx }); }
    else if (mv.cat === "boost") stageUp(B, o, op.slot, mv, say);
    else if (mv.cat === "debuff") stageDown(B, o, op.slot, mv, d, B.guard[B.ball], say);
  }
  if (!defFirst && !ended) defAct();
  else if (!defFirst && defMove?.cat === "weather") defAct();

  // the clock, legs, status timers, weather
  if (!ended) { B.clock--; if (B.clock <= 0) { say("violation", {}); flip(B, true); ended = true; } }
  for (const side of [0, 1]) for (let ri = 0; ri < B.rosters[side].length; ri++) {
    const on = B.on[side].includes(ri), st = B.status[side][ri];
    let dS = on ? (side === d && scheme === "press" && dp.kind === "scheme" ? -6 : -3) : 6;
    if (on && weatherOf(B) === "sand" && !mon(B.rosters[side][ri]).types.some((t) => ["rock", "ground", "steel"].includes(t))) dS -= 3;
    if (on && st?.k === "psn") dS -= 6;
    B.stam[side][ri] = clamp(B.stam[side][ri] + dS, 0, 100);
    if (st && on && st.k !== "psn" && --st.t <= 0) { B.status[side][ri] = null; say("cured", { side, ri, st: st.k }); }
  }
  if (B.weather && --B.weather.t <= 0) { say("weatherEnd", { wx: B.weather.k }); B.weather = null; }
  return { B, ev };
}

function stageUp(B, side, slot, mv, say) {
  const ri = who(B, side, slot), st = B.stage[side][ri], got = {};
  for (const [k, v] of Object.entries(mv.boost || {})) { const nv = Math.max(-STAGE_MAX, Math.min(STAGE_MAX, (st[k] || 0) + v)); if (nv !== (st[k] || 0)) got[k] = nv - (st[k] || 0); st[k] = nv; }
  if (mv.cost) B.stam[side][ri] = Math.max(0, B.stam[side][ri] - mv.cost);
  say("boost", { side, slot, move: mv.name, got });
}
function stageDown(B, side, slot, mv, tSide, tSlot, say) {
  const ri = who(B, tSide, tSlot), st = B.stage[tSide][ri], got = {};
  for (const [k, v] of Object.entries(mv.drop || {})) { const nv = Math.max(-STAGE_MAX, (st[k] || 0) + v); if (nv !== (st[k] || 0)) got[k] = nv - (st[k] || 0); st[k] = nv; }
  say("debuff", { side, slot, move: mv.name, tSide, tSlot, got });
}

function flip(B, dead) {
  B.off = 1 - B.off; B.ball = 0; B.clock = CLOCK; B.open = [0, 0, 0]; B.guard = [0, 1, 2]; B.dead = dead;
}
function weighted(rng, w) { let r = rng.float() * w.reduce((a, b) => a + b, 0); for (let i = 0; i < w.length; i++) if ((r -= w[i]) <= 0) return i; return w.length - 1; }

/** Sub at a dead ball: roster idx `inRi` comes in for court slot `slot`. Resting cures sleep / confusion / poison. */
export function sub(B0, side, slot, inRi) {
  if (!B0.dead || B0.on[side].includes(inRi) || inRi < 0 || inRi >= B0.rosters[side].length) return null;
  const B = clone(B0), outRi = B.on[side][slot];
  B.on[side][slot] = inRi; B.status[side][outRi] = null; B.stage[side][outRi] = {};
  return B;
}

// ── Bots: a one-step read of the other coach. Score every option against what the other side is likely to do
// (offense: points now + how much a setup is worth later; defense: the points it gives up), then pick by softmax, so
// a bot mixes it up and can't be farmed by always doing one thing. ──
const SCHEME_KEYS = Object.keys(SCHEMES);
// the handler's best shot, averaged over what the defense might run (what a setup is really buying)
function bestShot(B) {
  const o = B.off, n = dex(B, o, B.ball);
  if (B.status[o][who(B, o, B.ball)]?.k === "slp") return 0.15;
  let best = 0;
  movesOf(n).forEach((mv, mi) => {
    if ((mv.cat !== "deep" && mv.cat !== "rim") || B.pp[o][who(B, o, B.ball)][mi] <= 0) return;
    best = Math.max(best, SCHEME_KEYS.reduce((a, s) => a + shotValue(B, mv, s), 0) / SCHEME_KEYS.length);
  });
  return best;
}
// expected points: the make, the and-one / free throw at the rim, + what a miss is worth (offensive boards)
function shotValue(B, mv, scheme, extra = 0) {
  const p = shotChance(B, mv, scheme, extra);
  return mv.cat === "deep" ? 2 * p + 0.12 : p + FOUL[scheme] * (p + (1 - p) * 0.7) + 0.12;
}
const LOOK = 0.9;   // a setup costs a tick of the clock and some risk
function offValue(B, op, scheme) {
  if (op.kind === "hold") return 0;
  const o = B.off, mv = op.kind === "move" ? movesOf(dex(B, o, op.slot))[op.mi] : op.kind === "heave" ? { type: "normal", cat: "deep" } : { cat: "pass" };
  if (mv.cat === "deep" || mv.cat === "rim") return shotValue(B, mv, scheme, op.kind === "heave" ? -0.12 : 0);
  if (B.clock <= 1) return 0;   // anything but a shot on the last tick is a violation
  if (mv.cat === "pass") {
    const open = B.open.slice(); open[op.to] = Math.min(MAX_OPEN, open[op.to] + VS.pass[scheme] + (op.kind === "move" ? 1 : 0));
    const steal = scheme === "deny" ? clamp(0.32 - 0.25 * sk(B, o, B.ball).handle, 0.08, 0.4) : 0.02;
    return (1 - steal) * LOOK * bestShot({ ...B, ball: op.to, open });
  }
  if (mv.cat === "screen") {
    const open = B.open.slice(); open[B.ball] = Math.min(MAX_OPEN, open[B.ball] + VS.screen[scheme]);
    return (scheme === "press" ? 0.96 : 1) * LOOK * bestShot({ ...B, open });
  }
  if (mv.cat === "status") {
    const g = B.guard[B.ball], gRi = who(B, 1 - o, g);
    if (B.status[1 - o][gRi]) return 0;
    const status = B.status.slice(); status[1 - o] = B.status[1 - o].slice(); status[1 - o][gRi] = { k: mv.st, t: 2 };
    return LOOK * (0.75 * bestShot({ ...B, status }) + 0.25 * bestShot(B));
  }
  if (mv.cat === "weather") return weatherOf(B) === mv.wx ? 0 : LOOK * bestShot({ ...B, weather: { k: mv.wx, t: 6 } });
  if (mv.cat === "boost") return LOOK * bestShot(withStage(B, o, op.slot, mv.boost)) - (mv.cost || 0) / 400;
  if (mv.cat === "debuff") return LOOK * bestShot(withStage(B, 1 - o, B.guard[B.ball], mv.drop));
  return 0;
}
function withStage(B, side, slot, delta = {}) {
  const ri = who(B, side, slot), stage = B.stage.slice(); stage[side] = B.stage[side].slice();
  const st = { ...stage[side][ri] };
  for (const [k, v] of Object.entries(delta)) st[k] = Math.max(-STAGE_MAX, Math.min(STAGE_MAX, (st[k] || 0) + v));
  stage[side][ri] = st;
  return { ...B, stage };
}
const softPick = (rng, opts, vals, temp) => { const m = Math.max(...vals), w = vals.map((v) => Math.exp((v - m) / temp)); return opts[weighted(rng, w)]; };

export function botPick(B, side, rng, temp = 0.06) {
  const opts = options(B, side);
  if (opts.length <= 1) return opts[0];
  const schemes = Object.keys(SCHEMES);
  if (side === B.off) {
    const vals = opts.map((op) => schemes.reduce((a, s) => a + offValue(B, op, s), 0) / schemes.length);
    return softPick(rng, opts, vals, temp);
  }
  // defense: guess the offense's mix (softmax of its options vs man), then minimize what it gets
  const offOpts = options(B, B.off), ov = offOpts.map((op) => offValue(B, op, "man")), m = Math.max(...ov);
  const w = ov.map((v) => Math.exp((v - m) / 0.08)), W = w.reduce((a, b) => a + b, 0);
  const vals = opts.map((dp) => {
    if (dp.kind === "scheme") return -offOpts.reduce((a, op, i) => a + (w[i] / W) * offValue(B, op, dp.scheme), 0);
    const mv = movesOf(dex(B, side, dp.slot))[dp.mi], hRi = who(B, B.off, B.ball);
    if (mv.cat === "weather") return weatherOf(B) === mv.wx ? -9 : -bestShot({ ...B, weather: { k: mv.wx, t: 6 } }) - 0.04;
    if (mv.cat === "boost") return -bestShot(withStage(B, side, dp.slot, mv.boost)) - 0.1;
    if (mv.cat === "debuff") return -bestShot(withStage(B, B.off, B.ball, mv.drop)) - 0.08;
    if (B.status[B.off][hRi]) return -9;
    const status = B.status.slice(); status[B.off] = B.status[B.off].slice(); status[B.off][hRi] = { k: mv.st, t: 2 };
    return -(0.75 * bestShot({ ...B, status }) + 0.25 * bestShot(B)) - 0.1;   // playing it costs the scheme
  });
  return softPick(rng, opts, vals, temp);
}

/** Bot subs at a dead ball: rest anyone gassed or statused for the freshest bench player. */
export function botSubs(B, side) {
  let out = B;
  for (let slot = 0; slot < ON; slot++) {
    const ri = out.on[side][slot];
    if (out.stam[side][ri] > 45 && !out.status[side][ri]) continue;
    const bench = out.rosters[side].map((_, i) => i).filter((i) => !out.on[side].includes(i)).sort((a, b) => out.stam[side][b] - out.stam[side][a]);
    if (bench.length && out.stam[side][bench[0]] > out.stam[side][ri] + 25) out = sub(out, side, slot, bench[0]) || out;
  }
  return out;
}
