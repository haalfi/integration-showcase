"""Shared pytest configuration."""

from __future__ import annotations

from collections.abc import Generator

import pytest
from opentelemetry import propagate, trace
from opentelemetry.baggage.propagation import W3CBaggagePropagator
from opentelemetry.propagators.composite import CompositePropagator
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from integration_showcase.shared.otel import BaggageBusinessAttrSpanProcessor


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "integration: mark test as requiring Docker services (docker compose up -d)",
    )


# Install a real TracerProvider once per test session so the production OTel
# code paths (span creation, propagator, baggage) execute for real; the
# ``spans`` fixture clears the in-memory exporter between tests so each
# assertion sees only its own spans.
_SESSION_EXPORTER = InMemorySpanExporter()


@pytest.fixture(scope="session", autouse=True)
def _session_tracer_provider() -> Generator[InMemorySpanExporter, None, None]:
    provider = TracerProvider()
    provider.add_span_processor(BaggageBusinessAttrSpanProcessor())
    provider.add_span_processor(SimpleSpanProcessor(_SESSION_EXPORTER))
    trace.set_tracer_provider(provider)
    propagate.set_global_textmap(
        CompositePropagator([TraceContextTextMapPropagator(), W3CBaggagePropagator()])
    )
    yield _SESSION_EXPORTER


@pytest.fixture()
def spans() -> Generator[InMemorySpanExporter, None, None]:
    """Per-test access to the in-memory span exporter; clears on entry."""
    _SESSION_EXPORTER.clear()
    yield _SESSION_EXPORTER
    _SESSION_EXPORTER.clear()
