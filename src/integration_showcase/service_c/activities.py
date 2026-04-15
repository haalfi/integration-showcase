"""Payment charging activity."""

from __future__ import annotations

from temporalio import activity

from integration_showcase.shared.envelope import BlobRef, Envelope


class InsufficientFundsError(Exception):
    """Non-retryable: payment declined due to insufficient funds."""


@activity.defn(name="charge_payment")
async def charge_payment(envelope: Envelope) -> BlobRef:
    """Download inventory result blob, charge payment, upload receipt blob.

    IS-004: implement blob download, payment logic, blob upload.
    Set FORCE_PAYMENT_FAILURE=true to trigger the unhappy path.
    """
    raise NotImplementedError("IS-004")
