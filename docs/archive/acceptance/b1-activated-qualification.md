# B1 qualification through actual activation

Date: 2026-10-02. Baseline: accepted lifecycle commit `9cea16b` (migration 0091).
Status: locally accepted for the supported B1 scope after independent read-only
review found no remaining essential gap.

Scope: local qualification of the currently supported WEG/rental application,
including explicit bootstrap, restricted reads and effects, and durable revocation.
This is not a production/team rollout or a V05/V06 decision.

## Requirement and evidence map

| B1 requirement | Implementation / accepted evidence | Final integration evidence |
| --- | --- | --- |
| Separate organization, mandate, property, accounting type and effective unit scope | `accounting_scope`, immutable typed `accounting_scope_binding`, `scope_grant`; `accounting-scope-invariants.md`, foundation/scope-setup acceptance | Real WEG and rental mandates/scopes on the same property; independent explicit grants |
| Prepare/confirm/approve/send/export capabilities; admin is not financial permission | `core/rbac.py`, `services/authorization_service.py`, DB capability predicates; scope-grant and financial-boundary evidence | Administrator cannot create the first account before an explicit financial grant; clerk prepare selector omits the other sphere and approve selector is empty |
| Exact account/payment coherence without reparenting booked history | Composite tenant/property/account constraints and immutable canonical bindings; `test_scope_grants.py` accepted mismatch/date/immutability evidence | Foreign-sphere account creation/payment rejected; attempted account replacement denied and the payment's canonical scope stays unchanged |
| Proven migration and quarantine, explicit team review | Reviewed migration/reconciliation, source classification/disposition and checker-v3 coverage; migrations 0033–0090 and closure history | Populated property/person/financial graph receives fresh coverage, inspection and explicit retained/team acceptance before real resumption |
| Search, details and bounded selectors | Signed keyset `/page` endpoints; Properties/Persons/Users and targeted selectors; accepted selector browser and hook evidence | One-row continuations for properties/persons/units return exactly two permitted records; hidden IDs and a second organization return no data |
| Scope selection remains permission-sensitive | Scope page capability/type/context filters and exact live grants | Revocation removes the formerly selected sphere and account/payment details while independently granted property access remains |
| Original access | Shared source predicates, immutable custody/admissions, scoped download/export gates; accepted source-custody concurrency evidence | Actual activated creator downloads original bytes; other administrator/organization cannot; deactivation blocks subsequent download |
| Jobs and bulk effects recheck authority | Private worker lease/effect and review receipts; accepted principal, source and bulk-idempotency suites | Bulk review retries once; after role revocation the old bulk request is denied and the queued worker durably cancels with `JOB_PERMISSION_CHANGED` |
| Activation/bootstrap/pause/resume/rollback | 0091 selected-evidence command, exact predecessor state, transaction authorization, immutable receipts and retained downgrade; lifecycle report | Real empty-org activation, first mandate/property/scope/account setup, actual pause and populated-graph resumption; no synthetic readiness override |
| RLS and cross-org architecture | FORCE RLS, explicit policies, private table privileges; central coverage suite | Five new budget/disposition/lifecycle tables are registered and their exact deny-all policies are checked |

## Results and evidence reuse

The named B1 foundation run (`test_rbac.py`, `test_tenant_isolation.py`,
`test_rls_coverage.py`, `test_scope_grants.py`, `test_selector_paging.py`,
`test_accounting_scope_api.py`) passed 67 tests and exposed one registry omission:
the five new private tables were absent from the architectural test registry.
Their production RLS/privileges were already enforced. The registry now lists them
and checks their expected deny-all policies; all seven RLS tests passed afterward.

The new `test_b1_activated_journey.py` passed after correcting its payment-edit
request from unsupported PUT to the actual PATCH contract. It creates its own
organization and graph, uses a second existing test organization for isolation,
and invokes only the real public lifecycle. Domain data are created through APIs;
synthetic extraction candidates and authority revocation are controlled fixture
operations. No live provider or external service is called.

Reuse accepted 0091 evidence: 60 backend regression tests; 21 frontend component
tests; lint/build; fake-Docker migration-head checks; actual browser activation,
lost-response replay, pause, stale resumption denial, fresh resumption, reload and
390px layout, plus same-ID selection acknowledgment reset. Reuse the accepted
per-boundary mismatch, worker/download concurrency, review, selector and privacy
results recorded in prior B1 acceptance reports. No fresh execution of those
unchanged broad suites is claimed.

New logs: `/tmp/hv-b1-qualification-foundation.log` (67 pass/registry failure),
`/tmp/hv-b1-qualification-activated.log` (seven RLS pass/initial journey fixture
method failure), and `/tmp/hv-b1-qualification-journey.log` (journey pass).
Test environment: disposable PostgreSQL 16 at localhost:55432, Python 3.13,
actual app_rw/app_worker/app_review roles and synthetic local file storage.

## Limits

V05 approval policy and V06 sensitive disclosure remain release decisions.
Unsupported SEV financial adapters remain rejected. This acceptance does not
certify future portal/provider/outbox adapters, legal/tax correctness, production
operations, or any B2–G work. New effect and disclosure paths must integrate the
same live authorization boundary and receive their own qualification.
