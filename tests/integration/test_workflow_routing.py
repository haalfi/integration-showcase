"""Integration tests: OrderWorkflow routes each activity to its service queue.

Behavioral coverage using WorkflowEnvironment (time-skipping server):
  - Poison stubs on TASK_QUEUE fail immediately.
  - Real stubs on TASK_QUEUE_B/C/D succeed.
  - Retry back-off delays are timers, which the time-skipping server skips
    instantly.
  - Before the fix (BUG-001) the workflow hit the poison stubs and raised
    WorkflowFailureError; after the fix it routes to the real stubs and
    returns the business_tx_id.

Requires: embedded Temporal test-server binary (shipped with the
temporalio SDK).  No Docker needed.  Network I/O only to the in-process
test server.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from temporalio import activity
from temporalio.client import Client
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

# ---------------------------------------------------------------------------
# Shared stub helpers
# ---------------------------------------------------------------------------

_STUB_REF = BlobRef(blob_url="stub/ref.json", sha256="a" * 64)


def _start_envelope(tx: str = "routing-test-001") -> Envelope:
    return Envelope(
        workflow_id=f"order-{tx}",
        run_id="",
        business_tx_id=tx,
        step_id="start",
        payload_ref=BlobRef(blob_url=f"stub/{tx}/input.json", sha256="b" * 64),
        traceparent="",
        idempotency_key=Envelope.make_idempotency_key(tx, "start"),
    )


# ---------------------------------------------------------------------------
# Poison stubs — registered on TASK_QUEUE, fail immediately
# ---------------------------------------------------------------------------


@activity.defn(name="reserve_inventory")
def _poison_reserve(env: Envelope) -> BlobRef:  # noqa: ARG001
    raise RuntimeError("reserve_inventory routed to wrong queue (TASK_QUEUE)")


@activity.defn(name="charge_payment")
def _poison_charge(env: Envelope) -> BlobRef:  # noqa: ARG001
    raise RuntimeError("charge_payment routed to wrong queue (TASK_QUEUE)")


@activity.defn(name="dispatch_shipment")
def _poison_dispatch(env: Envelope) -> BlobRef:  # noqa: ARG001
    raise RuntimeError("dispatch_shipment routed to wrong queue (TASK_QUEUE)")


@activity.defn(name="compensate_reserve_inventory")
def _poison_compensate(env: Envelope) -> BlobRef:  # noqa: ARG001
    raise RuntimeError("compensate_reserve_inventory routed to wrong queue (TASK_QUEUE)")


# ---------------------------------------------------------------------------
# Real stubs — registered on per-service queues, succeed
# ---------------------------------------------------------------------------


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


async def _make_client(env: WorkflowEnvironment) -> Client:
    """Return a pydantic-aware client against the test server."""
    return await Client.connect(
        env.client.service_client.config.target_host,
        data_converter=pydantic_data_converter,
        namespace=env.client.namespace,
    )


# ---------------------------------------------------------------------------
# Behavioral test
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_happy_path_routes_each_activity_to_its_service_queue() -> None:
    """Workflow must reach stubs on TASK_QUEUE_B/C/D, not the poison on TASK_QUEUE.

    Before the fix (BUG-001), activities are dispatched to TASK_QUEUE and the
    poison stubs fail them immediately.  Retry timers are skipped, budget
    exhausted, workflow fails → WorkflowFailureError → test fails.

    After the fix, activities reach the real stubs and the workflow returns the
    business_tx_id.
    """
    async with await WorkflowEnvironment.start_time_skipping() as env:
        client = await _make_client(env)
        with ThreadPoolExecutor() as executor:
            async with (
                Worker(
                    client,
                    task_queue=TASK_QUEUE,
                    workflows=[OrderWorkflow],
                    activities=[
                        _poison_reserve,
                        _poison_charge,
                        _poison_dispatch,
                        _poison_compensate,
                    ],
                    activity_executor=executor,
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
                    _start_envelope(),
                    id="test-routing-happy-001",
                    task_queue=TASK_QUEUE,
                )
    assert result == "routing-test-001"
