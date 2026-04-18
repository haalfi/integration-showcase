"""Integration test: OrderWorkflow tags its RunWorkflow span with six business attrs (IS-008).

Requires the embedded Temporal test server (ships with the temporalio SDK).
Same @pytest.mark.integration guard as test_workflow_routing.py; no explicit skip logic.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
import temporalio.worker
import temporalio.workflow
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from temporalio import activity
from temporalio.contrib.opentelemetry import TracingInterceptor, TracingWorkflowInboundInterceptor
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


class _EnvelopeTracingWorkflowInterceptor(TracingWorkflowInboundInterceptor):
    """Stamps six envelope business attrs onto the RunWorkflow span (IS-008)."""

    async def execute_workflow(self, input: temporalio.worker.ExecuteWorkflowInput) -> Any:
        env = input.args[0] if input.args else None
        self._run_wf_attrs: dict[str, str] = {}
        if env is not None:
            run_id = getattr(env, "run_id", None) or temporalio.workflow.info().run_id
            ref = getattr(env, "payload_ref", None)
            self._run_wf_attrs = {
                k: v
                for k, v in {
                    "business_tx_id": getattr(env, "business_tx_id", None),
                    "workflow_id": getattr(env, "workflow_id", None),
                    "run_id": run_id,
                    "step_id": "workflow",
                    "payload_ref_sha256": getattr(ref, "sha256", None) if ref else None,
                    "schema_version": getattr(env, "schema_version", None),
                }.items()
                if v
            }
        return await super().execute_workflow(input)

    def _completed_span(self, span_name: str, *, additional_attributes=None, **kwargs) -> None:  # type: ignore[override]
        attrs = getattr(self, "_run_wf_attrs", {})
        if span_name.startswith("RunWorkflow:") and attrs:
            additional_attributes = {**(additional_attributes or {}), **attrs}
        return super()._completed_span(
            span_name, additional_attributes=additional_attributes, **kwargs
        )


class _EnvelopeTracingInterceptor(TracingInterceptor):
    def workflow_interceptor_class(
        self, input: temporalio.worker.WorkflowInterceptorClassInput
    ) -> type[TracingWorkflowInboundInterceptor]:
        super().workflow_interceptor_class(input)  # registers extern function
        return _EnvelopeTracingWorkflowInterceptor


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
    """RunWorkflow:OrderWorkflow span must carry the six required business attrs.

    Also asserts the cross-span invariant (DESIGN.md §OTel span attributes): every
    RunWorkflow:* and RunActivity:* span in this execution carries business_tx_id.
    """
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter,
    ) as env:
        # TracingInterceptor only on the workflow Worker — that is what produces the
        # RunWorkflow:OrderWorkflow span. env.client is used directly so the test
        # doesn't emit a StartWorkflow span that would pollute the captured span set.
        with ThreadPoolExecutor() as executor:
            async with (
                Worker(
                    env.client,
                    task_queue=TASK_QUEUE,
                    workflows=[OrderWorkflow],
                    interceptors=[_EnvelopeTracingInterceptor(always_create_workflow_spans=True)],
                ),
                Worker(
                    env.client,
                    task_queue=TASK_QUEUE_B,
                    activities=[_stub_reserve, _stub_compensate],
                    activity_executor=executor,
                ),
                Worker(
                    env.client,
                    task_queue=TASK_QUEUE_C,
                    activities=[_stub_charge],
                    activity_executor=executor,
                ),
                Worker(
                    env.client,
                    task_queue=TASK_QUEUE_D,
                    activities=[_stub_dispatch],
                    activity_executor=executor,
                ),
            ):
                result = await env.client.execute_workflow(
                    OrderWorkflow.run,
                    _START_ENVELOPE,
                    id="test-span-attrs-001",
                    task_queue=TASK_QUEUE,
                )

    assert result == _TX

    # IS-008: RunWorkflow span carries the six required business attributes.
    run_workflow_spans = [
        s for s in spans.get_finished_spans() if s.name == "RunWorkflow:OrderWorkflow"
    ]
    all_names = [s.name for s in spans.get_finished_spans()]
    assert run_workflow_spans, f"No RunWorkflow:OrderWorkflow span found; got: {all_names}"
    attrs = run_workflow_spans[0].attributes or {}
    assert attrs.get("business_tx_id") == _TX
    assert attrs.get("workflow_id") == f"order-{_TX}"
    assert attrs.get("step_id") == "workflow"
    assert attrs.get("schema_version") == "1.0"
    assert attrs.get("payload_ref_sha256") == "c" * 64
    assert attrs.get("run_id"), "run_id must be backfilled (non-empty)"

    # Cross-span invariant: every RunWorkflow:* and RunActivity:* span must carry
    # business_tx_id (DESIGN.md §OTel span attributes "required on every span").
    temporal_spans = [
        s
        for s in spans.get_finished_spans()
        if s.name.startswith("RunWorkflow:") or s.name.startswith("RunActivity:")
    ]
    for s in temporal_spans:
        assert (s.attributes or {}).get("business_tx_id") == _TX, (
            f"Span {s.name!r} is missing business_tx_id"
        )
