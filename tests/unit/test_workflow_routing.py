"""Unit tests: OrderWorkflow must route activities to per-service task queues.

The bug (BUG-001): all ``execute_activity`` calls omitted ``task_queue``, so
Temporal dispatched every activity to the workflow's default queue (TASK_QUEUE).
With workers on separate queues any worker that polled first received tasks it
could not handle, and the 3-attempt retry budget was exhausted on wrong workers.

Structural (fast, no server)
    AST inspection of the workflow source verifies that every ``execute_activity``
    call carries a ``task_queue`` kwarg pointing at the correct constant.

Behavioral (WorkflowEnvironment, time-skipping) tests live in
``tests/integration/test_workflow_routing.py``.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

from integration_showcase.shared.constants import (
    TASK_QUEUE,
    TASK_QUEUE_B,
    TASK_QUEUE_C,
    TASK_QUEUE_D,
)
from integration_showcase.workflow.order import OrderWorkflow

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

    def test_refund_payment_routes_to_task_queue_c(self) -> None:
        m = self._activity_to_task_queue()
        assert m.get("refund_payment") == "TASK_QUEUE_C"

    def test_dispatch_shipment_routes_to_task_queue_d(self) -> None:
        m = self._activity_to_task_queue()
        assert m.get("dispatch_shipment") == "TASK_QUEUE_D"
