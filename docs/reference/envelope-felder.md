# Reference: Envelope-Felder

Vollständige Feldliste des kanonischen Envelope, gruppiert nach Concern.
Jeder Eintrag: Typ, Pflicht/optional, Format, Kurzbeschreibung.

Narrative Erklärung: siehe [Konzept §3](../konzept.md#3-kanonischer-envelope).

## Prozess-Hauptbuch (Temporal)

| Feld              | Typ         | Pflicht | Format / Default                                                      | Beschreibung                                                                             |
| ----------------- | ----------- | ------- | --------------------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| `workflow_id`     | `string`    | ja      | deterministisch aus Fachkontext, z. B. `order-{business_tx_id}`       | Primärschlüssel in der Temporal Event History. Beim Start vom Caller vergeben.           |
| `run_id`          | `string`    | ja      | Temporal vergibt; leer `""` solange noch kein Handle existiert        | Unterscheidet mehrere Ausführungen desselben `workflow_id` (z. B. nach ContinueAsNew).   |
| `parent_step_id`  | `string?`   | nein    | `null` beim Workflow-Start                                            | Vorheriger `step_id`. Erlaubt Rekonstruktion der Kette.                                  |
| `step_id`         | `string`    | ja      | kebab-case, z. B. `reserve-inventory`, `compensate.charge-payment`    | Logischer Name des aktuellen Aktivitätsschritts. Landet als Span-Attribut.               |
| `idempotency_key` | `string`    | ja      | `{business_tx_id}:{step_id}:{schema_version}`                         | Deduplikations-Schlüssel für Activity-Retries. Wird bei jedem `advance` neu berechnet.   |

## Payload-Tresor (Blob Storage)

| Feld                   | Typ      | Pflicht  | Format / Default                                           | Beschreibung                                                                                 |
| ---------------------- | -------- | -------- | ---------------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| `payload_ref.blob_url` | `string` | ja       | Pfad oder URL, z. B. `workflows/{business_tx_id}/…json`    | Claim-Check-Referenz auf das Blob.                                                           |
| `payload_ref.sha256`   | `string` | ja       | Hex, 64 Zeichen                                            | SHA-256 des Byte-Inhalts. Einzige backend-unabhängige Integritätsgarantie.                   |
| `payload_ref.etag`     | `string` | nein     | Default `""`; vom Storage Backend nach dem Schreiben       | Storage-seitiger ETag. Nicht jedes Backend liefert einen.                                    |
| `content_type`         | `string` | ja       | Default `application/json`                                 | MIME-Typ des referenzierten Payloads.                                                        |

## Nervensystem (OpenTelemetry)

| Feld             | Typ                 | Pflicht | Format / Default                                        | Beschreibung                                                                       |
| ---------------- | ------------------- | ------- | ------------------------------------------------------- | ---------------------------------------------------------------------------------- |
| `business_tx_id` | `string`            | ja      | UUID oder fachlicher Schlüssel                          | Stabile fachliche Korrelations-ID. Überlebt Workflow-Restarts und Child-Workflows. |
| `traceparent`    | `string`            | ja      | W3C-Format: `00-<trace-id>-<span-id>-<flags>`           | W3C Trace Context Header.                                                          |
| `tracestate`     | `string`            | nein    | Default `""`                                            | W3C Trace Context Begleitkontext. Vendor-spezifische Trace-Zustände.               |
| `baggage`        | `map<string,string>`| nein    | Default `{}`; Minimum: `correlation.id = business_tx_id`| W3C Baggage. Fachliche Key-Value-Paare.                                            |

## Übergreifend

| Feld             | Typ      | Pflicht | Format / Default   | Beschreibung                                                                |
| ---------------- | -------- | ------- | ------------------ | --------------------------------------------------------------------------- |
| `schema_version` | `string` | ja      | Semver, z. B. `1.0`| Version des Envelope- und Payload-Schemas. Treiber für Kompatibilität.      |

## Invarianten

- `idempotency_key` ist nicht leer.
- `payload_ref.blob_url` und `payload_ref.sha256` sind nicht leer.
- `traceparent` folgt dem W3C-Format.
- Beim Fortschreiten in den nächsten Schritt (`advance`) bleiben
  `workflow_id`, `run_id`, `business_tx_id`, `schema_version`,
  `traceparent`, `tracestate`, `baggage` erhalten; `parent_step_id`
  erhält den alten `step_id`; `step_id`, `payload_ref`, `idempotency_key`
  werden neu gesetzt.

## Siehe auch

- [Reference: Regeln](regeln.md)
- [Reference: Korrelationsattribute](korrelationsattribute.md)
- [Konzept §3](../konzept.md#3-kanonischer-envelope)
