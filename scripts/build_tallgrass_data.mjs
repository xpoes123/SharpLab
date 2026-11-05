#!/usr/bin/env node
// Build web/static/tallgrass/data.json for all 251 Gen 1-2 species. Run locally and commit the output.
// Stats, abilities, heights and move rules come from Showdown; level-up and TM learnsets (Crystal), catch rate,
// XP, type chart, Pokédex text and Red/Blue/Yellow wild encounters come from PokeAPI.
import { writeFileSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const OUT = resolve(dirname(fileURLToPath(import.meta.url)), "../web/static/tallgrass/data.json");
const SD = "https://play.pokemonshowdown.com/data";
const API = "https://pokeapi.co/api/v2";
const id = (s) => s.toLowerCase().replace(/[^a-z0-9]/g, "");
const get = async (url) => {
  for (let tries = 0; ; tries++) {
    const r = await fetch(url);
    if (r.ok) return r.json();
    if (tries >= 3) throw new Error(`${r.status} ${url}`);
    await new Promise((f) => setTimeout(f, 1000 * (tries + 1)));
  }
};

const [sdMoves, sdDex, sdLearn] = await Promise.all([get(`${SD}/moves.json`), get(`${SD}/pokedex.json`), get(`${SD}/learnsets.json`)]);

// Gen 1-2 has almost no way to help an ally, so these team and debuff moves come from each species' modern learnset.
const TACTICS = new Set([
  "helpinghand", "followme", "ragepowder", "howl", "tailwind", "healpulse", "lifedew", "coaching", "healbell", "aromatherapy", "safeguard", "mist",
  "featherdance", "tickle", "faketears", "metalsound", "willowisp", "charm", "screech", "scaryface", "stunspore", "sleeppowder", "thunderwave", "toxic",
]);

// Power for moves whose base power is computed in-game. Flail and Reversal are computed from HP in sim.js.
const POWER = { flail: 1, reversal: 1, return: 102, frustration: 102, magnitude: 71, present: 40, lowkick: 60 };
const STATUS_SPECIAL = new Set(["transform", "metronome", "splash", "teleport", "haze", "focusenergy", "rest", "synthesis", "moonlight", "morningsun"]);
const SKIP = new Set(["futuresight", "snore", "dreameater", "sleeptalk"]);

function supported(key, m) {
  if (m.num < 1 || (m.num > 251 && !TACTICS.has(key)) || SKIP.has(key)) return false;
  if (TACTICS.has(key)) return true;
  if (m.category !== "Status") return m.basePower > 0 || m.damage || m.ohko || POWER[key] || key === "superfang" || key === "psywave";
  if (STATUS_SPECIAL.has(key)) return true;
  if (m.heal && m.target === "self") return true;
  if (m.status || m.weather || m.boosts) return true;
  if (m.volatileStatus === "confusion" || m.volatileStatus === "leechseed") return true;
  return m.sideCondition === "reflect" || m.sideCondition === "lightscreen";
}

const secondary = (s) => s && {
  chance: s.chance ?? 100, status: s.status ?? null, boosts: s.boosts ?? null,
  vol: s.volatileStatus ?? null, selfBoosts: s.self?.boosts ?? null,
};

const moves = {};
for (const [key, m] of Object.entries(sdMoves)) {
  if (!supported(key, m)) continue;
  moves[key] = {
    name: m.name, type: m.type.toLowerCase(), cat: m.category.toLowerCase(),
    pow: POWER[key] ?? m.basePower, acc: m.accuracy === true ? 0 : m.accuracy, pp: m.pp, target: m.target,
    prio: m.priority ?? 0, crit: m.critRatio ?? 1, contact: !!m.flags?.contact, punch: !!m.flags?.punch, powder: !!m.flags?.powder,
    recoil: m.recoil ?? null, drain: m.drain ?? null, heal: m.heal ?? null, hits: m.multihit ?? null,
    busy: !!(m.flags?.charge || m.flags?.recharge), faint: !!m.selfdestruct, damage: m.damage ?? null, ohko: !!m.ohko,
    status: m.status ?? null, boosts: m.boosts ?? null, selfBoosts: m.self?.boosts ?? null,
    vol: m.volatileStatus ?? null, weather: m.weather?.toLowerCase() ?? null, side: m.sideCondition ?? null,
    sec: secondary(m.secondary ?? m.secondaries?.[0]),
  };
}

const bySpecies = Object.values(sdDex).filter((s) => s.num >= 1 && s.num <= 251 && !s.forme);

const TYPES = ["normal", "fire", "water", "electric", "grass", "ice", "fighting", "poison", "ground", "flying", "psychic", "bug", "rock", "ghost", "dragon", "dark", "steel", "fairy"];
const typeChart = {};
await Promise.all(TYPES.map(async (t) => {
  const { damage_relations: d } = await get(`${API}/type/${t}`);
  typeChart[t] = {};
  for (const [k, mult] of [["double_damage_to", 2], ["half_damage_to", 0.5], ["no_damage_to", 0]])
    for (const x of d[k]) typeChart[t][x.name] = mult;
}));

const clean = (s) => s.replace(/­[\n\f\s]*/g, "").replace(/-[\n\f]+/g, "").replace(/\s+/g, " ").replace(/POK[Éé]MON/g, "Pokémon").trim();
const areas = {};

function learnsets(p, name) {
  const levelup = [], tm = new Set();
  for (const key of Object.keys(sdLearn[id(name)]?.learnset ?? {})) if (TACTICS.has(key) && moves[key]) tm.add(key);
  for (const { move, version_group_details: vs } of p.moves) {
    const key = id(move.name);
    if (!moves[key]) continue;
    for (const v of vs) {
      if (v.version_group.name !== "crystal") continue;
      if (v.move_learn_method.name === "level-up") levelup.push([v.level_learned_at, key]);
      else if (v.move_learn_method.name === "machine" || v.move_learn_method.name === "tutor") tm.add(key);
    }
  }
  return { levelup: levelup.sort((a, b) => a[0] - b[0]), tm: [...tm] };
}

async function speciesRow(s) {
  const [sp, p, enc] = await Promise.all([
    get(`${API}/pokemon-species/${s.num}`), get(`${API}/pokemon/${s.num}`), get(`${API}/pokemon/${s.num}/encounters`),
  ]);
  for (const e of enc) {
    let row = null;
    for (const v of e.version_details) {
      if (!["red", "blue", "yellow"].includes(v.version.name)) continue;
      for (const d of v.encounter_details) {
        row ??= { num: s.num, min: d.min_level, max: d.max_level, chance: 0, method: d.method.name };
        row.min = Math.min(row.min, d.min_level);
        row.max = Math.max(row.max, d.max_level);
        row.chance = Math.max(row.chance, v.max_chance);
      }
    }
    if (row) (areas[e.location_area.name] ??= []).push(row);
  }
  const evos = (s.evos ?? []).map((n) => sdDex[id(n)]).filter((e) => e && e.num <= 251);
  const levelEvo = evos.find((e) => e.evoLevel && !e.evoType);
  const b = s.baseStats;
  const flavor = sp.flavor_text_entries.filter((f) => f.language.name === "en");
  const dex = flavor.find((f) => f.version.name === "crystal") ?? flavor[0];
  return {
    num: s.num, name: s.name, types: s.types.map((t) => t.toLowerCase()),
    base: [b.hp, b.atk, b.def, b.spa, b.spd, b.spe], bst: b.hp + b.atk + b.def + b.spa + b.spd + b.spe,
    heightm: s.heightm, weightkg: s.weightkg, abilities: Object.fromEntries(Object.entries(s.abilities).map(([k, v]) => [k, id(v)])),
    abilityNames: s.abilities, evoTo: levelEvo?.num ?? null, evoLevel: levelEvo?.evoLevel ?? null,
    growth: sp.growth_rate.name, baseExp: p.base_experience, catchRate: sp.capture_rate,
    legendary: sp.is_legendary || sp.is_mythical, dex: dex ? clean(dex.flavor_text) : "",
    ...learnsets(p, s.name),
  };
}

const species = {};
for (let i = 0; i < bySpecies.length; i += 8)
  for (const row of await Promise.all(bySpecies.slice(i, i + 8).map(speciesRow))) species[row.num] = row;

mkdirSync(dirname(OUT), { recursive: true });
writeFileSync(OUT, JSON.stringify({ species, moves, typeChart, areas }));
console.log(`${Object.keys(species).length} species, ${Object.keys(moves).length} moves, ${Object.keys(areas).length} areas → ${OUT}`);
console.log(`no learned attack: ${Object.values(species).filter((s) => !s.levelup.some(([, m]) => moves[m].cat !== "status")).map((s) => s.name).join(", ")}`);
