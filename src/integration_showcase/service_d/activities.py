"""Shipment dispatch activity (Service D).

Reads the payment receipt blob, dispatches a shipment, persists shipment
state in a private SQLite DB keyed on ``envelope.idempotency_key``, and
uploads a confirmation blob.

This activity is ``def`` (not ``async def``) because it does blocking
I/O; the worker registers it with an ``activity_executor``
``ThreadPoolExecutor`` so blocking calls don't starve the Temporal event
loop.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime

from temporalio import activity

from integration_showcase.shared import blob, db
from integration_showcase.shared.envelope import BlobRef, Envelope

_DB_PATH_ENV = "SERVICE_D_DB_PATH"
_DEFAULT_DB_PATH = "./tmp/service_d.db"

_DDL = """
CREATE TABLE IF NOT EXISTS shipments (
    idempotency_key TEXT PRIMARY KEY,
    business_tx_id  TEXT NOT NULL,
    shipment_id     TEXT NOT NULL,
    charge_id       TEXT NOT NULL,
    dispatched_at   TEXT NOT NULL
)
"""


def _db_path() -> str:
    path = os.environ.get(_DB_PATH_ENV, _DEFAULT_DB_PATH)
    parent = os.path.dirname(path)
    if parent and path != ":memory:":
        os.makedirs(parent, exist_ok=True)
    return path


@activity.defn(name="dispatch_shipment")
def dispatch_shipment(envelope: Envelope) -> BlobRef:
    """Dispatch shipment. Idempotent per ``envelope.idempotency_key``."""
    input_bytes = blob.download(envelope.payload_ref)
    input_data = json.loads(input_bytes)
    charge_id = input_data["charge_id"]

    with db.connect(_db_path()) as conn:
        conn.execute(_DDL)
        # INSERT OR IGNORE: concurrent first-attempt calls race silently;
        # the unconditional re-read returns whichever row was persisted.
        conn.execute(
            "INSERT OR IGNORE INTO shipments"
            " (idempotency_key, business_tx_id, shipment_id, charge_id, dispatched_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                envelope.idempotency_key,
                envelope.business_tx_id,
                f"shp-{uuid.uuid4()}",
                charge_id,
                datetime.now(UTC).isoformat(),
            ),
        )
        row = conn.execute(
            "SELECT shipment_id, charge_id, dispatched_at FROM shipments WHERE idempotency_key = ?",
            (envelope.idempotency_key,),
        ).fetchone()
        shipment_id = row["shipment_id"]
        charge_id = row["charge_id"]
        dispatched_at = row["dispatched_at"]

    result = {
        "business_tx_id": envelope.business_tx_id,
        "shipment_id": shipment_id,
        "charge_id": charge_id,
        "dispatched_at": dispatched_at,
    }
    result_bytes = json.dumps(result, sort_keys=True).encode()
    blob_path = f"workflows/{envelope.business_tx_id}/dispatch-shipment.json"
    return blob.upload(result_bytes, blob_path)
