"""Inventory reservation and compensation activities."""

from __future__ import annotations

from temporalio import activity

from integration_showcase.shared.envelope import BlobRef, Envelope


@activity.defn(name="reserve_inventory")
async def reserve_inventory(envelope: Envelope) -> BlobRef:
    """Download input blob, reserve inventory in local DB, upload result blob.

    IS-004: implement blob download via shared/blob.py, SQLite write,
    blob upload, return new BlobRef.
    """
    raise NotImplementedError("IS-004")


@activity.defn(name="compensate_reserve_inventory")
async def compensate_reserve_inventory(envelope: Envelope) -> BlobRef:
    """Release inventory reservation (idempotent compensation).

    IS-004: release reservation, upload compensation-result blob.
    """
    raise NotImplementedError("IS-004")
