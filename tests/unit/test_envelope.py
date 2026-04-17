"""Unit tests for Envelope and BlobRef."""

from __future__ import annotations

import pytest

from integration_showcase.shared.envelope import BlobRef, Envelope


def _blob_ref(**overrides: str) -> BlobRef:
    defaults = {
        "blob_url": "workflows/tx-001/input.json",
        "sha256": "abc123",
    }
    return BlobRef(**{**defaults, **overrides})


def _envelope(**overrides: object) -> Envelope:
    defaults: dict[str, object] = {
        "workflow_id": "order-123",
        "run_id": "run-456",
        "business_tx_id": "tx-001",
        "step_id": "start",
        "payload_ref": _blob_ref(),
        "traceparent": "00-abc-def-01",
        "idempotency_key": "tx-001:start:1.0",
    }
    return Envelope(**{**defaults, **overrides})


class TestMakeIdempotencyKey:
    def test_canonical_format(self) -> None:
        key = Envelope.make_idempotency_key("tx-001", "reserve-inventory")
        assert key == "tx-001:reserve-inventory:1.0"

    def test_custom_schema_version(self) -> None:
        key = Envelope.make_idempotency_key("tx-001", "reserve-inventory", "2.0")
        assert key == "tx-001:reserve-inventory:2.0"

    @pytest.mark.parametrize(
        "tx_id, step_id, version",
        [
            ("tx-X", "step-Y", "1.0"),
            ("tx-abc", "charge-payment", "2.5"),
            ("tx-000", "compensate.reserve-inventory", "1.0"),
        ],
    )
    def test_all_three_segments_present(self, tx_id: str, step_id: str, version: str) -> None:
        key = Envelope.make_idempotency_key(tx_id, step_id, version)
        parts = key.split(":")
        assert parts == [tx_id, step_id, version]


class TestEnvelopeAdvance:
    def test_step_id_updated(self) -> None:
        env = _envelope(step_id="start")
        next_env = env.advance("reserve-inventory", _blob_ref())
        assert next_env.step_id == "reserve-inventory"

    def test_parent_step_id_is_previous_step(self) -> None:
        env = _envelope(step_id="start")
        next_env = env.advance("reserve-inventory", _blob_ref())
        assert next_env.parent_step_id == "start"

    def test_idempotency_key_reflects_new_step(self) -> None:
        env = _envelope(business_tx_id="tx-001", schema_version="1.0")
        next_env = env.advance("reserve-inventory", _blob_ref())
        assert next_env.idempotency_key == "tx-001:reserve-inventory:1.0"

    def test_correlation_ids_preserved(self) -> None:
        env = _envelope(workflow_id="order-123", run_id="run-456", business_tx_id="tx-001")
        next_env = env.advance("reserve-inventory", _blob_ref())
        assert next_env.workflow_id == "order-123"
        assert next_env.run_id == "run-456"
        assert next_env.business_tx_id == "tx-001"

    def test_payload_ref_updated(self) -> None:
        env = _envelope()
        new_ref = _blob_ref(sha256="newsha", blob_url="workflows/tx-001/result.json")
        next_env = env.advance("reserve-inventory", new_ref)
        assert next_env.payload_ref.sha256 == "newsha"
        assert next_env.payload_ref.blob_url == "workflows/tx-001/result.json"

    def test_advance_is_immutable(self) -> None:
        env = _envelope(step_id="start")
        env.advance("reserve-inventory", _blob_ref())
        assert env.step_id == "start"

    def test_traceparent_preserved(self) -> None:
        env = _envelope(traceparent="00-trace-span-01")
        next_env = env.advance("reserve-inventory", _blob_ref())
        assert next_env.traceparent == "00-trace-span-01"

    def test_tracestate_preserved(self) -> None:
        env = _envelope(tracestate="vendor=opaque")
        next_env = env.advance("reserve-inventory", _blob_ref())
        assert next_env.tracestate == "vendor=opaque"


class TestBlobMetadata:
    def test_returns_five_correlation_fields(self) -> None:
        env = _envelope(
            workflow_id="order-123",
            run_id="run-456",
            step_id="reserve-inventory",
            idempotency_key="tx-001:reserve-inventory:1.0",
        )
        assert env.blob_metadata() == {
            "workflow_id": "order-123",
            "run_id": "run-456",
            "step_id": "reserve-inventory",
            "schema_version": "1.0",
            "idempotency_key": "tx-001:reserve-inventory:1.0",
        }

    def test_values_are_strings(self) -> None:
        """Azure blob metadata requires string values; guard against accidental non-str."""
        meta = _envelope().blob_metadata()
        assert all(isinstance(v, str) for v in meta.values())


class TestEnvelopeValidation:
    def test_empty_idempotency_key_raises(self) -> None:
        with pytest.raises(Exception, match="idempotency_key"):
            _envelope(idempotency_key="")

    def test_default_schema_version(self) -> None:
        env = _envelope()
        assert env.schema_version == "1.0"

    def test_default_baggage_empty(self) -> None:
        env = _envelope()
        assert env.baggage == {}

    def test_default_tracestate_empty(self) -> None:
        env = _envelope()
        assert env.tracestate == ""

    def test_default_parent_step_id_none(self) -> None:
        env = _envelope()
        assert env.parent_step_id is None

    def test_baggage_roundtrip(self) -> None:
        env = _envelope(baggage={"correlation.id": "tx-001"})
        assert env.baggage["correlation.id"] == "tx-001"
