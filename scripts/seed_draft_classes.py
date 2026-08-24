"""Seed premium NBA draft-class card sets from curated rosters (scripts/draft_rosters.py).

Idempotent: a (sport, season) that already exists is skipped. Run on the VPS:
    cd /opt/sharplab && venv/bin/python scripts/seed_draft_classes.py
"""
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import queries  # noqa: E402
from db.schema import init_db  # noqa: E402
from shared import cards as engine  # noqa: E402
from scripts.draft_rosters import ROSTERS  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("seed_draft")

PACK_SIZE = 5


def build_designs(players: list[tuple[str, float]], total_packs: int) -> list[dict]:
    """Curated (name, fame) -> manifest designs with premium skew. The copy pool is
    sized to the full print run (total_packs * PACK_SIZE); legendaries are 1-of-1
    grails and the copies they free redistribute into commons so the pool still
    totals the print run (same approach as scripts/seed_cards.py)."""
    total_cards = total_packs * PACK_SIZE
    subjects = [{
        "subject_key": name.lower().replace(" ", "_").replace(".", ""),
        "name": name, "stardom": fame, "is_rookie": True, "career_fame": fame,
    } for name, fame in players]
    manifest = engine.build_manifest(subjects, total_cards, engine.DRAFT_TIERS)
    commons = [d for d in manifest if d["rarity"] == "common"] or manifest
    freed = 0
    for d in manifest:
        if d["rarity"] == "legendary" and d["copies"] > 1:
            freed += d["copies"] - 1
            d["copies"] = 1
    i = 0
    while freed > 0 and commons:
        commons[i % len(commons)]["copies"] += 1
        freed -= 1
        i += 1
    out = []
    for d in manifest:
        out.append({
            "subject_key": d["subject_key"], "subject_name": d["name"], "team": None,
            "rarity": d["rarity"], "is_rookie": True, "career_fame": d.get("career_fame"),
            "total_copies": d["copies"], "stats": {}, "headshot_url": None,
            "book_value": d["book_value"],
        })
    return out


async def seed_one(sport: str, season: int, cfg: dict) -> bool:
    if await queries.card_set_exists(sport, season):
        log.info("  %s %s already seeded — skipping", sport.upper(), season)
        return False
    total_packs = cfg["boxes"] * engine.PACKS_PER_BOX
    designs = build_designs(cfg["players"], total_packs)
    base_cost = round(cfg["box_price"] / engine.PACKS_PER_BOX)
    now = datetime.now(timezone.utc).isoformat()
    set_id = await queries.create_card_set(sport, season, cfg["name"], total_packs, base_cost, now)
    await queries.insert_card_designs(set_id, designs)
    log.info("  seeded %s (%d designs, base_cost=%d, box=%d, packs=%d)",
             cfg["name"], len(designs), base_cost, cfg["box_price"], total_packs)
    return True


async def main() -> None:
    await init_db()
    for (sport, season), cfg in ROSTERS.items():
        await seed_one(sport, season, cfg)


if __name__ == "__main__":
    asyncio.run(main())
