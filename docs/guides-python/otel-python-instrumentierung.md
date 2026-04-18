# OpenTelemetry in Python

> **Aufgabe.** Wie der Showcase Tracing, Baggage und Log-Korrelation
> in Python verdrahtet.

Volle Implementierung:

- OTel-Setup und Processoren:
  [`shared/otel.py`](../../src/integration_showcase/shared/otel.py)
- Log-Korrelation:
  [`shared/log_setup.py`](../../src/integration_showcase/shared/log_setup.py)

## Kernpunkte

- **TracerProvider-Setup** in `setup_tracing()`: OTLP-Exporter,
  Batch-Processor, W3C-Propagator als Default.
- **`BaggageBusinessAttrSpanProcessor`** liest Baggage beim
  `on_start` und setzt die sechs Business-Attribute als
  Span-Attribute. So tragen auch Library-Spans (`blob.put`,
  `db.query`) die IDs.
- **`EnvelopeTracingInterceptor`** (Temporal-Interceptor): extrahiert
  `traceparent` und Baggage aus dem Envelope, öffnet den Activity-Span
  als Kind.
- **`instrument_activity`-Decorator** publiziert Envelope-Werte
  **vor** dem Activity-Body in den OTel-Kontext (Baggage + aktive
  Attribute), damit der Span-Processor sie beim Start sieht.
- **Strukturierte JSON-Logs** via Custom-Formatter in
  `log_setup.py`: injiziert `trace_id`, `span_id`, `business_tx_id`,
  `workflow_id`, `run_id`, `step_id` in jede Log-Zeile.

## Fallstricke in Python

- **`trace.get_current_span()` im Workflow-Body** liefert
  `INVALID_SPAN`. Für den Workflow-Span den vom Temporal-OTel-Contrib
  gelieferten Handle verwenden; im Showcase: über `_from_context()`.
- **Baggage-Werte `""`**: der Processor filtert leere Strings, damit
  das Ingress-Blob-`run_id=""` nicht als Span-Attribut landet.
- **Pydantic-Typen im Log-Record**: `extra={"envelope": env}` erzeugt
  beim JSON-Formatter Default-Fehler. Nur primitive Werte oder
  explizites `env.model_dump()` übergeben.

## Siehe auch

- [Reference: Korrelationsattribute](../reference/korrelationsattribute.md)
- [Guide: Trace Context im Envelope](../guides/otel/trace-kontext-im-envelope.md)
- [Guide: Baggage zu Span-Attributen](../guides/otel/baggage-zu-span-attributen.md)
- [Guide: Workflow-Span-Attribute](../guides/otel/workflow-span-attribute.md)
- [Guide: Logs mit Traces korrelieren](../guides/otel/logs-mit-traces-korrelieren.md)
