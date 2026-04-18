"""Envelope construction helpers for the OrderWorkflow saga.

Single source of truth for step transitions: the workflow body and the
service-level unit tests both import from here so they cannot drift out
of sync over DESIGN.md §Compensation rules.
"""

from __future__ import annotations

from integration_showcase.shared.envelope import BlobRef, Envelope

# DESIGN.md §Compensation rules: ``step_id = "compensate.{original_step_id}"``.
COMPENSATE_RESERVE_INVENTORY_STEP = "compensate.reserve-inventory"
COMPENSATE_CHARGE_PAYMENT_STEP = "compensate.charge-payment"


def compensate_reserve_inventory_envelope(
    inventory_envelope: Envelope, inventory_ref: BlobRef
) -> Envelope:
    """Advance ``reserve-inventory`` -> ``compensate.reserve-inventory``.

    The returned envelope carries the canonical compensation
    ``idempotency_key`` (``"{business_tx_id}:compensate.reserve-inventory:
    {schema_version}"``) and keeps the inventory result blob as
    ``payload_ref`` for trace correlation.
    """
    return inventory_envelope.advance(COMPENSATE_RESERVE_INVENTORY_STEP, inventory_ref)


def refund_payment_envelope(payment_envelope: Envelope, payment_ref: BlobRef) -> Envelope:
    """Advance ``charge-payment`` -> ``compensate.charge-payment``.

    The returned envelope carries the canonical compensation
    ``idempotency_key`` (``"{business_tx_id}:compensate.charge-payment:
    {schema_version}"``) and keeps the payment receipt blob as
    ``payload_ref`` for trace correlation.
    """
    return payment_envelope.advance(COMPENSATE_CHARGE_PAYMENT_STEP, payment_ref)
