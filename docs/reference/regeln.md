# Reference: Regeln und Invarianten

Normative Regeln, gruppiert nach Concern. Jede Regel ist MUSS (verbindlich),
SOLL (empfohlen) oder NICHT (Verbot).

Narrative Einordnung: siehe [Konzept §6](../konzept.md#6-traceability-regeln).

## Temporal

### MUSS

| Regel | Beschreibung |
| --- | --- |
| **T-1.** | `workflow_id` ist **deterministisch** aus dem Fachkontext abgeleitet. Ingress-Retries erzeugen keinen neuen Workflow. |
| **T-2.** | Activities sind **idempotent**. Jede Fachoperation prüft `idempotency_key`, bevor sie einen Seiteneffekt durchführt. |
| **T-3.** | Pro Service existiert eine **eigene Task Queue**. Activities laufen ausschließlich auf dem Worker ihres Zielservices. |
| **T-4.** | Kompensations-Activities laufen **unabhängig voneinander**: Schlägt eine Kompensation fehl, werden die übrigen trotzdem ausgeführt. |
| **T-5.** | Non-retryable Fehler (fachliche Entscheidungen wie „insufficient funds") stehen in `non_retryable_error_types` der Retry Policy. |

### SOLL

| Regel | Beschreibung |
| --- | --- |
| **T-6.** | Retry Policy: exponentieller Backoff mit endlicher `maximum_attempts`. Kein unbegrenzter Retry. |
| **T-7.** | Activity-Timeouts (Start-to-Close, Schedule-to-Close) sind explizit gesetzt. |

### NICHT

| Regel | Beschreibung |
| --- | --- |
| **T-8.** | Raw payloads werden **nicht** als Activity-Argument übergeben. Cross-Service-Daten liegen in Blob Storage, nicht in der Event History. |
| **T-9.** | Activity-Ergebnisse enthalten **nicht** die persistierten Fachdaten, sondern eine neue `payload_ref`. |

## Blob Storage

### MUSS

| Regel | Beschreibung |
| --- | --- |
| **B-1.** | Jedes Blob wird **einmal geschrieben** und danach nur gelesen (write-once-read-many). Ergebnisse eines neuen Schritts landen unter einem **neuen** `blob_url`. |
| **B-2.** | Der Uploader berechnet `sha256` **vor** dem Upload. Jeder Konsument verifiziert den Hash nach dem Download. |
| **B-3.** | Jedes Blob trägt die Storage-seitigen Metadaten `workflow_id`, `run_id`, `step_id`, `schema_version`, `idempotency_key`. |
| **B-4.** | Pfadkonvention: `workflows/{business_tx_id}/{step_id}.json`. Erlaubt präfixbasiertes Listing pro Transaktion. |

### SOLL

| Regel | Beschreibung |
| --- | --- |
| **B-5.** | Große Payloads werden gestreamt (nicht in einem Schwung in den Speicher geladen). |
| **B-6.** | Der `etag`, falls vom Backend geliefert, wird im Envelope mitgeführt. Konsumenten dürfen sich nicht auf seine Anwesenheit verlassen. |

### NICHT

| Regel | Beschreibung |
| --- | --- |
| **B-7.** | Keine Overwrites desselben `blob_url` in einem laufenden Workflow. Jeder Schritt schreibt sein eigenes Blob. |
| **B-8.** | Raw payloads werden **nicht** inline in Logs, Spans oder Metriken aufgenommen. |

## OpenTelemetry

### MUSS

| Regel | Beschreibung |
| --- | --- |
| **O-1.** | W3C Trace Context (`traceparent`, `tracestate`) und Baggage werden an jeder Service-Grenze propagiert. Träger: der Envelope. |
| **O-2.** | Jeder Span trägt die Attribute `business_tx_id`, `workflow_id`, `run_id`, `step_id`, `payload_ref_sha256`, `schema_version`. |
| **O-3.** | Strukturierte Logs enthalten `business_tx_id`, `trace_id`, `span_id` als Felder. |
| **O-4.** | Non-retryable Fehler werden als Span-Event `exception` mit `error.type` und `error.message` markiert; der Span erhält Status `ERROR`. |

### SOLL

| Regel | Beschreibung |
| --- | --- |
| **O-5.** | Retries werden als eigene Spans modelliert, mit Attribut `attempt` (1-basiert). |
| **O-6.** | Kompensations-Spans tragen im `step_id` das Präfix `compensate.` und referenzieren den ausgelösenden Fehler als Span-Event. |

### NICHT

| Regel | Beschreibung |
| --- | --- |
| **O-7.** | Hochkardinale IDs (`business_tx_id`, `workflow_id`, `run_id`) erscheinen **nicht** in Metrik-Labels. Nur niedrigkardinale Dimensionen (`outcome`, `step_id`, `service`) sind erlaubt. |
| **O-8.** | Raw payloads oder personenbezogene Daten erscheinen **nicht** in Span-Attributen oder Log-Feldern. |

## Siehe auch

- [Reference: Envelope-Felder](envelope-felder.md)
- [Reference: Korrelationsattribute](korrelationsattribute.md)
- [Reference: Fehlertaxonomie](fehlertaxonomie.md)
