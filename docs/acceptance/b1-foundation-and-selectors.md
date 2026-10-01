# B1 foundation and bounded selectors

This increment establishes the schema and authorization primitives for B1. **B1
is not complete and scoped team rollout is not enabled.** Existing financial
resource policies remain in compatibility mode. Migration/backfill, activation,
all-resource policy coverage, source downloads, job revocation checks and scoped
administration UI are the next increment. V05/V06 remain open release gates.

## Implemented boundary

Migration 0032 adds immutable accounting scopes, explicit unit sets, exact grants,
one canonical typed binding for bank accounts/payments/costs/settlements/plans,
and migration-exception storage. It adds no guessed bindings or grants to existing
financial history and changes no booked amounts or original document bytes.
Known original mandates cannot be contradicted. A legacy null mandate can receive
an explicit reviewed classification through the canonical binding without
rewriting that original. This migration does not perform that backfill itself.
Composite keys prevent cross-organization/property bindings and enforce payment
and account sphere equality. Non-whole-property scopes must have reviewed units
before a grant or financial binding is accepted; a failed binding does not freeze
an incomplete unit review.

Grants are append-only except for one-way revocation. The live permission helper
checks authenticated actor, active membership/user, capability, expiry and
revocation. Administrative scope setup does not itself grant financial approval.
Authenticated requests set actor context. The authorization service holds shared
ACL transaction locks for authorized work and exclusive locks for grant changes.

Scope dates describe the **accounting period**, not today's authorization expiry.
A current grant can authorize historical reads/corrections; payment value dates,
cost service periods and settlement/plan periods must fit their selected scope.
Bound identity and scoped mandate definitions cannot silently change later.

New payments on an already bound account atomically inherit its scope and mandate,
including reversal rows. The database checks live preparation capability, plus
confirmation for directly inserted booked rows and the normal draft-to-booked
update. A separate trigger protects bound payment updates against missing preparation
or confirmation capability. The payment service reads the
server-derived mandate back. This does not claim that unbound legacy payments or other resource routes already
enforce the complete capability matrix.
Those routes must be covered before activation exists. Bound draft disposal records
`cancelled_at` and retains the canonical binding; the API/UI show “cancelled” /
“Verworfen”. Discarded drafts cannot be edited or booked. Unbound legacy draft
hard deletion stays compatible. Changing a bound draft's account requires a
replacement draft rather than reparenting its permanent association.

## Selectors

New `/page` endpoints for properties, persons, users and units use keyset order
(normalized display key, UUID), default 50/max 200 rows and signed continuation
cursors bound to actor, organization, endpoint, search and filters. Old list shapes
remain compatible. Property/person lists and the formerly placeholder user list
use these endpoints. Bank-account property selectors use bounded search and retain
an existing selection outside the current results. Account labels come from one
tenant-filtered property join, avoiding an unbounded property list for display.
The UI cancels obsolete queries and ignores their late responses.

## Verification

- PostgreSQL 16 disposable database on localhost:55432 only; migrations reset from
  base through 0032 during pytest. No development database was reset.
- Initial scope tests failed on absent tables, then passed after implementation.
  Scope/paging/RLS/RBAC/tenant/account checks initially passed 64 tests; the
  subsequent scope/paging/account/payment run passed 48. The final combined
  scope/RLS/RBAC/tenant/payment regression passed 75 tests. After extending the
  legacy-classification cases, all 18 focused scope tests passed, including
  reversal after null-mandate classification and contrary known-mandate rejection.
- Coverage includes same-property WEG versus rental grants, duplicate and foreign
  bindings, exact payment/account sphere, immutable evidence, expiry/revocation,
  read-only role constraints, runtime self-grant denial, explicit admin finance
  grants, empty SEV recovery, date boundaries, real payment-service mandate
  inheritance and reversal binding, duplicate sort keys, cursor tampering and
  cross-actor/organization/query reuse.
- Frontend full run initially had 62 passes and one obsolete property-list mock.
  After adapting that mock to the new page contract, all 32 tests in the affected
  modules passed; the other 31 unchanged test results are reused. Hook tests cover
  continuation deduplication and stale query responses. Lint and production build
  passed. The existing bundle warning remains (about 569 kB).
- Browser verification uses `scripts/acceptance/b1_selectors_browser.cjs`, the
  guarded `scripts/acceptance/b1_selectors_fixture.py` dataset, local API18000 and Vite13000. It
  checks 50→55 unique results, desktop/mobile layout, user list, empty contact
  search, bounded property options and selection retained after search. The first
  run exposed a temporary test-server working-directory error; the next exposed
  mobile table overflow, repaired with a local scrolling table container.

The browser functional checks passed with no JavaScript errors or unexpected HTTP
failures; two initial 401 session checks before login are expected. Final mobile
screenshots wait for the navigation transition before capture. See
`evidence/b1-selectors-browser.json`. A two-connection PostgreSQL test proves that
revocation waits for an in-flight authorization lock, then blocks the next effect.
The independent review's payment findings prompted a consolidated lifecycle pass:
mandate inheritance, preparation/confirmation separation, reversal, discard and
blocked resurrection are covered together. The final independent review found no
remaining correctness or security blocker in this increment. Its additional
known-mandate verification request is now covered by a passing regression.
This document does not certify full B1 enforcement.
