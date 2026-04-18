# Warum Envelope statt raw payload

Services tauschen nur einen Envelope plus Blob-Referenz aus, nie den
Payload selbst. Diese Trennung ist **keine Optimierung**, sondern eine
harte Architekturvorgabe. Sie ergibt sich aus vier Gründen.

## 1. Security und Datenschutz (der Hauptgrund)

Ein Temporal-Cluster ist ein **Orchestrator**, kein Datenspeicher mit
fachlichen Schutzklassen. Was in der Event History liegt, ist:

- **langlebig** (Retention in Monaten bis Jahren, für Audit und
  Debugging),
- **breit einsehbar** (Web UI, Support, Entwickler:innen, SRE),
- **außerhalb der Fach-Systeme gespiegelt** (Backups,
  Workflow-Replay, Child-Workflows).

Personenbezogene, finanzielle oder regulierte Daten in diesen Sichtbarkeits-
und Langlebigkeitsraum zu heben, konfliktet mit:

- **DSGVO-Löschansprüchen (Recht auf Vergessen).** Temporal-Events sind
  technisch append-only; das Löschen einzelner Payloads aus einer Event
  History ist kein vorgesehener Pfad. Der Blob hingegen hat eine klare
  Lifecycle Policy, kann gezielt gelöscht oder redigiert werden und
  lebt im Fachsystem mit den richtigen Berechtigungen.
- **Data Residency und Sovereignty.** Blob Storage lässt sich pro
  Container an Regionen binden; Temporal-Clustering (Multi-Region,
  Cross-Region-Replication) folgt anderen Regeln.
- **Minimum-Privilege-Zugriff.** Operator:innen brauchen in der Regel
  Zugriff auf Workflow-Metadaten (Start, Fortschritt, Fehler), nicht
  auf den Inhalt jeder Transaktion. Payload im Blob erlaubt die
  Trennung: History lesbar, Payload nicht.

Kurz: der **Envelope ist Metadaten-Terrain**, der **Blob ist Daten-Terrain**.
Die beiden dürfen keine Sichtbarkeits- und Löschregel miteinander
teilen.

## 2. Cloud Governance

Organisationen haben Lifecycle-, Audit- und Retention-Regeln an
**Speicher**, nicht an Orchestratoren:

- Immutable-Storage-Policies (WORM) für regulierte Domänen.
- Lifecycle Policies für Aging und Tiering.
- Zentrale Audit-Trails auf Storage-Ebene (wer hat wann welches Blob
  gelesen).
- DLP-Scanner, Virenscanner, Content-Klassifikation, die an
  Blob-Pipelines andocken.

Wenn Payloads in Temporal landen, umgehen sie all diese Kontrollen.
Der Blob zwingt sie hinein, automatisch.

## 3. Auditierbarkeit und Zuordenbarkeit

Ein persistierter Seiteneffekt soll **genau einer** Blob-Referenz und
**genau einem** Workflow-Schritt zuordenbar sein. Mit raw payload im
Activity-Argument vermischt sich der Payload mit Retry-Attempts,
Workflow-Forks und ContinueAsNew-Grenzen; die fachliche Identität des
Payload-Objekts verschwimmt.

Der Envelope mit `payload_ref.sha256` schafft stattdessen eine stabile,
inhaltsadressierte Identität: derselbe Payload hat denselben Hash, egal
wie oft Temporal retryt. Das macht Audit-Abfragen („welches Blob wurde
zu welchem Zeitpunkt vom welchem Service gelesen") trivial.

## 4. Als Nebeneffekt: technische Effizienz

Erst zuletzt, und bewusst in dieser Reihenfolge:

- Temporal-History bleibt klein. Große Payloads würden History-Latenz
  und Replay-Kosten in die Höhe treiben.
- Workflow-Events bleiben replay-freundlich: der Inhalt des Payloads
  ändert das Ergebnis des Workflows nicht, solange `sha256` gleich
  bleibt.
- Worker-RAM bleibt beherrschbar; Streaming-Uploads sind auf
  Blob-Seite ein natürliches Konzept, in einer Event History nicht.

Dieser Vorteil ist real, aber **nicht** der Grund für die Regel. Die
Regel hält auch bei kleinen Payloads, wo der Performance-Vorteil
vernachlässigbar wäre.

## Konsequenzen in der Praxis

- **Raw payloads sind nie Workflow-Input.** Nie Activity-Argument, nie
  Activity-Result, nie Span-Attribut, nie Log-Feld.
- **Raw payloads sind nie Ergebnis einer Activity.** Ergebnisse sind
  neue `BlobRef`s.
- **Fehlerbehandlung zeigt nie den Inhalt.** Ein `InsufficientFundsError`
  trägt „balance below threshold", nicht die konkrete Summe.

Wer diese Regel aufweicht, entfernt die Grundlage, auf der die
Datenschutz- und Governance-Zusagen des Systems stehen.

## Siehe auch

- [Konzept §3](../konzept.md#3-kanonischer-envelope) (Regel 1)
- [Reference: Regeln](../reference/regeln.md) (T-8, T-9, B-8)
