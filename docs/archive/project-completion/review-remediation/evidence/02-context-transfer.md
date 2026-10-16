# Kontextübergabe — crossreview-remediation-qwen36-49k (Update 2026-10-13)

**Erstellt:** 2026-10-13
**Aktualisiert:** 2026-10-13 (tatsächliche Testläufe)
**Geprüft durch:** Coddy (Qwen 3.6 49k)
**Plan:** `crossreview-remediation-qwen36-49k.plan.md`
**State:** `docs/project-completion/review-remediation/state.md` (current: `blocked`)
**Receipt:** `docs/project-completion/review-remediation/receipts/final-acceptance.md` (konsolidiert: BLOCKIERT)

---

## 1. Aktueller State

```
plan: crossreview-remediation-qwen36-49k
current: blocked
current_status: blocked
reason: steps/09-final-acceptance.md nicht vorhanden; alle Fachgates (V01–V09, Zwei-Reviewer-Quorum) offen
last_completed: steps/08b-scanner-recovery.md
last_receipt: receipts/08b-scanner-recovery.md (aktualisiert mit tatsächlichen Testergebnissen)
updated: 2026-10-13
```

**State ist korrekt `blocked`.** `steps/09-final-acceptance.md` existiert nicht. `receipts/final-acceptance.md` ist auf "BLOCKIERT" korrigiert.

---

## 2. Ownership gegen Baseline

**HEAD:** `aeee229b1bbb7415b35c5c05985a866d25a75210`

**Alle 52 geänderten Dateien sind fremde Vorhandene** (bereits bei Baseline vorhanden). Meine Reparaturketten-Dateien sind **neue ungetrackte Dateien** und tauchen im Diff nicht auf.

**Keine fremden Dateien berührt.** Ownership gesichert.

---

## 3. Reparaturpakete — tatsächliche Testergebnisse (2026-10-13)

### Paket 07 — WEG-Rücklagen

**Test:** `test_golden_weg.py::TestGolden3ThreeSpheres::test_reserve_not_in_owner_cost_total_or_spitze`

**Ergebnis:** 1/1 bestanden (6.06s)

**Befund:** Behaupteter Fehler (Rücklagenbetrag fließt fälschlich in Abrechnungsspitze) existiert nicht im Code. Golden-Test beweist korrekte Trennung. ADR-0007 konform implementiert.

**Dokumentierte Werte:**
- `umlagefaehig = 1000`, `nicht_umlagefaehig = 500`
- `reserve_contribution = 2000`, `plan_advance_total = 1400`
- `owner_cost_total = 1500.00`, `abrechnungsspitze = 100.00`
- Rücklage separat als `RESERVE_CONTRIBUTION`

**V01:** Fachgate offen (Steuerberater/Fachanwalt erforderlich). Keine Finanzcodedateien geändert.

---

### Paket 08a — Doppeladoption

**Test:** `backend/tests/test_allocation_adoptions.py`

**Ergebnis:** 17/17 bestanden (29.69s)

**Umfassende Prüfung:**
- Normale und Credit-Note-Adoption mit exakten Quellen
- Retention-Grenzen und Bestätigungen
- Integritätsfehler + historische Retry-Evidenz
- Atomare Rollbacks (Kosten, Dokument, Audit)
- Scope-/Organisation-/Uploader-Grenzen
- Aktuelle Proposal-/Treatment-Grundlagen
- Manuelle Legacy-Duplikate (beide Reihenfolgen)
- Gleiche Bytes + ungleiche Dokumente (beide Richtungen)
- Neue unverarbeitete gleiche-Byte-Quellen
- Runtime-Rollen + transaktionsgebundene Integrity-Receipts
- Retention der Evidenz + Downgrade-/Migration-Recovery

**Befund:** Sperrlogik korrekt implementiert. `adoption_invoice_identity` (Betrag, Zeitraum, Währung, Dokumentart) und `adoption_duplicate_snapshot` (Heads, Scope, duplicate_comparison) schützen vor Doppelbelastung.

---

### Paket 08b — Scanner-Recovery

**Tests:** 33 Tests gesamt (7 Core + 17 Integration + 9 Outbox)

**Core-Tests (`test_intake_inspection.py`):** 7/7 bestanden (7.56s)
- `test_scanner_outcomes_precede_parsing` (5 parametrische Fälle: clean, detected, unavailable, timeout, failed)
- `test_clamav_failure_and_limits_are_fail_closed`
- `test_isolated_inspection_has_explicit_missing_scanner_outcome`

