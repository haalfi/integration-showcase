# integration-showcase

**Temporal · Azure Blob (`remote-store`) · OpenTelemetry** — distributed saga orchestration showcase.

Demonstrates durable saga orchestration, the Claim-Check payload pattern, and full
end-to-end traceability across service boundaries using a canonical Envelope.

## What it shows

An order fulfillment saga across four services:

| Service | Role |
|---|---|
| **A** | HTTP ingress — writes payload blob, starts Temporal workflow |
| **B** | Inventory — reserves stock, uploads result blob |
| **C** | Payment — charges customer, uploads receipt blob; compensated on failure |
| **D** | Shipping — dispatches order |

Services exchange only a canonical **Envelope** (never raw payloads). Azure Blob Storage
(via [remote-store](https://github.com/haalfi/remote-store)) is the payload vault.
OpenTelemetry spans link the full trace — including retries and compensation — via a
stable `business_tx_id`.

See [sdd/research/research-temporal-azure-otel-orchestration.md](sdd/research/research-temporal-azure-otel-orchestration.md)
for full architecture details (German).

## Quick start

```bash
docker compose up -d
hatch run test

# Happy path: POSTs to Service A, awaits workflow completion,
# prints Jaeger and Temporal UI deep-links for the specific run.
hatch run scenario-happy

# Unhappy path: launch Service C with FORCE_PAYMENT_FAILURE=true to
# trigger the compensation branch, then run the scenario:
#   FORCE_PAYMENT_FAILURE=true python -m integration_showcase.service_c.worker
hatch run scenario-unhappy

# Retry-then-fail path (IS-012): Service C raises a retryable PaymentGatewayError
# on the first N attempts, then InsufficientFundsError on attempt N+1.
# N must be < _PAYMENT_RETRY.maximum_attempts (currently 3).
#   FORCE_PAYMENT_TRANSIENT_FAILS=2 FORCE_PAYMENT_FAILURE=true \
#     python -m integration_showcase.service_c.worker
# Jaeger will show 2 retried spans with exponential-backoff spacing before the
# terminal InsufficientFundsError that triggers compensation.
hatch run scenario-unhappy
```

| UI | URL |
|---|---|
| Jaeger traces | http://localhost:16686 |
| Temporal workflows | http://localhost:8088 |
| Azurite blob storage | http://localhost:10000 |

## Dev commands

```bash
hatch run all           # lint + format-check + test (pre-commit gate)
hatch run test          # unit tests only
hatch run lint          # ruff check
hatch run typecheck     # mypy
```

## Config switching (remote-store)

Blob I/O uses `remote-store`. Switch backends via env var — no code changes:

```bash
# Local dev (Azurite)
STORE_URL="DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;..."

# Production Azure
STORE_URL=az://myaccount/workflows
```
