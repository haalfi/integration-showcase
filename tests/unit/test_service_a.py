"""Unit tests for Service A POST /order endpoint.

Uses MemoryBackend for blob I/O and an AsyncMock for the Temporal client —
no Docker, no network required.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import AsyncGenerator
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from opentelemetry.context import attach, detach
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import get_current_span
from remote_store import Store
from remote_store.backends import MemoryBackend

import integration_showcase.service_a.app as app_module
import integration_showcase.shared.blob as blob_module
from integration_showcase.shared.constants import TASK_QUEUE
from integration_showcase.shared.envelope import Envelope
from integration_showcase.shared.otel import extract_context_from_envelope


@pytest.fixture()
def memory_store(monkeypatch: pytest.MonkeyPatch) -> Store:
    """Inject in-memory blob store via the module-level factory seam."""
    s = Store(MemoryBackend())

    @contextmanager
    def _factory():  # type: ignore[return]
        yield s

    monkeypatch.setattr(blob_module, "_store_factory", _factory)
    return s


_MOCK_RUN_ID = "run-mock-abcd1234"


@pytest.fixture()
def temporal_mock(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Inject a mock Temporal client via the module-level seam.

    ``start_workflow`` is configured to return a stub ``WorkflowHandle`` so
    the ingress can read ``first_execution_run_id`` and tag the span.
    """
    mock = AsyncMock()
    handle = MagicMock()
    handle.first_execution_run_id = _MOCK_RUN_ID
    mock.start_workflow.return_value = handle
    monkeypatch.setattr(app_module, "_temporal_client", mock)
    return mock


@pytest.fixture()
async def client(
    memory_store: Store,  # noqa: ARG001
    temporal_mock: AsyncMock,  # noqa: ARG001
) -> AsyncGenerator[httpx.AsyncClient, None]:
    """HTTP test client backed by the app's ASGI interface.

    ASGITransport does not trigger the FastAPI lifespan, so no real Temporal
    connection is attempted — the monkeypatched mock is used instead.
    """
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_module.app),
        base_url="http://test",
    ) as ac:
        yield ac


_VALID_ORDER = {"items": ["widget-1", "gadget-2"], "customer_id": "cust-001"}


class TestCreateOrder:
    async def test_returns_200_with_required_fields(self, client: httpx.AsyncClient) -> None:
        response = await client.post("/order", json=_VALID_ORDER)
        assert response.status_code == 200
        body = response.json()
        assert "business_tx_id" in body
        assert "workflow_id" in body

    async def test_business_tx_id_is_uuid4(self, client: httpx.AsyncClient) -> None:
        response = await client.post("/order", json=_VALID_ORDER)
        parsed = uuid.UUID(response.json()["business_tx_id"])
        assert parsed.version == 4

    async def test_workflow_id_derives_from_business_tx_id(self, client: httpx.AsyncClient) -> None:
        response = await client.post("/order", json=_VALID_ORDER)
        body = response.json()
        assert body["workflow_id"] == f"order-{body['business_tx_id']}"

    async def test_payload_uploaded_to_blob_store(
        self, client: httpx.AsyncClient, memory_store: Store
    ) -> None:
        response = await client.post("/order", json=_VALID_ORDER)
        business_tx_id = response.json()["business_tx_id"]

        data = memory_store.read_bytes(f"workflows/{business_tx_id}/input.json")
        payload = json.loads(data)
        assert payload["items"] == _VALID_ORDER["items"]
        assert payload["customer_id"] == _VALID_ORDER["customer_id"]

    async def test_start_workflow_envelope_and_task_queue(
        self, client: httpx.AsyncClient, temporal_mock: AsyncMock, memory_store: Store
    ) -> None:
        """Verify the full start_workflow call: workflow name, envelope invariants, task queue."""
        response = await client.post("/order", json=_VALID_ORDER)
        body = response.json()
        business_tx_id = body["business_tx_id"]
        workflow_id = body["workflow_id"]

        temporal_mock.start_workflow.assert_called_once()
        args, kwargs = temporal_mock.start_workflow.call_args

        # Workflow name (typo here is a silent Temporal runtime error)
        assert args[0] == "OrderWorkflow"

        # Envelope invariants (DESIGN.md §Envelope invariants)
        envelope: Envelope = args[1]
        assert isinstance(envelope, Envelope)
        assert envelope.business_tx_id == business_tx_id
        assert envelope.workflow_id == workflow_id
        assert envelope.step_id == "start"
        assert envelope.idempotency_key == f"{business_tx_id}:start:1.0"
        assert envelope.payload_ref.blob_url == f"workflows/{business_tx_id}/input.json"
        expected_payload = json.dumps(
            {"items": _VALID_ORDER["items"], "customer_id": _VALID_ORDER["customer_id"]}
        ).encode()
        assert envelope.payload_ref.sha256 == hashlib.sha256(expected_payload).hexdigest()

        # Temporal routing — must match the worker's task queue (shared/constants.py)
        assert kwargs["id"] == workflow_id
        assert kwargs["task_queue"] == TASK_QUEUE

    async def test_ingress_span_has_six_business_attrs(
        self,
        client: httpx.AsyncClient,
        spans: InMemorySpanExporter,
    ) -> None:
        response = await client.post("/order", json=_VALID_ORDER)
        body = response.json()

        ingress_spans = [
            s for s in spans.get_finished_spans() if s.name == "http.ingress POST /order"
        ]
        assert len(ingress_spans) == 1
        attrs = ingress_spans[0].attributes or {}
        assert attrs["business_tx_id"] == body["business_tx_id"]
        assert attrs["workflow_id"] == body["workflow_id"]
        assert attrs["step_id"] == "start"
        assert attrs["schema_version"] == "1.0"
        assert "payload_ref_sha256" in attrs
        # run_id is backfilled from the handle returned by start_workflow.
        assert attrs["run_id"] == _MOCK_RUN_ID

    async def test_envelope_traceparent_matches_ingress_trace(
        self,
        client: httpx.AsyncClient,
        temporal_mock: AsyncMock,
        spans: InMemorySpanExporter,
    ) -> None:
        await client.post("/order", json=_VALID_ORDER)

        ingress_spans = [
            s for s in spans.get_finished_spans() if s.name == "http.ingress POST /order"
        ]
        assert len(ingress_spans) == 1
        expected_trace_id = ingress_spans[0].get_span_context().trace_id

        envelope: Envelope = temporal_mock.start_workflow.call_args[0][1]
        assert envelope.traceparent != ""
        assert envelope.baggage.get("business_tx_id") == envelope.business_tx_id

        # Extract the carrier and verify trace_id continuity across the boundary.
        ctx = extract_context_from_envelope(envelope)
        token = attach(ctx)
        try:
            extracted_trace_id = get_current_span().get_span_context().trace_id
        finally:
            detach(token)
        assert extracted_trace_id == expected_trace_id

    async def test_uninitialized_temporal_client_returns_500(
        self,
        memory_store: Store,  # noqa: ARG001
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(app_module, "_temporal_client", None)
        # raise_app_exceptions=False lets the ASGI error middleware return 500
        # instead of propagating the RuntimeError to the test.
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_module.app, raise_app_exceptions=False),
            base_url="http://test",
        ) as ac:
            response = await ac.post("/order", json=_VALID_ORDER)
        assert response.status_code == 500
