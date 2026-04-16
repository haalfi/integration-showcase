# Completed Backlog Items

- [x] **IS-004 -- Activity implementations (B/C/D)**
  Real logic per activity: download payload via `shared/blob.py`, execute the
  local domain action against a private SQLite database (`shared/db.py` helper
  with test seam), upload the canonical result blob, return a new `BlobRef`.
  Idempotency by `envelope.idempotency_key` (or `business_tx_id` for the
  compensation lookup); retries reconstruct the same canonical bytes from
  persisted state, keeping the returned `BlobRef.sha256` stable. Includes
  `compensate_reserve_inventory` (idempotent release with orphan-tombstone
  fallback) and Temporal worker entry points for B/C/D plus the workflow
  worker. Unit tests via `MemoryBackend` + `:memory:` SQLite (no Docker).

- [x] **IS-003 -- Service A: HTTP ingress**
  FastAPI `POST /order`: serializes `OrderRequest` → JSON bytes, uploads to blob store
  via `shared/blob.py`, builds initial `Envelope` (`step_id="start"`), starts
  `OrderWorkflow` on Temporal (fire-and-forget). Module-level `_temporal_client` seam
  for testability. 6 unit tests via `MemoryBackend` + `AsyncMock`.

- [x] **IS-002 -- Blob client wrapper**
  `shared/blob.py`: `upload(data, path) -> BlobRef` and `download(ref) -> bytes`.
  `STORE_URL` = Azure/Azurite connection string; `STORE_CONTAINER` = container name.
  SHA-256 computed on upload, verified on download. 11 unit tests via `MemoryBackend`.

- [x] **IS-001 -- Initial project scaffold**
  pyproject.toml (hatch + ruff + pytest), src layout, `Envelope` + `BlobRef` models,
  `OrderWorkflow` skeleton with saga compensation, activity stubs, unit tests for
  Envelope, docker-compose.yml (Temporal, Azurite, Jaeger), SDD + Claude setup.
