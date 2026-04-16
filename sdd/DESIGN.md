# Code Style & Conventions

## Intent & Scope

Coding conventions for all `src/`, `tests/`, and `scenarios/` code.
All code must pass `ruff check` and `ruff format`.

## Rules

### 1. Formatting & Linting

- **Formatter/linter:** ruff (line-length 100)
- **`from __future__ import annotations`** in every module

### 2. Module docstrings

Every module starts with a 1-2 sentence docstring explaining *why it exists*:

```python
"""Canonical inter-service envelope (Claim-Check pattern)."""
```

### 3. Type annotations

Public signatures use PEP 604 union syntax (`X | None`, not `Optional[X]`).
Required args positional; behavior flags keyword-only (`*`).

## Envelope invariants

1. Services exchange **only** the Envelope -- never the raw payload.
2. Each service fetches the payload from Blob Storage using `payload_ref`.
3. Each service writes its result to its own blob and returns a new `BlobRef`.
4. `idempotency_key = "{business_tx_id}:{step_id}:{schema_version}"` -- unique per
   (transaction, step, schema); guards all activity side effects against Temporal retries.
5. `business_tx_id` never changes within a saga; it survives workflow restarts and
   child workflows. It appears in every span attribute and log field.

## Service boundary rules

- Each service owns its local database (SQLite in the POC).
- No service reads another service's database.
- Temporal orchestrates via Activity return values only -- no direct service-to-service calls.

## OTel span attributes (required on every span)

| Attribute | Source |
|---|---|
| `business_tx_id` | Envelope |
| `workflow_id` | Envelope |
| `run_id` | Envelope |
| `step_id` | Envelope |
| `payload_ref_sha256` | Envelope.payload_ref |
| `schema_version` | Envelope |

## Compensation rules

- Compensation activity `step_id = "compensate.{original_step_id}"`.
- Compensation is always idempotent.
- Temporal executes compensation in reverse step order.
- Compensation uses the same `business_tx_id` so all spans are queryable together.

### Compensation idempotency pattern

Invariant #4 says `idempotency_key` "guards all activity side effects against
Temporal retries." Compensation activities deviate in one controlled way:

- **Forward activities** are keyed on `idempotency_key` (PK + `INSERT OR IGNORE`);
  retries collide on the PK and no-op.
- **Compensation activities** must find and reverse the forward activity's
  effect, but the forward effect was stored under a *different* step's
  idempotency key. Compensation therefore looks up prior state by
  `business_tx_id` (which is stable across the whole saga) and achieves
  idempotency via a state-transition flag (e.g. `released_at IS NULL` ->
  `released_at = <ts>`, with a post-UPDATE re-read so concurrent retries
  observe the winning timestamp).
- Orphan compensation (no prior forward row) inserts a tombstone row keyed
  on the canonical compensation `idempotency_key` so subsequent retries are
  PK no-ops. The tombstone blob carries a `"kind": "orphan_tombstone"`
  discriminator so trace/audit consumers can distinguish it from a real
  release.

## remote-store usage

- Blob I/O goes through `remote-store`'s `Store` API -- never raw Azure SDK calls.
- Store URL is read from the `STORE_URL` env var.
- Local dev uses Azurite connection string (`UseDevelopmentStorage=true`).
- Backend switching requires no code changes.
