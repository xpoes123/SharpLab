import asyncio
from temporalio.client import Client

TASK_QUEUE = "sports-quant-lab"


async def main() -> None:
    client = await Client.connect("localhost:7233")

    handle = await client.start_workflow(
        "InjuryPollingWorkflow",
        5,  # 5-minute polling interval
        id="injury-polling-v1",
        task_queue=TASK_QUEUE,
    )

    print("Started InjuryPollingWorkflow:", handle.id)


if __name__ == "__main__":
    asyncio.run(main())
