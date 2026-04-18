"""Temporal worker for Service C -- registers payment and refund activities.

Run with::

    python -m integration_showcase.service_c.worker

The payment activity is sync ``def`` (it does blocking I/O), so the
worker needs an ``activity_executor`` ``ThreadPoolExecutor`` to run it
off the Temporal event loop.
"""

from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor

from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Worker

from integration_showcase.service_c.activities import charge_payment, refund_payment
from integration_showcase.shared.constants import TASK_QUEUE_C
from integration_showcase.shared.otel import EnvelopeTracingInterceptor, setup_tracing

_ACTIVITY_EXECUTOR_MAX_WORKERS = 10


async def main() -> None:
    setup_tracing("service-c")
    address = os.environ.get("TEMPORAL_ADDRESS", "localhost:7233")
    client = await Client.connect(
        address,
        data_converter=pydantic_data_converter,
        interceptors=[EnvelopeTracingInterceptor()],
    )
    with ThreadPoolExecutor(max_workers=_ACTIVITY_EXECUTOR_MAX_WORKERS) as executor:
        worker = Worker(
            client,
            task_queue=TASK_QUEUE_C,
            activities=[charge_payment, refund_payment],
            activity_executor=executor,
        )
        await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
