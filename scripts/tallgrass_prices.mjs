#!/usr/bin/env node
// Fit web/static/tallgrass/prices.json: pure armies of every species, at the bottom and top of its level range, with its
// wild moves and its strongest legal moves, fight each other for equal money. Prices of winners rise, losers fall.
//   node scripts/tallgrass_prices.mjs [iterations=24] [opponents=6]
import { Worker, isMainThread, parentPort, workerData } from "node:worker_threads";
import { readFileSync, writeFileSync } from "node:fs";
import { availableParallelism } from "node:os";
import { fileURLToPath } from "node:url";

const root = new URL("../web/static/tallgrass/", import.meta.url);
const BUDGET = 50000, ANCHOR = { num: 19, level: 19, price: 300 }, CAP = 1200;
const { levelRange, outcome, run, useData } = await import(new URL("sim.js", root));
const { bestMoves, unitPrice, usePrices } = await import(new URL("price.js", root));
const data = JSON.parse(readFileSync(new URL("data.json", root)));
useData(data);

function army(arm, side) {
  const sp = data.species[arm.num], [lo, hi] = levelRange(sp), level = arm.end ? hi : lo;
  const moves = arm.best ? bestMoves(sp, level) : undefined;
  const count = Math.max(1, Math.min(CAP, Math.floor(BUDGET / unitPrice(arm.num, level, moves))));
  return { side, num: arm.num, level, count, ...(moves ? { moves } : {}) };
}

if (!isMainThread) {
  parentPort.on("message", ({ table, jobs }) => {
    usePrices(data, table);
    parentPort.postMessage(jobs.map(([a, b, seed]) => outcome(run([army(a, 0), army(b, 1)], seed)).winner));
  });
} else {
  const iterations = +(process.argv[2] ?? 24), opponents = +(process.argv[3] ?? 6);
  const arms = [];
  for (const sp of Object.values(data.species)) {
    const [lo, hi] = levelRange(sp);
    for (const end of lo === hi ? [0] : [0, 1]) for (const best of [false, true]) arms.push({ num: sp.num, end, best });
  }
  const table = { k: {} };
  for (const sp of Object.values(data.species)) table.k[sp.num] = [1, 1];
  const pool = Array.from({ length: Math.max(1, availableParallelism() >> 1) }, () => new Worker(fileURLToPath(import.meta.url)));
  const round = (jobs) => new Promise((done) => {
    const size = Math.ceil(jobs.length / pool.length), results = [];
    let left = 0;
    pool.forEach((w, i) => {
      const part = jobs.slice(i * size, (i + 1) * size);
      if (!part.length) return;
      left++;
      w.once("message", (r) => { results[i] = r; if (--left === 0) done(results.flat()); });
      w.postMessage({ table, jobs: part });
    });
  });
  const anchor = () => {
    usePrices(data, table);
    const scale = ANCHOR.price / unitPrice(ANCHOR.num, ANCHOR.level);
    for (const v of Object.values(table.k)) { v[0] *= scale; v[1] *= scale; }
  };
  anchor();
  for (let it = 0; it < iterations; it++) {
    const started = Date.now();
    const jobs = [];
    for (const a of arms) for (let o = 0; o < opponents; o++) {
      const flip = o % 2 === 1, b = arms[Math.floor(Math.random() * arms.length)];
      jobs.push(flip ? [b, a, it * 100003 + jobs.length] : [a, b, it * 100003 + jobs.length]);
    }
    const winners = await round(jobs);
    const score = new Map();
    jobs.forEach(([a, b], i) => {
      for (const [arm, side] of [[a, 0], [b, 1]]) {
        const key = `${arm.num}:${arm.end}`, s = score.get(key) ?? { w: 0, n: 0 };
        s.w += winners[i] === side ? 1 : winners[i] === null ? 0.5 : 0;
        s.n++;
        score.set(key, s);
      }
    });
    const rate = 0.5 * Math.pow(0.97, it);
    let worst = 0, sum = 0;
    for (const [key, s] of score) {
      const [num, end] = key.split(":").map(Number), r = s.w / s.n;
      table.k[num][end] *= Math.exp(rate * (r - 0.5));
      worst = Math.max(worst, Math.abs(r - 0.5));
      sum += Math.abs(r - 0.5);
    }
    anchor();
    const out = { k: Object.fromEntries(Object.entries(table.k).map(([n, [a, b]]) => [n, [+a.toFixed(3), +b.toFixed(3)]])) };
    writeFileSync(new URL("prices.json", root), JSON.stringify(out));
    console.log(`iter ${it + 1}/${iterations}: mean |win-50%| ${(100 * sum / score.size).toFixed(1)} pts, worst ${(100 * worst).toFixed(0)} pts, ${((Date.now() - started) / 1000).toFixed(0)}s`);
  }
  for (const w of pool) w.terminate();
}
