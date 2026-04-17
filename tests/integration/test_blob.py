"""Integration tests for shared/blob.py requiring Docker services.

Run with: pytest -m integration  (after docker compose up -d)

No manual setup required: the session fixture in conftest.py automatically
creates the Azurite container and sets STORE_URL / STORE_CONTAINER defaults.
The fixture skips the session with a clear message if Azurite is unreachable.
"""

from __future__ import annotations

import pytest
from remote_store import NotFound

from integration_showcase.shared.blob import download, upload
from integration_showcase.shared.envelope import BlobRef


@pytest.mark.integration
class TestBlobIntegration:
    """Exercise the production code paths: _store_factory() + context-manager branches."""

    def test_upload_download_roundtrip_azurite(self) -> None:
        """Upload and download via real Azurite; verify sha256 integrity end-to-end."""
        data = b"integration test payload"
        path = "integration/test/blob.bin"
        ref = upload(data, path)
        result = download(ref)
        assert result == data
        assert ref.blob_url == path

    def test_download_raises_not_found_for_missing_path(self) -> None:
        """download() propagates NotFound through the context-manager teardown path."""
        ref = BlobRef(blob_url="integration/test/does-not-exist.bin", sha256="a" * 64)
        with pytest.raises(NotFound):
            download(ref)

    def test_etag_populated_from_azurite(self) -> None:
        """upload() populates BlobRef.etag from Azure ETag via get_file_info()."""
        ref = upload(b"etag test payload", "integration/test/etag-check.bin")
        assert ref.etag != "", "Expected non-empty etag from Azurite after upload"

    def test_sha256_mismatch_raises_value_error(self) -> None:
        """SHA-256 tamper detection works end-to-end through the real backend."""
        path = "integration/test/tampered.bin"
        ref = upload(b"original", path)
        # Overwrite the blob; ref still holds the sha256 of "original"
        upload(b"tampered", path)
        with pytest.raises(ValueError, match="SHA-256 mismatch"):
            download(ref)

    @pytest.mark.parametrize(
        "case, meta",
        [
            (
                "post-start",
                {
                    "workflow_id": "order-tx-meta",
                    "run_id": "run-meta-1",
                    "step_id": "reserve-inventory",
                    "schema_version": "1.0",
                    "idempotency_key": "tx-meta:reserve-inventory:1.0",
                },
            ),
            # Ingress writes the input blob before Temporal returns a run_id,
            # so run_id is always "" at that point. Pin Azurite's behaviour
            # for the empty-string case — the metadata layout BK-005 documents
            # as an accepted limitation.
            (
                "ingress-empty-run-id",
                {
                    "workflow_id": "order-tx-ingress",
                    "run_id": "",
                    "step_id": "start",
                    "schema_version": "1.0",
                    "idempotency_key": "tx-ingress:start:1.0",
                },
            ),
        ],
    )
    def test_metadata_roundtrip_from_azurite(self, case: str, meta: dict[str, str]) -> None:
        """upload(metadata=...) attaches Azure blob metadata readable via the Blob SDK.

        Concept §6 requires payload blobs to carry correlation IDs so operators
        can trace any orphaned blob back to the step that wrote it.
        """
        import os

        from azure.storage.blob import BlobServiceClient

        path = f"integration/test/metadata-check-{case}.bin"
        upload(b"metadata roundtrip", path, metadata=meta)

        service = BlobServiceClient.from_connection_string(os.environ["STORE_URL"])
        blob_client = service.get_blob_client(
            container=os.environ["STORE_CONTAINER"],
            blob=path,
        )
        props = blob_client.get_blob_properties()
        assert props.metadata == meta
