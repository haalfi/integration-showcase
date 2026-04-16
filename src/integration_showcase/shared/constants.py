"""Shared constants used by both starters (Service A) and workers (IS-004+)."""

from __future__ import annotations

# Task queue name must match between the workflow starter and the worker.
TASK_QUEUE = "order-tasks"

# OTel baggage key for the saga-wide business transaction ID (DESIGN.md invariant #5).
BUSINESS_TX_ID_BAGGAGE_KEY = "business_tx_id"
