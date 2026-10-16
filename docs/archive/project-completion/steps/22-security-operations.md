# 22-security-operations — Package 22

## Start / ausschließlich laden

`../contract.md`, `../state.md` und diesen Plan. Letzte Receipt nur bei relevanter
Dependency lesen. Vorgänger: 21-ux-search. Keine Gesamtplanlektüre.

## Begrenzter Auftrag

Produktionsbereitschaft separat prüfen: Runtimeflags/Least-Privilege/Secrets, Monitoring/Alarmierung, DB+Blob Backup/Restore, Migration/Forward-repair, Scanner und Provider-Gates. Keine echte Deployment-/Provideraktion ohne separate Autorisierung.

## Gezielt zu suchende Einstiegspfade

- `Dockerfile`
- `infra/`
- `docs/operations/`
- `scripts/test-compose-release-restore.sh`

## Verbindlicher Teilauftrag aus dem Oktoberplan

Dieser Vertrag beschreibt den Gesamtumfang des genannten Units. **In dieser
Sitzung nur den begrenzten Auftrag oben ausführen**; andere Stufen bleiben beim
Nachfolger. Bereits gelieferte Module verifizieren, nicht duplizieren.

**G1 — Performance, fairness and recovery.** Cross-cutting from B2; final measure
after supported M-C–M-F flows. Extend scoped repository indexes/pagination, job
quotas, artifact streaming and frontend lazy loading; add **new**
`backend/tests/performance/`, `docs/acceptance/professional-capacity.md` and a bounded
sanitized fixture generator. Review `scripts/test-compose-release-restore.sh` and
`infra/docker-compose.declaration-test.yml` before composing isolated recovery
tests; do not run production deployment scripts. Use 500 properties/10,000 units,
five years/1 million documents/2 million transactions, 20 active staff plus workers,
and two adversarial isolation orgs. Preserve design budgets: API page p95 <500 ms,
usable review p95 <2 s, 100 text invoices <2 min/100 five-page scans <10 min,
account collection+calculation <15 min and PDFs <30 min on recorded hardware.
Worker kill/restart at stage boundaries must preserve exactly-once effects;
fairness prevents one org exhausting OCR/render slots. Restore DB and blobs,
validate hashes/control totals and safe outbox replay. Provisional RPO ≤15 min,
RTO ≤4 h require measured V08 approval. Migrations/index rollout benchmark on a
copy; rollback removes new workload/flags, preserves evidence. A31–A33; unit tests
cannot establish these capacity/recovery claims.

## Prüfen und abgrenzen

1. Tatsächlichen Code/Status prüfen; den nächsten **nachgewiesenen** Gap auswählen.
2. Regression + kleinste passende Änderung; gezielte Backend/DB/UI-Tests, echte
   Browserjourney für geänderte Integration. Shared Engine/Auth/Jobs bei Änderung
   proportional breiter prüfen. Nie einen nicht ausgeführten Test als bestanden zählen.
3. Lokalen Teilumfang und noch offene Gesamtunit-/V-Gates getrennt dokumentieren.
   Keine Produktions- oder vollständige Paritybehauptung aufgrund lokaler Tests.

## Verpflichtende Übergabe — nächsten Plan aufrufen

Nach lokaler Abnahme: Receipt `../receipts/22-security-operations.md` schreiben; in `../state.md`
`current: steps/23a-capacity-recovery.md` und den Receiptlink setzen. Bei ungeklärtem lokalen
Blocker `current` hier behalten; nur unabhängigem Nachfolger ausdrücklich folgen.
**Jetzt Kontextwechsel nach contract.md ausführen. Erst danach**
`steps/23a-capacity-recovery.md` laden und dessen Auftrag bearbeiten. Nicht alle nachfolgenden
Pläne vorab laden. Übergabe für frische Sitzung: „Arbeite ab
`docs/project-completion/steps/23a-capacity-recovery.md` anhand von state.md und contract.md weiter.“
