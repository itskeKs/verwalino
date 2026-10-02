# B1 classified financial and invoice job boundary

Date: 2026-10-02. Incremental evidence; **B1 activation remains incomplete**.

Migrations 0034–0035 restrict bound financial rows and dependent invoice sources,
calculation lines, coverage and audit reads. Selected calculation contexts isolate
both money and unit membership. Unknown classification blocks computation.
Invoice attempts retain immutable principals and revalidate authorization before
claim and completion; permission loss only records fenced cancellation metadata.

Verification on the guarded disposable PostgreSQL 16 database: **102 passed**
across `test_financial_scope_boundary.py`, `test_invoice_extraction_worker.py`,
`test_financial_input_policy.py`, `test_scope_grants.py`,
`test_accounting_scope_api.py` and `test_m3_settlement.py`. The boundary tests use
`app_rw` for grant/admin denial, exact sums, confirmation/approval denial, source
byte access and revoked access. Worker tests cover revocation, deactivation and
role changes both before claim and before completion. Direct trigger tests prove
immutable principals and rejection of stale cancellation evidence after a newer
attempt. Original access regressions were observed failing against revision 0033
before implementing this boundary.

Independent read-only review accepted the bounded database/worker implementation;
its requested stale-attempt and principal-tamper regressions are included in the
102 passing tests. No frontend change in this increment. Migration reset through
base/head exercised disposable downgrade/upgrade; production rollback must retain
rows and permissions, pause writers and use forward repair.

Outstanding: exact contexts for all aggregate/statement/dunning paths, atomic new
and correction bindings, privacy retention/export completeness, other document/job
adapters, legacy reconciliation/quarantine, and explicit staged activation. SEV
financial binding remains disabled pending exact unit/owner adapters. This evidence
does not establish full B1 acceptance or close any external release decision.

## Aggregate and privacy completeness follow-up (0036–0037)

Payment statements, derived NK/Hausgeld totals and dunning queries now reject a
missing computation context whenever relevant classified finance exists. Explicit
plan workflows select the plan's scope before loading lines or payments. A selected
owner must have a relationship in that scope; a foreign owner cannot produce a
misleading zero. Draft computation locks follow ACL → financial inputs → draft row.

Privacy status, deletion, anonymization and export require complete visibility of
retained cost/run/plan references. Export additionally requires the exact export
capability. Definer checks return only completeness, never hidden financial data.
The organization-wide deadline monitor returns 409 when classified finance exists,
pending a scoped overview adapter; it no longer swallows authorization errors as
zero overdue items. This is an explicit temporary functionality limit.

Final verification: **155 passed** across boundary, financial-input policy, M3,
M4, M6 payments, M7 dunning, M9 privacy and M9 deadlines. Positive coverage includes
one owner with 100 in the selected scope and 900 elsewhere, exact derived 100,
foreign-scope owner rejection, complete retained bindings for an authorized reader,
read-only export denial and successful explicitly granted export. Negative privacy
and missing-context statement tests were observed failing before their fixes.

Independent review found the owner-membership and swallowed-monitor errors; both
were repaired and reviewed before the final passing suite. Boundary fixtures use
isolated organizations because direct DB test fixtures commit at teardown. No
frontend changes or full scoped-rollout claim in this increment.

## Atomic creation and correction inheritance (0038)

Creation APIs for accounts, costs, rental/WEG settlements, asset reports and plans
accept an explicit `accounting_scope_id`. Server checks require prepare access,
matching property, mandate, accounting type, unit and effective period. Classified
properties reject omitted context. A BEFORE INSERT trigger writes the typed binding
in the same transaction, derives the mandate and retains deferred composite checks.
New WEG runs inherit their explicitly referenced plan's context. Corrections inherit
the original context and retain the WEG plan reference; database checks reject
cross-scope successor pointers and linked plans. No released facts are rewritten.

Upload-first cost creation preserves source authorization before selecting the
destination. A savepoint limits the temporary visibility context needed to link an
unclassified original; failure restores the context and request rollback removes
cost, binding and link. Existing idempotent cost replay still checks current access.

Verification: **127 passed** across creation, boundary, M2, M3, M4 and bank-account
suites after all changes. The nine creation tests include raw app_rw inserts,
missing-context denial, exact correction inheritance, API upload-first plus replay,
mandate inheritance, period rejection, cross-scope chain rejection and forced-link
rollback. Four initial new-resource/correction regressions were observed failing
before implementation. Frontend TypeScript/production build passed after adding the
optional request fields; the existing approximately 579 KB bundle warning remains.
Independent review accepted the boundary after the savepoint repair. Its final
request to verify the latest fixtures is satisfied by the 127-test run.

This is an API/database increment; creation selectors are covered below. Broader
financial-write idempotency, SEV adapters and full rollout remain outstanding.

