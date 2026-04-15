"""Trigger an unhappy-path order (payment failure + saga compensation).

Usage:
    python scenarios/run_unhappy.py

Requires:
    docker compose up -d
    FORCE_PAYMENT_FAILURE=true uvicorn integration_showcase.service_a.app:app --port 8000
"""

from __future__ import annotations

import asyncio

import httpx


async def main() -> None:
    payload = {"items": ["widget-99"], "customer_id": "cust-declined"}
    async with httpx.AsyncClient() as client:
        # Expect a 500 after the saga compensates and the workflow fails
        response = await client.post("http://localhost:8000/order", json=payload, timeout=60)
        print(f"HTTP status: {response.status_code}")
        print(f"Body:        {response.text}")

    print()
    print("Inspect compensation spans:  http://localhost:16686")
    print("Inspect failed workflow:     http://localhost:8088")


if __name__ == "__main__":
    asyncio.run(main())
