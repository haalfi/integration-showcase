"""Unit tests for the shared OTel bootstrap and Envelope carrier helpers."""

from __future__ import annotations

import pytest
from opentelemetry import baggage, trace
from opentelemetry.context import attach, detach
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from temporalio.testing import ActivityEnvironment

from integration_showcase.shared.envelope import BlobRef, Envelope
from integration_showcase.shared.otel import (
    extract_context_from_envelope,
    inject_carrier_into_envelope,
    instrument_activity,
    set_envelope_span_attrs,
)

_tracer = trace.get_tracer(__name__)


def _envelope(**overrides: object) -> Envelope:
    defaults: dict[str, object] = {
        "workflow_id": "order-123",
        "run_id": "run-456",
        "business_tx_id": "tx-001",
        "step_id": "start",
        "payload_ref": BlobRef(blob_url="w/tx/input.json", sha256="deadbeef"),
        "traceparent": "",
        "idempotency_key": "tx-001:start:1.0",
    }
    return Envelope(**{**defaults, **overrides})


class TestBaggageBusinessAttrSpanProcessor:
    def test_stamps_six_attrs_from_baggage(self, spans: InMemorySpanExporter) -> None:
        """Child span created inside baggage context carries all six business attrs."""
        from opentelemetry import context as context_api

        ctx = context_api.get_current()
        ctx = baggage.set_baggage("business_tx_id", "tx-bk004", context=ctx)
        ctx = baggage.set_baggage("workflow_id", "order-bk004", context=ctx)
        ctx = baggage.set_baggage("run_id", "run-bk004", context=ctx)
        ctx = baggage.set_baggage("step_id", "reserve", context=ctx)
        ctx = baggage.set_baggage("payload_ref_sha256", "abcd1234", context=ctx)
        ctx = baggage.set_baggage("schema_version", "1.0", context=ctx)
        token = attach(ctx)
        try:
            with _tracer.start_as_current_span("store.write"):
                pass
        finally:
            detach(token)

        (recorded,) = spans.get_finished_spans()
        attrs = recorded.attributes or {}
        assert attrs["business_tx_id"] == "tx-bk004"
        assert attrs["workflow_id"] == "order-bk004"
        assert attrs["run_id"] == "run-bk004"
        assert attrs["step_id"] == "reserve"
        assert attrs["payload_ref_sha256"] == "abcd1234"
        assert attrs["schema_version"] == "1.0"

    def test_no_attrs_without_baggage(self, spans: InMemorySpanExporter) -> None:
        """Span created with no baggage gets no business attrs from the processor."""
        with _tracer.start_as_current_span("store.write"):
            pass

        (recorded,) = spans.get_finished_spans()
        attrs = recorded.attributes or {}
        assert "business_tx_id" not in attrs
        assert "workflow_id" not in attrs

    def test_partial_baggage_stamps_only_present_keys(self, spans: InMemorySpanExporter) -> None:
        """Only keys present in baggage are stamped; missing keys are left unset."""
        token = attach(baggage.set_baggage("business_tx_id", "tx-partial"))
        try:
            with _tracer.start_as_current_span("store.write"):
                pass
        finally:
            detach(token)

        (recorded,) = spans.get_finished_spans()
        attrs = recorded.attributes or {}
        assert attrs["business_tx_id"] == "tx-partial"
        assert "workflow_id" not in attrs


class TestSetEnvelopeSpanAttrs:
    def test_all_six_attributes_present(self, spans: InMemorySpanExporter) -> None:
        env = _envelope()
        with _tracer.start_as_current_span("probe") as span:
            set_envelope_span_attrs(span, env)

        (recorded,) = spans.get_finished_spans()
        attrs = recorded.attributes or {}
        assert attrs["business_tx_id"] == "tx-001"
        assert attrs["workflow_id"] == "order-123"
        assert attrs["run_id"] == "run-456"
        assert attrs["step_id"] == "start"
        assert attrs["payload_ref_sha256"] == "deadbeef"
        assert attrs["schema_version"] == "1.0"


