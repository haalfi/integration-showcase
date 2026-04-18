# Envelope als Pydantic-Modell (Python)

> **Aufgabe.** Den kanonischen Envelope in Python als
> Pydantic-BaseModel abbilden, inkl. Validierung und Helfer.

Volle Implementierung im Showcase:
[`src/integration_showcase/shared/envelope.py`](../../src/integration_showcase/shared/envelope.py).

## Kernpunkte

- Zwei BaseModels: `BlobRef` (blob_url, sha256, etag) und `Envelope`
  (Identity, payload_ref, trace fields, metadata).
- `field_validator` erzwingt nicht-leeren `idempotency_key`.
- Zwei Helfer:
  - `Envelope.make_idempotency_key(business_tx_id, step_id, schema_version)`.
  - `Envelope.blob_metadata()` liefert das Metadata-Dict für den
    Blob-Upload.
  - `Envelope.advance(next_step_id, new_payload_ref)` erzeugt den
    Envelope für den Folgeschritt; Identity-Felder bleiben, `step_id`
    und `idempotency_key` werden neu gesetzt.

## Typischer Einsatz

```python
env_out = env_in.advance(
    next_step_id   = "charge-payment",
    new_payload_ref = BlobRef(blob_url=..., sha256=..., etag=...),
)
```

## Siehe auch

- [Reference: Envelope-Felder](../reference/envelope-felder.md)
- [Guide: Workflow starten](../guides/temporal/workflow-mit-envelope-starten.md)
- Quelle: [`shared/envelope.py`](../../src/integration_showcase/shared/envelope.py)
