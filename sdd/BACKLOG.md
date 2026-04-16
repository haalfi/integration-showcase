# Development Backlog

Active work items. Completed items live in [BACKLOG-DONE.md](BACKLOG-DONE.md).

Items graduate: **Idea -> Backlog -> Spec -> Tests -> Code**.

## How this file works

**Status legend:** `[ ]` pending · `[~]` in progress

**Ordering:** newest first within each section.

**Completing work:**
- Fully done -> delete from here, add to `BACKLOG-DONE.md` as `[x]` (same commit as code change).
- Partially done -> split: ship done part to `BACKLOG-DONE.md` under original ID, create new ID here
  for remainder, link both.

**ID prefixes:**

| Prefix | Meaning |
|--------|---------|
| `BL-NNN` | Release blocker. |
| `BK-NNN` | Committed backlog work, queued behind blockers. |
| `BUG-NNN` | Confirmed defect with reproduction steps. |
| `IS-NNN` | Showcase item. |

---

## Backlog (Prioritized)

Ordered by recommended execution: concept-first (sets the acceptance bar),
then small-wins, then the substantive code work, then cleanup.

- [ ] **IS-008 -- Workflow-level span attributes**
  Temporal's `TracingInterceptor` creates a `RunWorkflow:OrderWorkflow` span that currently
  carries no business attrs, so concept §6 ("Alle Spans tragen als Attribute mindestens ...")
  is violated at the workflow level. Tag the span inside `OrderWorkflow.run` with the six
  required attributes (`business_tx_id`, `workflow_id`, `run_id`, `step_id="workflow"`,
  `payload_ref_sha256`, `schema_version`). Acceptance: a Jaeger search on a single
  `business_tx_id` returns the workflow span alongside ingress and activity spans.

- [ ] **IS-009 -- Payment failure touches blob I/O first**
  `charge_payment` checks `FORCE_PAYMENT_FAILURE` before `blob.download`, so the failed-
  attempt span tree in Jaeger has no `blob.get` child -- inconsistent with concept §5.2 which
  shows `C->AB: GET inventory-result.json` followed by `C--xT: Error`. Move the check to
  after `blob.download` + `json.loads` so the error span sits under a completed blob-read
  span. Small diff; preserves determinism because the downloaded bytes do not alter the
  control flow under forced failure.

- [ ] **IS-010 -- Blob metadata via remote-store**
  Concept §6 checklist item currently unimplemented. Extend `shared/blob.upload` to forward
  `metadata={workflow_id, run_id, step_id, schema_version, idempotency_key}` through
  remote-store's metadata channel (backend-dependent). Verify the Azure / Azurite backend
  surfaces metadata on read; document `MemoryBackend` behaviour explicitly (likely no-op).
  Acceptance: blob properties in Azurite show the business attrs; concept §6 blob-metadata
  checklist is satisfied; integration test reads back the metadata for a reserved blob.

- [ ] **IS-011 -- Full compensation tree**
  Showcase currently compensates only `reserve-inventory` on pre-charge payment failure.
  Extend so concept §5.3's state diagram is fully demoable -- three sub-items:
  - Add `refund_payment` activity in `service_c/activities.py`: idempotent,
    `compensate.charge-payment` step naming, orphan-tombstone pattern mirroring
    `compensate_reserve_inventory`.
  - Extend `OrderWorkflow` to execute compensations in reverse order on shipment failure:
    refund first, then release reservation.
  - Add a scenario script (or branch of `run_unhappy.py`) that forces a shipment failure to
    drive the two-step compensation path.
  Acceptance: Jaeger span tree matches concept §5.4 for both pre-charge and post-charge
  failure paths; unit tests in `tests/unit/test_workflow_routing.py` cover reverse-order
  dispatch; one behavioural test in `tests/integration/` exercises the two-step compensation.

- [ ] **IS-012 -- Retry-then-fail payment path**
  Concept §5.2 sequence shows attempt 1 = `gateway_timeout` (retryable) -> attempt 2 =
  `insufficient_funds` (non-retryable). Current implementation short-circuits on first
  attempt. Add `FORCE_PAYMENT_TRANSIENT_FAILS=N` env: first N attempts raise a retryable
  `PaymentGatewayError`; attempt N+1 either succeeds or raises `InsufficientFundsError`
  depending on `FORCE_PAYMENT_FAILURE`. Acceptance: trace shows two failed attempts + one
  terminal attempt with exponential-backoff spacing visible in Jaeger.

- [ ] **BK-003 -- BlobRef field hygiene**
  Depends on IS-010. After blob metadata lands, decide per field: populate `version_id`
  from the remote-store write result if the Azure backend exposes it, otherwise drop both
  `etag` and `version_id` from `BlobRef` and from the concept §3 canonical envelope. Keep
  `sha256` as the integrity guarantee. Update every envelope construction site accordingly.

- [ ] **BK-004 -- Business attrs on `store.*` spans**
  `otel_observe`-wrapped blob spans (`store.write`, `store.read_bytes`) currently lack the
  six business attributes, so concept §6's "Alle Spans" rule fails at the blob layer. Two
  options: (a) an OTel `SpanProcessor` that reads `business_tx_id` from baggage and stamps
  it at span-end, or (b) a thin wrapper around `Store` that opens a child span with the
  envelope attrs before delegating. Prefer (a) -- baggage already carries `business_tx_id`
  and it avoids touching every call site. Acceptance: every blob span in Jaeger carries the
  six business attrs.

---

## Ideas

- **IS-ext-metrics** -- One counter `saga_completed_total{outcome}` and one histogram
  `saga_duration_seconds`. Validates concept §6 cardinality rule (business_tx_id stays out
  of metric labels) with a real example. Pull when the demo story benefits from a metrics
  pane alongside traces.

- **IS-ext-otlp-logs** -- Replace the stdout JSON log handler with an OTLP log exporter;
  wire collector -> log backend. Today JSON logs land only in Docker logs; an OTLP pipeline
  would correlate logs with traces inside the same backend.

- **BK-ext-collector** -- Wire `otel-collector-config.yaml` between services and Jaeger;
  flip `OTEL_EXPORTER_OTLP_ENDPOINT` default from `jaeger:4317` to `otel-collector:4317`.
  Gives real batching / sampling / routing infrastructure instead of direct-to-Jaeger.
