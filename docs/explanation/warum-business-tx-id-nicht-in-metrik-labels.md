# Warum `business_tx_id` nicht in Metrik-Labels

`business_tx_id` ist die fachliche Korrelations-ID und gehört in
**Spans** und **Logs**. In **Metriken** gehört sie nicht als Label.
Diese Seite erklärt, warum.

## Wie Metriken intern funktionieren

Zeitreihen-Backends (Prometheus, Cortex, Mimir, Cloud-Monitoring-
Dienste) speichern Metriken als **unique Kombinationen von
Label-Werten**. Jede eindeutige Kombination ist eine eigene Zeitreihe.
Speicher, Indexgröße, Query-Zeit und Ingestion-Kosten skalieren
(oft nichtlinear) mit der Anzahl aktiver Zeitreihen.

Die Zahl heißt **Cardinality**. Niedrig-kardinale Labels (`service`,
`outcome`, `step_id`) haben endliche, kleine Wertebereiche. Hoch-
kardinale Werte (`user_id`, `request_id`, `business_tx_id`) können
pro Ausführung einen neuen Wert haben.

## Das Problem

Ein Counter `saga_completed_total` mit Labels
`{service, outcome, business_tx_id}` erzeugt **pro Transaktion** eine
neue Zeitreihe:

- 10.000 Transaktionen pro Tag: 10.000 neue Zeitreihen pro Tag.
- 30 Tage Retention: 300.000 lebende Zeitreihen, meistens mit genau
  **einer** Messung pro Reihe.

Effekte:

- Ingestion-Rate steigt sprunghaft, Metrik-Pipeline wird instabil.
- Speicherkosten steigen dramatisch; die meisten Backends haben ein
  Preisschild pro Zeitreihe.
- Queries über hunderttausende Reihen werden langsam oder laufen in
  Limits.
- Alarmierung wird unzuverlässig, weil die meisten Reihen genau eine
  Messung haben (keine Aggregationsbasis).

## Was stattdessen in Metriken gehört

Niedrig-kardinale Dimensionen:

- `service` (endlich viele Services)
- `step_id` (endlich viele Schritte pro Saga-Typ)
- `outcome` (`completed`, `compensated`, `failed`)
- `workflow_type` (nicht `workflow_id`)

Der Counter `saga_completed_total{service, step_id, outcome}`
aggregiert Millionen Transaktionen zu wenigen hundert Zeitreihen.
Dashboards bleiben schnell, Alarme sinnvoll.

## Woher dann die Korrelation?

Der Weg von einer auffälligen Metrik-Messung zur konkreten
Transaktion führt nicht über ein Label, sondern über den **Exemplar-**
Mechanismus von OpenTelemetry bzw. die naheliegende Manual-Query:

- Exemplar: die Metrik führt einen Beispiel-`trace_id` mit; Klick
  darauf öffnet den Trace.
- Manual: „im Zeitfenster, als die Error-Rate spike, welche Traces
  trugen `outcome=failed`?" ist eine Query im Trace-Backend, die
  `business_tx_id` aus Span-Attributen und Baggage liefert.

Die ID bleibt also per Klick erreichbar, ohne den Metrik-Speicher zu
belasten.

## Was zählt als „hochkardinal"?

Faustregel: hat der Wert über die Lebenszeit einer Metrik mehr als
einige tausend distinkte Ausprägungen, ist er hochkardinal.

| Wert                  | Cardinality       |
| --------------------- | ----------------- |
| `service`             | ~10               |
| `step_id`             | ~20 pro Saga-Typ  |
| `outcome`             | 3–5               |
| `user_id`             | Millionen         |
| `workflow_id`         | 1 pro Transaktion |
| `run_id`              | 1+ pro Ausführung |
| `business_tx_id`      | 1 pro Transaktion |

Die unteren drei gehören in Traces und Logs, nicht in Metriken.

## Siehe auch

- [Konzept §6](../konzept.md#6-traceability-regeln) (Regel 5)
- [Reference: Regeln](../reference/regeln.md) (O-7)
- OpenTelemetry: [_Metrics: Data model_](https://opentelemetry.io/docs/specs/otel/metrics/data-model/)
