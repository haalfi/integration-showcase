# Konzept: Temporal + Azure Blob + OpenTelemetry

> **Scope:** Protokoll und Konzept für verteilte Prozess-Orchestrierung über
> mehrere Services hinweg mit vollständiger Audit- und Trace-Fähigkeit.

## 1. Grundidee in einem Satz

> **Temporal** ist das **Prozess-Hauptbuch**, **Azure Blob Storage** ist der
> **Payload-Tresor**, und **OpenTelemetry** ist das **Nervensystem**, das
> beides über Service-Grenzen hinweg verbindet [^temporal-ext] [^azure-lp]
> [^otel-temporal].

Damit entstehen drei klar getrennte Zustandsschichten:

| Schicht              | Träger              | Inhalt                                                                 |
|----------------------|---------------------|------------------------------------------------------------------------|
| Orchestrierung       | Temporal            | Workflow-/Run-IDs, Activities, Retries, Kompensationen, Event History [^temporal-err] |
| Payload              | Azure Blob Storage  | Unveränderliche Datenblobs, Versionen, Metadaten (Claim-Check-Pattern) [^temporal-claim] |
| Observability        | OpenTelemetry       | Traces, Spans, Logs, Business-Korrelations-IDs [^otel-corr]            |

Diese Trennung hält die Temporal-History schlank, erlaubt beliebig große
Nutzdaten und macht jeden Seiteneffekt genau **einem Workflow-Schritt** und
**einer Blob-Referenz** zuordenbar [^temporal-idem].

---

## 2. Architekturüberblick

```mermaid
flowchart LR
    subgraph Edge["Eintrittsservice (Service A)"]
        A1["HTTP/Event<br/>Ingress"]
        A2["Starte Workflow<br/>+ business_tx_id"]
    end

    subgraph Temporal["Temporal Cluster (Prozess-Ledger)"]
        T1["Workflow<br/>order-123"]
        T2["Activity 1<br/>reserve-inventory"]
        T3["Activity 2<br/>charge-payment"]
        T4["Activity 3<br/>dispatch-shipment"]
        T1 --> T2 --> T3 --> T4
    end

    subgraph Blob["Azure Blob Storage (Payload-Tresor)"]
        B1[("input.json<br/>versioned + immutable")]
        B2[("inventory-result.json")]
        B3[("payment-receipt.json")]
    end

    subgraph Services["Fachliche Zielservices"]
        SB["Service B<br/>Inventory DB"]
        SC["Service C<br/>Payment DB"]
        SD["Service D<br/>Shipping DB"]
    end

    subgraph Obs["OpenTelemetry Collector"]
        OT["Traces · Logs · Metrics<br/>traceparent + business_tx_id"]
    end

    A1 --> A2 --> T1
    A2 -. "Payload schreiben" .-> B1
    T2 -- Envelope + blob_ref --> SB --> B2
    T3 -- Envelope + blob_ref --> SC --> B3
    T4 -- Envelope + blob_ref --> SD

    A2 -. Spans .-> OT
    T1 -. Spans .-> OT
    SB -. Spans .-> OT
    SC -. Spans .-> OT
    SD -. Spans .-> OT
```

---

## 3. Kanonischer Envelope

Jeder Hop zwischen Services trägt **denselben Umschlag** — niemals die
Nutzdaten selbst [^temporal-claim] [^otel-corr]:

```json
{
  "workflow_id":    "order-123",
  "run_id":         "run-456",
  "business_tx_id": "tx-789",
  "parent_step_id": "start",
  "step_id":        "reserve-inventory",
  "payload_ref": {
    "blob_url":   "https://acct.blob.core.windows.net/workflows/tx-789/input.json",
    "etag":       "\"0x8DB...\"",
    "version_id": "2026-04-15T12:34:56.0000000Z",
    "sha256":     "…"
  },
  "traceparent":    "00-<trace-id>-<span-id>-01",
  "baggage":        { "correlation.id": "tx-789" },
  "schema_version": "1.0",
  "content_type":   "application/json",
  "idempotency_key":"tx-789:reserve-inventory:v1"
}
```

**Regeln:**

1. Services tauschen **ausschließlich** den Envelope plus Blob-Referenz aus
   [^temporal-ext].
2. Jeder Service lädt den Payload selbst aus Blob Storage, führt **eine**
   lokale Fachaktion aus und persistiert das Ergebnis in seiner **eigenen**
   Datenbank [^saga].
3. Der Service meldet Erfolg/Fehler als Activity-Resultat zurück an Temporal
   mit demselben `business_tx_id` und einer ggf. neuen `payload_ref`
   [^temporal-err].
4. `idempotency_key` schützt gegen Temporal-Retries (Activities dürfen
   mehrfach ausgeführt werden) [^temporal-idem].

