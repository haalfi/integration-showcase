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

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from temporalio import activity
from temporalio.client import WorkflowFailureError
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from integration_showcase.service_c.activities import InsufficientFundsError, PaymentGatewayError
from integration_showcase.service_d.activities import ShipmentError
from integration_showcase.shared.constants import (
    TASK_QUEUE,
    TASK_QUEUE_B,
    TASK_QUEUE_C,
    TASK_QUEUE_D,
)
from integration_showcase.shared.envelope import BlobRef, Envelope
from integration_showcase.workflow.order import (
    _PAYMENT_RETRY,
    OrderWorkflow,
)

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


@pytest.mark.integration
async def test_retry_then_fail_payment_path_triggers_compensation() -> None:
    """§5.2 retry-then-fail: charge_payment raises retryable PaymentGatewayError on
    attempts 1-2, then non-retryable InsufficientFundsError on attempt 3.
    Compensation must still run (compensate_reserve_inventory on TASK_QUEUE_B).

    This is the acceptance test for IS-012: the workflow must not short-circuit
    compensation just because payment was retried before failing terminally.
    """
    charge_attempts: list[int] = []
    compensate_calls: list[bool] = []
    # Lock guards append+len so the stub is safe if the executor ever dispatches
    # concurrent calls. Temporal retries sync activities sequentially today, but
    # the lock makes the assumption explicit rather than invisible.
    _counter_lock = threading.Lock()

    @activity.defn(name="charge_payment")
    def _stub_charge_retry_then_fail(_env: Envelope) -> BlobRef:
        with _counter_lock:
            charge_attempts.append(1)
            attempt = len(charge_attempts)
        # Retryable on attempts 1..(maximum_attempts-1); terminal on maximum_attempts.
        # The guard mirrors _PAYMENT_RETRY.maximum_attempts so that a change to the
        # retry budget in order.py forces an update here rather than silently breaking.
        # Real exception classes (not ApplicationError wrappers) exercise Temporal's
        # type-name-based retry classification end-to-end: the SDK serialises the class
        # name as the ApplicationError type, which _PAYMENT_RETRY.non_retryable_error_types
        # then matches against.
        if attempt < _PAYMENT_RETRY.maximum_attempts:
            raise PaymentGatewayError(f"gateway_timeout on attempt {attempt}")
        raise InsufficientFundsError("insufficient funds")

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
                    activities=[_stub_charge_retry_then_fail, _stub_refund],
                    activity_executor=executor,
                ),
                Worker(
                    client,
                    task_queue=TASK_QUEUE_D,
                    activities=[_stub_dispatch],
                    activity_executor=executor,
                ),
            ):
                with pytest.raises(WorkflowFailureError) as exc_info:
                    await client.execute_workflow(
                        OrderWorkflow.run,
                        _start_envelope(tx="routing-test-004"),
                        id="test-routing-retry-then-fail-001",
                        task_queue=TASK_QUEUE,
                    )

    expected = _PAYMENT_RETRY.maximum_attempts
    assert len(charge_attempts) == expected, (
        f"Expected {expected} charge_payment attempts ({expected - 1} retryable + 1 terminal),"
        f" got {len(charge_attempts)}"
    )
    assert compensate_calls, (
        "compensate_reserve_inventory must be invoked after terminal payment failure"
    )

    cause: BaseException | None = exc_info.value
    while cause is not None:
        if isinstance(cause, ApplicationError) and cause.type == "InsufficientFundsError":
            break
        cause = getattr(cause, "cause", None)
    assert cause is not None, "InsufficientFundsError not found in WorkflowFailureError cause chain"


@pytest.mark.integration
async def test_payment_retry_budget_exhaustion_triggers_compensation() -> None:
    """Retry budget: charge_payment raises retryable PaymentGatewayError on every attempt.
    After maximum_attempts Temporal gives up; the workflow's except still runs compensation.

    This complements test_retry_then_fail_payment_path_triggers_compensation: that test
    exercises the non_retryable terminal switch; this one verifies the retry budget itself
    is honoured — the stub never becomes non-retryable, yet compensation fires.
    """
    charge_attempts: list[int] = []
    compensate_calls: list[bool] = []
    _counter_lock = threading.Lock()

    @activity.defn(name="charge_payment")
    def _stub_charge_always_gateway_error(_env: Envelope) -> BlobRef:
        with _counter_lock:
            charge_attempts.append(1)
        raise PaymentGatewayError("gateway_timeout (budget exhaustion test)")

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
                    activities=[_stub_charge_always_gateway_error, _stub_refund],
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
                        _start_envelope(tx="routing-test-005"),
                        id="test-routing-budget-exhaust-001",
                        task_queue=TASK_QUEUE,
                    )

    assert len(charge_attempts) == _PAYMENT_RETRY.maximum_attempts, (
        f"Expected {_PAYMENT_RETRY.maximum_attempts} attempts before budget exhaustion,"
        f" got {len(charge_attempts)}"
    )
    assert compensate_calls, "compensate_reserve_inventory must run even after budget exhaustion"


@pytest.mark.integration
async def test_refund_failure_does_not_skip_compensate_reserve_inventory() -> None:
    """BK-006: if refund_payment fails permanently, compensate_reserve_inventory must still run.

    Before the fix the two compensation steps were sequential with no isolation;
    a terminal refund failure propagated before compensate_reserve_inventory was
    dispatched, leaving the inventory reservation live.

    After the fix refund_payment is wrapped in its own try/except so
    compensate_reserve_inventory is always dispatched regardless of refund outcome.
    """
    compensate_calls: list[bool] = []

    @activity.defn(name="dispatch_shipment")
    def _stub_dispatch_fail(_env: Envelope) -> BlobRef:
        raise ShipmentError("carrier unavailable")

    @activity.defn(name="refund_payment")
    def _stub_refund_permanent_fail(_env: Envelope) -> BlobRef:
        raise PaymentGatewayError("refund gateway down")

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
                    activities=[_stub_charge, _stub_refund_permanent_fail],
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
                        _start_envelope(tx="routing-test-006"),
                        id="test-routing-refund-fail-001",
                        task_queue=TASK_QUEUE,
                    )

    assert compensate_calls, (
        "compensate_reserve_inventory must run even when refund_payment fails permanently"
    )
    # Verify the refund error surfaces as the workflow failure (due to explicit
    # cause chaining: raise refund_error from shipment_exc), not the shipment error.
    refund_failure: BaseException | None = exc_info.value
    while refund_failure is not None:
        if (
            isinstance(refund_failure, ApplicationError)
            and refund_failure.type == "PaymentGatewayError"
        ):
            break
        refund_failure = getattr(refund_failure, "cause", None)
    assert refund_failure is not None, (
        "PaymentGatewayError (refund failure) not found in cause chain"
    )
