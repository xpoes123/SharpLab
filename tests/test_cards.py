"""Unit tests for the pure card-economics engine (shared/cards.py)."""

import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared import cards


def _subjects(n=200):
    return [
        {
            "key": f"p{i}",
            "name": f"p{i}",
            "season": 2003,
            "stardom": float(i),
            "is_rookie": False,
        }
        for i in range(n)
    ]


# --- book value / bands ------------------------------------------------------


def test_book_value_is_band_floor():
    for r in cards.RARITIES:
        assert cards.band(r, False, None)[0] == cards.book_value(r, False, None)


def test_rarity_book_ordering():
    vals = [cards.book_value(r, False, None) for r in cards.RARITIES]
    assert vals == sorted(vals) and len(set(vals)) == len(vals)  # strictly increasing


def test_holo_doubles_book():
    assert cards.book_value("rare", True, None) == 2 * cards.book_value("rare", False, None)


def test_gem_floor_applies():
    b = cards.book_value("common", False, "sapphire")
    assert b == max(
        cards.book_value("common", False, None) * cards.GEMS["sapphire"][1],
        cards.GEMS["sapphire"][2],
    )
    assert b >= cards.GEMS["sapphire"][2]


def test_ceiling_above_floor():
    lo, hi = cards.band("epic", False, None)
    assert hi > lo


# --- rolls -------------------------------------------------------------------


def test_gem_roll_deterministic_and_rare():
    rng = random.Random(0)
    n = 100_000
    counts = {}
    for _ in range(n):
        g = cards.roll_gem(rng)
        counts[g] = counts.get(g, 0) + 1
    assert counts.get("black_lotus", 0) < n * 0.001  # extremely rare, not guaranteed
    assert counts.get(None, 0) > n * 0.9  # most cards have no gem


# --- manifest ----------------------------------------------------------------


def test_build_manifest_sums_and_orders():
    m = cards.build_manifest(_subjects(200), total_cards=5000)
    assert sum(d["copies"] for d in m) == 5000  # total copies == requested
    by_key = {d["key"]: d for d in m}
    assert by_key["p199"]["rarity"] == "legendary"  # highest stardom
    assert by_key["p0"]["rarity"] == "common"  # lowest stardom


def test_rarer_tiers_get_fewer_copies_per_design():
    """Per-design copy counts must decrease monotonically as tiers get rarer."""
    m = cards.build_manifest(_subjects(400), total_cards=8000)
    avg = {}
    for r in cards.RARITIES:
        designs = [d for d in m if d["rarity"] == r]
        assert designs, f"no {r} designs"
        avg[r] = sum(d["copies"] for d in designs) / len(designs)
    # common >= uncommon >= rare >= epic >= legendary
    ordered = [avg[r] for r in cards.RARITIES]  # common..legendary
    assert ordered == sorted(ordered, reverse=True)
    assert avg["legendary"] <= avg["common"]


def test_legendaries_can_be_forced_to_one_of_one():
    """With a small print run the seeder short-prints legendaries down to 1-of-1;
    build_manifest floors every design at >= 1 copy, so a low total_cards yields exactly 1."""
    m = cards.build_manifest(_subjects(200), total_cards=250)
    legs = [d for d in m if d["rarity"] == "legendary"]
    assert legs
    assert all(d["copies"] == 1 for d in legs)  # 1-of-1 legendaries
    assert all(d["copies"] >= 1 for d in m)  # nothing is unmintable


# --- pack open ---------------------------------------------------------------


def test_open_pack_size_and_depletes_pool():
    m = cards.build_manifest(_subjects(200), 5000)
    pool = {i: d["copies"] for i, d in enumerate(m)}
    before = sum(pool.values())
    out = cards.open_pack(pool, m, random.Random(1), pack_size=5)
    assert len(out) == 5
    assert sum(pool.values()) == before - 5
    for c in out:
        assert c["book_value"] >= cards.BOOK[m[c["design_index"]]["rarity"]]


def test_open_pack_never_exceeds_finite_pool():
    """A pack can never draw more cards than the whole finite pool holds."""
    m = cards.build_manifest(_subjects(50), 60)  # tiny pool
    pool = {i: d["copies"] for i, d in enumerate(m)}
    supply = sum(pool.values())
    out = cards.open_pack(pool, m, random.Random(2), pack_size=supply + 100)
    assert len(out) == supply  # capped at what exists
    assert all(c >= 0 for c in pool.values())
    assert sum(pool.values()) == 0  # fully drained


# --- expected value ----------------------------------------------------------


def test_expected_pack_value_positive_and_sane():
    m = cards.build_manifest(_subjects(200), 5000)
    ev = cards.expected_pack_value(m, pack_size=5)
    assert ev > 0
    assert 30 < ev < 70  # ballpark of the RTP target


# --- notable pulls -----------------------------------------------------------


def test_is_notable_pull_true_cases():
    assert cards.is_notable_pull({"rarity": "legendary", "is_holo": False, "gem": None})
    assert cards.is_notable_pull({"rarity": "epic", "is_holo": False, "gem": None})
    assert cards.is_notable_pull({"rarity": "common", "is_holo": False, "gem": "sapphire"})
    assert cards.is_notable_pull({"rarity": "common", "is_holo": False, "gem": "black_lotus"})
    assert cards.is_notable_pull({"rarity": "rare", "is_holo": True, "gem": None})


def test_is_notable_pull_false_for_plain_common():
    assert not cards.is_notable_pull({"rarity": "common", "is_holo": False, "gem": None})
    assert not cards.is_notable_pull({"rarity": "common", "is_holo": True, "gem": None})
    assert not cards.is_notable_pull({"rarity": "rare", "is_holo": False, "gem": None})
    assert not cards.is_notable_pull({"rarity": "uncommon", "is_holo": False, "gem": "chrome"})


def test_describe_pull_includes_season_and_tags():
    tag = cards.describe_pull(
        {"total_copies": 1, "gem": None, "is_holo": True, "rarity": "legendary",
         "name": "LeBron James", "season": 2003}
    )
    assert "1-of-1" in tag and "Holo" in tag and "Legendary" in tag
    assert "LeBron James" in tag and "(2003)" in tag


# --- vintage pricing (SharpLab addition) -------------------------------------


def test_pack_cost_current_year_equals_base():
    assert cards.pack_cost(2026, 2026) == cards.BASE_PACK_COST
    assert cards.pack_cost(2030, 2026) == cards.BASE_PACK_COST  # future clamps to base


def test_pack_cost_monotonic_increasing_with_age():
    current = 2026
    costs = [cards.pack_cost(y, current) for y in range(current, current - 30, -1)]
    for a, b in zip(costs, costs[1:]):
        assert b >= a  # older season -> not cheaper
    assert costs[-1] > costs[0]  # 29y older strictly pricier than current
    # spec sanity: ~108 at 10y, ~235 at 20y
    assert cards.pack_cost(current - 10, current) == round(50 * 1.08 ** 10)
    assert cards.pack_cost(current - 20, current) == round(50 * 1.08 ** 20)


if __name__ == "__main__":
    # Fallback self-check when pytest is unavailable.
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\nALL {len(fns)} CHECKS PASSED")
