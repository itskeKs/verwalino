# Reparaturpaket 03 — 03a-Receipt und B2-Restpunkte reparieren

Quelle: `../crossreview-remediation-qwen36-49k.plan.md`, Abschnitt 3.
Dieser Teilplan ist aus dem gespeicherten Original abgeleitet, kein Produktauftrag.

## Auftrag, Einstieg und Abnahme
Einstieg: `receipts/03a-intake-recovery.md`, `03b-intake-jobs.md`, `03c-isolated-qualification.md`, die beiden 03c-Teilpläne und `backend/tests/test_intake_batches.py`.

17 Testnamen werden im 03a-Receipt genannt; 14 fehlen in der angegebenen Datei. Per AST/Pytest-Collection tatsächliche Node-IDs ermitteln und vorhandene Logs auf Ergebnisse/Datum/Umgebung prüfen. Nicht fehlende Namen durch erfundene Tests ersetzen. Historische, nicht belegbare Behauptungen als nicht verifiziert kennzeichnen; ein neuer Lauf erhält einen neuen datierten Nachweis.

Restpunktmatrix für Jobs/Outbox, Worker, Inbox-/Batch-UI, Registry-Adapter/Backfill und Browserjourney erstellen: `lokal belegt`, `offen`, `release-blockiert` oder `durch späteren Nachweis abgelöst`, jeweils mit Quelle/Nachfolger. Vorhandene Implementierungen nur gezielt suchen, nicht erneut bauen. Den Übergang von `03c-intake-runtime` zu `03c-isolated-qualification` samt begrenztem Umfang explizit verknüpfen. Reale Scanner-/Corpus-/Capacityfreigaben bleiben offen.

**Abnahme:** Testnamen auflösbar; Ergebniszahlen durch Evidenz gedeckt oder ehrlich eingeschränkt; jeder B2-Restpunkt hat Status und Zuständigkeit.

## Übergabe und Grenzen
- Nachfolger: `steps/04-parity-register.md`. Nur nach lokal erfüllter Abnahme weitergehen; fachlich blockierte Anteile separat festhalten und unabhängige Pakete fortführen.
- Receipt im Reparaturverzeichnis schreiben, State aktualisieren und erneut lesen; anschließend Kontextwechsel.
- Nur diesen Teilplan, Reparatur-State und Vertrag als Auftrag verwenden. Relative Produkt-Receipts/Steps im Originalabschnitt beziehen sich auf `docs/project-completion/`, nicht auf dieses Reparaturverzeichnis.
- Kein Produkt-04b-Auftrag, keine Secrets, Produktionsaktionen, Provideraufrufe, Commits oder Pushes. Keine Finanzsemantik ohne einschlägige Freigabe ändern.
