# Reviewed accounting scope setup

This is a B1 preparation increment. It does not activate scoped team rollout or
replace the existing organization-wide financial read policies. The UI states
that restriction explicitly. Full enforcement, proven legacy migration,
quarantine, jobs and source downloads remain required before activation.

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
