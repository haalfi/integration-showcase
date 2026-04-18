"""Temporal worker hosting OrderWorkflow (no activities registered).

Run with::

    python -m integration_showcase.workflow.worker

The workflow worker polls ``TASK_QUEUE``; per-service workers poll
``TASK_QUEUE_B/C/D`` and the workflow dispatches each activity to the
owning service's queue (BUG-001).
"""

from __future__ import annotations

import asyncio
import os

from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Worker

from integration_showcase.shared.constants import TASK_QUEUE
from integration_showcase.shared.otel import EnvelopeTracingInterceptor, setup_tracing
from integration_showcase.workflow.order import OrderWorkflow


async def main() -> None:
    setup_tracing("order-workflow-worker")
    address = os.environ.get("TEMPORAL_ADDRESS", "localhost:7233")
    client = await Client.connect(
        address,
        data_converter=pydantic_data_converter,
        interceptors=[EnvelopeTracingInterceptor()],
    )
    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[OrderWorkflow],
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
