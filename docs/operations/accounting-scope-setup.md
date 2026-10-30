# Reviewed accounting scope setup

This remains a B1 preparation increment; scoped team rollout is not activated.
Migrations 0034–0035 restrict explicitly bound financial resources and their
invoice jobs. Unclassified resources retain compatibility behavior. The UI
continues to state that full rollout is unavailable. Aggregate integration,
proven legacy migration, quarantine and complete document/job coverage remain
required before activation.

Administrators use `/berechtigungen`, linked from the user list, to create an
immutable scope and explicitly grant/revoke team capabilities. Scope creation
selects a property, optional mandate, inclusive accounting dates and either the
whole property or a reviewed unit set. SEV requires owners for every unit. The
ownership history must cover the complete selected period, including any seamless
adjacent ownership records. Open-ended scopes require open-ended ownership at
the time of review. Later ownership changes remain possible; immutable scope
units retain the reviewed snapshot.

**SEV financial binding is unavailable.** Migration 0033 rejects it at the database
boundary until each financial adapter validates exact source unit and owner
semantics. A prepared SEV scope/grant must not be mistaken for an enabled SEV
financial workflow. No permanent ownership-history freeze is installed.

## API and retries

- `GET /api/accounting-scopes/page`: bounded, signed cursor pages with optional
  `property_id`; normal users see only explicitly granted scopes, administrators
  may inspect configuration.
- `POST /api/accounting-scopes`: administrator-only reviewed creation. Supply a
  UUID `Idempotency-Key`. Same actor/key/content returns the original scope;
  changed content returns 409. Invalid reference/ownership validation rolls back
  the whole command, including its request identity.
- `GET /api/accounting-scopes/{id}`: scope plus exact unit/owner selection.
- `GET /api/accounting-scopes/{id}/grants/page`: administrator-only grant history,
  including team display names, expiry and retained revocations.
- `POST /api/accounting-scopes/{id}/grants`: explicit capabilities including
  `read`, reviewed reason and optional timezone-aware expiry; UUID request key.
  A replay after revocation returns the revoked original and never reactivates it.
  Replacement requires a fresh explicit command after revoking the previous grant.
- `POST /api/accounting-scopes/{id}/grants/{grant_id}/revoke`: one-way, audited,
  idempotent revocation; scope and grant must agree.
- `POST /api/accounting-scopes/{id}/bindings`: administrator plus explicit
  `prepare` capability, typed resource ID and reason. The original must match the
  property, mandate, period and accounting type. Identical classification replays
  its existing binding; contrary classification returns 409. No original financial
  row is rewritten. Payment classification first requires its exact bank binding.

The scope UI retains the command key after a lost reply and starts a new key only
when the input changes or a previous command has completed successfully. Member
selection uses `GET /api/users/page?eligible_for_scope=true`, excluding inactive
and unassigned users. Scope, mandate, owner and unit choices use bounded searches.
The new `/api/mandates/page` and `/api/owner-profiles/page` contracts share the
existing 50-default/200-maximum actor/query/filter-bound cursor semantics.

## Concurrency and rollback

Scope setup/grant mutations take the organization ACL transaction lock. Migration
0033 makes membership changes and user activation/deactivation use that same
exclusive lock. Already authorized effects holding the shared lock finish first;
the next effect rechecks current role, active status and grant. This does not by
itself claim every existing financial or worker path already calls authorization.

SEV owner review and ownership writes share a per-unit lock so an in-flight
ownership edit cannot race creation of false evidence. Setup also validates
references in the current tenant and records an audit event in the same commit.

Production rollback must pause exposed scoped writers, retaining scopes, grants,
bindings and original financial facts. Alembic downgrade is for disposable
verification only. Reverting to organization-only access is not a valid rollout
rollback.

## Bound resource and invoice job boundary (0034–0035)

Canonical financial rows, their calculation lines, linked invoice originals,
coverage declarations and financial audit entries require a live exact scope
grant. Administrator status alone does not grant financial access. Normal
navigation can list all individually authorized scopes; a server-selected
calculation context restricts sources and units to one exact scope. Computation
rejects unclassified property inputs instead of treating invisible inputs as zero.
Confirming costs and finalizing runs/plans require separate confirm/approve
capabilities at the database boundary.

