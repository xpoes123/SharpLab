"""Pokémon war sim rules, run in Node against the same module the browser will load."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

CHECK = r"""
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { damage, gainXp, makeUnit, simulate, statAt, typeEff } from "./web/static/pokewar/sim.js";
const data = JSON.parse(readFileSync("web/static/pokewar/data.json"));
const num = (n) => Object.values(data.species).find((s) => s.name === n).num;

// Bulbapedia's worked example: L75 Glaceon Ice Fang vs Garchomp is 168 to 196.
const ex = { level: 75, power: 65, atk: 123, def: 163, stab: true, eff: 4 };
assert.equal(damage({ ...ex, roll: 85 }), 168);
assert.equal(damage({ ...ex, roll: 100 }), 196);
assert.equal(statAt(106, 70, true), 250);
assert.equal(typeEff(data, "ground", ["flying"]), 0);
assert.equal(typeEff(data, "ice", ["dragon", "ground"]), 4);

// Same seed, same war.
const war = [[{ num: num("Rattata"), count: 40 }], [{ num: num("Mewtwo") }]];
const pick = (r) => [r.winner, r.turns, r.survivors];
assert.deepEqual(pick(simulate(data, war, 7)), pick(simulate(data, war, 7)));

// Out of PP means Struggle, and its recoil finishes the user: 4 quarter-HP hits at most.
const empty = simulate(data, [[{ num: num("Pidgey"), level: 100, moves: [] }], [{ num: num("Snorlax"), level: 100, moves: [] }]], 1);
assert.ok(empty.struggles[0] <= 4 && empty.struggles[1] <= 4);

// Only 8 cells touch a target, so contact moves can't all land at once.
const blob = simulate(data, [[{ num: num("Rattata"), level: 19, moves: ["tackle"], count: 100 }], [{ num: num("Steelix"), level: 100, moves: [] }]], 3);
assert.ok(blob.moveUses[0] / blob.turns <= 8, `contact hits per turn ${blob.moveUses[0] / blob.turns}`);

// Earthquake can't touch Flying types but does hit the user's own side.
const friendly = simulate(data, [[{ num: num("Golem"), level: 100, moves: ["earthquake"] }, { num: num("Rattata"), count: 3 }], [{ num: num("Snorlax"), level: 100, moves: [] }]], 2);
assert.ok(friendly.units[0].slice(1).some((u) => u.hp < u.maxHp));

// A KO with enough XP evolves the attacker mid-battle.
const karp = makeUnit(data, { num: num("Magikarp"), level: 19 }, 0);
const stats = { turns: 1, evolutions: [] };
gainXp(data, karp, makeUnit(data, { num: num("Rattata"), level: 19 }, 1), stats);
assert.equal(karp.sp.name, "Magikarp");
gainXp(data, karp, makeUnit(data, { num: num("Mewtwo"), level: 70 }, 1), stats);
assert.equal(karp.sp.name, "Gyarados");
assert.equal(stats.evolutions[0].to, "Gyarados");
console.log("ok");
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_pokewar_rules():
    r = subprocess.run(["node", "--input-type=module", "-e", CHECK], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr + r.stdout
    assert "ok" in r.stdout
