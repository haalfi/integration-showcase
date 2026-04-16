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

- [ ] **BK-001 -- Explicit Temporal Client lifecycle in Service A**
  `service_a/app.py` currently relies on GC to clean up the `Client` when the
  FastAPI lifespan ends. The SDK's Rust runtime owns a background thread tied
  to the handle; under ASGI dev-reload or test harnesses that recreate the app
  multiple times, handles accumulate until interpreter exit. Investigate
  whether the SDK exposes an explicit close hook (or adopt a
  `contextlib.closing`-style guard once it does), and wire it into the
  lifespan's `finally` block. (Raised in PR #6 review.)

---

## Ideas

*(none yet)*
