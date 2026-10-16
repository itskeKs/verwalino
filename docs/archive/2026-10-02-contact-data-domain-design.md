# Explicit contact-data authorization

Status: locally verified preparation implementation, independently accepted. Baseline 9049c0e.
No source activation or permission backfill is authorized by this document.

## Decision

A Person is a shared contact record containing identity, communication details and
bank information. It may participate in several properties, financial spheres and
roles. Neither one linked property nor a finance grant defines the audience for
all of that contact data. Introduce an independent immutable `person_data_domain`
for an exact person and explicit per-user `person_data_grant` records. Capabilities
are READ, PREPARE and EXPORT. A current read-only member cannot exercise PREPARE.
Administrator configuration does not imply contact data access. Do not derive a
grant from a name match, property relationship, role or financial entitlement.

Creating an explicit domain/grant records contemporary authorization, not a claim
about historical custody. Administrative lookup of a known person ID must return
only existence, not otherwise hidden PII. Metadata labels are derived as `Contact <person UUID>` and checked by the database.
Commands take a controlled `reason_code`: INITIAL_SETUP, TEAM_ACCESS, TEMPORARY_ACCESS
or REPLACEMENT. No client label or free-text reason is accepted or copied into audit.
This closes the accidental metadata disclosure path identified in design review
and reduces typed fields. A display name, if needed, comes only from an ordinary
authorized Person read and is never persisted as administration metadata.
The domain person ID, grant actor/capability/reason code and database timestamps are
immutable; revocation is one-way. Replacement requires revocation plus a new
receipt. Actor-scoped idempotency keys reject changed requests and replays never
restore revoked grants. Shared/exclusive organization ACL locking follows the
existing property/financial grant protocol.

## Coherent preparation increment

Migration 0068 adds the two tables, tenant/composite identity constraints, bounded
boolean capability checks, current administrator write guards, canonical audit,
known-ID target lookup, deterministic cursor pages and idempotent setup APIs. The
administration UI explains that these records prepare contact access and do not
activate restrictions. Give no default/self grant merely because someone is an
administrator. Test negative tenant/actor/role cases, exact grants, expiry,
revocation, concurrent replay, immutable evidence, paging, safe rollback and a
positive administration journey. Existing contact readers remain in the documented
compatibility stage for this increment; activation readiness remains false.

## Required enforcement follow-up

New unassigned contacts need immutable actual creator custody rather than guessed
legacy ownership. Once explicitly published to a person domain, only domain grants
may satisfy contact access; creator fallback must not restore revoked access.
Historical creator NULL stays unknown. Person, tenant/owner/vendor profiles, lists,
selectors, direct details and copied audit payloads need the same person boundary.
A profile link cannot grant PII access or bypass a source original's independent
rights. Linking an existing contact into a relationship needs current source rights
and the destination's rights; unknown historical links require reviewed coverage.

Before enabling those policies, enumerate every expected contact needed by account
computation, account statements, stored outputs, exports and privacy operations.
Their completeness predicates must use trusted expected identities so hidden
profiles/names cannot become anonymous addressees, missing obligations or partial
exports. Keep pure calculations unchanged and financial grants independent. Property
master or legal-source access must not accidentally require contact rights where
no contact values are disclosed; add contact checks at actual consuming boundaries.
Privacy additionally requires its existing complete financial/history context and
current administrator authority. Contact PREPARE alone must not bypass erasure
retention rules or authorize privileged privacy state transitions.

Prepare migration/coverage evidence without inventing creators or sharing authority.
Retain all historical rows and source hashes. Strict rollout must remain impossible
until this complete graph, worker lifecycle integrity and remaining indirect views
are independently verified. B1 and all later milestones remain unfinished.

## Acceptance evidence

See [B1 preparation acceptance](acceptance/b1-property-data-preparation.md) for
backend, migration, UI and real-browser evidence. No activation claim is made.
