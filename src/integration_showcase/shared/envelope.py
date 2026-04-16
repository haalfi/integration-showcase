"""Canonical inter-service envelope (Claim-Check pattern).

Every hop between services carries this envelope -- never the raw payload.
The payload lives in Blob Storage; the envelope carries only a reference to it.
"""

from __future__ import annotations

from pydantic import BaseModel, field_validator


class BlobRef(BaseModel):
    """Immutable reference to a blob in the payload store.

    ``blob_url`` is a remote-store compatible path (e.g. ``workflows/tx-001/input.json``).
    ``sha256`` is computed by the uploader before upload and verified by the downloader
    after download. ``etag`` and ``version_id`` are reserved for future use; the
    remote-store ``Store.write`` API does not return write metadata, so both fields
    default to ``""`` and are not populated by :func:`~shared.blob.upload`.
    """

    blob_url: str
    sha256: str
    etag: str = ""
    version_id: str = ""


class Envelope(BaseModel):
    """Canonical message envelope for distributed saga steps.

    Encodes workflow identity, step position, payload reference, and
    OpenTelemetry propagation context. Services exchange only this envelope;
    each service fetches the payload from Blob Storage itself.
    """

    workflow_id: str
    run_id: str
    business_tx_id: str
    parent_step_id: str | None = None
    step_id: str
    payload_ref: BlobRef
    traceparent: str
    tracestate: str = ""
    baggage: dict[str, str] = {}
    schema_version: str = "1.0"
    content_type: str = "application/json"
    idempotency_key: str

    @field_validator("idempotency_key")
    @classmethod
    def _idempotency_key_not_empty(cls, v: str) -> str:
        if not v:
            raise ValueError("idempotency_key must not be empty")
        return v

    @staticmethod
    def make_idempotency_key(
        business_tx_id: str,
        step_id: str,
        schema_version: str = "1.0",
    ) -> str:
        """Canonical format: ``{business_tx_id}:{step_id}:{schema_version}``."""
        return f"{business_tx_id}:{step_id}:{schema_version}"

    def advance(self, next_step_id: str, new_payload_ref: BlobRef) -> Envelope:
        """Return a new envelope advanced to the next saga step.

        The new envelope inherits all correlation IDs and promotes the current
        ``step_id`` to ``parent_step_id``.
        """
        return self.model_copy(
            update={
                "parent_step_id": self.step_id,
                "step_id": next_step_id,
                "payload_ref": new_payload_ref,
                "idempotency_key": Envelope.make_idempotency_key(
                    self.business_tx_id, next_step_id, self.schema_version
                ),
            }
        )
