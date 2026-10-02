# B2 durable quarantine delivery design

Status: locally verified backend increment after 12e0eb9, through migration 0093.
This does not accept B2 or enable a production rollout.

Deliver one local upload → quarantine → inspection → clean/rejected/blocked path
before extraction, assignment or accounting effects. A batch has an immutable
1–100-item manifest with unique client keys, expected SHA-256/size, MIME and filename.
Each item is transferred in a separate bounded multipart request. Batch creation
and item replay preserve the original receipt; conflicting identity or bytes fail.
Scope hints are immutable, explicitly authorized context, never financial bindings.
Batch metadata is private to its uploader and requires current scope read authority
where selected. Prepare additionally requires a current writer, selected-scope
capability and unpaused source control. Administrators gain no implicit file access.

A new intake original retains one content-addressed byte object and immutable
custody metadata. As with migration 0082, a trusted app_review upload admission must
attest the freshly received bytes and exact org/actor/batch/item/hash/size/MIME/storage
payload before the main transaction may insert the original. A raw app_rw caller
cannot acquire another user's existing blob by knowing its hash. The admission
connection must not wait on the main transaction's ACL lock; insertion rechecks
current authority. Shared blobs are never deleted to compensate for DB failure.

Original, inspection job and received outbox event commit atomically. Database
commands own jobs, attempts and inspection results; web SQL cannot set clean.
Workers claim with short transactions, leases and fencing generations. They recheck
the recorded initiator and scope before reading bytes and committing a result.
Scanner/parser work runs outside database transactions. Replaying an upload never
restarts inspection. Attempts are bounded; explicit retry creates linked history.
Cancellation and revoked authority stop future effects without discarding originals.

A fourth typed registry adapter points to the intake original. Registry disclosure
requires a committed clean inspection plus the live uploader/scope boundary.
Quarantined originals have status only: no download, preview or field extraction.
Clean remains an inspection result, not assignment, confirmation or export approval.
Verified page counts remain tied to exact bytes and inspector version for later
logical children and field evidence.

Extend coverage-manifest dependencies and source entries for intake custody, scope,
inspection and work state. Queued/running work must block lifecycle acceptance.
A snapshot predating an intake change must fail currentness checks. All intake
commands follow ACL → financial-input locks before domain locks, matching lifecycle
and coverage operations. Any retained quarantine disposition must prove denial and
receive the existing explicit retention acknowledgment; do not infer readiness
from an empty audience alone.

Acceptance: concurrent replay, conflicting bytes, partial upload/reconnect, scanner
unavailable/detected/timeout, claim expiry and stale completion, revoked actor/scope,
pause, foreign access, raw mutation/admission forgery, registry visibility and source
hash preservation, coverage staleness/active-work blocking, RLS coverage and guarded
migration rollback. Test raw-body rejection before multipart parsing. Real ClamAV
with maintained signatures remains an integration prerequisite; a missing scanner
must be recoverably blocked and cannot be described as malware-qualified.

## Implemented contracts and evidence

`POST /api/intake-batches` requires an idempotency UUID and the bounded manifest.
`POST /api/intake-batches/{id}/items` receives exactly one file and client item key,
returning 202 with immutable receipt/original/job identities. `GET` on the batch
returns signed 50-default/200-maximum cursor pages, aggregate counts and a revision
digest for reconnect. Received receipts never imply a current clean status; clients
must read the batch. Confirmed count remains zero because this increment has no
financial effects. `POST /api/intake-batches/retry` requires a current generation,
reason and idempotency key. Only blocked/cancelled jobs with fewer than three
lifetime claims may retry. Rejected inputs require corrected new intake.

`GET /api/intake-batches/originals/{id}/content` requires clean inspection and the
uploader's current read boundary, plus scoped export where a scope hint exists.
It verifies bounded size/hash before responding as an attachment with no-store.
Coverage projects read/export through the same predicates used by actual access.
No preview or extraction endpoint is added. Clean means ready for the next review
stage, never an accepted expense. Original, received outbox event and job are atomic;
inspection/cancellation events persist separately with unique effect keys.

Run the sequential worker using `python -m app.workers.workflow --organization
<UUID>` with the existing dedicated worker credential configuration; `--once`
processes one item per supplied organization. It uses 120-second leases, preserves
expired attempts, fences stale completion, loads bounded bytes under a fresh live
lock, then closes the transaction before scanner work. The private inspector's
90-second process deadline remains unchanged. No worker service was deployed.

Verification on the disposable PostgreSQL instance:

- Initial missing-route tests failed as expected; creation/upload then passed.
- The combined intake, upload-limit, inspector, registry, RLS and source-lifecycle
  run passed 83 tests (`/tmp/hv-intake-quarantine-regression.log`).
- Export, recoverable retry and migration follow-ups passed three tests
  (`/tmp/hv-intake-quarantine-export.log`).
- Coverage read/export projections and the affected migration round trip passed
  four tests (`/tmp/hv-intake-quarantine-coverage.log`).
- Fake-Docker deployment-script checks passed at required head 0093.
- Independent review required recoverable retry and exact export projection;
  both are implemented and covered. No remaining concrete review blocker.

No frontend changed in this increment. Inbox UI, extraction/evidence revisions,
logical page children, adaptation of existing extraction workers and eventual
outbox delivery remain B2 work. Scanner-clean tests used explicitly synthetic
inspection results; real ClamAV/signature operation and Linux resource viability
remain unverified. Missing ClamAV produces blocked work with bounded explicit retry.
