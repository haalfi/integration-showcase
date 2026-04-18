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

| Kanal         | Rolle / Mechanismus                                                                                                                          | Besonderheit                                                                                                       |
| ------------- | -------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| Envelope      | Primärquelle. Werte werden aus dem eingehenden Envelope gelesen und unverändert weitergereicht.                                              | Ausnahmen: `step_id` und `idempotency_key` werden bei `advance` neu gesetzt.                                       |
| Baggage       | W3C Baggage transportiert Werte innerhalb des Prozesses über SDK-Grenzen.                                                                    | Span-Processor liest Baggage beim Span-Start; so tragen auch Library-Spans (`blob.put`, `db.write`) die Attribute. |
| Span-Attribut | Verpflichtend auf jedem Span (Regel O-2 in [`regeln.md`](regeln.md)).                                                                        | Attributnamen wörtlich, snake_case, ohne Präfix.                                                                   |
| Log-Feld      | Strukturierte Logs injizieren `business_tx_id`, `trace_id`, `span_id` zusätzlich zu den Standard-OTel-Feldern.                               | Log-Suche nach fachlicher ID funktioniert unabhängig vom Trace Backend.                                            |
| Blob-Metadata | Storage-seitig pro Blob gesetzt: `workflow_id`, `run_id`, `step_id`, `schema_version`, `idempotency_key`.                                    | Read-back über die Properties-API des Backends; Forensik ohne Envelope-Zugriff.                                    |

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
