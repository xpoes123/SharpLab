// Tall Grass rules engine. Turn-based like the games: each turn every unit walks, then acts, by priority and Speed.
// Real: damage formula, STAB, type chart, crits, accuracy, stat stages, PP, Struggle, status, weather, abilities,
// wild movesets, split XP, level-up moves, evolution, Transform. Invented for the map: tiles, walking, move ranges.
export const W = 120, H = 72, TILE_M = 0.5, ZONE = 40, MAX_TURNS = 400;
const NEAR = 6, FAR = 16, BURST = 3, IV = 15, BK = 8;
const BW = Math.ceil(W / BK), BH = Math.ceil(H / BK);
const SPREAD = new Set(["allAdjacent", "allAdjacentFoes"]);
const STAGE = { atk: 0, def: 1, spa: 2, spd: 3, spe: 4, accuracy: 5, evasion: 6 };
const WEATHER = { raindance: "rain", sunnyday: "sun", sandstorm: "sand" };
const STRUGGLE = { id: "struggle", name: "Struggle", type: null, cat: "physical", pow: 50, acc: 0, contact: true, target: "normal", prio: 0, crit: 1 };
const SANDPROOF = new Set(["rock", "ground", "steel"]);

// Abilities the sim implements, in the player's words. Anything else shows as "not simulated".
export const ABILITIES = {
  intimidate: "Drops Attack of nearby foes when the enemy closes in.", pressure: "Attackers burn 2 PP per move.",
  static: "Contact may paralyze the attacker.", poisonpoint: "Contact may poison the attacker.",
  flamebody: "Contact may burn the attacker.", effectspore: "Contact may poison, paralyze, or sleep the attacker.",
  levitate: "Immune to Ground moves.", waterabsorb: "Water moves heal it instead.", voltabsorb: "Electric moves heal it instead.",
  flashfire: "Fire moves power up its own Fire moves instead.", lightningrod: "Electric moves raise its Sp. Atk instead.",
  thickfat: "Fire and Ice hit it half as hard.", overgrow: "Grass moves +50% at low HP.", blaze: "Fire moves +50% at low HP.",
  torrent: "Water moves +50% at low HP.", swarm: "Bug moves +50% at low HP.", guts: "Attack +50% while statused.",
  hustle: "Attack +50%, physical accuracy -20%.", hugepower: "Attack doubled.", purepower: "Attack doubled.",
  technician: "Moves of 60 power or less hit 50% harder.", ironfist: "Punches hit 20% harder.", sheerforce: "Moves with side effects hit 30% harder and lose the effect.",
  adaptability: "Same-type bonus is 2× instead of 1.5×.", multiscale: "Takes half damage at full HP.", marvelscale: "Defense +50% while statused.",
  filter: "Super-effective hits do 25% less.", solidrock: "Super-effective hits do 25% less.", sturdy: "Survives any hit from full HP with 1 HP.",
  rockhead: "No recoil.", shellarmor: "Can't be critically hit.", battlearmor: "Can't be critically hit.", compoundeyes: "Accuracy +30%.",
  keeneye: "Accuracy can't be lowered.", noguard: "Its moves, and moves against it, never miss.", swiftswim: "Speed doubled in rain.",
  chlorophyll: "Speed doubled in sun.", sandrush: "Speed doubled in sand.", speedboost: "Speed rises every turn.", sandveil: "Evades more in sand.",
  clearbody: "Foes can't lower its stats.", hypercutter: "Foes can't lower its Attack.", innerfocus: "Can't flinch.", insomnia: "Can't sleep.",
  vitalspirit: "Can't sleep.", limber: "Can't be paralyzed.", immunity: "Can't be poisoned.", owntempo: "Can't be confused.", waterveil: "Can't be burned.",
  magmaarmor: "Can't be frozen.", earlybird: "Wakes twice as fast.", shedskin: "30% chance a turn to cure status.", sniper: "Critical hits do 50% more.",
  tintedlens: "Resisted hits do double.", drizzle: "Starts rain.", drought: "Starts harsh sun.", sandstream: "Starts a sandstorm.",
};

let D = null;
let preEvo = new Map();
export function useData(data) {
  D = data;
  for (const [k, m] of Object.entries(data.moves)) m.id = k;
  preEvo = new Map(Object.values(data.species).filter((s) => s.evoTo).map((s) => [s.evoTo, s]));
}

// Levels this form can exist at: a Dragonite can't be under 55, a Rattata past 19 would have evolved.
export function levelRange(sp) {
  const before = preEvo.get(sp.num);
  return [before ? before.evoLevel : 5, sp.evoLevel ? sp.evoLevel - 1 : 100];
}

// Moves this form can know at this level: level-up moves learned by now, plus TMs and tutors.
export function legalMoves(sp, level) {
  return [...new Set([...sp.levelup.filter(([l]) => l <= level).map(([, m]) => m), ...sp.tm])].filter((m) => D.moves[m]);
}

const rand = (w) => {
  w.r = (w.r + 0x6d2b79f5) >>> 0;
  let t = Math.imul(w.r ^ (w.r >>> 15), 1 | w.r);
  t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
};

export const statAt = (base, level, isHp) =>
  Math.floor(((2 * base + IV) * level) / 100) + (isHp ? level + 10 : 5);

// Main-series order with floors between steps. `other` is spread/weather/crit, `after` is burn/screens/abilities.
export function damage({ level, power, atk, def, stab = 1, eff = 1, roll = 100, other = 1, after = 1 }) {
  if (eff === 0) return 0;
  let d = Math.floor(Math.floor(Math.floor((2 * level) / 5 + 2) * power * atk / def) / 50) + 2;
  d = Math.floor(d * other);
  d = Math.floor((d * roll) / 100);
  d = Math.floor(d * stab);
  d = Math.floor(d * eff);
  return Math.max(1, Math.floor(d * after));
}

const XP_AT = {
  slow: (n) => Math.floor((5 * n * n * n) / 4), medium: (n) => n * n * n, fast: (n) => Math.floor((4 * n * n * n) / 5),
  "medium-slow": (n) => Math.floor((6 * n * n * n) / 5) - 15 * n * n + 100 * n - 140,
};
export const xpAt = (growth, n) => (XP_AT[growth] ?? XP_AT.medium)(n);
const stageMul = (n) => (n >= 0 ? (2 + n) / 2 : 2 / (2 - n));
const accMul = (n) => (n >= 0 ? (3 + n) / 3 : 3 / (3 - n));
export const footprint = (sp) => Math.max(1, Math.min(10, Math.round((sp.heightm * 0.6) / TILE_M)));

