"""Payment charging activity (Service C).

Reads the inventory result blob, charges the customer (POC: $42 per item),
persists payment state in a private SQLite DB keyed on
``envelope.idempotency_key``, and uploads a receipt blob.

Set ``FORCE_PAYMENT_FAILURE=true`` to trigger ``InsufficientFundsError``,
which is non-retryable in ``OrderWorkflow`` and drives compensation.

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


class InsufficientFundsError(Exception):
    """Non-retryable: payment declined due to insufficient funds."""


_DB_PATH_ENV = "SERVICE_C_DB_PATH"
_DEFAULT_DB_PATH = "./tmp/service_c.db"

# POC pricing: a flat per-item rate in cents.
_PRICE_PER_ITEM_CENTS = 4200

_DDL = """
CREATE TABLE IF NOT EXISTS payments (
    idempotency_key TEXT PRIMARY KEY,
    business_tx_id  TEXT NOT NULL,
    charge_id       TEXT NOT NULL,
    amount_cents    INTEGER NOT NULL,
    status          TEXT NOT NULL,
    charged_at      TEXT NOT NULL
)
"""


def _db_path() -> str:
    path = os.environ.get(_DB_PATH_ENV, _DEFAULT_DB_PATH)
    parent = os.path.dirname(path)
    if parent and path != ":memory:":
        os.makedirs(parent, exist_ok=True)
    return path


@activity.defn(name="charge_payment")
def charge_payment(envelope: Envelope) -> BlobRef:
    """Charge payment. Idempotent per ``envelope.idempotency_key``.

    The failure check runs before any I/O so a declined charge is a
    deterministic ``InsufficientFundsError`` regardless of the state of
    the blob store or the local DB. ``InsufficientFundsError`` is the
    workflow's non-retryable signal to start compensation.
    """
    if os.environ.get("FORCE_PAYMENT_FAILURE", "").lower() == "true":
        raise InsufficientFundsError(
            f"Payment declined for business_tx_id={envelope.business_tx_id}"
        )

    input_bytes = blob.download(envelope.payload_ref)
    input_data = json.loads(input_bytes)
    items = list(input_data["items"])
    amount_cents = len(items) * _PRICE_PER_ITEM_CENTS

    with db.connect(_db_path()) as conn:
        conn.execute(_DDL)
        row = conn.execute(
            "SELECT charge_id, amount_cents, status, charged_at"
            " FROM payments WHERE idempotency_key = ?",
            (envelope.idempotency_key,),
        ).fetchone()

        if row is None:
            charge_id = f"ch-{uuid.uuid4()}"
            charged_at = datetime.now(UTC).isoformat()
            conn.execute(
                "INSERT INTO payments"
                " (idempotency_key, business_tx_id, charge_id, amount_cents, status, charged_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    envelope.idempotency_key,
                    envelope.business_tx_id,
                    charge_id,
                    amount_cents,
                    "CHARGED",
                    charged_at,
                ),
            )
        else:
            charge_id = row["charge_id"]
            amount_cents = row["amount_cents"]
            charged_at = row["charged_at"]

    result = {
        "business_tx_id": envelope.business_tx_id,
        "charge_id": charge_id,
        "amount_cents": amount_cents,
        "status": "CHARGED",
        "charged_at": charged_at,
    }
    result_bytes = json.dumps(result, sort_keys=True).encode()
    blob_path = f"workflows/{envelope.business_tx_id}/charge-payment.json"
    return blob.upload(result_bytes, blob_path)
