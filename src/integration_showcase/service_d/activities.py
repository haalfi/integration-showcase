"""Shipment dispatch activity."""

from __future__ import annotations

from temporalio import activity

from integration_showcase.shared.envelope import BlobRef, Envelope


@activity.defn(name="dispatch_shipment")
async def dispatch_shipment(envelope: Envelope) -> BlobRef:
    """Download payment receipt blob, dispatch shipment, persist local state.

    IS-004: implement blob download, dispatch logic, local DB write.
    """
    raise NotImplementedError("IS-004")
