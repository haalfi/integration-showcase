"""Shared helpers for scenario drivers: HTTP ingress, Temporal await, UI deep-links."""

from __future__ import annotations

import argparse
from typing import Any
from urllib.parse import quote

import httpx
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ApplicationError

from integration_showcase.shared.otel import EnvelopeTracingInterceptor

_DEFAULT_ITEMS = ["widget-42", "gadget-7"]
_DEFAULT_CUSTOMER_ID = "cust-001"
_DEFAULT_SERVICE_A_URL = "http://localhost:8000"
_DEFAULT_TEMPORAL_ADDRESS = "localhost:7233"
_DEFAULT_JAEGER_URL = "http://localhost:16686"
_DEFAULT_TEMPORAL_UI_URL = "http://localhost:8088"
_TEMPORAL_NAMESPACE = "default"


def parse_trace_id(traceparent: str) -> str | None:
    """Extract the 32-hex trace_id from a W3C traceparent header.

    Format: ``{version}-{trace_id_32_hex}-{span_id_16_hex}-{flags}``.
    Returns None for any malformed input -- callers fall back to the search URL.
    """
    if not traceparent:
        return None
    parts = traceparent.split("-")
    if len(parts) != 4:
        return None
    trace_id = parts[1]
    if len(trace_id) != 32 or not all(c in "0123456789abcdef" for c in trace_id):
        return None
    return trace_id


def jaeger_trace_url(trace_id: str, *, base_url: str = _DEFAULT_JAEGER_URL) -> str:
    """Deep-link to a specific trace in Jaeger."""
    return f"{base_url.rstrip('/')}/trace/{trace_id}"


def jaeger_search_url(business_tx_id: str, *, base_url: str = _DEFAULT_JAEGER_URL) -> str:
    """Fallback: Jaeger search filtered by business_tx_id tag (used when trace_id is missing)."""
    tags = quote(f'{{"business_tx_id":"{business_tx_id}"}}', safe="")
    return f"{base_url.rstrip('/')}/search?service=service-a&tags={tags}"


def temporal_workflow_url(
    workflow_id: str,
    run_id: str | None = None,
    *,
    base_url: str = _DEFAULT_TEMPORAL_UI_URL,
) -> str:
    """Deep-link to a workflow (or a specific run, if provided) in the Temporal UI."""
    wf = quote(workflow_id, safe="")
    root = f"{base_url.rstrip('/')}/namespaces/{_TEMPORAL_NAMESPACE}/workflows/{wf}"
    if run_id:
        return f"{root}/{quote(run_id, safe='')}/history"
    return root


async def post_order(
    items: list[str],
    customer_id: str,
    *,
    base_url: str = _DEFAULT_SERVICE_A_URL,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """POST /order to Service A and return the parsed JSON response."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{base_url.rstrip('/')}/order",
            json={"items": items, "customer_id": customer_id},
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json()


async def await_workflow(
    workflow_id: str,
    *,
    address: str = _DEFAULT_TEMPORAL_ADDRESS,
) -> tuple[Any | None, BaseException | None, str | None]:
    """Connect to Temporal, fetch the workflow run_id, and await ``result()``.

    Returns ``(result, exc, run_id)``:
    - On success: ``(result, None, run_id)``.
    - On failure: ``(None, exc, run_id)`` -- run_id is still returned so callers
      can deep-link to the failed run in the Temporal UI.
    """
    # TracingInterceptor matches Service A / worker client config so any
    # OTel context on the caller is propagated across the Temporal boundary.
    # The SDK has no explicit ``close()``; the underlying gRPC channel is
    # released by GC. Acceptable for a short-lived script; callers invoking
    # this helper in a hot loop should hold a single Client across iterations.
    client = await Client.connect(
        address,
        data_converter=pydantic_data_converter,
        interceptors=[EnvelopeTracingInterceptor()],
    )
    handle = client.get_workflow_handle(workflow_id)

    result: Any = None
    exc: BaseException | None = None
    try:
        result = await handle.result()
    except BaseException as caught:  # noqa: BLE001 -- scripts report any failure mode
        exc = caught

    # describe() runs AFTER result() so ``run_id`` captures the terminal run.
    # ``handle.result()`` follows continuations by default (``follow_runs=True``);
    # for a workflow using ``continue_as_new``, calling describe() before would
    # pin an earlier run_id and the Temporal UI link would point at the wrong
    # execution. OrderWorkflow does not continue_as_new today, but the order
    # guards the helper against future workflow changes.
    run_id: str | None = None
    try:
        desc = await handle.describe()
        run_id = desc.run_id
    except Exception:
        # Describe is best-effort -- the run_id is only used for a UI link.
        pass

    return result, exc, run_id


def find_application_error(exc: BaseException, error_type: str) -> ApplicationError | None:
    """Walk the ``.cause`` chain looking for an ApplicationError of ``error_type``.

    Temporal serializes application exceptions through the boundary; the original
    class is not preserved, so we match on ``ApplicationError.type`` (the name).
    """
    current: BaseException | None = exc
    while current is not None:
        if isinstance(current, ApplicationError) and current.type == error_type:
            return current
        current = getattr(current, "cause", None)
    return None


def print_links(
    *,
    business_tx_id: str,
    workflow_id: str,
    run_id: str | None,
    trace_id: str | None,
    jaeger_url: str = _DEFAULT_JAEGER_URL,
    temporal_ui_url: str = _DEFAULT_TEMPORAL_UI_URL,
) -> None:
    """Print the standard UI link block (Jaeger + Temporal UI) to stdout."""
    if trace_id:
        jaeger = jaeger_trace_url(trace_id, base_url=jaeger_url)
    else:
        jaeger = jaeger_search_url(business_tx_id, base_url=jaeger_url)
    temporal = temporal_workflow_url(workflow_id, run_id, base_url=temporal_ui_url)

    print("UIs:")
    print(f"  Jaeger trace:   {jaeger}")
    print(f"  Temporal run:   {temporal}")


def build_argparser(
    *,
    description: str,
    default_items: list[str] | None = None,
    default_customer_id: str = _DEFAULT_CUSTOMER_ID,
) -> argparse.ArgumentParser:
    """Build the argparse parser shared by both scenario scripts."""
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--items",
        nargs="+",
        default=default_items if default_items is not None else _DEFAULT_ITEMS,
        help="Item IDs to order (space-separated).",
    )
    parser.add_argument(
        "--customer-id",
        default=default_customer_id,
        help="Customer identifier attached to the order.",
    )
    parser.add_argument(
        "--service-a-url",
        default=_DEFAULT_SERVICE_A_URL,
        help="Base URL for Service A's HTTP ingress.",
    )
    parser.add_argument(
        "--temporal-address",
        default=_DEFAULT_TEMPORAL_ADDRESS,
        help="Temporal frontend gRPC address (host:port).",
    )
    parser.add_argument(
        "--jaeger-url",
        default=_DEFAULT_JAEGER_URL,
        help="Jaeger UI base URL.",
    )
    parser.add_argument(
        "--temporal-ui-url",
        default=_DEFAULT_TEMPORAL_UI_URL,
        help="Temporal UI base URL.",
    )
    return parser
