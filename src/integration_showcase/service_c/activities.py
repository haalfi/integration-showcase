"""Payment charging and refund activities (Service C).

Reads the inventory result blob, charges the customer (POC: $42 per item),
persists payment state in a private SQLite DB keyed on
``envelope.idempotency_key``, and uploads a receipt blob.

Set ``FORCE_PAYMENT_FAILURE=true`` to trigger ``InsufficientFundsError``,
which is non-retryable in ``OrderWorkflow`` and drives compensation.

Set ``FORCE_PAYMENT_TRANSIENT_FAILS=N`` to raise a retryable
``PaymentGatewayError`` on the first N attempts; attempt N+1 then either
succeeds or raises ``InsufficientFundsError`` depending on
``FORCE_PAYMENT_FAILURE``.  Demonstrates the §5.2 retry-then-fail sequence
with exponential-backoff spacing visible in Jaeger (IS-012).
**Warning:** N must be strictly less than ``_PAYMENT_RETRY.maximum_attempts``
(currently 3).  Setting ``N >= maximum_attempts`` means the terminal attempt
also raises ``PaymentGatewayError``, which Temporal exhausts retries on; the
workflow still compensates (``except Exception`` catches it) but the root
cause type in the failure history will be ``PaymentGatewayError`` rather than
``InsufficientFundsError``.

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


class PaymentGatewayError(Exception):
    """Retryable: transient gateway failure (timeout, network error, etc.)."""


def _get_attempt() -> int:
    """Return the current Temporal activity attempt number (1-based).

    Module-level seam so unit tests can monkeypatch without a live Temporal context.
    Function form (not a module-level attribute) so
    ``monkeypatch.setattr(module, "_get_attempt", lambda: N)`` replaces the
    entire callable — consistent with how other Temporal-context-dependent
    seams should be structured in this codebase.
    """
    return activity.info().attempt


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
    charged_at      TEXT NOT NULL,
    refunded_at     TEXT
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

    ``blob.download`` runs before both the transient-fail guard
    (``FORCE_PAYMENT_TRANSIENT_FAILS``) and the hard-fail guard
    (``FORCE_PAYMENT_FAILURE``), so that every attempt — including retried
    ones — shows a ``blob.get`` child span in Jaeger beneath the
    ``charge_payment`` span.  This aligns with §5.2's trace shape (blob GET
    precedes the payment decision) on all paths.  Side-effect: on transient-fail
    retries Jaeger shows ``blob.get`` → ``gateway_timeout``; a reader should
    interpret that as "the blob was fetched, then the gateway decision
    fired", not as the blob call causing the timeout.  A blob-store outage on
    any attempt will surface as a retryable error rather than the deterministic
    ``InsufficientFundsError``; the previous guarantee ("failure check runs
    before any I/O") no longer holds.  ``InsufficientFundsError`` remains the
    workflow's non-retryable signal to start compensation when the blob store
    is healthy.
    """
    input_bytes = blob.download(envelope.payload_ref)
    input_data = json.loads(input_bytes)

    try:
        transient_n = max(0, int(os.environ.get("FORCE_PAYMENT_TRANSIENT_FAILS", "0")))
    except ValueError:
        transient_n = 0
    if transient_n > 0:
        attempt = _get_attempt()
        if attempt <= transient_n:
            raise PaymentGatewayError(
                f"gateway_timeout on attempt {attempt} for business_tx_id={envelope.business_tx_id}"
            )

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

    ``blob.download`` runs first to satisfy §Envelope invariants rule 2 and
    produce a ``blob.get`` child span under the activity span in Jaeger.

    Lookup of the prior payment uses ``business_tx_id`` because the payment
    was stored under the forward-step idempotency key (DESIGN.md §Compensation
    rules carve-out). The ``UPDATE ... WHERE refunded_at IS NULL`` achieves
    idempotency: SQLite's database-level write lock ensures only one concurrent
    caller can commit the timestamp; the post-UPDATE re-read returns the winning
    ``refunded_at`` so every caller produces identical result bytes
    (DESIGN.md §Compensation idempotency pattern).

    Edge case: if no prior payment row exists (orphan compensation), a tombstone
    row is inserted into ``payments`` under ``envelope.idempotency_key`` so the
    same-key retry no-ops and returns the same canonical blob. The blob carries
    ``"kind": "orphan_tombstone"`` to distinguish it from a real refund.
    """
    blob.download(envelope.payload_ref)  # satisfies §Envelope invariants; produces blob.get span

    with db.connect(_db_path()) as conn:
        conn.execute(_DDL)

        # Invariant: this activity is only dispatched after charge_payment succeeds, so a
        # CHARGED row always exists in `payments` for this business_tx_id when called in
        # production. The orphan path is a defensive fallback for edge cases (manual replay,
        # tests). A real CHARGED row can never appear *after* an orphan tombstone because the
        # workflow never retries charge_payment once it has started compensation.
        row = conn.execute(
            "SELECT charge_id, refunded_at FROM payments"
            " WHERE business_tx_id = ? ORDER BY charged_at DESC LIMIT 1",
            (envelope.business_tx_id,),
        ).fetchone()

        if row is None:
            now = datetime.now(UTC).isoformat()
            conn.execute(
                "INSERT OR IGNORE INTO payments"
                " (idempotency_key, business_tx_id, charge_id, amount_cents,"
                "  status, charged_at, refunded_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    envelope.idempotency_key,
                    envelope.business_tx_id,
                    "orphan",
                    0,
                    "orphan",
                    now,
                    now,
                ),
            )
            # Unconditional re-read: concurrent first-attempt race means the
            # winning INSERT's timestamp must be used so all callers produce
            # identical result bytes (DESIGN.md §Compensation idempotency pattern).
            persisted = conn.execute(
                "SELECT charge_id, refunded_at FROM payments WHERE idempotency_key = ?",
                (envelope.idempotency_key,),
            ).fetchone()
            charge_id = persisted["charge_id"]
            refunded_at = persisted["refunded_at"]
            kind = "orphan_tombstone"
        else:
            if row["refunded_at"] is None:
                conn.execute(
                    "UPDATE payments SET refunded_at = ?"
                    " WHERE business_tx_id = ? AND refunded_at IS NULL",
                    (datetime.now(UTC).isoformat(), envelope.business_tx_id),
                )
            # Re-read under the same transaction to pick up whichever timestamp
            # won a concurrent-refund race; keeps all callers' result bytes identical.
            persisted = conn.execute(
                "SELECT charge_id, refunded_at FROM payments"
                " WHERE business_tx_id = ? ORDER BY charged_at DESC LIMIT 1",
                (envelope.business_tx_id,),
            ).fetchone()
            charge_id = persisted["charge_id"]
            refunded_at = persisted["refunded_at"]
            # An earlier retry may have written an orphan tombstone; preserve its kind.
            kind = "orphan_tombstone" if charge_id == "orphan" else "refunded"

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
