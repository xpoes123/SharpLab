"""Reference checks + fixture for the Showdown evaluator, using the independent phevaluator library
(not a project dependency: run it with  uv run --with phevaluator python scripts/showdown_ref.py ...).

usage: python scripts/showdown_ref.py exhaustive        # all 2,598,960 5-card hands + all 376,992 short-deck hands
       python scripts/showdown_ref.py random N          # N random deals per variant vs reference (no file written)
       python scripts/showdown_ref.py fixture N tests/fixtures/showdown.json [tricky.json]
"""
import json
import random
import sys
from itertools import combinations

import phevaluator as ph

sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1]))
from shared import showdown_eval as se  # noqa: E402

# phevaluator rank bands (1 = royal flush, 7462 = 7-high): category from rank
_BANDS = [(10, 8), (166, 7), (322, 6), (1599, 5), (1609, 4), (2467, 3), (3325, 2), (6185, 1), (7462, 0)]


def ph_cat(rank):
    for hi, cat in _BANDS:
        if rank <= hi:
            return cat


def ref5(cards, short=False):
    """Reference key, LOWER = better: (−strength, rank-within)."""
    r = ph.evaluate_cards(*cards)
    cat = ph_cat(r)
    if short:
        ranks = {c[0] for c in cards}
        if ranks == set("A9876"):
            flush = len({c[1] for c in cards}) == 1
            cat, r = (8, 10.5) if flush else (4, 1609.5)
        strength = [0, 1, 2, 4, 3, 6, 5, 7, 8][cat]
    else:
        strength = cat
    return (-strength, r), cat


def ref_high(variant, hole, board):
    v = se.VARIANTS[variant]
    short = v["deck"] == 36
    if variant in ("plo4", "o8"):
        r = ph.evaluate_omaha_cards(*board, *hole)
        return (-ph_cat(r), r), ph_cat(r)
    if v["omaha"]:
        return min(ref5(list(h) + list(b), short) for h in combinations(hole, 2) for b in combinations(board, 3))
    if not short:
        r = ph.evaluate_cards(*hole, *board)
        return (-ph_cat(r), r), ph_cat(r)
    return min(ref5(list(f), True) for f in combinations(list(hole) + list(board), 5))


# All 56 eight-or-better lows, best first: compare highest card down.
LOWS = sorted(combinations(range(1, 9), 5), key=lambda s: sorted(s, reverse=True))


def ref_low(hole, board):
    def lr(c):
        return 1 if c[0] == "A" else se.rank_of(c)
    hr = {lr(c) for c in hole}
    br = {lr(c) for c in board}
    for i, s in enumerate(LOWS):
        for two in combinations(s, 2):
            rest = set(s) - set(two)
            if set(two) <= hr and rest <= br:
                return i, sorted(s, reverse=True)
    return None, None


def deal(variant, rng, n=None):
    v = se.VARIANTS[variant]
    n = n or rng.randint(2, se.max_hands(variant))
    d = se.deck_for(variant)
    rng.shuffle(d)
    hands = [d[i * v["hole"]:(i + 1) * v["hole"]] for i in range(n)]
    board = d[n * v["hole"]:n * v["hole"] + 5]
    return hands, board


def reference(variant, hands, board):
    refs = [ref_high(variant, h, board) for h in hands]
    best = min(k for k, _ in refs)
    out = {"high": [i for i, (k, _) in enumerate(refs) if k == best], "cats": [c for _, c in refs]}
    if se.VARIANTS[variant]["hilo"]:
        lows = [ref_low(h, board) for h in hands]
        vals = [i for i, _ in lows if i is not None]
        out["low"] = [j for j, (i, _) in enumerate(lows) if i is not None and i == min(vals)] if vals else []
        out["lows"] = [r for _, r in lows]
    return out


def check(variant, hands, board):
    ref = reference(variant, hands, board)
    got = se.showdown(variant, hands, board)
    assert got["high"] == ref["high"], (variant, hands, board, got["high"], ref)
    assert [e["cat"] for e in got["highs"]] == ref["cats"], (variant, hands, board)
    if "low" in ref:
        assert got["low"] == ref["low"], (variant, hands, board, got["low"], ref)
        assert [l and [1 if x == 1 else x for x in l["ranks"]] for l in got["lows"]] == ref["lows"]
    return ref


def exhaustive():
    for short in (False, True):
        deck = se.deck_for("short" if short else "nlh")
        by_ref, cats = {}, [0] * 9
        n = 0
        for five in combinations(deck, 5):
            score, cat, _ = se.eval5(five, short)
            key, rcat = ref5(five, short)
            assert cat == rcat, five
            prev = by_ref.setdefault(key, score)
            assert prev == score, ("same reference class, different score", five)
            cats[cat] += 1
            n += 1
        # ordering of classes must match exactly
        keys = sorted(by_ref)                     # best first
        scores = [by_ref[k] for k in keys]
        assert all(a > b for a, b in zip(scores, scores[1:])), "class order mismatch"
        print("short" if short else "standard", n, "hands,", len(keys), "classes, cats", cats)


def main():
    mode = sys.argv[1]
    if mode == "exhaustive":
        exhaustive()
    elif mode == "random":
        n = int(sys.argv[2])
        rng = random.Random(7)
        for v in se.VARIANTS:
            for _ in range(n):
                check(v, *deal(v, rng))
            print(v, n, "ok")
    elif mode == "fixture":
        n, path = int(sys.argv[2]), sys.argv[3]
        rng = random.Random(2026)
        out = []
        for t in (json.load(open(sys.argv[4])) if len(sys.argv) > 4 else []):   # the game's own tricky deals
            ref = check(t["variant"], t["hands"], t["board"])
            out.append({"variant": t["variant"], "hands": t["hands"], "board": t["board"], "tricky": True, **ref})
        for v in se.VARIANTS:
            for _ in range(n):
                hands, board = deal(v, rng)
                ref = check(v, hands, board)
                out.append({"variant": v, "hands": hands, "board": board, **ref})
        json.dump({"source": "phevaluator 0.5 + 56-low enumeration (scripts: see header)", "deals": out},
                  open(path, "w"), separators=(",", ":"))
        print("wrote", len(out))


main()
