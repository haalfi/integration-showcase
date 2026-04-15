"""Thin blob I/O wrapper — upload/download via remote-store Store API.

Bridges the application layer (Envelope BlobRefs) to the remote-store
backend.  STORE_URL holds the Azure / Azurite connection string;
STORE_CONTAINER holds the container name.  Backend switching (Azurite
for local dev, Azure Blob Storage for prod) requires no code changes.
"""

from __future__ import annotations

import hashlib
import os

from remote_store import Store
from remote_store.backends import AzureBackend

from integration_showcase.shared.envelope import BlobRef


def _make_store() -> Store:
    """Construct a Store from STORE_URL and STORE_CONTAINER env vars.

    STORE_URL   — Azure / Azurite connection string, e.g.
                  ``DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;...``
                  or the shorthand ``UseDevelopmentStorage=true``.
    STORE_CONTAINER — Azure Blob container name.
    """
    connection_string = os.environ["STORE_URL"]
    container = os.environ["STORE_CONTAINER"]
    backend = AzureBackend(container=container, connection_string=connection_string)
    return Store(backend)


def upload(data: bytes, path: str, *, _store: Store | None = None) -> BlobRef:
    """Write *data* to *path* in the blob store and return a ``BlobRef``.

    ``sha256`` is computed before upload.  Overwrites an existing blob at
    *path* so that Temporal retries are idempotent.

    Args:
        data: Raw payload bytes to store.
        path: Store-relative path, e.g. ``workflows/tx-001/input.json``.
        _store: Injected store for unit tests; production callers omit this.

    Returns:
        ``BlobRef`` with ``blob_url=path`` and the hex SHA-256 digest.
    """
    sha256 = hashlib.sha256(data).hexdigest()
    if _store is not None:
        _store.write(path, data, overwrite=True)
        return BlobRef(blob_url=path, sha256=sha256)
    with _make_store() as store:
        store.write(path, data, overwrite=True)
    return BlobRef(blob_url=path, sha256=sha256)


def download(ref: BlobRef, *, _store: Store | None = None) -> bytes:
    """Fetch the blob described by *ref* and verify its SHA-256 digest.

    Args:
        ref: ``BlobRef`` produced by :func:`upload`.
        _store: Injected store for unit tests; production callers omit this.

    Returns:
        The blob's raw bytes.

    Raises:
        remote_store.NotFound: If the blob does not exist.
        ValueError: If the downloaded content does not match ``ref.sha256``.
    """
    if _store is not None:
        data = _store.read_bytes(ref.blob_url)
    else:
        with _make_store() as store:
            data = store.read_bytes(ref.blob_url)
    actual = hashlib.sha256(data).hexdigest()
    if actual != ref.sha256:
        raise ValueError(
            f"SHA-256 mismatch for {ref.blob_url!r}: expected {ref.sha256!r}, got {actual!r}"
        )
    return data