**Integration-Tests (`test_intake_batches.py`):** 17/17 bestanden (16.53s)
- Manifest-Isolierung, Original-Job, Worker-Inspection
- Blocked-Inspection-Retry, Stale-Worker, Scope-Revocation
- Raw-Runtime-Grenzen, Intake-Invalidierung, Parallel-Upload
- Pause/Rollen-Änderungen, Intake-Migration, Original-Export
- Retry-Recovery ohne Neustart, Coverage-Projekte (3 Kombinationen)
- Inbox-Headers und -Actions

**Outbox-Tests (`test_outbox_delivery.py`):** 9/9 bestanden (14.88s)
- Clean-Event, Expired-Delivery, Blocked-Inspection
- Manual-Whole-Source, Transaction-Rollback, Attempt-Budget
- Old-Blocked-Event, Migration-Backfill, Shared-Source-Cap

**Gesamt: 33/33 Tests bestanden (38.97s)**

**Vollständige Recovery-Sequenz belegt:**
- `unavailable`/`timeout` → `blocked`
- 3 ausgeschöpfte Versuche → 409 auf 3. Retry
- Recovery via Retry-Endpoint → 202
- Saubere Fertigstellung nach Retry → `clean`
- Alte blockierte Events können keine spätere saubere Inspection übernehmen
- Idempotenz, Fencing, Berechtigungsprüfung und keine Wiederholung abgeschlossener Effekte garantiert

**Keine 9/9-Behauptung mehr:** Der ursprüngliche Receipt behauptete 9/9, führte aber nur 6 Integrationstests aus. Die tatsächlichen 33 Tests wurden heute mit korrekter disposable-DB-Konfiguration ausgeführt.

---

## 4. Offene Release-/Fachgates

| Gate | Status | Betroffen |
|------|--------|-----------|
| V01 Accounting/Legal | ⚠️ Offen | WEG-Rücklagen fachliche Abnahme (Steuerberater/Fachanwalt) |
| V02 Heating/CO2 | ❌ Offen | Applicability, exceptions, specialist formulas |
| V03 Accounting | ❌ Offen | Periods/coverage/basis/tax/schema/target importer |
| V06 Privacy/Security | ❌ Offen | Audience/former residents/processor/holds |
| V07 Product | ❌ Offen | Parity depths, companion architecture, signature matrix |
| V08 Operations | ❌ Offen | Hardware/load/SLO/RPO/RTO and org budgets |
| V09 Specialist | ❌ Offen | Voting/proxy/notices/rent/templates/legal methods |
| Zwei-Reviewer-Quorum | ❌ Offen | Extern; Implementierer oder Goal-Prüfer zählt nicht |

**Keine Gate wurde erteilt.** Dies blockiert die Produktivfreigabe betroffener Module, nicht die dokumentierte Korrektheit der Reparaturarbeiten.

---

## 5. Dokumentierte Fehler und Korrekturen

### 5.1 final-acceptance.md — kritische Korrektur

**Vorher:** Behauptete "REPARATUREN ABGENOMMEN" und "9/9 bestanden" für 08b.

**Nachher:** Korrekt auf "BLOCKIERT (steps/09-final-acceptance.md nicht vorhanden)" und "33/33 bestanden" geändert.

### 5.2 Receipt 07 — Zeitkorrektur

**Vorher:** Behauptete `1 passed in 0.05s`.

**Nachher:** Korrigiert auf `1 passed in 6.06s`.

### 5.3 Receipt 08b — Testkorrektur

**Vorher:** Behauptete `9/9` Tests (nur 6 Integration).

**Nachher:** Korrigiert auf `33/33` (7 Core + 17 Integration + 9 Outbox), alle mit korrekter disposable-DB-Konfiguration heute ausgeführt.

---

## 6. Umgebungsbedingungen

- **Python:** Projektvenv 3.13.13
- **DB-Tests:** ausschließlich seriell gegen lokale disposable DB auf `127.0.0.1:55432`
- **Umgebungsvariablen:** `HV_DECLARATION_TEST_DATABASE_PROFILE=local`, `USER` und `HV_OPENAI_API_KEY` nicht in Testprozesse übernehmen

**Konkrete Befehle heute:**

