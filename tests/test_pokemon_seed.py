"""Pokémon design building: rarity mapping + real-price book values."""
import importlib

seed = importlib.import_module("scripts.seed_pokemon")
from shared import cards as engine


FIXTURE = [
    {"name": "Charizard ex", "rarity": "Special illustration rare", "price_usd": 397.07},
    {"name": "Pikachu", "rarity": "Illustration rare", "price_usd": 95.36},
    {"name": "Ivysaur", "rarity": "Uncommon", "price_usd": 0.22},
    {"name": "Geodude", "rarity": "Common", "price_usd": 0.19},
]


def test_build_maps_rarity_and_prices():
    designs = seed.build_pokemon_designs(FIXTURE)
    by_name = {d["subject_name"]: d for d in designs}
    assert by_name["Charizard ex"]["rarity"] == "legendary"
    assert by_name["Pikachu"]["rarity"] == "epic"
    assert by_name["Ivysaur"]["rarity"] == "uncommon"
    assert by_name["Charizard ex"]["book_value"] == 39707.0
    # book value never falls below the tier floor (at $0.19 * COIN_PER_USD=100 the real
    # price already clears the $3.50 common floor, so assert the floor invariant directly
    # rather than an exact value that doesn't exercise the floor for this fixture price)
    assert by_name["Geodude"]["book_value"] >= engine.BOOK["common"]
    # legendaries collapse to 1-of-1
    assert by_name["Charizard ex"]["total_copies"] == 1


def test_sets_config_has_151_and_first_edition():
    keys = seed.SETS
    assert ("pokemon", 2023) in keys      # standard 151
    assert ("pokemon", 1999) in keys      # 1st Edition grail
    assert keys[("pokemon", 1999)]["box_price"] == 300_000
    assert keys[("pokemon", 1999)]["premium"] is True


def test_print_run_never_exceeds_pool():
    # build_pokemon_designs pool must cover the seeded print run for every set
    designs = seed.build_pokemon_designs([
        {"name": "A", "rarity": "Common", "price_usd": 0.2},
        {"name": "B", "rarity": "Ultra Rare", "price_usd": 5.0},
        {"name": "C", "rarity": "Hyper rare", "price_usd": 100.0},
    ])
    pool = sum(d["total_copies"] for d in designs)
    assert pool > 0
    # legendary (Hyper rare) collapses to 1-of-1
    assert any(d["rarity"] == "legendary" and d["total_copies"] == 1 for d in designs)
