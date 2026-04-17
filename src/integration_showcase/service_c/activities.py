"""Payment charging and refund activities (Service C).

Reads the inventory result blob, charges the customer (POC: $42 per item),
persists payment state in a private SQLite DB keyed on
``envelope.idempotency_key``, and uploads a receipt blob.

Set ``FORCE_PAYMENT_FAILURE=true`` to trigger ``InsufficientFundsError``,
which is non-retryable in ``OrderWorkflow`` and drives compensation.

``refund_payment`` compensates a prior ``charge_payment``. It follows the
same orphan-tombstone pattern as ``compensate_reserve_inventory``: if no
payment row is found the activity inserts a tombstone so retries are no-ops.

These activities are ``def`` (not ``async def``) because they do blocking
I/O; the worker registers them with an ``activity_executor``
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

_REFUNDS_DDL = """
CREATE TABLE IF NOT EXISTS payment_refunds (
    idempotency_key TEXT PRIMARY KEY,
    business_tx_id  TEXT NOT NULL,
    charge_id       TEXT NOT NULL,
    kind            TEXT NOT NULL,
    refunded_at     TEXT NOT NULL
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
    return blob.upload(result_bytes, blob_path, metadata=envelope.blob_metadata())


@activity.defn(name="refund_payment")
@instrument_activity
def refund_payment(envelope: Envelope) -> BlobRef:
    """Refund a prior payment for ``envelope.business_tx_id``.

    The workflow advances the envelope to ``step_id="compensate.charge-payment"``
    (DESIGN.md §Compensation rules) before invoking this activity, so
    ``envelope.idempotency_key`` here is the canonical compensation key
    ``"{business_tx_id}:compensate.charge-payment:{schema_version}"``.

    Lookup of the original payment uses ``business_tx_id`` because the
    payment was stored under the forward-step idempotency key. Idempotent
    under concurrent retries: ``INSERT OR IGNORE`` into ``payment_refunds``
    silently drops racing duplicates; the unconditional re-read returns the
    canonical row so every caller produces identical result bytes.

    Edge case: if no prior payment row exists (orphan compensation), a
    tombstone row is inserted under ``envelope.idempotency_key`` so the
    same-key retry no-ops. The blob carries ``"kind": "orphan_tombstone"``.
    """
    with db.connect(_db_path()) as conn:
        conn.execute(_DDL)
        conn.execute(_REFUNDS_DDL)

        payment_row = conn.execute(
            "SELECT charge_id FROM payments WHERE business_tx_id = ?"
            " ORDER BY charged_at DESC LIMIT 1",
            (envelope.business_tx_id,),
        ).fetchone()

        if payment_row is None:
            charge_id = "orphan"
            kind = "orphan_tombstone"
        else:
            charge_id = payment_row["charge_id"]
            kind = "refunded"

        refunded_at = datetime.now(UTC).isoformat()
        conn.execute(
            "INSERT OR IGNORE INTO payment_refunds"
            " (idempotency_key, business_tx_id, charge_id, kind, refunded_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (envelope.idempotency_key, envelope.business_tx_id, charge_id, kind, refunded_at),
        )
        # Re-read under the same transaction to pick up whichever row won
        # a concurrent-refund race; keeps all callers' result bytes identical.
        row = conn.execute(
            "SELECT charge_id, kind, refunded_at FROM payment_refunds WHERE idempotency_key = ?",
            (envelope.idempotency_key,),
        ).fetchone()
        charge_id = row["charge_id"]
        kind = row["kind"]
        refunded_at = row["refunded_at"]

    result = {
        "business_tx_id": envelope.business_tx_id,
        "charge_id": charge_id,
        "kind": kind,
        "refunded": True,
        "refunded_at": refunded_at,
    }
    result_bytes = json.dumps(result, sort_keys=True).encode()
    blob_path = f"workflows/{envelope.business_tx_id}/compensate.charge-payment.json"
    return blob.upload(result_bytes, blob_path, metadata=envelope.blob_metadata())
