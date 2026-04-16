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

- [ ] **IS-006 -- Scenario scripts**
  Implement `scenarios/run_happy.py` and `run_unhappy.py`: POST to Service A, wait for
  workflow completion, print Jaeger and Temporal UI links.

- [ ] **IS-005 -- OTel instrumentation**
  Add span attributes (`business_tx_id`, `workflow_id`, `run_id`, `step_id`,
  `payload_ref_sha256`) to every activity and the workflow itself.
  Propagate W3C `traceparent` + `baggage` via Envelope fields at every service boundary.
  **Prerequisite:** update `pyproject.toml` dependency to `remote-store[azure,otel]` before
  using `remote_store.ext.otel` (`otel_hooks` / `otel_observe`) for store-level tracing spans.

---

## Ideas

*(none yet)*
