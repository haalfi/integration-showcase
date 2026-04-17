"""Integration test: OrderWorkflow tags its RunWorkflow span with six business attrs (IS-008).

Requires the embedded Temporal test server (ships with the temporalio SDK).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from temporalio import activity
from temporalio.client import Client
from temporalio.contrib.opentelemetry import TracingInterceptor
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from integration_showcase.shared.constants import (
    TASK_QUEUE,
    TASK_QUEUE_B,
    TASK_QUEUE_C,
    TASK_QUEUE_D,
)
from integration_showcase.shared.envelope import BlobRef, Envelope
from integration_showcase.workflow.order import OrderWorkflow

_STUB_REF = BlobRef(blob_url="stub/ref.json", sha256="a" * 64)

_TX = "span-attr-test-001"
_START_ENVELOPE = Envelope(
    workflow_id=f"order-{_TX}",
    run_id="",
    business_tx_id=_TX,
    step_id="start",
    payload_ref=BlobRef(blob_url=f"stub/{_TX}/input.json", sha256="c" * 64),
    traceparent="",
    idempotency_key=Envelope.make_idempotency_key(_TX, "start"),
)


@activity.defn(name="reserve_inventory")
def _stub_reserve(env: Envelope) -> BlobRef:  # noqa: ARG001
    return _STUB_REF


@activity.defn(name="charge_payment")
def _stub_charge(env: Envelope) -> BlobRef:  # noqa: ARG001
    return _STUB_REF


@activity.defn(name="dispatch_shipment")
def _stub_dispatch(env: Envelope) -> BlobRef:  # noqa: ARG001
    return _STUB_REF


@activity.defn(name="compensate_reserve_inventory")
def _stub_compensate(env: Envelope) -> BlobRef:  # noqa: ARG001
    return _STUB_REF


@pytest.mark.integration
async def test_workflow_span_has_six_business_attrs(spans: InMemorySpanExporter) -> None:
    """RunWorkflow:OrderWorkflow span must carry the six required business attrs."""
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter,
    ) as env:
        # A new client with TracingInterceptor so the RunWorkflow span is recorded.
        client = await Client.connect(
            env.client.service_client.config.target_host,
            interceptors=[TracingInterceptor()],
            data_converter=pydantic_data_converter,
            namespace=env.client.namespace,
        )
        with ThreadPoolExecutor() as executor:
            async with (
                Worker(
                    client,
                    task_queue=TASK_QUEUE,
                    workflows=[OrderWorkflow],
                    interceptors=[TracingInterceptor()],
                ),
                Worker(
                    client,
                    task_queue=TASK_QUEUE_B,
                    activities=[_stub_reserve, _stub_compensate],
                    activity_executor=executor,
                ),
                Worker(
                    client,
                    task_queue=TASK_QUEUE_C,
                    activities=[_stub_charge],
                    activity_executor=executor,
                ),
                Worker(
                    client,
                    task_queue=TASK_QUEUE_D,
                    activities=[_stub_dispatch],
                    activity_executor=executor,
                ),
            ):
                result = await client.execute_workflow(
                    OrderWorkflow.run,
                    _START_ENVELOPE,
                    id="test-span-attrs-001",
                    task_queue=TASK_QUEUE,
                )

    assert result == _TX

    workflow_spans = [
        s
        for s in spans.get_finished_spans()
        if "RunWorkflow" in s.name or "OrderWorkflow" in s.name
    ]
    assert workflow_spans, (
        f"No RunWorkflow span found; got: {[s.name for s in spans.get_finished_spans()]}"
    )
    attrs = workflow_spans[0].attributes or {}
    assert attrs.get("business_tx_id") == _TX
    assert attrs.get("workflow_id") == f"order-{_TX}"
    assert attrs.get("step_id") == "workflow"
    assert attrs.get("schema_version") == "1.0"
    assert attrs.get("payload_ref_sha256") == "c" * 64
    assert attrs.get("run_id"), "run_id must be backfilled (non-empty)"
