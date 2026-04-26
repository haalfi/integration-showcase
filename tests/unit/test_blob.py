"""Unit tests for shared/blob.py using MemoryBackend (no I/O, no Docker)."""

from __future__ import annotations

import hashlib
from collections.abc import Generator
from contextlib import contextmanager

import pytest
from remote_store import NotFound, Store
from remote_store.backends import MemoryBackend

import integration_showcase.shared.blob as blob_module
from integration_showcase.shared.blob import download, list_files, list_folders, read_path, upload
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

    def test_etag_empty_for_memory_backend(self, store: Store) -> None:
        """MemoryBackend write response has no etag; WriteResult.etag=None normalises to ''."""
        ref = upload(b"payload", "etag/test.bin")
        assert ref.etag == ""


class TestUploadMetadata:
    def test_no_metadata_stored_when_none(self, store: Store) -> None:
        """``metadata=None`` writes the blob without setting any metadata."""
        upload(b"payload", "no-meta/blob.bin")
        assert store.get_file_info("no-meta/blob.bin").metadata is None

    def test_metadata_stored_with_blob(self, store: Store) -> None:
        """``metadata=...`` is written atomically and retrievable via get_file_info."""
        meta = {"workflow_id": "wf-1", "step_id": "start"}
        upload(b"payload", "meta/blob.bin", metadata=meta)
        assert store.get_file_info("meta/blob.bin").metadata == meta

    def test_empty_metadata_dict_reaches_store(self, store: Store) -> None:
        """``metadata={}`` is passed to the store and clears previously written metadata."""
        upload(b"payload", "meta/empty.bin", metadata={"k": "v"})
        upload(b"payload", "meta/empty.bin", metadata={})
        info = store.get_file_info("meta/empty.bin")
        # MemoryBackend may return {} or None after an empty-dict write — either is
        # acceptable, but the "k" key written in the first pass must be gone.
        assert info.metadata != {"k": "v"}

    @pytest.mark.parametrize(
        "case, meta",
        [
            (
                "full",
                {
                    "workflow_id": "order-tx-meta",
                    "run_id": "run-meta-1",
                    "step_id": "reserve-inventory",
                    "schema_version": "1.0",
                    "idempotency_key": "tx-meta:reserve-inventory:1.0",
                },
            ),
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
    def test_full_envelope_metadata_roundtrip(
        self, store: Store, case: str, meta: dict[str, str]
    ) -> None:
        """Five-key envelope metadata (including empty run_id) round-trips through the store."""
        path = f"meta/envelope-{case}.bin"
        upload(b"envelope payload", path, metadata=meta)
        assert store.get_file_info(path).metadata == meta


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


class TestListingHelpers:
    """``list_folders`` / ``list_files`` / ``read_path`` helpers feed the Service A browser."""

    def test_list_folders_returns_subfolder_names(self, store: Store) -> None:
        store.write("workflows/tx-1/input.json", b"{}", overwrite=True)
        store.write("workflows/tx-2/input.json", b"{}", overwrite=True)
        assert sorted(list_folders("workflows")) == ["tx-1", "tx-2"]

    def test_list_folders_missing_prefix_returns_empty(self, store: Store) -> None:
        assert list_folders("nothing-here") == []

    def test_list_files_returns_name_path_size(self, store: Store) -> None:
        store.write("workflows/tx-1/input.json", b"ab", overwrite=True)
        store.write("workflows/tx-1/reserve.json", b"abcd", overwrite=True)
        entries = {name: (path, size) for name, path, size in list_files("workflows/tx-1")}
        assert entries["input.json"] == ("workflows/tx-1/input.json", 2)
        assert entries["reserve.json"] == ("workflows/tx-1/reserve.json", 4)

    def test_read_path_returns_bytes(self, store: Store) -> None:
        store.write("workflows/tx-1/input.json", b'{"ok":true}', overwrite=True)
        assert read_path("workflows/tx-1/input.json") == b'{"ok":true}'

    def test_read_path_raises_not_found(self, store: Store) -> None:  # noqa: ARG002
        with pytest.raises(NotFound):
            read_path("workflows/tx-missing/input.json")
