"""Unit tests for Service B activities (inventory + compensation).

Uses MemoryBackend for blob I/O and a ``tmp_path``-backed SQLite file for
state. Each activity call opens and closes its own real connection
(matching production and the multi-worker reality), while a separate
viewer connection lets the test inspect persisted state.
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
from integration_showcase.service_b.activities import (
    compensate_reserve_inventory,
    reserve_inventory,
)
from integration_showcase.shared.blob import upload
from integration_showcase.shared.envelope import BlobRef, Envelope


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
    db_file = tmp_path / "service_b.db"

    def _factory(_path: str) -> sqlite3.Connection:
        return sqlite3.connect(str(db_file))

    monkeypatch.setattr(db_module, "_connect_factory", _factory)
    monkeypatch.setenv("SERVICE_B_DB_PATH", str(db_file))

    viewer = sqlite3.connect(str(db_file))
    viewer.row_factory = sqlite3.Row
    try:
        yield viewer
    finally:
        viewer.close()


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


def _compensation_envelope(env: Envelope, reserve_ref: BlobRef) -> Envelope:
    """Envelope as produced by ``OrderWorkflow`` before invoking compensation.

    The workflow advances to ``step_id="compensate.reserve-inventory"``
    (DESIGN.md §Compensation rules), which yields the canonical
    compensation ``idempotency_key``.
    """
    # Step promotion: start -> reserve-inventory -> compensate.reserve-inventory.
    inventory_env = env.advance("reserve-inventory", reserve_ref)
    return inventory_env.advance("compensate.reserve-inventory", reserve_ref)


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
        comp_env = _compensation_envelope(env, reserve_ref)
        # Sanity: the compensation envelope carries the canonical spec key.
        assert comp_env.idempotency_key == (
            f"{env.business_tx_id}:compensate.reserve-inventory:1.0"
        )

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
        comp_env = _compensation_envelope(env, reserve_ref)

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

        # Regression guard: a second compensation must NOT insert a second
        # row for the same business_tx_id (would indicate an UPDATE-predicate
        # or transaction-isolation bug between separate worker connections).
        count = db_conn.execute(
            "SELECT COUNT(*) FROM inventory_reservations WHERE business_tx_id = ?",
            (env.business_tx_id,),
        ).fetchone()[0]
        assert count == 1

    async def test_orphan_compensation_writes_tombstone_row(
        self, memory_store: Store, db_conn: sqlite3.Connection
    ) -> None:
        # No prior reserve_inventory call -- compensation runs orphaned.
        env = _make_envelope(["w"])
        comp_env = _compensation_envelope(env, env.payload_ref)

        ref = await compensate_reserve_inventory(comp_env)

        row = db_conn.execute(
            "SELECT idempotency_key, reservation_id, released_at"
            " FROM inventory_reservations WHERE business_tx_id = ?",
            (env.business_tx_id,),
        ).fetchone()
        assert row["reservation_id"] == "orphan"
        assert row["released_at"] is not None
        # Tombstone PK uses the canonical compensation idempotency_key
        # (DESIGN.md §Envelope invariants #4).
        assert row["idempotency_key"] == comp_env.idempotency_key

        # Retry of orphan compensation must return the same BlobRef
        # and must not insert a second row.
        again = await compensate_reserve_inventory(comp_env)
        assert ref == again
        count = db_conn.execute(
            "SELECT COUNT(*) FROM inventory_reservations WHERE business_tx_id = ?",
            (env.business_tx_id,),
        ).fetchone()[0]
        assert count == 1
