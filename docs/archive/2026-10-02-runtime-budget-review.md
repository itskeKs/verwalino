# Runtime budget attestation and explicit review

Migration 0088 binds safe budget configuration to the actual application transaction
through the isolated `app_review` runtime. HTTP clients supply only the expected
review, displayed digest, acknowledgment and reason. They cannot submit budget
values or invoke the registrar as `app_rw`.

The immutable attestation binds organization, actor, backend PID and xid8. Decimal
caps are canonicalized without floating-point conversion. A second connection,
transaction or actor cannot consume the proof. The trusted registrar does not
acquire the authorization lock held by its calling HTTP transaction; this avoids
a cross-connection cycle while revocation is queued. Its supplied connection
identity is part of the trusted runtime boundary, not a user credential.

An explicit administrator review rechecks current authority under exclusive ACL
locking, compares the expected prior review and policy digest, updates the DB
budget policy, and inserts an immutable review and audit event atomically.
Duplicate keys return the original receipt; changed payloads conflict. Replaying
an earlier review never restores an obsolete policy. Failed caller transactions
may leave a private attestation, which cannot be reused after that transaction.

Checker version 3 requires both trusted runtime attestation and a matching reviewed
policy. Earlier reports remain immutable, and new budget review evidence makes
prior reports stale. Downgrade refuses retained attestations, reviews or version 3
reports. Empty roundtrips preserve version 1 and 2 reports and private table ACLs.

The access preparation page shows exact configuration and requires a reason and
explicit acknowledgment. It retains input and request identity after response
loss, requires reload after conflicts, and fetches current state after a successful
review. An old replayed receipt is never presented as current policy approval.

## Verification

The initial three tests failed before implementation and then passed. The expanded
backend run passed 41 checks; three direct-command fixtures needed the newly
required runtime attestation. A focused follow-up passed those three plus queued
writer progress and migration retention (five checks). Three further checks passed
for unavailable runtime handling, stale snapshots and current administrator replay.
These runs cover all 13 budget tests, combined coverage and grant proposal paths.
Seventeen frontend tests, lint, production build and the fake-Docker deployment
probe passed. The existing approximately 666 kB bundle warning remains.
Independent read-only backend and UI reviews found no actionable production defect.
A local browser journey passed stale-policy rejection, retained reason, explicit
re-acknowledgment, response-loss replay, reload and 390 px mobile layout, with no
page errors or horizontal overflow. Desktop/mobile screenshots were inspected.
The initial browser harness was corrected for React StrictMode's double GET;
retained evidence was preserved and the next run used a changed synthetic cap.
Browser artifacts: `/tmp/hv-runtime-budget-browser.{mjs,log}` and
`/tmp/hv-runtime-budget-{desktop,mobile}.png`.

Local logs: `/tmp/hv-runtime-budget-{red,second,expanded,regression,repaired,final,ui,lint,build,deploy}.log`.

## Remaining boundary

This increment does not integrate attestation with strict live Q&A reservations.
That consumption path, retained-disposition acceptance, bootstrap and audited
activation/pause/resume remain outstanding. Source activation readiness stays
literal false. B1 and the overall implementation plan remain incomplete.