// A wild Pokémon knows the last four moves it learned by level. One departure from the games: if that leaves it with
// no attack (level 43 Muk would only have Harden, Poison Gas, Minimize, Screech), it keeps its latest attack instead.
export function wildMoves(sp, level) {
  const out = [];
  let lastAttack = null;
  for (const [l, m] of sp.levelup) {
    if (l < 1 || l > level) continue;
    if (D.moves[m]?.cat !== "status") lastAttack = m;
    if (out.includes(m)) continue;
    out.push(m);
    if (out.length > 4) out.shift();
  }
  if (lastAttack && !out.some((m) => D.moves[m]?.cat !== "status")) out[0] = lastAttack;
  return out;
}

// Rough level a species is first seen at: one short of its level evolution, else its evolution level, else a default.
export function defaultLevel(sp) {
  if (sp.evoLevel) return Math.max(sp.evoLevel - 1, 5);
  if (sp.legendary) return 70;
  const pre = preEvo.get(sp.num);
  return pre ? Math.max(pre.evoLevel + 5, 30) : 40;
}

export const typeEff = (moveType, defTypes) =>
  !moveType ? 1 : defTypes.reduce((t, x) => t * (D.typeChart[moveType][x] ?? 1), 1);

function refreshStats(u) {
  if (u.tf) return;
  const b = D.species[u.num].base;
  const before = u.maxHp ?? 0;
  u.maxHp = statAt(b[0], u.level, true);
  u.atk = statAt(b[1], u.level); u.def = statAt(b[2], u.level); u.spa = statAt(b[3], u.level);
  u.spd = statAt(b[4], u.level); u.spe = statAt(b[5], u.level); u.baseSpe = b[5];
  if (before) u.hp += u.maxHp - before;
}

function setSpecies(u, num) {
  const sp = D.species[num];
  u.num = num; u.types = sp.types;
  u.ability = sp.abilities[u.slot] ?? sp.abilities[0];
  refreshStats(u);
}

function makeUnit(spec, side, id) {
  const sp = D.species[spec.num];
  const level = spec.level ?? defaultLevel(sp);
  const u = {
    id, side, num: spec.num, squad: spec.squad ?? null, level, slot: spec.slot ?? "0", size: footprint(sp), x: 0, y: 0, alive: true,
    st: [0, 0, 0, 0, 0, 0, 0], status: null, sleep: 0, tox: 0, conf: 0, flinch: false, busy: 0, seeded: -1, focus: 0, ffire: false,
    roared: false, kills: 0, dealt: 0, hitBy: [], evolved: 0, tf: null, moves: [],
  };
  setSpecies(u, spec.num);
  if (spec.ability) u.ability = spec.ability;
  u.moves = (spec.moves ?? wildMoves(sp, level)).filter((m) => D.moves[m]).slice(0, 4).map((m) => ({ id: m, left: D.moves[m].pp, tm: !!spec.moves }));
  u.hp = u.maxHp;
  u.xp = xpAt(sp.growth, level);
  return u;
}

const key = (x, y) => y * W + x;

function fits(occ, x, y, s, id) {
  if (x < 0 || y < 0 || x + s > W || y + s > H) return false;
  for (let j = 0; j < s; j++) for (let i = 0; i < s; i++) {
    const o = occ[key(x + i, y + j)];
    if (o !== 0 && o !== id + 1) return false;
  }
  return true;
}
function stamp(occ, u, v) {
  for (let j = 0; j < u.size; j++) for (let i = 0; i < u.size; i++) occ[key(u.x + i, u.y + j)] = v;
}

export function gap(a, b) {
  const dx = Math.max(0, b.x - (a.x + a.size - 1), a.x - (b.x + b.size - 1));
  const dy = Math.max(0, b.y - (a.y + a.size - 1), a.y - (b.y + b.size - 1));
  return Math.max(dx, dy);
}

// Offsets from an anchor, nearest first, stretched so blocks form a wide battle line rather than a circle.
const OFFSETS = (() => {
  const out = [];
  for (let dx = -W; dx <= W; dx++) for (let dy = -H; dy <= H; dy++) out.push([dx, dy, dx * dx * 2.89 + dy * dy]);
  return out.sort((a, b) => a[2] - b[2]);
})();

export const zoneOf = (side) => (side === 0 ? [0, ZONE] : [W - ZONE, W]);

function placeSquad(occ, units, ax, ay, spacing, side) {
  const [zx0, zx1] = zoneOf(side);
  let k = 0, lastSize = -1;
  for (const u of units) {
    if (u.size !== lastSize) { k = 0; lastSize = u.size; }
    // Giants are longer than they are wide on the ground: give their ranks room so sprites don't stack.
    const lattice = u.size >= 5 ? Math.round(u.size * 1.7) + spacing - 1 : u.size + spacing - 1;
    for (; k < OFFSETS.length; k++) {
      const [dx, dy] = OFFSETS[k];
      if (lattice > 1 && (dx % lattice !== 0 || dy % lattice !== 0)) continue;
      const x = ax + dx - (u.size >> 1), y = ay + dy - (u.size >> 1);
      if (x < zx0 || x + u.size > zx1 || !fits(occ, x, y, u.size, u.id)) continue;
      u.x = x; u.y = y;
      stamp(occ, u, u.id + 1);
      k++;
      break;
    }
  }
}

const isRanged = (sp, level, moves) => {
  const ids = moves ?? wildMoves(sp, level);
  const hits = ids.map((m) => D.moves[m]).filter((m) => m && m.cat !== "status");
  return hits.length > 0 && hits.every((m) => !m.contact);
};

