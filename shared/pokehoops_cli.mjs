// Poké Hoops server-side engine bridge: shared/pokehoops.py pipes one JSON request in on stdin and reads one
// JSON reply from stdout. It runs the exact same JS the browser runs (web/static/pokehoops-draft.js on top of
// the vendored TV engine), so the server's verdict and the page's reveal can't drift.
//   {"op":"pack","seed":S,"picks":[...]}                  → {"pack":[{n,name,types,pos,ovr,archetype,move,effect,blurb,skills}...]} | {"done":true}
//   {"op":"season","seed":S,"picks":[5],"season_seed":T}  → summarize() output
// Errors: {"error": "..."} and exit code 1.
import { replay, nextPack, playSeason, summarize, bestPos, mon } from "../web/static/pokehoops-draft.js";
import { profile, skills } from "../web/static/pokehoops/ratings.js";

function card(n) {
  const m = mon(n), b = bestPos(n), p = profile(n) || {}, k = skills(n);
  const sk = Object.fromEntries(Object.entries(k).map(([key, v]) => [key, Math.round(v * 99)]));
  return { n, name: m.name, types: m.types, pos: b.pos, ovr: b.ovr, archetype: p.archetype || "", move: p.move || "",
           effect: p.effect || "", blurb: p.blurb || "", skills: sk };
}

function handle(req) {
  const seed = Number(req.seed), picks = (req.picks || []).map(Number);
  if (!Number.isInteger(seed) || seed < 0 || seed > 0xffffffff) return { error: "bad seed" };
  if (req.op === "pack") {
    const r = replay(seed, picks);
    if (r.error) return { error: r.error };
    const pack = nextPack(seed, picks);
    return pack ? { pack: pack.map(card), pick: picks.length + 1 } : { done: true };
  }
  if (req.op === "season") {
    const ss = Number(req.season_seed);
    if (!Number.isInteger(ss) || ss < 0 || ss > 0xffffffff) return { error: "bad season seed" };
    let s;
    try { s = playSeason(seed, picks, ss); } catch (e) { return { error: String(e.message || e) }; }
    const out = summarize(s.lineup, s);
    out.lineup_cards = s.lineup.map(card);
    return out;
  }
  return { error: "unknown op" };
}

let buf = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (c) => { buf += c; });
process.stdin.on("end", () => {
  let res;
  try { res = handle(JSON.parse(buf || "{}")); } catch (e) { res = { error: "bad request: " + (e.message || e) }; }
  process.stdout.write(JSON.stringify(res));
  process.exitCode = res.error ? 1 : 0;
});
