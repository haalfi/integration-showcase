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
