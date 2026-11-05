#!/usr/bin/env node
// Monte Carlo a war from the terminal. Army syntax: "200 rattata@19" or "mewtwo@70[psychic,earthquake,recover]"; join stacks with "+".
//   node scripts/tallgrass.mjs "200 rattata" "mewtwo" [games=100]
//   node scripts/tallgrass.mjs --find rattata "mewtwo" [games=60]     smallest swarm that wins at least half
//   node scripts/tallgrass.mjs --presets [games=40]                   win rates for every preset
import { readFileSync } from "node:fs";
import { outcome, run, useData } from "../web/static/tallgrass/sim.js";
import { PRESETS, resolveSquads } from "../web/static/tallgrass/presets.js";

const data = JSON.parse(readFileSync(new URL("../web/static/tallgrass/data.json", import.meta.url)));
useData(data);
const norm = (s) => s.toLowerCase().replace(/[^a-z0-9]/g, "");
const byName = new Map(Object.values(data.species).map((s) => [norm(s.name), s]));

function parse(text, side) {
  return text.split("+").map((part) => {
    const m = part.trim().match(/^(?:(\d+)\s+)?([^@[]+)(?:@(\d+))?(?:\[(.+)\])?$/);
    const sp = m && byName.get(norm(m[2]));
    if (!sp) throw new Error(`can't parse "${part}"`);
    return { side, num: sp.num, count: +(m[1] ?? 1), level: m[3] ? +m[3] : undefined, moves: m[4]?.split(",").map(norm) };
  });
}

function tally(squads, games) {
  const t = { wins: [0, 0], draws: 0, turns: 0, alive: [0, 0], evo: 0, ms: 0 };
  for (let i = 0; i < games; i++) {
    const started = performance.now();
    const o = outcome(run(squads.map((s) => ({ ...s })), i + 1));
    t.ms += performance.now() - started;
    if (o.winner === null) t.draws++; else t.wins[o.winner]++;
    t.turns += o.turns; t.evo += o.evolutions;
    t.alive[0] += o.alive[0]; t.alive[1] += o.alive[1];
  }
  return t;
}

const report = (label, t, games) => {
  const avg = (x) => (x / games).toFixed(1);
  console.log(`${label}\n  win%: ${(100 * t.wins[0] / games).toFixed(0)} / ${(100 * t.wins[1] / games).toFixed(0)}  draws: ${t.draws}  turns: ${avg(t.turns)}  survivors: ${avg(t.alive[0])} / ${avg(t.alive[1])}  evolutions: ${avg(t.evo)}  ${avg(t.ms)} ms/war`);
};

const argv = process.argv.slice(2);
if (argv[0] === "--presets") {
  const games = +(argv[1] ?? 40);
  for (const p of PRESETS) report(p.title, tally(resolveSquads(p, data), games), games);
} else if (argv[0] === "--find") {
  const defender = parse(argv[2], 1), games = +(argv[3] ?? 60);
  const rate = (n) => tally([...parse(`${n} ${argv[1]}`, 0), ...defender], games).wins[0] / games;
  let hi = 1;
  while (rate(hi) < 0.5) { hi *= 2; if (hi > 1600) { console.log(`no swarm up to 1600 ${argv[1]} wins`); process.exit(0); } }
  let lo = hi >> 1;
  while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (rate(mid) >= 0.5) hi = mid; else lo = mid; }
  console.log(`break-even: ${hi} ${argv[1]} vs ${argv[2]}`);
} else {
  const squads = [...parse(argv[0], 0), ...parse(argv[1], 1)], games = +(argv[2] ?? 100);
  report(`${argv[0]}  vs  ${argv[1]}`, tally(squads, games), games);
}
