"""OpenTelemetry bootstrap and Envelope carrier helpers.

Centralises tracer setup, the six required business span attributes
(DESIGN.md §OTel span attributes), and W3C propagation between the
in-memory OTel context and the :class:`Envelope` carrier fields.
"""

from __future__ import annotations

import functools
import inspect
import os
from typing import TYPE_CHECKING, Any, TypeVar

import temporalio.worker
import temporalio.workflow
from opentelemetry import baggage, propagate, trace
from opentelemetry import context as context_api
from opentelemetry.baggage.propagation import W3CBaggagePropagator
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.propagators.composite import CompositePropagator
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from temporalio import activity
from temporalio.contrib.opentelemetry import TracingInterceptor, TracingWorkflowInboundInterceptor

from integration_showcase.shared.envelope import Envelope
from integration_showcase.shared.log_setup import setup_logging

if TYPE_CHECKING:
    from collections.abc import Callable

    from opentelemetry.context import Context
    from opentelemetry.sdk.trace import ReadableSpan
    from opentelemetry.trace import Span

_R = TypeVar("_R")

# Baggage keys that carry the six required business attributes (DESIGN.md §OTel
# span attributes).  ``instrument_activity`` writes all six into baggage before
# the activity body runs so that child spans (``store.*`` blob ops) can pick
# them up via ``BaggageBusinessAttrSpanProcessor``.
_BUSINESS_ATTR_BAGGAGE_KEYS: tuple[str, ...] = (
    "business_tx_id",
    "workflow_id",
    "run_id",
    "step_id",
    "payload_ref_sha256",
    "schema_version",
)


class BaggageBusinessAttrSpanProcessor(SpanProcessor):
    """Stamp the six required business attributes from W3C baggage onto every span.

    Reads each of the six business-attr baggage keys from *parent_context* at
    ``on_start`` time and sets any non-empty value as a span attribute.  Because
    ``on_start`` fires synchronously in the span-creating thread -- before the
    span is handed to ``BatchSpanProcessor``'s background thread -- the baggage
    entries published by :func:`instrument_activity` are visible here when
    ``store.*`` child spans are opened inside an activity invocation.

    Wired in via :func:`setup_tracing`; also added to the test
    :class:`TracerProvider` in ``tests/conftest.py``.
    """

    def on_start(self, span: Span, parent_context: Context | None = None) -> None:
        ctx = parent_context if parent_context is not None else context_api.get_current()
        for key in _BUSINESS_ATTR_BAGGAGE_KEYS:
            value = baggage.get_baggage(key, context=ctx)
            if value is not None:
                span.set_attribute(key, str(value))

    def on_end(self, span: ReadableSpan) -> None:  # noqa: ARG002
        pass


def setup_tracing(service_name: str) -> TracerProvider:
    """Install a :class:`TracerProvider` and W3C propagator as process globals.

    Idempotent-ish: subsequent calls install a fresh provider but emit the
    usual OTel "Overriding current TracerProvider" warning.

    ``OTEL_EXPORTER_OTLP_ENDPOINT`` (default ``http://localhost:4317``) is
    read transparently by the OTLP exporter. For the service name, the
    ``OTEL_SERVICE_NAME`` environment variable wins when set, and the
    *service_name* argument is the fallback when it is unset. The env is
    resolved explicitly here because ``Resource.create`` gives caller-
    supplied attributes priority over environment-detected ones -- passing
    *service_name* unconditionally would silently override the env.
    """
    effective_name = os.environ.get("OTEL_SERVICE_NAME") or service_name
    resource = Resource.create({"service.name": effective_name})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BaggageBusinessAttrSpanProcessor())
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    propagate.set_global_textmap(
        CompositePropagator([TraceContextTextMapPropagator(), W3CBaggagePropagator()])
    )
    setup_logging(effective_name)
    return provider


def set_envelope_span_attrs(span: Span, envelope: Envelope) -> None:
    """Tag *span* with the six required business attributes from *envelope*.

    DESIGN.md §OTel span attributes mandates these on every span. Values
    come directly from the Envelope; the caller is responsible for
    ensuring ``envelope.run_id`` has been backfilled from
    ``workflow.info()`` / ``activity.info()`` where needed.
    """
    span.set_attributes(
        {
            "business_tx_id": envelope.business_tx_id,
            "workflow_id": envelope.workflow_id,
            "run_id": envelope.run_id,
            "step_id": envelope.step_id,
            "payload_ref_sha256": envelope.payload_ref.sha256,
            "schema_version": envelope.schema_version,
        }
    )


