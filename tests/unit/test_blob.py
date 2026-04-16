"""Unit tests for shared/blob.py using MemoryBackend (no I/O, no Docker)."""

from __future__ import annotations

import hashlib
from collections.abc import Generator
from contextlib import contextmanager

import pytest
from remote_store import NotFound, Store
from remote_store.backends import MemoryBackend

import integration_showcase.shared.blob as blob_module
from integration_showcase.shared.blob import download, upload
from integration_showcase.shared.envelope import BlobRef


@pytest.fixture()
def store(monkeypatch: pytest.MonkeyPatch) -> Store:
    """Inject a MemoryBackend-backed store via the module-level factory seam."""
    s = Store(MemoryBackend())

    @contextmanager
    def _factory() -> Generator[Store, None, None]:
        yield s

    monkeypatch.setattr(blob_module, "_store_factory", _factory)
    return s


class TestMakeStore:
    def test_upload_raises_key_error_when_store_url_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("STORE_URL", raising=False)
        monkeypatch.delenv("STORE_CONTAINER", raising=False)
        with pytest.raises(KeyError, match="STORE_URL"):
            upload(b"", "x")

    def test_upload_raises_key_error_when_store_container_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("STORE_URL", "UseDevelopmentStorage=true")
        monkeypatch.delenv("STORE_CONTAINER", raising=False)
        with pytest.raises(KeyError, match="STORE_CONTAINER"):
            upload(b"", "x")


class TestUpload:
    def test_returns_blobref_with_correct_path(self, store: Store) -> None:
        ref = upload(b"hello", "workflows/tx-001/input.json")
        assert ref.blob_url == "workflows/tx-001/input.json"

    def test_sha256_matches_content(self, store: Store) -> None:
        data = b"payload data"
        ref = upload(data, "blobs/data.bin")
        assert ref.sha256 == hashlib.sha256(data).hexdigest()

    def test_blob_is_readable_after_upload(self, store: Store) -> None:
        data = b"round-trip content"
        upload(data, "check/file.bin")
        assert store.read_bytes("check/file.bin") == data

    def test_upload_is_idempotent(self, store: Store) -> None:
        """Re-uploading the same path must not raise (overwrite=True)."""
        upload(b"first", "items/blob.bin")
        ref = upload(b"second", "items/blob.bin")
        assert store.read_bytes("items/blob.bin") == b"second"
        assert ref.sha256 == hashlib.sha256(b"second").hexdigest()

    @pytest.mark.parametrize(
        "data, path",
        [
            (b"", "empty/file.bin"),
            (b"\x00\xff\xfe", "binary/raw.bin"),
            (b"x" * 10_000, "large/chunk.bin"),
        ],
    )
    def test_various_payloads(self, store: Store, data: bytes, path: str) -> None:
        ref = upload(data, path)
        assert ref.sha256 == hashlib.sha256(data).hexdigest()
        assert ref.blob_url == path


class TestDownload:
    def test_roundtrip(self, store: Store) -> None:
        data = b"roundtrip payload"
        ref = upload(data, "rt/blob.bin")
        assert download(ref) == data

    def test_raises_not_found_for_missing_blob(self, store: Store) -> None:
        ref = BlobRef(blob_url="does/not/exist.bin", sha256="a" * 64)
        with pytest.raises(NotFound):
            download(ref)

    def test_raises_on_sha256_mismatch(self, store: Store) -> None:
        data = b"original"
        ref = upload(data, "tampered/blob.bin")
        # Overwrite the blob with different content behind ref's back
        store.write("tampered/blob.bin", b"tampered", overwrite=True)
        with pytest.raises(ValueError, match="SHA-256 mismatch"):
            download(ref)

    def test_empty_payload_roundtrip(self, store: Store) -> None:
        ref = upload(b"", "empty/payload.bin")
        result = download(ref)
        assert result == b""
