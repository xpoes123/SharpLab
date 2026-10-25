// Poké Hoops engine: the possession sim, full-game and 82-game-season sims, and the league. No TV dependencies:
// the TV cartridge, a web page or a Discord bot can all import this (plain ES module; rng = {float, int}).
// Game flow beyond single possessions lives here too (so every front end gets it): fast breaks off steals and long
// rebounds, a shot clock (late-clock shots are worse), momentum runs and the trailing coach's timeout, team fouls and
// the bonus, and the trailing team fouling on purpose at the end.
import { POS, POOL, skills, ovr, effectiveness, effectOf, profile, bestLineup, bestPos } from "./ratings.js";

const BASE = { three: 0.33, mid: 0.4, drive: 0.58, post: 0.45 };   // tuned against NBA shot-type FG% (sim audit)
const FT_PCT = 0.77;
export const FLOW = {
  slope: 0.17,                        // make% per point of skill edge; lower = closer games
  threeW: 2.6,                        // how keen shooters are to take threes (≈ a third of shots, like the NBA)
  breakSteal: 0.62, breakLong: 0.45,  // how often a steal / a long defensive rebound turns into a fast break
  breakTrailer: 0.2,                  // …of which a trailer spots up for a three instead of the layup
  reachFoul: 0.07,                    // a non-shooting foul in a half-court set (side out, or two shots in the bonus)
  bonusAt: 5,                         // the defense's 5th team foul of a quarter puts the offense in the bonus
  lateClock: 19.5, latePenalty: 0.08, // seconds used before a "late clock" forced shot, and what it costs
  run: 8, runHeat: 6, heat: 0.03,     // a run of 8 (unanswered-ish) draws the timeout; 6+ gives the running team a lift
  ato: 0.03,                          // after-timeout play bonus
  intentional: 0.85,                  // trailing late by 1-8: how often the defense fouls on purpose
  call: 3,                         // how strong a coach's call is (scales every call's modifiers; see CALLS)
};
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
function weighted(rng, ws) { const tot = ws.reduce((a, b) => a + b, 0); let r = rng.float() * tot; for (let i = 0; i < ws.length; i++) { r -= ws[i]; if (r <= 0) return i; } return ws.length - 1; }

