# Konzept: Temporal + Blob Storage + OpenTelemetry

> **Scope:** Protokoll und Konzept für verteilte Prozess-Orchestrierung über
> mehrere Services hinweg mit vollständiger Audit- und Trace-Fähigkeit.
>
> **Status:** Diese Seite ist die **normative Referenz** für die hier
> gesammelten Guides und Nachschlagewerke. Produktionshärtung,
> Migrations- und Betriebsthemen werden bewusst nicht behandelt; sie
> gehören in einen separaten Hardening-Track.

## 1. Grundidee in einem Satz

> **Temporal** ist das **Prozess-Hauptbuch**, **Blob Storage** ist der
> **Payload-Tresor**, und **OpenTelemetry** ist das **Nervensystem**, das
> beides über Service-Grenzen hinweg verbindet.

Damit entstehen drei klar getrennte Zustandsschichten:

| Schicht        | Träger         | Inhalt                                                                              |
| -------------- | -------------- | ----------------------------------------------------------------------------------- |
| Orchestrierung | Temporal       | Workflow- und Run-IDs, Activities, Retries, Kompensationen, Event History           |
| Payload        | Blob Storage   | Inhaltsadressierte Datenblobs (SHA-256-verifiziert), Metadaten (Claim-Check-Pattern) |
| Observability  | OpenTelemetry  | Traces, Spans, Logs, Business-Korrelations-IDs                                      |

Diese Trennung hält die Temporal-History schlank, erlaubt beliebig große
Nutzdaten und macht jeden Seiteneffekt genau **einem Workflow-Schritt** und
**einer Blob-Referenz** zuordenbar.

---

## 2. Architekturüberblick

