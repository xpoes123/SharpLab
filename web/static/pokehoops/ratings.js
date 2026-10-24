// Basketball ratings: scouting profiles (profiles.js) with a base-stat fallback, positions, lineups, and the type chart.
import { DEX } from "./dex.js";
import { PROFILES } from "./profiles.js";

export const POS = ["PG", "SG", "SF", "PF", "C"];
const BY_N = Object.fromEntries(DEX.map((r) => [r[0], r]));
const clamp01 = (v) => Math.max(0, Math.min(1, v));
const lnScale = (v, lo, hi) => clamp01((Math.log(Math.max(1, v)) - Math.log(lo)) / (Math.log(hi) - Math.log(lo)));

export const mon = (n) => { const r = BY_N[n]; return { n: r[0], name: r[1], types: r[2], h: r[3], w: r[4], hp: r[5], atk: r[6], def: r[7], spa: r[8], spd: r[9], spe: r[10] }; };

/** Scouting profile (ratings 1-99, positions, archetype, signature move + effect), or null if unscouted. */
export const profile = (n) => PROFILES[n] || null;
export const effectOf = (n) => PROFILES[n]?.effect || null;

/** Skills 0..1. From the scouting profile when there is one; otherwise derived from base stats + size. */
export function skills(n) {
  const pr = PROFILES[n];
  if (pr) return { shoot: pr.shoot / 100, finish: pr.finish / 100, handle: pr.handle / 100, perD: pr.perD / 100, intD: pr.intD / 100, reb: pr.reb / 100 };
  return derived(n);
}
function derived(n) {
  const m = mon(n), s = (v) => clamp01(v / 130);
  const size = lnScale(m.h, 3, 40), mass = lnScale(m.w, 20, 3000);   // decimetres / hectograms, log so Onix doesn't break it
  return {
    shoot: clamp01(0.7 * s(m.spa) + 0.3 * s(m.spd)),
    finish: clamp01(0.6 * s(m.atk) + 0.2 * size + 0.2 * mass),
    handle: clamp01(0.6 * s(m.spe) + 0.2 * s(m.spa) + 0.2 * s(m.atk)),
    perD: clamp01(0.5 * s(m.spe) + 0.5 * s(m.spd)),
    intD: clamp01(0.5 * s(m.def) + 0.3 * size + 0.2 * mass),
    reb: clamp01(0.45 * size + 0.25 * clamp01(m.hp / 150) + 0.15 * s(m.def) + 0.15 * mass),
  };
}

const WEIGHTS = {
  PG: { handle: 0.45, shoot: 0.3, perD: 0.25 },
  SG: { shoot: 0.5, handle: 0.25, perD: 0.25 },
  SF: { shoot: 0.3, finish: 0.3, perD: 0.2, reb: 0.2 },
  PF: { finish: 0.4, reb: 0.35, intD: 0.25 },
  C: { reb: 0.4, intD: 0.4, finish: 0.2 },
};
/** Overall at a position, 1–99. */
export function ovr(n, pos) {
  const k = skills(n), w = WEIGHTS[pos];
  let v = 0; for (const key in w) v += w[key] * k[key];
  return Math.round(Math.max(1, Math.min(99, 100 * v)));   // a weighted average of 1-99 ratings is already on the scale
}

// attacker type → [super effective against, not very effective against, no effect on]
const CHART = {
  normal: ["", "rock steel", "ghost"], fire: ["grass ice bug steel", "fire water rock dragon", ""],
  water: ["fire ground rock", "water grass dragon", ""], electric: ["water flying", "electric grass dragon", "ground"],
  grass: ["water ground rock", "fire grass poison flying bug dragon steel", ""], ice: ["grass ground flying dragon", "fire water ice steel", ""],
  fighting: ["normal ice rock dark steel", "poison flying psychic bug fairy", "ghost"], poison: ["grass fairy", "poison ground rock ghost", "steel"],
  ground: ["fire electric poison rock steel", "grass bug", "flying"], flying: ["grass fighting bug", "electric rock steel", ""],
  psychic: ["fighting poison", "psychic steel", "dark"], bug: ["grass psychic dark", "fire fighting poison flying ghost steel fairy", ""],
  rock: ["fire ice flying bug", "fighting ground steel", ""], ghost: ["psychic ghost", "dark", "normal"],
  dragon: ["dragon", "steel", "fairy"], dark: ["psychic ghost", "fighting dark fairy", ""],
  steel: ["ice rock fairy", "fire water electric steel", ""], fairy: ["fighting dragon dark", "fire poison steel", ""],
};
const eff1 = (a, d) => { const [se, nv, im] = CHART[a].map((x) => x.split(" ")); return im.includes(d) ? 0 : se.includes(d) ? 2 : nv.includes(d) ? 0.5 : 1; };
/** Best multiplier any of the shooter's types gets against the defender's types (0, 0.25 … 4). */
export function effectiveness(attN, defN) {
  const a = mon(attN).types, d = mon(defN).types;
  return Math.max(...a.map((t) => d.reduce((m, dt) => m * eff1(t, dt), 1)));
}

export const LEGENDS = [144, 145, 146, 150, 151];
export const POOL = DEX.map((r) => r[0]).filter((n) => !LEGENDS.includes(n));   // everyone but the Legendaries

/** Best position at which a Pokémon plays, and its overall there. */
export function bestPos(n) { let b = "PG"; for (const p of POS) if (ovr(n, p) > ovr(n, b)) b = p; return { pos: b, ovr: ovr(n, b) }; }

/** Five drafted Pokémon → the lineup (PG, SG, SF, PF, C) with the highest total overall. */
export function bestLineup(five) {
  let best = null, bestV = -1;
  const perm = (arr, out) => {
    if (!arr.length) { const v = out.reduce((a, n, i) => a + ovr(n, POS[i]), 0); if (v > bestV) { bestV = v; best = out.slice(); } return; }
    for (let i = 0; i < arr.length; i++) perm([...arr.slice(0, i), ...arr.slice(i + 1)], [...out, arr[i]]);
  };
  perm(five, []);
  return best;
}
export const findByName = (q) => { const s = q.trim().toLowerCase().replace(/[^a-z♀♂]/g, ""); return s ? DEX.find((r) => r[1].toLowerCase().replace(/[^a-z♀♂]/g, "").startsWith(s))?.[0] ?? null : null; };
