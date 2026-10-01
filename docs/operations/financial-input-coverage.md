# Financial input coverage

New rental, WEG, asset-report and economic-plan compute/finalize requests require
explicit complete inputs and a scoped, append-only coverage declaration. Missing
is unknown; a supplied zero or empty list is accepted only after attestation.
Legacy finalized snapshots remain readable without rewriting their bytes or hash.
Existing numeric drafts receive no automatic evidence backfill.

The draft review submits `POST /api/financial-input-coverage` with subject kind/id,
exact values, `confirmed_complete: true`, a nonblank reason and optional invoice
source IDs. Source references are organization-scoped; the declaration does not
grant access. Authorized write roles can attest, read-only actors cannot.
Identical retries reuse the latest declaration; changed evidence creates a new
revision. Subject row locks serialize concurrent attestations and finalization.
The record and its audit event commit in one request transaction.

Rental totals must be persisted before review. Full-year derived totals must
match the current booked payment sum for the same lease/year. A reversal blocks
both finalization and reattestation until the total is derived again. Custom
subperiods need an evidenced manual amount until interval allocations exist.
WEG requires an explicit paid amount for every period owner plus reserves, and a
current finalized plan for the same property/period. The engine still subtracts
plan Soll from costs; received payments remain informational. Asset reports need
every property bank account, with correct type, reserves, receivables and
payables. An empty budget plus explicit zero reserve is valid after approval.

Coverage freezes values, subject scope, source revision, interval, source IDs,
attestor, timestamp and reason in new snapshots. Distinct attestations produce
distinct document hashes even when accounting results are identical. Pure-engine
arithmetic and canonical serialization are unchanged.

Migration 0031 adds tenant RLS, composite subject/property FKs, revision uniqueness
and SELECT/INSERT-only application privileges. Source-table triggers acquire the
same transaction advisory lock as coverage/finalizers. Economic plans and their
lines participate so corrections cannot change Soll during a WEG finalization.
Revision hashing currently includes all property financial source rows, including
drafts and other periods: conservative invalidation, not precise dependency
tracking. The lock serializes financial writes within an organization; capacity
must be measured in M-G before professional scale claims.

Structured failures identify missing/stale coverage, changed derived totals,
unsupported derived periods, owner/account gaps and invalid linked plans. The UI
preserves entered data, requires renewed confirmation after value edits and gives
recovery guidance. Confirming coverage does not book payments or release costs.

Set `HV_FINANCIAL_COMPUTATION_ENABLED=false` to disable compute/preview/finalize
through the shared policy. Rollback uses this switch while preserving evidence
and reads. Never deploy the old unknown-to-zero finalizer or drop this evidence
in a live rollback. Migration downgrade is for disposable test teardown only.
A4 records migration/hash preservation and browser acceptance; specialist period
policy V03 and broader professional release gates remain open.