```mermaid
flowchart LR
    subgraph Edge["Entry Service (Service A)"]
        A1["HTTP/Event<br/>Ingress"]
        A2["StartWorkflow<br/>+ business_tx_id"]
    end

    subgraph Temporal["Temporal Cluster (Prozess-Ledger)"]
        T1["Workflow<br/>order-123"]
        T2["Activity 1<br/>reserve-inventory"]
        T3["Activity 2<br/>charge-payment"]
        T4["Activity 3<br/>dispatch-shipment"]
        T1 --> T2 --> T3 --> T4
    end

    subgraph Blob["Blob Storage (Payload-Tresor)"]
        B1[("input.json<br/>inhaltsadressiert (SHA-256)")]
        B2[("inventory-result.json")]
        B3[("payment-receipt.json")]
    end

    subgraph Services["Business Services"]
        SB["Service B<br/>Inventory DB"]
        SC["Service C<br/>Payment DB"]
        SD["Service D<br/>Shipping DB"]
    end

    subgraph Obs["Tracing Backend"]
        OT["Traces<br/>traceparent + business_tx_id"]
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

Jeder Hop zwischen Services trägt **denselben Envelope**, niemals die
Nutzdaten selbst. Der Envelope ist der Vertrag: alle Felder sind sichtbar,
keine versteckten Seitenkanäle.

### 3.1 JSON-Beispiel

```jsonc
{
  "workflow_id": "order-tx-789",
  "run_id": "run-456",
  "business_tx_id": "tx-789",
  "parent_step_id": "start",
  "step_id": "reserve-inventory",
  "payload_ref": {
    "blob_url": "workflows/tx-789/input.json",
    "sha256": "…",
    "etag": "0x8da4f1c93b7e9f2a"
  },
  "traceparent": "00-<trace-id>-<span-id>-01",
  "tracestate": "",
  "baggage": { "correlation.id": "tx-789" },
  "schema_version": "1.0",
  "content_type": "application/json",
  "idempotency_key": "tx-789:reserve-inventory:1.0"
}
```

### 3.2 Felder

Kurz beschrieben, gruppiert nach Concern. Details, Formate und
Invarianten stehen in den Guides.

**Prozess-Hauptbuch (Temporal).**

| Feld               | Beschreibung                                                                                                  |
| ------------------ | ------------------------------------------------------------------------------------------------------------- |
| `workflow_id`      | Deterministische Geschäfts-ID des Workflows. Primärschlüssel in der Temporal Event History.                   |
| `run_id`           | Von Temporal vergebene Lauf-ID. Unterscheidet mehrere Ausführungen desselben `workflow_id`.                   |
| `parent_step_id`   | Vorheriger Schritt in der Saga. Erlaubt Rekonstruktion der Kette. Bei Workflow-Start: `null`.                 |
| `step_id`          | Logischer Name des aktuellen Aktivitätsschritts. Landet als Span-Attribut.                                    |
| `idempotency_key`  | Deduplikations-Schlüssel für Activity-Retries. Format: `{business_tx_id}:{step_id}:{schema_version}`.         |

**Payload-Tresor (Blob Storage).**

| Feld                   | Beschreibung                                                                                                      |
| ---------------------- | ----------------------------------------------------------------------------------------------------------------- |
| `payload_ref.blob_url` | **Pflicht.** Pfad oder URL zum Blob. Konvention: `workflows/{business_tx_id}/{step_id}.json`.                     |
| `payload_ref.sha256`   | **Pflicht.** Hex-SHA-256 des Byte-Inhalts. Einzige backend-unabhängige Integritätsgarantie.                       |
| `payload_ref.etag`     | **Optional.** Vom Storage Backend nach dem Schreiben geliefert (Schreibantwort oder Properties-Read).             |
| `content_type`         | MIME-Typ des referenzierten Payloads. Default: `application/json`.                                                |

**Nervensystem (OpenTelemetry).**

| Feld            | Beschreibung                                                                                             |
| --------------- | -------------------------------------------------------------------------------------------------------- |
| `business_tx_id`| Fachliche Korrelations-ID. Stabil über Workflow-Restarts und Child-Workflows hinweg.                     |
| `traceparent`   | W3C Trace Context Header. Verknüpft Spans über Service-Grenzen hinweg.                                   |
| `tracestate`    | W3C Trace Context Begleitkontext. Vendor-spezifische Trace-Zustände.                                     |
| `baggage`       | W3C Baggage. Fachliche Key-Value-Paare, die kontextuell propagiert werden.                               |

**Übergreifend.**

| Feld              | Beschreibung                                                                                            |
| ----------------- | ------------------------------------------------------------------------------------------------------- |
| `schema_version`  | Semver des Envelope- und Payload-Schemas. Ermöglicht Kompatibilität bei Weiterentwicklung.              |

### 3.3 Regeln

1. **Payloads sind nie Teil der Temporal-History.** Services tauschen
   **ausschließlich** den Envelope plus Blob-Referenz aus. Raw payloads
   überqueren keine Service-Grenze und landen niemals als
   Workflow-Argument, Activity-Input oder Activity-Result. Die Regel
   gilt unabhängig von der Größe und ist **keine** Optimierung, sondern
   getragen von Security, Cloud Governance und Datenschutz:
   - Temporal-Events sind für Retention, Event-Sourcing und Debugging
     langzeitstabil. Fachdaten gehören in Systeme mit fachlichen
     Retention- und Löschregeln (Blob Storage mit Lifecycle Policy,
     WORM, Legal Hold), nicht in einen Orchestrator.
   - Temporal-History ist oft breit sichtbar (Web UI, Support,
     Entwickler). Personenbezogene oder vertrauliche Daten dürfen
     diesen Sichtbarkeitsradius nicht erben.
   - Der Blob ist der **einzige** Ort, an dem der Payload liegt; damit
     greifen Verschlüsselung, Access Policies, Audit Logging und
     DSGVO-Löschansprüche an genau **einer** Stelle.
2. Jeder Service lädt den Payload selbst aus Blob Storage, führt **eine**
   lokale Fachaktion aus und persistiert das Ergebnis in seiner **eigenen**
   Datenbank.
3. Der Service meldet Erfolg oder Fehler als Activity-Resultat an
   Temporal zurück, mit identischem `business_tx_id` und einer ggf. neuen
   `payload_ref`. Das Resultat enthält **keine** Fachdaten.
4. `idempotency_key` schützt gegen Temporal-Retries. Jede Fachoperation
   prüft diesen Schlüssel, bevor sie einen Seiteneffekt durchführt.

---

## 4. Happy Path

### 4.1 Sequenzdiagramm

```mermaid
sequenceDiagram
    autonumber
    participant U as Client
    participant A as Service A<br/>(Entry)
    participant T as Temporal Worker
    participant AB as Blob Storage
    participant B as Service B<br/>(Inventory)
    participant C as Service C<br/>(Payment)
    participant D as Service D<br/>(Shipping)
    participant O as Tracing Backend

    U->>A: POST /order (Payload)
    A->>A: generate business_tx_id=tx-789
    A->>AB: PUT workflows/tx-789/input.json
    AB-->>A: sha256, etag
    A->>T: StartWorkflow(order-tx-789, envelope)
    A-->>U: 202 Accepted {business_tx_id, workflow_id, traceparent}
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

    Note over U,T: Status-Propagation ist entkoppelt vom HTTP-Ingress.
    U-->>A: Status-Query (Endpoint / Webhook / Event-Stream)
    A-->>U: Workflow-Ergebnis
```

**Status-Propagation.** Der HTTP-Aufruf an Service A endet mit
`202 Accepted`, sobald der Workflow gestartet ist. Der Endzustand wird
entkoppelt vom Ingress-Request ausgeliefert: über einen Status-Endpunkt,
einen Webhook oder einen Event-Stream. Externe Clients sprechen nicht
direkt mit dem Orchestrator; die Übersetzung „Workflow-Status in
fachliche Repräsentation" liegt im Entry Service.

### 4.2 Span-Baum

```mermaid
flowchart TD
    S0["span: http.ingress<br/>service=A<br/>business_tx_id=tx-789"]
    S1["span: temporal.workflow<br/>workflow_id=order-tx-789<br/>run_id=run-456"]
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

Jeder Span trägt als Attribute mindestens:
`business_tx_id`, `workflow_id`, `run_id`, `step_id`,
`payload_ref_sha256`, `schema_version`.

---

## 5. Unhappy Path: Retry, Fehler und Kompensation

