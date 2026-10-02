# Internal inspection outbox delivery

Migration 0097 adds a private, fenced consumer for `intake.inspected` events.
Successful inspection proposes one unreviewed whole-source logical document.
This is a source candidate; it does not confirm costs, completeness or assignment.
Existing manual whole-source candidates are reused without changing their origin
or reason. A source already containing 200 children records a blocked delivery.

The event binds to its exact completed inspection attempt through the immutable
effect key. Existing events are backfilled through the same provenance check.
An earlier blocked inspection cannot adopt a later successful inspection. The
recorded uploader and scope are restored and current authorization is checked
under the intake locks before creating a candidate. Revocation cancels delivery.
Nonclean inspection events are recorded as skipped.

Only the actual worker database role can claim or apply deliveries. Claims use
short transactions, 120-second leases, fencing tokens and three lifetime attempts.
Expired attempts are closed before reclaim; stale completions have no effect.
Candidate creation, its logical-created event, delivery completion and attempt
result commit together. No external service or message is involved. The optional
shared runner exposes this consumer through the explicit `outbox` stage.

Delivery and attempt evidence invalidate access coverage. Pending deliveries
block disposition acceptance. Populated downgrade refuses evidence deletion;
an empty downgrade restores the prior functions while preserving their identity.
Logical metadata now exposes whether a candidate originated manually or from
inspection. Terminal cancellation and blocked outcomes have no automatic retry.

## Local verification

The disposable PostgreSQL 16 suite initially passed 52 checks and exposed one
older acceptance fixture that did not drain the newly pending outbox event.
That fixture now asserts the pending-work gate, consumes the skipped event and
captures fresh acceptance evidence. Independent review also found and prompted
the 200-child cap repair. The final focused suite passed all 10 checks, including
all nine outbox tests and the repaired acceptance regression. Unchanged successful
registry, extraction, logical-document and RLS checks were reused.

Coverage includes competing consumers, manual-candidate reuse, expired and stale
claims, three-attempt exhaustion, revoked rights, old blocked events, transaction
rollback, runtime-role denial, shared dispatch, migration backfill and saturated
sources. Focused independent re-review found no remaining concrete issue. The
fake-Docker deployment probe passed with head 0097; no deployment occurred.

Intake extraction, located evidence and full shared-runtime qualification remain
outstanding. Real scanner qualification is unavailable locally. This increment
does not accept B2 or the complete implementation plan.
