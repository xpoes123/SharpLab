import asyncio
import logging
from temporalio.client import Client

from shared.log_config import setup_logging

setup_logging()
log = logging.getLogger(__name__)

TASK_QUEUE = "sports-quant-lab"


async def main() -> None:
    client = await Client.connect("localhost:7233")

    handle = await client.start_workflow(
        "InjuryPollingWorkflow",
        5,  # 5-minute polling interval
        id="injury-polling-v1",
        task_queue=TASK_QUEUE,
    )

    log.info(f"Started InjuryPollingWorkflow: {handle.id}")


if __name__ == "__main__":
    asyncio.run(main())
