# Reparaturpaket 08 — Doppeladoption und Scanner-Recovery untersuchen

Quelle: `../crossreview-remediation-qwen36-49k.plan.md`, Abschnitt 8.
Dieser Teilplan ist aus dem gespeicherten Original abgeleitet, kein Produktauftrag.

## Auftrag, Einstieg und Abnahme
Zwei getrennte kleine Unterpakete; nach jedem Kontextwechsel.

**8a Duplikate:** `0109_allocation_adoptions.py:137–200`, einschlägige spätere Patches/Replacementfunktionen und bestehende Adoptions-/Duplikattests. Betrag und Zeitraum sind Teil der Businessidentität; eine Doppelbelastung ist noch nicht end-to-end bestätigt. Synthetische Fälle gleicher Lieferant/Nummer bei geändertem Hash, Betrag/Zeitraum, normaler Adoption vs Replacement, sowie Org-/Scope-/Uploadergrenzen und Belegnummernvarianten prüfen. Keine pauschale aggressive Nummernnormalisierung oder Cross-Scope-Metadatenoffenlegung. Bei bestätigter Lücke: roter Regressionstest vor kleinstem autorisiertem Fix; bei fachlich unklarer Kandidaten-/Entscheidungsregel blockieren. Widerlegte Verdachtsfälle mit Nachweis schließen.

**8b Scanner:** vorhandene Inspection-/Job-/Recoverypfade gezielt lesen. Scanner-Stubs für unavailable/timeout → ausgeschöpfte Versuche → Scanner wieder verfügbar verwenden. Feststellen, ob es einen autorisierten Recoverypfad gibt. Keine Live-Scannerinstallation und kein clean-Bypass. Ein gegebenenfalls ergänzter Retry darf abgeschlossene Effekte nicht wiederholen und muss aktuelle Berechtigung, Fencing, Versuchsevidenz und Idempotenz erhalten. Unklare Retrypolicy als Freigabefrage abgrenzen.

**Abnahme:** Jeder Verdacht hat Test-/Codebeleg und Status: widerlegt, behoben oder konkret blockiert; keine nur vermutete Sicherheitslücke als bestätigt ausgeben.

## Übergabe und Grenzen
- Nachfolger: `steps/09-final-audit.md`. Nur nach lokal erfüllter Abnahme weitergehen; fachlich blockierte Anteile separat festhalten und unabhängige Pakete fortführen.
- Receipt im Reparaturverzeichnis schreiben, State aktualisieren und erneut lesen; anschließend Kontextwechsel.
- Nur diesen Teilplan, Reparatur-State und Vertrag als Auftrag verwenden. Relative Produkt-Receipts/Steps im Originalabschnitt beziehen sich auf `docs/project-completion/`, nicht auf dieses Reparaturverzeichnis.
- Kein Produkt-04b-Auftrag, keine Secrets, Produktionsaktionen, Provideraufrufe, Commits oder Pushes. Keine Finanzsemantik ohne einschlägige Freigabe ändern.
- Ausführung zwingend aufteilen: zuerst `steps/08a-duplicate-adoption.md`, danach Kontextwechsel und `steps/08b-scanner-recovery.md`. Der gemeinsame Abschnitt ist nur der Dispatcher.
