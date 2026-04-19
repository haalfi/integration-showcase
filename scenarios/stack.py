"""Interactive stack runner — long-running Service A + 4 workers, blocks on Ctrl-C.

Usage::

    docker compose up -d     # infra: Temporal, Azurite, Jaeger, Temporal UI
    hatch run stack          # app:   Service A + workflow + B/C/D workers

Unlike ``demo-*``, this does not drive a scenario. It keeps the stack alive so
you can fire orders yourself via the Swagger UI at ``http://localhost:8000/docs``
and browse the resulting blobs at ``http://localhost:8000/blobs``.

Failure injection still works but must be set in the shell before launching::

    FORCE_PAYMENT_FAILURE=true hatch run stack

Making failure modes per-request is a separate backlog item.
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from pathlib import Path

from scenarios._demo import (
    _WORKER_SETTLE_DELAY,
    _ensure_azurite_container,
    _start_workers,
    _stop_workers,
    _wait_ready,
)

_STACK_LOG = Path("./tmp/stack.log")


def _print_urls() -> None:
    print()
    print("Stack ready. UIs:")
    print("  Service A (Swagger) http://localhost:8000/docs")
    print("  Service A (blobs)   http://localhost:8000/blobs")
    print("  Temporal UI         http://localhost:8088")
    print("  Jaeger UI           http://localhost:16686")
    print()
    print(f"Worker logs: {_STACK_LOG}")
    print("Press Ctrl-C to stop.")
    print(flush=True)


async def main() -> int:
    _STACK_LOG.parent.mkdir(exist_ok=True)
    try:
        _ensure_azurite_container()
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    # STORE_URL/CONTAINER must be set for Service A's lifespan Temporal client
    # and blob I/O. _demo.run_demo() sets these via os.environ.setdefault; we
    # replicate that here so users can launch stack without pre-exporting.
    os.environ.setdefault("STORE_URL", "UseDevelopmentStorage=true")
    os.environ.setdefault("STORE_CONTAINER", "integration-showcase")

    with _STACK_LOG.open("wb") as log:
        procs = _start_workers(log.fileno())
        try:
            print(f"Starting Service A + 4 workers (logs → {_STACK_LOG})...", flush=True)
            await _wait_ready()
            # Mirror run_demo(): workers need time to register on Temporal task
            # queues after Service A is up; firing POST /order before this delay
            # triggers StartToCloseTimeout on the first execution.
            await asyncio.sleep(_WORKER_SETTLE_DELAY)
            _print_urls()

            # Block until SIGINT/SIGTERM. asyncio.Event + signal handlers is the
            # cross-platform way; on Windows loop.add_signal_handler is not
            # supported, so signal.signal is the portable fallback.
            stop = asyncio.Event()

            def _request_stop(*_: object) -> None:
                stop.set()

            signal.signal(signal.SIGINT, _request_stop)
            signal.signal(signal.SIGTERM, _request_stop)

            await stop.wait()
            print("\nStopping workers...", flush=True)
            return 0
        finally:
            _stop_workers(procs)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(asyncio.run(main()))
