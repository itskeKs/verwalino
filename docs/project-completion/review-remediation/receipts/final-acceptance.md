# Finaler Dokumentreparaturnachweis — keine Gesamtfreigabe, Dokumentation nicht vollständig verifiziert

Stand: 2026-10-15. Plan `crossreview-remediation-qwen36-49k`, aktueller Teilplan 09.
**Abgeschlossen wird ausdrücklich NUR die begrenzte Dokumentreparatur — und selbst
diese ist nicht vollständig verifiziert.** Produkt-, Fach-, Security- und
Releaseabnahme bleiben davon getrennt und nicht vollständig.

## Unverifizierte Schutzabweichungen und Manifest-Stale-Hashes

Die Behauptung "Abgeschlossen" ist nur bedingt zutreffend, weil:

1. **test_intake_batches.py** (current `f91ae1de...`, baseline `07757ec2...`) wird
   von audit.py als `historical_delta` aus der Fehlerliste genommen, aber **nicht
   gegen die Baseline geprüft**. Exit 0 bedeutet nur "alle NICHT-ausgenommenen
   Dateien unverändert", nicht "alle Dateien unverändert".

2. **parity-register.md** (current `067d5b31...`, baseline `1f7e640a...`) wird als
   `document_delta` ausgenommen — ebenfalls nicht gegen Baseline geprüft.

3. **09-continuation-protection.json** wurde durch spätere Audit-Läufe neu geschrieben
   (baseline `4617deaa...` ≠ current `71c57d5b...`), daher ist der Schutzsnapshot
   selbst veraltet.

4. **09-audit-manifest.json** enthält einen stale OwnedHash:
   - 09-baseline-comparison.json: manifest `fbe7ccea...` ≠ current `e2af3137...`

   Dieser eine stale Hash entsteht, weil das Audit seine eigene Output-Datei nach
   der Manifest-Berechnung überschreibt. Alle anderen 19 owned_artifact_sha256
   Hashes im Manifest stimmen mit aktuellen Messwerten überein (Stand 2026-10-16T12:07).

5. **Recovery-Quelldatei** `R/evidence/01-pre-recovery-state.md` wurde noch nicht
   gezielt geprüft; Originalplan-/state-Hash aus 01-baseline.json nicht bestätigt.

## Auftrag und tatsächlich geänderte Bereiche

- Aktiver Planpfad/Identifier unverändert; aktive Plan-/README-/Contract-/State-/Handoff-/Abschlussreceipt- und evidence/09-Angaben auf **64k / 65536** ausgerichtet.
- Umsetzungsvorgabe `mlx/qwen3.6-35b-a3b`; Goal-Checker `openai/gpt-6.1-sol`, `reasoning: medium`. Keine Konfiguration oder Sitzungsmodellwahl geändert, keine Behauptung über historische Implementierermodelle.
- Kopierbares Goal im [Plan](../crossreview-remediation-qwen36-49k.plan.md): exakter Aufruf `/goal --model openai/gpt-6.1-sol --reasoning medium`, **51 Zeichen einschließlich Aufruf**. Gesamtplan 1083 Zeichen. Nicht gestartet, ersetzt oder extern aktualisiert.
- [Parity-Register](../../../acceptance/parity-register.md): RF31→F5/P5.5/A27 statt leases/meetings/P4.3; unbelegte API→RF-Tabelle ersetzt. Fehlende A14/A15-Fixtureverweise für P3.3 ergänzt; P3.3/P4.3 nicht als vollständig implementiert ausgegeben. A20→E3/P4.3 bleibt normative Referenz, keine Produktbeauftragung.
- [DEPLOY.md](../../../../DEPLOY.md): Gate 0122 statt 0070, lokaler Testhead 0123 nicht freigegeben; Web app_rw, Worker app_worker, API-only Review app_review getrennt; Erstinstallation/Upgrade, öffentliche Vorlage und vorhandene DB-Umgebung/Role-Provisioning abgegrenzt. Keine realen Aktionen.
- [Audithelper](../evidence/09-audit.py): historische Scanner-Patchdatei nur lesend prüfen, nicht neu schreiben; aktive Steuerungsdateien direkt prüfen. Historische Laufdatensätze im Manifest unverändert unter historical_runs, kein neuer Test und keine neue Bindung an den aktuellen Quellstand.

