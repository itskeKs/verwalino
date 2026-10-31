# Receipt 03a — Intake-Recovery: Test-Sammlung und Ausführung

Datum: 2026-10-13
Status: done-local
Geprüft von: Qwen3.6-49k Reparaturkette

## Auftrag aus 03-intake-evidence.md

- 17 Testnamen werden im Plan genannt; 14 sollen fehlen.
- Tatsächliche NodeIDs per AST/Pytest-Collection ermitteln.
- Vorhandene Logs auf Ergebnisse/Datum/Umgebung prüfen.
- Nicht fehlende Namen nicht durch erfundene Tests ersetzen.

## Tatsächliche Test-Sammlung

### Vorherige Zählung (manuell, unvollständig)

Zuvor manuell per `grep "^async def test_"` gezählt: **15 Testfunktionen**.

### Korrekte Pytest-Collection

```bash
cd $HOME/Developer/hausverwaltung
export HV_DATABASE_URL='postgresql+asyncpg://app_rw:app_secret@127.0.0.1:55432/hausverwaltung'
export HV_DATABASE_URL_MIGRATOR='postgresql+asyncpg://app_migrator:migrator_secret@127.0.0.1:55432/hausverwaltung'
export HV_DATABASE_URL_SYNC='postgresql://app_migrator:migrator_secret@127.0.0.1:55432/hausverwaltung'
backend/.venv/bin/python -m pytest backend/tests/test_intake_batches.py --collect-only
```

**Ergebnis: 17 Tests gesammelt** (nicht 15 wie zuvor manuell gezählt).

Die parameterisierten Tests erzeugen 3 Varianten:
- `test_coverage_projects_actual_intake_read_and_export[False-True]`
- `test_coverage_projects_actual_intake_read_and_export[True-False]`
- `test_coverage_projects_actual_intake_read_and_export[True-True]`

### Vollständige Liste der 17 Test-NodeIDs

1. `test_manifest_is_bounded_private_and_idempotent`
2. `test_received_original_job_and_outbox_commit_once`
3. `test_worker_inspection_releases_only_exact_private_reference`
4. `test_blocked_inspection_retries_same_original_with_bounded_replay`
5. `test_stale_worker_cannot_complete_reclaimed_generation`
6. `test_scope_revocation_cancels_work_and_hides_status`
7. `test_raw_runtime_cannot_forge_custody_or_clean_inspection`
8. `test_intake_invalidates_coverage_and_blocks_active_work`
9. `test_parallel_upload_and_signed_continuation_preserve_manifest`
10. `test_pause_and_role_changes_stop_new_work`
11. `test_intake_migration_refuses_data_and_preserves_function_identity`
12. `test_original_export_requires_clean_status_and_live_export_scope`
13. `test_retry_can_recover_without_restarting_completed_work`
14. `test_coverage_projects_actual_intake_read_and_export[False-True]`
15. `test_coverage_projects_actual_intake_read_and_export[True-False]`
16. `test_coverage_projects_actual_intake_read_and_export[True-True]`
17. `test_inbox_headers_and_actions_use_current_private_authority`

## Testausführung

### Befehl

```bash
backend/.venv/bin/python -m pytest backend/tests/test_intake_batches.py -v --tb=short
```

### Ergebnis

```
backend/tests/test_intake_batches.py::test_manifest_is_bounded_private_and_idempotent PASSED [  5%]
backend/tests/test_intake_batches.py::test_received_original_job_and_outbox_commit_once PASSED [ 11%]
backend/tests/test_intake_batches.py::test_worker_inspection_releases_only_exact_private_reference PASSED [ 17%]
backend/tests/test_intake_batches.py::test_blocked_inspection_retries_same_original_with_bounded_replay PASSED [ 23%]
backend/tests/test_intake_batches.py::test_stale_worker_cannot_complete_reclaimed_generation PASSED [ 29%]
backend/tests/test_intake_batches.py::test_scope_revocation_cancels_work_and_hides_status PASSED [ 35%]
backend/tests/test_intake_batches.py::test_raw_runtime_cannot_forge_custody_or_clean_inspection PASSED [ 41%]
backend/tests/test_intake_batches.py::test_intake_invalidates_coverage_and_blocks_active_work PASSED [ 47%]
backend/tests/test_intake_batches.py::test_parallel_upload_and_signed_continuation_preserve_manifest PASSED [ 52%]
backend/tests/test_intake_batches.py::test_pause_and_role_changes_stop_new_work PASSED [ 58%]
backend/tests/test_intake_batches.py::test_intake_migration_refuses_data_and_preserves_function_identity PASSED [ 64%]
backend/tests/test_intake_batches.py::test_original_export_requires_clean_status_and_live_export_scope PASSED [ 70%]
backend/tests/test_intake_batches.py::test_retry_can_recover_without_restarting_completed_work PASSED [ 76%]
backend/tests/test_intake_batches.py::test_coverage_projects_actual_intake_read_and_export[False-True] PASSED [ 82%]
backend/tests/test_intake_batches.py::test_coverage_projects_actual_intake_read_and_export[True-False] PASSED [ 88%]
backend/tests/test_intake_batches.py::test_coverage_projects_actual_intake_read_and_export[True-True] PASSED [ 94%]
backend/tests/test_intake_batches.py::test_inbox_headers_and_actions_use_current_private_authority PASSED [100%]

============================= 17 passed in 18.38s ==============================
```

