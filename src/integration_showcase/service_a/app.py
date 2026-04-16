"""Service A: HTTP ingress — receives order POST requests and starts the fulfillment saga."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import BaseModel
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter

from integration_showcase.shared import blob
from integration_showcase.shared.constants import TASK_QUEUE
from integration_showcase.shared.envelope import BlobRef, Envelope

# Module-level client instance; set by lifespan at startup, overridable in tests.
_temporal_client: Client | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Connect Temporal client at startup; drop reference on shutdown.

    ``temporalio.client.Client`` has no ``close`` method -- the underlying
    connection is cleaned up by GC when the reference is dropped.
    """
    global _temporal_client
    address = os.environ.get("TEMPORAL_ADDRESS", "localhost:7233")
    _temporal_client = await Client.connect(address, data_converter=pydantic_data_converter)
    try:
        yield
    finally:
        _temporal_client = None


app = FastAPI(title="integration-showcase -- Service A (Ingress)", lifespan=lifespan)


class OrderRequest(BaseModel):
    items: list[str]
    customer_id: str


class OrderResponse(BaseModel):
    business_tx_id: str
    workflow_id: str


@app.post("/order", response_model=OrderResponse)
async def create_order(request: OrderRequest) -> OrderResponse:
    """Ingest an order: write payload to Blob Storage, start OrderWorkflow.

    1. Generate business_tx_id (UUID4).
    2. Serialize OrderRequest → JSON bytes → upload to blob store.
    3. Build initial Envelope (step_id="start").
    4. Start OrderWorkflow on Temporal (fire-and-forget).
    5. Return business_tx_id + workflow_id immediately.
    """
    if _temporal_client is None:
        raise RuntimeError("Temporal client not initialized; is the server running?")

    business_tx_id = str(uuid.uuid4())
    workflow_id = f"order-{business_tx_id}"

    # Serialize payload and upload to blob store
    payload = json.dumps({"items": request.items, "customer_id": request.customer_id}).encode()
    blob_path = f"workflows/{business_tx_id}/input.json"
    payload_ref: BlobRef = blob.upload(payload, blob_path)

    # Build initial envelope (run_id and traceparent filled in by IS-005 OTel integration)
    envelope = Envelope(
        workflow_id=workflow_id,
        run_id="",  # Temporal assigns the run_id; IS-005 propagates via activity.info()
        business_tx_id=business_tx_id,
        step_id="start",
        payload_ref=payload_ref,
        traceparent="",  # IS-005: W3C trace context propagation
        idempotency_key=Envelope.make_idempotency_key(business_tx_id, "start"),
    )

    # Fire-and-forget: start the workflow without awaiting completion
    await _temporal_client.start_workflow(
        "OrderWorkflow",
        envelope,
        id=workflow_id,
        task_queue=TASK_QUEUE,
    )

    return OrderResponse(business_tx_id=business_tx_id, workflow_id=workflow_id)
