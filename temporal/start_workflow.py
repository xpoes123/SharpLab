import asyncio
from temporalio.client import Client
from temporalio.client import WorkflowHandle

TASK_QUEUE = "sports-quant-lab"

async def main():
    client = await Client.connect("localhost:7233")
    
    handle: WorkflowHandle = await client.start_workflow(
        "DemoWorkflow",
        id="demo-workflow-1",
        task_queue=TASK_QUEUE,
    )
    
    print("Started workflow:", handle.id)
    result = await handle.result()
    print("Result:", result)

if __name__ == "__main__":
    asyncio.run(main())