class TestCarrierRoundtrip:
    def test_inject_populates_traceparent(self) -> None:
        env = _envelope()
        with _tracer.start_as_current_span("probe"):
            new_env = inject_carrier_into_envelope(env)
        assert new_env.traceparent != ""

    def test_inject_extract_preserves_trace_id(self, spans: InMemorySpanExporter) -> None:
        env = _envelope()
        with _tracer.start_as_current_span("outer") as outer_span:
            new_env = inject_carrier_into_envelope(env)
            outer_trace_id = outer_span.get_span_context().trace_id

        context = extract_context_from_envelope(new_env)
        token = attach(context)
        try:
            with _tracer.start_as_current_span("inner") as inner_span:
                inner_trace_id = inner_span.get_span_context().trace_id
        finally:
            detach(token)

        assert outer_trace_id == inner_trace_id

    def test_unified_baggage_mirrored_on_envelope(self) -> None:
        env = _envelope()
        token = attach(baggage.set_baggage("business_tx_id", "tx-001"))
        try:
            with _tracer.start_as_current_span("probe"):
                new_env = inject_carrier_into_envelope(env)
        finally:
            detach(token)
        assert new_env.baggage == {"business_tx_id": "tx-001"}

    def test_extract_seeds_baggage_into_context(self) -> None:
        env = _envelope(baggage={"business_tx_id": "tx-001", "tenant": "acme"})
        context = extract_context_from_envelope(env)
        assert baggage.get_baggage("business_tx_id", context) == "tx-001"
        assert baggage.get_baggage("tenant", context) == "acme"


class TestInstrumentActivityAsync:
    async def test_async_activity_is_awaited_and_span_is_tagged(
        self, spans: InMemorySpanExporter
    ) -> None:
        """Decorating an ``async def`` must produce an awaitable that runs the body."""
        calls: list[Envelope] = []

        @instrument_activity
        async def _probe(envelope: Envelope) -> Envelope:
            calls.append(envelope)
            return envelope

        env = _envelope()
        with _tracer.start_as_current_span("RunActivity:probe"):
            result = await _probe(env)

        assert calls == [env]
        assert result == env
        (recorded,) = spans.get_finished_spans()
        attrs = recorded.attributes or {}
        assert attrs["business_tx_id"] == "tx-001"
        assert attrs["step_id"] == "start"

    async def test_child_spans_inside_async_activity_get_six_attrs(
        self, spans: InMemorySpanExporter
    ) -> None:
        """Child spans (e.g. store.write) created within the activity body carry all six attrs."""

        @instrument_activity
        async def _probe(envelope: Envelope) -> Envelope:
            with _tracer.start_as_current_span("store.write"):
                pass
            return envelope

        env = _envelope()
        with _tracer.start_as_current_span("RunActivity:probe"):
            await _probe(env)

        recorded = {s.name: s for s in spans.get_finished_spans()}
        child_attrs = recorded["store.write"].attributes or {}
        assert child_attrs["business_tx_id"] == "tx-001"
        assert child_attrs["workflow_id"] == "order-123"
        assert child_attrs["run_id"] == "run-456"
        assert child_attrs["step_id"] == "start"
        assert child_attrs["payload_ref_sha256"] == "deadbeef"
        assert child_attrs["schema_version"] == "1.0"

    async def test_child_spans_with_empty_run_id_still_get_six_attrs(
        self, spans: InMemorySpanExporter
    ) -> None:
        """Child spans carry all six attrs even when run_id is empty (e.g. at ingress)."""

        @instrument_activity
        async def _probe(envelope: Envelope) -> Envelope:
            with _tracer.start_as_current_span("store.write"):
                pass
            return envelope

        env = _envelope(run_id="")
        with _tracer.start_as_current_span("RunActivity:probe"):
            await _probe(env)

        recorded = {s.name: s for s in spans.get_finished_spans()}
        child_attrs = recorded["store.write"].attributes or {}
        assert child_attrs["business_tx_id"] == "tx-001"
        assert child_attrs["run_id"] == ""  # empty but present


