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
