# Completed Backlog Items

- [x] **IS-012 -- Retry-then-fail payment path**
  Added `PaymentGatewayError` (retryable) and `_get_attempt()` seam to
  `service_c/activities.py`. `FORCE_PAYMENT_TRANSIENT_FAILS=N` env var causes
  `charge_payment` to raise `PaymentGatewayError` on attempts 1..N; attempt N+1
  then either succeeds or raises `InsufficientFundsError` depending on
  `FORCE_PAYMENT_FAILURE`. `_PAYMENT_RETRY` in `order.py` was already correct
  (`maximum_attempts=3`, 2 s initial, `backoff_coefficient=2.0`,
  `non_retryable_error_types=["InsufficientFundsError"]`); no workflow changes
  needed. Demo usage: set both env vars on the Service C worker process.
  Unit tests: `TestTransientFailures` in `test_service_c.py` (early attempts,
  final-attempt success, final-attempt non-retryable, zero default, invalid value).
  Integration test: `test_retry_then_fail_payment_path_triggers_compensation` in
  `tests/integration/test_workflow_routing.py` — stub records 3 call-count attempts
  (2 retryable + 1 terminal) and asserts compensation ran. Trace acceptance (Jaeger
  exponential-backoff spacing) is a live-demo check.

- [x] **IS-011 -- Full compensation tree**
  Added `refund_payment` activity to `service_c/activities.py` (orphan-tombstone pattern,
  `compensate.charge-payment` step, `refunded_at TEXT` column added to the existing
  `payments` table; mirrors `compensate_reserve_inventory` from Service B). Extended `OrderWorkflow` to wrap
  `dispatch_shipment` in a try/except that runs two-step reverse-order compensation on
  any failure: `refund_payment` (TASK_QUEUE_C) then `compensate_reserve_inventory`
  (TASK_QUEUE_B). Added `refund_payment_envelope()` helper to `workflow/envelopes.py` as
  single source of truth for the compensation idempotency key. Added `FORCE_SHIPMENT_FAILURE`
  env var to `service_d/activities.py` to drive the path in demos. Added scenario script
  `scenarios/run_shipment_failure.py` (registered as `hatch run scenario-shipment-failure`).
  Unit tests: `TestRefundPayment` in `test_service_c.py` (normal, orphan, idempotent retry);
  `test_refund_payment_routes_to_task_queue_c` in `test_workflow_routing.py`.
  Behavioural integration test: `test_shipment_failure_triggers_two_step_reverse_compensation`
  in `tests/integration/test_workflow_routing.py` proves both compensations reach their
  correct service queues (poison stubs on TASK_QUEUE would catch any misrouting).
  Jaeger span tree acceptance (concept §5.4) is a live-demo check; verified structurally
  by integration test routing coverage.

- [x] **IS-014 -- Blob metadata via remote-store (correlation attrs on payload blobs)**
  `shared/blob.upload()` now accepts a `metadata: dict[str, str] | None` kwarg and forwards
  it to Azure Blob Storage. remote-store v0.23.0 has no metadata channel on `Store.write()`
  and `AzureBackend.unwrap()` only exposes `FileSystemClient` (DataLake/HNS) — our blob-only
  Azurite does not serve DFS — so `_set_azure_blob_metadata()` reuses `STORE_URL` /
  `STORE_CONTAINER` to build a `BlobServiceClient` and calls `set_blob_metadata()` after
  the remote-store write completes. The bypass is localised to `shared/blob.py`; all
  service-side call sites go through `Envelope.blob_metadata()` which returns
  `{workflow_id, run_id, step_id, schema_version, idempotency_key}` (concept §6 + the
  idempotency key so orphaned blobs trace back to their writing step). Service A ingress
  builds the dict inline because the envelope is not yet constructed when the input
  blob is written.
  Test seam: module-level `_metadata_setter` can be monkeypatched (defaulted to a no-op
  in unit tests via the `store` fixture). Unit tests: `TestUploadMetadata` asserts the
  setter is invoked once with the caller's dict, stays untouched when `metadata=None`,
  and is still invoked with an empty dict (the ``metadata is not None`` guard treats
  ``{}`` as a deliberate clear, not a skip). `test_envelope.TestBlobMetadata` pins the
  five returned keys. Integration test `test_metadata_roundtrip_from_azurite` reads the
  blob's metadata back via the Azure Blob SDK and compares to the dict written. Concept
  §6 checkbox is now `[x]`. The direct Azure SDK call is a documented deviation from
  `DESIGN.md § remote-store usage`; removal is tracked by BK-005.

