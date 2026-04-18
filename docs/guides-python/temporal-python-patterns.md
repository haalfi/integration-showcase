# Temporal-Muster in Python

> **Aufgabe.** Die Temporal-SDK-spezifischen Muster des Showcase auf
> einen Blick.

Volle Implementierungen:

- Workflow-Body: [`workflow/order.py`](../../src/integration_showcase/workflow/order.py)
- Worker pro Service: [`service_[abcd]/worker.py`](../../src/integration_showcase/)
- Ingress (FastAPI + StartWorkflow): [`service_a/app.py`](../../src/integration_showcase/service_a/app.py)
- Activity-Implementierungen: [`service_[bcd]/activities.py`](../../src/integration_showcase/)

## Kernpunkte

- **Data Converter:** JSON-kodierter Envelope als einziges Activity-
  Argument. Pydantic-Modell wird nativ serialisiert.
- **Task Queue pro Service:** Konstanten in `shared/constants.py`,
  Worker registrieren genau ihre Activities auf ihrer Queue.
- **Retry Policy:** in `workflow/order.py` pro `execute_activity`
  gesetzt, inkl. `non_retryable_error_types`.
- **Kompensation:** isolierte `try/except`-Blöcke pro
  Compensate-Activity (siehe BK-006, BK-007 im BACKLOG-DONE).
- **OTel-Integration:** `temporalio.contrib.opentelemetry`
  als Interceptor auf Client und Worker; eigener
  `EnvelopeTracingInterceptor` in `shared/otel.py` stempelt
  Envelope-Werte als Span-Attribute.
- **Testing:** `WorkflowEnvironment.start_time_skipping` mit
  eigenem Data Converter; siehe `tests/integration/conftest.py`.

## Häufige Fallstricke

- **`trace.get_current_span()` im Workflow-Body** liefert einen
  `INVALID_SPAN`. Der reale Workflow-Span ist über die OTel-Integration
  des SDKs verfügbar, nicht über die normale Tracer-API.
- **`workflow.now()` statt `datetime.now()`.** Außerhalb ist das Workflow
  nicht deterministisch.
- **Globale Zustände** im Workflow-Modul: bricht die Determinism-Zusage.

## Siehe auch

- [Guide: Workflow starten](../guides/temporal/workflow-mit-envelope-starten.md)
- [Guide: Activity implementieren](../guides/temporal/aktivitaet-implementieren.md)
- [Guide: Kompensation verdrahten](../guides/temporal/kompensation-verdrahten.md)
- [Guide: Task Queue pro Service](../guides/temporal/task-queue-pro-service.md)
