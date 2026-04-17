"""Service A: HTTP ingress — receives order POST requests and starts the fulfillment saga."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from opentelemetry import baggage, trace
from opentelemetry.context import attach, detach
from pydantic import BaseModel
from temporalio.client import Client
from temporalio.contrib.opentelemetry import TracingInterceptor
from temporalio.contrib.pydantic import pydantic_data_converter

from integration_showcase.shared import blob
from integration_showcase.shared.constants import BUSINESS_TX_ID_BAGGAGE_KEY, TASK_QUEUE
from integration_showcase.shared.envelope import BlobRef, Envelope
from integration_showcase.shared.otel import (
    inject_carrier_into_envelope,
    set_envelope_span_attrs,
    setup_tracing,
)

_tracer = trace.get_tracer(__name__)

# Module-level client instance; set by lifespan at startup, overridable in tests.
_temporal_client: Client | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Bootstrap tracing and connect the Temporal client at startup.

    ``temporalio.client.Client`` intentionally exposes no ``close`` method
    (confirmed: SDK docs, BK-001).  The underlying Rust-core connection is
    cleaned up by GC once the reference is released.  The ``finally`` block
    below nulls the module-level reference so GC can reclaim the handle
    promptly on lifespan teardown (e.g. ASGI dev-reload, test harness).
    """
    global _temporal_client
    setup_tracing("service-a")
    address = os.environ.get("TEMPORAL_ADDRESS", "localhost:7233")
    _temporal_client = await Client.connect(
        address,
        data_converter=pydantic_data_converter,
        interceptors=[TracingInterceptor()],
    )
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
    # W3C traceparent of the ingress span; clients parse trace_id for Jaeger deep-links.
    traceparent: str


@app.post("/order", response_model=OrderResponse, status_code=202)
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

    # Seed baggage so the business_tx_id rides the whole trace (DESIGN.md
    # §OTel span attributes; unified baggage per Q3).
    token = attach(baggage.set_baggage(BUSINESS_TX_ID_BAGGAGE_KEY, business_tx_id))
    try:
        with _tracer.start_as_current_span("http.ingress POST /order") as span:
            payload = json.dumps(
                {"items": request.items, "customer_id": request.customer_id}
            ).encode()
            blob_path = f"workflows/{business_tx_id}/input.json"
            idempotency_key = Envelope.make_idempotency_key(business_tx_id, "start")
            # run_id is empty at ingress (Temporal assigns it when the workflow
            # starts); later steps forward the real run_id via Envelope.blob_metadata().
            payload_ref: BlobRef = blob.upload(
                payload,
                blob_path,
                metadata={
                    "workflow_id": workflow_id,
                    "run_id": "",
                    "step_id": "start",
                    "schema_version": Envelope.model_fields["schema_version"].default,
                    "idempotency_key": idempotency_key,
                },
            )

            envelope = Envelope(
                workflow_id=workflow_id,
                run_id="",  # Temporal assigns the run_id; backfilled from the handle below.
                business_tx_id=business_tx_id,
                step_id="start",
                payload_ref=payload_ref,
                traceparent="",
                idempotency_key=idempotency_key,
            )

            # Serialize current trace context into the envelope so non-Temporal
            # consumers (audit, correlation) see it alongside the Temporal header.
            envelope = inject_carrier_into_envelope(envelope)

            handle = await _temporal_client.start_workflow(
                "OrderWorkflow",
                envelope,
                id=workflow_id,
                task_queue=TASK_QUEUE,
            )

            # Backfill run_id from the handle so the ingress span carries the
            # real value, not an empty placeholder.
            envelope = envelope.model_copy(update={"run_id": handle.first_execution_run_id})
            set_envelope_span_attrs(span, envelope)
    finally:
        detach(token)

    return OrderResponse(
        business_tx_id=business_tx_id,
        workflow_id=workflow_id,
        traceparent=envelope.traceparent,
    )
