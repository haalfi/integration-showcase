"""Drive the unhappy-path order saga (payment failure -> compensation) and deep-link.

Usage:
    python -m scenarios.run_unhappy [--items ... --customer-id ...]
    hatch run scenario-unhappy

Prerequisites:
    docker compose up -d
    uvicorn integration_showcase.service_a.app:app --port 8000
    python -m integration_showcase.workflow.worker
    python -m integration_showcase.service_b.worker
    FORCE_PAYMENT_FAILURE=true python -m integration_showcase.service_c.worker
    python -m integration_showcase.service_d.worker

The ``FORCE_PAYMENT_FAILURE`` env var is read by Service C's ``charge_payment``
activity -- it must be set on the worker process, not on Service A.

Exit codes:
    0 -- workflow failed with the expected InsufficientFundsError (demo succeeded).
    1 -- workflow succeeded, or failed for a different reason (demo misconfigured).
"""

from __future__ import annotations

import asyncio
import sys

from scenarios._common import (
    await_workflow,
    build_argparser,
    find_application_error,
    parse_trace_id,
    post_order,
    print_links,
)

_EXPECTED_ERROR_TYPE = "InsufficientFundsError"


async def main() -> int:
    parser = build_argparser(
        description="Run the unhappy-path order scenario (expects payment failure).",
        default_items=["widget-99"],
        default_customer_id="cust-declined",
    )
    args = parser.parse_args()

    print(
        "NOTE: Service C must be running with FORCE_PAYMENT_FAILURE=true "
        "for this scenario to exercise the compensation path."
    )

    response = await post_order(
        args.items,
        args.customer_id,
        base_url=args.service_a_url,
    )
    business_tx_id: str = response["business_tx_id"]
    workflow_id: str = response["workflow_id"]
    trace_id = parse_trace_id(response.get("traceparent", ""))

    print("Order accepted:")
    print(f"  business_tx_id: {business_tx_id}")
    print(f"  workflow_id:    {workflow_id}")
    print("Awaiting workflow completion (expecting failure)...")

    _result, exc, run_id = await await_workflow(workflow_id, address=args.temporal_address)

    print_links(
        business_tx_id=business_tx_id,
        workflow_id=workflow_id,
        run_id=run_id,
        trace_id=trace_id,
        jaeger_url=args.jaeger_url,
        temporal_ui_url=args.temporal_ui_url,
    )

    if exc is None:
        print(
            "UNEXPECTED: workflow succeeded. "
            "Is FORCE_PAYMENT_FAILURE=true set on the Service C worker?",
            file=sys.stderr,
        )
        return 1

    matched = find_application_error(exc, _EXPECTED_ERROR_TYPE)
    if matched is None:
        print(
            f"UNEXPECTED failure: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1

    print(
        f"Expected failure observed: {matched.type}: {matched.message} "
        "(compensation trace should show compensate.reserve-inventory)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
