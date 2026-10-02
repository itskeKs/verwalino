# Staged integration of property-source enforcement

Baseline: `9f6ce8f`. This integrates the accepted shared-domain boundary without
exposing an activation command before graph closure and reviewed coverage.

## Organization state and deployment contract

Introduce a tenant-scoped security-state row with PREPARATION, ENFORCED and PAUSED.
Missing rows mean PREPARATION. App users cannot insert, update or delete state.
An immutable transition guard forbids returning ENFORCED/PAUSED to PREPARATION or
deleting that evidence. An internal activation-readiness function initially returns
false and is not executable by the app. Attempts to enter ENFORCED fail until a
later migration implements complete coverage validation and reviewed activation.
Integration tests replace only this readiness function in the guarded disposable
database, install synthetic enforcement, then restore it. No production switch,
configuration flag or unreviewed administrative activation endpoint is added.

Every state read takes the existing shared organization ACL lock. State changes
take it exclusively before row locks. Strict reads remain in PAUSED; prepare and
approve effects fail. PREPARATION retains existing compatibility behavior, so
independently implemented graph components cannot prematurely narrow production.
The strict resolver continues to inspect current tenant/user/domain grants and
immutable source topology. Runtime helpers take only typed IDs/capabilities and
return booleans, not source payloads. Explicit-actor variants remain private.

## First integration (0053): declaration work

The declaration queue principal resolves the immutable queue actor, then in strict
state checks that actor's current import prepare authority. It never substitutes
the process GUC actor. Claim, heartbeat, source phases and effect phases already
call this principal resolver. Existing fenced cancellation remains able to update
safe status/usage and audit when ordinary source rows have become invisible.
Queue commands and identical retries must check current source prepare authority
before returning prior receipts. A paused source boundary denies new work and
cancels pending work through the same durable path.

## Second integration (0054): knowledge Q&A

Knowledge Q&A checks property read plus complete prepared source references at
source preparation, reservation and finalization. The immutable reservation actor
is resolved through a narrow metadata helper before reading the ordinary
query-run row, because future source RLS may withhold that row after revocation.
A private boolean enumerates the expected knowledge graph before ORM reads;
RLS-filtered hits cannot prove completeness. A hidden source among otherwise
readable facts/rules blocks the provider instead of producing a partial answer.
Organization-wide budget sums and expired-reservation cleanup run inside an atomic
reservation command with privileged whole-ledger reads. Counting only visible
rows would understate spend. Strict mode reads a private per-organization budget
policy, derives window and expiry from the database clock, holds the shared ACL
lock before the budget-window lock, and inserts the reservation in that transaction.
The command accepts no caller cap/window/expiry. Missing policy blocks admission.
An invoker insert trigger distinguishes direct app SQL from nested SQL executed
as the trusted reservation-function owner; no GUC can supply admission proof.
The aggregate helper and policy table are unavailable to the application role.
A separate compatibility command accepts existing application settings only while
state remains PREPARATION. Existing provider/model/rate controls remain in place.

Policy configuration is an internal migration/operator prerequisite, with no new
application endpoint. Changes require a current administrator context, exclusive
ACL lock, canonical clock/actor/reason audit, and retained evidence. The future
activation gate must validate a reviewed policy matching configured budget and
reservation settings; this increment does not enable activation. A populated policy
or strict state prevents downgrade. The migration locks organization, user and
query-ledger dependencies with NOWAIT.

The source resolver also recognizes every supported approved-fact subject:
PROPERTY, UNIT, RULE, LEGAL_REFERENCE, ASSIGNMENT and DOCUMENT_RELATION. Derived
subjects must exist under the same property and recursively satisfy their own
source topology. Unsupported subject kinds still fail closed.
Grant/role loss or a pause between bounded phases prevents the next provider call
or answer exposure. The cancellation helper and reservation trigger accept exact source denial
and PAUSED while the actor remains a live member. Durable cancellation records
safe outcome/cost independently
of withheld source payload; no transaction spans provider network I/O.

## Subsequent closure

Attach strict source CRUD policies and write guards, proof-required original
binding, explicit destination approval, storage export checks, whole-input
financial preflights, audit/privacy/export checks and source snapshot closure.
Classify every legacy origin, including already-bound originals, and report
unknown or mixed contexts. Only then replace the internal readiness gate with
coverage-backed activation validation and expose an explicit reviewed command.
No source enforcement or B1 completion claim follows from this staged increment.