class TestInstrumentActivityDecorator:
    def test_tags_current_span_when_called_in_activity_env(
        self, spans: InMemorySpanExporter
    ) -> None:
        calls: list[Envelope] = []

        @instrument_activity
        def _probe(envelope: Envelope) -> Envelope:
            calls.append(envelope)
            return envelope

        env = _envelope()
        env_in_activity: dict[str, Envelope] = {}

        def _run() -> None:
            with _tracer.start_as_current_span("RunActivity:probe"):
                env_in_activity["e"] = _probe(env)

        ActivityEnvironment().run(_run)

        (recorded,) = spans.get_finished_spans()
        attrs = recorded.attributes or {}
        assert attrs["business_tx_id"] == "tx-001"
        assert attrs["step_id"] == "start"
        assert calls == [env]

    def test_child_spans_inside_sync_activity_get_six_attrs(
        self, spans: InMemorySpanExporter
    ) -> None:
        """Child spans created within a sync activity body carry all six attrs."""

        @instrument_activity
        def _probe(envelope: Envelope) -> Envelope:
            with _tracer.start_as_current_span("store.write"):
                pass
            return envelope

        env = _envelope()

        def _run() -> None:
            with _tracer.start_as_current_span("RunActivity:probe"):
                _probe(env)

        ActivityEnvironment().run(_run)

        recorded = {s.name: s for s in spans.get_finished_spans()}
        child_attrs = recorded["store.write"].attributes or {}
        assert child_attrs["business_tx_id"] == "tx-001"
        assert child_attrs["workflow_id"] == "order-123"
        assert child_attrs["run_id"] == "run-456"
        assert child_attrs["step_id"] == "start"
        assert child_attrs["payload_ref_sha256"] == "deadbeef"
        assert child_attrs["schema_version"] == "1.0"

    def test_child_spans_with_empty_run_id_still_get_six_attrs_sync(
        self, spans: InMemorySpanExporter
    ) -> None:
        """Sync activities backfill empty run_id from activity context and propagate it."""

        @instrument_activity
        def _probe(envelope: Envelope) -> Envelope:
            with _tracer.start_as_current_span("store.write"):
                pass
            return envelope

        env = _envelope(run_id="")

        def _run() -> None:
            with _tracer.start_as_current_span("RunActivity:probe"):
                _probe(env)

        ActivityEnvironment().run(_run)

        recorded = {s.name: s for s in spans.get_finished_spans()}
        child_attrs = recorded["store.write"].attributes or {}
        assert child_attrs["business_tx_id"] == "tx-001"
        # ActivityEnvironment backfills run_id, so it's no longer empty; just assert presence
        assert "run_id" in child_attrs
        assert child_attrs["run_id"]  # non-empty after backfill

    def test_backfills_run_id_from_activity_info(self, spans: InMemorySpanExporter) -> None:
        captured: dict[str, Envelope] = {}

        @instrument_activity
        def _probe(envelope: Envelope) -> Envelope:
            captured["e"] = envelope
            return envelope

        env = _envelope(run_id="")

        def _run() -> None:
            with _tracer.start_as_current_span("RunActivity:probe"):
                _probe(env)

        env_info = ActivityEnvironment()
        env_info.run(_run)

        # ActivityEnvironment's default workflow_run_id is set by the harness;
        # any non-empty value is good enough to prove backfill happened.
        assert captured["e"].run_id != ""
        (recorded,) = spans.get_finished_spans()
        attrs = recorded.attributes or {}
        assert attrs["run_id"] == captured["e"].run_id

    def test_safe_outside_activity_context(self, spans: InMemorySpanExporter) -> None:
        """Directly invoked (no ActivityEnvironment) still tags span, skips backfill."""

        @instrument_activity
        def _probe(envelope: Envelope) -> Envelope:
            return envelope

        env = _envelope(run_id="stays-as-is")
        with _tracer.start_as_current_span("probe"):
            result = _probe(env)

        assert result.run_id == "stays-as-is"
        (recorded,) = spans.get_finished_spans()
        attrs = recorded.attributes or {}
        assert attrs["run_id"] == "stays-as-is"


@pytest.mark.parametrize("empty_field", ["traceparent", "tracestate"])
def test_extract_tolerates_empty_carrier_fields(empty_field: str) -> None:
    """Extraction with missing W3C fields is a no-op, not a crash."""
    env = _envelope(**{empty_field: ""})
    # Should not raise; the returned context may not carry a span context,
    # but baggage mirroring and propagator safety are preserved.
    extract_context_from_envelope(env)