### 5.1 Szenario

`charge-payment` scheitert dauerhaft. Temporal löst daraufhin die
**Saga-Kompensation** aus:

1. Retry mit exponentiellem Backoff (Temporal Retry Policy).
2. Bei finalem Fehlschlag: Kompensations-Activities **rückwärts** ausführen.
3. Jeder Undo-Schritt trägt denselben Envelope plus neue `step_id`
   (z. B. `compensate.reserve-inventory`) und bleibt voll idempotent.

### 5.2 Sequenzdiagramm

```mermaid
sequenceDiagram
    autonumber
    participant T as Temporal Worker
    participant B as Service B<br/>(Inventory)
    participant C as Service C<br/>(Payment)
    participant AB as Blob Storage
    participant O as Tracing Backend

    Note over T,C: Happy Prefix: reserve-inventory erfolgreich

    T->>C: Activity charge-payment(envelope, attempt=1)
    C->>AB: GET inventory-result.json
    C--xT: Error: gateway_timeout
    C-->>O: span "charge-payment" status=ERROR

    Note over T: Retry Policy: 2s Backoff
    T->>C: Activity charge-payment(envelope, attempt=2)
    C--xT: Error: insufficient_funds (non-retryable)
    C-->>O: span "charge-payment" status=ERROR

    Note over T: Non-retryable: Saga-Kompensation
    T->>B: Activity compensate.reserve-inventory(envelope)
    B->>B: release reservation (idempotent)
    B->>AB: PUT compensation-result.json
    B-->>T: Result(OK)
    B-->>O: span "compensate.reserve-inventory"

    T-->>T: Workflow failed with compensations applied
    T-->>O: span "temporal.workflow" status=ERROR<br/>outcome=compensated
```

### 5.3 Zustandsdiagramm

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

### 5.4 Span-Baum

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
komplette Pfad inklusive Retries und Kompensationen mit **einer** Query
im Tracing Backend rekonstruieren.

---

## 6. Traceability-Regeln

Normative Anforderungen an jede Implementierung dieses Konzepts:

1. **Korrelation.** `business_tx_id` steckt in **Span-Attributen und
   Log-Feldern**, nicht nur in Headern.
2. **Kontextpropagation.** **W3C Trace Context** (`traceparent`,
   `tracestate`) und **Baggage** werden an jeder Service-Grenze
   weitergereicht.
3. **Blob-Metadaten.** Jedes geschriebene Blob trägt die Attribute
   `workflow_id`, `run_id`, `step_id`, `schema_version`, `idempotency_key`
   als Storage-seitige Metadaten. Read-back erfolgt über die
   Properties-API des Backends.
4. **Idempotenz.** Activities sind **idempotent**. Temporal darf
   wiederholen. Jede Fachoperation prüft `idempotency_key`, bevor sie
   einen Seiteneffekt durchführt.
5. **Metriken-Cardinality.** Hochkardinale IDs (`business_tx_id`,
   `workflow_id`, `run_id`) gehören **nicht** in Metrik-Labels, nur in
   Traces und Logs. Metrik-Dimensionen bleiben niedrigkardinal
   (`outcome`, `step_id`, `service`).
6. **Zuordenbarkeit.** Jeder persistierte Seiteneffekt ist **genau einem**
   Workflow-Schritt **und einer** Blob-Referenz zuordenbar.

---

## 7. Referenzen nach Concern

### Prozess-Hauptbuch (Temporal)

- Temporal: [_Error handling in distributed systems_](https://temporal.io/blog/error-handling-in-distributed-systems)
- Temporal: [_Idempotency and durable execution_](https://temporal.io/blog/idempotency-and-durable-execution)
- Temporal Docs: [_External storage for large payloads_](https://docs.temporal.io/external-storage)
- Federico Bevione (dev.to): [_Transactions in Microservices, Part 3: Saga Pattern with Orchestration and Temporal.io_](https://dev.to/federico_bevione/transactions-in-microservices-part-3-saga-pattern-with-orchestration-and-temporalio-3e17)

### Payload-Tresor (Blob Storage)

- Microsoft Learn: [_Durable Task Scheduler: large payloads_](https://learn.microsoft.com/en-us/azure/durable-task/scheduler/durable-task-scheduler-large-payloads)
- Microsoft Learn: [_Immutable storage for Azure Blob Storage (Overview)_](https://learn.microsoft.com/en-us/azure/storage/blobs/immutable-storage-overview)

### Nervensystem (OpenTelemetry)

- OpenTelemetry: [_Baggage_](https://opentelemetry.io/docs/concepts/signals/baggage/)
- OpenTelemetry: [_Context Propagation_](https://opentelemetry.io/docs/concepts/context-propagation/)
- W3C: [_Trace Context_](https://www.w3.org/TR/trace-context/)
- W3C: [_Propagation format for distributed context: Baggage_](https://www.w3.org/TR/baggage/)
- OneUptime: [_Instrument Temporal.io workflows with OpenTelemetry_](https://oneuptime.com/blog/post/2026-02-06-instrument-temporal-io-workflows-opentelemetry/view)
