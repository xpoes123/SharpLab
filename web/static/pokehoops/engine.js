// Poké Hoops engine: the possession sim, full-game and 82-game-season sims, and the league. No TV dependencies:
// the TV cartridge, a web page or a Discord bot can all import this (plain ES module; rng = {float, int}).
import { POS, POOL, skills, ovr, effectiveness, effectOf, bestLineup, bestPos } from "./ratings.js";

const BASE = { three: 0.35, mid: 0.42, drive: 0.56, post: 0.5 };
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
function weighted(rng, ws) { const tot = ws.reduce((a, b) => a + b, 0); let r = rng.float() * tot; for (let i = 0; i < ws.length; i++) { r -= ws[i]; if (r <= 0) return i; } return ws.length - 1; }

/** A tiny seeded rng (mulberry32) for callers without one, and for the fixed league. */
export function seeded(seed) {
  let a = seed >>> 0;
  const float = () => { a = (a + 0x6d2b79f5) >>> 0; let t = a; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
  return { float, int: (n) => Math.floor(float() * n), shuffle: (arr) => { for (let i = arr.length - 1; i > 0; i--) { const j = Math.floor(float() * (i + 1)); [arr[i], arr[j]] = [arr[j], arr[i]]; } return arr; } };
}

/**
 * Roll one possession into timed beats (the TV animates them; a headless sim just applies them).
 * off/def = lineups (dex numbers, PG..C); team = offense's team index (0/1), kept on beats.
 * ctx = {late, hot:{"team:i": true}}: late = 4th quarter/OT (CLUTCH), hot = made their last shot (HEAT_CHECK).
 * Beats: bring · pass{who,to} · drive{who} · shot{who,kind,made,pts,eff,block,ast,fx} · reb{who,off} · to{who,by,fx}; `next` = ball after.
 * fx names the signature-move effect that decided the play (for the play-by-play), or "".
 */
export function genPoss(off, def, rng, team, holder = 0, depth = 0, ctx = { late: false, hot: {} }) {
  const K = off.map(skills), D = def.map(skills), E = off.map(effectOf), DE = def.map(effectOf), beats = [];
  const has = (arr, fx) => arr.includes(fx);
  if (depth === 0) beats.push({ k: "bring", d: 1.0, team, who: 0 });
  // turnovers: handle vs on-ball D; a floor general steadies it, a pickpocket or hypnotist on D makes it worse
  const pTO = clamp(0.06 + 0.12 * (D[holder].perD - K[holder].handle) - (has(E, "FLOOR_GENERAL") ? 0.03 : 0)
                    + (has(DE, "PICKPOCKET") ? 0.04 : 0) + (has(DE, "HYPNOTIST") ? 0.03 : 0), 0.02, 0.2);
  const passes = depth ? 0 : rng.int(3);
  let passer = -1;
  for (let p = 0; p < passes; p++) {
    const to = weighted(rng, off.map((_, i) => (i === holder ? 0 : 1)));
    if (rng.float() < pTO / Math.max(1, passes)) {
      const thief = DE.indexOf("PICKPOCKET"), sleeper = DE.indexOf("HYPNOTIST");
      const by = thief >= 0 && rng.float() < 0.6 ? thief : sleeper >= 0 && rng.float() < 0.5 ? sleeper : to;
      beats.push({ k: "to", d: 0.7, team, who: holder, by, fx: DE[by] === "PICKPOCKET" || DE[by] === "HYPNOTIST" ? DE[by] : "", next: 1 - team });
      return beats;
    }
    beats.push({ k: "pass", d: 0.45, team, who: holder, to }); passer = holder; holder = to;
  }
  const shooter = depth ? holder : weighted(rng, off.map((n, i) => 0.6 + ovr(n, POS[i]) / 100 + (ctx.hot[team + ":" + i] && E[i] === "HEAT_CHECK" ? 0.5 : 0)));
  if (shooter !== holder) { beats.push({ k: "pass", d: 0.45, team, who: holder, to: shooter }); passer = holder; }
  const k = K[shooter], fx = E[shooter];
  const kind = depth ? (k.finish > k.shoot ? "post" : "drive")
    : ["three", "mid", "drive", "post"][weighted(rng, [k.shoot * k.shoot * (shooter <= 2 ? 1 : 0.35) * (fx === "SHARPSHOOTER" ? 1.6 : 1), 0.35,
                                                       k.finish * k.handle * (fx === "SLASHER" ? 1.6 : 1), shooter >= 3 ? k.finish * 1.2 * (fx === "POSTERIZER" ? 1.4 : 1) : 0.1])];
  if (kind === "drive") beats.push({ k: "drive", d: 0.5, team, who: shooter });
  const rimIdx = D[3].intD >= D[4].intD ? 3 : 4, rimD = D[rimIdx].intD;
  const perimeter = kind === "three" || kind === "mid";
  const offSk = perimeter ? k.shoot : k.finish, defSk = perimeter ? D[shooter].perD : 0.5 * D[shooter].intD + 0.5 * rimD;
  const eff = effectiveness(off[shooter], def[shooter]);
  // the shooter's signature move, a teammate's, or a defender's can swing it
  let bonus = 0, used = "";
  const use = (v, name) => { bonus += v; if (!used || Math.abs(v) > 0.04) used = name; };
  if (fx === "SHARPSHOOTER" && kind === "three") use(0.07, fx);
  if (fx === "SLASHER" && kind === "drive") use(0.06, fx);
  if (fx === "POSTERIZER" && !perimeter) use(0.06, fx);
  if (fx === "CLUTCH" && ctx.late) use(0.07, fx);
  if (fx === "HEAT_CHECK" && ctx.hot[team + ":" + shooter]) use(0.08, fx);
  if (has(E.filter((_, i) => i !== shooter), "FLOOR_GENERAL")) bonus += 0.015;
  if (DE[shooter] === "LOCKDOWN" && perimeter) use(-0.06, "LOCKDOWN");
  if (has(DE, "INTIMIDATOR") && !perimeter) use(-0.05, "INTIMIDATOR");
  const p = clamp(BASE[kind] + 0.28 * (offSk - defSk) + (eff >= 2 ? 0.08 : eff === 0 ? -0.15 : eff < 1 ? -0.05 : 0) + bonus, 0.1, 0.85);
  const blocker = DE.indexOf("RIM_PROTECTOR") >= 0 ? DE.indexOf("RIM_PROTECTOR") : rimIdx;
  const blocked = !perimeter && rng.float() < 0.04 + 0.12 * D[blocker].intD - 0.05 * offSk + (DE[blocker] === "RIM_PROTECTOR" ? 0.06 : 0);
  const made = !blocked && rng.float() < p;
  const ast = made && passer >= 0 && rng.float() < (has(E, "FLOOR_GENERAL") ? 0.85 : 0.7) ? passer : -1;
  beats.push({ k: "shot", d: 0.95, team, who: shooter, kind, made, pts: kind === "three" ? 3 : 2, eff, block: blocked ? blocker : -1, ast,
               fx: blocked ? (DE[blocker] === "RIM_PROTECTOR" ? "RIM_PROTECTOR" : "") : used, next: 1 - team });
  if (made) return beats;
  const glass = (S, Fx) => Math.max(...S.map((s) => s.reb)) + (has(Fx, "GLASS_CLEANER") ? 0.08 : 0);
  const rebW = (S, Fx) => S.map((s, i) => s.reb * s.reb * (Fx[i] === "GLASS_CLEANER" ? 1.8 : 1));
  if (depth < 2 && rng.float() < clamp(0.22 + 0.35 * (glass(K, E) - glass(D, DE)), 0.08, 0.45)) {
    const who = weighted(rng, rebW(K, E));
    beats.push({ k: "reb", d: 0.6, team, who, off: true, fx: E[who] === "GLASS_CLEANER" ? "GLASS_CLEANER" : "" });
    return beats.concat(genPoss(off, def, rng, team, who, depth + 1, ctx));
  }
  beats.push({ k: "reb", d: 0.6, team: 1 - team, who: weighted(rng, rebW(D, DE)), off: false, fx: "", next: 1 - team });
  return beats;
}

export const emptyBox = () => { const box = {}; for (const t of [0, 1]) for (let i = 0; i < 5; i++) box[t + ":" + i] = { pts: 0, reb: 0, ast: 0, stl: 0, blk: 0 }; return box; };

/** Apply a finished beat to score + box score (+ who's hot in ctx). Returns what happened, for play-by-play. */
export function applyBeat(score, box, b, ctx = null) {
  if (ctx && b.k === "shot") { if (b.made) ctx.hot[b.team + ":" + b.who] = true; else delete ctx.hot[b.team + ":" + b.who]; }
  const bx = (team, i) => box[team + ":" + i];
  if (b.k === "to") { bx(1 - b.team, b.by).stl++; return "steal"; }
  if (b.k === "reb") { bx(b.team, b.who).reb++; return b.off ? "oreb" : "dreb"; }
  if (b.k === "shot") {
    if (b.block >= 0) { bx(1 - b.team, b.block).blk++; return "block"; }
    if (b.made) { score[b.team] += b.pts; bx(b.team, b.who).pts += b.pts; if (b.ast >= 0) bx(b.team, b.ast).ast++; return "made"; }
    return "miss";
  }
  return null;
}

/** A whole game, headless, between two lineups (PG..C). perQ = possessions per quarter (both teams; 50 ≈ an NBA game). */
export function simGame(a, b, rng, perQ = 50) {
  const rosters = [a, b], score = [0, 0], box = emptyBox(), ctx = { late: false, hot: {} };
  let team = rng.int(2);
  const play = (n) => { for (let i = 0; i < n; i++) for (const beat of genPoss(rosters[team], rosters[1 - team], rng, team, 0, 0, ctx)) { applyBeat(score, box, beat, ctx); if (beat.next !== undefined) team = beat.next; } };
  play(perQ * 3); ctx.late = true; play(perQ);
  while (score[0] === score[1]) play(6);                          // overtime
  return { score, box };
}

/** The league: 29 fixed AI teams (same for everyone, so seasons are comparable). Each is a sensible pack draft. */
export const LEAGUE = (() => {
  const r = seeded(1946), teams = [];
  for (let t = 0; t < 29; t++) {
    const pool = r.shuffle(POOL.slice()), five = [];
    for (let i = 0; i < 5; i++) {                                              // a 3-option draft, no Legendary: take one of the better two
      const opts = pool.slice(i * 3, i * 3 + 3).sort((x, y) => bestPos(y).ovr - bestPos(x).ovr);
      five.push(opts[r.int(2)]);
    }
    teams.push(bestLineup(five));
  }
  return teams;
})();

const GRADES = [[82, "82-0!!"], [72, "A+"], [64, "A"], [57, "B+"], [50, "B"], [43, "C"], [35, "D"], [0, "F"]];
export const grade = (wins) => GRADES.find(([w]) => wins >= w)[1];

/** 82 games against the league (each team about three times). Returns the results game by game plus the summary. */
export function simSeason(roster, rng) {
  const games = [];
  for (let g = 0; g < 82; g++) {
    const opp = g % LEAGUE.length, home = g % 2 === 0;
    const { score, box } = simGame(roster, LEAGUE[opp], rng);
    games.push({ opp, us: score[0], them: score[1], win: score[0] > score[1], home, top: topScorer(box) });
  }
  const wins = games.filter((x) => x.win).length;
  return { games, wins, losses: 82 - wins, grade: grade(wins) };
}
function topScorer(box) { let best = 0; for (let i = 1; i < 5; i++) if (box["0:" + i].pts > box["0:" + best].pts) best = i; return { i: best, pts: box["0:" + best].pts }; }
