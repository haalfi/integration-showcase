"""Temporal worker for Service D -- registers the shipment activity.

Run with::

    python -m integration_showcase.service_d.worker
"""

from __future__ import annotations

import asyncio
import os

from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Worker

from integration_showcase.service_d.activities import dispatch_shipment
from integration_showcase.shared.constants import TASK_QUEUE


async def main() -> None:
    address = os.environ.get("TEMPORAL_ADDRESS", "localhost:7233")
    client = await Client.connect(address, data_converter=pydantic_data_converter)
    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        activities=[dispatch_shipment],
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
