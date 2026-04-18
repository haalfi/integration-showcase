"""Order fulfillment saga workflow.

Steps: reserve-inventory -> charge-payment -> dispatch-shipment.
Payment failure triggers compensation of reserve-inventory (single step).
Shipment failure triggers reverse-order two-step compensation:
  refund-payment first, then release-reservation.
  Both compensation steps are isolated so one failure does not suppress the other.
  On any compensation failure, zero-duration OTel compensation spans record the full
  error context (shipment trigger, refund outcome, inventory-release outcome).
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from integration_showcase.shared.constants import (
        TASK_QUEUE_B,
        TASK_QUEUE_C,
        TASK_QUEUE_D,
    )
    from integration_showcase.shared.envelope import BlobRef, Envelope
    from integration_showcase.shared.otel import emit_workflow_compensation_span
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
    maximum_attempts=1,
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
        # InsufficientFundsError / payment path UX).
        try:
            await workflow.execute_activity(
                "dispatch_shipment",
                payment_envelope,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=_SHIPMENT_RETRY,
                task_queue=TASK_QUEUE_D,
            )
        except Exception as shipment_exc:
            # Reverse-order compensation: refund payment first, then release inventory.
            # Both steps are isolated so one failure never suppresses the other (BK-006/007).
            _sc = getattr(shipment_exc, "cause", shipment_exc)
            emit_workflow_compensation_span(
                "Compensation:Triggered",
                {"error.type": getattr(_sc, "type", None) or type(_sc).__name__},
            )

            refund_envelope = refund_payment_envelope(payment_envelope, payment_ref)
            refund_error: Exception | None = None
            try:
                await workflow.execute_activity(
                    "refund_payment",
                    refund_envelope,
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=_COMPENSATE_RETRY,
                    task_queue=TASK_QUEUE_C,
                )
            except Exception as exc:
                refund_error = exc

            compensate_envelope = compensate_reserve_inventory_envelope(
                inventory_envelope, inventory_ref
            )
            compensate_error: Exception | None = None
            try:
                await workflow.execute_activity(
                    "compensate_reserve_inventory",
                    compensate_envelope,
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=_COMPENSATE_RETRY,
                    task_queue=TASK_QUEUE_B,
                )
            except Exception as exc:
                compensate_error = exc

            if refund_error is not None:
                _rc = getattr(refund_error, "cause", refund_error)
                emit_workflow_compensation_span(
                    "Compensation:RefundFailed",
                    {"error.type": getattr(_rc, "type", None) or type(_rc).__name__},
                )
            if compensate_error is not None:
                _cc = getattr(compensate_error, "cause", compensate_error)
                emit_workflow_compensation_span(
                    "Compensation:InventoryReleaseFailed",
                    {"error.type": getattr(_cc, "type", None) or type(_cc).__name__},
                )

            if compensate_error is not None:
                raise compensate_error from None
            if refund_error is not None:
                # Both failures are in Temporal's event history and span events;
                # suppress Python's implicit context chain to avoid a misleading
                # "caused by" link between two independent saga failures.
                raise refund_error from None
            raise

        return envelope.business_tx_id
