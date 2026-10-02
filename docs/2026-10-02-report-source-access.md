# Complete source access for indirect reports

Status: implemented and verified locally; baseline a8ae01a, migration 0075.

## Behavior

In strict source mode, account statements, open items and the deadline monitor
require one exact financial scope plus complete current READ access to its property
source graph. Expected contacts attached only through scoped payment rows are
checked with definer visibility; a filtered profile cannot produce a partial or
zero result. An explicitly requested owner profile must also be readable. The
PREPARATION behavior and financial scope/type/period validation remain intact.

Stored dunning notices and their immutable provenance receive additional restrictive
RLS. Source READ is required for disclosure and source EXPORT for PDFs, independently
of financial READ/APPROVE/EXPORT. Source consumption needs READ, not property editing
rights. Preparation/issue/cancellation additionally require the nonpaused source
context and their existing financial capabilities. Unclassified legacy notices have
no reviewed scope and remain unavailable under strict enforcement; a contact grant
does not establish property provenance. Existing rows and frozen values are unchanged.

The application returns an actionable 404 before calculation/rendering when sources
are unavailable. Database policies independently guard dunning effects and provenance.
ACL and financial-input locks preserve the existing lock order. Privacy completeness
enumerates expected issued notices independently of RLS so hidden retained snapshots
cannot disappear from erasure/export checks. Rollback refuses strict-mode downgrade;
a preparation-mode roundtrip preserves historical row hashes.

## Evidence

Two RED tests reproduced an owner statement and issued dunning snapshot disclosed
without property rights. The first 29 report/dunning/RLS checks passed. A separate
RED test reproduced payment-only owner contact revocation still returning a report;
expected payment-recipient checks close it. A deadline RED test reproduced a false
“No overdue items” overview when its leases were hidden; the shared read-context
preflight now protects that consumer too.

The compatibility run passed 112 contact/privacy/dunning/report/RLS cases and exposed
one legacy error-order change. Limiting the requested-owner guard to strict mode
preserves that compatibility; the final 53 report/deadline/payment tests passed.
Coverage includes allowed statements and overdue monitors, contact revocation,
unknown originals, distinct read/export rights, PAUSED reads versus effects, raw
provenance insertion denial, privacy retention and a hash-preserving migration
roundtrip. One initial compatibility attempt hit only setup errors because the
disposable database held an earlier uncommitted 0075 shape; restoring that test-only
schema to 0074 allowed the clean rehearsal. No historical migration was edited.

Independent read-only review found no actionable boundary issue. Shell syntax and
fake-Docker deployment regression passed with head gate 0075. No frontend changes
required new browser checks; the accepted UI behavior is unchanged. No deployment,
external changes or production data were used. Activation remains false; legacy
remediation, coverage and remaining B1/full-plan work are still outstanding.
