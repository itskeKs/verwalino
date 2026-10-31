# 04b-Assignment-Mappings - Test-Evidenz (tatsächliche Ausführung)

**Datum:** 2026-10-13
**Ausführung:** Nachträgliche Verifikation der behaupteten 91 Tests
**Testdatenbank:** `infra/docker-compose.declaration-test.yml` auf `127.0.0.1:55432`
**Test-URLs:**

```
HV_DATABASE_URL=postgresql+asyncpg://app_rw:app_secret@127.0.0.1:55432/hausverwaltung
HV_DATABASE_URL_MIGRATOR=postgresql+asyncpg://app_migrator:migrator_secret@127.0.0.1:55432/hausverwaltung
HV_DATABASE_URL_SYNC=postgresql://app_migrator:migrator_secret@127.0.0.1:55432/hausverwaltung
```

**USER** und **HV_OPENAI_API_KEY** wurden auf leere Strings gesetzt.

## Testausführung 1 (45 Tests)

```bash
cd $HOME/Developer/hausverwaltung/backend
HV_DATABASE_URL="postgresql+asyncpg://app_rw:app_secret@127.0.0.1:55432/hausverwaltung" \
HV_DATABASE_URL_MIGRATOR="postgresql+asyncpg://app_migrator:migrator_secret@127.0.0.1:55432/hausverwaltung" \
HV_DATABASE_URL_SYNC="postgresql://app_migrator:migrator_secret@127.0.0.1:55432/hausverwaltung" \
USER="" HV_OPENAI_API_KEY="" \
.venv/bin/python -m pytest \
  tests/test_assignment_proposals.py \
  tests/test_context_mappings.py \
  tests/test_context_decisions.py \
  tests/test_allocation_adoptions.py \
  -v --tb=short
```

**Ergebnis:** 45 passed in 54.15s (Exit 0)

## Testausführung 2 (46 Tests)

```bash
cd $HOME/Developer/hausverwaltung/backend
HV_DATABASE_URL="postgresql+asyncpg://app_rw:app_secret@127.0.0.1:55432/hausverwaltung" \
HV_DATABASE_URL_MIGRATOR="postgresql+asyncpg://app_migrator:migrator_secret@127.0.0.1:55432/hausverwaltung" \
HV_DATABASE_URL_SYNC="postgresql://app_migrator:migrator_secret@127.0.0.1:55432/hausverwaltung" \
USER="" HV_OPENAI_API_KEY="" \
.venv/bin/python -m pytest \
  tests/test_duplicate_comparisons.py \
  tests/test_duplicate_exceptions.py \
  tests/test_intake_extraction_worker.py \
  tests/test_intake_batches.py \
  -v --tb=short
```

**Ergebnis:** 46 passed in 39.56s (Exit 0)

## Gesamtübersicht

| Datei | Tests | Ergebnis |
|-------|-------|----------|
| `test_assignment_proposals.py` | 6 | 6 passed |
| `test_context_mappings.py` | 13 | 13 passed |
| `test_context_decisions.py` | 9 | 9 passed |
| `test_allocation_adoptions.py` | 17 | 17 passed |
| `test_duplicate_comparisons.py` | 8 | 8 passed |
| `test_duplicate_exceptions.py` | 8 | 8 passed |
| `test_intake_extraction_worker.py` | 13 | 13 passed |
| `test_intake_batches.py` | 17 | 17 passed |
| **Gesamt** | **91** | **91 passed, 0 failed** |

## Spezifische Prüfungen

### Doppeladoption

`test_allocation_adoptions.py::test_adoption_exact_pair_resolution_and_retained_duplicate_claim` (4 Parameter-Kombinationen) und
`test_allocation_adoptions.py::test_adoption_duplicate_manual_without_attachment_in_both_orders` (2 Parameter-Kombinationen)
decken explizit Doppeladoption, Exact-Pair-Resolution und Duplicate-Pair-Auflösung ab. Alle 6 Parameter-Kombinationen bestanden.

### Scanner-/Intake-Recovery

`test_intake_extraction_worker.py` deckt ab:

- **Claim-Expiration + Lease-Fence:** `test_expired_claim_is_fenced_and_original_authority_rechecked`
- **Attempt-Limit (3 Versuche -> blocked):** `test_shared_runner_claim_competition_and_attempt_limit`
- **Scope-Revocation:** `test_permission_revocation_during_processor_discards_result`
- **Storage-Integrity-Fehler:** `test_storage_integrity_failure_is_durable_without_parsing`
- **Idempotency-Key:** `test_finish_is_atomic_and_stale_replay_has_no_effect`

Alle 13 Tests bestanden.

### IntakeService.status - Cursor-Befund

Die Kompaktierung behauptete, `IntakeService.status` verwende `payload` ohne `signer.loads()`.

**Korrektur:** Zeile 66 ruft explizit `payload=signer.loads(cursor,max_age=86400)` auf. Der Befund war ein falscher Positiver aus einem abgeschnittenen früheren Leseergebnis.

### WEG-Rücklagen

`docs/adr/0007-weg-abrechnungsspitze-und-wirtschaftsplan.md` bleibt im Status **Vorgeschlagen**.
Keine Finanzlogik geändert - menschliche/fachliche Bestätigung fehlt weiterhin.
