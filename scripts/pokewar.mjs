#!/usr/bin/env node
// Monte Carlo a Pokémon war. Army syntax: "200 rattata@19" or "mewtwo[psychic,earthquake,recover]"; join stacks with "+".
//   node scripts/pokewar.mjs "200 rattata" "mewtwo" [games=200]
//   node scripts/pokewar.mjs --find rattata "mewtwo" [games=100]    smallest swarm that wins at least half
import { readFileSync } from "node:fs";
import { simulate } from "../web/static/pokewar/sim.js";

const data = JSON.parse(readFileSync(new URL("../web/static/pokewar/data.json", import.meta.url)));
const byName = new Map(Object.values(data.species).map((s) => [s.name.toLowerCase().replace(/[^a-z0-9]/g, ""), s]));

function parse(text) {
  return text.split("+").map((part) => {
    const m = part.trim().match(/^(?:(\d+)\s+)?([^@[]+)(?:@(\d+))?(?:\[(.+)\])?$/);
    if (!m) throw new Error(`can't parse "${part}"`);
    const sp = byName.get(m[2].toLowerCase().replace(/[^a-z0-9]/g, ""));
    if (!sp) throw new Error(`unknown species "${m[2]}"`);
    return { num: sp.num, count: +(m[1] ?? 1), level: m[3] ? +m[3] : undefined, moves: m[4]?.split(",").map((s) => s.trim()) };
  });
}

function run(a, b, games) {
  const t = { wins: [0, 0], draws: 0, turns: 0, surv: [0, 0], evo: 0, struggle: [0, 0] };
  for (let i = 0; i < games; i++) {
    const r = simulate(data, [a, b], i + 1);
    if (r.winner === null) t.draws++; else t.wins[r.winner]++;
    t.turns += r.turns; t.evo += r.evolutions.length;
    for (const s of [0, 1]) { t.surv[s] += r.survivors[s]; t.struggle[s] += r.struggles[s]; }
  }
  return t;
}

const describe = (army) => army.map((s) => `${s.count} ${data.species[s.num].name}`).join(" + ");
const cost = (army) => army.reduce((t, s) => t + s.count * data.species[s.num].bst, 0);

const argv = process.argv.slice(2);
if (argv[0] === "--find") {
  const defender = parse(argv[2]), games = +(argv[3] ?? 100);
  const winrate = (n) => run(parse(`${n} ${argv[1]}`), defender, games).wins[0] / games;
  let hi = 1;
  while (winrate(hi) < 0.5) { hi *= 2; if (hi > 1600) { console.log(`no swarm up to 1600 ${argv[1]} wins`); process.exit(0); } }
  let lo = Math.floor(hi / 2);
  while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (winrate(mid) >= 0.5) hi = mid; else lo = mid; }
  const swarm = parse(`${hi} ${argv[1]}`);
  console.log(`break-even: ${describe(swarm)} vs ${describe(defender)} (BST ${cost(swarm)} vs ${cost(defender)})`);
} else {
  const a = parse(argv[0]), b = parse(argv[1]), games = +(argv[2] ?? 200);
  const t = run(a, b, games), avg = (x) => (x / games).toFixed(1);
  console.log(`${describe(a)}  vs  ${describe(b)}  (${games} games; BST ${cost(a)} vs ${cost(b)})`);
  console.log(`  win%: ${(100 * t.wins[0] / games).toFixed(1)} / ${(100 * t.wins[1] / games).toFixed(1)}  draws: ${t.draws}`);
  console.log(`  avg turns: ${avg(t.turns)}  survivors: ${avg(t.surv[0])} / ${avg(t.surv[1])}  evolutions: ${avg(t.evo)}  struggles: ${avg(t.struggle[0])} / ${avg(t.struggle[1])}`);
}
