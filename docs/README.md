# Dokumentation

Diese Dokumentation beschreibt das Muster **Temporal + Blob Storage +
OpenTelemetry** für verteilte Saga-Orchestrierung mit vollständiger
Audit- und Trace-Fähigkeit, sprachunabhängig.

Die Seiten folgen [Diataxis](https://diataxis.fr/): jede Seite gehört in
**genau eine** der vier Kategorien. Ausgangspunkt für alles Weitere ist
das Konzept als Rahmen.

## Rahmen

- **[Konzept](konzept.md)**: die normative Referenz. Grundidee, Envelope,
  Happy- und Unhappy-Path, Traceability-Regeln, Glossar, Quellen.

## Reference (zum Nachschlagen)

> *„Ich will etwas nachschlagen."*

Faktisch, strukturiert, vollständig. Keine Prosa, keine Tutorials.

- [`reference/envelope-felder.md`](reference/envelope-felder.md):
  alle Envelope-Felder, gruppiert nach Concern.
- [`reference/regeln.md`](reference/regeln.md):
  Invarianten nach Concern (Temporal, Blob, OTel), gegliedert in
  MUSS / SOLL / NICHT.
- [`reference/korrelationsattribute.md`](reference/korrelationsattribute.md):
  die sechs Business-Attribute und wo sie jeweils sichtbar sind
  (Baggage, Span, Log, Blob-Metadata).
- [`reference/fehlertaxonomie.md`](reference/fehlertaxonomie.md):
  retryable vs. non-retryable, Kompensations-Semantik.

## Guides (um eine Aufgabe zu erledigen)

> *„Ich will X umsetzen."*

Sprachunabhängig. Pseudocode und JSON-Wire-Format, bewusst wenig Code.
Jede Seite löst **eine** Aufgabe.

- `guides/temporal/`: Workflow starten, Activity implementieren,
  Kompensation verdrahten, Retry Policy wählen, Task Queue pro Service.
- `guides/blob/`: Payload schreiben, lesen und verifizieren,
  Blob-Metadaten stempeln.
- `guides/otel/`: Trace Context im Envelope, Baggage zu Span-Attributen,
  Workflow-Span-Attribute, Logs mit Traces korrelieren.

## Guides (Python, SDK-konkret)

> *„Ich will X in Python umsetzen."*

Spiegelt die Themen aus `guides/` thematisch, zeigt aber konkrete Muster
und Fallstricke des Python-Ökosystems.

- [`guides-python/envelope-als-pydantic-modell.md`](guides-python/envelope-als-pydantic-modell.md)
- [`guides-python/temporal-python-patterns.md`](guides-python/temporal-python-patterns.md)
- [`guides-python/blob-zugriff-in-python.md`](guides-python/blob-zugriff-in-python.md)
- [`guides-python/otel-python-instrumentierung.md`](guides-python/otel-python-instrumentierung.md)

## Explanation (um zu verstehen, warum)

> *„Ich will die Hintergründe verstehen."*

Bewusst schlank. Das Konzept trägt das „Warum"; hier stehen nur Themen,
die dort nicht tief genug ausgeführt sind.

- [`explanation/warum-envelope-statt-raw-payload.md`](explanation/warum-envelope-statt-raw-payload.md)
- [`explanation/warum-business-tx-id-nicht-in-metrik-labels.md`](explanation/warum-business-tx-id-nicht-in-metrik-labels.md)

---

**Orientierungshilfe**

| Du willst                         | Kategorie                    |
| --------------------------------- | ---------------------------- |
| etwas lernen, Schritt für Schritt | Tutorial _(nicht im Scope)_  |
| eine konkrete Aufgabe lösen       | Guides                       |
| ein Feld oder eine Regel nachsehen| Reference                    |
| Hintergründe verstehen            | Explanation                  |

Unsicher? Es ist vermutlich ein **Guide**.
