// Showdown deal generator: tricky deals on purpose, plus the "why" for each answer.
// Shared with SharpLab (web/static/showdown/deals.js, a verbatim copy; its server deals the daily with the
// Python twin shared/showdown.py and the page rebuilds each deal with fromCards). Only needs rng.float().
//
// A deal at level 0 is random (no chops). Levels 1-2 draw several candidates and keep the trickiest:
// a hand that LOOKS best read with the wrong rules (Hold'em eyes on an Omaha deal, normal rankings on a
// Short Deck deal), the top two in the same category (kickers, counterfeits), a chop (level 2), and
// for hi-lo an A-2 that doesn't win the low, or no low at all.
import { VARIANTS, CAT_NAMES, deckFor, bestHigh, showdown, handName, lowName, eval5, combos, maxHands } from "./eval.js";

export const LETTERS = "ABCDEFGHI";
export const LEVELS = ["Easy", "Medium", "Hard"];
const CANDIDATES = [1, 10, 20];

function shuffled(arr, rng) {
  const o = arr.slice();
  for (let i = o.length - 1; i > 0; i--) { const j = Math.floor(rng.float() * (i + 1)); [o[i], o[j]] = [o[j], o[i]]; }
  return o;
}

function rawDeal(variant, n, rng) {
  const hole = VARIANTS[variant].hole, d = shuffled(deckFor(variant), rng);
  return { hands: Array.from({ length: n }, (_, i) => d.slice(i * hole, (i + 1) * hole)), board: d.slice(n * hole, n * hole + 5) };
}

// What a hand looks like to someone reading it with the wrong rules.
function misread(variant, hand, board) {
  const V = VARIANTS[variant];
  if (V.omaha) {   // Hold'em eyes: any five of hole + board
    let best = null;
    for (const five of combos([...hand, ...board], 5)) { const e = eval5(five); if (!best || e.score > best.score) best = e; }
    return best;
  }
  if (V.deck === 36) return bestHigh("nlh", hand, board);   // normal rankings (no A-6-7-8-9 wheel either)
  return null;
}

function analyse(variant, deal) {
  const sd = showdown(variant, deal.hands, deal.board);
  const top = sd.highs[sd.high[0]];
  const notes = [];
  let trick = 0;
  // the wrong-rules trap: a loser who'd look like the winner
  const V = VARIANTS[variant];
  if (V.omaha || V.deck === 36) {
    const topWrong = V.deck === 36 ? misread(variant, deal.hands[sd.high[0]], deal.board) : null;
    deal.hands.forEach((h, i) => {
      if (sd.high.includes(i)) return;
      const m = misread(variant, h, deal.board);
      if (!m) return;
      const beats = V.deck === 36 ? (topWrong && m.score > topWrong.score) : m.score > top.score;
      if (beats && notes.length < 2) {
        trick += 3;
        notes.push(V.omaha
          ? `${LETTERS[i]} looks like ${handName(m).toLowerCase()}, but Omaha plays exactly 2 hole cards: only ${handName(sd.highs[i]).toLowerCase()}.`
          : `${LETTERS[i]} would win with normal rankings; in Short Deck ${shortRuleNote(sd.highs[i], top)}`);
      }
    });
  }
  const sorted = [...sd.highs].map((e, i) => ({ e, i })).sort((a, b) => b.e.score - a.e.score);
  if (sorted.length > 1 && sorted[1].e.cat === sorted[0].e.cat) trick += sd.high.length > 1 ? 3 : 2;
  if (!V.omaha && top.hole.length === 0) { trick += 2; notes.push("The board plays: nobody's hole cards beat it, so everyone left chops."); }
  if (V.hilo) {
    if (!sd.low.length) { trick += 1; notes.push("No low: a qualifying low needs 3 board cards 8 or under, all different."); }
    deal.hands.forEach((h, i) => {
      const ranks = h.map((c) => c[0]);
      if (ranks.includes("A") && ranks.includes("2") && !sd.low.includes(i) && sd.low.length && notes.length < 3) {
        trick += 2;
        notes.push(`${LETTERS[i]}'s A-2 is counterfeited or out-lowed: ${lowName(sd.lows[i])}.`);
      }
    });
    if (sd.low.length > 1) trick += 1;
  }
  return { sd, trick, notes };
}

function shortRuleNote(loser, winner) {
  if (winner.cat === 5 && loser.cat === 6) return "a flush beats a full house.";
  if (winner.cat === 3 && loser.cat === 4) return "three of a kind beats a straight.";
  if (winner.cat === 4 && winner.ranks[0] === 9) return "A-6-7-8-9 is a straight.";
  return "the order is flush > full house and trips > straight.";
}

