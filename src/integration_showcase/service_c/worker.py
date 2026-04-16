"""Temporal worker for Service C -- registers the payment activity.

Run with::

    python -m integration_showcase.service_c.worker
"""

from __future__ import annotations

import asyncio
import os

from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Worker

from integration_showcase.service_c.activities import charge_payment
from integration_showcase.shared.constants import TASK_QUEUE


async def main() -> None:
    address = os.environ.get("TEMPORAL_ADDRESS", "localhost:7233")
    client = await Client.connect(address, data_converter=pydantic_data_converter)
    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        activities=[charge_payment],
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
