# Produkt-State / 04b — begrenzter Baseline-Vergleich

Stand: 2026-10-14. Quelle: [ursprüngliche Workspace-Baseline](01-baseline.json), [konkreter Abgleich](09-baseline-comparison.json).

## Gesicherte Inhalte statt HEAD-Annahme

`existing_chain_sha256` bezieht sich auf **review-remediation**, nicht auf den Produktordner. Der Hash `8955fde…` stimmt mit [01-pre-recovery-state.md](01-pre-recovery-state.md) überein; deren Inhalt ist der alte Reparatur-State mit falschem 04b-Auftrag. Der [Inhaltsdiff](09-repair-state-baseline.diff) zeigt dessen Ablösung durch die Reparaturkette. Dieser Hash ist **keine Produkt-State-Baseline**.

| Gegenstand | Konkreter Befund |
|---|---|
| `review-remediation/04b-closure.md` | aktueller SHA-256 `186e8b76222031b3aec9e6798f42d4f340656b48e98217797340c632979990af`, identisch zur gespeicherten Baseline; aktuelle Bytes rekonstruieren den Altinhalt |
| `review-remediation/receipts/04b-assignment-mappings.md` | aktueller Hash identisch zur gespeicherten Baseline `2eeac183…`; Altinhalt unverändert |
| `review-remediation/operator-decision-required.md` | gespeicherter Baseline-Hash identisch; historischer falscher 04b-Blocker, nicht heutige Steuerung |
| Produkt-`state.md` | aktuelle Inhalte: current 04a, next 04b; lokale 04a-Abnahme, 04b unbegonnen, 0122 Produktionsgate/0123 lokaler Testhead, Verweis auf Reparaturkette und historische 91-Test-Angabe |
| Produkt-`steps/04b-assignment-mappings.md` | existiert; begrenzter B3-Auftrag und späterer 04c-Nachfolger; **kein Reparaturauftrag**, nicht ausgeführt |
| Produkt-`evidence/04b/04b-test-results.md` | existiert; historische 45+46=91 Testfälle und Doppeladoptions-/Extraction-Assertions, keine 04b-Produktabnahme |
| Produkt-`receipts/04b-assignment-mappings.md`, Produkt-`04b-closure.md` | aktuell nicht vorhanden; daraus folgt nicht, dass deren historische Inhalte bekannt wären |

Für die Produktpfade sind in `01-baseline.json` keine ursprünglichen Inhalte/Hashes gespeichert; sie fehlen auch im dort genannten HEAD `aeee229…`. Die begrenzte Suche im gespeicherten Workspace fand nur die **Reparatur**-State-Kopie. Ein ursprünglicher Produkt-Inhaltsdiff ist mit diesen Quellen **nicht rekonstruierbar**. Weder unveränderten Produkt-State noch Ownership sämtlicher früherer Änderungen behaupten. Andere historische Archive wurden nicht vollständig auditiert.

Die 54 `protected_worktree_sha256`-Einträge (52 tracked Dirty-Dateien plus zwei untracked Testhelfer) waren vor der eigenen Scanner-Testergänzung alle hashgleich. Jetzt sind **53/54 hashgleich**, einzig `backend/tests/test_intake_batches.py` hat den [separat gespeicherten eigenen Delta](08b-owned-test.delta.patch); [Originalbytes](08b-test-intake-batches.before.txt) entsprechen exakt der Baseline. Das ist ein konkreter Abgrenzungsbefund, kein Vollaudit fremder Änderungen.

## Korrigierte Abgrenzung

Produkt-State enthält tatsächlich einen Reparaturverweis. „Keine Cross-Referenzen“, „keine untracked Dateien geändert“ und „Produkt-State nachweislich unberührt“ waren falsch bzw. unbelegt. Untracked ist ein Git-Status, kein Änderungs-/Ownershipnachweis. Diese Fortsetzung verändert Reparaturdokumente/-diagnosen und ausschließlich die separat gespeicherte Scanner-Testassertion, keinen Produktions-/Harnesscode. Hashgleiche historische 04b-Artefakte bleiben erhalten, ihre alten Forderungen nach einem Reparatur-04b-Plan sind abgelöst. Maßgeblich: Reparatur-State, Vertrag und Teilplan 09. Kein neues 04b-/04c-Produktpaket und kein Vollaudit fremder Dirty-Dateien.
