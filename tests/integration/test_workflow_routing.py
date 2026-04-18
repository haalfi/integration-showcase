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
from temporalio.client import WorkflowFailureError
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from integration_showcase.service_d.activities import ShipmentError
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
# Poison stubs — registered on TASK_QUEUE, fail immediately.
# INVARIANT: these four and the _stub_* four share activity names on purpose;
# they must never be registered on the same Worker.
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


@activity.defn(name="refund_payment")
def _poison_refund(env: Envelope) -> BlobRef:  # noqa: ARG001
    raise RuntimeError("refund_payment routed to wrong queue (TASK_QUEUE)")


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


@activity.defn(name="refund_payment")
def _stub_refund(env: Envelope) -> BlobRef:  # noqa: ARG001
    return _STUB_REF


# ---------------------------------------------------------------------------
# Behavioral tests
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
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter,
    ) as env:
        client = env.client
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
                        _poison_refund,
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
                    activities=[_stub_charge, _stub_refund],
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


@pytest.mark.integration
async def test_unhappy_path_compensation_routes_to_task_queue_b() -> None:
    """Payment failure must trigger compensate_reserve_inventory on TASK_QUEUE_B.

    If compensation were misrouted to TASK_QUEUE, the poison stub would fail it
    and exhaust the retry budget without compensation being recorded.  The
    counter closed over by the tracking stub proves the correct queue was used.
    """
    compensate_calls: list[bool] = []

    @activity.defn(name="charge_payment")
    def _stub_charge_fail(_env: Envelope) -> BlobRef:
        raise ApplicationError(
            "insufficient funds", type="InsufficientFundsError", non_retryable=True
        )

    @activity.defn(name="compensate_reserve_inventory")
    def _stub_compensate_tracking(_env: Envelope) -> BlobRef:
        compensate_calls.append(True)
        return _STUB_REF

    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter,
    ) as env:
        client = env.client
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
                        _poison_refund,
                    ],
                    activity_executor=executor,
                ),
                Worker(
                    client,
                    task_queue=TASK_QUEUE_B,
                    activities=[_stub_reserve, _stub_compensate_tracking],
                    activity_executor=executor,
                ),
                Worker(
                    client,
                    task_queue=TASK_QUEUE_C,
                    activities=[_stub_charge_fail, _stub_refund],
                    activity_executor=executor,
                ),
                Worker(
                    client,
                    task_queue=TASK_QUEUE_D,
                    activities=[_stub_dispatch],
                    activity_executor=executor,
                ),
            ):
                with pytest.raises(WorkflowFailureError):
                    await client.execute_workflow(
                        OrderWorkflow.run,
                        _start_envelope(tx="routing-test-002"),
                        id="test-routing-unhappy-001",
                        task_queue=TASK_QUEUE,
                    )
    assert compensate_calls, "compensate_reserve_inventory must be invoked on TASK_QUEUE_B"


@pytest.mark.integration
async def test_shipment_failure_triggers_two_step_reverse_compensation() -> None:
    """Shipment failure must trigger refund_payment on TASK_QUEUE_C then
    compensate_reserve_inventory on TASK_QUEUE_B (reverse order).

    Poison stubs on TASK_QUEUE catch any misrouted compensation calls.
    Tracking stubs on the correct service queues prove both compensations ran.
    """
    call_order: list[str] = []

    @activity.defn(name="dispatch_shipment")
    def _stub_dispatch_fail(_env: Envelope) -> BlobRef:
        # Raise the real exception so Temporal's type-name serialization is exercised
        # end-to-end (find_application_error in run_shipment_failure.py matches on this).
        raise ShipmentError("carrier unavailable")

    @activity.defn(name="refund_payment")
    def _stub_refund_tracking(_env: Envelope) -> BlobRef:
        call_order.append("refund")
        return _STUB_REF

    @activity.defn(name="compensate_reserve_inventory")
    def _stub_compensate_tracking(_env: Envelope) -> BlobRef:
        call_order.append("compensate")
        return _STUB_REF

    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter,
    ) as env:
        client = env.client
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
                        _poison_refund,
                    ],
                    activity_executor=executor,
                ),
                Worker(
                    client,
                    task_queue=TASK_QUEUE_B,
                    activities=[_stub_reserve, _stub_compensate_tracking],
                    activity_executor=executor,
                ),
                Worker(
                    client,
                    task_queue=TASK_QUEUE_C,
                    activities=[_stub_charge, _stub_refund_tracking],
                    activity_executor=executor,
                ),
                Worker(
                    client,
                    task_queue=TASK_QUEUE_D,
                    activities=[_stub_dispatch_fail],
                    activity_executor=executor,
                ),
            ):
                with pytest.raises(WorkflowFailureError) as exc_info:
                    await client.execute_workflow(
                        OrderWorkflow.run,
                        _start_envelope(tx="routing-test-003"),
                        id="test-routing-shipment-fail-001",
                        task_queue=TASK_QUEUE,
                    )

    assert call_order == ["refund", "compensate"], (
        f"Expected refund before compensate (reverse order), got: {call_order}"
    )
    # Verify ShipmentError type is preserved through Temporal's serialization boundary.
    cause: BaseException | None = exc_info.value
    while cause is not None:
        if isinstance(cause, ApplicationError) and cause.type == "ShipmentError":
            break
        cause = getattr(cause, "cause", None)
    assert cause is not None, "ShipmentError not found in WorkflowFailureError cause chain"
