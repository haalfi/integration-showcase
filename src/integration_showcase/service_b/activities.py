"""Inventory reservation and compensation activities (Service B).

Side-effect state lives in a private SQLite database keyed by
``envelope.idempotency_key`` so Temporal retries are no-ops. The result
blob's contents are reconstructed from the persisted row, which keeps the
returned ``BlobRef.sha256`` stable across retries even though the
generated reservation_id is a fresh uuid on the first attempt.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import UTC, datetime

from temporalio import activity

from integration_showcase.shared import blob, db
from integration_showcase.shared.envelope import BlobRef, Envelope

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


def _get_conn() -> sqlite3.Connection:
    path = os.environ.get(_DB_PATH_ENV, _DEFAULT_DB_PATH)
    parent = os.path.dirname(path)
    if parent and path != ":memory:":
        os.makedirs(parent, exist_ok=True)
    conn = db.connect(path)
    conn.execute(_DDL)
    conn.commit()
    return conn


@activity.defn(name="reserve_inventory")
async def reserve_inventory(envelope: Envelope) -> BlobRef:
    """Reserve inventory for the order. Idempotent per ``envelope.idempotency_key``.

    Downloads the input payload from blob storage, inserts (or fetches on
    retry) the reservation row, and uploads the canonical result blob.
    """
    input_bytes = blob.download(envelope.payload_ref)
    input_data = json.loads(input_bytes)

    conn = _get_conn()
    row = conn.execute(
        "SELECT reservation_id, items, reserved_at"
        " FROM inventory_reservations WHERE idempotency_key = ?",
        (envelope.idempotency_key,),
    ).fetchone()

    if row is None:
        reservation_id = f"res-{uuid.uuid4()}"
        items = list(input_data["items"])
        reserved_at = datetime.now(UTC).isoformat()
        conn.execute(
            "INSERT INTO inventory_reservations"
            " (idempotency_key, business_tx_id, reservation_id, items, reserved_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                envelope.idempotency_key,
                envelope.business_tx_id,
                reservation_id,
                json.dumps(items),
                reserved_at,
            ),
        )
        conn.commit()
    else:
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
    path = f"workflows/{envelope.business_tx_id}/reserve-inventory.json"
    return blob.upload(result_bytes, path)


@activity.defn(name="compensate_reserve_inventory")
async def compensate_reserve_inventory(envelope: Envelope) -> BlobRef:
    """Release the inventory reservation for ``envelope.business_tx_id``.

    Lookup is by ``business_tx_id`` (not ``idempotency_key``) because the
    compensation activity sees a different envelope step than the original
    reservation. Idempotent: once ``released_at`` is set, subsequent calls
    preserve it.

    Edge case: if no prior reservation row exists (orphan compensation),
    a tombstone row is inserted so retries return the same canonical blob.
    """
    conn = _get_conn()
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
            " (idempotency_key, business_tx_id, reservation_id, items, reserved_at, released_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                f"orphan:{envelope.business_tx_id}",
                envelope.business_tx_id,
                reservation_id,
                json.dumps(items),
                released_at,
                released_at,
            ),
        )
        conn.commit()
    elif row["released_at"] is None:
        reservation_id = row["reservation_id"]
        items = json.loads(row["items"])
        released_at = datetime.now(UTC).isoformat()
        conn.execute(
            "UPDATE inventory_reservations SET released_at = ?"
            " WHERE business_tx_id = ? AND released_at IS NULL",
            (released_at, envelope.business_tx_id),
        )
        conn.commit()
    else:
        reservation_id = row["reservation_id"]
        items = json.loads(row["items"])
        released_at = row["released_at"]

    result = {
        "business_tx_id": envelope.business_tx_id,
        "reservation_id": reservation_id,
        "items": items,
        "released": True,
        "released_at": released_at,
    }
    result_bytes = json.dumps(result, sort_keys=True).encode()
    path = f"workflows/{envelope.business_tx_id}/compensate.reserve-inventory.json"
    return blob.upload(result_bytes, path)
