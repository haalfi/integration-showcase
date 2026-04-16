"""OpenTelemetry bootstrap and Envelope carrier helpers.

Centralises tracer setup, the six required business span attributes
(DESIGN.md §OTel span attributes), and W3C propagation between the
in-memory OTel context and the :class:`Envelope` carrier fields.
"""

from __future__ import annotations

import functools
from typing import TYPE_CHECKING, TypeVar

from opentelemetry import baggage, propagate, trace
from opentelemetry.baggage.propagation import W3CBaggagePropagator
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.propagators.composite import CompositePropagator
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from temporalio import activity

from integration_showcase.shared.envelope import Envelope

if TYPE_CHECKING:
    from collections.abc import Callable

    from opentelemetry.context import Context
    from opentelemetry.trace import Span

_R = TypeVar("_R")


def setup_tracing(service_name: str) -> TracerProvider:
    """Install a :class:`TracerProvider` and W3C propagator as process globals.

    Idempotent-ish: subsequent calls install a fresh provider but emit the
    usual OTel "Overriding current TracerProvider" warning.

    ``OTEL_EXPORTER_OTLP_ENDPOINT`` (default ``http://localhost:4317``) and
    ``OTEL_SERVICE_NAME`` are read transparently by the OTLP exporter /
    :class:`Resource`. The *service_name* argument is the fallback when
    ``OTEL_SERVICE_NAME`` is unset.
    """
    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    propagate.set_global_textmap(
        CompositePropagator([TraceContextTextMapPropagator(), W3CBaggagePropagator()])
    )
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


def instrument_activity(
    fn: Callable[[Envelope], _R],
) -> Callable[[Envelope], _R]:
    """Tag the current span with the six required business attributes.

    The ``TracingInterceptor`` creates a ``RunActivity:*`` span as the
    current span around every activity invocation; this decorator reads
    the incoming :class:`Envelope` (first positional arg), backfills
    ``run_id`` from :func:`temporalio.activity.info` when the caller
    didn't supply one, and tags the span.

    When called outside a Temporal activity context (unit tests that
    invoke the activity directly), the ``activity.info()`` lookup is
    skipped and the span is tagged with whatever ``run_id`` the envelope
    already carries.
    """

    @functools.wraps(fn)
    def wrapper(envelope: Envelope) -> _R:
        if not envelope.run_id and activity.in_activity():
            envelope = envelope.model_copy(update={"run_id": activity.info().workflow_run_id})
        set_envelope_span_attrs(trace.get_current_span(), envelope)
        return fn(envelope)

    return wrapper
