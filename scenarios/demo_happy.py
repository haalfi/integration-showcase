"""Run the happy-path demo. Usage: hatch run demo-happy. Prerequisites: docker compose up -d"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from scenarios._demo import run_demo
from scenarios.run_happy import main as _run_happy

if __name__ == "__main__":  # pragma: no cover
    sys.exit(asyncio.run(run_demo(_run_happy, Path("./tmp/demo-happy-workers.log"))))
