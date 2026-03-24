import asyncio
from temporalio.client import Client

TASK_QUEUE = "sports-quant-lab"


async def main() -> None:
    client = await Client.connect("localhost:7233")

    handle = await client.start_workflow(
        "BetResolutionWorkflow",
        2,  # interval_hours
        id="bet-resolution-v1",
        task_queue=TASK_QUEUE,
    )

    print("Started BetResolutionWorkflow:", handle.id)


if __name__ == "__main__":
    asyncio.run(main())
