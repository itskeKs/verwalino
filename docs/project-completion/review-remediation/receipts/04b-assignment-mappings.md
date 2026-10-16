# 04b-Assignment-Mappings Receipt (aktualisiert)

**Datum:** 2026-10-13
**Teilplan:** `steps/04b-assignment-mappings.md`
**Status:** `open` — Blockade: Reparatur-Teilplan fehlt

## Geprüfte Tests (91 Tests, alle bestanden)

| Testdatei | Tests | Ergebnis |
|-----------|-------|----------|
| `test_assignment_proposals.py` | 6 | 6 passed |
| `test_context_mappings.py` | 13 | 13 passed |
| `test_context_decisions.py` | 9 | 9 passed |
| `test_allocation_adoptions.py` | 17 | 17 passed |
| `test_duplicate_comparisons.py` | 8 | 8 passed |
| `test_duplicate_exceptions.py` | 8 | 8 passed |
| `test_intake_extraction_worker.py` | 13 | 13 passed |
| `test_intake_batches.py` | 17 | 17 passed |
| **Gesamt** | **91** | **91 passed, 0 failed** |

## Testausführungen (tatsächlich durchgeführt)

### Ausführung 1 (45 Tests)
```bash
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

### Ausführung 2 (46 Tests)
```bash
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

## Spezifische Plan-Prüfungen

### Doppeladotion
`test_allocation_adoptions.py` deckt:
- Exact-Pair-Resolution (`test_adoption_exact_pair_resolution_and_retained_duplicate_claim`)
- Duplicate-Pair-Auflösung (`test_adoption_duplicate_manual_without_attachment_in_both_orders`)
- Atomisches Rollback, Scope-/Audience-Zugriff, Rollen, Invalidierung durch neue Receipts

Alle 4 Parameter-Kombinationen bestanden.

### Scanner-/Intake-Recovery
`test_intake_extraction_worker.py` deckt:
- **Claim-Expiration + Lease-Fence:** `test_expired_claim_is_fenced_and_original_authority_rechecked`
- **Attempt-Limit (3 Versuche -> blocked):** `test_shared_runner_claim_competition_and_attempt_limit`
- **Scope-Revocation:** `test_permission_revocation_during_processor_discards_result`
- **Storage-Integrity-Fehler:** `test_storage_integrity_failure_is_durable_without_parsing`
- **Idempotency-Key:** `test_finish_is_atomic_and_stale_replay_has_no_effect`

Alle 13 Tests bestanden.

### WEG-Rücklagen
`docs/adr/0007-weg-abrechnungsspitze-und-wirtschaftsplan.md` bleibt im Status **Vorgeschlagen**.
Keine Finanzlogik geändert - menschliche/fachliche Bestätigung fehlt.

### IntakeService.status - Cursor-Befund
Die Kompaktierung behauptete, `IntakeService.status` verwende `payload` ohne `signer.loads()`.

**Korrektur:** Zeile 66 ruft explizit `payload=signer.loads(cursor,max_age=86400)` auf. Der Befund war ein falscher Positiver aus einem abgeschnittenen früheren Leseergebnis.

## Testdatenbank-Konfiguration

```
HV_DATABASE_URL=postgresql+asyncpg://app_rw:app_secret@127.0.0.1:55432/hausverwaltung
HV_DATABASE_URL_MIGRATOR=postgresql+asyncpg://app_migrator:migrator_secret@127.0.0.1:55432/hausverwaltung
HV_DATABASE_URL_SYNC=postgresql://app_migrator:migrator_secret@127.0.0.1:55432/hausverwaltung
```

`USER` und `HV_OPENAI_API_KEY` wurden auf leere Strings gesetzt.

## Blocker

### Dokumentationsblockade: Reparatur-Teilplan fehlt

Der State verweist auf `steps/04b-assignment-mappings.md`, aber dieser existiert
nicht unter `docs/project-completion/review-remediation/steps/`.

**Prüfung:**
```
ls docs/project-completion/review-remediation/steps/  → (leer)
```

Der gleichnamige Produkt-Teilplan unter
`docs/project-completion/steps/04b-assignment-mappings.md` existiert, darf aber
nicht als Reparatur-Teilplan verwendet werden (Vertrag: "Ausschließlich den
gespeicherten Plan crossreview-remediation-qwen36-49k umsetzen").

**Folge:** Die Plan-Abnahme des 04b-Schritts kann nicht durchgeführt werden,
weil der Plan selbst fehlt. Dies ist eine Dokumentationsblockade, keine
Codeblockade.

### Fachliche Blocker

| Gate | Status | Beschreibung |
|------|--------|-------------|
| V07 Product | Vorgeschlagen | ADR-0007 (WEG-Abrechnungsspitze/Wirtschaftsplan) — fachliche Freigabe fehlt |
| V01 Accounting/Legal | Offen | eligibility/VAT/WEG/reserves/mixed scope — keine Freigabe |
| V03 Accounting | Offen | periods/coverage/basis/tax/schema/target importer — keine Freigabe |

## Nächster Schritt

**Pausiere ehrlich:** 04b ist lokal abgeschlossen, alle 91 Tests bestanden,
aber der Reparatur-Teilplan fehlt.

- Reparatur-Teilplan `review-remediation/steps/04b-assignment-mappings.md` muss
  vom Operator bereitgestellt oder die Blockade dokumentiert werden.
- Keine Fachliche Freigabe für WEG-Änderung vorhanden.
- Keine Fortsetzung zu 04c gemäß Zielvorgabe "keine Fortsetzung der
  Produkt-Roadmap ab 04b".

---

*Erstellt: 2026-10-13 | Aktualisiert: 2026-10-13*
*Quelle: crossreview-remediation-qwen36-49k Plan*
