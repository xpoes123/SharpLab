// Poké Hoops (SharpLab) — the seeded solo draft + season, shared by the browser page and the server-side
// verifier (shared/pokehoops_cli.mjs runs this exact file under node). The sim itself is the TV cartridge's
// engine, vendored unmodified in ./pokehoops/ (scripts/sync-pokehoops.sh).
//
// Draft: 8 picks, each offering 3 options; pick 1 is 3 of the 11 Legendaries, picks 2-8 are the TV's "tiered"
// packs (tierPack: three options from one random overall band, preferring distinct best positions) drawn from
// the rest of the dex minus what you already took. Every pack is derived from the draft seed alone (one mulberry32 stream,
// one shuffle per pick), so the server can replay seed + picks and check each pick was really offered.
// Season: 82 games vs the fixed LEAGUE with a SEPARATE season seed the server only reveals after the picks are
// locked in — so nobody can try every pick path offline and submit the luckiest.
// Lineup: after the draft the coach (the player) sets the team — five starters at PG..C plus a 3-man bench, each
// reserve backing up one spot: {five: [5 dex], bench: [3 dex], backs: [3 spot indexes]}. Nothing is auto-arranged.
import { simSeason, seeded, LEAGUE, BENCH, members, validTeam } from "./pokehoops/engine.js";
import { POS, POOL, LEGENDS, bestLineup, bestPos, mon, ovr } from "./pokehoops/ratings.js";

export const PICKS = 5 + BENCH;
export const PACK = 3;

// Copied verbatim from the TV cartridge's game.js (not vendored: game.js is TV-bound); tests/test_pokehoops.py
// checks this function's text against game.js at the vendored revision, so the offers can't drift.
// three options from one tier (a random overall band), in different positions where possible: no dead packs, no
// runaway best option, and position fit becomes part of the choice
function tierPack(pool, rng) {
  const target = 56 + rng.float() * 30;
  for (let band = 6; band <= 40; band += 6) {
    const near = rng.shuffle(pool.filter((n) => Math.abs(bestPos(n).ovr - target) <= band));
    const pack = [];
    for (const n of near) if (pack.length < 3 && !pack.some((m) => bestPos(m).pos === bestPos(n).pos)) pack.push(n);
    for (const n of near) if (pack.length < 3 && !pack.includes(n)) pack.push(n);
    if (pack.length === 3) return pack;
  }
  return rng.shuffle(pool.slice()).slice(0, 3);
}

/** Replay a draft. Returns {packs, picks, error}: packs[i] is what pick i offered (packs.length = picks + 1
 *  while drafting, PICKS when done). error is set (and replay stops) at the first pick that wasn't offered. */
export function replay(seed, picks = []) {
  const rng = seeded(seed >>> 0), packs = [];
  let pool = POOL.slice();
  if (!Array.isArray(picks) || picks.length > PICKS) return { packs, picks: [], error: "bad picks" };
  for (let i = 0; i < PICKS; i++) {
    const pack = i === 0 ? rng.shuffle(LEGENDS.slice()).slice(0, PACK) : tierPack(pool, rng);
    packs.push(pack);
    if (i >= picks.length) break;
    const n = picks[i];
    if (!pack.includes(n)) return { packs, picks: picks.slice(0, i), error: `pick ${i + 1} (${n}) was not offered` };
    pool = pool.filter((x) => x !== n);
  }
  return { packs, picks: picks.slice(), error: null };
}

/** The pack on offer for the next pick, or null when the draft is complete / invalid. */
export function nextPack(seed, picks) {
  const r = replay(seed, picks);
  return r.error || picks.length >= PICKS ? null : r.packs[picks.length];
}

/** The coach's starting arrangement before they touch anything: draft order, bench backs PG, SG, SF. */
export function defaultTeam(picks) {
  return { five: picks.slice(0, 5), bench: picks.slice(5, PICKS), backs: [0, 1, 2].slice(0, BENCH) };
}

/** Normalize an untrusted team object (from JSON) to {five, bench, backs} of integers, or null if malformed. */
export function cleanTeam(t) {
  if (!t || typeof t !== "object") return null;
  const ints = (a) => Array.isArray(a) && a.every((x) => Number.isInteger(x)) ? a.slice() : null;
  const five = ints(t.five), bench = ints(t.bench), backs = ints(t.backs);
  return five && bench && backs ? { five, bench, backs } : null;
}

/** Season for a finished, legal draft + a legal coach's team: simSeason under the season seed. */
export function playSeason(seed, picks, team, seasonSeed) {
  const r = replay(seed, picks);
  if (r.error) throw new Error(r.error);
  if (picks.length !== PICKS) throw new Error("draft incomplete");
  const t = cleanTeam(team);
  if (!t || !validTeam(t, picks)) throw new Error("illegal lineup: use each drafted player once, five starters + three bench backing up PG..C");
  const season = simSeason(t, seeded(seasonSeed >>> 0));
  return { team: t, lineup: t.five, ...season };
}

/** League team display name: named after its best starter (LEAGUE entries are teams: {five, bench, backs}). */
export function teamName(opp) {
  const five = LEAGUE[opp].five;
  let best = five[0], bv = -1;
  five.forEach((n, i) => { const v = ovr(n, POS[i]); if (v > bv) { bv = v; best = n; } });
  return `Team ${mon(best).name}`;
}

/** The headline numbers from a season (what the server stores and the page / bot show). */
export function summarize(team, season) {
  const roster = members(team), size = roster.length;
  const g = season.games;
  let best = -1, streak = 0, run = 0;
  g.forEach((x, i) => {
    if (x.win && (best < 0 || x.us - x.them > g[best].us - g[best].them)) best = i;
    run = x.win ? run + 1 : 0; streak = Math.max(streak, run);
  });
  const led = Array(size).fill(0), high = Array(size).fill(0);
  for (const x of g) { led[x.top.i]++; high[x.top.i] = Math.max(high[x.top.i], x.top.pts); }
  const ti = led.indexOf(Math.max(...led));
  const ppg = Math.round((10 * g.reduce((a, x) => a + x.us, 0)) / g.length) / 10;
  const oppg = Math.round((10 * g.reduce((a, x) => a + x.them, 0)) / g.length) / 10;
  return {
    wins: season.wins, losses: season.losses, grade: season.grade, team, lineup: team.five,
    strip: g.map((x) => (x.win ? "W" : "L")).join(""),
    streak, ppg, oppg,
    best_game: best < 0 ? null : { g: best + 1, opp: g[best].opp, opp_name: teamName(g[best].opp), us: g[best].us, them: g[best].them },
    top_scorer: { n: roster[ti], name: mon(roster[ti]).name, pos: ti < 5 ? POS[ti] : "bench",
                  bench: ti >= 5, led: led[ti], high: high[ti] },
  };
}

export { POS, LEGENDS, BENCH, bestPos, bestLineup, mon, ovr, LEAGUE, members, validTeam };