**Ergebnis: 17/17 Tests bestanden in 18.38 Sekunden.**

## Korrektur früherer Fehler

### Fehler 1: Falsche manuelle Zählung

Die manuelle Zählung per `grep "^async def test_"` ergab 15, weil sie parameterisierte Tests nicht korrekt auflöste. Die korrekte Pytest-Collection zeigt 17 Test-NodeIDs.

### Fehler 2: Fehlende 03a/03b/03c-Receipts

Die behaupteten Receipts `03a-intake-recovery.md`, `03b-intake-jobs.md` und `03c-isolated-qualification.md` existierten nicht im Reparaturverzeichnis. Dieser Receipt erstellt die fehlende 03a-Evidenz neu.

## Restpunktmatrix (03a-Anteil)

| Restpunkt | Status | Quelle | Nachweis | Zuständigkeit |
|-----------|--------|--------|----------|---------------|
| Jobs/Outbox-Kommunikation | lokal belegt | test_received_original_job_and_outbox_commit_once | 03a-Passed | Intake/Worker |
| Worker-Inspection | lokal belegt | test_worker_inspection_releases_only_exact_private_reference | 03a-Passed | Intake/Worker |
| Blocked-Inspection Replay | lokal belegt | test_blocked_inspection_retries_same_original_with_bounded_replay | 03a-Passed | Intake/Worker |
| Stale Worker Completion | lokal belegt | test_stale_worker_cannot_complete_reclaimed_generation | 03a-Passed | Intake/Worker |
| Scope Revocation | lokal belegt | test_scope_revocation_cancels_work_and_hides_status | 03a-Passed | Intake/Scope |
| Raw Runtime Inspection | lokal belegt | test_raw_runtime_cannot_forge_custody_or_clean_inspection | 03a-Passed | Intake/Security |
| Intake Coverage Invalidierung | lokal belegt | test_intake_invalidates_coverage_and_blocks_active_work | 03a-Passed | Intake/Coverage |
| Parallel Upload/Manifest | lokal belegt | test_parallel_upload_and_signed_continuation_preserve_manifest | 03a-Passed | Intake/Upload |
| Pause/Rollenänderung | lokal belegt | test_pause_and_role_changes_stop_new_work | 03a-Passed | Intake/Roles |
| Intake Migration | lokal belegt | test_intake_migration_refuses_data_and_preserves_function_identity | 03a-Passed | Intake/Migration |
| Original Export | lokal belegt | test_original_export_requires_clean_status_and_live_export_scope | 03a-Passed | Intake/Export |
| Retry Recovery | lokal belegt | test_retry_can_recover_without_restarting_completed_work | 03a-Passed | Intake/Retry |
| Coverage Projects (3 Varianten) | lokal belegt | test_coverage_projects_actual_intake_read_and_export[*] | 03a-Passed | Intake/Coverage |
| Inbox Headers/Actions | lokal belegt | test_inbox_headers_and_actions_use_current_private_authority | 03a-Passed | Intake/Inbox |
| Registry-Adapter/Backfill | offen | Nicht direkt getestet | Offen | Registry |
| Browserjourney | offen | Nicht direkt getestet | Offen | Browser |

**Zusammenfassung 03a:** 14 von 16 Restpunkten lokal belegt durch bestandene Tests. 2 Restpunkte (Registry-Adapter/Backfill, Browserjourney) bleiben offen.

## Abnahme 03a

- [x] Testnamen per Pytest-Collection aufgelöst (17 NodeIDs, nicht 15)
- [x] Ergebnisse evidenzgedeckt (17/17 bestanden)
- [x] Jeder B2-Restpunkt hat Status und Zuständigkeit
- [x] Parameterisierung korrekt berücksichtigt (3 Varianten statt 1 Test)
- [x] Fehlende frühere Receipts durch neuen datierten Nachweis ersetzt

**Status: done-local**
