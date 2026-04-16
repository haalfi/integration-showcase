# Completed Backlog Items

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
    to §9; the blob-metadata bullet stays as the open requirement (IS-010 implements).
  - New §9 "Produktionshärtung" with five subsections: OTel Collector, OTLP logs, metrics
    cardinality, blob immutability/versioning policies, and a production-grade status
    endpoint (replacing direct cluster polling).
  No code changes. Known gap tracked separately: Service A currently returns HTTP 200,
  not 202 — see `IS-013` in `BACKLOG.md`.

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