// Default battle line: melee up front, ranged behind, big bodies in the middle of their rank.
export function autoAnchor(squads, side) {
  const order = squads
    .map((s) => ({ s, sp: D.species[s.num] }))
    .sort((a, b) => isRanged(a.sp, a.s.level ?? defaultLevel(a.sp), a.s.moves) - isRanged(b.sp, b.s.level ?? defaultLevel(b.sp), b.s.moves));
  let depth = 3;
  for (const { s, sp } of order) {
    const pitch = footprint(sp) >= 5 ? footprint(sp) * 1.7 : footprint(sp);
    const cell = pitch + (s.spacing ?? 1) - 1;
    const area = (s.count ?? 1) * cell * cell;
    const half = Math.ceil(Math.sqrt(area / 1.7) / 1.7);
    depth += half;
    s.x = side === 0 ? ZONE - depth : W - ZONE + depth;
    s.y = H >> 1;
    depth += half + 1;
  }
}

// squads: [{ side, num, count, level?, moves?, ability?, slot?, x?, y?, spacing? }]. Later squads fill around earlier ones.
export function createWorld(squads, seed = 1) {
  const w = {
    r: seed >>> 0, turn: 0, weather: null, screens: [{ reflect: 0, lightscreen: 0 }, { reflect: 0, lightscreen: 0 }],
    units: [], occ: new Int32Array(W * H), quiet: 0, over: false, peak: [0, 0],
  };
  for (const side of [0, 1]) {
    const mine = squads.filter((s) => s.side === side);
    if (mine.every((s) => s.x === undefined)) autoAnchor(mine, side);
    // A squad added after the player arranged things waits mid-zone instead of reshuffling their line.
    for (const s of mine) if (s.x === undefined) { s.x = side === 0 ? ZONE >> 1 : W - (ZONE >> 1); s.y = H >> 1; }
  }
  // Fill largest bodies first so a horde can't squat on the only space a giant fits.
  const order = squads.map((s, i) => [s, i]).sort((a, b) => footprint(D.species[b[0].num]) - footprint(D.species[a[0].num]) || a[1] - b[1]);
  for (const [s] of order) {
    const units = [];
    for (let i = 0; i < (s.count ?? 1); i++) {
      const u = makeUnit({ ...s, squad: s.id ?? null }, s.side, w.units.length);
      w.units.push(u);
      units.push(u);
    }
    placeSquad(w.occ, units, s.x, s.y, s.spacing ?? 1, s.side);
  }
  for (const u of w.units) w.peak[u.side] += u.maxHp;
  return w;
}

export const fork = (w, seed) => { const c = structuredClone(w); c.r = seed >>> 0; return c; };

const buckets = [Array.from({ length: BW * BH }, () => []), Array.from({ length: BW * BH }, () => [])];
function buildBuckets(w) {
  for (const b of buckets) for (const a of b) a.length = 0;
  for (const u of w.units) if (u.alive) buckets[u.side][(u.y >> 3) * BW + (u.x >> 3)].push(u);
}

function nearestFoe(u) {
  const b = buckets[1 - u.side], cx = u.x >> 3, cy = u.y >> 3;
  let best = null, bg = Infinity, bd = Infinity;
  for (let r = 0; r < BW; r++) {
    if (best && (r - 1) * BK > bg) break;
    for (let y = cy - r; y <= cy + r; y++) {
      if (y < 0 || y >= BH) continue;
      const edge = y === cy - r || y === cy + r;
      for (let x = cx - r; x <= cx + r; x += edge || r === 0 ? 1 : 2 * r) {
        if (x < 0 || x >= BW) continue;
        for (const o of b[y * BW + x]) {
          if (!o.alive) continue;
          const g = gap(u, o);
          const d = (u.x - o.x) * (u.x - o.x) + (u.y - o.y) * (u.y - o.y);
          if (g < bg || (g === bg && d < bd)) { bg = g; bd = d; best = o; }
        }
      }
    }
  }
  return best;
}

let tick = 1;
const seen = new Map();
function around(w, u, r) {
  tick++;
  const out = [];
  const x0 = Math.max(0, u.x - r), x1 = Math.min(W - 1, u.x + u.size - 1 + r);
  const y0 = Math.max(0, u.y - r), y1 = Math.min(H - 1, u.y + u.size - 1 + r);
  for (let y = y0; y <= y1; y++) for (let x = x0; x <= x1; x++) {
    const v = w.occ[key(x, y)];
    if (v === 0 || v === u.id + 1 || seen.get(v) === tick) continue;
    seen.set(v, tick);
    out.push(w.units[v - 1]);
  }
  return out;
}

const burst = (u) => BURST + (u.size >> 1);
const reach = (u, m) => (SPREAD.has(m.target) ? burst(u) : m.contact ? 1 : m.target === "any" ? FAR : NEAR);
const selfish = (m) => ["self", "allySide", "all", "allyTeam", "adjacentAllyOrSelf"].includes(m.target) || m.id === "teleport" || m.id === "splash";
const weatherOn = (w, k) => w.weather?.kind === k;

function speed(w, u) {
  let s = u.spe * stageMul(u.st[4]);
  if (u.status === "par") s *= 0.5;
  if ((u.ability === "swiftswim" && weatherOn(w, "rain")) || (u.ability === "chlorophyll" && weatherOn(w, "sun")) || (u.ability === "sandrush" && weatherOn(w, "sand"))) s *= 2;
  return s;
}
function steps(w, u) {
  let b = u.baseSpe * stageMul(u.st[4]);
  if (u.status === "par") b *= 0.5;
  if (speed(w, u) > u.spe * stageMul(u.st[4]) && u.status !== "par") b *= 2;
  return Math.max(1, Math.min(12, Math.round(2 + b / 28)));
}

function typeMult(m, o) {
  const a = o.ability;
  if ((m.type === "ground" && a === "levitate") || (m.type === "water" && a === "waterabsorb") || (m.type === "electric" && (a === "voltabsorb" || a === "lightningrod")) || (m.type === "fire" && a === "flashfire")) return 0;
  return typeEff(m.type, o.types);
}

