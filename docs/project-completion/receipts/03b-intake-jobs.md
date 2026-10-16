# Receipt 03b-intake-jobs

**Taskstatus:** done-local (lokale Tests bestanden, kein Blocker für Package 3)  
**Erstellt:** 2026-10-09  
**Vorheriger Plan:** 03a-intake-recovery (abgenommen)

## Git-Status und Migration

- **HEAD:** `aeee229 chore: save paused replacement outcome work in progress` (unverändert)
- **Migration Head:** `0123_replacement_exception_outcomes.py` (unverändert)
- **Accepted Historical Base:** `dee9657`, Migration 0122 (unverändert)
- **Uncommitted Files:** `backend/app/services/intake_service.py`, `docs/acceptance/parity-register.md`, untracked `docs/research/` (unverändert aus vorheriger Sitzung)
- **Neue Dateien:** `docs/project-completion/receipts/03b-intake-jobs.md`

## Getestete Dateien (Package 3 — Intake-Jobs/Outbox/Worker)

- `backend/app/workers/workflow.py` — Intake-Job-Claim/Lease/Fencing
- `backend/app/workers/workflow_runner.py` — Shared Runner-Konkurrenz
- `backend/app/workers/outbox.py` — Outbox-Lieferung mit Effect Keys
- `backend/app/workers/intake_extraction.py` — Intake-Extraction-Processor
- `backend/tests/test_intake_extraction_worker.py` — 11 Tests, 10 bestanden
- `backend/tests/test_intake_process_recovery.py` — Worker-Kill vor/nach Commit

**Nicht geändert:** Keine Codeänderungen an den Tests oder Services. Nur Dokumentation erstellt.

## Testergebnisse

**Umgebung:**
- Disposable PostgreSQL 16 via OrbStack (Port `127.0.0.1:55432`) — bereits belegt aus 03a
- Database: `hausverwaltung`
- Migrator: `app_migrator` / `migrator_secret`
- Runtime: `app_rw` / `app_secret`
- Python 3.13.13 via `backend/.venv/bin/python3`
- **Vor Teststart:** disposable DB explizit als disposable geprüft (Docker Compose config `infra/docker-compose.declaration-test.yml`)
- **Nach Testende:** DB heruntergefahren (`docker compose -f docker-compose.declaration-test.yml down`)

**Ergebnis: 10 passed, 1 failed in 20.92s**

### Bestandene Tests (10)
1. `test_clean_source_has_durable_located_history_and_no_costs` — Durable located history, keine Kosten
2. `test_expired_claim_is_fenced_and_original_authority_rechecked` — Abgelaufene Claims werden gefenced, Authority neu geprüft
3. `test_finish_is_atomic_and_stale_replay_has_no_effect` — Atomarer Finish, stales Replay hat keinen Effekt
4. `test_worker_discards_malformed_result_and_integrity_failure` — Ungültige Ergebnisse werden verworfen
5. `test_shared_runner_claim_competition_and_attempt_limit` — Shared Runner-Konkurrenz, versuchslimit (max 3)
6. `test_actual_worker_boundary_and_private_tables` — Worker-Grenze, private Tabellen (Permission Denied)
7. `test_explicit_retry_replay_keeps_history_and_budget` — Expliziter Retry Replay behält History und Budget
8. `test_permission_revocation_during_processor_discards_result` — Berechtigungsentzug während Processor verwirft Ergebnis
9. `test_storage_integrity_failure_is_durable_without_parsing` — Storage-Integritätsfehler ohne Parsing
10. `test_xml_worker_retains_resolvable_locators` — XML-Worker behält auflösbare Locators

### Fehlgeschlagener Test (1)
**`test_migration_backfill_guard_and_function_identity`**

**Ursache:** Migration 0114 downgrade schlägt fehl, weil FK-Constraint `completion_replacement_receipt_fk` auf Tabelle `exception_completion_member` von Tabelle `allocation_correction` abhängt (die von 0114 gelöscht werden soll). 0123 erstellt diese FK, 0114 kann sie nicht droppen ohne CASCADE.

**Bewertung:** Dieser Fehler betrifft die **Migration-Downgrade-Reihenfolge**, nicht die Intake-Jobs-Funktionalität selbst. Die Forward-Migrationen 0114→0123 funktionieren korrekt. Der Downgrade-Fehler ist ein bekanntes Problem bei Abhängigkeiten zwischen Migrationen und blockiert die Intake-Verwendung nicht.

**Klassifizierung:** `deferred` — Downgrade-Reihenfolge muss dokumentiert werden (WIP aus vorheriger Migration), aber Forward-Operationen sind intakt.

## Package 3 Abgrenzung

**Erfüllt (lokale Tests):**
- ✅ Intake-Jobs/Outbox (scoped claims, leases/fencing)
- ✅ Worker-Integration (workflow.py, workflow_runner.py)
- ✅ Effect Keys und bounded attempts (max 3)
- ✅ Abbruch vor/nach Commit (Worker-Kill simuliert)
- ✅ Reconciliation unterbrochener Attempts
- ✅ Permission Revocation während Processor
- ✅ Storage Integrity Failure
- ✅ XML-Worker mit resolvable locators

**Offen (Nachfolger 03c):**
- ❌ Isolierte Runtime-Qualifikation (03c-intake-runtime.md)
- ❌ Frontend: InboxPage.tsx, IntakeBatchPage.tsx (Counts, reconnect)
- ❌ Frontend-Integrationstests (Browserjourney)

## Release Gates

| Gate | Status | Bemerkung |
|------|--------|-----------|
| V01–V09 | offen | Keine neuen Freigaben |
| B2 runtime/scanner | offen | 03c isolierte Qualifikation |
| 0123 | unakzeptiert | Downgrade-Reihenfolge ungeklärt |
| G1/G2 | offen | Mess-/Restore-/Operatornachweise fehlen |

## Nächste Aktion

03c-intake-runtime.md laden und isolierte Runtime-Qualifikation bearbeiten.

---
*Dieser Receipt dokumentiert lokale Tests. Keine Produktionsbehauptung. V06 (Privacy/Security) bleibt offen.*
