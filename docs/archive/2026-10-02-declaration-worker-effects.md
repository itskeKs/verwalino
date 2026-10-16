# Declaration worker effect fencing

Status: implemented and verified locally. Baseline 3f88b02; migration 0071.

Worker lifecycle commands alone did not prove that individual output writes carried
the same lease. Migration 0071 adds a private transaction receipt keyed by organization/import,
backend PID and PostgreSQL transaction ID. Only the validated lease-lock command
can mint it, only for the actual app_worker login, after resolving the immutable
queue actor and current source authority. Neither runtime role can read or write
this table. A caller GUC does not establish worker-effect authority.

Strict output triggers require that receipt plus the current locked parent,
matching request/generation/token, current expiry and queue actor. This covers
extraction runs, pass evidence, generated candidates/evidence/issues and document
page counts. The import lock serializes cancellation/reclaim; a stale transaction
or expired lease cannot publish. Typed cancellation/reclaim already execute as the
command owner and retain their narrow cleanup paths.

The private receipts contain no source content and are not historical approvals.
They expire with the lease and are pruned in bounded batches with SKIP LOCKED.
PREPARATION keeps existing compatibility. NEEDS_REVIEW candidate/issue review
writes remain an explicitly separate residual until the atomic review-command
increment; the worker receipt must not become proof of human review.

Acceptance requires raw web/worker-without-receipt denials, valid worker writes,
forged GUC denial, stale/expired/revoked fences, full strict extraction, cancellation
race behavior, migration NOWAIT/round-trip checks and private-table RLS coverage.
Activation remains false and B1 remains incomplete.

## Verification

Six initial red cases reproduced unfenced writes; all six now fail closed for web
and worker sessions without a receipt. The expanded command/RLS run passed 62
cases; two fixture errors were corrected. The pipeline/review/approval compatibility
run passed 195 cases, including valid receipt writes, same-backend next-transaction
rejection, private privileges, wrong lease components, expiration, human-review
forgery denial, pass/evidence parent resolution, migration locks and cancellation
serialization. Three fixture issues were corrected and their final focused run
passed 14 cases with strict pipeline and RLS coverage. These repairs cover expected
principal-denied handling, refreshing PostgreSQL activity snapshots during a real
lock wait, and releasing setup locks before the older migration rehearsal.

The new row trigger runs after the legacy evidence import-ID fill trigger. The
unreleased migration was renamed during development; the disposable database's
old trigger names were aligned before the clean migration rehearsal. No deployed
migration or production data was changed. Worker startup requires the effect
helper and private table. Packaged release gates now expect 0071. The fake-Docker
deployment regression passed; no deployment was run.

Independent read-only review found no new correctness/security blocker and asked
for direct pass/evidence cases; those are included in the 195 passing checks.
Activation remains literally false. Atomic review effects and the rest of B1
remain outstanding.
