"""Shared infrastructure for self-contained demo runners (demo_happy/unhappy/shipment_failure)."""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

import httpx

_SERVICE_A_URL = "http://localhost:8000"
_AZURITE_CONN = "UseDevelopmentStorage=true"
_AZURITE_CONTAINER = "integration-showcase"
_READINESS_TIMEOUT = 30.0
_POLL_INTERVAL = 0.5
# Workers need time to register on Temporal task queues after Service A is ready.
# Temporal has no "pollers ready" probe; this delay avoids StartToCloseTimeout
# on the first workflow execution.
_WORKER_SETTLE_DELAY = 2.0

_FORCE_VARS = frozenset(
    {
        "FORCE_PAYMENT_FAILURE",
        "FORCE_PAYMENT_TRANSIENT_FAILS",
        "FORCE_SHIPMENT_FAILURE",
    }
)

ScenarioMain = Callable[[], Coroutine[Any, Any, int]]


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


def _build_worker_env(overrides: dict[str, str] | None = None) -> dict[str, str]:
    """Strip FORCE_* vars so workers never inherit accidental shell leakage."""
    env = {k: v for k, v in os.environ.items() if k not in _FORCE_VARS}
    if overrides:
        env.update(overrides)
    return env


def _start_workers(
    log: int,
    *,
    service_c_overrides: dict[str, str] | None = None,
    service_d_overrides: dict[str, str] | None = None,
) -> list[subprocess.Popen[bytes]]:
    exe = sys.executable
    base = _build_worker_env()
    env_c = _build_worker_env(service_c_overrides)
    env_d = _build_worker_env(service_d_overrides)
    uvicorn_cmd = [exe, "-m", "uvicorn", "integration_showcase.service_a.app:app", "--port", "8000"]
    specs: list[tuple[list[str], dict[str, str]]] = [
        (uvicorn_cmd, base),
        ([exe, "-m", "integration_showcase.workflow.worker"], base),
        ([exe, "-m", "integration_showcase.service_b.worker"], base),
        ([exe, "-m", "integration_showcase.service_c.worker"], env_c),
        ([exe, "-m", "integration_showcase.service_d.worker"], env_d),
    ]
    # CREATE_NEW_PROCESS_GROUP is required on Windows to send CTRL_BREAK_EVENT;
    # terminate() maps to TerminateProcess (immediate kill), bypassing OTel flush.
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    return [
        subprocess.Popen(cmd, env=env, stdout=log, stderr=log, creationflags=flags)
        for cmd, env in specs
    ]


def _stop_workers(procs: list[subprocess.Popen[bytes]]) -> None:
    for p in procs:
        if sys.platform == "win32":
            p.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
        else:
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
                await client.get(f"{_SERVICE_A_URL}/openapi.json", timeout=1.0)
                return
            except httpx.HTTPError:
                await asyncio.sleep(_POLL_INTERVAL)
    raise TimeoutError(f"Service A not ready after {timeout}s — is `docker compose up -d` running?")


async def run_demo(
    scenario_main: ScenarioMain,
    log_file: Path,
    *,
    service_c_overrides: dict[str, str] | None = None,
    service_d_overrides: dict[str, str] | None = None,
) -> int:
    log_file.parent.mkdir(exist_ok=True)
    os.environ.setdefault("STORE_URL", _AZURITE_CONN)
    os.environ.setdefault("STORE_CONTAINER", _AZURITE_CONTAINER)

    print("Ensuring Azurite container...", flush=True)
    try:
        _ensure_azurite_container()
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    with log_file.open("wb") as log:
        procs = _start_workers(
            log.fileno(),
            service_c_overrides=service_c_overrides,
            service_d_overrides=service_d_overrides,
        )
        try:
            print(f"Waiting for Service A (worker logs → {log_file})...", flush=True)
            await _wait_ready()
            await asyncio.sleep(_WORKER_SETTLE_DELAY)
            return await scenario_main()
        except TimeoutError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        finally:
            _stop_workers(procs)
