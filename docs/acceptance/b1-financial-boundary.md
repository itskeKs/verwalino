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