/** A tiny seeded rng (mulberry32) for callers without one, and for the fixed league. */
export function seeded(seed) {
  let a = seed >>> 0;
  const float = () => { a = (a + 0x6d2b79f5) >>> 0; let t = a; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
  return { float, int: (n) => Math.floor(float() * n), shuffle: (arr) => { for (let i = arr.length - 1; i > 0; i--) { const j = Math.floor(float() * (i + 1)); [arr[i], arr[j]] = [arr[j], arr[i]]; } return arr; } };
}

/** Game context that carries between possessions (plain JSON). */
export const newCtx = (timeouts = 4) => ({ late: false, hot: {}, tf: [0, 0], run: { team: -1, pts: 0, opp: 0 }, tos: [timeouts, timeouts], trans: null, ato: -1, foulGame: false });
const full = (ctx) => Object.assign(ctx, { ...newCtx(), ...ctx });

/**
 * Roll one possession into beats (the TV animates them; a headless sim just applies them).
 * off/def = lineups (dex numbers, PG..C); team = offense's team index (0/1), kept on beats.
 * ctx (newCtx) is read, never written here: applyBeat does that. late = 4th quarter/OT (CLUTCH), hot = made their last
 * shot (HEAT_CHECK), trans = a fast-break chance from the last possession, foulGame = the defense fouls on purpose.
 * Beats: bring · break{who} · pass{who,to} · drive{who} · foul{who,by,shooting?} · shot{who,kind,made,pts,eff,block,ast,fx,late,fb,foul}
 *        · ft{who,n,hits,made} · reb{who,off,long} · to{who,by,steal,fx} (and timeout{team}, added by rollPoss).
 * Every beat has d (nominal seconds) and sc (shot clock used when it ends). `next` (on the possession's last beat only)
 * = who has the ball after; `first` marks a possession's opening beat; `trans` on the last beat = a break chance for the other side.
 */
export function genPoss(off, def, rng, team, holder = 0, depth = 0, ctx = newCtx()) {
  full(ctx);
  const K = off.map(skills), D = def.map(skills), E = off.map(effectOf), DE = def.map(effectOf), beats = [];
  // a coach's call in force (coach calls, below): mo = the offense's, md = the defense's. Both null in plain sims.
  const mo = ctx.mods?.[team] && ctx.mods[team].side !== "def" ? ctx.mods[team] : null, md = ctx.mods?.[1 - team] && ctx.mods[1 - team].side !== "off" ? ctx.mods[1 - team] : null;
  const mine = (i) => !!mo && i === mo.x, callW = (i) => (mine(i) ? CALLS[mo.fx].focus || 1 : 1) * (md && i === md.tg ? (md.fx === "HYPNOTIST" ? 0.05 : 0.7) : 1), callTag = (m) => ({ call: [m === mo ? team : 1 - team, m.move] });
  const has = (arr, fx) => arr.includes(fx);
  const run = ctx.run, dteam = 1 - team;
  let fouls = ctx.tf[dteam], sc = 0, bonus0 = (ctx.ato === team ? FLOW.ato : 0) + (run.team === team && run.pts - run.opp >= FLOW.runHeat ? FLOW.heat : 0);
  const push = (b) => { sc += b.d * (b.k === "bring" ? 4.5 : b.k === "pass" ? 4.2 : b.k === "drive" ? 2.5 : b.k === "break" ? 3.5 : 1); b.sc = Math.min(24, Math.round(sc)); beats.push(b); return b; };
  // free throws (the last one missed: a rebound, the defense's nine times in ten)
  const fts = (who, n, extra = {}) => {
    const hits = []; for (let i = 0; i < n; i++) hits.push(rng.float() < FT_PCT);
    const made = hits.filter(Boolean).length, last = hits[n - 1];
    const b = push({ k: "ft", d: 0.5 + 0.3 * n, team, who, n, hits, made, ...extra });
    if (last) { b.next = dteam; return beats; }
    const offReb = rng.float() < 0.12;
    if (offReb) { push({ k: "reb", d: 0.6, team, who: weighted(rng, K.map((s) => s.reb * s.reb)), off: true, fx: "" }); return beats.concat(genPoss(off, def, rng, team, beats[beats.length - 1].who, depth + 1, ctx)); }
    push({ k: "reb", d: 0.6, team: dteam, who: weighted(rng, D.map((s) => s.reb * s.reb)), off: false, fx: "", next: dteam });
    return beats;
  };
  const pTO = clamp(0.14 + 0.12 * (D[holder].perD - K[holder].handle) - (has(E, "FLOOR_GENERAL") ? 0.02 : 0)
                    + (has(DE, "PICKPOCKET") ? 0.04 : 0) + (has(DE, "HYPNOTIST") ? 0.03 : 0)
                    - (mo?.fx === "FLOOR_GENERAL" ? 0.05 * mo.m : 0) + (md?.fx === "PICKPOCKET" ? 0.07 * md.m : 0)
                    + (md?.fx === "HYPNOTIST" ? (0.06 + (holder === md.tg ? 0.05 : 0)) * md.m : 0), 0.05, md?.fx === "PICKPOCKET" || md?.fx === "HYPNOTIST" ? 0.36 : 0.25);   // ≈ 14 TO per 100, like the NBA
  const steal = (who, to) => {
    const thief = DE.indexOf("PICKPOCKET"), sleeper = DE.indexOf("HYPNOTIST");
    const caller = (md?.fx === "PICKPOCKET" || md?.fx === "HYPNOTIST") && rng.float() < 0.75 ? md.x : -1;   // the press / the sleeper gets it
    const by = caller >= 0 ? caller : thief >= 0 && rng.float() < 0.6 ? thief : sleeper >= 0 && rng.float() < 0.5 ? sleeper : to;
    const special = DE[by] === "PICKPOCKET" || DE[by] === "HYPNOTIST", isSteal = special || rng.float() < 0.5;   // half of turnovers are steals
    push({ k: "to", d: 0.7, team, who, by, steal: isSteal, fx: special ? DE[by] : "", next: dteam, ...(isSteal ? { trans: { kind: "steal", who: by } } : {}), ...(caller >= 0 ? callTag(md) : {}) });
    return beats;
  };

  let fb = false, kindForce = "", passer = -1, shooter = -1;
  if (depth === 0) {
    // trailing late: foul the ball-handler on purpose, two shots
    if (ctx.foulGame) {
      push({ k: "bring", d: 0.8, team, who: holder, first: true });
      push({ k: "foul", d: 0.6, team, who: holder, by: holder, intentional: true });
      return fts(holder, 2, { bonus: true });
    }
    // a run-out off a steal or a long rebound: quick, at the rim (or a trailer three), hard to stop
    // a full-court press that gets broken is a run-out the other way
    const tr = ctx.trans || (md?.fx === "PICKPOCKET" && rng.float() < 0.15 ? { kind: "press", who: rng.int(3) } : null);
    if (tr && (tr.kind === "press" || rng.float() < (tr.kind === "steal" ? FLOW.breakSteal : FLOW.breakLong))) {
      fb = true; holder = tr.who;
      push({ k: "break", d: 1.3, team, who: holder, first: true, ...(tr.kind === "press" ? { press: true } : {}) });
      if (rng.float() < 0.04) return steal(holder, holder);
      if (rng.float() < FLOW.breakTrailer) { const to = [0, 1, 2].filter((i) => i !== holder)[rng.int(2)]; push({ k: "pass", d: 0.45, team, who: holder, to }); passer = holder; shooter = to; kindForce = "three"; }
      else { shooter = holder; kindForce = "drive"; }
    } else push({ k: "bring", d: 1.0, team, who: 0, first: true });
  }
  if (!fb) {
    const passes = depth ? 0 : 1 + rng.int(2) + (rng.float() < 0.25 ? 1 : 0);
    for (let p = 0; p < passes; p++) {
      // a reach-in: side out, or two shots once the defense is in the penalty
      if (depth === 0 && rng.float() < FLOW.reachFoul / passes) {
        const by = rng.int(5);
        push({ k: "foul", d: 0.6, team, who: holder, by });
        if (++fouls >= FLOW.bonusAt) return fts(holder, 2, { bonus: true });
      }
      const to = weighted(rng, off.map((_, i) => (i === holder ? 0 : 1)));
      if (rng.float() < pTO / Math.max(1, passes)) return steal(holder, to);
      push({ k: "pass", d: 0.45, team, who: holder, to }); passer = holder; holder = to;
    }
    shooter = depth ? holder : weighted(rng, off.map((n, i) => (0.6 + ovr(n, POS[i]) / 100 + (ctx.hot[team + ":" + i] && E[i] === "HEAT_CHECK" ? 0.5 : 0)) * callW(i)));
    if (shooter !== holder) { push({ k: "pass", d: 0.45, team, who: holder, to: shooter }); passer = holder; }
  }
  const k = K[shooter], fx = E[shooter], cf = mine(shooter) ? mo.fx : "", paint = md?.fx === "RIM_PROTECTOR" ? 0.6 : md?.fx === "INTIMIDATOR" ? 0.7 : 1;
  const kind = kindForce || (depth ? (k.finish > k.shoot ? "post" : "drive")
    : ["three", "mid", "drive", "post"][weighted(rng, [FLOW.threeW * k.shoot * k.shoot * (shooter <= 2 ? 1 : 0.5) * (fx === "SHARPSHOOTER" ? 1.6 : 1) * (cf === "SHARPSHOOTER" || cf === "HEAT_CHECK" ? 2 : 1), 0.22,
                                                       k.finish * k.handle * (fx === "SLASHER" ? 1.6 : 1) * (cf === "SLASHER" ? 2.2 : cf === "POSTERIZER" ? 1.3 : 1) * paint,
                                                       (shooter >= 3 || cf === "POSTERIZER" ? k.finish * (cf === "POSTERIZER" ? 0.8 : 0.4) * (fx === "POSTERIZER" ? 1.4 : 1) : 0.1) * paint])]);
  if (kind === "drive" && !fb) push({ k: "drive", d: 0.5, team, who: shooter });
  // the shot clock: a long set eats it, and a late-clock shot is a forced one (or no shot at all)
  const used = depth ? 0 : sc + rng.float() * 8 + (rng.float() < 0.08 ? 5 : 0);
  if (!fb && depth === 0 && used > 24) { push({ k: "to", d: 0.7, team, who: holder, by: holder, steal: false, violation: true, fx: "", next: dteam }); return beats; }   // shot-clock violation
  const late = !fb && used > FLOW.lateClock;
  const rimIdx = D[3].intD >= D[4].intD ? 3 : 4, rimD = D[rimIdx].intD;
  const perimeter = kind === "three" || kind === "mid";
  const offSk = perimeter ? k.shoot : k.finish, defSk = perimeter ? D[shooter].perD : 0.5 * D[shooter].intD + 0.5 * rimD;
  const eff = effectiveness(off[shooter], def[shooter]);
  // the shooter's signature move, a teammate's, or a defender's can swing it
  let bonus = bonus0, used2 = "";
  const use = (v, name) => { bonus += v; if (!used2 || Math.abs(v) > 0.04) used2 = name; };
  if (fx === "SHARPSHOOTER" && kind === "three") use(0.07, fx);
  if (fx === "SLASHER" && kind === "drive") use(0.06, fx);
  if (fx === "POSTERIZER" && !perimeter) use(0.06, fx);
  if (fx === "CLUTCH" && ctx.late) use(0.12, fx);
  if (fx === "HEAT_CHECK" && ctx.hot[team + ":" + shooter]) use(0.08, fx);
  if (has(E.filter((_, i) => i !== shooter), "FLOOR_GENERAL")) bonus += 0.008;
  if (!fb && DE[shooter] === "LOCKDOWN" && perimeter) use(-0.06, "LOCKDOWN");
  if (!fb && has(DE, "INTIMIDATOR") && !perimeter) use(-0.03, "INTIMIDATOR");
  // the coach's call: the play was run for this guy, or the defense was set up for it
  let tagged = null;
  const callBonus = (m, v) => { bonus += v * m.m; tagged = m; };
  if (cf === "SHARPSHOOTER" && kind === "three") callBonus(mo, 0.06);
  if (cf === "SLASHER" && kind === "drive") callBonus(mo, 0.07);
  if (cf === "POSTERIZER" && !perimeter) callBonus(mo, 0.07);
  if (cf === "CLUTCH") callBonus(mo, 0.09);
  if (cf === "HEAT_CHECK") callBonus(mo, rng.float() < 0.5 ? 0.14 : -0.07);   // green light: feast or famine
  if (mo?.fx === "FLOOR_GENERAL") bonus += 0.03 * mo.m;               // credited below, when the set ends in an assist
  if (md?.fx === "LOCKDOWN" && shooter === md.tg) callBonus(md, -0.16);
  if (md?.fx === "INTIMIDATOR" && kind !== "three") callBonus(md, kind === "mid" ? -0.02 : -0.07);
  if (late) bonus -= FLOW.latePenalty;
  if (fb) bonus += kind === "three" ? 0.05 : 0.14;                       // nobody back: an open look
  const p = clamp(BASE[kind] + FLOW.slope * (offSk - defSk) + (eff >= 2 ? 0.08 : eff === 0 ? -0.15 : eff < 1 ? -0.05 : 0) + bonus, 0.1, fb ? 0.92 : 0.85);
  const wall = md?.fx === "RIM_PROTECTOR" && !perimeter;
  const blocker = wall ? md.x : [3, 4].find((i) => DE[i] === "RIM_PROTECTOR") ?? rimIdx;   // only a big protects the rim (unless the call says so)
  const blocked = !perimeter && rng.float() < (fb ? 0.3 : 1) * (0.04 + 0.12 * D[blocker].intD - 0.05 * offSk + (DE[blocker] === "RIM_PROTECTOR" ? 0.09 : 0) + (wall ? 0.1 * md.m : 0));
  if (wall) tagged = md;
  const made = !blocked && rng.float() < p;
  const ast = made && passer >= 0 && rng.float() < (mo?.fx === "FLOOR_GENERAL" ? 0.92 : has(E, "FLOOR_GENERAL") ? 0.85 : 0.7) ? passer : -1;
  if (mo?.fx === "FLOOR_GENERAL" && ast >= 0) tagged = mo;
  // shooting fouls: mostly on drives and post-ups; a make gets the and-one, a miss goes to the line
  const fouled = !blocked && rng.float() < (perimeter ? 0.035 : fb ? 0.16 : 0.24) + ((cf === "SLASHER" || cf === "POSTERIZER") && !perimeter ? 0.08 : 0);
  const dunk = !perimeter && made && (fx === "POSTERIZER" || k.finish >= 0.8 || (fb && k.finish >= 0.6));
  const shot = push({ k: "shot", d: 0.95, team, who: shooter, kind, made, pts: kind === "three" ? 3 : 2, eff, block: blocked ? blocker : -1, ast, dunk, late, fb,
                      foul: fouled ? (perimeter ? shooter : blocker === shooter || rng.float() < 0.5 ? shooter : rimIdx) : -1,
                      fx: blocked ? (DE[blocker] === "RIM_PROTECTOR" ? "RIM_PROTECTOR" : "") : used2,
                      ...(tagged && (tagged === mo ? made : !made) ? callTag(tagged) : {}) });   // credit the call when it went its way
  if (fouled) return fts(shooter, made ? 1 : kind === "three" ? 3 : 2, { andOne: made });
  if (made) { shot.next = dteam; return beats; }
  const glass = (S, Fx) => Math.max(...S.map((s) => s.reb)) + (has(Fx, "GLASS_CLEANER") ? 0.08 : 0);
  const crash = (m) => (m?.fx === "GLASS_CLEANER" ? m : null), co = crash(mo), cd = crash(md);
  const rebW = (S, Fx, m) => S.map((s, i) => s.reb * s.reb * (Fx[i] === "GLASS_CLEANER" ? 1.8 : 1) * (m && i === m.x ? 3 : 1));
  const long = rng.float() < (kind === "three" ? 0.4 : kind === "mid" ? 0.2 : 0.05);   // long rebounds start breaks
  if (depth < 2 && rng.float() < clamp(0.26 + 0.2 * (glass(K, E) - glass(D, DE)) - (long ? 0.06 : 0) + (co ? 0.13 * co.m : 0) - (cd ? 0.1 * cd.m : 0), 0.08, co ? 0.6 : 0.45)) {
    const who = weighted(rng, rebW(K, E, co));
    push({ k: "reb", d: 0.6, team, who, off: true, long, fx: E[who] === "GLASS_CLEANER" ? "GLASS_CLEANER" : "", ...(co && who === co.x ? callTag(co) : {}) });
    return beats.concat(genPoss(off, def, rng, team, who, depth + 1, ctx));
  }
  const who = weighted(rng, rebW(D, DE, cd));
  push({ k: "reb", d: 0.6, team: dteam, who, off: false, long, fx: "", next: dteam, ...(long || blocked ? { trans: { kind: "long", who } } : {}) });
  return beats;
}
// ---- teams: five starters (PG..C, placed by the coach) + a three-man bench, each backing up one spot ------------
export const BENCH = 3;
/** A team is {five: [PG,SG,SF,PF,C], bench: [3 dex], backs: [slot each bench player backs up]}. A bare 5-array = no bench. */
export const asTeam = (t) => (Array.isArray(t) ? { five: t, bench: [], backs: [] } : t);
export const members = (t) => [...asTeam(t).five, ...asTeam(t).bench];   // roster index 0-4 starters, 5-7 bench
/** The coach's arrangement is legal: the same eight it drafted, each once, backs are spots 0-4. */
export function validTeam(t, picks) {
  const m = members(t);
  return t.five.length === 5 && t.bench.length === picks.length - 5 && t.backs.length === t.bench.length && t.backs.every((s) => Number.isInteger(s) && s >= 0 && s < 5)
    && m.length === picks.length && new Set(m).size === m.length && m.every((n) => picks.includes(n));
}
/** Who's on the floor (roster indexes, by spot) at fraction f of quarter q (5+ = OT). Starters open and close every
 *  quarter; the bench plays the middle of Q1-Q3 and early Q4 (~15 min a night). Two backups at one spot split it. */
export function onCourt(t, q, f) {
  t = asTeam(t);
  const [a, b] = q <= 3 ? [0.4, 0.8] : q === 4 ? [0.15, 0.4] : [0, 0], on = [0, 1, 2, 3, 4];
  if (f < a || f >= b) return on;
  for (let s = 0; s < 5; s++) {
    const subs = t.backs.map((x, j) => (x === s ? j : -1)).filter((j) => j >= 0);
    if (subs.length) on[s] = 5 + subs[Math.min(subs.length - 1, Math.floor(((f - a) / (b - a)) * subs.length))];
  }
  return on;
}
/** Starters + bench for a computer coach: best five in their best spots, each reserve backing up its natural spot. */
export function autoTeam(picks) {
  const byOvr = picks.slice().sort((x, y) => bestPos(y).ovr - bestPos(x).ovr), five = bestLineup(byOvr.slice(0, 5)), bench = byOvr.slice(5);
  return { five, bench, backs: bench.map((n) => POS.indexOf(bestPos(n).pos)) };
}


export const emptyBox = (size = 5 + BENCH) => {
  const box = {};
  for (const t of [0, 1]) for (let i = 0; i < size; i++) box[t + ":" + i] = { pts: 0, reb: 0, ast: 0, stl: 0, blk: 0, fgm: 0, fga: 0, tpm: 0, tpa: 0, ftm: 0, fta: 0, pf: 0 };
  return box;
};

/** Apply a finished beat to score + box score + ctx (hot hands, runs, team fouls, fast-break chances). Returns what
 *  happened, for play-by-play. Beats name spots 0-4; on = [team0, team1] spot → roster index maps (from onCourt). */
export function applyBeat(score, box, b, ctx = null, on = [[0, 1, 2, 3, 4], [0, 1, 2, 3, 4]]) {
  if (ctx) full(ctx);
  const bx = (team, i) => box[team + ":" + on[team][i]];
  const scored = (team, pts) => { if (!ctx || !pts) return; const r = ctx.run;
    if (r.team === team) r.pts += pts; else if (r.opp + pts > 3 || r.team < 0) ctx.run = { team, pts, opp: 0 }; else r.opp += pts; };
  if (ctx && b.first) { ctx.trans = null; ctx.ato = -1; }
  if (ctx && b.trans) ctx.trans = { ...b.trans };
  if (b.k === "timeout") return "timeout";
  if (b.k === "to") { if (b.steal) bx(1 - b.team, b.by).stl++; return b.steal ? "steal" : "turnover"; }
  if (b.k === "foul") { bx(1 - b.team, b.by).pf++; if (ctx) ctx.tf[1 - b.team]++; return "foul"; }
  if (b.k === "ft") { const s = bx(b.team, b.who); score[b.team] += b.made; s.pts += b.made; s.ftm += b.made; s.fta += b.n; scored(b.team, b.made); return "ft"; }
  if (b.k === "reb") { bx(b.team, b.who).reb++; return b.off ? "oreb" : "dreb"; }
  if (b.k === "shot") {
    const s = bx(b.team, b.who), three = b.kind === "three";
    if (ctx) { if (b.made) ctx.hot[b.team + ":" + b.who] = true; else delete ctx.hot[b.team + ":" + b.who]; }
    s.fga++; if (three) s.tpa++;
    if (b.foul >= 0) { bx(1 - b.team, b.foul).pf++; if (ctx) ctx.tf[1 - b.team]++; }
    if (b.block >= 0) { bx(1 - b.team, b.block).blk++; return "block"; }
    if (b.made) { score[b.team] += b.pts; s.pts += b.pts; s.fgm++; if (three) s.tpm++; if (b.ast >= 0) bx(b.team, b.ast).ast++; scored(b.team, b.pts); return "made"; }
    return "miss";
  }
  return null;
}

/** The five on the floor (dex numbers by spot) for a team at fraction f of quarter q, plus the roster-index map. */
export function floor(t, q, f) { const on = onCourt(t, q, f), m = members(t); return { on, five: on.map((i) => m[i]) }; }

// ---- a game, possession by possession: shared by simGame (instant) and the TV (animated, or highlights) ----------
export const OT_POSS = 20;   // overtime: ~10 possessions a side
/** A new game between two teams. perQ = possessions per quarter (both teams; 50 ≈ an NBA game). Plain JSON. */
export function newGame(rng, perQ = 50) {
  return { q: 1, ot: 0, i: 0, perQ, score: [0, 0], box: emptyBox(), team: rng.int(2), ctx: newCtx(Math.max(2, Math.round(perQ / 8))), calls: newCalls(),
           on: [[0, 1, 2, 3, 4], [0, 1, 2, 3, 4]], stats: { poss: 0, to: 0, fga: 0, tpa: 0, fgm: 0, fta: 0, pf: 0, fb: 0, timeouts: 0, late: 0, intentional: 0 } };
}
const possIn = (G) => (G.ot ? OT_POSS : G.perQ);
export const quarterDone = (G) => G.i >= possIn(G);
export const gameDone = (G) => quarterDone(G) && (G.q >= 4 || G.ot > 0) && G.score[0] !== G.score[1];
export function nextQuarter(G) { if (G.q >= 4) { G.ot++; if (G.calls) G.calls.left = G.calls.left.map((n) => n + CALL_OT); } else G.q++; G.i = 0; G.ctx.tf = [0, 0]; }
/** What anyone could know before the next possession is played (never its outcome): the clock, the score from the
 *  offense's side, whether the trailing coach will call time, whether the defense will foul on purpose. */
export function preplay(G) {
  const frac = G.i / possIn(G), lastQ = G.q >= 4 || G.ot > 0, diff = G.score[G.team] - G.score[1 - G.team], ctx = full(G.ctx), r = ctx.run;
  const timeout = r.team >= 0 && r.team !== G.team && r.pts - r.opp >= FLOW.run && ctx.tos[G.team] > 0 ? G.team : -1;
  return { q: G.q, ot: G.ot, frac, secLeft: Math.round((G.ot ? 300 : 720) * (1 - frac)), offense: G.team, diff, lastQ,
           close: Math.abs(diff) <= 6, clutch: lastQ && frac >= 0.7 && Math.abs(diff) <= 6, timeout,
           foulGame: lastQ && frac >= 0.9 && diff >= 1 && diff <= 8, trans: !!ctx.trans, run: r.team >= 0 ? { team: r.team, pts: r.pts, opp: r.opp } : null,
           call: callPending(G) };
}
/** Roll the next possession (after preplay): subs, a timeout if one's due, then the play. Doesn't apply it. */
export function rollPoss(G, teams, rng, pre = preplay(G)) {
  const fl = teams.map((t) => floor(asTeam(t), G.ot ? 5 : G.q, G.i / possIn(G))), ctx = G.ctx, beats = [], C = G.calls, off = G.team;
  G.on = fl.map((x) => x.on);
  ctx.late = pre.lastQ;
  ctx.foulGame = pre.foulGame && rng.float() < FLOW.intentional;
  const timeout = (team, manual) => {           // stop the run: it cools the other side's hot hands, and draws up a play
    const r = ctx.run;
    beats.push({ k: "timeout", d: 1.6, team, run: [r.pts, r.opp], sc: 0, ...(manual ? { manual: true } : {}) });
    ctx.tos[team]--; for (const k of Object.keys(ctx.hot)) if (k.startsWith(1 - team + ":")) delete ctx.hot[k];
    ctx.run = { team: -1, pts: 0, opp: 0 }; ctx.ato = team;
  };
  if (C) for (const t of [0, 1]) if (C.timeout[t]) { C.timeout[t] = false; if (ctx.tos[t] > 0) timeout(t, true); }   // a coach's own timeout
  if (pre.timeout >= 0 && !beats.length) timeout(pre.timeout, false);
  if (C) callsIn(G, teams, fl, beats);
  G.i++;
  const out = beats.concat(genPoss(fl[off].five, fl[1 - off].five, rng, off, 0, 0, ctx));
  if (C) callsOut(G, off);
  return out;
}

// ---- coach calls: each drafted Pokémon's signature move is one play its coach can call, once a game -------------------
// A call is queued from the phone and starts with the next possession (never mid-play). It only works with the Pokémon
// ON THE FLOOR: calling a reserve's (or a resting starter's) move checks him in at his spot for the call. It lasts `n`
// trips: the caller's own possessions (off), the opponent's (def), or both ends (both). The effects are modifiers that
// genPoss reads from ctx.mods; with no calls (simGame, simSeason, the 82-0) nothing changes. `m` scales a call by the
// type matchup (the move's Pokémon vs whoever it's aimed at): super effective 1.3×, not very effective 0.8×, no effect 0.5×.
export const CALL_LIMIT = 3, CALL_OT = 1;
export const CALLS = {
  SHARPSHOOTER: { side: "off", n: 4, focus: 3, does: "run plays for {X}: more threes, and they fall more" },
  SLASHER: { side: "off", n: 4, focus: 2.5, does: "{X} attacks the rim: more drives, more fouls drawn" },
  POSTERIZER: { side: "off", n: 4, focus: 2.5, does: "feed {X} inside: post-ups and dunks, more fouls drawn" },
  HEAT_CHECK: { side: "off", n: 4, focus: 4, does: "green light for {X}: lots of shots, boom or bust" },
  CLUTCH: { side: "off", n: 3, focus: 3, late: true, does: "the ball goes to {X} in crunch time: a big boost (Q4/OT only)" },
  FLOOR_GENERAL: { side: "off", n: 4, does: "{X} settles the offense: fewer turnovers, more assists, ends their run" },
  GLASS_CLEANER: { side: "both", n: 6, does: "{X} crashes the boards at both ends" },
  LOCKDOWN: { side: "def", n: 4, does: "{X} locks onto their hottest scorer{T}" },
  RIM_PROTECTOR: { side: "def", n: 4, does: "{X} walls off the paint: fewer drives, more blocks" },
  INTIMIDATOR: { side: "def", n: 4, does: "{X} stares down the paint: their shots inside get worse" },
  PICKPOCKET: { side: "def", n: 4, does: "full-court press led by {X}: steals up, but beat it and it's a fast break" },
  HYPNOTIST: { side: "def", n: 3, does: "{X} puts their ball-handler{T} to sleep: turnovers up" },
};
export const callTrips = (fx) => { const c = CALLS[fx]; return c.side === "off" ? `your next ${c.n} trips` : c.side === "def" ? `their next ${c.n} trips` : `the next ${c.n} possessions`; };
export const newCalls = () => ({ left: [CALL_LIMIT, CALL_LIMIT], used: [[], []], queue: [-1, -1], active: [null, null], timeout: [false, false], made: 0 });
export const callPending = (G) => !!G.calls && (G.calls.queue.some((x) => x >= 0) || G.calls.timeout.some(Boolean));
const typeMult = (eff) => (eff >= 2 ? 1.3 : eff === 0 ? 0.5 : eff < 1 ? 0.8 : 1);
const homeSpot = (t, ri) => (ri < 5 ? ri : asTeam(t).backs[ri - 5]);
// who a call is aimed at, from the floor right now: [their roster index, their spot]
function callTarget(G, teams, team, ri) {
  const fx = effectOf(members(teams[team])[ri]), opp = 1 - team, theirs = G.on[opp];
  if (fx === "HYPNOTIST") return [theirs[0], 0];                                       // the ball-handler (their PG)
  if (fx === "LOCKDOWN") { let s = 0; for (let i = 1; i < 5; i++) if (G.box[opp + ":" + theirs[i]].pts > G.box[opp + ":" + theirs[s]].pts) s = i; return [theirs[s], s]; }
  const x = G.on[team].includes(ri) ? G.on[team].indexOf(ri) : homeSpot(teams[team], ri);   // otherwise: the man across from him
  return [theirs[x], x];
}
/** Can this coach call roster index ri's move now? {ok, why ("" | used | queued | limit | busy | late | none), fx, move, eff, target, bench}. */
export function callStatus(G, teams, team, ri) {
  const C = G.calls, n = members(teams[team])[ri], fx = effectOf(n);
  if (!C || n === undefined || !CALLS[fx]) return { ok: false, why: "none" };
  const [target] = callTarget(G, teams, team, ri), eff = effectiveness(n, members(teams[1 - team])[target]);
  const why = C.used[team].includes(ri) ? "used" : C.queue[team] === ri ? "queued" : C.left[team] <= 0 ? "limit" : C.queue[team] >= 0 || C.active[team] ? "busy"
    : CALLS[fx].late && !(G.q >= 4 || G.ot) ? "late" : "";
  return { ok: !why, why, fx, move: profile(n).move, eff, target, bench: !G.on[team].includes(ri) };
}
/** Queue a call for the next possession (true if accepted). */
export function queueCall(G, teams, team, ri) {
  if (!callStatus(G, teams, team, ri).ok) return false;
  G.calls.queue[team] = ri; return true;
}
/** Queue a coach's own timeout for the next dead ball (true if accepted). */
export function queueTimeout(G, team) {
  if (!G.calls || G.calls.timeout[team] || G.ctx.tos[team] <= 0) return false;
  G.calls.timeout[team] = true; return true;
}
// start queued calls (a "call" beat), check callers in, and hand genPoss this possession's modifiers
function callsIn(G, teams, fl, beats) {
  const C = G.calls, ctx = G.ctx;
  for (const t of [0, 1]) {
    const ri = C.queue[t];
    if (ri < 0) continue;
    C.queue[t] = -1;
    if (C.used[t].includes(ri) || C.left[t] <= 0 || C.active[t]) continue;
    const n = members(teams[t])[ri], fx = effectOf(n), [target, tspot] = callTarget(G, teams, t, ri), eff = effectiveness(n, members(teams[1 - t])[target]), c = CALLS[fx];
    C.used[t].push(ri); C.left[t]--; C.made++;
    C.active[t] = { ri, fx, move: profile(n).move, side: c.side, n: c.n, left: c.n, target, tspot, eff, m: typeMult(eff) };
    const b = { k: "call", d: 2, sc: 0, team: t, ri, fx, move: profile(n).move, target, eff, n: c.n, side: c.side };
    if (fx === "FLOOR_GENERAL" && ctx.run.team === 1 - t) { b.run = [ctx.run.pts, ctx.run.opp]; ctx.run = { team: -1, pts: 0, opp: 0 }; }   // settles it down
    beats.push(b);
  }
  ctx.mods = null;
  for (const t of [0, 1]) {
    const a = C.active[t];
    if (!a) continue;
    const on = G.on[t];
    if (!on.includes(a.ri)) {                    // check the caller in at his spot for as long as the call lasts
      const s = homeSpot(teams[t], a.ri), b = beats.find((x) => x.k === "call" && x.team === t);
      if (b) b.subFor = on[s];
      on[s] = a.ri; fl[t].five[s] = members(teams[t])[a.ri];
    }
    const tg = (a.fx === "LOCKDOWN" || a.fx === "HYPNOTIST") && G.on[1 - t].includes(a.target) ? G.on[1 - t].indexOf(a.target) : -1;
    (ctx.mods ||= [null, null])[t] = { fx: a.fx, move: a.move, side: a.side, x: on.indexOf(a.ri), tg, m: a.m * FLOW.call };
  }
}
/** A computer coach's call: a roster index to call now, or -1. Saves them for the moment: Floor General when the other
 *  side is on a run, Lockdown on a hot scorer, Clutch late and close, the press when behind late; otherwise one call
 *  around mid-Q2, mid-Q3 and early Q4 for whoever has the best matchup, preferring Pokémon already on the floor. */
export function botCall(G, teams, team) {
  const C = G.calls;
  if (!C || C.left[team] <= 0 || C.queue[team] >= 0 || C.active[team]) return -1;
  const frac = G.i / possIn(G), lastQ = G.q >= 4 || G.ot > 0, diff = G.score[team] - G.score[1 - team], r = G.ctx.run, opp = 1 - team;
  const theyRun = r.team === opp ? r.pts - r.opp : 0, n = C.used[team].length;
  const hot = Math.max(...G.on[opp].map((i) => G.box[opp + ":" + i].pts));
  let best = -1, bestV = 0;
  for (let ri = 0; ri < members(teams[team]).length; ri++) {
    const s = callStatus(G, teams, team, ri);
    if (!s.ok) continue;
    let v = 1;
    if (s.fx === "FLOOR_GENERAL" && theyRun >= 6) v = 3;
    if (s.fx === "LOCKDOWN" && hot >= G.perQ * 0.45) v = 2.6;
    if (s.fx === "CLUTCH" && lastQ && frac >= 0.55 && Math.abs(diff) <= 8) v = 4;
    if (s.fx === "PICKPOCKET" && lastQ && frac >= 0.5 && diff <= -5) v = 3;
    if ((s.fx === "HYPNOTIST" || s.fx === "RIM_PROTECTOR" || s.fx === "INTIMIDATOR") && diff < 0) v = 1.5;
    if (s.fx === "FLOOR_GENERAL" && theyRun < 6) v = 0.6;   // save it for a run
    v *= typeMult(s.eff) * (s.bench ? 0.8 : 1);
    if (v > bestV) { bestV = v; best = ri; }
  }
  if (best < 0) return -1;
  const cap = lastQ ? CALL_LIMIT + G.ot : G.q === 3 ? 2 : 1, due = (G.q === 2 && frac >= 0.5 && n < 1) || (G.q === 3 && frac >= 0.5 && n < 2) || (lastQ && frac >= 0.25) ;
  return n < cap && (bestV >= 2.5 || due) ? best : -1;
}
// count down the calls that applied to the possession just rolled (off = who had the ball)
function callsOut(G, off) {
  const C = G.calls;
  for (const t of [0, 1]) {
    const a = C.active[t];
    if (!a) continue;
    if (a.side === "both" || (a.side === "off") === (off === t)) a.left--;
    if (a.left <= 0) C.active[t] = null;
  }
  G.ctx.mods = null;
}
/** Apply one beat to the game (score, box, ctx, possession arrow, league-norm stats). */
export function playBeat(G, b) {
  const st = G.stats, what = applyBeat(G.score, G.box, b, G.ctx, G.on);
  if (b.first) st.poss++;
  if (b.k === "to") st.to++;
  if (b.k === "shot") { st.fga++; if (b.kind === "three") st.tpa++; if (b.made) st.fgm++; if (b.fb) st.fb++; if (b.late) st.late++; if (b.foul >= 0) st.pf++; }
  if (b.k === "ft") st.fta += b.n;
  if (b.k === "foul") { st.pf++; if (b.intentional) st.intentional++; }
  if (b.k === "timeout") st.timeouts++;
  if (b.next !== undefined) G.team = b.next;
  return what;
}

/** A whole game, headless, between two teams (or bare PG..C lineups). perQ = possessions per quarter (both teams; 50 ≈ an NBA game). */
export function simGame(a, b, rng, perQ = 50) {
  const teams = [asTeam(a), asTeam(b)], G = newGame(rng, perQ);
  for (;;) {
    if (quarterDone(G)) { if (gameDone(G)) break; nextQuarter(G); }
    for (const beat of rollPoss(G, teams, rng)) playBeat(G, beat);
  }
  return { score: G.score, box: G.box, stats: G.stats };
}

/** The league: 29 fixed AI teams (same for everyone, so seasons are comparable). Each is a sensible pack draft. */
export const LEAGUE = (() => {
  const r = seeded(1946), teams = [];
  for (let t = 0; t < 29; t++) {
    const pool = r.shuffle(POOL.slice()), eight = [];
    for (let i = 0; i < 5 + BENCH; i++) {                                      // a 3-option draft, no Legendary: take the best
      const opts = pool.slice(i * 3, i * 3 + 3).sort((x, y) => bestPos(y).ovr - bestPos(x).ovr);
      eight.push(opts[0]);
    }
    const bench = eight.slice(5);                                              // first five start (league strength as tuned), next three are its bench
    teams.push({ five: bestLineup(eight.slice(0, 5)), bench, backs: bench.map((n) => POS.indexOf(bestPos(n).pos)) });
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
function topScorer(box) { let best = 0; for (let i = 1; i < 5 + BENCH; i++) if (box["0:" + i].pts > box["0:" + best].pts) best = i; return { i: best, pts: box["0:" + best].pts }; }
