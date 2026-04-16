"""SQLite connection helper for per-service local state.

Each activity worker (B/C/D) owns a private SQLite database keyed by the
service-specific env var (``SERVICE_B_DB_PATH`` etc.). Activities persist
side-effect state keyed on ``envelope.idempotency_key`` so that Temporal
retries become no-ops (DESIGN.md §Envelope invariants #4).

Test seam: replace ``_connect_factory`` via ``monkeypatch.setattr`` to
inject a shared ``:memory:`` connection without touching the filesystem.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

# Module-level factory -- replace in tests via monkeypatch.setattr to inject a
# shared in-memory connection without touching env vars or the filesystem.
_connect_factory: Callable[[str], sqlite3.Connection] = sqlite3.connect


def connect(path: str) -> sqlite3.Connection:
    """Open (or create) a SQLite database at *path* with sane POC defaults.

    ``row_factory = sqlite3.Row`` makes ``SELECT`` results addressable by
    column name. ``PRAGMA journal_mode = WAL`` improves concurrent-reader
    behavior on file-backed databases; on ``:memory:`` the pragma is a
    silent no-op.

    The caller is responsible for committing and closing the returned
    connection (POC: short-lived per activity invocation).
    """
    conn = _connect_factory(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    return conn
