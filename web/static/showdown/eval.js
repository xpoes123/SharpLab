// Showdown hand evaluator — every variant the game deals, brute force and exhaustively tested.
//
// Shared code: this file is copied verbatim into SharpLab (web/static/showdown/eval.js) and ported
// to Python there (shared/showdown_eval.py). Change one, change all three; both repos test against
// the same reference fixture (tests/fixtures/showdown.json in both), generated from the independent
// phevaluator library by SharpLab's scripts/showdown_ref.py (which also checks all 2,598,960 5-card
// hands and all 376,992 Short Deck hands against it, and 20,000 random deals per variant).
//
// Cards are strings "As", "Td", "7c": rank 2-9 T J Q K A, suit c d h s.
// A 5-card hand scores as one integer, higher = better:
//   score = strength(category) * 15^5 + the five ranks packed most-significant first.
// Categories (CAT): 0 high card, 1 pair, 2 two pair, 3 trips, 4 straight, 5 flush, 6 full house,
// 7 quads, 8 straight flush. Short Deck (6+, Triton rules) re-orders strength: a flush beats a
// full house, trips beat a straight, and A-6-7-8-9 is the wheel.
//
// Variants:
//   nlh    Hold'em: 2 hole cards, best 5 of the 7 in ANY combination (can play the board).
//   short  Short Deck 6+ Hold'em: 36-card deck (6-A), Hold'em rules, re-ordered rankings.
//   plo4 / plo5 / plo6  Omaha: EXACTLY 2 hole cards + EXACTLY 3 board cards.
//   o8     Omaha Hi-Lo 8-or-better (4 cards): the pot splits between the best high and the best
//          qualifying low (5 distinct ranks 8 or under, ace low, straights/flushes don't count,
//          again exactly 2 hole + 3 board, chosen independently of the high hand).
//   bigo   Big O: 5-card Omaha Hi-Lo.

export const RANKS = "23456789TJQKA";
export const SUITS = "cdhs";
export const CAT_NAMES = ["High card", "Pair", "Two pair", "Three of a kind", "Straight", "Flush",
  "Full house", "Four of a kind", "Straight flush"];
const RANK_WORD = { 2: "Two", 3: "Three", 4: "Four", 5: "Five", 6: "Six", 7: "Seven", 8: "Eight", 9: "Nine",
  10: "Ten", 11: "Jack", 12: "Queen", 13: "King", 14: "Ace" };
const RANK_PLURAL = { 6: "Sixes" };

export const VARIANTS = {
  nlh:   { id: "nlh",   name: "Hold'em",          short: "NLH",   hole: 2, omaha: false, hilo: false, deck: 52 },
  short: { id: "short", name: "Short Deck 6+",    short: "6+",    hole: 2, omaha: false, hilo: false, deck: 36 },
  plo4:  { id: "plo4",  name: "PLO",              short: "PLO4",  hole: 4, omaha: true,  hilo: false, deck: 52 },
  plo5:  { id: "plo5",  name: "PLO5",             short: "PLO5",  hole: 5, omaha: true,  hilo: false, deck: 52 },
  plo6:  { id: "plo6",  name: "PLO6",             short: "PLO6",  hole: 6, omaha: true,  hilo: false, deck: 52 },
  o8:    { id: "o8",    name: "Omaha Hi-Lo",      short: "O8",    hole: 4, omaha: true,  hilo: true,  deck: 52 },
  bigo:  { id: "bigo",  name: "Big O",            short: "Big O", hole: 5, omaha: true,  hilo: true,  deck: 52 },
};
// Most hands a variant can deal from one deck with a 5-card board.
export function maxHands(v) { return Math.min(9, Math.floor((VARIANTS[v].deck - 5) / VARIANTS[v].hole)); }

const STD_STRENGTH = [0, 1, 2, 3, 4, 5, 6, 7, 8];
// Short Deck strength by category: high 0, pair 1, two pair 2, straight 3, trips 4, full house 5,
// flush 6, quads 7, straight flush 8.
const SHORT_STRENGTH = [0, 1, 2, 4, 3, 6, 5, 7, 8];

export function rankOf(card) { return RANKS.indexOf(card[0]) + 2; }
export function suitOf(card) { return card[1]; }

export function deckFor(variant) {
  const lo = VARIANTS[variant].deck === 36 ? 6 : 2;
  const d = [];
  for (const r of RANKS) if (RANKS.indexOf(r) + 2 >= lo) for (const s of SUITS) d.push(r + s);
  return d;
}

function pack(cat, ranks, shortDeck) {
  let v = (shortDeck ? SHORT_STRENGTH : STD_STRENGTH)[cat];
  for (let i = 0; i < 5; i++) v = v * 15 + (ranks[i] || 0);
  return v;
}

