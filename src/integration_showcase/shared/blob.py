"""Thin blob I/O wrapper — upload/download via remote-store Store API.

Bridges the application layer (Envelope BlobRefs) to the remote-store
backend.  STORE_URL holds the Azure / Azurite connection string;
STORE_CONTAINER holds the container name.  Backend switching (Azurite
for local dev, Azure Blob Storage for prod) requires no code changes.

Test seam: replace ``_store_factory`` via ``monkeypatch.setattr`` to inject
a ``MemoryBackend``-backed store without touching env vars or network.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable

from remote_store import Capability, Store
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


# Module-level factory — replace in tests via monkeypatch.setattr to inject a
# MemoryBackend-backed store without touching env vars or network I/O.
_store_factory: Callable[[], Store] = _make_store


def upload(data: bytes, path: str) -> BlobRef:
    """Write *data* to *path* in the blob store and return a ``BlobRef``.

    ``sha256`` is computed before upload.  Overwrites an existing blob at
    *path* so that Temporal retries are idempotent.

    Args:
        data: Raw payload bytes to store.
        path: Store-relative path, e.g. ``workflows/tx-001/input.json``.

    Returns:
        ``BlobRef`` with ``blob_url=path`` and the hex SHA-256 digest.
    """
    sha256 = hashlib.sha256(data).hexdigest()
    etag = ""
    with _store_factory() as store:
        store.write(path, data, overwrite=True)
        if store.supports(Capability.METADATA):
            etag = store.get_file_info(path).etag or ""
    return BlobRef(blob_url=path, sha256=sha256, etag=etag)


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
