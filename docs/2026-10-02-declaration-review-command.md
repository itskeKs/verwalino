# Atomic declaration review decisions

Status: implemented and verified locally, baseline d3dbba2, migration 0072.

The existing NEEDS_REVIEW compatibility path lets raw preparers stamp candidates,
increment draft revisions, and insert review audit events independently. Replace
those three effects with one narrow command. It resolves the current human actor,
locks ACL then import and candidates, verifies revision/source authority and the
closed decision envelope, and owns timestamps, candidate versions, revision and
canonical audit. Exact replay has no effects. The worker login cannot invoke a
human review. A failing statement or later caller-transaction error rolls back the
whole decision. Bulk actions retain the conservative risk/evidence/comment policy.

The API keeps canonical correction parsing and complete Python graph revalidation
in the same caller transaction. This increment does not make arbitrary issue patches
or complex correction semantics trusted database proof. Managed issue resolution
still needs its own authoritative boundary; strict compatibility for those issue
writes remains explicit. Activation stays false, and B1 remains incomplete. No
additional service credential or caller-controlled GUC establishes review proof.

Acceptance: direct raw candidate/revision/audit forgery denied; valid individual and
bulk commands atomically stamp the actual actor, advance revision once and audit
without values; stale revision, foreign/hidden candidates, current authority loss,
worker identity and malformed envelopes fail closed. Preserve existing correction,
replay and issue-revalidation API behavior. Rehearse migration refusal/round-trip
and NOWAIT dependencies; independently review the boundary.

## Boundary and compatibility

A changed command reopens every previously resolved managed graph issue before
returning. A direct caller cannot inherit stale resolutions simply by skipping the
Python validator. The service expires stale ORM state, recomputes the complete graph,
and preserves an earlier resolution timestamp only if that graph proves the issue
resolved again. The separate raw issue-write compatibility is still a known gap.
CORRECTED payloads are object-shape checked in SQL; field/payload canonical semantics
remain application validation. Do not treat the command as full semantic proof.

The existing optimistic API replay contract is unchanged: an identical decision at
the current revision has no effect, while the original now-stale revision returns
409. This is not an idempotency-key receipt for lost-response replay. A durable
key/digest replay contract remains a separate requirement; no such guarantee is
claimed here. Stored decision audit contains IDs, decision and resulting revision,
never corrected text or source values. The worker's inherited function privilege
does not authorize it: the command checks the actual login is app_rw.

## Verification

Three initial red tests reproduced independent candidate/revision/audit forgery.
The first focused run passed 25 cases and exposed resolution timestamp churn;
preserving timestamps only after successful revalidation fixed it. The expanded
run passed 102 cases with two fixture expectation errors subsequently repaired.
Review/approval/source compatibility passed 153 cases. Its three failures were a
test UUID-to-text binding, an old strict fixture inserting worker output directly,
and a transient disposable PostgreSQL connection/SSL-upgrade rejection. The UUID
binding and fixture were corrected; the fixture now uses migration-owned pending
extraction output followed by the real strict HTTP review before publication.
The final 34-case run passed all three reruns plus authority races, strict HTTP,
atomic rollback, concurrent revision conflicts and migration history/lock checks.

Independent review accepted this bounded authority/audit increment with the stated
semantic residuals. No frontend contract changed. Activation remains false and B1
and the later milestones remain incomplete.

The final two-candidate bulk regression passed: one revision and one audit per
candidate. The fake-Docker deployment regression also passed with packaged head
0072. No deployment or external mutation occurred.
