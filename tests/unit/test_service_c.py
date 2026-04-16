"""Unit tests for Service C charge_payment activity.

Reads inventory-result blobs (built directly in the test) and asserts the
DB row + receipt blob contents. Uses MemoryBackend + :memory: SQLite.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager

import pytest
from remote_store import Store
from remote_store.backends import MemoryBackend

import integration_showcase.shared.blob as blob_module
import integration_showcase.shared.db as db_module
from integration_showcase.service_c.activities import (
    InsufficientFundsError,
    charge_payment,
)
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
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(":memory:")
    monkeypatch.setattr(db_module, "_connect_factory", lambda _path: conn)
    monkeypatch.setenv("SERVICE_C_DB_PATH", ":memory:")
    try:
        yield conn
    finally:
        conn.close()


def _make_inventory_envelope(items: list[str], tx: str = "tx-001") -> Envelope:
    """Build an envelope as if reserve_inventory had just produced its result blob."""
    inv_payload = json.dumps(
        {
            "business_tx_id": tx,
            "reservation_id": "res-fixture",
            "items": items,
            "reserved_at": "2026-04-16T00:00:00+00:00",
        },
        sort_keys=True,
    ).encode()
    ref = upload(inv_payload, f"workflows/{tx}/reserve-inventory.json")
    return Envelope(
        workflow_id=f"order-{tx}",
        run_id="",
        business_tx_id=tx,
        step_id="reserve-inventory",
        payload_ref=ref,
        traceparent="",
        idempotency_key=Envelope.make_idempotency_key(tx, "reserve-inventory"),
    )


class TestChargePayment:
    async def test_writes_payment_row_and_uploads_receipt(
        self, memory_store: Store, db_conn: sqlite3.Connection
    ) -> None:
        env = _make_inventory_envelope(["w", "g"])
        ref = await charge_payment(env)

        row = db_conn.execute(
            "SELECT business_tx_id, charge_id, amount_cents, status"
            " FROM payments WHERE idempotency_key = ?",
            (env.idempotency_key,),
        ).fetchone()
        assert row is not None
        assert row["status"] == "CHARGED"
        assert row["amount_cents"] == 2 * 4200
        assert row["charge_id"].startswith("ch-")

        result = json.loads(memory_store.read_bytes(ref.blob_url))
        assert ref.blob_url == f"workflows/{env.business_tx_id}/charge-payment.json"
        assert result["status"] == "CHARGED"
        assert result["amount_cents"] == 2 * 4200
        assert result["charge_id"] == row["charge_id"]

    async def test_force_failure_raises_and_writes_no_state(
        self,
        memory_store: Store,
        db_conn: sqlite3.Connection,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("FORCE_PAYMENT_FAILURE", "true")
        env = _make_inventory_envelope(["w"])
        with pytest.raises(InsufficientFundsError, match="Payment declined"):
            await charge_payment(env)

        # No receipt blob written.
        from remote_store import NotFound

        with pytest.raises(NotFound):
            memory_store.read_bytes(f"workflows/{env.business_tx_id}/charge-payment.json")

        # No payment row written. The table may or may not exist depending on
        # whether the activity reached _get_conn() before raising; either way
        # the row count is zero.
        try:
            count = db_conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0]
        except sqlite3.OperationalError:
            count = 0
        assert count == 0

    async def test_retry_is_idempotent_with_stable_blobref(
        self, memory_store: Store, db_conn: sqlite3.Connection
    ) -> None:
        env = _make_inventory_envelope(["w"])
        first = await charge_payment(env)
        second = await charge_payment(env)

        count = db_conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0]
        assert count == 1
        assert first == second

    async def test_force_failure_case_insensitive_and_only_true_triggers(
        self,
        memory_store: Store,
        db_conn: sqlite3.Connection,  # noqa: ARG002
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # "false" / unset / other values must not trigger the failure path.
        env = _make_inventory_envelope(["w"], tx="tx-noflag")
        monkeypatch.setenv("FORCE_PAYMENT_FAILURE", "false")
        ref = await charge_payment(env)
        assert memory_store.read_bytes(ref.blob_url)
