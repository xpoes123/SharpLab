"""Curated draft-class seeding: manifest shape + premium skew."""
import importlib

seed = importlib.import_module("scripts.seed_draft_classes")
rosters = importlib.import_module("scripts.draft_rosters")
from shared import cards as engine


def test_rosters_present_and_priced():
    for key in [("nba", 2003), ("nba", 1979), ("nba", 1984)]:
        r = rosters.ROSTERS[key]
        assert r["players"] and r["name"] and r["box_price"] > 0 and r["boxes"] > 0
    # Jordan class is the priciest
    assert rosters.ROSTERS[("nba", 1984)]["box_price"] == 250_000


def test_build_designs_shape_and_premium():
    players = rosters.ROSTERS[("nba", 1984)]["players"]
    designs = seed.build_designs(players)
    assert len(designs) == len(players)
    keys = {"subject_key", "subject_name", "rarity", "is_rookie", "total_copies", "book_value"}
    assert keys <= set(designs[0])
    assert all(d["is_rookie"] for d in designs)
    # premium skew yields at least one legendary in a ~15-player elite set
    assert any(d["rarity"] == "legendary" for d in designs)
    # legendaries are 1-of-1 grails
    assert all(d["total_copies"] == 1 for d in designs if d["rarity"] == "legendary")
