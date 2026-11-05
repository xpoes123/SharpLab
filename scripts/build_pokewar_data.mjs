#!/usr/bin/env node
// Build web/static/pokewar/data.json: real stats/moves (Showdown), catch rate + XP (PokeAPI), type chart (PokeAPI).
// Run locally and commit the output. Species and default sets come from the vendored Poké Hoops dex.
import { writeFileSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { MOVESETS } from "../web/static/pokehoops/movesets.js";

const OUT = resolve(dirname(fileURLToPath(import.meta.url)), "../web/static/pokewar/data.json");
const SD = "https://play.pokemonshowdown.com/data";
const API = "https://pokeapi.co/api/v2";
const id = (s) => s.toLowerCase().replace(/[^a-z0-9]/g, "");
const get = async (url) => {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${r.status} ${url}`);
  return r.json();
};

const [sdMoves, sdDex] = await Promise.all([get(`${SD}/moves.json`), get(`${SD}/pokedex.json`)]);

// Damaging moves with a fixed power, plus healing. Variable-power (Flail), fixed-damage (Seismic Toss) and OHKO are out for now.
const usable = (m) => (m.category !== "Status" && m.basePower > 0 && !m.ohko) || (m.category === "Status" && m.heal && m.target === "self");
const moves = {};
for (const [key, m] of Object.entries(sdMoves)) {
  if (m.num < 1 || m.num > 251 || !usable(m)) continue;
  moves[key] = {
    name: m.name, type: m.type.toLowerCase(), cat: m.category.toLowerCase(), pow: m.basePower,
    acc: m.accuracy === true ? 0 : m.accuracy, pp: m.pp, target: m.target,
    contact: !!m.flags?.contact, recoil: m.recoil ?? null, drain: m.drain ?? null, heal: m.heal ?? null,
    hits: m.multihit ?? null, busy: !!(m.flags?.charge || m.flags?.recharge), faint: !!m.selfdestruct,
  };
}

const bySpecies = Object.values(sdDex).filter((s) => s.num >= 1 && s.num <= 251 && !s.forme);
const nameToNum = new Map(bySpecies.map((s) => [s.name, s.num]));

const TYPES = ["normal", "fire", "water", "electric", "grass", "ice", "fighting", "poison", "ground", "flying", "psychic", "bug", "rock", "ghost", "dragon", "dark", "steel", "fairy"];
const typeChart = {};
await Promise.all(TYPES.map(async (t) => {
  const { damage_relations: d } = await get(`${API}/type/${t}`);
  typeChart[t] = {};
  for (const [k, mult] of [["double_damage_to", 2], ["half_damage_to", 0.5], ["no_damage_to", 0]])
    for (const x of d[k]) typeChart[t][x.name] = mult;
}));

async function speciesRow(s) {
  const [sp, p] = await Promise.all([get(`${API}/pokemon-species/${s.num}`), get(`${API}/pokemon/${s.num}`)]);
  const evo = (s.evos ?? []).map((n) => sdDex[id(n)]).find((e) => e && e.num <= 251 && e.evoLevel && !e.evoType);
  const set = (MOVESETS[s.num] ?? []).map(id).filter((m) => moves[m]);
  const b = s.baseStats;
  // Default level: one short of its level evolution, lore level for legendaries, otherwise mid-game.
  const level = evo ? evo.evoLevel - 1 : sp.is_legendary || sp.is_mythical ? 70 : (s.evos ?? []).length ? 30 : 50;
  return {
    num: s.num, name: s.name, types: s.types.map((t) => t.toLowerCase()),
    base: [b.hp, b.atk, b.def, b.spa, b.spd, b.spe], bst: b.hp + b.atk + b.def + b.spa + b.spd + b.spe,
    level, evoTo: evo ? nameToNum.get(evo.name) : null, evoLevel: evo?.evoLevel ?? null,
    growth: sp.growth_rate.name, baseExp: p.base_experience, catchRate: sp.capture_rate,
    moves: set,
  };
}

// PokeAPI is free: keep it to a few requests at a time.
const species = {};
for (let i = 0; i < bySpecies.length; i += 8)
  for (const row of await Promise.all(bySpecies.slice(i, i + 8).map(speciesRow))) species[row.num] = row;

mkdirSync(dirname(OUT), { recursive: true });
writeFileSync(OUT, JSON.stringify({ species, moves, typeChart }));
const noAttack = Object.values(species).filter((s) => !s.moves.some((m) => moves[m].cat !== "status"));
console.log(`${Object.keys(species).length} species, ${Object.keys(moves).length} moves → ${OUT}`);
console.log(`struggle-only: ${noAttack.map((s) => s.name).join(", ") || "none"}`);
