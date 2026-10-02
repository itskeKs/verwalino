# Source completeness in retention and privacy exports

Migration 0064 prevents source-filtered financial outputs from disappearing from
privacy retention decisions. It adds no retention period, legal interpretation,
original mutation or source activation. Existing retention arithmetic and financial
scope authorization remain unchanged.

## Boundary

The existing privacy completeness function now combines financial access with a
private source check. The latter enumerates finalized settlement and economic-plan
recipients through trusted, tenant-matched parent and line rows. It does not infer
completeness from the rows an ordinary reader can see. For every expected property,
current access to the complete source graph is required. Status and erasure review
use READ; the privacy export independently requires EXPORT. An unbound or hidden
expected output fails closed rather than becoming an absent retention binding.

PREPARATION preserves compatibility. PAUSED retains authorized retention inspection
and export. Existing person-erasure rules still determine whether an authorized
request must be refused or restricted; retained outputs and originals are never
rewritten by this change.

The service checks completeness before discovery and again after all RLS-filtered
binding reads. This second check matters because a grant can expire while the ACL
lock is held, making later queries return fewer rows without a concurrent ACL edit.
An incomplete discovery cannot produce an erasure decision or partial export.

All four privacy endpoints recheck the administrator role inside their data
transaction while holding the organization ACL lock. Authentication in an earlier
transaction cannot authorize a request after role revocation. Existing internal
service callers retain their explicit financial/source checks.

## Migration and limits

The new source enumerator and prior financial implementation are private. The
public completeness function returns only a boolean; all new definers fix their
search path and disable RLS. Eight dependencies use NOWAIT. Strict state refuses
rollback; a PREPARATION round trip restores the previous financial predicate.

This boundary covers retained outputs hidden by the financial source policies.
Person/profile/history visibility, audit and general report disclosure, raw worker
state-transition hardening and reviewed legacy activation coverage remain separate
requirements. Source activation remains unavailable and B1 is unfinished.
