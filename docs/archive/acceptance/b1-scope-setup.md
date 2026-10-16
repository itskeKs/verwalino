# B1 reviewed scope administration acceptance

Baseline: `6d1d1e5`. This increment supplies explicit administrator setup and
retained grant history. **Full B1 and scoped team rollout remain unfinished.**
See `../operations/accounting-scope-setup.md` for API contracts and limits.

## Delivered behavior

The administrator can create a property/mandate/accounting-period scope, choose
exact units with reviewed owners for SEV, and grant/revoke named team members'
capabilities. Searches for scopes, mandates, owners, units and eligible members
are bounded. Role alone never supplies financial preparation/approval permission.
Reviewed classification of existing non-SEV financial originals requires an
explicit preparation grant and preserves the original financial row.

Same-command create/grant retries return the original result. Different content
under an occupied key is rejected. Revoked grants stay revoked on replay; a new
explicit grant command gets a fresh key. Invalid references and SEV owner-period
validation fail atomically and leave the request key available for correction.

Migration 0033 orders membership changes and user deactivation against in-flight
scope effects with the existing ACL transaction lock. SEV setup requires the
selected owner's actual unit history to cover the scope's full accounting period.
Later ownership changes are allowed. Every SEV financial binding remains rejected
until exact resource/unit/owner semantics are implemented; neither the API nor UI
claims that financial workflow is enabled.

## Evidence

- Initial new API tests failed on missing endpoints; the first SEV-owner regression
  then demonstrated that an unrelated owner could be accepted. The database guard
  now rejects unrelated, incomplete and non-open-ended ownership coverage.
- Combined API/scope/paging/RBAC/tenant run: **61 passed**. After adding the
  unassigned-member selector assertion, final API/scope/RLS run: **34 passed**.
  The first attempt of that final run stopped on a test-only quoting error; it was
  corrected before the successful run.
- Two independent database connections verify that grant revocation, membership
  role changes and deactivation wait for an already authorized effect, then deny
  the next effect despite a stale cached role. Scope tests also cover foreign
  tenants, same-property different scopes, immutable bindings, incompatible
  accounting types and cancellation/reversal behavior inherited from 0032.
- Six affected frontend tests pass, including actual form interaction where the
  first grant response is lost, its retry retains one key, and the next completed
  command uses a fresh key. Lint and production build pass. The existing bundle
  size warning remains, approximately 580 kB.
- Browser acceptance uses `scripts/acceptance/b1_scope_setup_browser.cjs` with
  existing Playwright/Chromium, local API18000/Vite13000 and synthetic users in
  disposable PostgreSQL55432. It deliberately aborts already-committed scope and
  grant replies, retries through the UI and verifies one result for each. The
  clerk sees 404 before grant, 200 after grant and 404 after revocation. Grant
  history retains the revocation and displays the member's name.
- The browser test first exposed an inaccessible exact select label, repaired
  with an explicit accessible label. A later test wait incorrectly matched the
  revoke action cell before the response committed; the test now waits for the
  status cell. The final run has no JavaScript errors or unexpected HTTP failures.
  Two initial 401 session probes before login are expected.
- Desktop and mobile screenshots were visually inspected. Main content measures
  390/390 px at a 390×844 viewport; tables scroll in their own container. JSON
  evidence is `evidence/b1-scope-setup-browser.json`; local screenshots remain in
  `/tmp/hv-b1-scope-acceptance`.
- Final independent read-only review accepted the saved setup increment. Findings
  about SEV ownership and ineligible member choices were repaired and covered.
  Its temporary concern about 422 mapping was resolved by verifying the existing
  request-boundary savepoint/error handler.

All database resets used the guarded disposable PostgreSQL16 database on port
55432. The earlier uncommitted 0033 prototype was explicitly reverted there before
base-to-head verification of the revised migration. No deployment, external
system or development database was changed. V05/V06 and full-resource enforcement,
legacy migration/quarantine, new-organization setup and job/download checks remain
open before rollout.
