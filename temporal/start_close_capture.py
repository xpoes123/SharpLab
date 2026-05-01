import asyncio
import logging
from temporalio.client import Client
from datetime import datetime, timezone, timedelta

TASK_QUEUE = "sports-quant-lab"
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
log = logging.getLogger(__name__)

async def main() -> None:
    client = await Client.connect("localhost:7233")

    game_id = "GAME123"
    start_time = (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()

    handle = await client.start_workflow(
        "CloseCaptureWorkflow",
        (game_id, start_time, "nba"),
        id=f"close-capture-{game_id}-{int(datetime.now(timezone.utc).timestamp())}",
        task_queue=TASK_QUEUE,
    )

    log.info('Started CloseCaptureWorkflow: %s', handle.id)
    await handle.result()
    log.info('Close capture completed')


if __name__ == "__main__":
    asyncio.run(main())