// Evaluate exactly five cards → {score, cat, ranks (tiebreak order), cards (display order)}.
export function eval5(cards, shortDeck = false) {
  const rs = cards.map(rankOf), ss = cards.map(suitOf);
  const flush = ss.every((s) => s === ss[0]);
  const count = {};
  for (const r of rs) count[r] = (count[r] || 0) + 1;
  // groups: by count desc, then rank desc
  const groups = Object.keys(count).map(Number).sort((a, b) => count[b] - count[a] || b - a);
  const uniq = [...new Set(rs)].sort((a, b) => b - a);
  let straightHigh = 0;
  if (uniq.length === 5) {
    if (uniq[0] - uniq[4] === 4) straightHigh = uniq[0];
    else if (!shortDeck && uniq.join() === "14,5,4,3,2") straightHigh = 5;
    else if (shortDeck && uniq.join() === "14,9,8,7,6") straightHigh = 9;
  }
  let cat, ranks;
  const c0 = count[groups[0]], c1 = groups.length > 1 ? count[groups[1]] : 0;
  if (straightHigh && flush) { cat = 8; ranks = [straightHigh]; }
  else if (c0 === 4) { cat = 7; ranks = groups; }
  else if (c0 === 3 && c1 === 2) { cat = 6; ranks = groups; }
  else if (flush) { cat = 5; ranks = uniq; }
  else if (straightHigh) { cat = 4; ranks = [straightHigh]; }
  else if (c0 === 3) { cat = 3; ranks = groups; }
  else if (c0 === 2 && c1 === 2) { cat = 2; ranks = groups; }
  else if (c0 === 2) { cat = 1; ranks = groups; }
  else { cat = 0; ranks = uniq; }
  // display order: grouped cards first, straights high to low (wheel ace last)
  const key = (c) => {
    const r = rankOf(c);
    if (straightHigh && r === 14 && straightHigh !== 14) return -1;
    return count[r] * 100 + r;
  };
  const shown = [...cards].sort((a, b) => key(b) - key(a) || SUITS.indexOf(suitOf(b)) - SUITS.indexOf(suitOf(a)));
  return { score: pack(cat, ranks, shortDeck), cat, ranks, cards: shown };
}

// k-combinations of an array (indices kept in order).
export function combos(arr, k) {
  const out = [];
  const pick = (start, acc) => {
    if (acc.length === k) { out.push(acc.slice()); return; }
    for (let i = start; i <= arr.length - (k - acc.length); i++) { acc.push(arr[i]); pick(i + 1, acc); acc.pop(); }
  };
  pick(0, []);
  return out;
}

// Best high hand for one player. Returns the eval5 result plus which hole/board cards it used.
export function bestHigh(variant, hole, board) {
  const V = VARIANTS[variant], sd = V.deck === 36;
  let best = null;
  const consider = (h, b) => {
    const e = eval5([...h, ...b], sd);
    if (!best || e.score > best.score) best = { ...e, hole: h, board: b };
  };
  if (V.omaha) {
    for (const h of combos(hole, 2)) for (const b of combos(board, 3)) consider(h, b);
  } else {
    const all = [...hole, ...board];
    for (const five of combos(all, 5)) consider(five.filter((c) => hole.includes(c)), five.filter((c) => board.includes(c)));
  }
  return best;
}

// Low value of 5 distinct low ranks (ace = 1): smaller is better; packed high card first.
function lowValue(ranks) {
  const s = [...ranks].sort((a, b) => b - a);
  let v = 0;
  for (const r of s) v = v * 15 + r;
  return v;
}
const lowRank = (c) => (rankOf(c) === 14 ? 1 : rankOf(c));

// Best qualifying 8-or-better low (Omaha: 2 hole + 3 board), or null when there is none.
export function bestLow(variant, hole, board) {
  let best = null;
  for (const h of combos(hole, 2)) for (const b of combos(board, 3)) {
    const rs = [...h, ...b].map(lowRank);
    if (rs.some((r) => r > 8) || new Set(rs).size < 5) continue;
    const v = lowValue(rs);
    if (!best || v < best.value) best = { value: v, ranks: [...rs].sort((a, b) => b - a), hole: h, board: b,
      cards: [...h, ...b].sort((x, y) => lowRank(y) - lowRank(x)) };
  }
  return best;
}

// Full showdown: who wins the high (and the low in hi-lo). Ties → several winners (a chop).
export function showdown(variant, hands, board) {
  const highs = hands.map((h) => bestHigh(variant, h, board));
  const top = Math.max(...highs.map((e) => e.score));
  const out = { variant, highs, high: highs.map((e, i) => (e.score === top ? i : -1)).filter((i) => i >= 0), lows: null, low: null };
  if (VARIANTS[variant].hilo) {
    out.lows = hands.map((h) => bestLow(variant, h, board));
    const vals = out.lows.filter(Boolean).map((l) => l.value);
    if (vals.length) {
      const m = Math.min(...vals);
      out.low = out.lows.map((l, i) => (l && l.value === m ? i : -1)).filter((i) => i >= 0);
    } else out.low = [];
  }
  return out;
}

function word(r) { return RANK_WORD[r]; }
function plural(r) { return RANK_PLURAL[r] || word(r) + "s"; }

// "Flush, Ace-high", "Full house, Kings full of Sevens", "Straight, Five-high (wheel)" …
export function handName(e) {
  const r = e.ranks;
  switch (e.cat) {
    case 8: return r[0] === 14 ? "Royal flush" : `Straight flush, ${word(r[0])}-high`;
    case 7: return `Four ${plural(r[0])}`;
    case 6: return `Full house, ${plural(r[0])} full of ${plural(r[1])}`;
    case 5: return `Flush, ${word(r[0])}-high`;
    case 4: return `Straight, ${word(r[0])}-high`;
    case 3: return `Three ${plural(r[0])}`;
    case 2: return `Two pair, ${plural(r[0])} and ${plural(r[1])}`;
    case 1: return `Pair of ${plural(r[0])}`;
    default: return `${word(r[0])}-high`;
  }
}
export function lowName(l) {
  if (!l) return "No low";
  return l.ranks.map((x) => (x === 1 ? "A" : String(x))).join("-") + " low";
}