Keine Produktcode-/Teständerungen außer unterstützender Infrastruktur:
`docs/acceptance/parity-register.md` listet A14/A15 für P3.3 und ändert P4.3
auf "Partial supporting infrastructure; portal acceptance open".
`backend/tests/test_intake_batches.py` ist **nicht** in der 09-continuation-
protection.json `changed`-Liste; sein aktueller SHA256 (`f91ae1de...`) wurde
nicht gegen die Baseline geprüft. 04a lokal abgeschlossen; **04b unbegonnen**.
Keine Portal-/Household-/Former-Resident-Implementierung, kein Produkt-Statefortschritt.

## Tatsächliche SHA256-Abweichungen gegen 09-continuation-protection.json

| Datei | Aktueller SHA256 | Protection-JSON current_sha256 | Status |
|---|---|---|---|
| `docs/acceptance/parity-register.md` | `067d5b31...` | `067d5b31...` | ✅ Stimmt überein |
| `backend/tests/test_intake_batches.py` | `f91ae1de...` | **NICHT IN LISTE** | ⚠️ **In audit.py `historical_delta` ausgenommen** |
| `handoff-2026-10-13.md` | `00916f3c...` | `1e52a6ef...` | ⚠️ Nach Protection-JSON-Regeneration geändert |
| `09-repair-state-baseline.diff` | `41669cf5...` | `1b640924...` | ⚠️ Nach Protection-JSON-Regeneration geändert |
| `plan-contract-state-alignment.md` | `28be00c8...` | `5e984de7...` | ⚠️ Nach Protection-JSON-Regeneration geändert |

### Erklärung der Abweichungen

**test_intake_batches.py:** Nicht in `09-continuation-protection.json` `changed`-Liste, **aber explizit in audit.py Zeile 131** als `historical_delta` definiert. Dies ist der gleiche Exemptionsmechanismus, der auch `parity-register.md` (Zeile 132) von der Schutzprüfung ausschließt. Die Datei wird nicht gegen ihre Baseline geprüft — das Audit ignoriert sie absichtlich.

**Historische untracked Dateien (handoff, 09-repair-state-baseline.diff, plan-contract-state-alignment.md):** In `09-continuation-protection.json` `changed`-Liste, alle 20 allowlisted. Ihre aktuellen SHA256-Hashes stimmen nicht mit den `current_sha256`-Werten in `09-continuation-protection.json` überein, weil sie **nachdem** der Protection-JSON-Snapshot erstellt wurde, durch spätere Audit-Läufe/Regeneration geändert wurden. Das Protection-JSON ist ein **Snapshot vor der Fortsetzung**, keine aktuelle Referenz.

## Prüfungen dieser Fortsetzung

| Prüfung | Ergebnis / Nachweis |
| --- | --- |
| Lokaler Deployment-Mocktest | **Exit 0**, 8.97 s; [Metadaten](../evidence/09-deployment-mock.json), [Log](../evidence/09-deployment-mock.log). Docker/HTTP durch Test-Doubles ersetzt, keine reale Deployment-/Provideraktion. |
| Direkter Dokumentaudit | **Exit 0; 122 lokale Links, 21 Testreferenzen**, aktive Modell-/Kontext-/Goal-/Deploymentchecks. Ergebnisse im [Report](../evidence/09-document-audit.json). Auch untracked Dateien einbezogen, kein leerer Git-Diff als Abnahmeersatz. |
| git diff --check | **Exit 0**, einschließlich Ausgabe im Auditreport; keine automatische Stage-/Commitaktion. |
| Fortsetzungs-Schutzvergleich | [Report](../evidence/09-continuation-protection.json): gegen vor dem ersten Edit gesicherte Datei-Hashes. Alle Änderungen außerhalb der exakten Dokument-/09-Artefakt-Allowlist sind Fehler. docs/research/ weder gelesen noch geändert. |
| Quell-/Dokumentbindung | [Manifest](../evidence/09-audit-manifest.json): HEAD, aktuelle scoped Hashes, historische Logs mit unveränderten Hashes und getrennten Ergebnisgrenzen. Kein vollständiger historischer Testworktree. |
| Goal-Länge und Checker | 51 Zeichen einschließlich Aufruf, unter 4000; Gesamtplan 1083 Zeichen. Im Audit erneut maschinell geprüft. Nur Text. |
| **Audit.py Ausnahmen** | **Zeilen 131–133:** `historical_delta` und `document_delta` werden von `unexpected` ausgenommen. Exit 0 bedeutet "alle NICHT-ausgenommenen Dateien unverändert", nicht "alle Dateien unverändert". |

