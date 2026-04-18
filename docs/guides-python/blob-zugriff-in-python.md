# Blob-Zugriff in Python

> **Aufgabe.** Wie der Showcase Blob-I/O in Python kapselt.

Volle Implementierung:
[`src/integration_showcase/shared/blob.py`](../../src/integration_showcase/shared/blob.py).

## Kernpunkte

- **Eine Schicht** kapselt alle Blob-Operationen; Services rufen die
  Helfer auf, nicht das Backend-SDK direkt.
- Drei Kernfunktionen:
  - `upload(store, blob_url, data, metadata)`: berechnet SHA-256,
    schreibt Bytes plus Metadata in einer Operation, liefert `BlobRef`.
  - `download(store, ref) -> bytes`: lädt und verifiziert `sha256`;
    Mismatch ist ein non-retryable `IntegrityError`.
  - Metadata-Stempel (Teil von `upload`) gemäß `Envelope.blob_metadata()`.

## Integrität

Die Hash-Prüfung ist Teil des Download-Pfads, nicht Aufgabe des
Aufrufers. Ein aufrufendes Activity-Modul ruft `download(...)` und kann
davon ausgehen, dass die Bytes integritätsgeprüft sind.

## Siehe auch

- [Reference: Regeln](../reference/regeln.md) (B-1 bis B-8)
- [Guide: Payload schreiben](../guides/blob/payload-schreiben.md)
- [Guide: Payload lesen und verifizieren](../guides/blob/payload-lesen-und-verifizieren.md)
- [Guide: Blob-Metadaten stempeln](../guides/blob/blob-metadaten-stempeln.md)
