"""Shared constants used by both starters (Service A) and workers (IS-004+)."""

from __future__ import annotations

# Workflow task queue -- the workflow worker polls here; no activities registered.
TASK_QUEUE = "order-tasks"

# Per-service activity task queues.  Each service worker polls its own queue so
# Temporal routes each activity to exactly the worker that registered it.
TASK_QUEUE_B = "order-tasks-b"  # inventory (reserve / compensate)
TASK_QUEUE_C = "order-tasks-c"  # payment (charge)
TASK_QUEUE_D = "order-tasks-d"  # shipment (dispatch)

# OTel baggage key for the saga-wide business transaction ID (DESIGN.md invariant #5).
BUSINESS_TX_ID_BAGGAGE_KEY = "business_tx_id"
