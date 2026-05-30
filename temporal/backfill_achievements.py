"""One-shot: backfill achievements for everyone. Evaluation is idempotent — if a
user already meets a (possibly newly-added) achievement's condition, it unlocks
and awards the XP; already-held ones are skipped. Safe to re-run.

    python -m temporal.backfill_achievements
"""
from __future__ import annotations

import asyncio

from db import schema, queries
from bot.cogs.progression import evaluate_user_achievements, ACHIEVEMENTS_BY_ID


async def main() -> None:
    await schema.init_db()
    users = await queries.get_all_achievement_users()
    print(f"=== Achievement backfill — {len(users)} user(s) ===")
    total = 0
    for uid in users:
        try:
            newly = await evaluate_user_achievements(uid)
        except Exception as e:
            print(f"  {uid}: FAILED {e!r}")
            continue
        if newly:
            total += len(newly)
            names = ", ".join(ACHIEVEMENTS_BY_ID[a].name for a in newly)
            print(f"  {uid}: +{len(newly)} → {names}")
    print(f"=== done: {total} achievement(s) unlocked across {len(users)} user(s) ===")


if __name__ == "__main__":
    asyncio.run(main())