- [x] **IS-010 -- Blob metadata via remote-store (scoped to etag population)**
  `shared/blob.upload()` now calls `store.get_file_info(path)` inside the write
  context manager when `store.supports(Capability.METADATA)`, and forwards the
  returned `FileInfo.etag` into `BlobRef.etag` (normalised `None → ""`). This
  closes BK-003 for the etag field.
  Scope note: `remote_store.Store.write()` has no metadata parameter in v0.23.0, so
  custom business attributes (`workflow_id`, `run_id`, etc.) cannot be forwarded as
  Azure blob metadata through remote-store. That path requires either a future
  upstream API change or a sidecar-file strategy; neither is implemented here.
  `BlobRef.version_id` remains `""` — Azure blob versioning is not enabled in this
  showcase. `BlobRef` docstring updated to reflect the new behaviour.
  Unit tests: `test_etag_empty_for_memory_backend` (MemoryBackend returns no etag),
  `test_etag_populated_when_backend_provides_etag` (custom `_EtagMemoryBackend` subclass).
  Integration test: `test_etag_populated_from_azurite` verifies a non-empty etag
  is returned after a real write to Azurite.

- [x] **IS-009 -- Payment failure touches blob I/O first**
  Moved `blob.download` (and `json.loads`) above the `FORCE_PAYMENT_FAILURE` check in
  `service_c/activities.py`. The failed-payment span in Jaeger now includes a `blob.get`
  child span, aligning with §5.2's pattern of a blob GET preceding the payment error.
  Note: §5.2's full retry-then-fail sequence (attempt 1 = gateway_timeout, attempt 2 =
  insufficient_funds) remains unimplemented and is tracked by IS-012.
  Updated the `charge_payment` docstring: a blob-store outage now surfaces as a retryable
  error rather than the deterministic `InsufficientFundsError`.
  Renamed the unit test and added a download-call spy to assert `blob.download` actually
  executes on the failure path, so a future regression cannot silently revert the ordering.

- [x] **IS-008 -- Workflow-level span attributes**
  Added `trace` + `set_envelope_span_attrs` imports to `workflow/order.py`
  (inside `workflow.unsafe.imports_passed_through()`). After the `run_id` backfill,
  `set_envelope_span_attrs` is called on `trace.get_current_span()` with a copy of the
  envelope where `step_id="workflow"`, tagging the `RunWorkflow:OrderWorkflow` span
  created by `TracingInterceptor` with all six required business attributes.
  New integration test `tests/integration/test_workflow_span_attrs.py` verifies the span
  carries `business_tx_id`, `workflow_id`, `run_id`, `step_id="workflow"`,
  `payload_ref_sha256`, and `schema_version` when the workflow runs with `TracingInterceptor`.

- [x] **IS-013 -- Service A `POST /order` returns 202 Accepted**
  Added `status_code=202` to `@app.post("/order")` in `service_a/app.py`.
  Renamed `test_returns_200_with_required_fields` → `test_returns_202_with_required_fields`
  and flipped the assertion to `202` in `tests/unit/test_service_a.py`.

- [x] **IS-007 -- Concept adaptation pass**
  Aligned `sdd/research/research-temporal-azure-otel-orchestration.md` with what the
  showcase actually demonstrates and split production-only concerns into a new section.
  - §2: replaced "unveränderlich" with "inhaltsadressiert (SHA-256-verifiziert)" in both
    the layer table and the architecture diagram.
  - §3: reordered `payload_ref` so `sha256` leads; added a "Pflicht- vs. optionale Felder"
    paragraph that marks `etag`/`version_id` as backend-dependent and names `sha256` as
    the only backend-independent integrity guarantee.
  - §4.1: ingress now responds `202 Accepted {business_tx_id, workflow_id, traceparent}`
    immediately after `StartWorkflow`; added a status-polling block in the sequence
    diagram and an explanatory paragraph that points at `scenarios/_common.py::await_workflow`
    and notes a status endpoint as the production alternative.
  - §4.2: the "alle Spans tragen ..." attribute list now references `payload_ref.sha256`
    instead of `payload_ref.etag`.
  - §6: rephrased the metrics-cardinality bullet as "Optional / Erweiterung" and forwarded
    to §9; the blob-metadata bullet stays as the open requirement (IS-014 implements).
  - New §9 "Produktionshärtung" with five subsections: OTel Collector, OTLP logs, metrics
    cardinality, blob immutability/versioning policies, and a production-grade status
    endpoint (replacing direct cluster polling).
  No code changes. IS-013 closes the corresponding implementation gap.

- [x] **BUG-001 -- Activity mis-routing: all activities dispatched to TASK_QUEUE**
  All four `execute_activity` calls in `OrderWorkflow.run` omitted `task_queue=`, so
  Temporal dispatched every activity to the workflow's default queue (`TASK_QUEUE`).
  When multiple workers polled the same queue, the wrong worker (not registered for
  that activity) received the task, failed the attempt, and consumed the retry budget.
  The happy-path scenario had never been run end-to-end before; the bug was latent since
  IS-004. Fix: added per-service queue constants (`TASK_QUEUE_B/C/D`) to `constants.py`,
  threaded `task_queue=` into every `execute_activity` call in the workflow, and updated
  each service worker to poll its own dedicated queue. Regression tests:
  `tests/unit/test_workflow_routing.py` — 6 structural (AST) tests verify each call uses
  the correct constant; 1 behavioral test runs the workflow in a time-skipping
  `WorkflowEnvironment` with poison stubs on `TASK_QUEUE` and real stubs on B/C/D.

