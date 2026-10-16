# Reparaturunterpaket 08b — Scanner-Recovery

Quelle: `../crossreview-remediation-qwen36-49k.plan.md`, Abschnitt 8.

## Auftrag und Einstieg
vorhandene Inspection-/Job-/Recoverypfade gezielt lesen. Scanner-Stubs für unavailable/timeout → ausgeschöpfte Versuche → Scanner wieder verfügbar verwenden. Feststellen, ob es einen autorisierten Recoverypfad gibt. Keine Live-Scannerinstallation und kein clean-Bypass. Ein gegebenenfalls ergänzter Retry darf abgeschlossene Effekte nicht wiederholen und muss aktuelle Berechtigung, Fencing, Versuchsevidenz und Idempotenz erhalten. Unklare Retrypolicy als Freigabefrage abgrenzen.

## Abnahme
Verdacht mit Test-/Codebeleg als widerlegt, behoben oder konkret blockiert dokumentieren. Fehlende Matrixfälle nicht durch pauschal grüne Testzahlen ersetzen.

## Grenzen und Übergabe
Keine Secrets, Live-Scanner, Provider-, Produktionsaktionen, Cross-Scope-Offenlegung oder Finanzänderungen ohne Freigabe. Kein Produkt-04b, kein Commit/Push.
Receipt schreiben; State aktualisieren und erneut lesen; Kontextwechsel.
Nachfolger: `steps/09-final-audit.md`; unabhängiges Unterpaket trotz konkret festgehaltenem Blocker fortführen.
