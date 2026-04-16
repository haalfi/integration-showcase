"""Unit tests for OtelContextFilter, JsonFormatter, and setup_logging.

Validates that trace_id / span_id / business_tx_id are injected into log
records and emitted as top-level JSON fields, and that trace_id matches
the W3C / Jaeger 32-char lowercase hex format.
"""

from __future__ import annotations

import json
import logging as stdlib_logging

import pytest
from opentelemetry import baggage, trace
from opentelemetry.context import attach, detach

from integration_showcase.shared.log_setup import (
    JsonFormatter,
    OtelContextFilter,
    setup_logging,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_record(msg: str = "test message") -> stdlib_logging.LogRecord:
    return stdlib_logging.LogRecord(
        name="test.logger",
        level=stdlib_logging.INFO,
        pathname="",
        lineno=0,
        msg=msg,
        args=(),
        exc_info=None,
    )


def _apply_filter(record: stdlib_logging.LogRecord) -> stdlib_logging.LogRecord:
    """Run OtelContextFilter on *record* in place and return it."""
    OtelContextFilter().filter(record)
    return record


# ---------------------------------------------------------------------------
# OtelContextFilter
# ---------------------------------------------------------------------------


class TestOtelContextFilter:
    def test_outside_span_trace_id_empty(self) -> None:
        record = _apply_filter(_make_record())
        assert record.trace_id == ""  # type: ignore[attr-defined]

    def test_outside_span_span_id_empty(self) -> None:
        record = _apply_filter(_make_record())
        assert record.span_id == ""  # type: ignore[attr-defined]

    def test_outside_span_business_tx_id_empty(self) -> None:
        record = _apply_filter(_make_record())
        assert record.business_tx_id == ""  # type: ignore[attr-defined]

    def test_inside_span_trace_id_matches(self, spans: object) -> None:
        tracer = trace.get_tracer("test")
        with tracer.start_as_current_span("s") as span:
            expected = format(span.get_span_context().trace_id, "032x")
            record = _apply_filter(_make_record())
        assert record.trace_id == expected  # type: ignore[attr-defined]

    def test_inside_span_trace_id_is_32_hex_chars(self, spans: object) -> None:
        tracer = trace.get_tracer("test")
        with tracer.start_as_current_span("s"):
            record = _apply_filter(_make_record())
        assert len(record.trace_id) == 32  # type: ignore[attr-defined]
        assert all(c in "0123456789abcdef" for c in record.trace_id)  # type: ignore[attr-defined]

    def test_inside_span_span_id_matches(self, spans: object) -> None:
        tracer = trace.get_tracer("test")
        with tracer.start_as_current_span("s") as span:
            expected = format(span.get_span_context().span_id, "016x")
            record = _apply_filter(_make_record())
        assert record.span_id == expected  # type: ignore[attr-defined]

    def test_inside_span_span_id_is_16_hex_chars(self, spans: object) -> None:
        tracer = trace.get_tracer("test")
        with tracer.start_as_current_span("s"):
            record = _apply_filter(_make_record())
        assert len(record.span_id) == 16  # type: ignore[attr-defined]

    def test_business_tx_id_from_baggage(self) -> None:
        token = attach(baggage.set_baggage("business_tx_id", "tx-abc-123"))
        try:
            record = _apply_filter(_make_record())
        finally:
            detach(token)
        assert record.business_tx_id == "tx-abc-123"  # type: ignore[attr-defined]

    def test_no_baggage_business_tx_id_empty(self) -> None:
        record = _apply_filter(_make_record())
        assert record.business_tx_id == ""  # type: ignore[attr-defined]

    def test_filter_always_returns_true(self) -> None:
        f = OtelContextFilter()
        assert f.filter(_make_record()) is True

    def test_trace_id_resets_after_span_exits(self, spans: object) -> None:
        tracer = trace.get_tracer("test")
        with tracer.start_as_current_span("s"):
            pass  # span has ended
        record = _apply_filter(_make_record())
        assert record.trace_id == ""  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# JsonFormatter
# ---------------------------------------------------------------------------


def _fmt(msg: str = "hello", service: str = "svc") -> dict[str, object]:
    record = _apply_filter(_make_record(msg))
    return json.loads(JsonFormatter(service).format(record))


class TestJsonFormatter:
    @pytest.mark.parametrize(
        "field",
        [
            "timestamp",
            "level",
            "service",
            "logger",
            "message",
            "trace_id",
            "span_id",
            "business_tx_id",
        ],
    )
    def test_required_top_level_field_present(self, field: str) -> None:
        assert field in _fmt()

    def test_message_field(self) -> None:
        assert _fmt("my message")["message"] == "my message"

    def test_service_field(self) -> None:
        assert _fmt(service="my-svc")["service"] == "my-svc"

    def test_level_field(self) -> None:
        assert _fmt()["level"] == "INFO"

    def test_logger_field(self) -> None:
        assert _fmt()["logger"] == "test.logger"

    def test_trace_id_empty_outside_span(self) -> None:
        assert _fmt()["trace_id"] == ""

    def test_span_id_empty_outside_span(self) -> None:
        assert _fmt()["span_id"] == ""

    def test_trace_id_matches_span(self, spans: object) -> None:
        tracer = trace.get_tracer("test")
        with tracer.start_as_current_span("s") as span:
            expected = format(span.get_span_context().trace_id, "032x")
            doc = json.loads(JsonFormatter("svc").format(_apply_filter(_make_record())))
        assert doc["trace_id"] == expected
        assert len(str(doc["trace_id"])) == 32

    def test_business_tx_id_in_json(self) -> None:
        token = attach(baggage.set_baggage("business_tx_id", "tx-999"))
        try:
            doc = _fmt()
        finally:
            detach(token)
        assert doc["business_tx_id"] == "tx-999"

    def test_output_is_valid_json(self) -> None:
        # _fmt already parses JSON; if it didn't raise, output is valid
        assert isinstance(_fmt(), dict)

    def test_timestamp_is_iso8601(self) -> None:
        from datetime import datetime

        ts = str(_fmt()["timestamp"])
        # Should parse without error
        datetime.fromisoformat(ts)


# ---------------------------------------------------------------------------
# setup_logging
# ---------------------------------------------------------------------------


class TestSetupLogging:
    def test_root_logger_has_otel_filter(self) -> None:
        setup_logging("test-svc")
        root = stdlib_logging.getLogger()
        filters = [f for h in root.handlers for f in h.filters]
        assert any(isinstance(f, OtelContextFilter) for f in filters)

    def test_root_logger_has_json_formatter(self) -> None:
        setup_logging("test-svc")
        root = stdlib_logging.getLogger()
        formatters = [h.formatter for h in root.handlers]
        assert any(isinstance(f, JsonFormatter) for f in formatters)

    def test_root_logger_level_is_info(self) -> None:
        setup_logging("test-svc")
        assert stdlib_logging.getLogger().level == stdlib_logging.INFO

    def test_subsequent_call_does_not_stack_handlers(self) -> None:
        setup_logging("svc-a")
        setup_logging("svc-b")
        assert len(stdlib_logging.getLogger().handlers) == 1