- [x] **BK-001 -- Explicit Temporal Client lifecycle in Service A**
  Investigation result: `temporalio.client.Client` intentionally exposes no `close()` method
  (confirmed via SDK docs and context7 — "Clients do not have an explicit 'close' method").
  The current lifespan already releases the reference in its `finally` block
  (`_temporal_client = None`), which is the best achievable GC-prompt cleanup.
  Unit tests bypass the lifespan entirely (via `httpx.ASGITransport` without lifespan trigger),
  so no real client handles accumulate in the test harness.  Docstring updated to record the
  finding.  No further action available until the SDK exposes a close hook.

- [x] **IS-005b -- Log correlation**
  `shared/log_setup.py`: `OtelContextFilter` reads `trace.get_current_span().get_span_context()`
  and `baggage.get_baggage(BUSINESS_TX_ID_BAGGAGE_KEY)` and injects `trace_id` (32-char W3C hex),
  `span_id` (16-char hex), and `business_tx_id` as `LogRecord` extras; `JsonFormatter`
  emits them as top-level fields (alongside `timestamp`, `level`, `service`, `logger`,
  `message`); `exc_info`/`stack_info` are preserved as `exception`/`stack` fields so
  `logger.exception(...)` retains its traceback. `setup_logging(service_name)` replaces
  root logger handlers with a stdout `StreamHandler` and explicitly patches the three
  uvicorn loggers (`uvicorn`, `uvicorn.access`, `uvicorn.error`) with the same handler,
  clearing their prior handlers and keeping `propagate=False` — this ensures service-a's
  request access logs and error logs are JSON-structured even though uvicorn configures
  those loggers before the FastAPI lifespan runs. Called automatically from `setup_tracing`
  so all five service entry points get structured log correlation with no additional
  callsites. `BUSINESS_TX_ID_BAGGAGE_KEY` constant added to `shared/constants.py`; both
  the producer (`service_a/app.py`) and consumer (`log_setup.py`) import it. 35 unit tests;
  `TestSetupLogging` uses a restore fixture for logger-state isolation and validates emitted
  JSON via `capsys`. Full suite 137 tests @ 92.66% coverage.

- [x] **IS-006 -- Scenario scripts**
  `scenarios/run_happy.py` and `scenarios/run_unhappy.py` POST to Service A,
  await workflow completion via a direct Temporal client
  (`pydantic_data_converter`), and print deep-links: Jaeger trace URL parsed
  from the new `OrderResponse.traceparent` field; Temporal UI URL built from
  `workflow_id` + `run_id` (fetched via `handle.describe()`). Shared helpers
  live in `scenarios/_common.py` (`parse_trace_id`, `jaeger_trace_url`,
  `jaeger_search_url`, `temporal_workflow_url`, `post_order`, `await_workflow`,
  `find_application_error`, `print_links`, `build_argparser`). Both scripts
  take argparse flags for items/customer_id/URLs. Unhappy exits 0 only when
  the workflow fails with `InsufficientFundsError` (demo-success semantics).
  Hatch scripts `scenario-happy` / `scenario-unhappy`. 20 unit tests cover
  the pure helpers and 8 more cover the `main()` entry points via mocked
  `post_order` / `await_workflow` stubs.

- [x] **IS-005 -- OTel instrumentation**
  `shared/otel.py`: `setup_tracing(service)` installs TracerProvider + OTLP exporter
  + W3C TraceContext/Baggage composite propagator; `set_envelope_span_attrs` tags a
  span with the six required business attrs (`business_tx_id`, `workflow_id`,
  `run_id`, `step_id`, `payload_ref_sha256`, `schema_version`); `inject_carrier_into_envelope` /
  `extract_context_from_envelope` serialize the W3C context into `Envelope.traceparent` /
  `tracestate` + unified `baggage` dict; `@instrument_activity` decorator backfills
  `run_id` from `activity.info()` and tags the current `RunActivity:*` span. Each worker
  (workflow/B/C/D) bootstraps tracing and installs Temporal's `TracingInterceptor`.
  Service A wraps `POST /order` in an `http.ingress` span and injects the carrier
  before `start_workflow`. Blob store wrapped with `remote_store.ext.otel.otel_observe`.
  15 new unit tests; full suite 74 tests @ 95.56% coverage. Log correlation split
  into IS-005b.

- [x] **BK-002 -- GitHub Actions CI quality gate**
  `.github/workflows/ci.yml`: runs on push/PR to `main`. Jobs: `changes` (path
  filter), `lint` (ruff check + format-check), `typecheck` (mypy), `test`
  (pytest --cov, fail-under=80), `gate` (aggregator). Uses
  `actions/checkout@v6`, `actions/setup-python@v6` (3.13),
  `astral-sh/setup-uv@v8.0.0`. No Docker services needed (unit tests use
  in-process fakes). Register `gate` as required status check in repo settings.

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
