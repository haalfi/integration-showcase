"""Trigger a happy-path order workflow via Service A.

Usage:
    python scenarios/run_happy.py

Requires:
    docker compose up -d
    uvicorn integration_showcase.service_a.app:app --port 8000
"""

from __future__ import annotations

import asyncio

import httpx


async def main() -> None:
    payload = {"items": ["widget-42", "gadget-7"], "customer_id": "cust-001"}
    async with httpx.AsyncClient() as client:
        response = await client.post("http://localhost:8000/order", json=payload, timeout=30)
        response.raise_for_status()
        data = response.json()

    print("Order started")
    print(f"  business_tx_id: {data['business_tx_id']}")
    print(f"  workflow_id:    {data['workflow_id']}")
    print()
    print("Track traces:    http://localhost:16686")
    print("Track workflow:  http://localhost:8088")


if __name__ == "__main__":
    asyncio.run(main())
