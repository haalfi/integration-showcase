"""Inventory reservation and compensation activities (Service B).

Side-effect state lives in a private SQLite database keyed by
``envelope.idempotency_key`` so Temporal retries are no-ops. The result
blob's contents are reconstructed from the persisted row, which keeps the
returned ``BlobRef.sha256`` stable across retries even though the
generated reservation_id is a fresh uuid on the first attempt.

These activities are ``def`` (not ``async def``) because they do blocking
I/O (blob HTTP + SQLite). The worker registers them with an
``activity_executor`` ``ThreadPoolExecutor`` so blocking calls don't
starve the Temporal event loop.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime

from temporalio import activity

from integration_showcase.shared import blob, db
from integration_showcase.shared.envelope import BlobRef, Envelope
from integration_showcase.shared.otel import instrument_activity

_DB_PATH_ENV = "SERVICE_B_DB_PATH"
_DEFAULT_DB_PATH = "./tmp/service_b.db"

_DDL = """
CREATE TABLE IF NOT EXISTS inventory_reservations (
    idempotency_key TEXT PRIMARY KEY,
    business_tx_id  TEXT NOT NULL,
    reservation_id  TEXT NOT NULL,
    items           TEXT NOT NULL,
    reserved_at     TEXT NOT NULL,
    released_at     TEXT
)
"""


def _db_path() -> str:
    path = os.environ.get(_DB_PATH_ENV, _DEFAULT_DB_PATH)
    parent = os.path.dirname(path)
    if parent and path != ":memory:":
        os.makedirs(parent, exist_ok=True)
    return path


@activity.defn(name="reserve_inventory")
@instrument_activity
def reserve_inventory(envelope: Envelope) -> BlobRef:
    """Reserve inventory for the order. Idempotent per ``envelope.idempotency_key``.

    Downloads the input payload from blob storage, inserts (or fetches on
    retry) the reservation row, and uploads the canonical result blob.
    """
    input_bytes = blob.download(envelope.payload_ref)
    input_data = json.loads(input_bytes)

    with db.connect(_db_path()) as conn:
        conn.execute(_DDL)
        # INSERT OR IGNORE: if two concurrent first-attempt calls race here,
        # the loser's INSERT is silently dropped (rather than raising
        # IntegrityError). The unconditional re-read below means both callers
        # end up reading the winner's row, so result bytes and BlobRef.sha256
        # are identical regardless of which call "won".
        conn.execute(
            "INSERT OR IGNORE INTO inventory_reservations"
            " (idempotency_key, business_tx_id, reservation_id, items, reserved_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                envelope.idempotency_key,
                envelope.business_tx_id,
                f"res-{uuid.uuid4()}",
                json.dumps(list(input_data["items"])),
                datetime.now(UTC).isoformat(),
            ),
        )
        row = conn.execute(
            "SELECT reservation_id, items, reserved_at"
            " FROM inventory_reservations WHERE idempotency_key = ?",
            (envelope.idempotency_key,),
        ).fetchone()
        reservation_id = row["reservation_id"]
        items = json.loads(row["items"])
        reserved_at = row["reserved_at"]

    result = {
        "business_tx_id": envelope.business_tx_id,
        "reservation_id": reservation_id,
        "items": items,
        "reserved_at": reserved_at,
    }
    result_bytes = json.dumps(result, sort_keys=True).encode()
    blob_path = f"workflows/{envelope.business_tx_id}/reserve-inventory.json"
    return blob.upload(result_bytes, blob_path)


@activity.defn(name="compensate_reserve_inventory")
@instrument_activity
def compensate_reserve_inventory(envelope: Envelope) -> BlobRef:
    """Release the inventory reservation for ``envelope.business_tx_id``.

    The workflow advances the envelope to ``step_id="compensate.reserve-inventory"``
    (DESIGN.md §Compensation rules) before invoking this activity, so
    ``envelope.idempotency_key`` here is the canonical compensation key
    ``"{business_tx_id}:compensate.reserve-inventory:{schema_version}"``.

    Lookup of the reservation itself uses ``business_tx_id`` because the
    original reservation was stored under the forward-step idempotency key
    (e.g. ``"{business_tx_id}:start:1.0"``). See DESIGN.md §Compensation
    rules for the spec carve-out that permits this. Idempotent under
    concurrent retries: the ``UPDATE ... WHERE released_at IS NULL``
    serializes via SQLite's row locks, and the post-UPDATE re-read
    returns the winning ``released_at`` so every caller produces identical
    result bytes.

    Edge case: if no prior reservation row exists (orphan compensation),
    a tombstone row is inserted under ``envelope.idempotency_key`` so the
    same-named retry no-ops and returns the same canonical blob. The blob
    carries ``"kind": "orphan_tombstone"`` to distinguish it from a real
    release in trace/audit consumers.
    """
    with db.connect(_db_path()) as conn:
        conn.execute(_DDL)
        row = conn.execute(
            "SELECT reservation_id, items, reserved_at, released_at"
            " FROM inventory_reservations WHERE business_tx_id = ?"
            " ORDER BY reserved_at DESC LIMIT 1",
            (envelope.business_tx_id,),
        ).fetchone()

        if row is None:
            reservation_id = "orphan"
            items: list[str] = []
            released_at = datetime.now(UTC).isoformat()
            conn.execute(
                "INSERT INTO inventory_reservations"
                " (idempotency_key, business_tx_id, reservation_id,"
                "  items, reserved_at, released_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    envelope.idempotency_key,
                    envelope.business_tx_id,
                    reservation_id,
                    json.dumps(items),
                    released_at,
                    released_at,
                ),
            )
            kind = "orphan_tombstone"
        else:
            reservation_id = row["reservation_id"]
            items = json.loads(row["items"])
            if row["released_at"] is None:
                conn.execute(
                    "UPDATE inventory_reservations SET released_at = ?"
                    " WHERE business_tx_id = ? AND released_at IS NULL",
                    (datetime.now(UTC).isoformat(), envelope.business_tx_id),
                )
            # Re-read under the same transaction to pick up whichever
            # timestamp won a concurrent-compensation race; this keeps all
            # callers' result bytes (and thus BlobRef.sha256) identical.
            persisted = conn.execute(
                "SELECT released_at FROM inventory_reservations"
                " WHERE business_tx_id = ? ORDER BY reserved_at DESC LIMIT 1",
                (envelope.business_tx_id,),
            ).fetchone()
            released_at = persisted["released_at"]
            # An earlier retry may have written an orphan tombstone; preserve
            # its ``kind`` so a second orphan-compensation call produces the
            # same canonical blob as the first.
            kind = "orphan_tombstone" if reservation_id == "orphan" else "released"

    result = {
        "business_tx_id": envelope.business_tx_id,
        "reservation_id": reservation_id,
        "items": items,
        "kind": kind,
        "released": True,
        "released_at": released_at,
    }
    result_bytes = json.dumps(result, sort_keys=True).encode()
    blob_path = f"workflows/{envelope.business_tx_id}/compensate.reserve-inventory.json"
    return blob.upload(result_bytes, blob_path)