function calc(w, u, o, m, roll, crit, spread) {
  const eff = typeMult(m, o);
  if (eff === 0) return { d: 0, eff: 0 };
  if (m.damage) return { d: m.damage === "level" ? u.level : m.damage, eff: 1 };
  if (m.id === "superfang") return { d: Math.max(1, o.hp >> 1), eff: 1 };
  if (m.id === "psywave") return { d: Math.max(1, Math.floor((u.level * roll) / 100)), eff: 1 };

  const phys = m.cat === "physical", ua = u.ability, oa = o.ability;
  let pow = m.pow;
  if (m.id === "flail" || m.id === "reversal") {
    const f = u.hp / u.maxHp;
    pow = f < 0.05 ? 200 : f < 0.1 ? 150 : f < 0.2 ? 100 : f < 0.35 ? 80 : f < 0.7 ? 40 : 20;
  }
  if (ua === "technician" && pow <= 60) pow *= 1.5;
  if (ua === "ironfist" && m.punch) pow *= 1.2;
  if (ua === "sheerforce" && m.sec) pow *= 1.3;

  let aSt = u.st[phys ? 0 : 2], dSt = o.st[phys ? 1 : 3];
  if (crit) { aSt = Math.max(aSt, 0); dSt = Math.min(dSt, 0); }
  let A = (phys ? u.atk : u.spa) * stageMul(aSt), Df = (phys ? o.def : o.spd) * stageMul(dSt);
  if (phys && (ua === "hugepower" || ua === "purepower")) A *= 2;
  if (phys && ua === "hustle") A *= 1.5;
  if (phys && ua === "guts" && u.status) A *= 1.5;
  if (u.hp * 3 <= u.maxHp && ((ua === "overgrow" && m.type === "grass") || (ua === "blaze" && m.type === "fire") || (ua === "torrent" && m.type === "water") || (ua === "swarm" && m.type === "bug"))) A *= 1.5;
  if (u.ffire && m.type === "fire") A *= 1.5;
  if (oa === "thickfat" && (m.type === "fire" || m.type === "ice")) A *= 0.5;
  if (oa === "marvelscale" && phys && o.status) Df *= 1.5;
  if (!phys && weatherOn(w, "sand") && o.types.includes("rock")) Df *= 1.5;

  let other = spread ? 0.75 : 1;
  if (weatherOn(w, "rain")) other *= m.type === "water" ? 1.5 : m.type === "fire" ? 0.5 : 1;
  if (weatherOn(w, "sun")) other *= m.type === "fire" ? 1.5 : m.type === "water" ? 0.5 : 1;
  if (crit) other *= ua === "sniper" ? 2.25 : 1.5;

  let after = 1;
  if (phys && u.status === "brn" && ua !== "guts") after *= 0.5;
  const scr = w.screens[o.side];
  if (!crit && ((phys && scr.reflect > 0) || (!phys && scr.lightscreen > 0))) after *= 0.5;
  if (oa === "multiscale" && o.hp === o.maxHp) after *= 0.5;
  if ((oa === "filter" || oa === "solidrock") && eff > 1) after *= 0.75;
  if (ua === "tintedlens" && eff < 1) after *= 2;

  const hasStab = !!m.type && u.types.includes(m.type);
  const d = damage({
    level: u.level, power: pow, atk: Math.max(1, Math.floor(A)), def: Math.max(1, Math.floor(Df)),
    stab: hasStab ? (ua === "adaptability" ? 2 : 1.5) : 1, eff, roll, other, after,
  });
  return { d, eff };
}

const hitCount = (w, m) => {
  if (!m.hits) return 1;
  if (typeof m.hits === "number") return m.hits;
  return [2, 2, 2, 3, 3, 3, 4, 5][Math.floor(rand(w) * 8)];
};
const avgHits = (m) => (!m.hits ? 1 : typeof m.hits === "number" ? m.hits : 3);

function chanceToHit(u, o, m) {
  if (u.ability === "noguard" || o.ability === "noguard" || !m.acc) return 101;
  let acc = m.acc * accMul(u.st[5] - o.st[6]);
  if (u.ability === "compoundeyes") acc *= 1.3;
  if (u.ability === "hustle" && m.cat === "physical") acc *= 0.8;
  return acc;
}

function expected(w, u, o, m) {
  if (m.ohko) return o.level > u.level ? 0 : o.hp * 0.3;
  const { d } = calc(w, u, o, m, 92, false, SPREAD.has(m.target));
  return Math.min(o.hp, d * avgHits(m)) * Math.min(1, chanceToHit(u, o, m) / 100);
}

function burstTargets(w, u, m) {
  return around(w, u, burst(u)).filter((o) => o.alive && (m.target === "allAdjacent" || o.side !== u.side));
}

function attackValue(w, u, t, m) {
  let s = 0;
  if (SPREAD.has(m.target)) {
    for (const o of burstTargets(w, u, m)) s += (o.side === u.side ? -0.4 : 1) * expected(w, u, o, m); // armies accept some friendly fire
  } else s = expected(w, u, t, m);
  if (m.faint) s -= u.hp * 0.45; // a unit about to fall takes the enemy with it
  if (m.busy) s *= 0.55;
  if (m.recoil && u.ability !== "rockhead") s -= (s * m.recoil[0]) / m.recoil[1] * 0.5;
  if (m.drain) s *= 1 + (0.5 * (u.maxHp - u.hp)) / u.maxHp;
  return s;
}

function statusValue(w, u, t, m, inReach) {
  const id = m.id, frac = u.hp / u.maxHp;
  if (m.heal || id === "rest" || id === "synthesis" || id === "moonlight" || id === "morningsun") {
    if (id === "rest") return frac < 0.35 && !u.status ? u.maxHp * 0.6 : 0;
    return frac < 0.5 ? u.maxHp * 0.5 : 0;
  }
  if (m.weather) {
    const kind = WEATHER[m.weather];
    return !kind || weatherOn(w, kind) ? 0 : weatherPlan(w, u.side, kind);
  }
  if (m.side) return w.screens[u.side][m.side] > 0 ? 0 : 18;
  if (id === "transform") return u.tf || !inReach ? 0 : 1e6;
  if (id === "metronome") return inReach ? 25 : 0;
  if (id === "focusenergy") return u.focus ? 0 : 6;
  if (id === "haze") return w.units.some((o) => o.alive && o.side !== u.side && o.st.some((x) => x > 1)) ? 20 : 0;
  if (id === "teleport") return frac < 0.4 && inReach ? 30 : 0;
  if (id === "splash") return 0;
  if (m.target === "self" && m.boosts) {
    const [stat, n] = Object.entries(m.boosts)[0];
    if (u.st[STAGE[stat]] >= 3 || n < 0) return 0;
    return frac > 0.6 ? 12 : 0;
  }
  if (!inReach) return 0;
  const accf = Math.min(1, chanceToHit(u, t, m) / 100);
  if (m.status) {
    if (t.status || !canHaveStatus(t, m.status) || (m.powder && t.types.includes("grass")) || typeEff(m.type, t.types) === 0) return 0;
    return t.hp * (m.status === "slp" ? 0.5 : 0.3) * accf;
  }
  if (m.vol === "confusion") return t.conf || t.ability === "owntempo" ? 0 : t.hp * 0.2 * accf;
  if (m.vol === "leechseed") return t.seeded >= 0 || t.types.includes("grass") ? 0 : t.hp * 0.25 * accf;
  if (m.boosts) {
    const [stat, n] = Object.entries(m.boosts)[0];
    if (n > 0 || t.st[STAGE[stat]] <= -2) return 0;
    const area = SPREAD.has(m.target) ? 3 : 1;
    return Math.min(t.hp, 60) * 0.12 * area * accf;
  }
  return 0;
}

