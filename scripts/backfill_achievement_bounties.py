"""One-time: top up already-unlocked achievements from the old flat 150 to the new
XP*5 bounty. Credits max(0, xp*5 - 150) per (user, achievement), guarded by the
bounty_backfilled table so re-runs pay nothing. Run on the VPS:
    cd /opt/sharplab && venv/bin/python scripts/backfill_achievement_bounties.py
"""
import asyncio
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiosqlite  # noqa: E402
from db import queries  # noqa: E402
from db.schema import init_db, DB_PATH  # noqa: E402
from shared.achievements import ACHIEVEMENTS_BY_ID  # noqa: E402


async def backfill() -> int:
    """Returns the number of (user, achievement) top-ups paid this run."""
    await init_db()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT ua.discord_user, ua.achievement_id FROM user_achievements ua "
            "LEFT JOIN bounty_backfilled bb "
            "  ON bb.discord_user = ua.discord_user AND bb.achievement_id = ua.achievement_id "
            "WHERE bb.discord_user IS NULL")).fetchall()
    paid = 0
    for r in rows:
        aid = r["achievement_id"]
        ach = ACHIEVEMENTS_BY_ID.get(aid)
        if ach is None:
            continue
        delta = max(0, ach.xp_reward * 5 - 150)
        if delta > 0:
            await queries.credit_coins(
                r["discord_user"], delta, f"Achievement bounty top-up: {ach.name}",
                datetime.now(timezone.utc).isoformat())
            paid += 1
        # mark handled either way so we never revisit
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT OR IGNORE INTO bounty_backfilled (discord_user, achievement_id) VALUES (?, ?)",
                (r["discord_user"], aid))
            await db.commit()
    return paid


async def main() -> None:
    n = await backfill()
    print(f"backfilled {n} achievement bounty top-up(s)")


if __name__ == "__main__":
    asyncio.run(main())
