# 03b-intake-jobs — Package 3

## Start / ausschließlich laden

`../contract.md`, `../state.md` und diesen Plan. Letzte Receipt nur bei relevanter
Dependency lesen. Vorgänger: 03a-intake-recovery. Keine Gesamtplanlektüre.

## Begrenzter Auftrag

Claims/Leases/Fencing/Versuche, Outbox-Effektschlüssel und Abbruch vor/nach Commit qualifizieren. A2-/Declaration-Adapter nur an gemeinsam geänderten Grenzen prüfen; unterbrochene Versuche vor Umstellung versöhnen.

## Gezielt zu suchende Einstiegspfade

- `backend/app/workers/workflow.py`
- `backend/app/workers/workflow_runner.py`
- `backend/app/workers/outbox.py`
- `backend/app/workers/intake_extraction.py`
- `backend/tests/test_intake_process_recovery.py`
- `backend/tests/test_intake_extraction_worker.py`

## Verbindlicher Teilauftrag aus dem Oktoberplan

Dieser Vertrag beschreibt den Gesamtumfang des genannten Units. **In dieser
Sitzung nur den begrenzten Auftrag oben ausführen**; andere Stufen bleiben beim
Nachfolger. Bereits gelieferte Module verifizieren, nicht duplizieren.

**B2 — Shared originals, intake jobs and outbox.** P1.1 after B1/A2. Add **new**
`document_ref`, `intake_batch`, `intake_item`, `extraction_revision`, `field_evidence`,
`outbox_event` and `job_attempt` module families, `services/intake_service.py`,
`api/intake_batches.py`, `workers/workflow.py`; adapt A2 and existing
`services/property_document_service.py`, `services/mandate_document_service.py`,
`services/invoice_document_service.py` and declaration worker at their boundaries.
Typed unique registry FKs reference existing sources without duplicating bytes.
Logical children retain container/page ranges; no dangling polymorphic source IDs.
Implement the design's intake create/item/status contracts, per-item idempotency,
quarantine/MIME/malware/page limits, at-most-100 batch manifest and 202/status links.
Extend `core/uploads.py`'s explicit upload-route matcher and proxy limits for the
new per-item endpoint; test oversized bodies before multipart parsing as well as
the shared bounded file reader. A batch is multiple bounded requests, not one
100-file request that bypasses the existing 25 MiB cap.
PostgreSQL jobs/outbox use short scoped claims, leases/fencing, effect keys and
bounded attempts. In **new** `frontend/src/pages/InboxPage.tsx` and
`IntakeBatchPage.tsx`, preserve received/processing/review/confirmed/failed counts
across reconnect. Backfill registry adapters with uniqueness; reconcile interrupted
attempts before moving A2 to shared jobs. Rollback stops claims, leaves status and
source reads intact. A03/A25/A31/A32; RF09/RF10. Tests: mixed transfer failure, tab
close/reconnect, worker kill before/after commit, unauthorized original fetch and
malicious payload fixtures. Keep declaration-specific contracts/evals passing only
where shared mechanics change. No live provider needed; V06 gates disclosure.

## Prüfen und abgrenzen

1. Tatsächlichen Code/Status prüfen; den nächsten **nachgewiesenen** Gap auswählen.
2. Regression + kleinste passende Änderung; gezielte Backend/DB/UI-Tests, echte
   Browserjourney für geänderte Integration. Shared Engine/Auth/Jobs bei Änderung
   proportional breiter prüfen. Nie einen nicht ausgeführten Test als bestanden zählen.
3. Lokalen Teilumfang und noch offene Gesamtunit-/V-Gates getrennt dokumentieren.
   Keine Produktions- oder vollständige Paritybehauptung aufgrund lokaler Tests.

## Verpflichtende Übergabe — nächsten Plan aufrufen

Nach lokaler Abnahme: Receipt `../receipts/03b-intake-jobs.md` schreiben; in `../state.md`
`current: steps/03c-intake-runtime.md` und den Receiptlink setzen. Bei ungeklärtem lokalen
Blocker `current` hier behalten; nur unabhängigem Nachfolger ausdrücklich folgen.
**Jetzt Kontextwechsel nach contract.md ausführen. Erst danach**
`steps/03c-intake-runtime.md` laden und dessen Auftrag bearbeiten. Nicht alle nachfolgenden
Pläne vorab laden. Übergabe für frische Sitzung: „Arbeite ab
`docs/project-completion/steps/03c-intake-runtime.md` anhand von state.md und contract.md weiter.“