function weatherPlan(w, side, kind) {
  let mine = 0, theirs = 0;
  for (const u of w.units) {
    if (!u.alive) continue;
    const hurt = kind === "sand" ? !u.types.some((t) => SANDPROOF.has(t)) : 0;
    const good = kind === "rain" ? u.types.includes("water") || u.ability === "swiftswim" : kind === "sun" ? u.types.includes("fire") || u.ability === "chlorophyll" : u.ability === "sandrush" || !hurt;
    const bad = kind === "rain" ? u.types.includes("fire") : kind === "sun" ? u.types.includes("water") : hurt;
    if (u.side === side) mine += (good ? 1 : 0) - (bad ? 1 : 0); else theirs += (good ? 1 : 0) - (bad ? 1 : 0);
  }
  return mine > theirs ? 15 + (mine - theirs) : 0;
}

function canHaveStatus(o, s) {
  const a = o.ability, ty = o.types;
  if ((s === "psn" || s === "tox") && (ty.includes("poison") || ty.includes("steel") || a === "immunity")) return false;
  if (s === "brn" && (ty.includes("fire") || a === "waterveil")) return false;
  if (s === "par" && (ty.includes("electric") || a === "limber")) return false;
  if (s === "frz" && (ty.includes("ice") || a === "magmaarmor")) return false;
  if (s === "slp" && (a === "insomnia" || a === "vitalspirit")) return false;
  return true;
}

function setStatus(w, o, s, ev) {
  if (o.status || !o.alive || !canHaveStatus(o, s)) return false;
  o.status = s;
  if (s === "slp") o.sleep = 1 + Math.floor(rand(w) * 3);
  if (s === "tox") o.tox = 1;
  ev?.push({ k: "status", o: o.id, s });
  return true;
}

function boost(o, stat, n, foeCaused, ev) {
  if (!o.alive) return;
  if (foeCaused && n < 0 && (o.ability === "clearbody" || (stat === "atk" && o.ability === "hypercutter") || (stat === "accuracy" && o.ability === "keeneye"))) return;
  const i = STAGE[stat];
  if (i === undefined) return;
  const next = Math.max(-6, Math.min(6, o.st[i] + n));
  if (next === o.st[i]) return;
  o.st[i] = next;
  ev?.push({ k: "boost", o: o.id, stat, n });
}

function faint(w, o, ev) {
  o.alive = false;
  stamp(w.occ, o, 0);
  ev?.push({ k: "faint", o: o.id });
  w.quiet = 0;
  // Experience splits between everyone who fought it, with the trainer-battle 1.5× bonus.
  const fighters = o.hitBy.map((id) => w.units[id]).filter((u) => u.alive && u.side !== o.side);
  if (!fighters.length) return;
  const each = Math.max(1, Math.floor((D.species[o.num].baseExp * o.level * 1.5) / 7 / fighters.length));
  for (const u of fighters) gainXp(w, u, each, ev);
}

export function gainXp(w, u, amount, ev) {
  if (u.tf) return;
  u.xp += amount;
  const growth = D.species[u.num].growth;
  while (u.level < 100 && u.xp >= xpAt(growth, u.level + 1)) levelUp(w, u, ev);
}

function learn(u, level) {
  for (const [l, id] of D.species[u.num].levelup) {
    if (l !== level || u.moves.some((s) => s.id === id)) continue;
    const slot = { id, left: D.moves[id].pp, tm: false };
    if (u.moves.length < 4) u.moves.push(slot);
    else {
      // forget the oldest move, unless that would leave it with no attack
      const attacks = u.moves.filter((s) => D.moves[s.id]?.cat !== "status").length;
      const gainsAttack = D.moves[id]?.cat !== "status";
      const i = u.moves.findIndex((s) => !s.tm && (gainsAttack || attacks > 1 || D.moves[s.id]?.cat === "status"));
      if (i >= 0) { u.moves.splice(i, 1); u.moves.push(slot); }
    }
  }
}

function levelUp(w, u, ev) {
  u.level++;
  refreshStats(u);
  learn(u, u.level);
  ev?.push({ k: "level", u: u.id, level: u.level });
  const sp = D.species[u.num];
  if (sp.evoLevel && u.level >= sp.evoLevel) evolve(w, u, sp.evoTo, ev);
}

export function evolve(w, u, to, ev) {
  const from = u.num;
  setSpecies(u, to);
  learn(u, u.level);
  u.evolved++;
  const size = footprint(D.species[to]);
  if (size !== u.size && w) {
    stamp(w.occ, u, 0);
    const grown = fits(w.occ, u.x, u.y, size, u.id) ? [u.x, u.y] : fits(w.occ, u.x - (size - u.size), u.y - (size - u.size), size, u.id) ? [u.x - (size - u.size), u.y - (size - u.size)] : null;
    if (grown) { [u.x, u.y] = grown; u.size = size; }
    stamp(w.occ, u, u.id + 1);
  }
  ev?.push({ k: "evolve", u: u.id, from, to });
}

function hurt(w, o, d, src, ev, why) {
  if (d <= 0 || !o.alive) return;
  o.hp -= d;
  if (src && src.side !== o.side) {
    src.dealt += d;
    if (!o.hitBy.includes(src.id)) { if (o.hitBy.length >= 8) o.hitBy.shift(); o.hitBy.push(src.id); }
  }
  w.quiet = 0;
  if (why) ev?.push({ k: "chip", o: o.id, d, why });
  if (o.hp <= 0) {
    if (src && src.side !== o.side && src.alive) src.kills++;
    faint(w, o, ev);
  }
}

