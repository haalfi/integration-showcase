"""Unit tests: OrderWorkflow must route activities to per-service task queues.

The bug (BUG-001): all ``execute_activity`` calls omitted ``task_queue``, so
Temporal dispatched every activity to the workflow's default queue (TASK_QUEUE).
With workers on separate queues any worker that polled first received tasks it
could not handle, and the 3-attempt retry budget was exhausted on wrong workers.

Two layers of coverage:

Structural (fast, no server)
    AST inspection of the workflow source verifies that every ``execute_activity``
    call carries a ``task_queue`` kwarg pointing at the correct constant.

Behavioral (WorkflowEnvironment, time-skipping)
    "Poison" stubs registered on TASK_QUEUE fail immediately.  Real stubs on
    TASK_QUEUE_B/C/D succeed.  Retry back-off delays are timers, which the
    time-skipping server skips instantly.  Before the fix the workflow hits the
    poison stubs and raises WorkflowFailureError; after the fix it routes to the
    real stubs and returns the business_tx_id.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from concurrent.futures import ThreadPoolExecutor

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
# Structural tests (no server required, millisecond-fast)
# ---------------------------------------------------------------------------


class TestWorkflowRoutingContract:
    """Inspect workflow source via AST to verify routing contract."""

    def _execute_activity_calls(self) -> list[ast.Call]:
        """Return all execute_activity AST Call nodes from OrderWorkflow.run."""
        source = textwrap.dedent(inspect.getsource(OrderWorkflow.run))
        tree = ast.parse(source)
        calls = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Await):
                continue
            call = node.value
            if not isinstance(call, ast.Call):
                continue
            if isinstance(call.func, ast.Attribute) and call.func.attr == "execute_activity":
                calls.append(call)
        return calls

    def _activity_to_task_queue(self) -> dict[str, str]:
        """Map activity name string → task_queue constant name from workflow source."""
        result: dict[str, str] = {}
        for call in self._execute_activity_calls():
            if not call.args or not isinstance(call.args[0], ast.Constant):
                continue
            name = call.args[0].value
            for kw in call.keywords:
                if kw.arg == "task_queue" and isinstance(kw.value, ast.Name):
                    result[name] = kw.value.id
        return result

    def test_task_queue_constants_are_all_distinct(self) -> None:
        queues = {TASK_QUEUE, TASK_QUEUE_B, TASK_QUEUE_C, TASK_QUEUE_D}
        assert len(queues) == 4, "All four task queue names must be unique"

    def test_all_execute_activity_calls_specify_task_queue(self) -> None:
        """Every execute_activity call must carry task_queue= or activities mis-route."""
        for call in self._execute_activity_calls():
            kwarg_names = {kw.arg for kw in call.keywords}
            assert "task_queue" in kwarg_names, (
                f"execute_activity call for activity "
                f"'{call.args[0].value if call.args else '?'}' is missing task_queue="
            )

    def test_reserve_inventory_routes_to_task_queue_b(self) -> None:
        m = self._activity_to_task_queue()
        assert m.get("reserve_inventory") == "TASK_QUEUE_B"

    def test_compensate_reserve_inventory_routes_to_task_queue_b(self) -> None:
        m = self._activity_to_task_queue()
        assert m.get("compensate_reserve_inventory") == "TASK_QUEUE_B"

    def test_charge_payment_routes_to_task_queue_c(self) -> None:
        m = self._activity_to_task_queue()
        assert m.get("charge_payment") == "TASK_QUEUE_C"

    def test_dispatch_shipment_routes_to_task_queue_d(self) -> None:
        m = self._activity_to_task_queue()
        assert m.get("dispatch_shipment") == "TASK_QUEUE_D"


# ---------------------------------------------------------------------------
# Behavioral tests (WorkflowEnvironment, time-skipping)
# ---------------------------------------------------------------------------
# Poison stubs on TASK_QUEUE fail immediately → retry timers (skipped by the
# time-skipping server) → fast permanent failure.
# Real stubs on TASK_QUEUE_B/C/D succeed.


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


async def test_happy_path_routes_each_activity_to_its_service_queue() -> None:
    """Workflow must reach stubs on TASK_QUEUE_B/C/D, not the poison on TASK_QUEUE.

    Before the fix, activities are dispatched to TASK_QUEUE and the poison stubs
    fail them immediately.  Retry timers are skipped, budget exhausted, workflow
    fails → WorkflowFailureError → this test raises, meaning it fails.

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
