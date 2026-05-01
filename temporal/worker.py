import asyncio
import logging

from temporalio.client import Client
from temporalio.worker import Worker

from db.schema import init_db
from shared.log_config import setup_logging
from .workflows import OddsPollingWorkflow, CloseCaptureWorkflow, InjuryPollingWorkflow, BetResolutionWorkflow
from .activities import (
    fetch_games_for_today,
    fetch_odds_batch,
    fetch_injuries,
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

    client = await Client.connect("localhost:7233")

    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[OddsPollingWorkflow, CloseCaptureWorkflow, InjuryPollingWorkflow, BetResolutionWorkflow],
        activities=[
            fetch_games_for_today,
            fetch_odds_batch,
            fetch_injuries,
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

    log.info(f"Worker started on task queue: {TASK_QUEUE}")
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
