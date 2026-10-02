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
creation until staged activation. Financial UI selectors and general creation
idempotency are still pending; only the existing cost request has replay receipts.
