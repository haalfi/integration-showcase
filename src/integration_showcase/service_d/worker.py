"""Temporal worker for Service D -- registers the shipment activity.

Run with::

    python -m integration_showcase.service_d.worker

The shipment activity is sync ``def`` (it does blocking I/O), so the
worker needs an ``activity_executor`` ``ThreadPoolExecutor`` to run it
off the Temporal event loop.
"""

from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor

from temporalio.client import Client
from temporalio.contrib.opentelemetry import TracingInterceptor
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Worker

from integration_showcase.service_d.activities import dispatch_shipment
from integration_showcase.shared.constants import TASK_QUEUE_D
from integration_showcase.shared.otel import setup_tracing

_ACTIVITY_EXECUTOR_MAX_WORKERS = 10


async def main() -> None:
    setup_tracing("service-d")
    address = os.environ.get("TEMPORAL_ADDRESS", "localhost:7233")
    client = await Client.connect(
        address,
        data_converter=pydantic_data_converter,
        interceptors=[TracingInterceptor()],
    )
    with ThreadPoolExecutor(max_workers=_ACTIVITY_EXECUTOR_MAX_WORKERS) as executor:
        worker = Worker(
            client,
            task_queue=TASK_QUEUE_D,
            activities=[dispatch_shipment],
            activity_executor=executor,
        )
        await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
