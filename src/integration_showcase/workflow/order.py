"""Order fulfillment saga workflow.

Steps: reserve-inventory -> charge-payment -> dispatch-shipment.
Payment failure triggers compensation of reserve-inventory (single step).
Shipment failure triggers reverse-order two-step compensation:
  refund-payment first, then release-reservation.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from opentelemetry import trace

    from integration_showcase.shared.constants import (
        TASK_QUEUE_B,
        TASK_QUEUE_C,
        TASK_QUEUE_D,
    )
    from integration_showcase.shared.envelope import BlobRef, Envelope
    from integration_showcase.shared.otel import set_envelope_span_attrs
    from integration_showcase.workflow.envelopes import (
        compensate_reserve_inventory_envelope,
        refund_payment_envelope,
    )


_DEFAULT_RETRY = RetryPolicy(
    maximum_attempts=3,
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2.0,
)

_COMPENSATE_RETRY = RetryPolicy(maximum_attempts=5)

_PAYMENT_RETRY = RetryPolicy(
    maximum_attempts=3,
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2.0,
    non_retryable_error_types=["InsufficientFundsError"],
)

_SHIPMENT_RETRY = RetryPolicy(
    maximum_attempts=3,
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2.0,
    non_retryable_error_types=["ShipmentError"],
)


@workflow.defn
class OrderWorkflow:
    """Order fulfillment saga with compensation on payment failure."""

    @workflow.run
    async def run(self, envelope: Envelope) -> str:
        """Execute the saga. Returns business_tx_id on success."""
        # Service A sends run_id="" (Temporal assigns it here). Backfill so every
        # activity span (IS-005) sees a stable run_id without needing activity.info().
        if not envelope.run_id:
            envelope = envelope.model_copy(update={"run_id": workflow.info().run_id})

        # Tag the TracingInterceptor RunWorkflow span with the six required business
        # attributes (IS-008). step_id="workflow" is a fixed saga-root marker for this
        # span only — the envelope passed to activities keeps step_id="start" so that
        # envelope.advance("reserve-inventory", ...) correctly sets parent_step_id="start".
        set_envelope_span_attrs(
            trace.get_current_span(),
            envelope.model_copy(update={"step_id": "workflow"}),
        )

        # Step 1: Reserve inventory
        inventory_ref: BlobRef = await workflow.execute_activity(
            "reserve_inventory",
            envelope,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=_DEFAULT_RETRY,
            task_queue=TASK_QUEUE_B,
        )
        inventory_envelope = envelope.advance("reserve-inventory", inventory_ref)

        # Step 2: Charge payment -- compensate reservation on any failure
        try:
            payment_ref: BlobRef = await workflow.execute_activity(
                "charge_payment",
                inventory_envelope,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=_PAYMENT_RETRY,
                task_queue=TASK_QUEUE_C,
            )
        except Exception:
            # Single source of truth for compensation envelope construction
            # (DESIGN.md §Compensation rules); the tests import the same
            # helper so the idempotency_key contract cannot drift.
            compensate_envelope = compensate_reserve_inventory_envelope(
                inventory_envelope, inventory_ref
            )
            await workflow.execute_activity(
                "compensate_reserve_inventory",
                compensate_envelope,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=_COMPENSATE_RETRY,
                task_queue=TASK_QUEUE_B,
            )
            raise

        payment_envelope = inventory_envelope.advance("charge-payment", payment_ref)

        # Step 3: Dispatch shipment -- compensate payment then reservation on failure.
        # ShipmentError is non-retryable so compensation starts immediately (matches
        # InsufficientFundsError / payment path UX). Known gap: if refund_payment
        # exhausts _COMPENSATE_RETRY, compensate_reserve_inventory is never reached
        # and the reservation remains live until operator intervention (BK-006).
        try:
            await workflow.execute_activity(
                "dispatch_shipment",
                payment_envelope,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=_SHIPMENT_RETRY,
                task_queue=TASK_QUEUE_D,
            )
        except Exception:
            # Reverse-order compensation: refund payment first, then release inventory.
            refund_envelope = refund_payment_envelope(payment_envelope, payment_ref)
            await workflow.execute_activity(
                "refund_payment",
                refund_envelope,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=_COMPENSATE_RETRY,
                task_queue=TASK_QUEUE_C,
            )
            compensate_envelope = compensate_reserve_inventory_envelope(
                inventory_envelope, inventory_ref
            )
            await workflow.execute_activity(
                "compensate_reserve_inventory",
                compensate_envelope,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=_COMPENSATE_RETRY,
                task_queue=TASK_QUEUE_B,
            )
            raise

        return envelope.business_tx_id