function contactEffects(w, u, o, ev) {
  const a = o.ability;
  if (rand(w) >= 0.3) return;
  if (a === "static") setStatus(w, u, "par", ev);
  else if (a === "poisonpoint") setStatus(w, u, "psn", ev);
  else if (a === "flamebody") setStatus(w, u, "brn", ev);
  else if (a === "effectspore" && !u.types.includes("grass")) setStatus(w, u, ["psn", "par", "slp"][Math.floor(rand(w) * 3)], ev);
}

function secondary(w, u, o, sec, ev) {
  if (!sec || u.ability === "sheerforce" || rand(w) * 100 >= sec.chance) return;
  if (sec.status) setStatus(w, o, sec.status, ev);
  if (sec.boosts) for (const [s, n] of Object.entries(sec.boosts)) boost(o, s, n, true, ev);
  if (sec.selfBoosts) for (const [s, n] of Object.entries(sec.selfBoosts)) boost(u, s, n, false, ev);
  if (sec.vol === "flinch" && o.ability !== "innerfocus") o.flinch = true;
  if (sec.vol === "confusion" && o.ability !== "owntempo" && !o.conf) { o.conf = 2 + Math.floor(rand(w) * 4); ev?.push({ k: "status", o: o.id, s: "conf" }); }
}

function isCrit(w, u, o, m) {
  if (o.ability === "shellarmor" || o.ability === "battlearmor") return false;
  const stage = (m.crit ?? 1) - 1 + (u.focus ? 2 : 0);
  return rand(w) < (stage <= 0 ? 1 / 24 : stage === 1 ? 1 / 8 : stage === 2 ? 1 / 2 : 1);
}

function strike(w, u, m, t, ev) {
  const spread = SPREAD.has(m.target);
  const targets = spread ? burstTargets(w, u, m) : [t];
  ev?.push({ k: "use", u: u.id, m: m.id, t: spread ? null : t.id, x: u.x, y: u.y });
  let dealt = 0;
  for (const o of targets) {
    if (!o.alive) continue;
    if (m.ohko) {
      const eff = typeEff(m.type, o.types);
      if (eff === 0 || o.level > u.level || o.ability === "sturdy" || rand(w) * 100 >= 30 + u.level - o.level) { ev?.push({ k: "miss", u: u.id, o: o.id }); continue; }
      ev?.push({ k: "hit", u: u.id, o: o.id, d: o.hp, eff: 1, crit: false, m: m.id, ohko: true });
      hurt(w, o, o.hp, u, ev);
      continue;
    }
    const immune = typeMult(m, o) === 0;
    if (immune) {
      const a = o.ability;
      if (a === "waterabsorb" || a === "voltabsorb") {
        const heal = Math.min(o.maxHp - o.hp, o.maxHp >> 2);
        if (heal > 0) { o.hp += heal; ev?.push({ k: "heal", u: o.id, n: heal }); }
      } else if (a === "flashfire") o.ffire = true;
      else if (a === "lightningrod") boost(o, "spa", 1, false, ev);
      ev?.push({ k: "immune", o: o.id });
      continue;
    }
    if (rand(w) * 100 >= chanceToHit(u, o, m)) { ev?.push({ k: "miss", u: u.id, o: o.id }); continue; }
    const hits = hitCount(w, m);
    for (let h = 0; h < hits && o.alive; h++) {
      const crit = isCrit(w, u, o, m);
      const roll = 85 + Math.floor(rand(w) * 16);
      let { d, eff } = calc(w, u, o, m, roll, crit, spread);
      if (o.ability === "sturdy" && o.hp === o.maxHp && d >= o.hp) d = o.hp - 1;
      if (m.id === "falseswipe" && d >= o.hp) d = o.hp - 1;
      ev?.push({ k: "hit", u: u.id, o: o.id, d, eff, crit, m: m.id });
      dealt += d;
      hurt(w, o, d, u, ev);
      if (m.contact && o.alive && u.alive) contactEffects(w, u, o, ev);
    }
    if (o.alive) secondary(w, u, o, m.sec, ev);
  }
  if (!u.alive) return;
  if (m.id === "struggle") hurt(w, u, Math.max(1, u.maxHp >> 2), null, ev, "struggle");
  else if (m.recoil && u.ability !== "rockhead" && dealt) hurt(w, u, Math.max(1, Math.floor((dealt * m.recoil[0]) / m.recoil[1])), null, ev, "recoil");
  if (m.drain && dealt && u.alive) {
    const heal = Math.min(u.maxHp - u.hp, Math.floor((dealt * m.drain[0]) / m.drain[1]));
    if (heal > 0) { u.hp += heal; ev?.push({ k: "heal", u: u.id, n: heal }); }
  }
  if (m.selfBoosts) for (const [s, n] of Object.entries(m.selfBoosts)) boost(u, s, n, false, ev);
  if (m.faint && u.alive) { u.hp = 0; faint(w, u, ev); }
  if (m.busy) u.busy = 1; // ponytail: charge moves land first, then idle; same tempo as charging first, not the same dodge window
}

