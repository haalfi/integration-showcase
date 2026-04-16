"""Structured JSON logging with OTel trace/baggage injection.

Wires a logging.Filter that reads the current span and baggage into every
log record so that trace_id, span_id, and business_tx_id appear as top-level
JSON fields — correlatable with Jaeger and queryable in Loki / Elasticsearch.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime

from opentelemetry import baggage, trace

from integration_showcase.shared.constants import BUSINESS_TX_ID_BAGGAGE_KEY

# Uvicorn configures these loggers with propagate=False before the FastAPI
# lifespan runs; setup_logging patches them so they emit JSON too.
_UVICORN_LOGGERS = ("uvicorn", "uvicorn.access", "uvicorn.error")


class OtelContextFilter(logging.Filter):
    """Inject OTel trace context and baggage into every LogRecord.

    Reads the current span via ``trace.get_current_span()`` and the
    ``business_tx_id`` baggage entry, then sets ``trace_id``, ``span_id``,
    and ``business_tx_id`` as extra attributes on the record. Values are
    empty strings when no active span / baggage entry exists so the JSON
    formatter always has the fields present.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        span = trace.get_current_span()
        ctx = span.get_span_context()
        if ctx.is_valid:
            record.trace_id = format(ctx.trace_id, "032x")
            record.span_id = format(ctx.span_id, "016x")
        else:
            record.trace_id = ""
            record.span_id = ""
        record.business_tx_id = baggage.get_baggage(BUSINESS_TX_ID_BAGGAGE_KEY) or ""
        return True


class JsonFormatter(logging.Formatter):
    """Format log records as single-line JSON with trace correlation fields.

    Emits ``trace_id``, ``span_id``, and ``business_tx_id`` as top-level
    fields for Loki / Elasticsearch indexing. Preserves ``exc_info`` and
    ``stack_info`` as ``exception`` and ``stack`` fields so that
    ``logger.exception(...)`` calls retain their traceback in the JSON
    output. Requires :class:`OtelContextFilter` to be installed on the same
    handler so the trace fields are guaranteed to exist on the record.
    """

    def __init__(self, service_name: str) -> None:
        super().__init__()
        self._service = service_name

    def format(self, record: logging.LogRecord) -> str:
        doc: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "service": self._service,
            "logger": record.name,
            "message": record.getMessage(),
            "trace_id": getattr(record, "trace_id", ""),
            "span_id": getattr(record, "span_id", ""),
            "business_tx_id": getattr(record, "business_tx_id", ""),
        }
        if record.exc_info:
            doc["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            doc["stack"] = self.formatStack(record.stack_info)
        return json.dumps(doc)


def setup_logging(service_name: str) -> None:
    """Configure the root logger and uvicorn loggers with JSON + OTel context.

    Replaces any existing handlers on the root logger with a single
    ``StreamHandler`` (stdout) that is guarded by :class:`OtelContextFilter`
    and formatted by :class:`JsonFormatter`. The same handler is attached to
    the three uvicorn loggers (``uvicorn``, ``uvicorn.access``,
    ``uvicorn.error``) with ``propagate=False`` so their output stays JSON
    even though uvicorn configures them before the FastAPI lifespan runs.

    Safe to call multiple times — subsequent calls replace the previous
    configuration.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(OtelContextFilter())
    handler.setFormatter(JsonFormatter(service_name))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    for name in _UVICORN_LOGGERS:
        uv = logging.getLogger(name)
        uv.handlers.clear()
        uv.addHandler(handler)
        uv.propagate = False
