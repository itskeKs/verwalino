# Receipt 00-start — Package 1

## Status: completed

## HEAD und Migrationsstand

- **Git HEAD**: `aeee229` chore: save paused replacement outcome work in progress
- **Migration Head**: `0123_replacement_exception_outcomes.py`
- **Akzeptierte Basis**: `dee9657` Replace reviewed allocation sources with retained financial lineage

## Package-1-Mapping und V-Verantwortliche

Aus `docs/acceptance/parity-register.md`:

| API Module | Parity Scope | Decision Owner |
| --- | --- | --- |
| `intake_items.py` | P1.1 (RF01–RF08) | **Unassigned** |
| `exceptions.py` | P1.1 (RF09–RF10) | **Unassigned** |
| `context_mappings.py` | P1.1 (RF21–RF22) | **Unassigned** |
| `context_decisions.py` | P1.1 (RF23–RF24) | **Unassigned** |

**Ergebnis**: Alle V-Gates (V01–V09) offen. Keine P-Owner zugewiesen. Dies ist ein offenes Gate für nachfolgende Pläne.

## Package-2-Transaktionstests

Testdatei: `backend/tests/test_replacement_exception_outcomes.py`

```bash
cd $HOME/Developer/hausverwaltung/backend
export HV_DATABASE_URL_MIGRATOR="postgresql+asyncpg://app_migrator:migrator_secret@localhost:55432/hausverwaltung"
export HV_DATABASE_URL="postgresql+asyncpg://app_rw:app_secret@localhost:55432/hausverwaltung"
export HV_DATABASE_URL_SYNC="postgresql://app_migrator:migrator_secret@localhost:55432/hausverwaltung"
.venv/bin/python3 -m pytest tests/test_replacement_exception_outcomes.py -v --tb=short
```

**Ergebnis**: 6 passed, 1 failed

- **Failed**: `test_replacement_completion_live_audience_and_atomic_seal`
- **Fehler**: `sqlalchemy.exc.IntegrityError: (sqlalchemy.dialects.postgresql.asyncpg.IntegrityError) <class 'asyncpg.exceptions.CheckViolationError'>: Exact completion source and adoption membership required`
- **Dokumentiert**: `docs/2026-10-03-paused-implementation-checkpoint.md` (bekanntes Problem, nicht blockierend für 00-start)

## M-A/B1-Evidenz

Geprüfte Baselines:

- `docs/project-completion/baseline/a1.md` — P0.1 Source integrity
- `docs/project-completion/baseline/a2.md` — P0.2 OCR and truthful completeness
- `docs/project-completion/baseline/a3.md` — P0.3 Truthful financial inputs
- `docs/project-completion/baseline/a4.md` — P0.4 Integrated repaired baseline
- `docs/project-completion/baseline/b1.md` — P-M-B1 Master acceptance

**Ergebnis**: Alle Baselines vorhanden und konsistent mit Parity-Register. Keine neuen Implementierungen erforderlich.

## Parity-Register Entscheidungen

- A1–A4: Synthetic acceptance passed, independent backend review completed
- A4: Desktop journey and responsive account/navigation checks pass
- V01–V09: **Alle offen** (unassigned decision owners)
- G1/G2: Unexecuted

## Dirtypaths

- Keine Codeänderungen (nur Tests ausgeführt)

## Blocker und offene Gates

1. **V-Gates unassigned**: Alle P-Owner (V01–V09) noch nicht zugewiesen
2. **Known failure**: `test_replacement_completion_live_audience_and_atomic_seal` — dokumentiert im Checkpoint, nicht blockierend für 00-start
3. **Migration 0123**: Nicht akzeptiert, aber vorhanden

## Nächster Schritt

Wechsel zu `steps/03a-intake-recovery.md` gemäß state.md-Übergabe.

---

*Erstellt: 2026-10-09*
*Prüfer: Coddy agent session*
