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

## Proposal interface

The source view now offers manual selection from currently permitted preparation
scopes, a reason, exact evidence/predecessor submission and the latest 20 readable
proposal revisions with destination labels and conflict explanations. Scope labels
are joined under the existing source-and-target RLS. The form never preselects an
accounting context. A replaced partition/extraction or concurrent proposal keeps
the draft and requires explicit adoption of the current basis. Unknown responses
retain the exact key/body for replay across read failures; unavailable reads hide
stale details. Late responses after changing sources cannot affect the new view.

Eligibility requires a succeeded/needs-review revision with a non-null payload,
matching the backend contract. A valid incomplete-text/OCR-limit outcome with no
payload cannot be proposed. Initial asynchronous loading does not create a false
stale-draft conflict. Saving a partition refreshes the proposal context.

The UI checks cover 24 source, partition and assignment cases (23 passed in the
expanded run and the eight source-page cases passed after correcting an async
test selector). The six backend proposal tests pass with scope labels. Build and
lint pass with the existing application chunk-size warning. Independent review
identified and verified repairs for payload eligibility and initial loading.

Actual local API/database browser qualification created a synthetic property,
WEG scope and explicit preparation grant through the API, then uploaded a text
PDF, injected a clean inspection, performed actual isolated extraction, reviewed
its partition and submitted a manual proposal by keyboard. Reload retained the
history without automatically selecting a scope for a new proposal. Desktop
1440×1000 and mobile 390×844 had no horizontal overflow; screenshots were visually
inspected. A simulated source denial cleared the proposal and source evidence.
There were no page errors, pre-fault console errors or development overlays.
The initial browser preflight found no scopes in the shared login fixture; the
scenario therefore created its own local synthetic setup. API and Vite were
stopped after qualification. This is not production/scanner qualification.
