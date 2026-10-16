# 03c-isolated-qualification — Receipt

## Prüfzeitraum

- Geprüft: 2026-10-09
- HEAD: `aeee229 chore: save paused replacement outcome work in progress`
- Accepted historical base: `dee9657`, Migration 0122
- Migration head: `0123_replacement_exception_outcomes.py`

## Geprüfte Komponenten

### intake_inspection

- **Datei**: `backend/app/core/intake_inspection.py`
- **Tests**: `backend/tests/test_intake_inspection.py` (27 Tests)
- **Ergebnis**: 27/27 bestanden

**Abgedeckte Qualifikation**:
- MIME-Sniffing precedes parsing (signature/empty/size limits)
- PDF bounds (corrupt/encrypted/page limit)
- XML validation (XXE, oversized, well-formed)
- Image validation (PNG/JPEG, pixel limits, truncated JPEG)
- Malware-Scanner-Integration (fail-closed: unavailable/timeout/failed/detected)
- Isolated inspection subprocess (scanner_unavailable/inspection_timeout)
- Isolated clean path with explicit scanner stub (protocol stub proves wiring)
- Malformed child result blocking (explicit schema validation)

### workflow_runner

- **Datei**: `backend/app/workers/workflow_runner.py`
- **Tests**: `backend/tests/test_workflow_runner.py` (8 Tests)
- **Ergebnis**: 8/8 bestanden

**Abgedeckte Qualifikation**:
- Tenant-Rotation ohne Duplicate-Slots (fair dispatch)
- Idle/Failed-Stages ohne Starvation (source text redaction)
- Parallel ticks serial + Cancellation propagation
- Shared invoice adapter: live lease preservation + expired attempt reconciliation
- Declaration adapter: scoped tenant discovery (no global enumeration)
- Disabled declaration + discovery failure safe (no crash)
- Cancelled local processor drains real thread before next tick
- Queue lock wait cancellable before processor starts (advisory lock)

### Dockerfile

- **Datei**: `Dockerfile`
- **Prüfung**: Build-Konsistenz, Multi-Stage-Build, pinned digests, worker-entrypoint
- **Status**: Konstruktiv geprüft (kein Code-Change notwendig)

## Bekannte Fehler

### Migration-Downgrade-Reihenfolge (deferred)

```
psycopg2.errors.DependentObjectsStillExist
constraint completion_replacement_receipt_fk on table exception_completion_member
depends on table allocation_correction
```

Beim Downgrade von `0114_allocation_corrections.py` soll `allocation_correction` gelöscht werden, obwohl die von `0123_replacement_exception_outcomes.py` eingeführte FK `completion_replacement_receipt_fk` noch davon abhängt.

- Status: `deferred`
- Migration `0123`: weiterhin unakzeptiert
- Fehler nicht als gelöst behandeln
- Keine Ableitung einer vollständigen Package-/Release-Abnahme

## Zusammenfassung

| Komponente | Tests | Bestanden | Status |
|------------|-------|-----------|--------|
| intake_inspection | 27 | 27 | done-local |
| workflow_runner | 8 | 8 | done-local |
| Dockerfile | - | - | geprüft |

**Gesamt**: 35/35 Tests bestanden, 1 deferred (Migration-Downgrade)

## Übergabe

Dieser Teilplan ist lokal abgeschlossen (`done-local`). Keine Produktions- oder vollständige Paritybehauptung abgeleitet.

Nächster Schritt: `steps/04a-replacement-outcomes.md` laden und dessen Auftrag bearbeiten.
