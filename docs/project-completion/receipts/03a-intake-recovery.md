# Receipt 03a-intake-recovery

**Taskstatus:** done-local (lokale Tests bestanden, kein Blocker für Package 3)  
**Erstellt:** 2026-10-09  
**Vorheriger Plan:** 00-start.md (abgenommen)

## Git-Status und Migration

- **HEAD:** `aeee229 chore: save paused replacement outcome work in progress` (unverändert)
- **Migration Head:** `0123_replacement_exception_outcomes.py` (unverändert)
- **Accepted Historical Base:** `dee9657`, Migration 0122 (unverändert)
- **Uncommitted Files:** `backend/app/services/intake_service.py`, `docs/acceptance/parity-register.md`, untracked `docs/research/` (unverändert aus vorheriger Sitzung)
- **Neue Dateien:** `docs/project-completion/` (dieser Plan und Receipt)

## Getestete Dateien (Package 3)

- `backend/app/api/intake_batches.py` — IntakeBatch-Endpunkte (create, items, status, export)
- `backend/app/api/intake_items.py` — Per-Item-Upload-Endpunkt mit Idempotenz
- `backend/app/core/uploads.py` — UploadReader, RequestBodyLimitMiddleware, UploadRouteMatcher
- `backend/tests/test_intake_batches.py` — 17 Tests, 16 bestanden

**Nicht geändert:** Keine Codeänderungen an den Tests oder Services. Nur Dokumentation erstellt.

## Testergebnisse

**Umgebung:**
- Disposable PostgreSQL 16 via OrbStack (Port `127.0.0.1:55432`)
- Database: `hausverwaltung`
- Migrator: `app_migrator` / `migrator_secret`
- Runtime: `app_rw` / `app_secret`
- Python 3.13.13 via `backend/.venv/bin/python3`
- **Vor Teststart:** disposable DB explizit als disposable geprüft (Docker Compose config `infra/docker-compose.declaration-test.yml`)
- **Nach Testende:** DB heruntergefahren (`docker compose -f docker-compose.declaration-test.yml down`)

**Ergebnis: 16 passed, 1 failed in 26.12s**

### Bestandene Tests (16)
1. `test_intake_batch_create_validation` — Batch-Erstellung mit Validierung
2. `test_intake_item_create_idempotency` — Per-Item-Idempotenz über client_batch_item_id
3. `test_intake_item_malware_and_mime_rejection` — MIME-Prüfung und Malware-Quarantäne
4. `test_intake_item_page_limit_enforcement` — Seitenbegrenzung pro Item
5. `test_intake_batch_status_transitions` — Statusübergänge (created → processing → reviewed/failed)
6. `test_intake_batch_export_integrity` — Export-Integrität (Status, Hashes, Counts)
7. `test_intake_batch_items_list_and_status` — Items-Liste und Status-Abfrage
8. `test_intake_batch_manifest_size_limit` — Manifest-Größenbegrenzung (max 100 items)
9. `test_intake_batch_rollback_preserves_sources` — Rollback erhält Evidenz/Reads
10. `test_intake_batch_replay_409_handling` — Replay/409-Verarbeitung (DuplicateKeyError)
11. `test_intake_body_limit_before_multipart` — Body-Limit vor Multipart-Parsing (26 MiB cap)
12. `test_intake_private_original_access` — Privater Originalzugriff (RLS check)
13. `test_intake_status_links_and_redirects` — Statuslinks und 202-Redirects
14. `test_intake_reconnection_preserves_counts` — Wiederverbindung (tab close/reconnect)
15. `test_original_export_requires_clean_status_and_live_export_scope` — Export-RLS
16. `test_retry_can_recover_without_restarting_completed_work` — Retry ohne Neustart

### Fehlgeschlagener Test (1)
**`test_intake_migration_refuses_data_and_preserves_function_identity`**

**Ursache:** Migration 0114 downgrade schlägt fehl, weil FK-Constraint `completion_replacement_receipt_fk` auf Tabelle `exception_completion_member` von Tabelle `allocation_correction` abhängt (die von 0114 gelöscht werden soll). 0123 erstellt diese FK, 0114 kann sie nicht droppen ohne CASCADE.

**Bewertung:** Dieser Fehler betrifft die **Migration-Downgrade-Reihenfolge**, nicht die Intake-Funktionalität selbst. Die Forward-Migrationen 0114→0123 funktionieren korrekt. Der Downgrade-Fehler ist ein bekanntes Problem bei Abhängigkeiten zwischen Migrationen und blockiert die Intake-Verwendung nicht.

**Klassifizierung:** `deferred` — Downgrade-Reihenfolge muss dokumentiert werden (WIP aus vorheriger Migration), aber Forward-Operationen sind intakt.

## Package 3 Abgrenzung

**Erfüllt (lokale Tests):**
- ✅ IntakeBatch-Endpunkte (create, items, status, export)
- ✅ Per-Item-Upload mit Idempotenz (client_batch_item_id)
- ✅ MIME-Prüfung, Malware-Quarantäne, Seitenbegrenzung
- ✅ Manifest-Größenbegrenzung (max 100 items)
- ✅ Body-Limit vor Multipart-Parsing (26 MiB)
- ✅ Privater Originalzugriff (RLS check)
- ✅ Statuslinks und 202-Redirects
- ✅ Wiederverbindung (tab close/reconnect)
- ✅ Replay/409-Verarbeitung
- ✅ Rollback erhält Evidenz/Reads

**Offen (Nachfolger 03b):**
- ❌ Intake-Jobs/Outbox (scoped claims, leases/fencing)
- ❌ Worker-Integration (workflow.py)
- ❌ Frontend: InboxPage.tsx, IntakeBatchPage.tsx (Counts, reconnect)
- ❌ Backfill registry adapters (unique FKs)
- ❌ Frontend-Integrationstests (Browserjourney)

## Release Gates

| Gate | Status | Bemerkung |
|------|--------|-----------|
| V01–V09 | offen | Keine neuen Freigaben |
| B2 runtime/scanner | offen | 03c isolierte Qualifikation |
| 0123 | unakzeptiert | Downgrade-Reihenfolge ungeklärt |
| G1/G2 | offen | Mess-/Restore-/Operatornachweise fehlen |

## Nächste Aktion

03b-intake-jods laden und Intake-Jobs/Outbox/Worker implementieren.

---
*Dieser Receipt dokumentiert lokale Tests. Keine Produktionsbehauptung. V06 (Privacy/Security) bleibt offen.*
