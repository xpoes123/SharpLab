"""Showdown hand evaluator — Python port of eval.js. The JS original lives in the TV repo
(xpoes123/tv games/showdown/eval.js); web/static/showdown/eval.js is a verbatim copy of it. Change one,
change all three: both repos test against the same phevaluator-generated fixture
(tests/fixtures/showdown.json, made by scripts/showdown_ref.py).

Cards are "As", "Td", "7c". A 5-card hand scores as one int, higher = better:
score = strength(category) * 15**5 + the five ranks packed most-significant first.
Categories: 0 high, 1 pair, 2 two pair, 3 trips, 4 straight, 5 flush, 6 full house, 7 quads,
8 straight flush. Short Deck (Triton rules): flush > full house, trips > straight, A-6-7-8-9 wheel.
Omaha variants use EXACTLY 2 hole + 3 board cards; Hold'em variants any 5 of the 7.
Hi-lo (o8, bigo): 8-or-better low, 5 distinct ranks <= 8, ace low, also exactly 2 + 3.
"""

from __future__ import annotations

from itertools import combinations

RANKS = "23456789TJQKA"
SUITS = "cdhs"
CAT_NAMES = ["High card", "Pair", "Two pair", "Three of a kind", "Straight", "Flush",
             "Full house", "Four of a kind", "Straight flush"]

VARIANTS = {
    "nlh":   {"name": "Hold'em",       "hole": 2, "omaha": False, "hilo": False, "deck": 52},
    "short": {"name": "Short Deck 6+", "hole": 2, "omaha": False, "hilo": False, "deck": 36},
    "plo4":  {"name": "PLO",           "hole": 4, "omaha": True,  "hilo": False, "deck": 52},
    "plo5":  {"name": "PLO5",          "hole": 5, "omaha": True,  "hilo": False, "deck": 52},
    "plo6":  {"name": "PLO6",          "hole": 6, "omaha": True,  "hilo": False, "deck": 52},
    "o8":    {"name": "Omaha Hi-Lo",   "hole": 4, "omaha": True,  "hilo": True,  "deck": 52},
    "bigo":  {"name": "Big O",         "hole": 5, "omaha": True,  "hilo": True,  "deck": 52},
}
_STD = [0, 1, 2, 3, 4, 5, 6, 7, 8]
_SHORT = [0, 1, 2, 4, 3, 6, 5, 7, 8]


def max_hands(variant: str) -> int:
    v = VARIANTS[variant]
    return min(9, (v["deck"] - 5) // v["hole"])


def rank_of(card: str) -> int:
    return RANKS.index(card[0]) + 2


def deck_for(variant: str) -> list[str]:
    lo = 6 if VARIANTS[variant]["deck"] == 36 else 2
    return [r + s for r in RANKS if RANKS.index(r) + 2 >= lo for s in SUITS]


def eval5(cards, short_deck: bool = False) -> tuple[int, int, list[int]]:
    """(score, category, tiebreak ranks) for exactly five cards."""
    rs = [rank_of(c) for c in cards]
    flush = len({c[1] for c in cards}) == 1
    count: dict[int, int] = {}
    for r in rs:
        count[r] = count.get(r, 0) + 1
    groups = sorted(count, key=lambda r: (-count[r], -r))
    uniq = sorted(set(rs), reverse=True)
    straight = 0
    if len(uniq) == 5:
        if uniq[0] - uniq[4] == 4:
            straight = uniq[0]
        elif not short_deck and uniq == [14, 5, 4, 3, 2]:
            straight = 5
        elif short_deck and uniq == [14, 9, 8, 7, 6]:
            straight = 9
    c0 = count[groups[0]]
    c1 = count[groups[1]] if len(groups) > 1 else 0
    if straight and flush:
        cat, ranks = 8, [straight]
    elif c0 == 4:
        cat, ranks = 7, groups
    elif c0 == 3 and c1 == 2:
        cat, ranks = 6, groups
    elif flush:
        cat, ranks = 5, uniq
    elif straight:
        cat, ranks = 4, [straight]
    elif c0 == 3:
        cat, ranks = 3, groups
    elif c0 == 2 and c1 == 2:
        cat, ranks = 2, groups
    elif c0 == 2:
        cat, ranks = 1, groups
    else:
        cat, ranks = 0, uniq
    v = (_SHORT if short_deck else _STD)[cat]
    for i in range(5):
        v = v * 15 + (ranks[i] if i < len(ranks) else 0)
    return v, cat, list(ranks)


def best_high(variant: str, hole, board) -> dict:
    v = VARIANTS[variant]
    sd = v["deck"] == 36
    best = None
    if v["omaha"]:
        cands = ((h, b) for h in combinations(hole, 2) for b in combinations(board, 3))
    else:
        cands = (([c for c in five if c in hole], [c for c in five if c in board])
                 for five in combinations(list(hole) + list(board), 5))
    for h, b in cands:
        score, cat, ranks = eval5(list(h) + list(b), sd)
        if best is None or score > best["score"]:
            best = {"score": score, "cat": cat, "ranks": ranks, "hole": list(h), "board": list(b)}
    return best


def _low_rank(c: str) -> int:
    return 1 if rank_of(c) == 14 else rank_of(c)


def best_low(variant: str, hole, board) -> dict | None:
    best = None
    for h in combinations(hole, 2):
        for b in combinations(board, 3):
            rs = [_low_rank(c) for c in h + b]
            if max(rs) > 8 or len(set(rs)) < 5:
                continue
            desc = sorted(rs, reverse=True)
            value = 0
            for r in desc:
                value = value * 15 + r
            if best is None or value < best["value"]:
                best = {"value": value, "ranks": desc, "hole": list(h), "board": list(b)}
    return best


def showdown(variant: str, hands, board) -> dict:
    highs = [best_high(variant, h, board) for h in hands]
    top = max(e["score"] for e in highs)
    out = {"highs": highs, "high": [i for i, e in enumerate(highs) if e["score"] == top],
           "lows": None, "low": None}
    if VARIANTS[variant]["hilo"]:
        lows = [best_low(variant, h, board) for h in hands]
        out["lows"] = lows
        vals = [l["value"] for l in lows if l]
        out["low"] = [i for i, l in enumerate(lows) if l and l["value"] == min(vals)] if vals else []
    return out
