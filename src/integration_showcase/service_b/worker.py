"""Temporal worker for Service B -- registers inventory activities.

Run with::

    python -m integration_showcase.service_b.worker

The inventory activities are sync ``def`` (they do blocking I/O), so the
worker needs an ``activity_executor`` ``ThreadPoolExecutor`` to run them
off the Temporal event loop.
"""

from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor

from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Worker

from integration_showcase.service_b.activities import (
    compensate_reserve_inventory,
    reserve_inventory,
)
from integration_showcase.shared.constants import TASK_QUEUE

_ACTIVITY_EXECUTOR_MAX_WORKERS = 10


async def main() -> None:
    address = os.environ.get("TEMPORAL_ADDRESS", "localhost:7233")
    client = await Client.connect(address, data_converter=pydantic_data_converter)
    with ThreadPoolExecutor(max_workers=_ACTIVITY_EXECUTOR_MAX_WORKERS) as executor:
        worker = Worker(
            client,
            task_queue=TASK_QUEUE,
            activities=[reserve_inventory, compensate_reserve_inventory],
            activity_executor=executor,
        )
        await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
