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
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager

# Module-level factory -- replace in tests via monkeypatch.setattr.
_connect_factory: Callable[[str], sqlite3.Connection] = sqlite3.connect

# Paths for which we've already issued ``PRAGMA journal_mode = WAL`` in this
# process. The pragma is persistent on file-backed databases, so reissuing it
# on every connection is wasteful and can conflict with other writers. Kept
# behind a lock because worker activities may run concurrently on a
# ``ThreadPoolExecutor``.
_bootstrapped_paths: set[str] = set()
_bootstrap_lock = threading.Lock()


def _reset_bootstrap_cache() -> None:
    """Test hook: forget bootstrapped paths (used when tests swap factories)."""
    with _bootstrap_lock:
        _bootstrapped_paths.clear()


@contextmanager
def connect(path: str) -> Iterator[sqlite3.Connection]:
    """Open a SQLite connection at *path* as a context manager.

    ``row_factory = sqlite3.Row`` makes ``SELECT`` results addressable by
    column name. ``PRAGMA journal_mode = WAL`` is issued once per path per
    process (WAL is persistent on file-backed databases; reissuing it on
    every connect is wasteful). On ``:memory:`` the pragma is harmless but
    still runs once -- SQLite reports ``memory`` as the journal_mode there
    rather than switching to WAL.

    On context exit the connection is committed (on success), rolled back
    (on exception), and always closed. Callers must therefore do all DB
    work inside the ``with`` block.
    """
    conn = _connect_factory(path)
    conn.row_factory = sqlite3.Row
    with _bootstrap_lock:
        if path not in _bootstrapped_paths:
            conn.execute("PRAGMA journal_mode = WAL")
            _bootstrapped_paths.add(path)
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
