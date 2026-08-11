"""Seed sports-card sets (one per sport-season) from ESPN data.

For each (sport, season): fetch players (scripts/card_sources.py), rank by
career fame with a rookie premium, build the print-run manifest (shared/cards.py),
collapse legendaries to 1-of-1, and insert a card_set + its card_designs.

Idempotent: a (sport, season) that already exists is skipped. Tolerant: a season
that returns too few players is skipped with a log line, never aborts the run.

Run on the VPS (needs network):
    cd /opt/sharplab && venv/bin/python scripts/seed_cards.py            # all configured
    cd /opt/sharplab && venv/bin/python scripts/seed_cards.py nba 2024   # one set
"""

import asyncio
import logging
import os
import sys
from datetime import datetime, timezone

# Make this runnable as `python scripts/seed_cards.py` from anywhere: put the
# script's own dir on the path (for `card_sources`) and the repo root (for db/shared).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import card_sources  # noqa: E402
from db import queries  # noqa: E402
from db.schema import init_db  # noqa: E402
from shared import cards as engine  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("seed_cards")

TOTAL_PACKS = 500          # print run per set => TOTAL_PACKS * 5 cards
PACK_SIZE = 5
MIN_PLAYERS = 20           # skip a season that returns fewer than this (data too thin)

# Newest (== "current") season per sport; older seasons cost more via pack_cost().
CURRENT = {"nba": 2025, "nfl": 2025, "mlb": 2025}
# ~15 recent seasons per sport (adjust freely; historical years are best-effort).
SEASONS = {
    "nba": list(range(2011, 2026)),
    "nfl": list(range(2011, 2026)),
    "mlb": list(range(2011, 2026)),
}
FETCHERS = {
    "nba": card_sources.fetch_nba_season,
    "nfl": card_sources.fetch_nfl_season,
    "mlb": card_sources.fetch_mlb_season,
}


def _set_name(sport: str, season: int) -> str:
    if sport == "nba":
        return f"NBA {season}-{str(season + 1)[-2:]}"
    return f"{sport.upper()} {season}"


def _build_designs(subjects: list[dict]) -> list[dict]:
    """subjects -> manifest designs with rarity/copies/book_value, legendaries 1-of-1."""
    for s in subjects:
        fame = s.get("career_fame") or 0.0
        s["stardom"] = fame * (engine.ROOKIE_MULT if s.get("is_rookie") else 1.0)
    total_cards = TOTAL_PACKS * PACK_SIZE
    manifest = engine.build_manifest(subjects, total_cards)
    # Legendaries are 1-of-1 grails; freed copies flow to commons so the run still totals total_cards.
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
    # shape into the insert dict the query layer wants
    out = []
    for d in manifest:
        out.append({
            "subject_key": d["subject_key"],
            "subject_name": d["name"],
            "team": d.get("team"),
            "rarity": d["rarity"],
            "is_rookie": d.get("is_rookie", False),
            "career_fame": d.get("career_fame"),
            "total_copies": d["copies"],
            "stats": d.get("stats") or {},
            "headshot_url": d.get("headshot_url"),
            "book_value": d["book_value"],
        })
    return out


async def seed_one(sport: str, season: int) -> bool:
    if await queries.card_set_exists(sport, season):
        log.info("  %s %s already seeded — skipping", sport.upper(), season)
        return False
    try:
        subjects = await FETCHERS[sport](season)
    except Exception:
        log.exception("  fetch failed for %s %s", sport, season)
        return False
    # de-dup by subject_key (a player can appear once per set)
    seen, uniq = set(), []
    for s in subjects:
        k = s.get("subject_key")
        if k and k not in seen:
            seen.add(k)
            uniq.append(s)
    if len(uniq) < MIN_PLAYERS:
        log.info("  %s %s: only %d players — skipping", sport.upper(), season, len(uniq))
        return False
    designs = _build_designs(uniq)
    base_cost = engine.pack_cost(season, CURRENT.get(sport, season))
    now = datetime.now(timezone.utc).isoformat()
    set_id = await queries.create_card_set(
        sport, season, _set_name(sport, season), TOTAL_PACKS, base_cost, now
    )
    n = await queries.insert_card_designs(set_id, designs)
    ev = engine.expected_pack_value([{**d, "copies": d["total_copies"]} for d in designs], PACK_SIZE)
    log.info(
        "  ✅ %s %s: %d designs, %d cards, pack=%d🪙, E[pack]=%.0f",
        sport.upper(), season, n, sum(d["total_copies"] for d in designs), base_cost, ev,
    )
    return True


async def main() -> None:
    await init_db()
    args = sys.argv[1:]
    if len(args) == 2:
        plan = [(args[0], int(args[1]))]
    else:
        plan = [(sp, yr) for sp in SEASONS for yr in SEASONS[sp]]
    seeded = 0
    for sport, season in plan:
        if await seed_one(sport, season):
            seeded += 1
    log.info("Done. Seeded %d new set(s).", seeded)


if __name__ == "__main__":
    asyncio.run(main())
