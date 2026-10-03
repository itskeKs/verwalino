# Manual context proposal foundation

Migration 0101 adds immutable manual accounting-context proposals for logical
intake sources. This records a suggestion for later review; it does not adopt
extracted fields, confirm a context, create a cost, share an original, clear a
duplicate or learn a mapping. B3 remains incomplete.

The extractor currently exposes invoice amounts, dates, vendor and document
number, not verified property, unit, contract or customer identifiers. Therefore
this increment records `method=manual_selection` and the actor's reason. It does
not infer property evidence from a vendor name, supplier address or numerical
confidence, and does not claim period compatibility from unreviewed dates.

Every proposal pins one logical source, an exact reviewed partition membership
and an exact usable extraction revision. Composite foreign keys enforce same
organization and original. New proposals require the latest partition and the latest extraction overall
to be usable at creation. A newer failed extraction prevents selecting an older
successful result. Repartitioning never retargets prior
proposals. Future adoption must explicitly reference these revisions, revalidate
current authority and evidence, and prevent unresolved conflicts from producing
financial effects.

`POST /api/logical-documents/{id}/assignment-proposals` accepts the partition and
extraction IDs, a permitted accounting-scope ID and a reason. It requires an
idempotency key and a quoted predecessor ID in `If-Match`, or `"none"` initially.
The immutable predecessor chain prevents lost edits; replay returns its original
proposal even after repartitioning without making it current again. A changed
request under the same key conflicts. `GET` returns only readable proposals,
newest first, with bounded revision pagination and no hidden-destination counts.

Under the shared intake/ACL lock, creation requires the actual interactive
`app_rw` role, the original uploader's current clean-source preparation rights,
current target-scope read/prepare rights and compatible selected-scope context.
Reads require both current original and destination access. A caller who cannot
read the prior destination cannot overwrite that proposal. Current authority is
checked before replay. Background roles cannot impersonate the human review path
by restoring actor context. Runtime tables allow reads only; updates/deletes are
immutable. Proposal evidence changes the access-coverage manifest. Populated
rollback refuses to destroy it, and no released source columns change.

A different upload scope hint records a blocked `scope_hint_conflict`. The hint
is not treated as authoritative evidence, and a free-text reason does not clear
the conflict. SEV proposals retain an `unsupported_scope_type` blocker. Even an
unblocked `proposed` record is unconfirmed. Shared exceptions and explicit review
will provide conflict handling before any downstream adoption. Stable-identifier
resolution, approved scoped mappings, financial-period review, duplicate handling
and allocation acceptance remain subsequent B3 work.

## Verification

Three initial behavior tests failed before the endpoint existed and passed after
implementation. The expanded assignment/partition/RLS run passed 20 tests;
nine source-registry and dependent migration rehearsals passed. A pagination
assertion passed in a subsequent focused run. The added SEV blocker case initially
hit the existing unit-set grant guard because its synthetic setup omitted owner
units; after supplying a valid reviewed unit/owner set, the case passed. No
production guard was weakened. The fake-Docker script probe passed with the 0101
head gate; no deployment occurred. Independent read-only review found no blocker.

Checks cover source/member/extraction injection, hidden target scopes, revoked
grants, exact replay after replacement, concurrent revisions, conflicting hints,
selected scope, worker/review-role denial, pause, atomic rollback, immutable data,
RLS registration, coverage invalidation and guarded migration identity. These
checks qualify proposal recording only; no adoption or financial claims follow.