// One deal ready to play: cards, the answer, the choice lists for each step, and the explanation.
// level: 0 easy (random, no chops, pick the seat), 1 medium (tricky), 2 hard (trickiest + chops + name the hand).
export function makeDeal(rng, variant, n, level) {
  n = Math.max(2, Math.min(n, maxHands(variant)));
  let best = null;
  for (let k = 0; k < 400 && (!best || k < CANDIDATES[level]); k++) {
    const deal = rawDeal(variant, n, rng);
    const a = analyse(variant, deal);
    const chop = a.sd.high.length > 1 || (a.sd.low && a.sd.low.length > 1);
    if (level < 2 && chop) continue;                              // chops only at Hard
    if (level === 0 && a.sd.highs[a.sd.high[0]].hole.length === 0) continue;
    if (!best || a.trick > best.a.trick) best = { deal, a };
  }
  if (!best) { const deal = rawDeal(variant, n, rng); best = { deal, a: analyse(variant, deal) }; }
  return build(variant, best.deal, best.a, level);
}

function seatItems(n) { return Array.from({ length: n }, (_, i) => ({ name: "s" + i, label: LETTERS[i] })); }
const chopLabel = (ids) => "Chop " + ids.map((i) => LETTERS[i]).join("+");

// The split key is always offered (a decoy when there's no chop), so its presence gives nothing away.
function splitItem(winners, scores) {
  if (winners.length > 1) return { name: "split", label: chopLabel(winners), ids: winners };
  const order = scores.map((s, i) => ({ s, i })).filter((x) => x.s !== null).sort((a, b) => b.s - a.s).map((x) => x.i);
  const ids = order.length >= 2 ? order.slice(0, 2).sort((a, b) => a - b) : [0, 1];
  return { name: "split", label: chopLabel(ids), ids };
}

// Rebuild a deal from bare cards (SharpLab's server-dealt daily): same steps, answers and explanation.
export function fromCards(variant, hands, board, level) {
  const deal = { hands, board };
  return build(variant, deal, analyse(variant, deal), level);
}

export function build(variant, deal, a, level) {
  const { sd, notes } = a;
  const n = deal.hands.length, V = VARIANTS[variant];
  const steps = [];
  const highAns = sd.high.length > 1 ? "split" : "s" + sd.high[0];
  const hiItems = seatItems(n);
  if (level === 2) { const sp = splitItem(sd.high, sd.highs.map((e) => e.score)); hiItems.push({ name: sp.name, label: sp.label }); }
  steps.push({ key: "high", prompt: V.hilo ? "Who wins the HIGH?" : "Who wins?", items: hiItems, answer: highAns,
    accept: sd.high.length > 1 && level < 2 ? sd.high.map((i) => "s" + i) : null });
  if (V.hilo) {
    const loItems = seatItems(n);
    if (level === 2) { const sp = splitItem(sd.low, sd.lows.map((l) => (l ? -l.value : null))); loItems.push({ name: sp.name, label: sp.label }); }
    loItems.push({ name: "nolow", label: "No low" });
    const loAns = !sd.low.length ? "nolow" : sd.low.length > 1 ? (level === 2 ? "split" : "s" + sd.low[0]) : "s" + sd.low[0];
    steps.push({ key: "low", prompt: "Who wins the LOW?", items: loItems, answer: loAns, accept: sd.low.length > 1 && level < 2 ? sd.low.map((i) => "s" + i) : null });
  }
  if (level === 2) {
    const cat = sd.highs[sd.high[0]].cat;
    const near = [cat];
    for (const d of [1, -1, 2, -2, 3, -3, 4]) { const c = cat + d; if (c >= 0 && c <= 8 && near.length < 4) near.push(c); }
    const items = near.sort((x, y) => x - y).map((c) => ({ name: "c" + c, label: CAT_NAMES[c] }));
    steps.push({ key: "cat", prompt: "Winning hand?", items, answer: "c" + cat });
  }
  const hands = deal.hands.map((h, i) => {
    const e = sd.highs[i];
    const out = { cards: h, use: e.hole, board: e.board, name: handName(e), cat: e.cat, win: sd.high.includes(i) };
    if (V.hilo) { const l = sd.lows[i]; out.low = l ? { use: l.hole, board: l.board, name: lowName(l) } : null; out.lowWin = sd.low.includes(i); }
    return out;
  });
  const w = sd.high.map((i) => LETTERS[i]).join(" + ");
  const top = hands[sd.high[0]];
  let headline = sd.high.length > 1 ? `${w} chop · ${top.name}` : `${w} wins · ${top.name}`;
  const used = V.omaha ? `${top.use.join(" ")} + ${top.board.join(" ")}` : top.use.length ? `${top.use.join(" ")} with the board` : "the board";
  let lowLine = null;
  if (V.hilo) lowLine = sd.low.length ? `Low: ${sd.low.map((i) => LETTERS[i]).join(" + ")} · ${hands[sd.low[0]].low.name}` : "Low: none qualifies";
  return { variant, level, board: deal.board, hands, steps, headline, used, lowLine, notes: notes.slice(0, 2) };
}

// Grade a full answer (one pick per step) → {right, wrongStep}.
export function grade(deal, picks) {
  for (let i = 0; i < deal.steps.length; i++) {
    const st = deal.steps[i], p = picks[i];
    if (p !== st.answer && !(st.accept && st.accept.includes(p))) return { right: false, wrongStep: i };
  }
  return { right: true, wrongStep: -1 };
}
