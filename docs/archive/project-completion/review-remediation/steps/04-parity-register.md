# Reparaturpaket 04 — Parity-Register konsolidieren

Quelle: `../crossreview-remediation-qwen36-49k.plan.md`, Abschnitt 4.
Dieser Teilplan ist aus dem gespeicherten Original abgeleitet, kein Produktauftrag.

## Auftrag, Einstieg und Abnahme
Einstieg: `docs/acceptance/parity-register.md`, nur relevante Zeilen aus Hauptplan §6, Produktdesign und `docs/project-completion/traceability.md`.

Die API-Matrix bei `:104–123` widerspricht der behaupteten unveränderten RF-Zuordnung. Insbesondere `leases.py`/`meetings.py` mit RF31 und `financial_input_coverage.py` unter P3.3 prüfen. Normative P/A/RF-Zuordnung aus den Quellen ableiten; zusätzliche API-Hinweise ausdrücklich nicht-normativ kennzeichnen oder entfernen, wenn sie keine belegte Funktion haben. Keine eindeutige 1:1-Zuordnung erzwingen, wenn die Quelle mehrere zulässt. P4.3-Bezeichnung bei `:26` vervollständigen und fehlende Fixtureverweise prüfen.

**Abnahme:** Normative Zuordnungen stimmen überein; Beobachtungstiefe und offene Freigaben bleiben erhalten; lokale API-/Testexistenz wird nicht als vollständige Parity ausgegeben.

## Übergabe und Grenzen
- Nachfolger: `steps/05-deployment-runbook.md`. Nur nach lokal erfüllter Abnahme weitergehen; fachlich blockierte Anteile separat festhalten und unabhängige Pakete fortführen.
- Receipt im Reparaturverzeichnis schreiben, State aktualisieren und erneut lesen; anschließend Kontextwechsel.
- Nur diesen Teilplan, Reparatur-State und Vertrag als Auftrag verwenden. Relative Produkt-Receipts/Steps im Originalabschnitt beziehen sich auf `docs/project-completion/`, nicht auf dieses Reparaturverzeichnis.
- Kein Produkt-04b-Auftrag, keine Secrets, Produktionsaktionen, Provideraufrufe, Commits oder Pushes. Keine Finanzsemantik ohne einschlägige Freigabe ändern.
