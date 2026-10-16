# Testverifikation — tatsächliche Läufe statt Summenclaims

Stand: 2026-10-14. Frühere pauschale Angaben „71/71 in85.59s“ und „53/53 in51.37s“ sind aus den hier gesicherten Rohlogs **nicht** rekonstruierbar; nicht als aktueller Gesamtlauf verwenden.

| Nachweis | Status | Quelle |
|---|---|---|
| `bg_426` Doppeladoption17/29.27s/Exit0 | echter historischer Taskoutput; getrennt archivierte Ergebnistranskription, damaliger gesamter Worktree nicht vollständig gebunden | [Output](08a-bg426-results.log) |
| `bg_614` unveränderte Scanner-Budgetregression1/7.39s/Exit0 | neuer belegter Ausgangslauf | [Log](08b-baseline-20261014.log) |
| `bg_616` Scanner/Recovery/Rechte/Fencing/Rollback12/10.20s/Exit0 | neuer belegter Lauf nach minimaler Testergänzung | [Log](08b-regression-20261014.log), [Assertion-Matrix](08b-assertion-mapping.md) |
| DB-freier WEG-Diagnosefall | Kosten1000+Rücklage200→Soll1200→Spitze−200, Exit0; keine V01-Freigabe | [Log](07-reserve-diagnostic.log) |

## Historische, nicht erneut verifizierte Dokumentclaims

Inspection27/7.74s, Batches17/19.62s, Outbox9/24.01s, Adoption17/31.00s und WEG1/3.22s wurden früher als volle aktuelle Belege bezeichnet. Die vorhandenen Receipts enthalten diese Angaben; Rohoutputs/Quellenbindung wurden für diese behaupteten Läufe nicht gefunden. Sie werden weder gelöscht noch rückwirkend durch neue Läufe bestätigt. Die 31.00s werden nicht mit dem echten bg_426-Ergebnis29.27s vermischt. WEG-Einzellaufzeiten3.22/6.06/6.32s bleiben ungeklärte historische Claims.

Die gefilterte Auswahl mit fünf Scannerparametern und zwei Einzeltests enthält7 Fälle; „7passed,20deselected“ darf nicht als27passed wiedergegeben werden.

## AST-Bestand ist keine Testausführung

[09-test-nodes.json](09-test-nodes.json): Adoption12 Funktionen/17 Fälle; Inspection9/27; Batches15/17; Outbox9/9. Inspection27 =5Scanner+3Basic+3PDF+5Einzeltests+**11malformed**, nicht12. Frühere Anzahlformeln mit12 waren falsch. Neue Läufe werden separat angegeben, nicht zu71 addiert.

Die gezielten aktuellen DB-Läufe nutzten die erneut bestätigte localhost/tmpfs-Disposable-DB, vorhandene isolierte Umgebung und entfernten USER/HV_OPENAI_API_KEY. [Skip-/Suppressionqualifikation](04-skip-suppression-befund.md). Keine Garantie über den ganzen Testbestand aus diesen Teilnachweisen.
