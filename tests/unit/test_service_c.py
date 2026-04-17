"""Unit tests for Service C charge_payment activity.

Reads inventory-result blobs (built directly in the test) and asserts the
DB row + receipt blob contents. Uses MemoryBackend + a ``tmp_path``-backed
SQLite file -- each activity call opens and closes its own real connection.
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
from remote_store import NotFound, Store
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
    monkeypatch.setattr(blob_module, "_metadata_setter", lambda _path, _meta: None)
    return s


@pytest.fixture()
def db_conn(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Generator[sqlite3.Connection, None, None]:
    db_file = tmp_path / "service_c.db"

    def _factory(_path: str) -> sqlite3.Connection:
        return sqlite3.connect(str(db_file))

    monkeypatch.setattr(db_module, "_connect_factory", _factory)
    monkeypatch.setenv("SERVICE_C_DB_PATH", str(db_file))
    db_module._reset_bootstrap_cache()

    viewer = sqlite3.connect(str(db_file))
    viewer.row_factory = sqlite3.Row
    try:
        yield viewer
    finally:
        viewer.close()


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
    def test_writes_payment_row_and_uploads_receipt(
        self, memory_store: Store, db_conn: sqlite3.Connection
    ) -> None:
        env = _make_inventory_envelope(["w", "g"])
        ref = charge_payment(env)

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

    def test_force_failure_raises_after_blob_download_before_db_write(
        self,
        memory_store: Store,
        db_conn: sqlite3.Connection,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``FORCE_PAYMENT_FAILURE`` raises after blob.download but before DB/receipt I/O.

        IS-009: blob.download now precedes the failure check so the demo trace
        in Jaeger includes a ``blob.get`` child under the failed span.
        """
        monkeypatch.setenv("FORCE_PAYMENT_FAILURE", "true")
        env = _make_inventory_envelope(["w"], tx="tx-failure")

        download_calls: list[str] = []
        real_download = blob_module.download

        def _spy_download(ref):  # type: ignore[no-untyped-def]
            download_calls.append(ref.blob_url)
            return real_download(ref)

        monkeypatch.setattr(blob_module, "download", _spy_download)

        with pytest.raises(InsufficientFundsError, match="Payment declined"):
            charge_payment(env)

        # blob.download must have been called before the failure check fires.
        assert download_calls == [env.payload_ref.blob_url], (
            "blob.download was not called on the failure path — "
            "the FORCE_PAYMENT_FAILURE check may have moved back above blob.download"
        )

        # No receipt blob written.
        with pytest.raises(NotFound):
            memory_store.read_bytes(f"workflows/{env.business_tx_id}/charge-payment.json")

        # No payment row written.
        try:
            count = db_conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0]
        except sqlite3.OperationalError:
            count = 0
        assert count == 0

    def test_retry_is_idempotent_with_stable_blobref(
        self, memory_store: Store, db_conn: sqlite3.Connection
    ) -> None:
        env = _make_inventory_envelope(["w"])
        first = charge_payment(env)
        second = charge_payment(env)

        count = db_conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0]
        assert count == 1
        assert first == second

    @pytest.mark.parametrize(
        "flag_value, expect_raise",
        [
            # Truthy: only exact ``true`` after .lower() triggers failure.
            ("true", True),
            ("True", True),
            ("TRUE", True),
            ("tRuE", True),
            # Everything else must NOT trigger -- the activity implements
            # a strict ``.lower() == "true"`` check, not Python truthiness.
            ("false", False),
            ("False", False),
            ("", False),
            ("1", False),
            ("yes", False),
            ("on", False),
        ],
    )
    def test_force_failure_flag_matrix(
        self,
        memory_store: Store,
        db_conn: sqlite3.Connection,  # noqa: ARG002
        monkeypatch: pytest.MonkeyPatch,
        flag_value: str,
        expect_raise: bool,
    ) -> None:
        # Use a distinct business_tx_id per parametrization so the :memory:
        # idempotency key stays unique across cases.
        env = _make_inventory_envelope(["w"], tx=f"tx-{flag_value or 'empty'}")
        monkeypatch.setenv("FORCE_PAYMENT_FAILURE", flag_value)

        if expect_raise:
            with pytest.raises(InsufficientFundsError, match="Payment declined"):
                charge_payment(env)
        else:
            ref = charge_payment(env)
            # Receipt blob is readable: success path ran to completion.
            assert memory_store.read_bytes(ref.blob_url)


class TestActivitySpanAttributes:
    def test_charge_payment_tags_current_span(
        self,
        memory_store: Store,  # noqa: ARG002
        db_conn: sqlite3.Connection,  # noqa: ARG002
        spans: InMemorySpanExporter,
    ) -> None:
        env = _make_inventory_envelope(["w"])
        tracer = trace.get_tracer(__name__)
        with tracer.start_as_current_span("RunActivity:charge_payment"):
            charge_payment(env)

        (recorded,) = [
            s for s in spans.get_finished_spans() if s.name == "RunActivity:charge_payment"
        ]
        attrs = recorded.attributes or {}
        assert attrs["business_tx_id"] == env.business_tx_id
        assert attrs["step_id"] == env.step_id
        assert attrs["payload_ref_sha256"] == env.payload_ref.sha256
