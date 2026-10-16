# Reparaturpaket 02 — Statusautorität und Modellvertrag klären

Quelle: `../crossreview-remediation-qwen36-49k.plan.md`, Abschnitt 2.
Dieser Teilplan ist aus dem gespeicherten Original abgeleitet, kein Produktauftrag.

## Auftrag, Einstieg und Abnahme
Gezielt lesen:
- `docs/project-completion/state.md`
- `docs/project-completion/contract.md`
- `docs/project-completion/README.md`
- historischer Pauseabschnitt in `docs/2026-10-01-property-management-implementation-plan.md:484`

Der Hauptplan beschreibt 0123 noch als pausierte, nicht akzeptierte Arbeit; der aktuelle State beschreibt 04a als lokal qualifiziert. Historischen Checkpoint ausdrücklich mit Datum und Link auf die spätere lokale Abnahme ablösen, nicht rückwirkend löschen. Die Formulierung „no product functionality implemented by this document“ allein ist kein Implementierungsfehler.

Kopfzeilen müssen eindeutig sagen: **04a lokal abgeschlossen; 04b nicht begonnen; Produktionsgate unverändert 0122; lokaler Testhead 0123**. `done-local` an den abgeschlossenen Schritt binden, nicht scheinbar an `current: 04b`. Leser-/Vertragsreferenzen entsprechend prüfen. Den bestehenden Qwen3.8-Hinweis auf den beauftragten Qwen3.6-/49k-Vertrag aktualisieren; keine weiteren Modell-/Providerpolicy-Änderungen.

**Abnahme:** Keine widersprüchliche aktuelle Wiederaufnahmeanweisung; historische Aussagen bleiben als historische Evidenz erhalten.

## Übergabe und Grenzen
- Nachfolger: `steps/03-intake-evidence.md`. Nur nach lokal erfüllter Abnahme weitergehen; fachlich blockierte Anteile separat festhalten und unabhängige Pakete fortführen.
- Receipt im Reparaturverzeichnis schreiben, State aktualisieren und erneut lesen; anschließend Kontextwechsel.
- Nur diesen Teilplan, Reparatur-State und Vertrag als Auftrag verwenden. Relative Produkt-Receipts/Steps im Originalabschnitt beziehen sich auf `docs/project-completion/`, nicht auf dieses Reparaturverzeichnis.
- Kein Produkt-04b-Auftrag, keine Secrets, Produktionsaktionen, Provideraufrufe, Commits oder Pushes. Keine Finanzsemantik ohne einschlägige Freigabe ändern.
