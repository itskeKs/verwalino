# Reparaturpaket 07 — WEG-Rücklagenfehler qualifizieren; Fachgate respektieren

Quelle: `../crossreview-remediation-qwen36-49k.plan.md`, Abschnitt 7.
Dieser Teilplan ist aus dem gespeicherten Original abgeleitet, kein Produktauftrag.

## Auftrag, Einstieg und Abnahme
Einstieg:
- `backend/app/engine/wirtschaftsplan.py:111–113`
- `backend/app/services/weg_settlement_service.py:478–500`
- `backend/app/engine/weg.py:410,492`
- relevante Engine-Typen, WEG-/Plan-Tests und ADR-0007

Bereits reproduziert: ein Eigentümer, 1.000 € Plankosten, 200 € Rücklage, 1.000 € Ist-Kosten → `OWNER_ADVANCE=1.200`, Spitze `−200`. Der Plan enthält Rücklage; die Kostenbasis schließt sie aus; der Service zieht den Gesamtvorschuss ab.

Zunächst synthetischen Diagnosefall ohne DB/Provider dauerhaft dokumentieren. Prüfen, ob eine **bereits dokumentierte, einschlägige V01-Freigabe** die Zielsemantik festlegt. Fehlt sie: Entscheidungsvorlage mit Kosten-/Rücklagenanteil, Golden Case und historischen Auswirkungen erstellen; Paket `blocked-fachfreigabe`. Keine fachliche Formel eigenmächtig ändern und kein absichtlich roter Test in der normalen Suite hinterlassen. Unabhängige Dokumentpakete weiterführen.

Nach ausdrücklicher Freigabe: gewünschte Invariante als zunächst roten Regressionstest formulieren, kleinsten konsistenten Engine-/Service-/Snapshot-Fix implementieren. Fälle: Null-/positive Rücklage, mehrere Einheiten/Eigentümer, Rundung, nachrichtliche Zahlungen ohne Spitzeneffekt, historische Final-Snapshots unverändert. Kein pauschaler Add-back ohne saubere Herkunft/Trennung. Schemaänderung nur als additive Migration nach tatsächlichem Head; akzeptierte Migrationen und Finalhashes nicht verändern.

**Abnahme:** Entweder freigegebene Semantik samt grünen relevanten Tests oder reproduzierter Befund plus konkrete ausstehende Fachentscheidung. Letzteres ist kein erledigter Fix.

## Übergabe und Grenzen
- Nachfolger: `steps/08-duplicates-scanner.md`. Nur nach lokal erfüllter Abnahme weitergehen; fachlich blockierte Anteile separat festhalten und unabhängige Pakete fortführen.
- Receipt im Reparaturverzeichnis schreiben, State aktualisieren und erneut lesen; anschließend Kontextwechsel.
- Nur diesen Teilplan, Reparatur-State und Vertrag als Auftrag verwenden. Relative Produkt-Receipts/Steps im Originalabschnitt beziehen sich auf `docs/project-completion/`, nicht auf dieses Reparaturverzeichnis.
- Kein Produkt-04b-Auftrag, keine Secrets, Produktionsaktionen, Provideraufrufe, Commits oder Pushes. Keine Finanzsemantik ohne einschlägige Freigabe ändern.
