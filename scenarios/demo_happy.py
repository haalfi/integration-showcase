"""Start all workers, run the happy-path scenario, shut everything down.

Usage:
    hatch run demo-happy

Prerequisites:
    docker compose up -d
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

from scenarios.run_happy import main as _run_happy

_SERVICE_A_URL = "http://localhost:8000"
_AZURITE_CONN = "UseDevelopmentStorage=true"
_AZURITE_CONTAINER = "integration-showcase"
_READINESS_TIMEOUT = 30.0
_POLL_INTERVAL = 0.5
_LOG_FILE = Path("./tmp/demo-workers.log")


def _ensure_azurite_container() -> None:
    from azure.core.exceptions import ResourceExistsError, ServiceRequestError
    from azure.storage.blob import BlobServiceClient

    client = BlobServiceClient.from_connection_string(_AZURITE_CONN)
    try:
        client.create_container(_AZURITE_CONTAINER)
    except ResourceExistsError:
        pass
    except ServiceRequestError as exc:
        raise RuntimeError("Azurite not reachable — run: docker compose up -d") from exc


def _start_workers(log: int) -> list[subprocess.Popen[bytes]]:
    exe = sys.executable
    cmds: list[list[str]] = [
        [exe, "-m", "uvicorn", "integration_showcase.service_a.app:app", "--port", "8000"],
        [exe, "-m", "integration_showcase.workflow.worker"],
        [exe, "-m", "integration_showcase.service_b.worker"],
        [exe, "-m", "integration_showcase.service_c.worker"],
        [exe, "-m", "integration_showcase.service_d.worker"],
    ]
    return [subprocess.Popen(cmd, stdout=log, stderr=log) for cmd in cmds]


def _stop_workers(procs: list[subprocess.Popen[bytes]]) -> None:
    for p in procs:
        p.terminate()
    for p in procs:
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()


async def _wait_ready(timeout: float = _READINESS_TIMEOUT) -> None:
    deadline = time.monotonic() + timeout
    async with httpx.AsyncClient() as client:
        while time.monotonic() < deadline:
            try:
                await client.get(_SERVICE_A_URL, timeout=1.0)
                return
            except httpx.ConnectError:
                await asyncio.sleep(_POLL_INTERVAL)
    raise TimeoutError(f"Service A not ready after {timeout}s — is `docker compose up -d` running?")


async def _demo() -> int:
    _LOG_FILE.parent.mkdir(exist_ok=True)
    os.environ.setdefault("STORE_URL", _AZURITE_CONN)
    os.environ.setdefault("STORE_CONTAINER", _AZURITE_CONTAINER)

    print("Ensuring Azurite container...", flush=True)
    _ensure_azurite_container()

    with _LOG_FILE.open("wb") as log:
        procs = _start_workers(log.fileno())
        try:
            print(f"Waiting for Service A (worker logs → {_LOG_FILE})...", flush=True)
            await _wait_ready()
            return await _run_happy()
        finally:
            _stop_workers(procs)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(asyncio.run(_demo()))
