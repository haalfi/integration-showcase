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
from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
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
from integration_showcase.workflow.envelopes import (
    compensate_reserve_inventory_envelope,
)


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
    # PRAGMA journal_mode = WAL is issued once per path per process; this
    # fixture reuses paths across tests, so reset the bootstrap cache.
    db_module._reset_bootstrap_cache()

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

    Reuses the production helper from ``workflow.envelopes`` so the step
    promotion contract has a single source of truth.
    """
    inventory_env = env.advance("reserve-inventory", reserve_ref)
    return compensate_reserve_inventory_envelope(inventory_env, reserve_ref)


class TestReserveInventory:
    def test_writes_db_row_and_uploads_result_blob(
        self, memory_store: Store, db_conn: sqlite3.Connection
    ) -> None:
        env = _make_envelope(["widget-1", "gadget-2"])
        ref = reserve_inventory(env)

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

    def test_retry_is_idempotent_with_stable_blobref(
        self, memory_store: Store, db_conn: sqlite3.Connection
    ) -> None:
        env = _make_envelope(["w"])
        first = reserve_inventory(env)
        second = reserve_inventory(env)

        count = db_conn.execute("SELECT COUNT(*) FROM inventory_reservations").fetchone()[0]
        assert count == 1
        # Same canonical bytes -> same sha256 -> equal BlobRef.
        assert first == second

    def test_distinct_business_tx_ids_create_distinct_rows(
        self, memory_store: Store, db_conn: sqlite3.Connection
    ) -> None:
        env_a = _make_envelope(["w"], tx="tx-a")
        env_b = _make_envelope(["w"], tx="tx-b")
        reserve_inventory(env_a)
        reserve_inventory(env_b)
        rows = db_conn.execute(
            "SELECT business_tx_id FROM inventory_reservations ORDER BY business_tx_id"
        ).fetchall()
        assert [r["business_tx_id"] for r in rows] == ["tx-a", "tx-b"]


class TestCompensateReserveInventory:
    def test_marks_existing_reservation_released(
        self, memory_store: Store, db_conn: sqlite3.Connection
    ) -> None:
        env = _make_envelope(["w"])
        reserve_ref = reserve_inventory(env)
        comp_env = _compensation_envelope(env, reserve_ref)
        # Sanity: the compensation envelope carries the canonical spec key.
        assert comp_env.idempotency_key == (
            f"{env.business_tx_id}:compensate.reserve-inventory:1.0"
        )

        ref = compensate_reserve_inventory(comp_env)

        row = db_conn.execute(
            "SELECT released_at FROM inventory_reservations WHERE business_tx_id = ?",
            (env.business_tx_id,),
        ).fetchone()
        assert row["released_at"] is not None

        assert ref.blob_url == (f"workflows/{env.business_tx_id}/compensate.reserve-inventory.json")
        result = json.loads(memory_store.read_bytes(ref.blob_url))
        assert result["released"] is True
        assert result["kind"] == "released"
        assert result["released_at"] == row["released_at"]

    def test_double_compensation_preserves_first_released_at(
        self, memory_store: Store, db_conn: sqlite3.Connection
    ) -> None:
        env = _make_envelope(["w"])
        reserve_ref = reserve_inventory(env)
        comp_env = _compensation_envelope(env, reserve_ref)

        first = compensate_reserve_inventory(comp_env)
        first_released = db_conn.execute(
            "SELECT released_at FROM inventory_reservations WHERE business_tx_id = ?",
            (env.business_tx_id,),
        ).fetchone()["released_at"]

        second = compensate_reserve_inventory(comp_env)
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

    def test_orphan_compensation_writes_tombstone_row(
        self, memory_store: Store, db_conn: sqlite3.Connection
    ) -> None:
        # No prior reserve_inventory call -- compensation runs orphaned.
        env = _make_envelope(["w"])
        comp_env = _compensation_envelope(env, env.payload_ref)

        ref = compensate_reserve_inventory(comp_env)

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

        # Blob discriminator lets trace/audit consumers distinguish the
        # orphan tombstone from a real release.
        result = json.loads(memory_store.read_bytes(ref.blob_url))
        assert result["kind"] == "orphan_tombstone"

        # Retry of orphan compensation must return the same BlobRef
        # and must not insert a second row.
        again = compensate_reserve_inventory(comp_env)
        assert ref == again
        count = db_conn.execute(
            "SELECT COUNT(*) FROM inventory_reservations WHERE business_tx_id = ?",
            (env.business_tx_id,),
        ).fetchone()[0]
        assert count == 1


class TestActivitySpanAttributes:
    """instrument_activity must tag the surrounding span with business attrs."""

    def test_reserve_inventory_tags_current_span(
        self,
        memory_store: Store,
        db_conn: sqlite3.Connection,  # noqa: ARG002
        spans: InMemorySpanExporter,
    ) -> None:
        env = _make_envelope(["w"])
        tracer = trace.get_tracer(__name__)
        with tracer.start_as_current_span("RunActivity:reserve_inventory"):
            reserve_inventory(env)

        (recorded,) = [
            s for s in spans.get_finished_spans() if s.name == "RunActivity:reserve_inventory"
        ]
        attrs = recorded.attributes or {}
        assert attrs["business_tx_id"] == env.business_tx_id
        assert attrs["step_id"] == env.step_id
        assert attrs["payload_ref_sha256"] == env.payload_ref.sha256

    def test_compensate_reserve_inventory_tags_current_span(
        self,
        memory_store: Store,  # noqa: ARG002
        db_conn: sqlite3.Connection,  # noqa: ARG002
        spans: InMemorySpanExporter,
    ) -> None:
        # Drive the orphan-tombstone branch: no prior reservation means the
        # compensation activity runs without needing _make_envelope -> reserve
        # setup, so the test isolates the decorator contract from activity
        # internals.
        env = _make_envelope(["w"])
        comp_env = _compensation_envelope(env, env.payload_ref)
        tracer = trace.get_tracer(__name__)
        with tracer.start_as_current_span("RunActivity:compensate_reserve_inventory"):
            compensate_reserve_inventory(comp_env)

        (recorded,) = [
            s
            for s in spans.get_finished_spans()
            if s.name == "RunActivity:compensate_reserve_inventory"
        ]
        attrs = recorded.attributes or {}
        assert attrs["business_tx_id"] == comp_env.business_tx_id
        assert attrs["step_id"] == comp_env.step_id
        assert attrs["payload_ref_sha256"] == comp_env.payload_ref.sha256