def inject_carrier_into_envelope(envelope: Envelope) -> Envelope:
    """Return a copy of *envelope* with W3C carrier fields populated.

    Uses the global propagator to serialise the current OTel context into
    ``traceparent`` / ``tracestate`` / ``baggage``. Baggage is written as a
    ``dict[str, str]`` (unified with OTel baggage per Q3 design decision):
    every OTel baggage key/value in the current context is mirrored onto
    ``envelope.baggage``.

    Note: Temporal's :class:`TracingInterceptor` carries the trace context
    through its own headers on the Temporal side -- the envelope carrier
    fields are intended for *non-Temporal* consumers (audit tooling, blob
    scanners, cross-system correlation from upstream services) that inspect
    the envelope out-of-band. :func:`extract_context_from_envelope` is the
    receiving-side counterpart for those consumers.
    """
    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    current_baggage = {k: str(v) for k, v in baggage.get_all().items()}
    return envelope.model_copy(
        update={
            "traceparent": carrier.get("traceparent", ""),
            "tracestate": carrier.get("tracestate", ""),
            "baggage": current_baggage,
        }
    )


def extract_context_from_envelope(envelope: Envelope) -> Context:
    """Return an OTel :class:`Context` seeded from *envelope* carrier fields.

    Extracts the W3C trace context via the global propagator and seeds
    OTel baggage from ``envelope.baggage`` (unified baggage, Q3).

    Intended for non-Temporal consumers that receive an Envelope
    out-of-band (e.g. a tool reading persisted workflow payloads) and want
    to emit spans under the originating trace. Temporal workers do NOT
    need to call this -- :class:`TracingInterceptor` propagates the
    context through Temporal headers automatically.
    """
    carrier: dict[str, str] = {}
    if envelope.traceparent:
        carrier["traceparent"] = envelope.traceparent
    if envelope.tracestate:
        carrier["tracestate"] = envelope.tracestate
    context = propagate.extract(carrier)
    for key, value in envelope.baggage.items():
        context = baggage.set_baggage(key, value, context=context)
    return context


def _backfill_run_id(envelope: Envelope) -> Envelope:
    if not envelope.run_id and activity.in_activity():
        return envelope.model_copy(update={"run_id": activity.info().workflow_run_id})
    return envelope


def _envelope_baggage_context(envelope: Envelope) -> Context:
    """Return a context with all six business attrs written into W3C baggage.

    Called by :func:`instrument_activity` before the activity body so that
    any child spans (``store.*`` blob ops) created inside the activity see all
    six attrs via :class:`BaggageBusinessAttrSpanProcessor`.
    """
    ctx = context_api.get_current()
    for key, value in (
        ("business_tx_id", envelope.business_tx_id),
        ("workflow_id", envelope.workflow_id),
        ("run_id", envelope.run_id),
        ("step_id", envelope.step_id),
        ("payload_ref_sha256", envelope.payload_ref.sha256),
        ("schema_version", envelope.schema_version),
    ):
        ctx = baggage.set_baggage(key, str(value), context=ctx)
    return ctx


def instrument_activity(
    fn: Callable[..., _R],
) -> Callable[..., _R]:
    """Tag the current span and propagate business attrs to child spans.

    The ``TracingInterceptor`` creates a ``RunActivity:*`` span as the
    current span around every activity invocation; this decorator reads
    the incoming :class:`Envelope` (first positional arg), backfills
    ``run_id`` from :func:`temporalio.activity.info` when the caller
    didn't supply one, and tags the span with the six required business
    attributes. Additionally, all six attrs are written into W3C baggage
    before *fn* is called and removed after, so that child spans (e.g.
    ``store.write`` blob ops) can pick them up via
    :class:`BaggageBusinessAttrSpanProcessor`. Any additional positional or
    keyword arguments are forwarded unchanged to *fn*.

    Works for both ``def`` and ``async def`` activities: the wrapper
    preserves *fn*'s async-ness via :func:`inspect.iscoroutinefunction`,
    so coroutine activities are awaited (not returned unawaited).

    When called outside a Temporal activity context (unit tests that
    invoke the activity directly), the ``activity.info()`` lookup is
    skipped and the span is tagged with whatever ``run_id`` the envelope
    already carries.
    """
    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def async_wrapper(envelope: Envelope, *args: object, **kwargs: object) -> _R:
            envelope = _backfill_run_id(envelope)
            set_envelope_span_attrs(trace.get_current_span(), envelope)
            token = context_api.attach(_envelope_baggage_context(envelope))
            try:
                # fn is iscoroutinefunction here, so fn(...) returns an Awaitable.
                # mypy cannot narrow Callable[..., _R] through iscoroutinefunction.
                return await fn(envelope, *args, **kwargs)  # type: ignore[no-any-return]
            finally:
                context_api.detach(token)

        return async_wrapper  # type: ignore[return-value]

    @functools.wraps(fn)
    def wrapper(envelope: Envelope, *args: object, **kwargs: object) -> _R:
        envelope = _backfill_run_id(envelope)
        set_envelope_span_attrs(trace.get_current_span(), envelope)
        token = context_api.attach(_envelope_baggage_context(envelope))
        try:
            return fn(envelope, *args, **kwargs)
        finally:
            context_api.detach(token)

    return wrapper


