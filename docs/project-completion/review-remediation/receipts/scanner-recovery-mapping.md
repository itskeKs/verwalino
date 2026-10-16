# Scanner-Recovery — Sequenzen und historische Auswahl getrennt

Stand: 2026-10-14. Maßgeblich [08b-Receipt](08b-scanner-recovery.md) und [Assertion-Matrix](../evidence/08b-assertion-mapping.md).

1. **Endgültige Budgetgrenze:** unavailable dreimal→blocked→Retry409; frischer Retry mit verfügbarem Clean-Stub ebenfalls409, kein vierter Versuch/keine Effekte. Neu ergänzt und grün geprüft.
2. **Recovery innerhalb des Budgets:** timeout→Retry202→clean; Replay identisches Receipt, kein weiterer Claim. Vorhandener Test grün geprüft.
3. **Eventintegrität:** unavailable→Retry202→clean vor Ausschöpfung; altes Event Dokument0, neues Event Dokument1. Vorhandener Test grün geprüft.
4. **Negative Grenzen:** Rechteentzug Retry404, staleFenceFalse, Crashrollback pending1/Dokument0 danach pending0/Dokument1. Vorhandene Tests grün geprüft.

[Aktueller Lauf](../evidence/08b-regression-20261014.log): `bg_616`, **12 passed in10.20s, Exit0**. Auswahl: fünf parametrisierte Scannerergebnisse, vier Batch-Einzeltests, drei Outbox-Einzeltests. Das ist kein53-/71-Gesamtlauf.

## Historische Filterkorrektur

Das frühere Kommando mit `-k "test_scanner_outcomes_precede_parsing or test_clamav_failure_and_limits_are_fail_closed or test_isolated_inspection_has_explicit_missing_scanner_outcome"` wählt fünf Scannerparameter +zwei Einzeltests =**7** Fälle. Die überlieferte Angabe „7 passed,20 deselected“ passt zu27 vorhandenen Fällen und wird **nicht** zu27passed umgeschrieben. Für diesen historischen Lauf ist hier kein Rohoutput verfügbar; Laufzeit7.56s bleibt historische Dokumentangabe. Separat behauptete volle27/7.74s sind ohne Rohlog nicht bewiesen.

[AST](../evidence/09-test-nodes.json): Inspection9/27, Batches15/17, Outbox9/9. Malformed11, nicht12. Testfunktionen, Parameterfälle und vollständige Sequenzen nicht gleichsetzen.

## Scope

Der Originalplan verlangt keine erzwungene erfolgreiche Recovery nach endgültigem409. Aktuelle Policy lässt dann keinen neuen Retry zu; ein weiterer autorisierter Pfad wäre eine konkrete zu entscheidende Policyänderung, nicht durch diesen Dokumentauftrag freigegeben. WEG-Reserve-Golden-Test ohne Planherkunft widerlegt den gesonderten WEG-Planfall nicht. Kein Produkt04b oder Zusatzteilplan, keine Live-Scanner-/Provideraktion.
