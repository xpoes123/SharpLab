import asyncio
from temporalio.client import Client
from temporalio.worker import Worker
import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

from .workflows import OddsPollingWorkflow, CloseCaptureWorkflow
from .activities import (
    fetch_games_for_today,
    fetch_close_odds_snapshot,
    fetch_odds_snapshot,
    persist_odds_snapshot,
    upsert_odds_snapshot
)

TASK_QUEUE = "sports-quant-lab"

async def main() -> None:
    client = await Client.connect("localhost:7233")
    
    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[OddsPollingWorkflow, CloseCaptureWorkflow],
        activities=[fetch_games_for_today, upsert_odds_snapshot, fetch_odds_snapshot, persist_odds_snapshot, fetch_close_odds_snapshot],
    )
    
    print(f"Worker started on task queue: {TASK_QUEUE}")
    await worker.run()

if __name__ == "__main__":
    asyncio.run(main())