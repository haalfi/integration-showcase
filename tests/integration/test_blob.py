"""Integration tests for shared/blob.py requiring Docker services.

Run with: pytest -m integration  (after docker compose up -d)

Env vars required:
    STORE_URL       Azurite connection string, e.g.
                    DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;
                    AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IF
                    suFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;
                    BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;
    STORE_CONTAINER Azure Blob container name (must exist in Azurite)
"""

from __future__ import annotations

import pytest

from integration_showcase.shared.blob import download, upload


@pytest.mark.integration
class TestBlobIntegration:
    """Exercise the production code paths: _make_store() + context-manager branches."""

    def test_upload_download_roundtrip_azurite(self) -> None:
        """Upload and download via real Azurite; verify sha256 integrity end-to-end."""
        data = b"integration test payload"
        path = "integration/test/blob.bin"
        ref = upload(data, path)
        result = download(ref)
        assert result == data
        assert ref.blob_url == path