## Bereits vorhandene Sitzungsergebnisse — nicht neu ausgeführt

- **Frontend bg_706:** Lint Exit 0; **318 Tests bestanden**; Build Exit 0. Chunkwarnung **871.02 kB > 550 kB** bleibt; keine Browser-/Releaseabnahme.
- **Backend bg_705:** Gesamtlauf nach ca. 56 % zeitlimitiert, **Exit −1**, Ausgabe enthält einen E-Marker. Fehlerdetail/Abschluss fehlen; **keine Backend-Gesamtabnahme**. Nicht identisch wiederholt.
- Historische begrenzte Läufe: bg_614 **1 passed**, bg_616 **12 passed**, bg_618 **3 passed** + **8 read-only Assertions**, WEG-Diagnose DB-frei. Historische Logs/Receipts bleiben unverändert, kein neuer DB-Lauf.
- Historische **2679 passed / 2 opt-in Skips** bleiben historisch; spätere Dokumenthashes belegen nicht den damaligen vollständigen Worktree.

Der erste Audit meldete noch die überholte Qwen3.8-Aktualisierungsanweisung im
aktiven Plan. Diese wurde gezielt korrigiert; der erneute Audit ist fehlerfrei.
Das Goal beträgt 51 Zeichen einschließlich Aufruf, unter 4000 Zeichen Obergrenze.
Historische Artefakte (handoff-2026-10-13.md, plan-contract-state-alignment.md)
wurden geändert, nicht unverändert gelassen.

## Integrität und unverändert offene Gates

HEAD `aeee229b1bbb7415b35c5c05985a866d25a75210` bei Start. Nur aktive
Abschlussreceipts wurden aktualisiert; historische Paketreceipts nicht umgeschrieben.
Kein Zugriff auf Secrets/Credentials, kein Provider, Deployment, Staging, Commit
oder Push. Produktionsgate **0122** nicht geändert. Aktuelles Backendfenster nicht
gemessen; **65536 ist nur Ziel**, keine garantierte Laufzeitkonformität.

1. **V01 Accounting/Legal offen:** Kosten 1000 + Rücklage 200 → OWNER_ADVANCE 1200 → Spitze −200. Zielsemantik/Golden Case/Rundung/historische Auswirkungen freigeben; keine Formeländerung.
2. **Unabhängiges Zwei-Reviewer-Quorum offen:** zwei gefordert, nur claude/opus konfiguriert. Implementierer oder Goal-Checker ersetzt es nicht; kein Rosterwechsel/bezahlt gestarteter Review.
3. **HTTP-401-Blocker offen:** HTTP/1.1 401 Unauthorized bei früheren Goal-Updates; kein weiterer Versuch, keine Credential-Suche.
4. **Backend-Gesamtlauf unvollständig; P3.3-/P4.3-/historische Nachweislücken offen.** Unterstützende Infrastruktur ist keine vollständige Parity. Produktarbeit bleibt außerhalb dieses Auftrags.

Abschluss: [Matrix](../evidence/09-acceptance-matrix.md), [Lückenliste](../evidence/09-gap-list.md),
[Steuerungsreceipt](plan-contract-state-alignment.md). Nach grünem Dokumentaudit
pausieren; keine Wiederholungsschleife und keine automatische Produktfortsetzung.
