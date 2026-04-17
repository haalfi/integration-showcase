"""Thin blob I/O wrapper — upload/download via remote-store Store API.

Bridges the application layer (Envelope BlobRefs) to the remote-store
backend.  STORE_URL holds the Azure / Azurite connection string;
STORE_CONTAINER holds the container name.  Backend switching (Azurite
for local dev, Azure Blob Storage for prod) requires no code changes.

Test seams: replace ``_store_factory`` to inject a ``MemoryBackend``-backed
store; replace ``_metadata_setter`` to observe (or no-op) the Azure SDK
metadata write. Both via ``monkeypatch.setattr``.
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


def _set_azure_blob_metadata(path: str, metadata: dict[str, str]) -> None:
    """Attach Azure blob metadata to *path* via the Azure SDK directly.

    remote-store v0.23.0 has no metadata channel on ``Store.write()`` and
    ``AzureBackend.unwrap()`` only exposes ``FileSystemClient`` (DataLake /
    HNS), which our blob-only Azurite does not serve. Until remote-store
    grows a native metadata API, we reuse ``STORE_URL`` / ``STORE_CONTAINER``
    to build a ``BlobServiceClient`` and call ``set_blob_metadata`` after
    the remote-store write completes.
    """
    from azure.storage.blob import BlobServiceClient

    connection_string = os.environ["STORE_URL"]
    container = os.environ["STORE_CONTAINER"]
    service = BlobServiceClient.from_connection_string(connection_string)
    service.get_blob_client(container=container, blob=path).set_blob_metadata(metadata)


# Module-level seams — replace in tests via monkeypatch.setattr to inject a
# MemoryBackend-backed store or capture metadata writes without Azure I/O.
_store_factory: Callable[[], Store] = _make_store
_metadata_setter: Callable[[str, dict[str, str]], None] = _set_azure_blob_metadata


def upload(data: bytes, path: str, metadata: dict[str, str] | None = None) -> BlobRef:
    """Write *data* to *path* in the blob store and return a ``BlobRef``.

    ``sha256`` is computed before upload.  Overwrites an existing blob at
    *path* so that Temporal retries are idempotent.

    Args:
        data: Raw payload bytes to store.
        path: Store-relative path, e.g. ``workflows/tx-001/input.json``.
        metadata: Optional Azure blob metadata (e.g.
            :meth:`Envelope.blob_metadata`). Applied after the write via the
            Azure SDK directly — see :func:`_set_azure_blob_metadata`.

    Returns:
        ``BlobRef`` with ``blob_url=path`` and the hex SHA-256 digest.
    """
    sha256 = hashlib.sha256(data).hexdigest()
    etag = ""
    with _store_factory() as store:
        store.write(path, data, overwrite=True)
        # ``is not None``: an empty dict must still reach the SDK so a caller
        # can deliberately clear existing Azure blob metadata.
        if metadata is not None:
            _metadata_setter(path, metadata)
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
