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
from integration_showcase.shared.otel import instrument_activity


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
@instrument_activity
def charge_payment(envelope: Envelope) -> BlobRef:
    """Charge payment. Idempotent per ``envelope.idempotency_key``.

    ``blob.download`` runs before the failure check so that the demo trace
    in Jaeger shows a ``blob.get`` child span under the failed
    ``charge_payment`` span, aligning with §5.2's pattern of a blob GET
    preceding the payment error.  Note: §5.2's full retry-then-fail sequence
    (attempt 1 = gateway_timeout, attempt 2 = insufficient_funds) is tracked
    separately by IS-012.  A blob-store outage will surface as a retryable
    error rather than the deterministic ``InsufficientFundsError``; the
    previous guarantee ("failure check runs before any I/O") no longer holds.
    ``InsufficientFundsError`` remains the workflow's non-retryable signal
    to start compensation when the blob store is healthy.
    """
    input_bytes = blob.download(envelope.payload_ref)
    input_data = json.loads(input_bytes)

    if os.environ.get("FORCE_PAYMENT_FAILURE", "").lower() == "true":
        raise InsufficientFundsError(
            f"Payment declined for business_tx_id={envelope.business_tx_id}"
        )
    items = list(input_data["items"])
    amount_cents = len(items) * _PRICE_PER_ITEM_CENTS

    with db.connect(_db_path()) as conn:
        conn.execute(_DDL)
        # INSERT OR IGNORE: concurrent first-attempt calls race silently;
        # the unconditional re-read returns whichever row was persisted.
        conn.execute(
            "INSERT OR IGNORE INTO payments"
            " (idempotency_key, business_tx_id, charge_id, amount_cents, status, charged_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                envelope.idempotency_key,
                envelope.business_tx_id,
                f"ch-{uuid.uuid4()}",
                amount_cents,
                "CHARGED",
                datetime.now(UTC).isoformat(),
            ),
        )
        row = conn.execute(
            "SELECT charge_id, amount_cents, status, charged_at"
            " FROM payments WHERE idempotency_key = ?",
            (envelope.idempotency_key,),
        ).fetchone()
        charge_id = row["charge_id"]
        amount_cents = row["amount_cents"]
        status = row["status"]
        charged_at = row["charged_at"]

    result = {
        "business_tx_id": envelope.business_tx_id,
        "charge_id": charge_id,
        "amount_cents": amount_cents,
        "status": status,
        "charged_at": charged_at,
    }
    result_bytes = json.dumps(result, sort_keys=True).encode()
    blob_path = f"workflows/{envelope.business_tx_id}/charge-payment.json"
    return blob.upload(result_bytes, blob_path)