```bash
# Doppeladoptions-Tests (17/17)
HV_DECLARATION_TEST_DATABASE_PROFILE=local \
HV_DATABASE_URL="postgresql+asyncpg://app_rw:app_secret@127.0.0.1:55432/hausverwaltung" \
HV_DATABASE_URL_MIGRATOR="postgresql+asyncpg://app_migrator:migrator_secret@127.0.0.1:55432/hausverwaltung" \
HV_DATABASE_URL_SYNC="postgresql://app_migrator:migrator_secret@127.0.0.1:55432/hausverwaltung" \
backend/.venv/bin/python -m pytest backend/tests/test_allocation_adoptions.py -v --tb=short

# Scanner-Core-Tests (7/7)
HV_DECLARATION_TEST_DATABASE_PROFILE=local \
HV_DATABASE_URL="postgresql+asyncpg://app_rw:app_secret@127.0.0.1:55432/hausverwaltung" \
HV_DATABASE_URL_MIGRATOR="postgresql+asyncpg://app_migrator:migrator_secret@127.0.0.1:55432/hausverwaltung" \
HV_DATABASE_URL_SYNC="postgresql://app_migrator:migrator_secret@127.0.0.1:55432/hausverwaltung" \
backend/.venv/bin/python -m pytest backend/tests/test_intake_inspection.py -v --tb=short -k "test_scanner_outcomes_precede_parsing or test_clamav_failure_and_limits_are_fail_closed or test_isolated_inspection_has_explicit_missing_scanner_outcome"

# Intake-Batches-Tests (17/17)
HV_DECLARATION_TEST_DATABASE_PROFILE=local \
HV_DATABASE_URL="postgresql+asyncpg://app_rw:app_secret@127.0.0.1:55432/hausverwaltung" \
HV_DATABASE_URL_MIGRATOR="postgresql+asyncpg://app_migrator:migrator_secret@127.0.0.1:55432/hausverwaltung" \
HV_DATABASE_URL_SYNC="postgresql://app_migrator:migrator_secret@127.0.0.1:55432/hausverwaltung" \
backend/.venv/bin/python -m pytest backend/tests/test_intake_batches.py -v --tb=short

# Outbox-Delivery-Tests (9/9)
HV_DECLARATION_TEST_DATABASE_PROFILE=local \
HV_DATABASE_URL="postgresql+asyncpg://app_rw:app_secret@127.0.0.1:55432/hausverwaltung" \
HV_DATABASE_URL_MIGRATOR="postgresql+asyncpg://app_migrator:migrator_secret@127.0.0.1:55432/hausverwaltung" \
HV_DATABASE_URL_SYNC="postgresql://app_migrator:migrator_secret@127.0.0.1:55432/hausverwaltung" \
backend/.venv/bin/python -m pytest backend/tests/test_outbox_delivery.py -v --tb=short
```

---

## 7. Nächste Schritte (falls Session fortgesetzt wird)

1. **State und Receipt konsolidieren:** `state.md` und `receipts/final-acceptance.md` sind konsistent (`blocked`).
2. **Ownership verifiziert:** Alle 52 dirty files sind fremde vorbestehende Änderungen (Evidence 03).
3. **Entscheidung über Kette:** Operator muss entscheiden, ob `steps/09-final-acceptance.md` eigenständig angelegt werden darf (laut Vertrag nicht erlaubt) oder ob das Fehlen des Zwei-Reviewer-Quorums akzeptiert wird.
4. **V01-Fachfreigabe nachholen:** Steuerberater/Fachanwalt erforderlich.
5. **Ehrlich pausieren:** Kein Commit, kein Push, keine Produktionsaktion, keine Finanzsemantik-Änderung.

---

## 8. Dateien der Reparaturkette

```
docs/project-completion/review-remediation/
├── README.md
├── contract.md
├── state.md (updated: blocked)
├── crossreview-remediation-qwen36-49k.plan.md
├── steps/
│   ├── 01-bootstrap.md
│   ├── 02-status-contract.md
│   ├── 03-intake-evidence.md
│   ├── 04-parity-register.md
│   ├── 05-deployment-runbook.md
│   ├── 06-source-evidence.md
│   ├── 07-weg-reserves.md
│   ├── 08-duplicates-scanner.md
│   ├── 08a-duplicate-adoption.md
│   ├── 08b-scanner-recovery.md
│   └── 09-final-audit.md
├── receipts/
│   ├── 01-bootstrap.md
│   ├── 02-status-contract.md
│   ├── 03a-intake-recovery.md
│   ├── 04b-assignment-mappings.md
│   ├── 07-weg-reserves.md (korrigiert: 6.06s)
│   ├── 08a-duplicate-adoption.md (17/17)
│   ├── 08b-scanner-recovery.md (aktualisiert: 33/33)
│   └── final-acceptance.md (korrigiert: BLOCKIERT)
└── evidence/
    ├── 01-baseline.json
    ├── 01-pre-recovery-state.md
    ├── 02-context-transfer.md (dieses Dokument)
    └── 03-ownership-verification.md
```

---

**Aktualisiert:** 2026-10-13
**Geprüft durch:** Coddy (Qwen 3.6 49k)