## Financial creation scope selectors

The six independent creation forms (account, manual cost, upload-first cost,
rental settlement, economic plan and asset report) now provide a bounded scope
search filtered by the authenticated actor's current prepare grant. Configuration
visibility does not imply eligibility. The page API binds property, capability,
accounting type and creation filters into its cursor. SEV remains unavailable.
WEG settlement creation explains that its selected plan supplies the scope.

Property changes clear the selected scope. Bank type changes also clear it and
restrict deposits to rental scopes and reserves to WEG. Search retains an explicit
selection; uncertain cost creation retains both selection and request identity.

Verification: **17 backend tests passed**, **four frontend tests passed** (three
selector/upload-first tests, plus the bank-type transition test after review).
Lint and production build passed; the existing bundle warning is approximately
581 KB. Independent review found and verified the bank-type reset repair.

The final real Chromium run of `scripts/acceptance/b1_scope_creation_browser.cjs`
passed bank save, object/type reset, grant/type filtering on all six forms,
upload-first lost-response replay with the same key/result, and mobile width
390/390. The final screenshot was visually inspected. There were no page errors
or unexpected HTTP failures. Two dashboard calls received the documented 409 for
the still-unavailable legacy organization-wide deadline monitor; these are recorded
separately in `b1-scope-creation-browser.json`, not counted as successful overview
behavior. Initial browser attempts corrected ambiguous test locators; final evidence
uses the completed run. Disposable original bytes/screenshots remain under `/tmp`.

The browser script expects an existing synthetic PDF at
`/tmp/hv-scope-creation.pdf`, or `HV_SCOPE_INVOICE_FIXTURE`. Generate one with the
installed backend runtime before starting the browser:

```sh
env -u USER backend/.venv/bin/python -c "from reportlab.pdfgen import canvas; c=canvas.Canvas('/tmp/hv-scope-creation.pdf'); c.drawString(70,750,'Synthetic scope invoice: 119.00 EUR'); c.save()"
```

Run against only the disposable database with API port 18000 and Vite port 13000.
Set `HV_PLAYWRIGHT_PACKAGE` and `HV_CHROMIUM_EXECUTABLE` to existing runtime paths;
no package installation or live provider is required. The script uses the synthetic
Alpha seed account and writes only disposable test records.

### Unavailable overview presentation (2026-10-02)

A blocked or failed deadline query now renders unavailable finance on the dashboard
and deadline monitor. It cannot become a green zero, an empty success, or a retained
result for another reference date. Dashboard master counts survive an independent
finance failure; missing open-item evidence remains unknown. Obsolete date requests
cannot overwrite the current result.

Verification: six focused `FinancialOverviewAvailability.test.tsx` tests pass
(initial five failures reproduced before the fix), frontend lint and production
build pass. The existing approximately 582 KB bundle warning remains. Independent
read-only review accepted the change. No backend policy or financial calculation
changed; the classified-finance overview remains explicitly blocked until its
scoped adapter is complete.

### Scoped statements and complete open-items queries (2026-10-02)

Lease/owner statement and lease open-item APIs accept an explicit accounting scope.
Live read permission, rental/WEG type, effective dates and classified-input
completeness are checked before reading subjects or calculating balances. Lease
units and owner evidence remain constrained by RLS. A selected plan can provide
an owner's scope; its period and any explicit scope must agree. Report access does
not require preparation permission and has no administrator fallback.

The tenant open-items page now calls one transactional aggregate endpoint. Any
unavailable input blocks the whole response; a failed lease is never silently
omitted. Both statement screens use bounded read-grant selectors, discard results
when criteria change, and freeze criteria while a request is pending. Shared
selector controls fit long scope labels inside a mobile viewport.

Verification: 80 combined backend tests passed for the statement increment plus
existing payment, dunning and financial-boundary regressions. The expanded final
nine statement tests additionally exercise partial unit scopes, expired periods,
plan inheritance/mismatch and out-of-plan periods. Eleven relevant frontend tests
passed; the final selector adjustment received a focused four-test rerun. Frontend
lint/build pass (existing approximately 583 KB bundle warning). Independent
read-only review accepted the implementation. The new backend fixture initially
needed its dedicated tenant fixture import and a fresh migration connection after
commit; these harness issues were corrected before the passing runs.

Browser reproduction uses `scripts/acceptance/b1_scope_statements_browser.cjs`,
local API 18000, Vite 13000 and the disposable database 55432. Supply the same
`HV_PLAYWRIGHT_PACKAGE` and `HV_CHROMIUM_EXECUTABLE` values as the scoped-creation
acceptance. Browser plugin not available; regular Playwright is the fallback.
Only synthetic local records are created. Browser evidence covers selected 100 EUR
versus excluded 900 EUR, rejected period and retry, matching bulk debt, grant
revocation, unavailable dashboard/deadlines, and mobile width. Early runs corrected
test locators, then exposed and fixed a real long-label selector overflow.

