# Completed Backlog Items

- [x] **IS-002 -- Blob client wrapper**
  `shared/blob.py`: `upload(data, path) -> BlobRef` and `download(ref) -> bytes`.
  `STORE_URL` = Azure/Azurite connection string; `STORE_CONTAINER` = container name.
  SHA-256 computed on upload, verified on download. 11 unit tests via `MemoryBackend`.

- [x] **IS-001 -- Initial project scaffold**
  pyproject.toml (hatch + ruff + pytest), src layout, `Envelope` + `BlobRef` models,
  `OrderWorkflow` skeleton with saga compensation, activity stubs, unit tests for
  Envelope, docker-compose.yml (Temporal, Azurite, Jaeger), SDD + Claude setup.
