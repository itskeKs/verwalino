# Declaration job principal boundary

Status: implemented and independently reviewed on base `03c691f`; acceptance evidence
is recorded in `docs/acceptance/b1-financial-boundary.md`.
This is a bounded B1 prerequisite. Property-source scope classification and full
team activation remain separate unfinished work.

## Queue identity

The draft's `initiated_by` identifies its creator. It cannot identify a subsequent
start/retry performed by another team member. Preserve it as immutable provenance.
Each accepted explicit start/retry instead creates an immutable
`declaration_queue_request`: organization, import, triggering actor, UUID request
key, operation (START/RETRY), previous request, timestamp and request ID.

An import references its current queue request through a composite foreign key.
Requests reference the same import, actor and previous request through tenant-safe
foreign keys. Unique `(organization, import, request_key)` makes the command stable;
a unique predecessor, including one null genesis, prevents history forks. Current
request pointers change only on explicit allowed queue transitions. Automatic
lease recovery keeps the existing request. Principal rows never infer authority
from historical import creators or session role claims.

Start/retry require an Idempotency-Key. Under ACL shared lock and the import row
lock, resolve live writer membership and check prior receipts before state checks.
An identical actor/operation/key replay acknowledges its original receipt and
returns current import status, without requeueing. A changed command conflicts.
A fresh key while already active conflicts rather than silently adopting another
principal. The UI retains a command key after failure and clears it after success.

## Worker transactions and cancellation

WorkerLease carries a request ID in addition to organization/import/generation/
token/expiry. Every worker transaction establishes organization context, obtains
ACL shared lock before the import row lock, checks the lease plus current request,
resolves the actor from database evidence and verifies current active writer
membership. It then sets actor context from that result. Never trust an actor
copied from a lease or the import creator. Use current database time for guards
that wait on locks. Count/budget checks, claims, heartbeat, document load, provider
reservation/checkpoint, transitions and publication use this boundary.

Add a guard immediately before each bounded file read. Release the transaction
before PDF parsing/OCR and provider work. Existing reservation checkpoints guard
provider dispatch and completion. Already dispatched provider work cannot be
recalled; its output must not publish after revocation. Keep already-persisted
conservative budget reservations when cancellation stops further processing.

Permission denial has a distinct terminal signal. Do not mutate state and then
raise LEASE_LOST: transaction rollback would undo the mutation and pipeline `_fail`
intentionally does nothing for lease loss. Instead unwind the effect transaction,
then commit a separate narrow cancellation transaction. Its database helper checks
organization, current request, generation and token, rechecks that authority is
missing, closes RUNNING runs, records safe error/audit evidence, increments the
fence and clears the lease. It must not affect a later queue request. Claim and
heartbeat paths can commit this helper by returning normally. Pipeline and worker
outer boundaries catch denial and invoke it after the failed transaction closes.

Unknown historical queue principals become CANCELLED/JOB_PRINCIPAL_UNVERIFIED;
revoked, inactive or non-writer actors become CANCELLED/JOB_PERMISSION_CHANGED.
Only these permission cancellations can be explicitly retried by a currently
authorized user with a fresh command. Ordinary user cancellation stays terminal.
No old queue principal is guessed or silently backfilled.

## Verification and limits

Verify different creator/starter identity, stable retries and later replay,
concurrent commands, role change/deactivation before claim, before bytes, during
provider work and before publication, durable cancellation, stale-fence immunity,
heartbeat interruption, preserved originals/budget evidence, and foreign-org denial.
Update existing synthetic worker/pipeline fixtures with explicit queue identity;
do not relax production guards to make actorless fixtures pass. Reuse existing
pipeline/fencing regressions and local fake providers. Live provider disclosure
and property-specific source permissions remain outside this increment.
