"""Run the shipment-failure demo.

Usage: hatch run demo-shipment-failure
Prerequisites: docker compose up -d
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from scenarios._demo import run_demo
from scenarios.run_shipment_failure import main as _run_shipment_failure

if __name__ == "__main__":  # pragma: no cover
    sys.exit(
        asyncio.run(
            run_demo(
                _run_shipment_failure,
                Path("./tmp/demo-shipment-failure-workers.log"),
                service_d_overrides={"FORCE_SHIPMENT_FAILURE": "true"},
            )
        )
    )
