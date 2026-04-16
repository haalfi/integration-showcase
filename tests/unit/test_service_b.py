"""Unit tests for Service B activities (inventory + compensation).

Uses MemoryBackend for blob I/O and a shared :memory: SQLite connection
for state -- no Docker, no filesystem, no network.
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
from integration_showcase.service_b.activities import (
    compensate_reserve_inventory,
    reserve_inventory,
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
    monkeypatch.setenv("SERVICE_B_DB_PATH", ":memory:")
    try:
        yield conn
    finally:
        conn.close()


def _make_envelope(items: list[str], tx: str = "tx-001") -> Envelope:
    payload = json.dumps({"items": items, "customer_id": "cust-x"}).encode()
    ref = upload(payload, f"workflows/{tx}/input.json")
    return Envelope(
        workflow_id=f"order-{tx}",
        run_id="",
        business_tx_id=tx,
        step_id="start",
        payload_ref=ref,
        traceparent="",
        idempotency_key=Envelope.make_idempotency_key(tx, "start"),
    )


class TestReserveInventory:
    async def test_writes_db_row_and_uploads_result_blob(
        self, memory_store: Store, db_conn: sqlite3.Connection
    ) -> None:
        env = _make_envelope(["widget-1", "gadget-2"])
        ref = await reserve_inventory(env)

        row = db_conn.execute(
            "SELECT business_tx_id, reservation_id, items, released_at"
            " FROM inventory_reservations WHERE idempotency_key = ?",
            (env.idempotency_key,),
        ).fetchone()
        assert row is not None
        assert row["business_tx_id"] == env.business_tx_id
        assert row["reservation_id"].startswith("res-")
        assert row["released_at"] is None

        assert ref.blob_url == f"workflows/{env.business_tx_id}/reserve-inventory.json"
        result_bytes = memory_store.read_bytes(ref.blob_url)
        result = json.loads(result_bytes)
        assert result["business_tx_id"] == env.business_tx_id
        assert result["items"] == ["widget-1", "gadget-2"]
        assert result["reservation_id"] == row["reservation_id"]

    async def test_retry_is_idempotent_with_stable_blobref(
        self, memory_store: Store, db_conn: sqlite3.Connection
    ) -> None:
        env = _make_envelope(["w"])
        first = await reserve_inventory(env)
        second = await reserve_inventory(env)

        count = db_conn.execute("SELECT COUNT(*) FROM inventory_reservations").fetchone()[0]
        assert count == 1
        # Same canonical bytes -> same sha256 -> equal BlobRef.
        assert first == second

    async def test_distinct_business_tx_ids_create_distinct_rows(
        self, memory_store: Store, db_conn: sqlite3.Connection
    ) -> None:
        env_a = _make_envelope(["w"], tx="tx-a")
        env_b = _make_envelope(["w"], tx="tx-b")
        await reserve_inventory(env_a)
        await reserve_inventory(env_b)
        rows = db_conn.execute(
            "SELECT business_tx_id FROM inventory_reservations ORDER BY business_tx_id"
        ).fetchall()
        assert [r["business_tx_id"] for r in rows] == ["tx-a", "tx-b"]


class TestCompensateReserveInventory:
    async def test_marks_existing_reservation_released(
        self, memory_store: Store, db_conn: sqlite3.Connection
    ) -> None:
        env = _make_envelope(["w"])
        reserve_ref = await reserve_inventory(env)
        # Compensation receives the envelope advanced to "reserve-inventory"
        # (mirrors workflow/order.py: it passes inventory_envelope).
        comp_env = env.advance("reserve-inventory", reserve_ref)

        ref = await compensate_reserve_inventory(comp_env)

        row = db_conn.execute(
            "SELECT released_at FROM inventory_reservations WHERE business_tx_id = ?",
            (env.business_tx_id,),
        ).fetchone()
        assert row["released_at"] is not None

        assert ref.blob_url == (f"workflows/{env.business_tx_id}/compensate.reserve-inventory.json")
        result = json.loads(memory_store.read_bytes(ref.blob_url))
        assert result["released"] is True
        assert result["released_at"] == row["released_at"]

    async def test_double_compensation_preserves_first_released_at(
        self, memory_store: Store, db_conn: sqlite3.Connection
    ) -> None:
        env = _make_envelope(["w"])
        reserve_ref = await reserve_inventory(env)
        comp_env = env.advance("reserve-inventory", reserve_ref)

        first = await compensate_reserve_inventory(comp_env)
        first_released = db_conn.execute(
            "SELECT released_at FROM inventory_reservations WHERE business_tx_id = ?",
            (env.business_tx_id,),
        ).fetchone()["released_at"]

        second = await compensate_reserve_inventory(comp_env)
        second_released = db_conn.execute(
            "SELECT released_at FROM inventory_reservations WHERE business_tx_id = ?",
            (env.business_tx_id,),
        ).fetchone()["released_at"]

        assert first_released == second_released
        assert first == second

    async def test_orphan_compensation_writes_tombstone_row(
        self, memory_store: Store, db_conn: sqlite3.Connection
    ) -> None:
        # No prior reserve_inventory call -- compensation runs orphaned.
        env = _make_envelope(["w"])
        comp_env = env.advance("reserve-inventory", env.payload_ref)

        ref = await compensate_reserve_inventory(comp_env)

        row = db_conn.execute(
            "SELECT reservation_id, released_at FROM inventory_reservations"
            " WHERE business_tx_id = ?",
            (env.business_tx_id,),
        ).fetchone()
        assert row["reservation_id"] == "orphan"
        assert row["released_at"] is not None

        # Retry of orphan compensation must return the same BlobRef.
        again = await compensate_reserve_inventory(comp_env)
        assert ref == again
