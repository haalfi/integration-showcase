# Development Backlog

Active work items. Completed items live in [BACKLOG-DONE.md](BACKLOG-DONE.md).

Items graduate: **Idea -> Backlog -> Spec -> Tests -> Code**.

## How this file works

**Status legend:** `[ ]` pending · `[~]` in progress

**Ordering:**
- *Backlog (Prioritized)* is execution-ordered: concept-first, then small wins,
  then substantive code work, then cleanup. Insert new items at the position
  that matches their priority, not at the top.
- *Ideas* is newest-first; new ideas go to the top.

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

- [ ] **BK-005 -- Remove direct Azure SDK bypass in `shared/blob.py`**
  IS-014 added `_set_azure_blob_metadata()` which opens its own `BlobServiceClient` to call
  `set_blob_metadata()`. This violates `DESIGN.md § remote-store usage` (Blob I/O goes
  through remote-store's Store API by default; raw Azure SDK calls are forbidden except
  for this one documented deviation), because remote-store v0.23.0 has no metadata channel
  on `Store.write()` and `AzureBackend.unwrap()` only exposes `FileSystemClient`
  (DataLake/HNS) — not `ContainerClient`/`BlobServiceClient`.
  Follow the upstream remote-store changelog. When either (a) `Store.write()` grows a
  `metadata=` kwarg, or (b) `AzureBackend.unwrap()` starts returning
  `BlobServiceClient`/`ContainerClient`, delete `_set_azure_blob_metadata()` and route the
  metadata write through remote-store. Update the `DESIGN.md` carve-out and this item's
  acceptance: no direct Azure SDK usage in `shared/blob.py`; integration test
  `test_metadata_roundtrip_from_azurite` still passes unchanged.
  **Known limitation — `run_id=""` on ingress blob:** Service A writes
  `workflows/{business_tx_id}/input.json` before `start_workflow` returns, so the blob's
  `run_id` metadata is always `""`. Temporal provides the real `run_id` only via the
  returned handle, after the blob has been persisted. A second `set_blob_metadata` PUT
  after start_workflow would patch it, but that doubles the Azure round-trip on the
  ingress hot path for metadata no operator queries today. Accepted: the ingress blob's
  `run_id` stays empty; all subsequent per-step blobs carry the real `run_id` via
  `envelope.blob_metadata()`. Revisit if/when a metadata-driven lookup needs it.

- [ ] **BK-004 -- Business attrs on `store.*` spans**
  All `otel_observe`-wrapped blob spans (currently `store.write`, `store.read_bytes`,
  `store.get_file_info` -- and any future ops added at the blob layer) lack the six
  business attributes, so concept §6's "Alle Spans" rule fails at the blob layer. Two
  options: (a) an OTel `SpanProcessor` that reads `business_tx_id` from baggage and stamps
  it at span-end, or (b) a thin wrapper around `Store` that opens a child span with the
  envelope attrs before delegating. Prefer (a) -- baggage already carries `business_tx_id`
  and it avoids touching every call site. Acceptance: every blob span in Jaeger carries the
  six business attrs.

---

- [ ] **BK-006 -- Two-step compensation short-circuit on catastrophic refund failure**
  If `refund_payment` exhausts its `_COMPENSATE_RETRY` budget (5 attempts), the workflow
  raises without reaching `compensate_reserve_inventory`, leaving the inventory reservation
  live until operator intervention. The risk is documented in `workflow/order.py` step 3.
  Fix: wrap the `refund_payment` `execute_activity` call in its own try/except and
  always dispatch `compensate_reserve_inventory` regardless of refund outcome, surfacing
  both errors. Acceptance: integration test proves `compensate_reserve_inventory` runs even
  when `refund_payment` fails permanently.

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
