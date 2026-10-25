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
import { simSeason, simGame, seeded, LEAGUE, BENCH, members, validTeam, newGame, quarterDone, gameDone, nextQuarter, rollPoss, playBeat } from "./pokehoops/engine.js";
import { POS, POOL, LEGENDS, bestLineup, bestPos, mon, ovr } from "./pokehoops/ratings.js";

export const PICKS = 5 + BENCH;
export const PACK = 3;

// Copied verbatim from the TV cartridge's game.js (not vendored: game.js is TV-bound); tests/test_pokehoops.py
// checks this function's text against game.js at the vendored revision, so the offers can't drift.
// three options from one tier (a random overall band), in different positions where possible: no dead packs, no
// runaway best option, and position fit becomes part of the choice
function tierPack(pool, rng, k = 3) {
  const target = 56 + rng.float() * 30;
  for (let band = 6; band <= 40; band += 6) {
    const near = rng.shuffle(pool.filter((n) => Math.abs(bestPos(n).ovr - target) <= band));
    const pack = [];
    for (const n of near) if (pack.length < k && !pack.some((m) => bestPos(m).pos === bestPos(n).pos)) pack.push(n);
    for (const n of near) if (pack.length < k && !pack.includes(n)) pack.push(n);
    if (pack.length === k) return pack;
  }
  return rng.shuffle(pool.slice()).slice(0, k);
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

// A finished, legal draft + a legal coach's team, or throw.
function checked(seed, picks, team) {
  const r = replay(seed, picks);
  if (r.error) throw new Error(r.error);
  if (picks.length !== PICKS) throw new Error("draft incomplete");
  const t = cleanTeam(team);
  if (!t || !validTeam(t, picks)) throw new Error("illegal lineup: use each drafted player once, five starters + three bench backing up PG..C");
  return t;
}

/** Season for a finished, legal draft + a legal coach's team: simSeason under the season seed. */
export function playSeason(seed, picks, team, seasonSeed) {
  const t = checked(seed, picks, team);
  const season = simSeason(t, seeded(seasonSeed >>> 0));
  return { team: t, lineup: t.five, ...season };
}

export const SERIES_WINS = 4;   // head-to-head: best of seven

/** A head-to-head best-of-seven: your verified draft + team vs another coach's recorded team, under a series seed.
 *  games[i] = {us, them, win, top: [{i, pts} ours, {i, pts} theirs]} (top.i indexes members(team)). */
export function playSeries(seed, picks, team, opp, seriesSeed, log = false) {
  const t = checked(seed, picks, team), o = cleanTeam(opp);
  if (!o || !validTeam(o, members(o))) throw new Error("bad opponent team");
  return { team: t, opp: o, ...bestOf7(t, o, seriesSeed, log) };
}

/** Best of seven between two legal teams under a series seed. log=true adds each game's box, score flow and big plays
 *  (same rng stream either way, so a logged replay matches the server's unlogged sim). */
export function bestOf7(t, o, seriesSeed, log = false) {
  const rng = seeded(seriesSeed >>> 0), games = [];
  let wins = 0, losses = 0;
  while (wins < SERIES_WINS && losses < SERIES_WINS) {
    const g = log ? logGame(t, o, rng) : simGame(t, o, rng), { score, box } = g;
    const win = score[0] > score[1];
    games.push({ us: score[0], them: score[1], win, top: [0, 1].map((s) => topOf(box, s)),
                 ...(log ? { box, flow: g.flow, plays: g.plays, periods: g.periods } : {}) });
    if (win) wins++; else losses++;
  }
  return { games, wins, losses };
}

// simGame's exact loop (same rng draws), watching each possession: the score after it, and the plays worth showing.
// plays: [q, team, dex, what, score0, score1]. what: dunk | three | block | steal | and1 | clutch
function logGame(a, b, rng) {
  const teams = [a, b], m = [members(a), members(b)], G = newGame(rng), flow = [[0, 0, 1]], plays = [];
  const dex = (team, spot) => m[team][G.on[team][spot]];
  for (;;) {
    if (quarterDone(G)) { if (gameDone(G)) break; nextQuarter(G); }
    for (const bt of rollPoss(G, teams, rng)) {
      const what = playBeat(G, bt), q = G.q + G.ot, sc = [G.score[0], G.score[1]];
      const clutch = q >= 4 && Math.abs(sc[0] - sc[1]) <= 5 && G.i / (G.ot ? 20 : G.perQ) > 0.75;
      if (what === "made") {
        const k = clutch ? "clutch" : bt.dunk ? "dunk" : bt.foul >= 0 ? "and1" : bt.kind === "three" ? "three" : null;
        if (k) plays.push([q, bt.team, dex(bt.team, bt.who), k, ...sc]);
      } else if (what === "block") plays.push([q, 1 - bt.team, dex(1 - bt.team, bt.block), "block", ...sc]);
      else if (what === "steal") plays.push([q, 1 - bt.team, dex(1 - bt.team, bt.by), "steal", ...sc]);
    }
    flow.push([G.score[0], G.score[1], G.q + G.ot]);
  }
  return { score: G.score, box: G.box, flow, plays, periods: G.q + G.ot };
}

/** Game k of a recorded season, logged (box, score flow, big plays) like bestOf7's: replays simSeason's rng stream
 *  up to it, so it's the very game the season had. Pure read: the season itself is still simSeason's. */
export function seasonGame(team, seasonSeed, k) {
  const rng = seeded(seasonSeed >>> 0);
  for (let g = 0; g < k; g++) simGame(team, LEAGUE[g % LEAGUE.length], rng);
  const opp = k % LEAGUE.length, g = logGame(team, LEAGUE[opp], rng), { score, box } = g;
  return { opp, us: score[0], them: score[1], win: score[0] > score[1], top: [0, 1].map((s) => topOf(box, s)),
           box, flow: g.flow, plays: g.plays, periods: g.periods };
}

const binom = (n, k) => { let r = 1; for (let i = 1; i <= k; i++) r = (r * (n - k + i)) / i; return r; };
function hashOf(x) { let h = 2166136261; for (const c of JSON.stringify(x)) h = Math.imul(h ^ c.charCodeAt(0), 16777619); return h >>> 0; }
const median = (a) => { const s = a.slice().sort((x, y) => x - y); return s[s.length >> 1]; };

/** The pregame line: Monte Carlo n games of exactly this matchup (fixed seed per matchup, so it's reproducible).
 *  Games in a series are independent draws, so the series price is binomial in the single-game p.
 *  p: game win prob (team a) · series: P(a wins the series) · exact: {"4-0": p, ... "0-4": p} from a's side ·
 *  spread: median margin (a − b) · total: median points · mvp: top single-game scorers [{side, dex, p}] */
export function matchLine(a, b, n = 2000) {
  const rng = seeded(hashOf([a, b])), margins = [], totals = [], tops = {}, ma = members(a), mb = members(b);
  let w = 0;
  for (let i = 0; i < n; i++) {
    const { score, box } = simGame(a, b, rng);
    if (score[0] > score[1]) w++;
    margins.push(score[0] - score[1]); totals.push(score[0] + score[1]);
    const ta = topOf(box, 0), tb = topOf(box, 1), k = ta.pts >= tb.pts ? `0:${ma[ta.i]}` : `1:${mb[tb.i]}`;
    tops[k] = (tops[k] || 0) + 1;
  }
  const p = w / n, q = 1 - p, exact = {};
  for (let k = 0; k < SERIES_WINS; k++) {
    exact[`${SERIES_WINS}-${k}`] = binom(SERIES_WINS - 1 + k, k) * p ** SERIES_WINS * q ** k;
    exact[`${k}-${SERIES_WINS}`] = binom(SERIES_WINS - 1 + k, k) * q ** SERIES_WINS * p ** k;
  }
  const series = Object.entries(exact).filter(([r]) => r.startsWith(`${SERIES_WINS}-`)).reduce((s, [, v]) => s + v, 0);
  const mvp = Object.entries(tops).sort((x, y) => y[1] - x[1]).slice(0, 4)
    .map(([k, c]) => ({ side: Number(k[0]), dex: Number(k.slice(2)), p: c / n }));
  return { n, p, series, exact, spread: median(margins), total: median(totals), mvp,
           se: Math.sqrt((p * q) / n) };
}

// ── Duel: two coaches draft at the same time from separate packs (the TV's "together" mode), lockstep by round.
// Round i offers each side its own three: pick 1 splits six of the Legendaries, picks 2-8 are tierPacks where the
// second side's pack excludes the first's, so nobody is ever offered the same Pokémon. Both picks leave the pool.
/** Replay a duel draft: picks = [[side 0...], [side 1...]] (equal length = completed rounds).
 *  Returns {rounds, error}: rounds[i] = [pack0, pack1]; one more round than picks while drafting. */
export function duelReplay(seed, picks) {
  const rng = seeded((seed ^ 0x9e3779b9) >>> 0), rounds = [];
  if (!Array.isArray(picks) || picks.length !== 2 || !picks.every(Array.isArray)) return { rounds, error: "bad picks" };
  const n = picks[0].length;
  if (picks[1].length !== n || n > PICKS) return { rounds, error: "bad picks" };
  let pool = POOL.slice();
  for (let i = 0; i < PICKS; i++) {
    let a, b;
    if (i === 0) { const L = rng.shuffle(LEGENDS.slice()); a = L.slice(0, PACK); b = L.slice(PACK, 2 * PACK); }
    else { a = tierPack(pool, rng); b = tierPack(pool.filter((x) => !a.includes(x)), rng); }
    rounds.push([a, b]);
    if (i >= n) break;
    for (const s of [0, 1]) if (!rounds[i][s].includes(picks[s][i])) return { rounds, error: `pick ${i + 1} (${picks[s][i]}) was not offered` };
    pool = pool.filter((x) => x !== picks[0][i] && x !== picks[1][i]);
  }
  return { rounds, error: null };
}

/** Both teams legal for a finished duel draft, or throw. */
export function duelTeams(seed, picks, teams) {
  const r = duelReplay(seed, picks);
  if (r.error) throw new Error(r.error);
  if (picks[0].length !== PICKS) throw new Error("draft incomplete");
  return [0, 1].map((s) => {
    const t = cleanTeam(teams?.[s]);
    if (!t || !validTeam(t, picks[s])) throw new Error("illegal lineup");
    return t;
  });
}
function topOf(box, side) { let best = 0; for (let i = 1; i < 5 + BENCH; i++) if (box[`${side}:${i}`].pts > box[`${side}:${best}`].pts) best = i; return { i: best, pts: box[`${side}:${best}`].pts }; }

/** League team indexes, weakest starting five first (bot difficulty for a single game; never shown as a number). */
export const LEAGUE_BY_STRENGTH = LEAGUE.map((t, i) => [i, t.five.reduce((a, n, k) => a + ovr(n, POS[k]), 0)])
  .sort((a, b) => a[1] - b[1]).map(([i]) => i);

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
