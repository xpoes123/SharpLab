"""One-shot: seed portfolio_snapshots for existing users so /stock graph and the
leaderboard's weekly/YTD views have data immediately instead of waiting for the
hourly loop to accumulate it. Idempotent — safe to re-run.

For each portfolio user:
  - reconstruct the daily stock-value curve from their trade log (once), and
  - record one live account-value snapshot for "now".

    python -m temporal.backfill_portfolios
"""
from __future__ import annotations

import asyncio

from db import schema, queries
from bot.cogs import stock


async def main() -> None:
    await schema.init_db()
    users = await queries.get_all_portfolio_users()
    print(f"=== Portfolio snapshot backfill — {len(users)} user(s) ===")
    backfilled = live = 0
    for uid in users:
        trades = await queries.get_stock_trades(uid)
        if trades and not await queries.has_backfill_snapshots(uid):
            try:
                await stock._ensure_backfill(uid, trades)
                backfilled += 1
                print(f"  backfilled history for {uid}")
            except Exception as e:
                print(f"  backfill FAILED for {uid}: {e!r}")
        try:
            await stock._take_live_snapshot(uid)
            live += 1
        except Exception as e:
            print(f"  live snapshot FAILED for {uid}: {e!r}")

    print(f"=== done: {backfilled} backfilled, {live} live snapshots taken ===")


if __name__ == "__main__":
    asyncio.run(main())
