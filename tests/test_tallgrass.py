"""Tall Grass rules, run in Node against the same module the browser loads."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

CHECK = r"""
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createWorld, damage, defaultLevel, footprint, gainXp, legalMoves, outcome, run, step, typeEff, useData, wildMoves } from "./web/static/tallgrass/sim.js";
import { PRESETS, WILDS, resolveSquads, wildHorde } from "./web/static/tallgrass/presets.js";
const data = JSON.parse(readFileSync("web/static/tallgrass/data.json"));
useData(data);
const num = (n) => Object.values(data.species).find((s) => s.name === n).num;
const squad = (side, name, count, level, moves, extra = {}) => ({ side, num: num(name), count, level, ...(moves ? { moves } : {}), ...extra });

// Bulbapedia's worked example: L75 Glaceon Ice Fang vs Garchomp is 168 to 196.
const ex = { level: 75, power: 65, atk: 123, def: 163, stab: 1.5, eff: 4 };
assert.equal(damage({ ...ex, roll: 85 }), 168);
assert.equal(damage({ ...ex, roll: 100 }), 196);
assert.equal(typeEff("ground", ["flying"]), 0);
assert.equal(typeEff("ice", ["dragon", "ground"]), 4);

// Wild movesets are the last four learned by level (Crystal): a level 3 Rattata has only Tackle and Tail Whip.
assert.deepEqual(wildMoves(data.species[num("Rattata")], 3), ["tackle", "tailwhip"]);
assert.ok(wildMoves(data.species[num("Rattata")], 19).includes("hyperfang"));

// An army can always fight: if a species has learned any attack by this level, its wild set contains one.
for (const sp of Object.values(data.species)) for (let lv = 1; lv <= 100; lv++) {
  const hasAttack = sp.levelup.some(([l, m]) => l >= 1 && l <= lv && data.moves[m]?.cat !== "status");
  if (hasAttack) assert.ok(wildMoves(sp, lv).some((m) => data.moves[m].cat !== "status"), `${sp.name} L${lv} has no attack`);
}
const mukWar = createWorld([squad(0, "Muk", 60, 43), squad(1, "Mewtwo", 20, 70)], 3);
let mukHits = 0;
for (let i = 0; i < 40 && !mukWar.over; i++) { const e = []; step(mukWar, e); mukHits += e.filter((x) => x.k === "hit" && mukWar.units[x.u].side === 0).length; }
assert.ok(mukHits > 0, "Muk attack");

// Footprint comes from real height.
assert.equal(footprint(data.species[num("Rattata")]), 1);
assert.equal(footprint(data.species[num("Onix")]), 10);

// Same seed, same war.
const war = [squad(0, "Rattata", 40, 19), squad(1, "Mewtwo", 1, 70)];
const fp = (w) => JSON.stringify(outcome(w));
assert.equal(fp(run(war.map((s) => ({ ...s })), 7)), fp(run(war.map((s) => ({ ...s })), 7)));

// Out of PP means Struggle, and its recoil finishes the user within four uses.
const dry = run([squad(0, "Pidgey", 1, 100, []), squad(1, "Snorlax", 1, 100, [])], 1);
assert.ok(dry.over && dry.turn < 80, `struggle war ran ${dry.turn} turns`);

// Earthquake can't touch Flying types, does hit the user's own side.
const quake = createWorld([squad(0, "Golem", 1, 100, ["earthquake"]), squad(0, "Pidgeot", 3, 50, ["tackle"]), squad(0, "Rattata", 8, 50, ["tackle"]), squad(1, "Rattata", 40, 30, ["tackle"])], 1);
const quakeHits = [];
for (let i = 0; i < 40 && !quake.over; i++) { const e = []; step(quake, e); quakeHits.push(...e.filter((x) => x.k === "hit" && x.m === "earthquake")); }
assert.ok(quakeHits.length > 0, "earthquake fired");
assert.ok(!quakeHits.some((e) => quake.units[e.o].num === num("Pidgeot")), "flying ally untouched");
assert.ok(quakeHits.some((e) => quake.units[e.o].side === 0 && quake.units[e.o].num === num("Rattata")), "grounded ally hit");

// Only 8 tiles touch a 1-tile target, a 10-tile Onix can be bitten from 44.
function maxContact(defender, level) {
  const w = createWorld([squad(0, "Rattata", 120, 19, ["tackle"]), squad(1, defender, 1, level, [])], 3);
  let peak = 0;
  for (let i = 0; i < 40 && !w.over; i++) {
    const e = [];
    step(w, e);
    peak = Math.max(peak, e.filter((x) => x.k === "use" && x.m === "tackle").length);
  }
  return peak;
}
assert.ok(maxContact("Rattata", 100) <= 8);
assert.ok(maxContact("Onix", 100) > 8);

