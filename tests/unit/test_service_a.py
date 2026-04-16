"""Unit tests for Service A POST /order endpoint.

Uses MemoryBackend for blob I/O and an AsyncMock for the Temporal client —
no Docker, no network required.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator
from contextlib import contextmanager
from unittest.mock import AsyncMock

import httpx
import pytest
from remote_store import Store
from remote_store.backends import MemoryBackend

import integration_showcase.service_a.app as app_module
import integration_showcase.shared.blob as blob_module


@pytest.fixture()
def memory_store(monkeypatch: pytest.MonkeyPatch) -> Store:
    """Inject in-memory blob store via the module-level factory seam."""
    s = Store(MemoryBackend())

    @contextmanager
    def _factory():  # type: ignore[return]
        yield s

    monkeypatch.setattr(blob_module, "_store_factory", _factory)
    return s


@pytest.fixture()
def temporal_mock(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Inject a mock Temporal client via the module-level seam."""
    mock = AsyncMock()
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

    async def test_temporal_start_workflow_called_with_matching_id(
        self, client: httpx.AsyncClient, temporal_mock: AsyncMock
    ) -> None:
        response = await client.post("/order", json=_VALID_ORDER)
        workflow_id = response.json()["workflow_id"]

        temporal_mock.start_workflow.assert_called_once()
        _args, kwargs = temporal_mock.start_workflow.call_args
        assert kwargs["id"] == workflow_id

    async def test_uninitialized_temporal_client_returns_500(
        self,
        memory_store: Store,
        monkeypatch: pytest.MonkeyPatch,  # noqa: ARG001
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
