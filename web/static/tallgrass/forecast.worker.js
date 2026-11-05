// Monte Carlo runs off the main thread so the field keeps animating.
import { outcome, run, useData } from "./sim.js";

const ready = fetch(new URL("./data.json", import.meta.url)).then((r) => r.json()).then(useData);
let current = 0;

onmessage = async ({ data: { id, squads, games } }) => {
  await ready;
  current = id;
  const t = { wins: [0, 0], draws: 0, turns: 0, alive: [0, 0], n: 0 };
  for (let i = 0; i < games; i++) {
    if (current !== id) return;
    const o = outcome(run(squads.map((s) => ({ ...s })), 1000 + i * 7));
    if (o.winner === null) t.draws++; else t.wins[o.winner]++;
    t.turns += o.turns; t.alive[0] += o.alive[0]; t.alive[1] += o.alive[1]; t.n++;
    if (i % 5 === 4 || i === games - 1) {
      postMessage({ id, ...t, games });
      await new Promise((f) => setTimeout(f));
    }
  }
};
