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

Full documentation covering the envelope contract, rules reference, how-to guides
for Temporal, Blob Storage, and OpenTelemetry, plus design rationale is provided in
[docs](docs/) (German).

## Quick start

```bash
docker compose up -d
hatch run demo-happy             # happy path — all services succeed
hatch run demo-unhappy           # payment fails → compensate inventory
hatch run demo-shipment-failure  # shipment fails → refund + compensate inventory
```

Each demo starts all workers, waits for Service A, runs the saga, prints Jaeger
and Temporal UI deep-links, then shuts everything down.

```bash
hatch run test   # unit tests (no live services required)
```

## Interactive mode

The `demo-*` commands drive a single hard-coded scenario and exit. To click
around the API yourself — fire arbitrary orders, inspect blobs, watch traces —
use `hatch run stack` instead:

```bash
docker compose up -d   # infra: Temporal, Azurite, Jaeger, Temporal UI
hatch run stack        # app:   Service A + workflow + B/C/D workers (Ctrl-C to stop)
```

The stack stays up until Ctrl-C. Worker logs stream to `./tmp/stack.log`.

| UI | URL |
|---|---|
| Service A Swagger (fire orders) | http://localhost:8000/docs |
| Service A blob browser | http://localhost:8000/blobs |
| Jaeger traces | http://localhost:16686 |
| Temporal workflows | http://localhost:8088 |
| Azurite blob storage | http://localhost:10000 |

Failure injection (`FORCE_PAYMENT_FAILURE=true`, `FORCE_SHIPMENT_FAILURE=true`,
`FORCE_PAYMENT_TRANSIENT_FAILS=N`) is read at worker startup, so set it in the
shell *before* `hatch run stack` — you cannot toggle a scenario per request today.

## Dev commands

```bash
hatch run all           # lint + format-check + test (pre-commit gate)
hatch run test          # unit tests only
hatch run lint          # ruff check
hatch run typecheck     # mypy

# Manual scenario runners (use when workers are already running separately)
hatch run scenario-happy
hatch run scenario-unhappy
hatch run scenario-shipment-failure
```

## Config (env vars)

The `demo-*` scripts set both vars automatically for local dev (Azurite). Set
them explicitly when running workers manually or targeting a different backend:

| Var | Local dev (Azurite) | Purpose |
|---|---|---|
| `STORE_URL` | `UseDevelopmentStorage=true` | remote-store backend |
| `STORE_CONTAINER` | `integration-showcase` | blob container name |

```bash
# Production Azure
STORE_URL=az://myaccount/workflows
STORE_CONTAINER=my-container
```