This increment does not enable scoped dunning, change the existing year-based
payment attribution rules, or claim completion of the remaining B1 adapters.

### Scoped dashboard and deadline monitor (2026-10-02)

The dashboard and deadline monitor now accept a live read-capable accounting
scope. Property, accounting type and effective unit set determine the inputs;
rental obligations are bounded by the scope's effective dates. WEG reports omit
the tenant-debt metric instead of displaying a tenant-debt zero. A closed scope
does not acquire next-year plan obligations outside its term. Partial fiscal or
plan years require reviewed coverage and return an explicit conflict. Scoped
completion requires full fiscal coverage from finalized runs; legacy rules remain
unchanged. A malformed lease blocks an aggregate rather than disappearing.

Independent review found that unlinked invoice documents could be hidden by RLS
and falsely become a scoped zero-retention count. Scoped retention now remains an
explicit unavailable INFO item; no count or expiry date is queried or inferred.
An unlinked-document regression verifies this. The complete source-assignment
adapter remains pending. Review accepted the repair with no remaining findings.

Evidence: four scoped acceptance cases first failed on the missing API context;
eight final scoped cases plus existing deadline engine/API and scoped statement
regressions pass (51 tests). Eleven frontend tests, lint and production build pass
(existing approximately 583 KB bundle warning). One intermediate DB connection
rejected an SSL upgrade; the disposable container remained healthy and the full
focused rerun passed. No application change was needed for that transient failure.
Browser script `scripts/acceptance/b1_scoped_deadlines_browser.cjs` passes rental
versus WEG selection, dashboard recovery, unknown retention, revocation and mobile
390/390. Evidence is `b1-scoped-deadlines-browser.json`; screenshot inspected at
`/tmp/hv-b1-deadlines-acceptance/deadlines-mobile.png`. Browser plugin not available;
regular Playwright used against API18000/Vite13000/disposable DB55432 only.

## Immutable dunning provenance (0039)

New scoped tenant and owner notices retain an immutable `PREPARED` context with
scope, exact lease or finalized plan, period, creator and reason. Deferred database
checks validate tenant/source/property/unit membership and lease overlap, or the
owner's advance line and same-scope finalized plan. Missing provenance cannot be
bypassed with direct app-role inserts. An issued notice retains its existing frozen
body; cancelling a scoped draft preserves its context and audit trail.

Live capabilities separate READ, PREPARE, APPROVE and EXPORT. Administrative role
alone grants no financial access. Detail, list, PDF, generic audit and privacy
exports honor that boundary. Escalation counts only the selected scope's issued
history; any unresolved legacy issued history blocks calculation. Unknown legacy
letters associated with classified finance are quarantined. They are not assigned
a guessed source. Proven legacy review/backfill remains unfinished B1 work, so
this increment does not establish full B1 activation or a permanent quarantine policy.
Scoped owner dunning requires a bound finalized economic plan; a payment-only
owner adapter remains unsupported. The populated migration cannot be downgraded;
use forward repair and preserve the context table. Only the disposable test reset
explicitly discards synthetic contexts before a full schema reset.

Evidence: 48 backend tests passed across scoped dunning, legacy M7 and RLS coverage.
After exact selector capability hydration was added, 18 affected scoped-dunning and
scope-API tests passed, reusing unaffected legacy evidence. Tests cover raw app-role
rejection, immutable provenance even under the migration principal, populated
downgrade refusal, audit visibility, complete authorized privacy export, export
capability loss, 100-versus-900 money separation, per-scope escalation and live
revocation. Independent read-only review accepted the repaired boundary and UI.

Twelve UI regressions pass across scoped dunning, statements, overviews and bank
creation. The Open Items shortcut preserves a visibly hydrated scope. Delayed
source loading, wrong-property or unavailable inherited scopes block submission;
the UI does not silently replace them with legacy context. Hydration checks the
requested live preparation capability. Cancelled notices display as discarded;
denied actions hide previously loaded amounts, and list failures never appear as
an empty successful result. Lint passes without warnings; the production build
passes with the existing bundle-size warning (approximately 586 KB).

The Chromium journey in `scripts/acceptance/b1_scoped_dunning_browser.cjs` passed
against only the disposable local API/database. It checks Open Items → scope
prefill → invalid-period retry → 7,100 EUR draft → issue → denied/authorized PDF,
retained cancellation, separate 6,700 EUR scope at level 1, and revoked detail/PDF.
The 390 px mobile screenshot was inspected; the main content stays within 390 px.
The report is `b1-scoped-dunning-browser.json`; there were no page errors.

## Proven legacy bank descendants (0040)

