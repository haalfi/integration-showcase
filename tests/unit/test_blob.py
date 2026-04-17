"""Unit tests for shared/blob.py using MemoryBackend (no I/O, no Docker)."""

from __future__ import annotations

import dataclasses
import hashlib
from collections.abc import Generator
from contextlib import contextmanager

import pytest
from remote_store import Capability, FileInfo, NotFound, Store
from remote_store.backends import MemoryBackend

import integration_showcase.shared.blob as blob_module
from integration_showcase.shared.blob import download, upload
from integration_showcase.shared.envelope import BlobRef


class _EtagMemoryBackend(MemoryBackend):
    """MemoryBackend that injects a fake etag into get_file_info() responses."""

    FAKE_ETAG = "test-etag-abc123"

    def get_file_info(self, path: str) -> FileInfo:
        return dataclasses.replace(super().get_file_info(path), etag=self.FAKE_ETAG)


@pytest.fixture()
def store(monkeypatch: pytest.MonkeyPatch) -> Store:
    """Inject a MemoryBackend-backed store via the module-level factory seam.

    Also stubs the metadata seam so tests do not touch Azure even if a caller
    passes ``metadata=``.
    """
    s = Store(MemoryBackend())

    @contextmanager
    def _factory() -> Generator[Store, None, None]:
        yield s

    monkeypatch.setattr(blob_module, "_store_factory", _factory)
    monkeypatch.setattr(blob_module, "_metadata_setter", lambda _path, _meta: None)
    return s


@pytest.fixture()
def store_with_etag(monkeypatch: pytest.MonkeyPatch) -> Store:
    """Inject an _EtagMemoryBackend store that returns a non-empty etag from get_file_info."""
    s = Store(_EtagMemoryBackend())

    @contextmanager
    def _factory() -> Generator[Store, None, None]:
        yield s

    monkeypatch.setattr(blob_module, "_store_factory", _factory)
    monkeypatch.setattr(blob_module, "_metadata_setter", lambda _path, _meta: None)
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

    def test_etag_empty_for_memory_backend(self, store: Store) -> None:
        """MemoryBackend supports METADATA but get_file_info() returns etag=None; normalised to ''."""  # noqa: E501
        ref = upload(b"payload", "etag/test.bin")
        assert ref.etag == ""

    def test_etag_populated_when_backend_provides_etag(self, store_with_etag: Store) -> None:
        """upload() forwards the etag from get_file_info() into BlobRef."""
        assert store_with_etag.supports(Capability.METADATA)
        ref = upload(b"payload", "etag/test.bin")
        assert ref.etag == _EtagMemoryBackend.FAKE_ETAG


class TestUploadMetadata:
    def test_metadata_setter_not_called_when_metadata_absent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The Azure metadata seam stays untouched when no metadata dict is passed."""
        s = Store(MemoryBackend())

        @contextmanager
        def _factory() -> Generator[Store, None, None]:
            yield s

        monkeypatch.setattr(blob_module, "_store_factory", _factory)
        calls: list[tuple[str, dict[str, str]]] = []
        monkeypatch.setattr(
            blob_module,
            "_metadata_setter",
            lambda path, meta: calls.append((path, meta)),
        )
        upload(b"payload", "no-meta/blob.bin")
        assert calls == []

    def test_metadata_setter_receives_path_and_dict(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The Azure metadata seam is invoked once with the caller's dict."""
        s = Store(MemoryBackend())

        @contextmanager
        def _factory() -> Generator[Store, None, None]:
            yield s

        monkeypatch.setattr(blob_module, "_store_factory", _factory)
        calls: list[tuple[str, dict[str, str]]] = []
        monkeypatch.setattr(
            blob_module,
            "_metadata_setter",
            lambda path, meta: calls.append((path, meta)),
        )
        meta = {"workflow_id": "wf-1", "step_id": "start"}
        upload(b"payload", "meta/blob.bin", metadata=meta)
        assert calls == [("meta/blob.bin", meta)]

    def test_metadata_setter_invoked_with_empty_dict(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``metadata={}`` reaches the SDK so callers can clear Azure blob metadata.

        Only ``metadata=None`` skips the setter; an empty dict is a distinct intent.
        """
        s = Store(MemoryBackend())

        @contextmanager
        def _factory() -> Generator[Store, None, None]:
            yield s

        monkeypatch.setattr(blob_module, "_store_factory", _factory)
        calls: list[tuple[str, dict[str, str]]] = []
        monkeypatch.setattr(
            blob_module,
            "_metadata_setter",
            lambda path, meta: calls.append((path, meta)),
        )
        upload(b"payload", "meta/empty.bin", metadata={})
        assert calls == [("meta/empty.bin", {})]


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
