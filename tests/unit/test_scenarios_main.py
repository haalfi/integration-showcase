"""Unit tests for the happy/unhappy scenario ``main()`` entry points.

Mocks ``post_order`` and ``await_workflow`` so the test exercises the assertion
and exit-code logic without touching Service A or Temporal.
"""

from __future__ import annotations

import sys

import pytest
from temporalio.exceptions import ApplicationError

import scenarios._common as common_module
import scenarios.run_happy as run_happy
import scenarios.run_shipment_failure as run_shipment_failure
import scenarios.run_unhappy as run_unhappy

_BUSINESS_TX_ID = "tx-test-123"
_WORKFLOW_ID = f"order-{_BUSINESS_TX_ID}"
_RUN_ID = "run-test-abc"
_TRACEPARENT = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"


class _WorkflowFailureError(Exception):
    """Temporal's WorkflowFailureError exposes the underlying cause via ``.cause``."""

    def __init__(self, cause: BaseException) -> None:
        super().__init__("workflow failed")
        self.cause = cause


@pytest.fixture()
def patch_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    """Minimal argv so argparse doesn't pick up pytest flags."""
    monkeypatch.setattr(sys, "argv", ["scenario"])


def _stub_post_order(
    monkeypatch: pytest.MonkeyPatch, target_module: object, *, traceparent: str = _TRACEPARENT
) -> None:
    async def _fake_post_order(
        items: list[str], customer_id: str, *, base_url: str = "", timeout: float = 30.0
    ) -> dict[str, str]:
        return {
            "business_tx_id": _BUSINESS_TX_ID,
            "workflow_id": _WORKFLOW_ID,
            "traceparent": traceparent,
        }

    monkeypatch.setattr(target_module, "post_order", _fake_post_order)


def _stub_await_workflow(
    monkeypatch: pytest.MonkeyPatch,
    target_module: object,
    *,
    result: object | None,
    exc: BaseException | None,
) -> None:
    async def _fake_await_workflow(
        workflow_id: str, *, address: str = ""
    ) -> tuple[object | None, BaseException | None, str | None]:
        return result, exc, _RUN_ID

    monkeypatch.setattr(target_module, "await_workflow", _fake_await_workflow)


class TestRunHappyMain:
    async def test_returns_0_when_result_matches_business_tx_id(
        self,
        monkeypatch: pytest.MonkeyPatch,
        patch_argv: None,  # noqa: ARG002
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _stub_post_order(monkeypatch, run_happy)
        _stub_await_workflow(monkeypatch, run_happy, result=_BUSINESS_TX_ID, exc=None)

        rc = await run_happy.main()

        assert rc == 0
        out = capsys.readouterr().out
        assert "/trace/0af7651916cd43dd8448eb211c80319c" in out
        assert f"/workflows/{_WORKFLOW_ID}/{_RUN_ID}/history" in out

    async def test_returns_1_when_workflow_raises(
        self,
        monkeypatch: pytest.MonkeyPatch,
        patch_argv: None,  # noqa: ARG002
    ) -> None:
        _stub_post_order(monkeypatch, run_happy)
        _stub_await_workflow(monkeypatch, run_happy, result=None, exc=RuntimeError("boom"))

        assert await run_happy.main() == 1

    async def test_returns_1_when_result_does_not_match(
        self,
        monkeypatch: pytest.MonkeyPatch,
        patch_argv: None,  # noqa: ARG002
    ) -> None:
        _stub_post_order(monkeypatch, run_happy)
        _stub_await_workflow(monkeypatch, run_happy, result="something-else", exc=None)

        assert await run_happy.main() == 1

    async def test_falls_back_to_search_link_when_traceparent_empty(
        self,
        monkeypatch: pytest.MonkeyPatch,
        patch_argv: None,  # noqa: ARG002
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _stub_post_order(monkeypatch, run_happy, traceparent="")
        _stub_await_workflow(monkeypatch, run_happy, result=_BUSINESS_TX_ID, exc=None)

        assert await run_happy.main() == 0
        out = capsys.readouterr().out
        assert "/search?service=service-a" in out


class TestRunUnhappyMain:
    async def test_returns_0_when_expected_failure_in_cause_chain(
        self,
        monkeypatch: pytest.MonkeyPatch,
        patch_argv: None,  # noqa: ARG002
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        app_err = ApplicationError("payment declined", type="InsufficientFundsError")
        wrapper = _WorkflowFailureError(app_err)
        _stub_post_order(monkeypatch, run_unhappy)
        _stub_await_workflow(monkeypatch, run_unhappy, result=None, exc=wrapper)

        rc = await run_unhappy.main()

        assert rc == 0
        out = capsys.readouterr().out
        assert "Expected failure observed: InsufficientFundsError" in out

    async def test_returns_1_when_workflow_succeeds(
        self,
        monkeypatch: pytest.MonkeyPatch,
        patch_argv: None,  # noqa: ARG002
    ) -> None:
        _stub_post_order(monkeypatch, run_unhappy)
        _stub_await_workflow(monkeypatch, run_unhappy, result=_BUSINESS_TX_ID, exc=None)

        assert await run_unhappy.main() == 1

    async def test_returns_1_when_unexpected_exception_type(
        self,
        monkeypatch: pytest.MonkeyPatch,
        patch_argv: None,  # noqa: ARG002
    ) -> None:
        other_err = ApplicationError("connection refused", type="NetworkError")
        wrapper = _WorkflowFailureError(other_err)
        _stub_post_order(monkeypatch, run_unhappy)
        _stub_await_workflow(monkeypatch, run_unhappy, result=None, exc=wrapper)

        assert await run_unhappy.main() == 1


class TestRunShipmentFailureMain:
    async def test_returns_0_when_expected_failure_in_cause_chain(
        self,
        monkeypatch: pytest.MonkeyPatch,
        patch_argv: None,  # noqa: ARG002
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        app_err = ApplicationError("carrier unavailable", type="ShipmentError")
        wrapper = _WorkflowFailureError(app_err)
        _stub_post_order(monkeypatch, run_shipment_failure)
        _stub_await_workflow(monkeypatch, run_shipment_failure, result=None, exc=wrapper)

        rc = await run_shipment_failure.main()

        assert rc == 0
        out = capsys.readouterr().out
        assert "Expected failure observed: ShipmentError" in out

    async def test_returns_1_when_workflow_succeeds(
        self,
        monkeypatch: pytest.MonkeyPatch,
        patch_argv: None,  # noqa: ARG002
    ) -> None:
        _stub_post_order(monkeypatch, run_shipment_failure)
        _stub_await_workflow(monkeypatch, run_shipment_failure, result=_BUSINESS_TX_ID, exc=None)

        assert await run_shipment_failure.main() == 1

    async def test_returns_1_when_unexpected_exception_type(
        self,
        monkeypatch: pytest.MonkeyPatch,
        patch_argv: None,  # noqa: ARG002
    ) -> None:
        other_err = ApplicationError("payment declined", type="InsufficientFundsError")
        wrapper = _WorkflowFailureError(other_err)
        _stub_post_order(monkeypatch, run_shipment_failure)
        _stub_await_workflow(monkeypatch, run_shipment_failure, result=None, exc=wrapper)

        assert await run_shipment_failure.main() == 1


class TestCommonModuleUnaffected:
    """Regression guard: the common helpers are the same objects the scripts import."""

    def test_scripts_reference_same_helpers(self) -> None:
        assert run_happy.post_order is common_module.post_order
        assert run_happy.await_workflow is common_module.await_workflow
        assert run_unhappy.find_application_error is common_module.find_application_error