function statusMove(w, u, m, t, ev) {
  const id = m.id;
  ev?.push({ k: "use", u: u.id, m: id, t: selfish(m) ? null : t?.id ?? null, x: u.x, y: u.y });
  if (m.heal || id === "synthesis" || id === "moonlight" || id === "morningsun") {
    const frac = m.heal ? m.heal[0] / m.heal[1] : weatherOn(w, "sun") ? 2 / 3 : w.weather ? 1 / 4 : 1 / 2;
    const n = Math.min(u.maxHp - u.hp, Math.floor(u.maxHp * frac));
    if (n > 0) { u.hp += n; ev?.push({ k: "heal", u: u.id, n }); }
    return;
  }
  if (id === "rest") { u.hp = u.maxHp; u.status = "slp"; u.sleep = 2; ev?.push({ k: "status", o: u.id, s: "slp" }); return; }
  if (m.weather) {
    const kind = WEATHER[m.weather];
    if (kind) setWeather(w, kind, 5, ev);
    return;
  }
  if (m.side) { w.screens[u.side][m.side] = 5; ev?.push({ k: "screen", side: u.side, s: m.side }); return; }
  if (id === "transform") return transform(u, t, ev);
  if (id === "focusenergy") { u.focus = 1; return; }
  if (id === "haze") { for (const o of w.units) if (o.alive) o.st.fill(0); return; }
  if (id === "splash") { ev?.push({ k: "splash", u: u.id }); return; }
  if (id === "teleport") return teleport(w, u, ev);
  if (m.target === "self") { for (const [s, n] of Object.entries(m.boosts ?? {})) boost(u, s, n, false, ev); return; }

  const targets = SPREAD.has(m.target) ? burstTargets(w, u, m).filter((o) => o.side !== u.side) : t ? [t] : [];
  for (const o of targets) {
    if (rand(w) * 100 >= chanceToHit(u, o, m)) { ev?.push({ k: "miss", u: u.id, o: o.id }); continue; }
    if (m.status && !(m.powder && o.types.includes("grass")) && typeEff(m.type, o.types) !== 0) setStatus(w, o, m.status, ev);
    if (m.boosts) for (const [s, n] of Object.entries(m.boosts)) boost(o, s, n, true, ev);
    if (m.vol === "confusion" && !o.conf && o.ability !== "owntempo") { o.conf = 2 + Math.floor(rand(w) * 4); ev?.push({ k: "status", o: o.id, s: "conf" }); }
    if (m.vol === "leechseed" && !o.types.includes("grass")) { o.seeded = u.id; ev?.push({ k: "status", o: o.id, s: "seed" }); }
  }
}

function transform(u, t, ev) {
  u.tf = { num: t.num };
  u.types = t.types; u.ability = t.ability;
  u.atk = t.atk; u.def = t.def; u.spa = t.spa; u.spd = t.spd; u.spe = t.spe; u.baseSpe = t.baseSpe;
  u.st = [...t.st];
  u.moves = t.moves.map((s) => ({ id: s.id, left: 5, tm: true }));
  ev?.push({ k: "transform", u: u.id, to: t.num });
}

function teleport(w, u, ev) {
  for (let i = 0; i < 12; i++) {
    const x = Math.floor(rand(w) * (W - u.size)), y = Math.floor(rand(w) * (H - u.size));
    if (!fits(w.occ, x, y, u.size, u.id) || (u.side === 0 ? x > u.x : x < u.x)) continue;
    stamp(w.occ, u, 0);
    u.x = x; u.y = y;
    stamp(w.occ, u, u.id + 1);
    ev?.push({ k: "warp", u: u.id, x, y });
    return;
  }
}

function setWeather(w, kind, turns, ev) {
  if (w.weather?.kind === kind) { w.weather.turns = Math.max(w.weather.turns, turns); return; }
  w.weather = { kind, turns };
  ev?.push({ k: "weather", kind });
}

function walk(w, u, t, stopAt, ev) {
  const sx = u.x, sy = u.y, n = steps(w, u), s = u.size;
  const tcx = t.x + (t.size - 1) / 2, tcy = t.y + (t.size - 1) / 2;
  let px = -1, py = -1;
  for (let i = 0; i < n && gap(u, t) > stopAt; i++) {
    const cx = u.x + (s - 1) / 2, cy = u.y + (s - 1) / 2;
    const cur = (cx - tcx) * (cx - tcx) + (cy - tcy) * (cy - tcy);
    let bx = -1, by = -1, bd = Infinity;
    for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
      if (!dx && !dy) continue;
      const x = u.x + dx, y = u.y + dy;
      if (x === px && y === py) continue;
      const ex = x + (s - 1) / 2 - tcx, ey = y + (s - 1) / 2 - tcy, d = ex * ex + ey * ey;
      if (d >= bd || d > cur + 3 || !fits(w.occ, x, y, s, u.id)) continue;
      bd = d; bx = x; by = y;
    }
    if (bx < 0) break;
    px = u.x; py = u.y;
    stamp(w.occ, u, 0);
    u.x = bx; u.y = by;
    stamp(w.occ, u, u.id + 1);
  }
  if (u.x !== sx || u.y !== sy) ev?.push({ k: "move", u: u.id, x: u.x, y: u.y });
}

function ableToAct(w, u, ev) {
  if (u.ability === "shedskin" && u.status && rand(w) < 0.3) { u.status = null; ev?.push({ k: "cure", u: u.id }); }
  if (u.status === "slp") {
    u.sleep -= u.ability === "earlybird" ? 2 : 1;
    if (u.sleep > 0) { ev?.push({ k: "skip", u: u.id, why: "slp" }); return false; }
    u.status = null;
    ev?.push({ k: "cure", u: u.id });
  }
  if (u.status === "frz") {
    if (rand(w) >= 0.2 && !weatherOn(w, "sun")) { ev?.push({ k: "skip", u: u.id, why: "frz" }); return false; }
    u.status = null;
    ev?.push({ k: "cure", u: u.id });
  }
  if (u.flinch) { u.flinch = false; ev?.push({ k: "skip", u: u.id, why: "flinch" }); return false; }
  if (u.status === "par" && rand(w) < 0.25) { ev?.push({ k: "skip", u: u.id, why: "par" }); return false; }
  if (u.conf > 0) {
    u.conf--;
    if (rand(w) < 1 / 3) {
      const d = damage({ level: u.level, power: 40, atk: u.atk, def: u.def, roll: 85 + Math.floor(rand(w) * 16) });
      hurt(w, u, d, null, ev, "confusion");
      return false;
    }
  }
  return true;
}

function intimidate(w, u, t, ev) {
  if (u.roared || u.ability !== "intimidate" || gap(u, t) > 8) return;
  u.roared = true;
  ev?.push({ k: "roar", u: u.id });
  for (const o of around(w, u, 8)) if (o.side !== u.side) boost(o, "atk", -1, true, ev);
}

const movesIn = (u) => u.moves.map((s) => [s, D.moves[s.id]]);

