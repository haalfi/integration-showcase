"""Temporal worker hosting OrderWorkflow (no activities registered).

Run with::

    python -m integration_showcase.workflow.worker

Activities run in the per-service workers (service_b/c/d). All workers
poll the same task queue (``integration_showcase.shared.constants.TASK_QUEUE``).
"""

from __future__ import annotations

import asyncio
import os

from temporalio.client import Client
from temporalio.contrib.opentelemetry import TracingInterceptor
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Worker

from integration_showcase.shared.constants import TASK_QUEUE
from integration_showcase.shared.otel import setup_tracing
from integration_showcase.workflow.order import OrderWorkflow


async def main() -> None:
    setup_tracing("order-workflow-worker")
    address = os.environ.get("TEMPORAL_ADDRESS", "localhost:7233")
    client = await Client.connect(
        address,
        data_converter=pydantic_data_converter,
        interceptors=[TracingInterceptor()],
    )
    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[OrderWorkflow],
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
