// What a Pokémon costs in a duel. A formula covers stats, level and moves; prices.json holds per-species
// corrections fitted by Monte Carlo (scripts/tallgrass_prices.mjs) so no pure army dominates at equal money.
import { H, defaultLevel, levelRange, legalMoves, statAt, wildMoves, zoneOf } from "./sim.js";

export const BUDGETS = [["Skirmish", 20000], ["War", 50000], ["Total War", 150000]];
export const MAX_UNITS = 1500;
const REF = 102; // a typical defensive stat: level 50, base 90
const FLOOR = 10, ELITE = 4000;
const SPREAD = new Set(["allAdjacent", "allAdjacentFoes"]);

let D = null, K = {};
export function usePrices(data, table) { D = data; K = table?.k ?? {}; }

function moveDamage(sp, level, m) {
  const atk = statAt(sp.base[m.cat === "physical" ? 1 : 3], level);
  if (m.damage) return (m.damage === "level" ? level : m.damage) * ((m.acc || 100) / 100);
  if (m.ohko) return 90;
  const pow = m.id === "superfang" ? 70 : m.id === "flail" || m.id === "reversal" ? 60 : m.id === "psywave" ? 60 : m.pow;
  const hits = !m.hits ? 1 : typeof m.hits === "number" ? m.hits : 3;
  let d = (Math.floor((2 * level) / 5 + 2) * pow * atk / REF / 50 + 2) * hits * ((m.acc || 100) / 100);
  if (m.type && sp.types.includes(m.type)) d *= 1.5;
  if (m.busy) d *= 0.55;
  if (m.faint) d *= 0.5;
  if (m.recoil) d *= 0.9;
  if (m.drain) d *= 1.15;
  return d;
}

// Formula part, before the fitted correction.
function raw(sp, level, ids) {
  let dmg = 1, mods = 1, splash = false, sleep = false, control = false, heal = false;
  for (const id of ids) {
    const m = D.moves[id];
    if (!m) continue;
    if (m.cat !== "status") {
      dmg = Math.max(dmg, moveDamage(sp, level, m));
      if (SPREAD.has(m.target) && m.pow >= 50) splash = true;
    } else if (m.heal || id === "rest" || id === "synthesis" || id === "moonlight" || id === "morningsun") heal = true;
    else if (m.status === "slp") sleep = true;
    else if (m.status || m.vol || m.boosts) control = true;
  }
  if (splash) mods *= 1.3;
  if (heal) mods *= 1.15;
  if (sleep) mods *= 1.12;
  if (control) mods *= 1.04;
  const hp = statAt(sp.base[0], level, true), bulk = (statAt(sp.base[2], level) + statAt(sp.base[4], level)) / 2 / REF;
  return 30 * Math.sqrt(dmg * hp * bulk) * (0.75 + statAt(sp.base[5], level) / 400) * mods;
}

// The fitted points for a species: its lowest level, the level people usually field it at, and its highest.
export function pricePoints(sp) {
  const [lo, hi] = levelRange(sp);
  return [lo, Math.max(lo, Math.min(hi, defaultLevel(sp))), hi];
}

function correction(num, level) {
  const k = K[num];
  if (!k) return 1;
  const [lo, mid, hi] = pricePoints(D.species[num]);
  const between = (a, b, x, y) => (y > x ? Math.exp(Math.log(a) * (1 - (level - x) / (y - x)) + Math.log(b) * ((level - x) / (y - x))) : a);
  return level <= mid ? between(k[0], k[1], lo, mid) : between(k[1], k[2], mid, hi);
}

export function unitPrice(num, level, moves) {
  const sp = D.species[num];
  let p = raw(sp, level, moves ?? wildMoves(sp, level)) * correction(num, level);
  // Very few expensive pure armies exist for the fit to learn from, and they came out strong, so the top end pays a premium.
  if (p > ELITE) p *= (p / ELITE) ** 0.3;
  return Math.max(FLOOR, Math.round(p / 10) * 10);
}

export const squadPrice = (s) => unitPrice(s.num, s.level, s.moves) * s.count;
export const armyPrice = (squads) => squads.reduce((t, s) => t + squadPrice(s), 0);

// The strongest legal set: up to three hard-hitting moves of different types, plus a heal if it has one.
export function bestMoves(sp, level) {
  const legal = legalMoves(sp, level).map((id) => D.moves[id]);
  const hits = legal.filter((m) => m.cat !== "status").map((m) => ({ m, v: moveDamage(sp, level, m) * (SPREAD.has(m.target) ? 1.3 : 1) })).sort((a, b) => b.v - a.v);
  const out = [], types = new Set();
  for (const { m } of hits) {
    if (out.length >= 3) break;
    if (types.has(m.type)) continue;
    types.add(m.type);
    out.push(m.id);
  }
  const extra = legal.find((m) => m.heal) ?? legal.find((m) => m.status === "slp") ?? hits.find(({ m }) => !out.includes(m.id))?.m;
  if (extra && !out.includes(extra.id)) out.push(extra.id);
  return out.length ? out : wildMoves(sp, level);
}

const MAX_SQUADS = 40;
const isInt = (x, lo, hi) => Number.isInteger(x) && x >= lo && x <= hi;

// The rules of a duel army. The browser uses this for live feedback; the server runs the same code as the real check.
// Returns { squads, cost } with only known fields kept, or { error }.
export function validateArmy(raw, side, budget) {
  if (!Array.isArray(raw) || raw.length === 0) return { error: "Add at least one Pokémon." };
  if (raw.length > MAX_SQUADS) return { error: `At most ${MAX_SQUADS} squads.` };
  const [x0, x1] = zoneOf(side);
  const squads = [];
  let units = 0, cost = 0;
  for (const [i, s] of raw.entries()) {
    const sp = D.species[s?.num];
    if (!sp) return { error: "Unknown Pokémon." };
    const [lo, hi] = levelRange(sp);
    if (!isInt(s.count, 1, MAX_UNITS)) return { error: `Bad count for ${sp.name}.` };
    if (!isInt(s.level, lo, hi)) return { error: `${sp.name} must be level ${lo} to ${hi}.` };
    if (!isInt(s.x, x0, x1 - 1) || !isInt(s.y, 0, H - 1)) return { error: `${sp.name} is outside your side of the field.` };
    const spacing = s.spacing ?? 1;
    if (!isInt(spacing, 1, 5)) return { error: `Bad formation for ${sp.name}.` };
    const squad = { id: side * 1000 + i + 1, side, num: sp.num, count: s.count, level: s.level, spacing, x: s.x, y: s.y };
    if (s.moves !== undefined && s.moves !== null) {
      if (!Array.isArray(s.moves) || s.moves.length > 4 || new Set(s.moves).size !== s.moves.length) return { error: `Bad moves for ${sp.name}.` };
      const legal = new Set(legalMoves(sp, s.level));
      for (const m of s.moves) if (!legal.has(m)) return { error: `${sp.name} can't know ${D.moves[m]?.name ?? "that move"} at level ${s.level}.` };
      squad.moves = s.moves;
    }
    units += s.count;
    cost += squadPrice(squad);
    squads.push(squad);
  }
  if (units > MAX_UNITS) return { error: `At most ${MAX_UNITS.toLocaleString()} units.` };
  if (cost > budget) return { error: `That army costs ₽${cost.toLocaleString()}, over your ₽${budget.toLocaleString()}.` };
  return { squads, cost };
}
