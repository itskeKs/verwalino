# Reparaturpaket 01 — Separaten Auftrag und Ausgangszustand sichern

Quelle: `../crossreview-remediation-qwen36-49k.plan.md`, Abschnitt 1.
Dieser Teilplan ist aus dem gespeicherten Original abgeleitet, kein Produktauftrag.

## Auftrag, Einstieg und Abnahme
In Agent-Modus `pwd`, `git status --short`, HEAD, verfügbare Toolchain und laufende Tasks prüfen. Keine fremden Änderungen zurücksetzen, überschreiben oder automatisch stagen. Vorbestehende dirty/untracked Dateien bleiben vorbestehend; Änderungen dieses Auftrags separat erfassen.

Unter `docs/project-completion/review-remediation/` einen kurzen `README.md`, `contract.md`, `state.md`, neun kleine `steps/01-…` bis `09-…`, `receipts/` und bereinigte `evidence/` anlegen. Dieser Plan ist deren Quelle; jedes Teilpaket enthält nur seinen Auftrag, Einstiegspfade, Checks, Stop-Grenzen und Nachfolger. Keine Kopie der gesamten Produkthistorie.

Statefelder: `current`, `current_status`, `last_completed`, `last_receipt`, `open_blockers`, `next_action`, aktuelle owned Dirtypaths und laufende Task-IDs. Einen fachlich blockierten Punkt getrennt festhalten und ausdrücklich unabhängige Pakete weiterbearbeiten. Der Parent-State wird nicht auf spätere Produktpakete weitergesetzt; dort höchstens einen klar begrenzten Verweis auf den Reparaturauftrag ergänzen.

**Abnahme:** Ausgangszustand und Eigentumsgrenzen gespeichert; frische Sitzung kann allein mit Reparatur-State/Vertrag/aktuellem Teilplan fortsetzen.

## Übergabe und Grenzen
- Nachfolger: `steps/02-status-contract.md`. Nur nach lokal erfüllter Abnahme weitergehen; fachlich blockierte Anteile separat festhalten und unabhängige Pakete fortführen.
- Receipt im Reparaturverzeichnis schreiben, State aktualisieren und erneut lesen; anschließend Kontextwechsel.
- Nur diesen Teilplan, Reparatur-State und Vertrag als Auftrag verwenden. Relative Produkt-Receipts/Steps im Originalabschnitt beziehen sich auf `docs/project-completion/`, nicht auf dieses Reparaturverzeichnis.
- Kein Produkt-04b-Auftrag, keine Secrets, Produktionsaktionen, Provideraufrufe, Commits oder Pushes. Keine Finanzsemantik ohne einschlägige Freigabe ändern.
