"""FastAPI application for Service A (HTTP ingress)."""

from __future__ import annotations

import uuid

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="integration-showcase -- Service A (Ingress)")


class OrderRequest(BaseModel):
    items: list[str]
    customer_id: str


class OrderResponse(BaseModel):
    business_tx_id: str
    workflow_id: str


@app.post("/order", response_model=OrderResponse)
async def create_order(request: OrderRequest) -> OrderResponse:
    """Ingest an order: write payload to Blob Storage, start Temporal workflow.

    IS-003: implement blob upload, envelope construction, workflow start.
    """
    business_tx_id = str(uuid.uuid4())
    # TODO IS-003
    raise NotImplementedError("IS-003")
