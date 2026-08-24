"""One-time (idempotent) pack rebalance:
- Pokémon sets: bump stock (non-legendary copies × STOCK_FACTOR, legendaries stay 1-of-1
  grails), attach card ART (headshot_url → pokemon.djiang.xyz/<img>), and REOPEN the sets
  (resize total_packs to the new pool, clear `closed`) — they'd sold out.
- Premium/expensive sets (Pokémon 151 1st-Edition + NBA 1984/1979): scale design book_value
  × PREMIUM_MULT so pricier boxes yield proportionally more valuable cards.

Idempotent: every value is RECOMPUTED from source (the vendored data / engine BOOK / COPIES_REL),
never multiplied in place — so re-running lands on the same numbers. Only DESIGNS change (future
mints); already-owned instances keep their minted book_value/serial.

Run on the VPS:
    cd /opt/sharplab && venv/bin/python scripts/rebalance_packs.py
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiosqlite  # noqa: E402
from db.schema import init_db, DB_PATH  # noqa: E402
from shared import cards as engine  # noqa: E402
from scripts.seed_pokemon import SETS as POKEMON_SETS  # noqa: E402

STOCK_FACTOR = 8      # non-legendary Pokémon copies ×8 (effectively won't sell out)
PREMIUM_MULT = 3      # expensive-box cards worth 3× (box stays a strong sink)
PACK_SIZE = 5
IMG_BASE = "https://pokemon.djiang.xyz/"
DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "data", "pokemon_cards.json")
PREMIUM_SPORTS = {("nba", 1984), ("nba", 1979)}  # expensive draft boxes → 3× card value


def _pokemon_cards_by_srcid() -> dict:
    d = json.load(open(DATA))
    return {s["id"]: s["cards"] for s in d["sets"]}


async def rebalance() -> None:
    await init_db()
    by_src = _pokemon_cards_by_srcid()

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # ── Pokémon: stock + art + (premium) value, then reopen ──
        for (sport, season), cfg in POKEMON_SETS.items():
            srow = await (await db.execute(
                "SELECT set_id FROM card_sets WHERE sport=? AND season=?", (sport, season))).fetchone()
            if srow is None:
                print(f"  {cfg['name']}: not seeded — skipping")
                continue
            set_id = srow["set_id"]
            cards = by_src[cfg["src_id"]]
            premium = cfg.get("premium", False)
            designs = await (await db.execute(
                "SELECT design_id, subject_key, rarity FROM card_designs WHERE set_id=?", (set_id,))).fetchall()
            pool = 0
            for d in designs:
                tier = d["rarity"]
                i = int(d["subject_key"][:3])          # subject_key = "{i:03d}_{slug}"
                src = cards[i]
                copies = 1 if tier == "legendary" else engine.COPIES_REL[tier] * STOCK_FACTOR
                book = engine.pokemon_book_value(src.get("price_usd", 0.0), tier)
                if premium:
                    book *= PREMIUM_MULT
                url = (IMG_BASE + src["img"]) if src.get("img") else None
                await db.execute(
                    "UPDATE card_designs SET total_copies=?, book_value=?, headshot_url=? WHERE design_id=?",
                    (copies, book, url, d["design_id"]))
                pool += copies
            total_packs = pool // PACK_SIZE
            await db.execute(
                "UPDATE card_sets SET total_packs=?, closed=0 WHERE set_id=?", (total_packs, set_id))
            print(f"  {cfg['name']}: {len(designs)} designs, pool→{total_packs} packs, "
                  f"art+{'3x value' if premium else 'stock'} ✓")

        # ── Premium sports draft sets: scale card value ×3 (keep scarce stock) ──
        for (sport, season) in PREMIUM_SPORTS:
            srow = await (await db.execute(
                "SELECT set_id, name FROM card_sets WHERE sport=? AND season=?", (sport, season))).fetchone()
            if srow is None:
                continue
            designs = await (await db.execute(
                "SELECT design_id, rarity FROM card_designs WHERE set_id=?", (srow["set_id"],))).fetchall()
            for d in designs:
                book = engine.BOOK[d["rarity"]] * PREMIUM_MULT
                await db.execute("UPDATE card_designs SET book_value=? WHERE design_id=?",
                                 (book, d["design_id"]))
            print(f"  {srow['name']}: {len(designs)} designs → 3× value ✓")

        await db.commit()


if __name__ == "__main__":
    asyncio.run(rebalance())
