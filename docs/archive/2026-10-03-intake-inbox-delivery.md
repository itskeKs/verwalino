# Durable intake inbox delivery

This increment implements the B2 inbox over the accepted 0093 quarantine backend.
It does not accept B2 or authorize rollout. Original inspection is not cost,
assignment or account confirmation.

## Behavior and authority

Migration 0094 adds bounded, uploader-private batch headers with current scope
access checks. Signed cursors bind pagination to the organization and actor.
Status includes current upload, retry and export availability from the existing
authorization helpers. The migration preserves function identity and restores
the preceding status function on downgrade without discarding intake records.

The inbox submits at most 100 files using separate bounded requests. Creation
and retries retain their exact idempotency key and payload after uncertain
responses. A persisted batch can be reopened after closing the tab; missing
files are reselected and checked against their declared hash and size. Counts
come from persisted status and active jobs are polled without overlapping reads.
Authorization failures clear cached status; temporary failures label it stale
and disable actions. Download links are hidden while status is unavailable.

The responsive detail view distinguishes missing, received, processing, review,
confirmed and failed items. Retry requires a reason; quarantined originals do
not have download links. The retry field has a visible border and focus state.

## Local acceptance evidence

Environment: disposable PostgreSQL 16 on localhost, isolated API and Vite,
synthetic PDF fixtures, Chromium, existing installed frontend dependencies.
All checks below completed with exit status 0 against this increment unless
otherwise stated. No production service or live provider was used.

| Check | Coverage and result |
| --- | --- |
| Backend focused pytest, `backend` | 3 passed: private paged headers/current actions, 0093 populated rollback guard and empty roundtrip beneath 0094, registry rollback beneath both newer migrations. Reused after frontend-only repairs. |
| Intake UI Vitest, `frontend` | 6 passed, including lost-response key reuse, recovery, retry and clean status followed by failed refresh hiding the cached download. |
| ESLint, `frontend` | Passed after action repair; subsequent change only adds static presentation classes. |
| TypeScript and Vite build, `frontend` | Passed after final presentation change. Existing chunk-size warning remains (about 690 kB main JavaScript). |
| Fake-Docker deployment probe, repository root | Passed at required head 0094 with ambient provider key removed. The initial run with an inherited key failed its environment guard; the corrected invocation passed. No deployment occurred. |
| Browser upload/recovery journey | Passed separate requests, lost receipt after commit, second transfer failure, tab close/reopen, exact-file recovery, actual worker blocking on unavailable scanner, explicit retry and inbox rediscovery. No page errors or horizontal overflow at 390 px. |
| Browser visual follow-up | Passed computed input border, editable retry field and mobile width after the visual repair; desktop/mobile screenshots inspected. |
| Independent read-only review | Found the disabled-fieldset anchor issue; fixed with explicit link suppression and regression test. No other concrete blocker reported. |

The full browser journey and backend checks were reused for the presentation-only
follow-up. A working ClamAV installation is still unavailable, so the browser
verifies fail-closed behavior, not clean-file malware qualification. Logical
children, extraction evidence, existing-worker adapters and outbox delivery
remain outstanding for B2, followed by B3–G and their release gates.