def emit_workflow_compensation_span(name: str, attributes: dict[str, str]) -> None:
    """Emit a zero-duration OTel span from within the Temporal workflow body.

    Inside the workflow sandbox ``trace.get_current_span()`` is always
    ``INVALID_SPAN`` because the RunWorkflow span is created and ended by
    ``_completed_span`` *before* the workflow body runs.  This helper reaches
    the active interceptor via the public static
    ``TracingWorkflowInboundInterceptor._from_context()`` and delegates to its
    ``_completed_span`` method -- the only correct OTel emission path from
    workflow code.  Falls back silently when no interceptor is present (unit
    tests that do not wire a TracingInterceptor).
    """
    interceptor = TracingWorkflowInboundInterceptor._from_context()
    if interceptor is not None:
        # _completed_span is a private method; pinning temporalio<2 in
        # pyproject.toml guards against silent breakage on upgrades.
        interceptor._completed_span(name, additional_attributes=attributes)


class _EnvelopeTracingWorkflowInboundInterceptor(TracingWorkflowInboundInterceptor):
    """Injects six envelope business attrs into RunWorkflow spans.

    ``_completed_span`` is a private method of ``TracingWorkflowInboundInterceptor``.
    Pinning ``temporalio<2`` in pyproject.toml guards against silent breakage
    on upgrades -- verify the override still works on each version bump.
    """

    async def execute_workflow(self, input: temporalio.worker.ExecuteWorkflowInput) -> Any:
        env = input.args[0] if input.args else None
        self._run_wf_attrs: dict[str, str] = {}
        if env is not None:
            run_id = getattr(env, "run_id", None) or temporalio.workflow.info().run_id
            ref = getattr(env, "payload_ref", None)
            self._run_wf_attrs = {
                k: v
                for k, v in {
                    "business_tx_id": getattr(env, "business_tx_id", None),
                    "workflow_id": getattr(env, "workflow_id", None),
                    "run_id": run_id,
                    "step_id": "workflow",
                    "payload_ref_sha256": getattr(ref, "sha256", None) if ref else None,
                    "schema_version": getattr(env, "schema_version", None),
                }.items()
                if v
            }
        return await super().execute_workflow(input)

    def _completed_span(
        self, span_name: str, *, additional_attributes: Any = None, **kwargs: Any
    ) -> None:
        attrs = getattr(self, "_run_wf_attrs", {})
        if span_name.startswith("RunWorkflow:") and attrs:
            additional_attributes = {**(additional_attributes or {}), **attrs}
        return super()._completed_span(
            span_name, additional_attributes=additional_attributes, **kwargs
        )


class EnvelopeTracingInterceptor(TracingInterceptor):
    """TracingInterceptor that stamps six envelope business attrs onto RunWorkflow spans.

    Drop-in replacement for ``TracingInterceptor()`` in all workers and clients.
    Accepts the same constructor arguments (e.g. ``always_create_workflow_spans``).

    The underlying mechanism overrides ``_completed_span`` -- a private method of
    ``TracingWorkflowInboundInterceptor``.  Pinning ``temporalio<2`` in
    pyproject.toml guards against silent breakage on SDK upgrades.
    """

    def workflow_interceptor_class(
        self, input: temporalio.worker.WorkflowInterceptorClassInput
    ) -> type[TracingWorkflowInboundInterceptor]:
        super().workflow_interceptor_class(input)  # registers extern function
        return _EnvelopeTracingWorkflowInboundInterceptor
