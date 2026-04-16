"""OpenTelemetry bootstrap and Envelope carrier helpers.

Centralises tracer setup, the six required business span attributes
(DESIGN.md §OTel span attributes), and W3C propagation between the
in-memory OTel context and the :class:`Envelope` carrier fields.
"""

from __future__ import annotations

import functools
import inspect
import os
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
from integration_showcase.shared.log_setup import setup_logging

if TYPE_CHECKING:
    from collections.abc import Callable

    from opentelemetry.context import Context
    from opentelemetry.trace import Span

_R = TypeVar("_R")


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


def instrument_activity(
    fn: Callable[..., _R],
) -> Callable[..., _R]:
    """Tag the current span with the six required business attributes.

    The ``TracingInterceptor`` creates a ``RunActivity:*`` span as the
    current span around every activity invocation; this decorator reads
    the incoming :class:`Envelope` (first positional arg), backfills
    ``run_id`` from :func:`temporalio.activity.info` when the caller
    didn't supply one, and tags the span. Any additional positional or
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
            # fn is iscoroutinefunction here, so fn(...) returns an Awaitable.
            # mypy cannot narrow Callable[..., _R] through iscoroutinefunction.
            return await fn(envelope, *args, **kwargs)  # type: ignore[no-any-return]

        return async_wrapper  # type: ignore[return-value]

    @functools.wraps(fn)
    def wrapper(envelope: Envelope, *args: object, **kwargs: object) -> _R:
        envelope = _backfill_run_id(envelope)
        set_envelope_span_attrs(trace.get_current_span(), envelope)
        return fn(envelope, *args, **kwargs)

    return wrapper
