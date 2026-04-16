"""SQLite connection helper for per-service local state.

Each activity worker (B/C/D) owns a private SQLite database keyed by the
service-specific env var (``SERVICE_B_DB_PATH`` etc.). Activities persist
side-effect state keyed on ``envelope.idempotency_key`` so that Temporal
retries become no-ops (DESIGN.md §Envelope invariants #4).

Test seam: replace ``_connect_factory`` via ``monkeypatch.setattr`` to
inject file-backed ``tmp_path`` connections (or anything else) without
touching env vars.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager

# Module-level factory -- replace in tests via monkeypatch.setattr.
_connect_factory: Callable[[str], sqlite3.Connection] = sqlite3.connect


@contextmanager
def connect(path: str) -> Iterator[sqlite3.Connection]:
    """Open a SQLite connection at *path* as a context manager.

    ``row_factory = sqlite3.Row`` makes ``SELECT`` results addressable by
    column name. ``PRAGMA journal_mode = WAL`` improves concurrent-reader
    behavior on file-backed databases; on ``:memory:`` the pragma is a
    silent no-op.

    On context exit the connection is committed (on success), rolled back
    (on exception), and always closed. Callers must therefore do all DB
    work inside the ``with`` block.
    """
    conn = _connect_factory(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
