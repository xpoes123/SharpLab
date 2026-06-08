import asyncio
import logging

from temporalio.client import Client
from temporalio.worker import Worker

from db.schema import init_db
from shared.log_config import setup_logging
from .bootstrap import ensure_workflows
from .workflows import OddsPollingWorkflow, CloseCaptureWorkflow, InjuryPollingWorkflow, BetResolutionWorkflow
from .activities import (
    fetch_games_for_today,
    fetch_odds_batch,
    fetch_nba_player_props,
    fetch_nba_player_prop_alts,
    games_with_open_prop_bets,
    fetch_injuries,
    record_injury_changes,
    upsert_odds_snapshot,
    fetch_close_odds_snapshot,
    fetch_kalshi_odds_batch,
    fetch_kalshi_close_snapshot,
    fetch_polymarket_odds_batch,
    fetch_polymarket_close_snapshot,
    fetch_final_scores,
    resolve_bets_for_game,
)

setup_logging()

log = logging.getLogger(__name__)

TASK_QUEUE = "sports-quant-lab"


async def main() -> None:
    await init_db()

    for attempt in range(30):
        try:
            client = await Client.connect("localhost:7233")
            break
        except Exception as e:
            if attempt == 29:
                raise
            log.warning(f"Temporal not ready (attempt {attempt + 1}/30), retrying in 10s: {e}")
            await asyncio.sleep(10)

    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[OddsPollingWorkflow, CloseCaptureWorkflow, InjuryPollingWorkflow, BetResolutionWorkflow],
        activities=[
            fetch_games_for_today,
            fetch_odds_batch,
            fetch_nba_player_props,
            fetch_nba_player_prop_alts,
            games_with_open_prop_bets,
            fetch_injuries,
            record_injury_changes,
            upsert_odds_snapshot,
            fetch_close_odds_snapshot,
            fetch_kalshi_odds_batch,
            fetch_kalshi_close_snapshot,
            fetch_polymarket_odds_batch,
            fetch_polymarket_close_snapshot,
            fetch_final_scores,
            resolve_bets_for_game,
        ],
    )

    # Ensure the singleton long-running workflows exist. Idempotent: already-running
    # ones are left alone. Runs on every worker start, so a deploy (which restarts
    # temporal) or a terminated workflow self-heals without manual re-kicking.
    await ensure_workflows(client, TASK_QUEUE)

    log.info(f"Worker started on task queue: {TASK_QUEUE}")
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