function act(w, u, ev) {
  if (u.busy > 0) { u.busy--; return; }
  let t = u.plan && w.units[u.plan.t]?.alive ? w.units[u.plan.t] : nearestFoe(u);
  if (!t) return;
  if (!ableToAct(w, u, ev) || !u.alive) return;
  intimidate(w, u, t, ev);

  const live = movesIn(u).filter(([s]) => s.left > 0);
  const hits = live.filter(([, m]) => m.cat !== "status");
  const options = hits.length ? hits.map(([, m]) => m) : !live.length ? [STRUGGLE] : [];
  let goal = NEAR;
  if (options.length) {
    let best = -Infinity;
    for (const m of options) {
      // Splash moves are worth nothing from far away, so judge them by the blast it would reach by closing in.
      const distant = SPREAD.has(m.target) && gap(u, t) > burst(u);
      const v = distant ? expected(w, u, t, m) * (m.faint ? 2.5 : 1.5) - (m.faint ? u.hp * 0.45 : 0) : attackValue(w, u, t, m);
      if (v > best) { best = v; goal = reach(u, m); }
    }
  } else if (live.some(([, m]) => m.id === "teleport") || live.every(([, m]) => selfish(m))) goal = 12;
  walk(w, u, t, goal, ev);
  if (gap(u, t) > goal) {
    const near = around(w, u, 1).find((o) => o.side !== u.side);
    if (near) t = near;
  }

  let best = null, bv = 0;
  for (const [s, m] of live) {
    const inReach = gap(u, t) <= reach(u, m);
    const v = m.cat === "status" ? statusValue(w, u, t, m, inReach) : inReach ? attackValue(w, u, t, m) : 0;
    if (v > bv) { bv = v; best = [s, m]; }
  }
  if (!best) {
    if (!live.length) {
      if (gap(u, t) <= 1) strike(w, u, STRUGGLE, t, ev);
      return;
    }
    // Nothing but useless moves and the enemy is on top of it: it still has to do something, and PP burns toward Struggle.
    if (hits.length || gap(u, t) > 10) return;
    best = live.find(([, m]) => m.cat === "status") ?? null;
    if (!best) return;
  }
  const [slot, m] = best;
  slot.left = Math.max(0, slot.left - (t.ability === "pressure" && !selfish(m) ? 2 : 1));
  if (m.id === "metronome") {
    const pool = Object.values(D.moves).filter((x) => x.cat !== "status" && x.id !== "metronome");
    const picked = pool[Math.floor(rand(w) * pool.length)];
    ev?.push({ k: "metronome", u: u.id, m: picked.id });
    return strike(w, u, picked, gap(u, t) <= reach(u, picked) ? t : t, ev);
  }
  if (m.cat === "status") statusMove(w, u, m, t, ev);
  else strike(w, u, m, t, ev);
}

function endOfTurn(w, ev) {
  const kind = w.weather?.kind;
  for (const u of w.units) {
    if (!u.alive) continue;
    if (kind === "sand" && !u.types.some((t) => SANDPROOF.has(t)) && u.ability !== "sandveil" && u.ability !== "sandrush") hurt(w, u, Math.max(1, u.maxHp >> 4), null, ev, "sand");
    if (!u.alive) continue;
    if (u.status === "psn") hurt(w, u, Math.max(1, u.maxHp >> 3), null, ev, "psn");
    else if (u.status === "tox") { hurt(w, u, Math.max(1, (u.maxHp * u.tox) >> 4), null, ev, "psn"); u.tox++; }
    else if (u.status === "brn") hurt(w, u, Math.max(1, u.maxHp >> 4), null, ev, "brn");
    if (!u.alive) continue;
    if (u.seeded >= 0) {
      const src = w.units[u.seeded];
      const n = Math.max(1, u.maxHp >> 3);
      hurt(w, u, n, null, ev, "seed");
      if (src?.alive) src.hp = Math.min(src.maxHp, src.hp + n);
    }
    if (u.alive && u.ability === "speedboost") boost(u, "spe", 1, false, ev);
    u.flinch = false;
  }
  if (w.weather && --w.weather.turns <= 0) { w.weather = null; ev?.push({ k: "weather", kind: null }); }
  for (const s of w.screens) { if (s.reflect > 0) s.reflect--; if (s.lightscreen > 0) s.lightscreen--; }
}

// Advance one turn. Pass an array to collect events for the renderer; `order` on each event is its action slot.
export function step(w, ev = null) {
  if (w.over) return;
  w.turn++;
  w.quiet++;
  buildBuckets(w);
  if (w.turn === 1) {
    const setters = w.units.filter((u) => ["drizzle", "drought", "sandstream"].includes(u.ability)).sort((a, b) => b.spe - a.spe);
    for (const u of setters) setWeather(w, { drizzle: "rain", drought: "sun", sandstream: "sand" }[u.ability], 5, ev);
  }
  const queue = [];
  for (const u of w.units) {
    if (!u.alive) continue;
    const t = nearestFoe(u);
    u.plan = t ? { t: t.id } : null;
    let prio = 0;
    if (t) for (const s of u.moves) {
      const m = D.moves[s.id];
      if (s.left > 0 && m.prio > 0 && m.cat !== "status" && gap(u, t) <= steps(w, u) + reach(u, m)) prio = Math.max(prio, m.prio);
    }
    queue.push([prio, speed(w, u) + rand(w), u]);
  }
  queue.sort((a, b) => b[0] - a[0] || b[1] - a[1]);
  let order = 0;
  for (const [, , u] of queue) {
    if (!u.alive) continue;
    const before = ev?.length ?? 0;
    act(w, u, ev);
    if (ev) { for (let i = before; i < ev.length; i++) ev[i].order = order; order++; }
    if (w.units.every((x) => !x.alive || x.side === u.side)) break;
  }
  endOfTurn(w, ev);
  const alive = [0, 0];
  for (const u of w.units) if (u.alive) alive[u.side]++;
  if (!alive[0] || !alive[1] || w.turn >= MAX_TURNS || w.quiet > 80) w.over = true;
}

export function outcome(w) {
  const alive = [0, 0], hp = [0, 0], total = [0, 0];
  for (const u of w.units) {
    total[u.side] += u.maxHp;
    if (u.alive) { alive[u.side]++; hp[u.side] += Math.max(0, u.hp); }
  }
  const share = [hp[0] / (total[0] || 1), hp[1] / (total[1] || 1)];
  const winner = alive[0] && !alive[1] ? 0 : alive[1] && !alive[0] ? 1 : share[0] === share[1] ? null : share[0] > share[1] ? 0 : 1;
  return { winner, turns: w.turn, alive, share, evolutions: w.units.reduce((t, u) => t + u.evolved, 0) };
}

export function run(squads, seed) {
  const w = createWorld(squads, seed);
  while (!w.over) step(w);
  return w;
}
