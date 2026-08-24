"""Seed Pokémon card sets from the vendored data/pokemon_cards.json (real names,
rarities, market prices). Standard sets + a premium 151 '1st Edition' grail box.

Idempotent. Run on the VPS:
    cd /opt/sharplab && venv/bin/python scripts/seed_pokemon.py
"""
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import queries  # noqa: E402
from db.schema import init_db  # noqa: E402
from shared import cards as engine  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("seed_pokemon")

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "data", "pokemon_cards.json")

# (sport, season) -> config. src_id matches data/pokemon_cards.json set ids.
SETS: dict[tuple[str, int], dict] = {
    ("pokemon", 2023): {"src_id": "sv03.5", "name": "Pokémon 151", "box_price": 15_000, "boxes": 30, "premium": False},
    ("pokemon", 1999): {"src_id": "sv03.5", "name": "Pokémon 151 — 1st Edition", "box_price": 300_000, "boxes": 5, "premium": True},
    ("pokemon", 2025): {"src_id": "sv08.5", "name": "Prismatic Evolutions", "box_price": 20_000, "boxes": 25, "premium": False},
    ("pokemon", 2024): {"src_id": "sv08", "name": "Surging Sparks", "box_price": 15_000, "boxes": 25, "premium": False},
}

PACK_SIZE = 5


def _load_cards(src_id: str) -> list[dict]:
    d = json.load(open(DATA))
    for s in d["sets"]:
        if s["id"] == src_id:
            return s["cards"]
    raise ValueError(f"set {src_id} not in {DATA}")


def build_pokemon_designs(cards: list[dict]) -> list[dict]:
    """Real-card dicts {name, rarity, price_usd} -> designs. Rarity mapped directly,
    book_value from price, copies from COPIES_REL by tier, legendaries 1-of-1."""
    out = []
    for c in cards:
        tier = engine.map_pokemon_rarity(c["rarity"])
        copies = 1 if tier == "legendary" else engine.COPIES_REL[tier]
        out.append({
            "subject_key": c["name"].lower().replace(" ", "_"),
            "subject_name": c["name"], "team": None, "rarity": tier, "is_rookie": False,
            "career_fame": c.get("price_usd", 0.0), "total_copies": copies,
            "stats": {}, "headshot_url": None,
            "book_value": engine.pokemon_book_value(c.get("price_usd", 0.0), tier),
        })
    return out


async def seed_one(sport: str, season: int, cfg: dict) -> bool:
    if await queries.card_set_exists(sport, season):
        log.info("  %s %s already seeded — skipping", sport.upper(), season)
        return False
    designs = build_pokemon_designs(_load_cards(cfg["src_id"]))
    base_cost = round(cfg["box_price"] / engine.PACKS_PER_BOX)
    # Cap the print run to the real card pool — unlike sports sets, the Pokémon pool is
    # fixed (copies come from COPIES_REL per card, not scaled to boxes*36). Selling more
    # packs than the pool supports would short later buyers at full price.
    pool_packs = sum(d["total_copies"] for d in designs) // PACK_SIZE
    total_packs = min(cfg["boxes"] * engine.PACKS_PER_BOX, pool_packs)
    if total_packs < cfg["boxes"] * engine.PACKS_PER_BOX:
        log.info("  %s: pool caps print run at %d packs (%d boxes requested)",
                 cfg["name"], total_packs, cfg["boxes"])
    now = datetime.now(timezone.utc).isoformat()
    set_id = await queries.create_card_set(sport, season, cfg["name"], total_packs, base_cost, now)
    await queries.insert_card_designs(set_id, designs)
    log.info("  seeded %s (%d designs, base_cost=%d, box=%d)",
             cfg["name"], len(designs), base_cost, cfg["box_price"])
    return True


async def main() -> None:
    await init_db()
    for (sport, season), cfg in SETS.items():
        await seed_one(sport, season, cfg)


if __name__ == "__main__":
    asyncio.run(main())
