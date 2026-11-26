"""Showdown — the hand-reading speed game's daily Time Trial (pure logic, no I/O).

Every hand and the board are face up; name the winner. The daily is DAILY_DEALS deals per
(variant, hands) table, the same for everyone that puzzle-day (seeded from the day), ramping
easy → medium → hard. The server deals and grades; the page (web/static/showdown.js) rebuilds each
deal's choices + explanation from the bare cards with deals.js `fromCards`, which shares its rules
with this module: one pick per step —
  high: "s<i>" (seat i), or "split" when the high is chopped (hard deals only)
  low (hi-lo variants): "s<i>", "split" (hard, chopped low) or "nolow"
  cat (hard deals): "c<category>" of the winning high hand
Score = server-measured time + PENALTY_MS per missed deal (lower wins).

Twin of the TV repo's games/showdown/deals.js (trap selection is the same idea, not bit-identical:
the server deals with Python's random, the TV with its own rng).
"""

from __future__ import annotations

import hashlib
import random
from itertools import combinations

from shared.showdown_eval import VARIANTS, best_high, deck_for, eval5, max_hands, showdown

DAILY_DEALS = 10
PENALTY_MS = 10_000
CANDIDATES = [1, 10, 20]
DEFAULT_HANDS = {"nlh": 6, "short": 6, "plo4": 4, "plo5": 4, "plo6": 4, "o8": 4, "bigo": 4}


def level_for(i: int, count: int = DAILY_DEALS) -> int:
    """Ramp: first third easy, middle third medium, last third hard."""
    return 0 if i < count / 3 else 1 if i < 2 * count / 3 else 2


def valid_table(variant: str, hands: int) -> bool:
    return variant in VARIANTS and 2 <= hands <= max_hands(variant)


def daily_seed(day: str, variant: str, hands: int) -> int:
    return int(hashlib.sha256(f"showdown:{day}:{variant}:{hands}".encode()).hexdigest()[:8], 16)


def _misread(variant: str, hand, board):
    v = VARIANTS[variant]
    if v["omaha"]:
        return max(eval5(f)[0] for f in combinations(list(hand) + list(board), 5))
    if v["deck"] == 36:
        return best_high("nlh", hand, board)["score"]
    return None


def trickiness(variant: str, hands, board, sd: dict) -> int:
    v = VARIANTS[variant]
    trick = 0
    top = sd["highs"][sd["high"][0]]["score"]
    if v["omaha"] or v["deck"] == 36:
        top_wrong = _misread(variant, hands[sd["high"][0]], board) if v["deck"] == 36 else top
        traps = sum(1 for i, h in enumerate(hands)
                    if i not in sd["high"] and _misread(variant, h, board) > top_wrong)
        trick += 3 * min(2, traps)
    cats = sorted((e["cat"] for e in sd["highs"]), reverse=True)
    if len(cats) > 1 and cats[0] == cats[1]:
        trick += 3 if len(sd["high"]) > 1 else 2
    if not v["omaha"] and not sd["highs"][sd["high"][0]]["hole"]:
        trick += 2
    if v["hilo"]:
        if not sd["low"]:
            trick += 1
        for i, h in enumerate(hands):
            ranks = {c[0] for c in h}
            if {"A", "2"} <= ranks and sd["low"] and i not in sd["low"]:
                trick += 2
        if len(sd["low"]) > 1:
            trick += 1
    return trick


def make_deal(rng: random.Random, variant: str, n: int, level: int) -> dict:
    hole = VARIANTS[variant]["hole"]
    best = None
    for k in range(400):
        if best and k >= CANDIDATES[level]:
            break
        d = deck_for(variant)
        rng.shuffle(d)
        hands = [d[i * hole:(i + 1) * hole] for i in range(n)]
        board = d[n * hole:n * hole + 5]
        sd = showdown(variant, hands, board)
        chop = len(sd["high"]) > 1 or (sd["low"] is not None and len(sd["low"]) > 1)
        if level < 2 and chop:
            continue
        t = trickiness(variant, hands, board, sd)
        if best is None or t > best[0]:
            best = (t, hands, board)
    return {"hands": best[1], "board": best[2], "level": level}


def daily_deals(day: str, variant: str, hands: int) -> list[dict]:
    rng = random.Random(daily_seed(day, variant, hands))
    return [make_deal(rng, variant, hands, level_for(i)) for i in range(DAILY_DEALS)]


def answers(variant: str, deal: dict) -> list[str]:
    """The right pick for each step of a deal, in step order."""
    sd = showdown(variant, deal["hands"], deal["board"])
    lvl = deal["level"]
    out = ["split" if len(sd["high"]) > 1 else f"s{sd['high'][0]}"]
    if VARIANTS[variant]["hilo"]:
        low = sd["low"]
        out.append("nolow" if not low else "split" if len(low) > 1 else f"s{low[0]}")
    if lvl == 2:
        out.append(f"c{sd['highs'][sd['high'][0]]['cat']}")
    return out


def grade(variant: str, deals: list[dict], picks: list) -> list[bool]:
    """Per deal: did the player's picks match every step? Missing/garbled picks are wrong."""
    out = []
    for i, deal in enumerate(deals):
        p = picks[i] if i < len(picks) and isinstance(picks[i], list) else []
        out.append([str(x) for x in p] == answers(variant, deal))
    return out


def score_ms(elapsed_ms: int, right: list[bool]) -> int:
    return elapsed_ms + PENALTY_MS * sum(1 for r in right if not r)