---

## 4. Happy Path — Spans & Flow

### 4.1 Sequenzdiagramm (Happy Path)

```mermaid
sequenceDiagram
    autonumber
    participant U as Client
    participant A as Service A<br/>(Entry)
    participant T as Temporal Worker
    participant AB as Azure Blob
    participant B as Service B<br/>(Inventory)
    participant C as Service C<br/>(Payment)
    participant D as Service D<br/>(Shipping)
    participant O as OTel Collector

    U->>A: POST /order (Payload)
    A->>A: generate business_tx_id=tx-789
    A->>AB: PUT workflows/tx-789/input.json
    AB-->>A: etag, version_id
    A->>T: StartWorkflow(order-123, envelope)
    A-->>O: span "ingress"

    T->>B: Activity reserve-inventory(envelope)
    B->>AB: GET input.json
    B->>B: reserve + persist local state
    B->>AB: PUT inventory-result.json
    B-->>T: Result(new payload_ref)
    B-->>O: span "reserve-inventory"

    T->>C: Activity charge-payment(envelope)
    C->>AB: GET inventory-result.json
    C->>C: charge + persist receipt
    C->>AB: PUT payment-receipt.json
    C-->>T: Result(new payload_ref)
    C-->>O: span "charge-payment"

    T->>D: Activity dispatch-shipment(envelope)
    D->>AB: GET payment-receipt.json
    D->>D: dispatch + persist shipment
    D-->>T: Result(OK)
    D-->>O: span "dispatch-shipment"

    T-->>A: Workflow completed
    A-->>U: 200 OK {tx-789}
```

### 4.2 Span-Baum (Happy Path)

```mermaid
flowchart TD
    S0["span: http.ingress<br/>service=A<br/>business_tx_id=tx-789"]
    S1["span: temporal.workflow<br/>workflow_id=order-123<br/>run_id=run-456"]
    S2["span: activity.reserve-inventory<br/>service=B · step_id=reserve-inventory"]
    S2a["span: blob.get input.json"]
    S2b["span: db.write inventory"]
    S2c["span: blob.put inventory-result.json"]
    S3["span: activity.charge-payment<br/>service=C · step_id=charge-payment"]
    S3a["span: blob.get inventory-result.json"]
    S3b["span: db.write payment"]
    S3c["span: blob.put payment-receipt.json"]
    S4["span: activity.dispatch-shipment<br/>service=D · step_id=dispatch-shipment"]
    S4a["span: blob.get payment-receipt.json"]
    S4b["span: db.write shipment"]

    S0 --> S1
    S1 --> S2 --> S2a
    S2 --> S2b
    S2 --> S2c
    S1 --> S3 --> S3a
    S3 --> S3b
    S3 --> S3c
    S1 --> S4 --> S4a
    S4 --> S4b
```

**Alle Spans** tragen als Attribute mindestens:
`business_tx_id`, `workflow_id`, `run_id`, `step_id`, `payload_ref.etag`,
`schema_version` [^otel-corr] [^otel-prop].

---

## 5. Unhappy Path — Retry, Fehler & Kompensation

### 5.1 Szenario

`charge-payment` scheitert dauerhaft → Temporal löst **Saga-Kompensation**
aus [^temporal-err] [^saga]:

1. Retry mit exponentiellem Backoff (Temporal-Retry-Policy).
2. Bei finalem Fehlschlag: Kompensations-Activities **rückwärts** ausführen.
3. Jeder Undo-Schritt trägt denselben Envelope + neue `step_id`
   (z. B. `compensate.reserve-inventory`) und bleibt voll idempotent
   [^temporal-idem].

### 5.2 Sequenzdiagramm (Unhappy Path)

```mermaid
sequenceDiagram
    autonumber
    participant T as Temporal Worker
    participant B as Service B<br/>(Inventory)
    participant C as Service C<br/>(Payment)
    participant AB as Azure Blob
    participant O as OTel Collector

    Note over T,C: Happy Prefix: reserve-inventory erfolgreich

    T->>C: Activity charge-payment(envelope, attempt=1)
    C->>AB: GET inventory-result.json
    C--xT: Error: gateway_timeout
    C-->>O: span "charge-payment" status=ERROR

    Note over T: Retry-Policy: 2s Backoff
    T->>C: Activity charge-payment(envelope, attempt=2)
    C--xT: Error: insufficient_funds (non-retryable)
    C-->>O: span "charge-payment" status=ERROR

    Note over T: Non-retryable → Saga-Kompensation
    T->>B: Activity compensate.reserve-inventory(envelope)
    B->>B: release reservation (idempotent)
    B->>AB: PUT compensation-result.json
    B-->>T: Result(OK)
    B-->>O: span "compensate.reserve-inventory"

    T-->>T: Workflow failed with compensations applied
    T-->>O: span "temporal.workflow" status=ERROR<br/>outcome=compensated
```

