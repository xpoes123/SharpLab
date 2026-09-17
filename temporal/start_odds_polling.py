import asyncio
import logging
import sys
from datetime import datetime, timezone

from temporalio.client import Client

from shared.log_config import setup_logging

setup_logging()
log = logging.getLogger(__name__)

TASK_QUEUE = "sports-quant-lab"


async def dry_run(sport: str) -> None:
    """Run one refresh cycle — fetch/parse/validate everything, write nothing.

    Calls the activity functions directly (no Temporal server needed) with
    dry_run=True on the writers, so quota is still spent on the real API calls
    but the DB is never touched. Logs every snapshot that would be upserted.
    Player props/alts are skipped (workflow-patch-gated, separate write path).
    """
    from temporal import activities as act

    now = datetime.now(timezone.utc)
    games = await act.fetch_games_for_today(sport, dry_run=True)

    upcoming = []
    for g in games:
        start = datetime.fromisoformat(g.start_time_utc_iso.replace("Z", "+00:00"))
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if start > now:
            upcoming.append(g)
    ids = [g.game_id for g in upcoming]
    log.info(f"[dry-run] {len(games)} games, {len(upcoming)} upcoming to poll")

    batches = [
        await act.fetch_odds_batch(ids, sport),
        await act.fetch_kalshi_odds_batch(upcoming, sport),
        await act.fetch_polymarket_odds_batch(upcoming),
    ]
    total = 0
    for batch in batches:
        for snap in batch.snapshots:
            await act.upsert_odds_snapshot(snap, dry_run=True)
            total += 1
    log.info(f"[dry-run] done — {total} snapshots would have been written, 0 DB writes made")


async def main() -> None:
    argv = [a for a in sys.argv[1:] if a != "--dry-run"]
    sport = argv[0] if argv else "nba"

    if "--dry-run" in sys.argv:
        await dry_run(sport)
        return

    client = await Client.connect("localhost:7233")
    handle = await client.start_workflow(
        "OddsPollingWorkflow",
        args=[30, sport],
        id=f"odds-polling-{sport}-v2",
        task_queue=TASK_QUEUE,
    )
    log.info(f"Started OddsPollingWorkflow ({sport}): {handle.id}")


if __name__ == "__main__":
    asyncio.run(main())