Invoice attempts persist an immutable initiating actor and requested scope.
Claims and completion recheck current membership, activation and scope grant.
Revocation, deactivation or role changes cancel the attempt without passing
source bytes to extraction or saving extraction results. A narrow fenced definer
function records cancellation metadata and an audit event; an older canceled
attempt cannot overwrite a newer result. Existing attempts without a proven
initiator are canceled and require an explicit new retry.

This boundary does not certify all computations, exports or retention workflows.
Exact contexts for statements/derived totals, correction inheritance, source
registries, privacy retention and staged enforcement remain activation blockers.
Never roll back to organization-wide visibility for already classified resources.


Payment aggregate adapters (0036) require an exact context once relevant classified
finance exists. Owner statements and WEG paid-advance derivation use the explicitly
chosen plan. Other legacy statement paths return 409 until supplied a validated
scope. Privacy operations (0037) refuse incomplete retained-reference visibility;
exports require export grants. The legacy organization-wide deadline monitor is
unavailable for classified finance until its scoped adapter is implemented. These
errors prevent partial counts or hidden retention records from becoming decisions.


Creation API requests may supply `accounting_scope_id` (0038). It is required once
the property has classified finance. The server derives the scope's mandate and
rejects wrong property/type/period/unit before writing; the database independently
creates and validates the binding atomically. WEG runs inherit the chosen plan's
context. Corrections retain the original context; moving a correction to another
scope is not supported. Existing unclassified properties retain compatibility
creation until staged activation. General creation idempotency remains pending; only the existing cost request has
replay receipts. Financial UI selectors are described below.


Financial creation forms now expose the exact scope selector. The page endpoint
accepts `capability`, `scope_type` and `financial_creation`; these filter choices,
not authorization of effects. Every save independently validates live access.
Property and bank-type changes reset selection. General creation idempotency and
full staged rollout remain outstanding; UI scope selection does not activate them.

### Read-only statements and open items

Mietkonto and the tenant open-items overview offer accounting scopes using live
`read` grants. Preparation permission is not required. Choose a rental scope for
a lease and a WEG scope for an owner; the requested interval must fit the scope.
An owner statement with a selected economic plan can inherit that plan's scope.
An explicit different scope is rejected, as is a period outside the scoped plan.

The open-items overview is a single database transaction. A failed or denied
subject blocks the result instead of disappearing from a partial debt list. A
changed selection clears old results, and query controls stay fixed until the
response arrives. An omitted scope works only for legacy contexts whose inputs
are not classified; administrative role alone never authorizes scoped reports.

### Scoped deadline overview

Select an accounting scope on the dashboard or deadline monitor to recover a
classified-finance overview. A reference date after the scope ends is allowed so
outstanding deadlines can still be inspected; dates before the scope starts are
rejected. Rental obligations stay inside the scope's term and units. WEG scopes
do not display tenant-debt totals. Partial fiscal or next-plan years remain
blocked pending reviewed coverage. Scoped document retention is explicitly
unavailable until source assignment can prove complete document coverage.

### Scoped dunning

The Open Items shortcut, scope retention and draft discard journey is now a [browser feature](../../features/scoped_dunning.feature).

Tenant dunning selects a rental accounting scope and a matching lease whose term
overlaps the requested period. Open Items carries that scope into the draft form;
the form checks the live preparation permission before enabling creation. Failed
or mismatched inherited scopes remain visible and block submission until the user
explicitly changes the selection. Owner dunning in a WEG scope requires a bound,
finalized economic plan covering the requested period. Payment-only scoped owner
dunning is not yet available.

Preparing a draft requires PREPARE, issuing requires APPROVE, and PDF export
requires EXPORT, each with live READ access to that exact scope. Organization
administrators also need these grants. Discarding a scoped draft preserves the
immutable context and audit record and prevents later issue. Existing unclassified
legacy drafts retain their deletion behavior.

Escalation only counts issued letters in the selected scope. Unresolved legacy
issued letters block escalation, and legacy letters for classified financial
subjects are quarantined from ordinary list/detail/export access. A proven legacy
review/backfill workflow remains outstanding; do not invent historical provenance
or remove the context table to restore access. Migration 0039 refuses downgrade
when scoped notices exist. Repair forward with sources and grants preserved.

### Proven legacy payment review

