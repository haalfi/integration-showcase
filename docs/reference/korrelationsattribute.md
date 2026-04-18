# Reference: Korrelationsattribute

Sechs Attribute tragen fachliche und technische Korrelation durch alle
Schichten. Diese Seite dokumentiert, **wo** sie jeweils sichtbar sein
müssen.

## Die sechs Attribute

| Attribut              | Quelle                                        |
| --------------------- | --------------------------------------------- |
| `business_tx_id`      | vom Entry Service vergeben                    |
| `workflow_id`         | vom Entry Service vergeben (deterministisch)  |
| `run_id`              | von Temporal vergeben                         |
| `step_id`             | vom Workflow pro Activity vergeben            |
| `payload_ref_sha256`  | vom Uploader berechnet                        |
| `schema_version`      | Envelope-Versionierung                        |

## Sichtbarkeit nach Kanal

| Attribut              | Envelope | Baggage | Span-Attribut | Log-Feld | Blob-Metadata |
| --------------------- | :------: | :-----: | :-----------: | :------: | :-----------: |
| `business_tx_id`      | ja       | ja      | ja            | ja       | indirekt [^1] |
| `workflow_id`         | ja       | ja      | ja            | ja       | ja            |
| `run_id`              | ja       | ja      | ja            | ja       | ja            |
| `step_id`             | ja       | ja      | ja            | ja       | ja            |
| `payload_ref_sha256`  | ja       | nein    | ja            | ja       | ja (als ETag-nahe Integrität; Backend-spezifisch) |
| `schema_version`      | ja       | ja      | ja            | ja       | ja            |

[^1]: `business_tx_id` steckt im Blob-Pfad
(`workflows/{business_tx_id}/…`); eine zusätzliche Metadata-Zeile ist
üblich, aber nicht strikt erforderlich.

## Kanal-Details

### Envelope

Primärquelle. Jeder Service liest die Werte aus dem eingehenden Envelope
und reicht sie unverändert weiter (außer `step_id` und `idempotency_key`,
die bei `advance` neu gesetzt werden).

### Baggage

W3C Baggage transportiert die Werte innerhalb eines Prozesses über
SDK-Grenzen. Ein Baggage-Span-Processor liest Baggage beim Span-Start
und setzt nicht-leere Werte als Span-Attribute. So tragen auch
tief verschachtelte Child-Spans (z. B. `blob.put`, `db.write`) die
Attribute, ohne sie explizit zu setzen.

### Span-Attribut

Verpflichtend auf jedem Span (siehe Regeln O-2 in
[`regeln.md`](regeln.md)). Attributnamen wörtlich wie oben, ohne
Präfix.

### Log-Feld

Strukturierte Logs injizieren `business_tx_id`, `trace_id`, `span_id`
zusätzlich zu den Standard-OTel-Feldern. Das erlaubt Logsuche nach
fachlicher ID im Log Backend, unabhängig vom Trace Backend.

### Blob-Metadata

Storage-seitig pro Blob gesetzt: `workflow_id`, `run_id`, `step_id`,
`schema_version`, `idempotency_key`. Read-back über die Properties-API
des Backends. Erlaubt Forensik (welcher Workflow-Schritt hat dieses
Blob geschrieben) ohne Envelope-Zugriff.

## Namenskonventionen

- Span-Attribute und Log-Felder: exakt die Feldnamen oben
  (snake_case, `business_tx_id`, nicht `businessTxId`).
- Baggage-Keys: gleiche Namen; Zusätze wie `correlation.id` sind
  erlaubt, ersetzen aber die primären Keys nicht.
- Blob-Metadata: exakt die Feldnamen oben. Manche Backends
  normalisieren Keys (z. B. Lowercase); das kanonische Format ist
  snake_case.

## Siehe auch

- [Reference: Envelope-Felder](envelope-felder.md)
- [Reference: Regeln](regeln.md)
- [Guide: Baggage zu Span-Attributen](../guides/otel/baggage-zu-span-attributen.md)
- [Guide: Blob-Metadaten stempeln](../guides/blob/blob-metadaten-stempeln.md)
