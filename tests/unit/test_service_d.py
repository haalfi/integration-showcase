"""Unit tests for Service D dispatch_shipment activity.

Reads payment-receipt blobs (built directly in the test) and asserts the
DB row + confirmation blob contents. Uses MemoryBackend + a ``tmp_path``-
backed SQLite file -- each activity call opens and closes its own real
connection.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

import pytest
from remote_store import Store
from remote_store.backends import MemoryBackend

import integration_showcase.shared.blob as blob_module
import integration_showcase.shared.db as db_module
from integration_showcase.service_d.activities import dispatch_shipment
from integration_showcase.shared.blob import upload
from integration_showcase.shared.envelope import Envelope


@pytest.fixture()
def memory_store(monkeypatch: pytest.MonkeyPatch) -> Store:
    s = Store(MemoryBackend())

    @contextmanager
    def _factory() -> Generator[Store, None, None]:
        yield s

    monkeypatch.setattr(blob_module, "_store_factory", _factory)
    return s


@pytest.fixture()
def db_conn(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Generator[sqlite3.Connection, None, None]:
    db_file = tmp_path / "service_d.db"

    def _factory(_path: str) -> sqlite3.Connection:
        return sqlite3.connect(str(db_file))

    monkeypatch.setattr(db_module, "_connect_factory", _factory)
    monkeypatch.setenv("SERVICE_D_DB_PATH", str(db_file))

    viewer = sqlite3.connect(str(db_file))
    viewer.row_factory = sqlite3.Row
    try:
        yield viewer
    finally:
        viewer.close()


def _make_payment_envelope(charge_id: str = "ch-fixture", tx: str = "tx-001") -> Envelope:
    """Build an envelope as if charge_payment had just produced its receipt blob."""
    receipt = json.dumps(
        {
            "business_tx_id": tx,
            "charge_id": charge_id,
            "amount_cents": 4200,
            "status": "CHARGED",
            "charged_at": "2026-04-16T00:00:00+00:00",
        },
        sort_keys=True,
    ).encode()
    ref = upload(receipt, f"workflows/{tx}/charge-payment.json")
    return Envelope(
        workflow_id=f"order-{tx}",
        run_id="",
        business_tx_id=tx,
        step_id="charge-payment",
        payload_ref=ref,
        traceparent="",
        idempotency_key=Envelope.make_idempotency_key(tx, "charge-payment"),
    )


class TestDispatchShipment:
    async def test_writes_shipment_row_and_uploads_confirmation(
        self, memory_store: Store, db_conn: sqlite3.Connection
    ) -> None:
        env = _make_payment_envelope(charge_id="ch-abc")
        ref = await dispatch_shipment(env)

        row = db_conn.execute(
            "SELECT business_tx_id, shipment_id, charge_id"
            " FROM shipments WHERE idempotency_key = ?",
            (env.idempotency_key,),
        ).fetchone()
        assert row is not None
        assert row["business_tx_id"] == env.business_tx_id
        assert row["shipment_id"].startswith("shp-")
        assert row["charge_id"] == "ch-abc"

        result = json.loads(memory_store.read_bytes(ref.blob_url))
        assert ref.blob_url == f"workflows/{env.business_tx_id}/dispatch-shipment.json"
        assert result["shipment_id"] == row["shipment_id"]
        assert result["charge_id"] == "ch-abc"

    async def test_retry_is_idempotent_with_stable_blobref(
        self, memory_store: Store, db_conn: sqlite3.Connection
    ) -> None:
        env = _make_payment_envelope()
        first = await dispatch_shipment(env)
        second = await dispatch_shipment(env)

        count = db_conn.execute("SELECT COUNT(*) FROM shipments").fetchone()[0]
        assert count == 1
        assert first == second