In a scope's setup page, choose **Altzahlungen prüfen** after reviewing and binding
the original bank account. Active administrators also need that scope's PREPARE
grant. The preview lists still-unclassified payments on those accounts and explains
which ones require individual review. Select up to 200 proven rows, enter a reason,
and apply the selection. A conflicting or changed row rejects the complete batch;
reload the preview before changing the selection. Unknown period allocations are
preserved, not promoted to verified attribution.

If a response is lost, retry the unchanged selection and reason: the same request
key returns its original receipt without another financial effect. Permission loss
or expiry blocks both application and replay. The UI retains the command after an
error; deliberately changing the command uses a new key. Partial-unit/SEV and
unbooked sources remain unsupported by this automatic proof. Other resources do
not become classified merely because these payments were reconciled.

Migration 0040 records immutable receipt items referencing the exact payment
bindings. It does not modify original booked records and refuses populated
downgrade. Preserve the evidence and repair forward; never delete bindings or
rewrite historical transactions to restore compatibility behavior.

### Scoped effect pause and recovery (0041)

The **Bereichsbezogene Finanzaktionen** control on the scope administration page
pauses PREPARE, CONFIRM, APPROVE and SEND for already classified financial scopes
throughout the organization. An active administrator records a reason for every
pause or resume. The control does not itself grant financial access. READ and
EXPORT retain their existing exact-scope requirements; grants, bindings, money
and released results are preserved. Grant administration remains available.

This is **not an organization-wide financial freeze**: unclassified legacy writes
and invoice jobs retain their compatibility behavior. Full enforced team rollout
is still unavailable. Do not describe this control as stopping every financial
workflow, source import or background job. Scoped invoice attempts recheck their
persisted principal before claim and before storing results; a pause at either
check cancels the attempt without applying extraction results. An attempt already
canceled stays canceled after resume. A scoped effect transaction already holding
the ACL lock finishes before the pause is accepted.

Resume restores evaluation of current grants, not historical grants. Revoked or
expired access remains denied. Retry an unchanged failed command to recover its
immutable receipt; a retry never reapplies an old pause after a later resume. The
UI therefore reloads the current state after every successful receipt. A concurrent
change rejects a stale expected predecessor; reload and review before issuing a
new command. A failed status load is unavailable, never an assumed active state.

The append-only event chain records actor, reason, predecessor and request key.
Migration 0041 preserves the existing capability function's policy references and
refuses to remove recorded control history. Use an audited resume rather than a
populated schema downgrade. Empty downgrade/upgrade has been rehearsed on the
isolated disposable database; it is not authorization for a production rollback.

### New scoped mandate documents (0042)

On a mandate's **Zugängliche Dokumente** page, select a preparation-authorized
scope from that exact mandate before uploading a new source. Record why the
entire file belongs to the selected scope. A mandate spanning several properties
does not prove that its documents belong to any one property's scope. The
explicit scope and reason are retained with the original file metadata.

READ permits listing; EXPORT is checked separately before opening a download
stream. An administrator without the exact financial grants cannot bypass these
checks. Scope pause prevents new scoped uploads and archiving while preserving
currently granted read/export. In-flight effects share the ACL lock; expiry is
also rechecked at database insertion after storage returns. A failed insertion
may leave an unreferenced content-addressed blob, but commits no source record.

Classified originals cannot be edited, moved to another scope, or hard-deleted.
Choose **Archivieren**, give a reason, and use **Archivierte Unterlagen anzeigen**
to inspect retained metadata. Original bytes remain available with EXPORT. Archive
records retain actor/reason evidence; the same retry returns the existing archive,
while a changed reason conflicts. This is evidence preservation, not an inferred
retention duration or a general disposal policy.

Lists explicitly contain only accessible documents and cannot establish a complete
mandate file. The array endpoint signals `X-Document-Visibility: accessible-only`;
`accounting_scope_id` optionally selects one authorized same-mandate scope and
`include_archived=true` includes its retained archives. Failed UI refreshes hide
stale rows and report unavailable state instead of a successful empty result.

Unclassified legacy documents keep their current organization-level compatibility
access and deletion behavior. They are not classified automatically or through a
raw context update. A later legacy review must verify original bytes and provenance;
archived and unclassified sources remain part of the outstanding historical-coverage
work before enforced rollout. This increment does not cover declaration extraction
jobs or property legal-source access. Populated 0042 downgrade refuses to erase the
classified boundary; preserve originals and repair forward.
