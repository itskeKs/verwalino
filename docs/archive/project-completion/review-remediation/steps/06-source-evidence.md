# Reparaturpaket 06 — Evidenz und Reproduzierbarkeit sichern

Quelle: `../crossreview-remediation-qwen36-49k.plan.md`, Abschnitt 6.
Dieser Teilplan ist aus dem gespeicherten Original abgeleitet, kein Produktauftrag.

## Auftrag, Einstieg und Abnahme
Einstieg: `receipts/04a-replacement-outcomes.md`, `state.md` und kleine Zusammenfassungen unter `evidence/04a/`.

Den vorhandenen Vollrun als historischen Nachweis erhalten: **2679 passed, 2 opt-in Skips, Exit 0**, passender Log-SHA-256. Nicht erneut als heutigen Lauf ausgeben. Der dokumentierte HEAD `aeee229…` enthält relevante uncommittete Änderungen nicht vollständig.

Für den **jetzt** geprüften owned Quellstand ein datiertes Manifest anlegen: HEAD, präzise Dateiliste, Inhalts-SHA-256, relevante untracked Helper, Base-relative Patch/Delta und exakte Prüfkommandos. Nur beauftragte, secretfreie Inhalte aufnehmen. Historische Testbindung nur behaupten, wenn sie tatsächlich rekonstruierbar ist; andernfalls ausdrücklich „historischer Lauf; damaliger Worktree nicht vollständig rekonstruierbar“. Nach weiteren Codeänderungen Snapshot und zugehörige neue Evidenz aktualisieren.

Kein automatischer Commit. Ein Hash des heutigen Diffs beweist nicht den damaligen Testzustand.

**Abnahme:** Historische Evidenz unverfälscht; aktuelle Nachweise besitzen eine eindeutige Quellenbindung; verbliebene historische Lücke ausdrücklich sichtbar.

## Übergabe und Grenzen
- Nachfolger: `steps/07-weg-reserves.md`. Nur nach lokal erfüllter Abnahme weitergehen; fachlich blockierte Anteile separat festhalten und unabhängige Pakete fortführen.
- Receipt im Reparaturverzeichnis schreiben, State aktualisieren und erneut lesen; anschließend Kontextwechsel.
- Nur diesen Teilplan, Reparatur-State und Vertrag als Auftrag verwenden. Relative Produkt-Receipts/Steps im Originalabschnitt beziehen sich auf `docs/project-completion/`, nicht auf dieses Reparaturverzeichnis.
- Kein Produkt-04b-Auftrag, keine Secrets, Produktionsaktionen, Provideraufrufe, Commits oder Pushes. Keine Finanzsemantik ohne einschlägige Freigabe ändern.
