# Blob-Zugriff in Python

> **Aufgabe.** Wie der Showcase Blob-I/O in Python kapselt.

Volle Implementierung:
[`src/integration_showcase/shared/blob.py`](../../src/integration_showcase/shared/blob.py).

## Kernpunkte

- **Eine Schicht** kapselt alle Blob-Operationen; Services rufen die
  Helfer auf, nicht das Backend-SDK direkt.
- Drei Kernfunktionen:
  - `upload(data, path, *, metadata=None)`: berechnet SHA-256,
    schreibt Bytes und Metadata **atomar** in einem einzigen
    `store.write(metadata=...)` (remote-store ≥ 0.24.0), liefert `BlobRef`.
  - `download(store, ref): bytes`: lädt und verifiziert `sha256`;
    Mismatch wirft `ValueError` (ein dedizierter `IntegrityError` ist
    nicht ausgerollt). Aktivitäten müssen `ValueError` in
    `non_retryable_error_types` listen oder den Fehler in eine eigene
    Fachklasse heben.
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
