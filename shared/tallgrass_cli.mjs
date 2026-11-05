#!/usr/bin/env node
// Server-side bridge to the Tall Grass engine, so duels are checked by the same code the browser runs.
// One JSON request on stdin, one JSON reply on stdout.
//   { op: "validate", squads, side, budget } -> { squads, cost } | { error }
//   { op: "war", squads, seed }              -> { winner, turns, alive }
import { readFileSync } from "node:fs";
import { outcome, run, useData } from "../web/static/tallgrass/sim.js";
import { usePrices, validateArmy } from "../web/static/tallgrass/price.js";

const file = (name) => JSON.parse(readFileSync(new URL(`../web/static/tallgrass/${name}`, import.meta.url)));
const data = file("data.json");
useData(data);
usePrices(data, file("prices.json"));

const req = JSON.parse(readFileSync(0, "utf8"));
let res;
if (req.op === "validate") res = validateArmy(req.squads, req.side, req.budget);
else if (req.op === "war") {
  const { winner, turns, alive } = outcome(run(req.squads.map((s) => ({ ...s })), req.seed));
  res = { winner, turns, alive };
} else res = { error: "unknown op" };
process.stdout.write(JSON.stringify(res));
