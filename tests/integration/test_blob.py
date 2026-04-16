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

    def test_sha256_mismatch_raises_value_error(self) -> None:
        """SHA-256 tamper detection works end-to-end through the real backend."""
        path = "integration/test/tampered.bin"
        ref = upload(b"original", path)
        # Overwrite the blob; ref still holds the sha256 of "original"
        upload(b"tampered", path)
        with pytest.raises(ValueError, match="SHA-256 mismatch"):
            download(ref)