### 5.3 Zustandsdiagramm (Workflow-Outcome)

```mermaid
stateDiagram-v2
    [*] --> Started
    Started --> ReserveInventory
    ReserveInventory --> ChargePayment: OK
    ReserveInventory --> Failed: unrecoverable

    ChargePayment --> DispatchShipment: OK
    ChargePayment --> Retrying: transient error
    Retrying --> ChargePayment: backoff elapsed
    Retrying --> Compensating: non-retryable / budget exhausted

    DispatchShipment --> Completed: OK
    DispatchShipment --> Compensating: error

    Compensating --> CompensatePayment: if charged
    CompensatePayment --> CompensateInventory
    Compensating --> CompensateInventory: if only reserved
    CompensateInventory --> Compensated

    Completed --> [*]
    Compensated --> [*]
    Failed --> [*]
```

### 5.4 Span-Baum (Unhappy Path)

```mermaid
flowchart TD
    W["span: temporal.workflow<br/>status=ERROR · outcome=compensated"]
    R["span: activity.reserve-inventory · OK"]
    P1["span: activity.charge-payment<br/>attempt=1 · ERROR gateway_timeout"]
    P2["span: activity.charge-payment<br/>attempt=2 · ERROR insufficient_funds"]
    CR["span: activity.compensate.reserve-inventory · OK"]

    W --> R
    W --> P1
    W --> P2
    W --> CR

    R -. "sibling" .- P1
    P1 -. "retry-of" .- P2
    P2 -. "triggers" .- CR
```

Dank identischem `business_tx_id` auf **allen** Spans lässt sich der
komplette Pfad — inklusive Retries und Kompensationen — mit **einer** Query
im Tracing-Backend rekonstruieren [^otel-corr].

---

## 6. Traceability-Regeln (Checkliste)

- [ ] `business_tx_id` steckt in **Span-Attributen UND Log-Feldern**, nicht
      nur in Headern [^otel-corr].
- [ ] **W3C Trace Context** (`traceparent`) + **Baggage** an jeder
      Service-Grenze propagieren [^otel-prop].
- [ ] Blob-**Metadaten** enthalten `workflow_id`, `run_id`, `step_id`,
      `schema_version` [^azure-ret].
- [ ] Activities sind **idempotent** (Temporal darf wiederholen) —
      `idempotency_key` in jeder Fachoperation prüfen [^temporal-idem].
- [ ] Hohe Kardinalitäts-IDs **nicht** in Metrik-Labels — nur Traces/Logs
      [^otel-corr].
- [ ] Jeder persistierte Seiteneffekt ist **genau einem** Workflow-Schritt
      **und einer** Blob-Referenz zuordenbar [^temporal-err].

---

## 7. Guardrails & Designentscheidungen

| Entscheidung                               | Begründung                                                        | Quelle                 |
|--------------------------------------------|-------------------------------------------------------------------|------------------------|
| Claim-Check-Pattern (nur Referenzen)       | Temporal-History klein, Replay schnell, Payloads frei skalierbar  | [^temporal-claim]      |
| Blob-I/O via remote-store                  | Backend-Wechsel (Azurite/Azure/Local) ohne Code-Änderung          | —                      |
| Fach-Outcome in Service-eigener DB         | Orchestrierung und Domänenzustand bleiben entkoppelt              | [^saga]                |
| `business_tx_id` ≠ `workflow_id`           | Fachliche Korrelation überlebt Workflow-Restarts / Child-Workflows| [^otel-corr]           |
| Kompensation als eigene Activity           | Auch Undo ist audit- und replay-fähig                             | [^temporal-err]        |
| OTel-Collector zentral                     | Einheitliches Schema für Spans aus Temporal-Worker und Services   | [^otel-temporal]       |

---

## 8. Glossar & Feldherkunft

