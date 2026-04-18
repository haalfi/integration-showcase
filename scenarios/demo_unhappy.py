"""Run the unhappy-path demo. Usage: hatch run demo-unhappy. Prerequisites: docker compose up -d"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from scenarios._demo import run_demo
from scenarios.run_unhappy import main as _run_unhappy

if __name__ == "__main__":  # pragma: no cover
    sys.exit(
        asyncio.run(
            run_demo(
                _run_unhappy,
                Path("./tmp/demo-unhappy-workers.log"),
                service_c_overrides={"FORCE_PAYMENT_FAILURE": "true"},
            )
        )
    )
