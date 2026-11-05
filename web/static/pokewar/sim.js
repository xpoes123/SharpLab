// Pokémon mass-battle sim. Turn-based like the games: each turn every unit moves, then acts, in Speed order.
// Real: damage formula, STAB, type chart, crits, accuracy, PP, Struggle, recoil/drain, XP, level-up, evolution.
// Invented for the map: grid, movement, RANGE, SPREAD_RADIUS.
export const W = 60, H = 40;
export const RANGE = 8, SPREAD_RADIUS = 2, MAX_TURNS = 500;
const STRUGGLE = { name: "Struggle", type: null, cat: "physical", pow: 50, acc: 0, contact: true, target: "normal", struggle: true };
const SPREAD = new Set(["allAdjacent", "allAdjacentFoes"]);

export function rng(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export const statAt = (base, level, isHp) =>
  Math.floor(((2 * base + 31) * level) / 100) + (isHp ? level + 10 : 5);

// Main-series order, with floors between steps. `other` holds spread and crit multipliers.
export function damage({ level, power, atk, def, stab = false, eff = 1, roll = 100, other = 1 }) {
  let d = Math.floor(Math.floor(Math.floor((2 * level) / 5 + 2) * power * atk / def) / 50) + 2;
  d = Math.floor(d * other);
  d = Math.floor((d * roll) / 100);
  if (stab) d = Math.floor(d * 1.5);
  d = Math.floor(d * eff);
  return eff === 0 ? 0 : Math.max(1, d);
}

const XP_AT = {
  slow: (n) => Math.floor((5 * n ** 3) / 4),
  medium: (n) => n ** 3,
  fast: (n) => Math.floor((4 * n ** 3) / 5),
  "medium-slow": (n) => Math.floor((6 * n ** 3) / 5) - 15 * n ** 2 + 100 * n - 140,
};
const xpAt = (growth, n) => (XP_AT[growth] ?? XP_AT.medium)(n);
const dist = (a, b) => Math.max(Math.abs(a.x - b.x), Math.abs(a.y - b.y));

export function typeEff(data, moveType, defTypes) {
  if (!moveType) return 1;
  return defTypes.reduce((m, t) => m * (data.typeChart[moveType][t] ?? 1), 1);
}

export function makeUnit(data, spec, side) {
  const sp = data.species[spec.num];
  const u = { side, sp, level: spec.level ?? sp.level, alive: true, busy: 0, kills: 0, evolved: 0, x: 0, y: 0 };
  setMoves(data, u, spec.moves ?? sp.moves);
  refreshStats(u);
  u.hp = u.maxHp;
  u.xp = xpAt(sp.growth, u.level);
  return u;
}

function setMoves(data, u, ids) {
  u.moves = ids.filter((m) => data.moves[m]).map((m) => ({ ...data.moves[m], id: m, left: data.moves[m].pp }));
}

function refreshStats(u) {
  const b = u.sp.base;
  const before = u.maxHp ?? 0;
  u.maxHp = statAt(b[0], u.level, true);
  [u.atk, u.def, u.spa, u.spd, u.spe] = b.slice(1).map((s) => statAt(s, u.level, false));
  if (before) u.hp += u.maxHp - before;
  u.steps = 2 + Math.round(b[5] / 40);
}

// Blocks fan out from each side's edge in the order given: first units sit at the front.
export function deploy(units, side) {
  const dir = side === 0 ? 1 : -1, front = side === 0 ? 12 : W - 13, rows = H - 2;
  units.forEach((u, i) => {
    const col = Math.floor(i / rows), row = i % rows;
    const offset = row % 2 ? (row + 1) / 2 : -row / 2;
    u.x = front - dir * col;
    u.y = Math.floor(H / 2) + offset;
  });
}

const key = (x, y) => y * W + x;

function hitTargets(m, u, target, all) {
  if (!SPREAD.has(m.target)) return [target];
  return all.filter((o) => o.alive && o !== u && dist(u, o) <= SPREAD_RADIUS && (m.target === "allAdjacent" || o.side !== u.side));
}

const attackStat = (m, u) => (m.cat === "physical" ? u.atk : u.spa);
const defendStat = (m, o) => (m.cat === "physical" ? o.def : o.spd);

function expected(data, m, u, o, spread) {
  const d = damage({
    level: u.level, power: m.pow, atk: attackStat(m, u), def: defendStat(m, o),
    stab: !!m.type && u.sp.types.includes(m.type), eff: typeEff(data, m.type, o.sp.types),
    roll: 92, other: spread ? 0.75 : 1,
  });
  const hits = Array.isArray(m.hits) ? 3 : m.hits ?? 1;
  return Math.min(o.hp, d * hits) * ((m.acc || 100) / 100);
}

function score(data, m, u, target, all) {
  const spread = SPREAD.has(m.target);
  let s = 0;
  for (const o of hitTargets(m, u, target, all)) s += (o.side === u.side ? -1 : 1) * expected(data, m, u, o, spread);
  if (m.faint) s -= u.hp;
  if (m.busy) s /= 2;
  return s;
}

const attacks = (u) => u.moves.filter((m) => m.left > 0 && m.cat !== "status");
const reach = (m) => (SPREAD.has(m.target) ? SPREAD_RADIUS : m.contact ? 1 : RANGE);

function nearestFoe(u, foes) {
  let best = null, bd = Infinity;
  for (const o of foes) {
    if (!o.alive) continue;
    const d = dist(u, o) * 1000 + (u.x - o.x) ** 2 + (u.y - o.y) ** 2;
    if (d < bd) { bd = d; best = o; }
  }
  return best;
}

function walk(u, target, stopAt, occupied) {
  for (let i = 0; i < u.steps && dist(u, target) > stopAt; i++) {
    let best = null, bd = (u.x - target.x) ** 2 + (u.y - target.y) ** 2;
    for (let dx = -1; dx <= 1; dx++) for (let dy = -1; dy <= 1; dy++) {
      const x = u.x + dx, y = u.y + dy;
      if (x < 0 || y < 0 || x >= W || y >= H || occupied.has(key(x, y))) continue;
      const d = (x - target.x) ** 2 + (y - target.y) ** 2;
      if (d < bd) { bd = d; best = [x, y]; }
    }
    if (!best) return;
    occupied.delete(key(u.x, u.y));
    [u.x, u.y] = best;
    occupied.add(key(u.x, u.y));
  }
}

export function gainXp(data, u, foe, stats) {
  u.xp += Math.floor((foe.sp.baseExp * foe.level) / 7);
  while (u.level < 100 && u.xp >= xpAt(u.sp.growth, u.level + 1)) u.level++;
  refreshStats(u);
  if (u.sp.evoLevel && u.level >= u.sp.evoLevel) {
    u.sp = data.species[u.sp.evoTo];
    setMoves(data, u, u.sp.moves);
    refreshStats(u);
    u.evolved++;
    stats.evolutions.push({ turn: stats.turns, side: u.side, to: u.sp.name });
  }
}

function strike(data, m, u, targets, rand, occupied, stats) {
  stats.moveUses[u.side]++;
  let dealt = 0;
  for (const o of targets) {
    if (m.acc && rand() * 100 >= m.acc) continue;
    const hits = Array.isArray(m.hits) ? [2, 2, 3, 3, 3, 3, 4, 5][Math.floor(rand() * 8)] : m.hits ?? 1;
    for (let h = 0; h < hits && o.alive; h++) {
      const d = damage({
        level: u.level, power: m.pow, atk: attackStat(m, u), def: defendStat(m, o),
        stab: !!m.type && u.sp.types.includes(m.type), eff: typeEff(data, m.type, o.sp.types),
        roll: 85 + Math.floor(rand() * 16), other: (SPREAD.has(m.target) ? 0.75 : 1) * (rand() < 1 / 24 ? 1.5 : 1),
      });
      o.hp -= d;
      dealt += d;
      if (o.hp <= 0) {
        o.alive = false;
        occupied.delete(key(o.x, o.y));
        if (o.side !== u.side && u.alive) { u.kills++; gainXp(data, u, o, stats); }
      }
    }
  }
  if (m.struggle) { u.hp -= Math.max(1, Math.floor(u.maxHp / 4)); stats.struggles[u.side]++; }
  if (m.recoil) u.hp -= Math.max(1, Math.floor((dealt * m.recoil[0]) / m.recoil[1]));
  if (m.drain) u.hp = Math.min(u.maxHp, u.hp + Math.floor((dealt * m.drain[0]) / m.drain[1]));
  if (m.faint) u.hp = 0;
  if (m.busy) u.busy = 1; // ponytail: charge moves land first, then idle; same tempo as charging first, not the same dodge window
  if (u.hp <= 0) { u.alive = false; occupied.delete(key(u.x, u.y)); }
}

function act(data, u, foes, all, rand, occupied, stats) {
  if (u.busy) { u.busy--; return; }
  const target = nearestFoe(u, foes);
  if (!target) return;

  const heal = u.hp < u.maxHp / 2 && u.moves.find((m) => m.heal && m.left > 0);
  if (heal) {
    heal.left--;
    u.hp = Math.min(u.maxHp, u.hp + Math.floor((u.maxHp * heal.heal[0]) / heal.heal[1]));
    return;
  }

  const options = attacks(u);
  if (!options.length) options.push(STRUGGLE);
  const plan = options.reduce((a, b) => (score(data, b, u, target, all) > score(data, a, u, target, all) ? b : a));
  walk(u, target, reach(plan), occupied);

  const inReach = options.filter((m) => dist(u, target) <= reach(m));
  if (!inReach.length) return;
  const m = inReach.reduce((a, b) => (score(data, b, u, target, all) > score(data, a, u, target, all) ? b : a));
  const targets = hitTargets(m, u, target, all);
  if (m.left !== undefined) m.left--;
  strike(data, m, u, targets, rand, occupied, stats);
}

// army: [{ num, level?, moves?, count }]. ponytail: nearest-foe scan is O(n²) per turn, fine to a few thousand units; add a bucket grid past that.
export function simulate(data, armies, seed = 1) {
  const rand = rng(seed);
  const sides = armies.map((army, side) => {
    const units = army.flatMap((spec) => Array.from({ length: spec.count ?? 1 }, () => makeUnit(data, spec, side)));
    deploy(units, side);
    return units;
  });
  const all = sides.flat();
  const occupied = new Set(all.map((u) => key(u.x, u.y)));
  const stats = { turns: 0, evolutions: [], struggles: [0, 0], moveUses: [0, 0] };

  while (stats.turns < MAX_TURNS && sides.every((s) => s.some((u) => u.alive))) {
    stats.turns++;
    const order = all.filter((u) => u.alive).map((u) => [u.spe + rand(), u]).sort((a, b) => b[0] - a[0]);
    for (const [, u] of order) if (u.alive) act(data, u, sides[1 - u.side], all, rand, occupied, stats);
  }

  const left = sides.map((s) => s.filter((u) => u.alive));
  const hp = sides.map((s) => s.reduce((t, u) => t + Math.max(u.hp, 0) * u.alive, 0) / s.reduce((t, u) => t + u.maxHp, 0));
  const winner = !left[1].length && left[0].length ? 0 : !left[0].length && left[1].length ? 1 : hp[0] === hp[1] ? null : hp[0] > hp[1] ? 0 : 1;
  return { winner, ...stats, survivors: left.map((s) => s.length), hpLeft: hp, units: sides };
}
