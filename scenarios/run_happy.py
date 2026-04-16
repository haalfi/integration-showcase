"""Drive the happy-path order saga end-to-end and deep-link into the UIs.

Usage:
    python -m scenarios.run_happy [--items ... --customer-id ...]
    hatch run scenario-happy

Prerequisites:
    docker compose up -d
    uvicorn integration_showcase.service_a.app:app --port 8000
    python -m integration_showcase.workflow.worker
    python -m integration_showcase.service_b.worker
    python -m integration_showcase.service_c.worker
    python -m integration_showcase.service_d.worker
"""

from __future__ import annotations

import asyncio
import sys

from scenarios._common import (
    await_workflow,
    build_argparser,
    parse_trace_id,
    post_order,
    print_links,
)


async def main() -> int:
    args = build_argparser(description="Run the happy-path order scenario.").parse_args()

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
    print("Awaiting workflow completion...")

    result, exc, run_id = await await_workflow(workflow_id, address=args.temporal_address)

    print_links(
        business_tx_id=business_tx_id,
        workflow_id=workflow_id,
        run_id=run_id,
        trace_id=trace_id,
        jaeger_url=args.jaeger_url,
        temporal_ui_url=args.temporal_ui_url,
    )

    if exc is not None:
        print(f"UNEXPECTED failure: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    if result != business_tx_id:
        print(
            f"UNEXPECTED result: got {result!r}, expected business_tx_id {business_tx_id!r}",
            file=sys.stderr,
        )
        return 1
    print(f"Workflow completed successfully (returned {result!r}).")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
