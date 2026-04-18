# Blob-Zugriff in Python

> **Aufgabe.** Wie der Showcase Blob-I/O in Python kapselt.

Volle Implementierung:
[`src/integration_showcase/shared/blob.py`](../../src/integration_showcase/shared/blob.py).

## Kernpunkte

- **Eine Schicht** kapselt alle Blob-Operationen; Services rufen die
  Helfer auf, nicht das Backend-SDK direkt.
- Drei Kernfunktionen:
  - `upload(store, blob_url, data, metadata)`: berechnet SHA-256,
    schreibt Bytes und Metadata in **zwei** Schritten (`store.write()`
    gefolgt von einem separaten Azure-SDK-`set_blob_metadata`), liefert
    `BlobRef`. Siehe BK-005 unten.
  - `download(store, ref): bytes`: lädt und verifiziert `sha256`;
    Mismatch wirft `ValueError` (ein dedizierter `IntegrityError` ist
    nicht ausgerollt). Aktivitäten müssen `ValueError` in
    `non_retryable_error_types` listen oder den Fehler in eine eigene
    Fachklasse heben.
  - Metadata-Stempel (Teil von `upload`) gemäß `Envelope.blob_metadata()`.

## BK-005: zweistufiger Write

remote-store v0.23.0 bietet keinen Metadata-Kanal auf `Store.write()`.
Bis upstream eine native API liefert, setzt `_set_azure_blob_metadata`
die Blob-Metadaten mit dem Azure SDK direkt, **nach** dem
`store.write`. Das ist eine bewusste, dokumentierte Abweichung von der
sprachagnostischen Guideline (siehe
[`guides/blob/blob-metadaten-stempeln.md`](../guides/blob/blob-metadaten-stempeln.md),
Abschnitt „Schreibpfad").

## Integrität

Die Hash-Prüfung ist Teil des Download-Pfads, nicht Aufgabe des
Aufrufers. Ein aufrufendes Activity-Modul ruft `download(...)` und kann
davon ausgehen, dass die Bytes integritätsgeprüft sind.

## Siehe auch

- [Reference: Regeln](../reference/regeln.md) (B-1 bis B-8)
- [Guide: Payload schreiben](../guides/blob/payload-schreiben.md)
- [Guide: Payload lesen und verifizieren](../guides/blob/payload-lesen-und-verifizieren.md)
- [Guide: Blob-Metadaten stempeln](../guides/blob/blob-metadaten-stempeln.md)