| Feld               | Schicht            | Definition / Herkunft                                                                                                               |
|--------------------|--------------------|-------------------------------------------------------------------------------------------------------------------------------------|
| `workflow_id`      | Prozess-Hauptbuch  | Eindeutige Geschäfts-ID des Workflows, vom Starter vergeben; Primärschlüssel in der Temporal Event History [^temporal-err].         |
| `run_id`           | Prozess-Hauptbuch  | Von Temporal vergebene Lauf-ID; unterscheidet mehrere Ausführungen desselben `workflow_id` [^temporal-err].                         |
| `business_tx_id`   | Nervensystem       | Fachliche Korrelations-ID; stabil über Workflow-Restarts/Child-Workflows [^otel-corr].                                              |
| `parent_step_id`   | Prozess-Hauptbuch  | Vorheriger Schritt in der Saga; erlaubt Rekonstruktion der Kette [^saga].                                                           |
| `step_id`          | Prozess-Hauptbuch  | Logischer Name des aktuellen Aktivitätsschritts; landet als Span-Attribut [^otel-temporal].                                         |
| `payload_ref`      | Payload-Tresor     | Claim-Check-Referenz auf das Blob [^temporal-claim].                                                                                |
| `traceparent`      | Nervensystem       | W3C Trace Context Header; verknüpft Spans über Service-Grenzen hinweg [^otel-prop].                                                 |
| `baggage`          | Nervensystem       | W3C Baggage: fachliche Key-Value-Paare, die kontextuell propagiert werden [^otel-prop].                                             |
| `schema_version`   | übergreifend       | Semver des Envelope-/Payload-Schemas; ermöglicht Kompatibilität bei Weiterentwicklung.                                              |
| `idempotency_key`  | Prozess-Hauptbuch  | Deduplikations-Schlüssel für Activity-Retries; Formel: `business_tx_id:step_id:schema_version` [^temporal-idem].                    |

---

## 9. Referenzen nach Concern

### 9.1 Prozess-Hauptbuch (Temporal)

- [^temporal-err]: Temporal — *Error handling in distributed systems*.
  <https://temporal.io/blog/error-handling-in-distributed-systems>
- [^temporal-idem]: Temporal — *Idempotency and durable execution*.
  <https://temporal.io/blog/idempotency-and-durable-execution>
- [^temporal-claim]: Temporal AI Cookbook — *Claim-check pattern (Python)*.
  <https://docs.temporal.io/ai-cookbook/claim-check-pattern-python>
- [^temporal-ext]: Temporal Docs — *External storage for large payloads*.
  <https://docs.temporal.io/external-storage>
- [^saga]: Federico Bevione (dev.to) — *Transactions in Microservices,
  Part 3: Saga Pattern with Orchestration and Temporal.io*.
  <https://dev.to/federico_bevione/transactions-in-microservices-part-3-saga-pattern-with-orchestration-and-temporalio-3e17>

### 9.2 Payload-Tresor (Azure Blob Storage)

- [^azure-lp]: Microsoft Learn — *Durable Task Scheduler: large payloads*.
  <https://learn.microsoft.com/en-us/azure/durable-task/scheduler/durable-task-scheduler-large-payloads>
- [^azure-immut]: Microsoft Learn — *Immutable storage for Azure Blob Storage (Overview)*.
  <https://learn.microsoft.com/en-us/azure/storage/blobs/immutable-storage-overview>
- [^azure-ret]: OneUptime — *How to configure Azure Blob Storage retention policies for compliance*.
  <https://oneuptime.com/blog/post/2026-02-16-how-to-configure-azure-blob-storage-retention-policies-for-compliance/view>

### 9.3 Nervensystem (OpenTelemetry)

- [^otel-temporal]: OneUptime — *Instrument Temporal.io workflows with OpenTelemetry*.
  <https://oneuptime.com/blog/post/2026-02-06-instrument-temporal-io-workflows-opentelemetry/view>
- [^otel-corr]: OneUptime — *OTel request-scoped correlation IDs*.
  <https://oneuptime.com/blog/post/2026-02-06-otel-request-scoped-correlation-ids/view>
- [^otel-prop]: OneUptime — *Distributed tracing context propagation*.
  <https://oneuptime.com/blog/post/2026-02-02-distributed-tracing-context-propagation/view>

[^temporal-err]: <https://temporal.io/blog/error-handling-in-distributed-systems>
[^temporal-idem]: <https://temporal.io/blog/idempotency-and-durable-execution>
[^temporal-claim]: <https://docs.temporal.io/ai-cookbook/claim-check-pattern-python>
[^temporal-ext]: <https://docs.temporal.io/external-storage>
[^saga]: <https://dev.to/federico_bevione/transactions-in-microservices-part-3-saga-pattern-with-orchestration-and-temporalio-3e17>
[^azure-lp]: <https://learn.microsoft.com/en-us/azure/durable-task/scheduler/durable-task-scheduler-large-payloads>
[^azure-immut]: <https://learn.microsoft.com/en-us/azure/storage/blobs/immutable-storage-overview>
[^azure-ret]: <https://oneuptime.com/blog/post/2026-02-16-how-to-configure-azure-blob-storage-retention-policies-for-compliance/view>
[^otel-temporal]: <https://oneuptime.com/blog/post/2026-02-06-instrument-temporal-io-workflows-opentelemetry/view>
[^otel-corr]: <https://oneuptime.com/blog/post/2026-02-06-otel-request-scoped-correlation-ids/view>
[^otel-prop]: <https://oneuptime.com/blog/post/2026-02-02-distributed-tracing-context-propagation/view>
