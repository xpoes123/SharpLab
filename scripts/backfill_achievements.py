"""One-time: silently unlock every earned-but-missing achievement for all known
users (and grant the matching XP), so we don't dump backlogs in-channel the next
time someone logs a bet/trade. evaluate_user_achievements does not announce, so
this is silent by construction. Idempotent — safe to re-run."""
import asyncio

import aiosqlite

from db import schema
from db.schema import DB_PATH
from bot.cogs.progression import evaluate_user_achievements


async def main() -> None:
    await schema.init_db()
    ids: set[str] = set()
    async with aiosqlite.connect(DB_PATH) as db:
        for table in ("casino_history", "bets", "stock_trades", "option_trades", "user_xp", "achievements"):
            try:
                async with db.execute(f"SELECT DISTINCT discord_user FROM {table}") as cur:
                    ids.update(str(r[0]) for r in await cur.fetchall() if r[0] is not None)
            except Exception:
                pass
    total_new = 0
    for uid in sorted(ids):
        newly = await evaluate_user_achievements(uid)
        if newly:
            total_new += len(newly)
            print(f"  {uid}: +{len(newly)} ({', '.join(newly)})")
    print(f"Backfilled {total_new} achievement unlock(s) across {len(ids)} user(s), silently.")


if __name__ == "__main__":
    asyncio.run(main())
