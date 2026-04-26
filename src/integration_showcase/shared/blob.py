"""Thin blob I/O wrapper — upload/download via remote-store Store API.

Bridges the application layer (Envelope BlobRefs) to the remote-store
backend.  STORE_URL holds the Azure / Azurite connection string;
STORE_CONTAINER holds the container name.  Backend switching (Azurite
for local dev, Azure Blob Storage for prod) requires no code changes.

Test seam: replace ``_store_factory`` to inject a ``MemoryBackend``-backed
store via ``monkeypatch.setattr``.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable

from remote_store import Store
from remote_store.backends import AzureBackend
from remote_store.ext.otel import otel_observe

from integration_showcase.shared.envelope import BlobRef


def _make_store() -> Store:
    """Construct a Store from STORE_URL and STORE_CONTAINER env vars.

    STORE_URL       — Azure / Azurite connection string, e.g.
                      ``DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;...``
                      or the shorthand ``UseDevelopmentStorage=true``.
    STORE_CONTAINER — Azure Blob container name.

    Wrapped in :func:`otel_observe` so every Store I/O op emits a
    ``store.<operation>`` span as a child of the current activity span
    (IS-005). When no :class:`TracerProvider` is configured, the OTel
    calls are zero-cost no-ops.
    """
    connection_string = os.environ["STORE_URL"]
    container = os.environ["STORE_CONTAINER"]
    backend = AzureBackend(container=container, connection_string=connection_string)
    return otel_observe(Store(backend))


# Module-level seam — replace in tests via monkeypatch.setattr to inject a
# MemoryBackend-backed store without Azure I/O.
_store_factory: Callable[[], Store] = _make_store


def upload(data: bytes, path: str, *, metadata: dict[str, str] | None = None) -> BlobRef:
    """Write *data* to *path* in the blob store and return a ``BlobRef``.

    ``sha256`` is computed before upload.  Overwrites an existing blob at
    *path* so that Temporal retries are idempotent.

    Args:
        data: Raw payload bytes to store.
        path: Store-relative path, e.g. ``workflows/tx-001/input.json``.
        metadata: Optional blob metadata (e.g. :meth:`Envelope.blob_metadata`).
            Written atomically with the blob via ``Store.write(metadata=...)``.
            ``None`` omits the metadata kwarg entirely; an empty dict explicitly
            clears any existing metadata. Keyword-only per DESIGN.md §3.

    Returns:
        ``BlobRef`` with ``blob_url=path``, the hex SHA-256 digest, and the
        etag returned by the backend (``""`` when the backend does not echo
        one in its write response).
    """
    sha256 = hashlib.sha256(data).hexdigest()
    with _store_factory() as store:
        if metadata is not None:
            result = store.write(path, data, overwrite=True, metadata=metadata)
        else:
            result = store.write(path, data, overwrite=True)
        etag = result.etag or ""
    return BlobRef(blob_url=path, sha256=sha256, etag=etag)


def list_folders(prefix: str) -> list[str]:
    """Return the immediate subfolder names under *prefix* (empty list if missing)."""
    with _store_factory() as store:
        return [entry.name for entry in store.list_folders(prefix)]


def list_files(prefix: str) -> list[tuple[str, str, int]]:
    """Return ``(name, path, size)`` for every blob under *prefix* (recursive = False)."""
    with _store_factory() as store:
        return [(info.name, str(info.path), info.size) for info in store.list_files(prefix)]


def read_path(path: str) -> bytes:
    """Read raw bytes at *path*. Raises ``remote_store.NotFound`` when absent."""
    with _store_factory() as store:
        return store.read_bytes(path)


def download(ref: BlobRef) -> bytes:
    """Fetch the blob described by *ref* and verify its SHA-256 digest.

    Args:
        ref: ``BlobRef`` produced by :func:`upload`.

    Returns:
        The blob's raw bytes.

    Raises:
        remote_store.NotFound: If the blob does not exist.
        ValueError: If the downloaded content does not match ``ref.sha256``.
    """
    with _store_factory() as store:
        data = store.read_bytes(ref.blob_url)
    actual = hashlib.sha256(data).hexdigest()
    if actual != ref.sha256:
        raise ValueError(
            f"SHA-256 mismatch for {ref.blob_url!r}: expected {ref.sha256!r}, got {actual!r}"
        )
    return data