A bounded, explicit review now classifies already-booked payments whose immutable
bank association proves an existing accounting scope. It does not infer scope
from property presentation type, counterparty role, or a year-only allocation.
Partial-unit and SEV scopes, unbooked payments, incompatible financial semantics,
unclassified linked settlements and conflicting correction accounts remain review
blockers. The preview is limited to descendants of accounts in the selected scope;
it is not a completeness claim for every historical financial resource.

The API and narrow SECURITY DEFINER apply function both require active organization
administration plus exact PREPARE. The general binding INSERT policy still only
permits REVIEWED rows. The definer revalidates canonical payment/bank/scope hashes
under ACL → financial → receipt → ordered source locks, then writes only immutable
PROVEN_LEGACY sidecars, typed receipt items and audit evidence. A batch has at most
200 explicitly selected IDs; one conflict aborts the entire batch. Original money,
periods and frozen rows remain unchanged. Retry keys are actor-scoped and command-
bound; replay rechecks live authorization, including grant expiry after waiting
for the financial lock. Downgrade refuses to erase populated reconciliation evidence.

Final focused checks: **17 passed** (10 reconciliation cases plus 7 RLS coverage),
including concurrent identical retries, changed-command conflict, all-or-nothing
failure and retry, full original-row JSON equality, cross-organization denial,
raw app-role forgery denial, receipt/audit isolation, immutable evidence, blocked
partial scopes, bounded signed cursors and populated downgrade refusal. The expiry
race was reproduced failing before the post-lock recheck. An earlier combined run
also passed the 17 unchanged accounting-scope API and financial-creation tests;
that valid evidence was reused after the reconciliation-only repair. Migration
functions were created by the disposable database's real migration principal and
invoked by app_rw. Independent review accepted the implementation and expiry delta.

Four UI tests pass across reconciliation and scope setup. Lint and production build
pass with the existing size warning (approximately 590 KB). The local Chromium
journey passed preview → blocked/proven selection → lost committed response → same-
key retry → unchanged original → revocation. No page errors; mobile main content
and visible width both measure 390 px, and the screenshot was inspected. See
`scripts/acceptance/b1_payment_reconciliation_browser.cjs` and
`b1-payment-reconciliation-browser.json`. The browser evidence remains valid after
the isolated post-lock expiry repair, which is covered by the dedicated regression.

B1 remains incomplete: other historical resources, dunning source review,
proven initial scope/role proposals, document/job adapters, staged activation and
SEV adapters still require their own evidence. No team rollout or release gate
has been activated by this increment.

## Scoped effect rollback control (0041)

The organization-level control applies narrowly to **classified financial effects**.
It does not activate enforced rollout or stop unclassified legacy workflows. Paused
PREPARE/CONFIRM/APPROVE/SEND checks fail through the existing capability function,
whose OID is preserved so stored RLS dependencies retain the gate. READ and EXPORT
continue to require live exact grants. Events form an immutable linear chain with
one genesis/successor per organization, a deterministic sequence and actor-bound
retry keys. The exclusive ACL lock precedes live administration, replay and
expected-predecessor checks. Resume changes no grant or financial row.

On the isolated PostgreSQL 16 database, **89 backend tests passed**: control,
existing scope grants, financial boundary/creation, scoped dunning, legacy payment
reconciliation and RLS coverage. The ten control cases include concurrent identical
retries and conflicting successors, in-flight shared-lock ordering, read/export
retention, live administration, tenant/audit isolation, unchanged originals, revoked
rights after resume, and paired scoped/unclassified invoice jobs before claim and
finish. Scoped cancellation leaves extraction payloads unapplied; unclassified
compatibility is asserted deliberately. Populated downgrade is rejected; empty
0041 downgrade/upgrade preserves the capability OID and is rolled back to restore
all synthetic control evidence. The initial API regression failed before the
literal control routes existed.

Four UI tests passed, including lost-response retry, replay followed by a later
resume, unavailable current state, and predecessor selection. Lint passed; the
production build passed after correcting an unsupported test-query option. The
existing bundle size warning remains (approximately 593 KB). Independent static
review found no actionable issue. Checks run against the 0041 increment on base
`ee0c74e`, using the repository Python environment and installed frontend toolchain;
no production data or services were used.

Local Chromium acceptance also passed pause → simulated lost committed response →
same-key retry → live paused state → retained read/blocked creation → revoke →
resume → blocked financial access. Replaying the earlier pause returns the original
receipt while the current mode remains compatibility. No page errors; mobile
content and visible width both measure 390 px and the screenshot was inspected.
See `scripts/acceptance/b1_scope_control_browser.cjs` and
`b1-scope-control-browser.json`. The browser used only synthetic local fixtures.
B1 remains partial: legacy dunning/source review, initial scope/role proposals,
document and job adapters, enforced activation and SEV coverage remain open.
