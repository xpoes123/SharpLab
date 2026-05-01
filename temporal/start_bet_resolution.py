import asyncio
import logging
import sys
from temporalio.client import Client

from shared.log_config import setup_logging

setup_logging()
log = logging.getLogger(__name__)

TASK_QUEUE = "sports-quant-lab"


async def main() -> None:
    sport = sys.argv[1] if len(sys.argv) > 1 else "nba"
    client = await Client.connect("localhost:7233")

    handle = await client.start_workflow(
        "BetResolutionWorkflow",
        args=[2, sport],
        id=f"bet-resolution-{sport}-v2",
        task_queue=TASK_QUEUE,
    )

    log.info(f"Started BetResolutionWorkflow ({sport}): {handle.id}")


if __name__ == "__main__":
    asyncio.run(main())
