# Knowledge query principal boundary

Status: implemented and independently accepted on base `444468a`.
This closes the optional Q&A adapter's live-principal gap, without claiming
property/accounting-scope classification or enabling a live provider.

## Identity and phases

Pass the authenticated user ID into `query_property_knowledge`. Each short tenant
transaction sets that actor context, takes the shared org ACL lock before budget
or domain locks, and rechecks active membership. Active read-only members retain
current approved-knowledge compatibility access. The provider call remains outside
all database transactions. Overview, rule and lexical-search HTTP endpoints use
the existing live-member dependency.

Persist nullable `initiated_by` on `property_knowledge_query_run`, with tenant-safe
user FK and immutable identity. Existing null identities remain historical unknowns;
no inferred backfill. New reservations require the explicit current active actor.
Finalization must match the persisted actor, not trust detached provider/lease data.
No source question, source excerpts or provider answer are added to accounting rows.

Recheck before initial source retrieval, reservation/provider dispatch and answer
validation/return. Never construct or call the provider for a principal denied in
the initial transaction. A denial during a provider call suppresses its answer,
even if its citations validate. Already-dispatched provider input cannot be recalled.

## Revocation accounting

Do not lose a final permission outcome through transaction rollback. After denial
unwinds the effect transaction, a narrow database cancellation helper checks the
organization, reservation ID, recorded actor and current loss of active membership,
then takes the existing budget-window lock and finalizes only a still-RESERVED row.
It writes `QA_PERMISSION_CHANGED`, preserves the full conservative reservation as
estimated cost and content-free audit evidence. It cannot alter an already-finalized
or foreign reservation. Unknown old reservations are never attached to a new user.
Ordinary expiry remains the existing budget mechanism; no row can expire early.
EXPIRED explicitly permits another active member to reclaim historical actorless
and cross-actor reservations, without changing identity and only with fixed
RESERVATION_EXPIRED outcome/error, zero cost/tokens and null latency. The database
clock verifies the persisted expiry timestamp.

The principal trigger must freeze initiated_by, reject new actorless reservations,
require a live matching actor for ordinary finalization, and permit only the narrow
unauthorized cancellation metadata transition. Expiry may cover other users' old
reservations under the existing shared org budget, with active membership and the
recorded expiry time verified. Match lock order ACL → budget window → run row.
Populated downgrade refuses to remove known actor evidence.

## Acceptance

Use fake providers only. Verify actor persistence/immutability, stale authentication
on overview/search/rules/query, active READ_ONLY compatibility, denial before source
retrieval/provider construction/reservation, deactivation during provider execution,
no returned answer, durable conservative accounting, cross-tenant and wrong-actor
cancellation denial, stale/already-finalized immunity, expiration/budget/concurrency
regressions, and unchanged valid citation behavior. Update only actorless synthetic
reservation fixtures; never relax production checks to accommodate them.