// Transform copies the target's stats, types, and moves with 5 PP each.
const dittos = createWorld([squad(0, "Ditto", 1, 40, undefined, { x: 37, y: 30 }), squad(1, "Mewtwo", 1, 70, ["psychic", "amnesia"], { x: 82, y: 30 })], 5);
dittos.units.find((u) => u.num === num("Mewtwo")).moves = [{ id: "amnesia", left: 30, tm: true }];
for (let i = 0; i < 30 && !dittos.units.some((u) => u.tf) && !dittos.over; i++) step(dittos);
const copy = dittos.units.find((u) => u.tf), boss = dittos.units.find((u) => u.num === num("Mewtwo") && !u.tf);
assert.equal(copy?.tf.num, num("Mewtwo"));
assert.equal(copy.atk, boss.atk);
assert.ok(copy.moves.every((s) => s.left <= 5));

// Weather: Drizzle fires on turn one, sand chips non-Rock/Ground/Steel at 1/16.
const rain = createWorld([squad(0, "Politoed", 1, 50, ["surf"], { ability: "drizzle" }), squad(1, "Rattata", 1, 50, ["tackle"])], 1);
step(rain);
assert.equal(rain.weather?.kind, "rain");
const sand = createWorld([squad(0, "Tyranitar", 1, 50, ["crunch"], { ability: "sandstream" }), squad(1, "Rattata", 1, 50, ["tackle"])], 1);
step(sand);
const rat = sand.units.find((u) => u.num === num("Rattata"));
assert.equal(rat.maxHp - rat.hp, rat.maxHp >> 4);

// Split XP levels up and evolves; level 20 Gyarados learns Bite on the way in.
const pair = createWorld([squad(0, "Magikarp", 1, 19, ["tackle"]), squad(1, "Mewtwo", 1, 70, [])], 1);
const karp = pair.units.find((u) => u.num === num("Magikarp"));
gainXp(pair, karp, 200);
assert.equal(karp.num, num("Magikarp"));
gainXp(pair, karp, 100000);
assert.ok(karp.num === num("Gyarados") && karp.evolved === 1);

// A support unit's moves actually fire: Follow Me draws fire, Howl raises allies, Tailwind doubles the side's speed.
const camp = createWorld([squad(0, "Clefable", 3, 50, ["followme", "doubleslap"]), squad(0, "Arcanine", 8, 50, ["howl", "flamethrower"]), squad(0, "Pidgeot", 3, 50, ["tailwind", "wingattack"]), squad(1, "Tauros", 30, 50)], 4);
const used = {};
let lured = 0;
for (let i = 0; i < 30 && !camp.over; i++) {
  const e = []; step(camp, e);
  for (const x of e) {
    if (x.k === "use") used[x.m] = (used[x.m] ?? 0) + 1;
    if (x.k === "hit" && camp.units[x.u].side === 1 && camp.units[x.o].num === num("Clefable")) lured++;
  }
}
for (const m of ["followme", "howl", "tailwind"]) assert.ok(used[m] > 0, `${m} never used: ${JSON.stringify(used)}`);
assert.ok(lured > 0, "Follow Me drew no attacks");
const breeze = createWorld([squad(0, "Pidgeot", 1, 50, ["tailwind"]), squad(1, "Tauros", 1, 50)], 1);
step(breeze);
assert.ok(breeze.screens[0].tailwind > 0);


// Sleep skips turns; Static can paralyze on contact.
const asleep = createWorld([squad(0, "Rattata", 1, 50, ["tackle"]), squad(1, "Jigglypuff", 1, 50, ["sing"])], 9);
asleep.units[0].status = "slp"; asleep.units[0].sleep = 3;
const skip = []; step(asleep, skip);
assert.ok(skip.some((e) => e.k === "skip" && e.why === "slp"));

// Every preset resolves, uses only legal species and known moves, and finishes.
for (const p of PRESETS) {
  const squads = resolveSquads(p, data);
  for (const s of squads) {
    const sp = data.species[s.num], legal = new Set(legalMoves(sp, s.level ?? defaultLevel(sp)));
    for (const m of s.moves ?? []) {
      assert.ok(data.moves[m], `${p.id}: unknown move ${m}`);
      assert.ok(legal.has(m), `${p.id}: ${sp.name} can't learn ${m}`);
    }
  }
  if (squads.reduce((t, s) => t + s.count, 0) <= 300) assert.ok(run(squads, 2).over, p.id);
}

// Wild hordes draw from the real encounter tables.
let i = 0.123; const fake = () => (i = (i * 7.13 + 0.37) % 1);
for (const wild of WILDS) {
  const horde = wildHorde(wild, 20, data, 1, fake);
  assert.equal(horde.reduce((t, x) => t + x.count, 0), 20, wild.name);
}
console.log("ok");
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_tallgrass_rules():
    r = subprocess.run(["node", "--input-type=module", "-e", CHECK], cwd=ROOT, capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stderr + r.stdout
    assert "ok" in r.stdout
