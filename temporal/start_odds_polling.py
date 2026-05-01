import asyncio
import logging
import sys
from temporalio.client import Client

TASK_QUEUE = "sports-quant-lab"
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
log = logging.getLogger(__name__)

async def main() -> None:
    sport = sys.argv[1] if len(sys.argv) > 1 else "nba"
    client = await Client.connect("localhost:7233")

    handle = await client.start_workflow(
        "OddsPollingWorkflow",
        args=[30, sport],
        id=f"odds-polling-{sport}-v2",
        task_queue=TASK_QUEUE,
    )

    log.info('Started OddsPollingWorkflow (%s): %s', sport, handle.id)

if __name__ == "__main__":
    asyncio.run(main())
