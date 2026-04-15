# Claude Reference

## Repository layout

```
integration-showcase/
├── src/integration_showcase/
│   ├── shared/          # Envelope, BlobRef, blob helpers (remote-store wrapper)
│   ├── service_a/       # FastAPI HTTP ingress
│   ├── service_b/       # Inventory Temporal worker
│   ├── service_c/       # Payment Temporal worker
│   ├── service_d/       # Shipping Temporal worker
│   └── workflow/        # OrderWorkflow (saga + compensation)
├── tests/unit/          # Pure-logic unit tests (no Docker)
├── tests/integration/   # Docker-backed integration tests
├── scenarios/           # CLI trigger scripts
├── sdd/                 # Design rules, testing rules, research
│   └── research/        # Concept documents (German)
└── docker-compose.yml   # Temporal, Azurite, Jaeger
```

## Key invariants (quick lookup)

| Rule | Detail |
|---|---|
| Claim-Check | Services exchange Envelope + BlobRef, never raw payload |
| `business_tx_id` | Stable across restarts; in every span attribute + log field |
| `idempotency_key` | `{business_tx_id}:{step_id}:{schema_version}` -- guard all activity side effects |
| Compensation | Reverse order; same envelope; `step_id = "compensate.{original}"` |
| OTel | W3C `traceparent` + `baggage` propagated at every service boundary |
| Blob I/O | Always through `remote-store` Store API; never raw Azure SDK |

## Local dev quick start

```bash
docker compose up -d
hatch run test
python scenarios/run_happy.py
# http://localhost:16686  -- Jaeger UI
# http://localhost:8088   -- Temporal UI
```

## Environment variables

| Variable | Purpose | Dev default |
|---|---|---|
| `STORE_URL` | remote-store backend URL | Azurite connection string |
| `TEMPORAL_ADDRESS` | Temporal server | `localhost:7233` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | OTel collector / Jaeger | `http://localhost:4317` |
| `FORCE_PAYMENT_FAILURE` | Trigger unhappy path in Service C | `false` |
