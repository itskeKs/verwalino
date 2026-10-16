# Property and unit authority with atomic creation

Migration 0061 protects direct property and unit reads and writes with restrictive
RLS. In ENFORCED/PAUSED state, a master row requires both the current property-data
capability and READ authority over the complete expected property-source graph.
This deliberately conservative rule prevents a canonical field from exposing facts
copied from an unproven or quarantined original. PREPARATION preserves compatibility.
PAUSED allows authorized reads and blocks writes. Financial grants remain separate.

Policies use the property ID on the row, so a new unit or property can satisfy
INSERT RETURNING without querying an as-yet invisible instance of itself. UPDATE
checks both old and new property audiences, protecting raw unit reparenting as well
as HTTP changes. Statement triggers lock the ACL before row locks. HTTP read/write
transactions recheck the live principal and take the matching ACL lock first.

New strict properties require an explicit audience. The administrator records a
domain and individual grants before creating the parent in one transaction. The
composite domain-to-property foreign key is deferred until commit; a missing or
foreign parent still fails. The creator must explicitly retain read and prepare.
Declaration publication additionally retains its existing explicit creator approve
requirement and orders audience, destination, approval receipt, originals and facts.
No role implicitly grants source authority. An admin-only boolean existence helper
allows setup for a known hidden property ID without returning its source fields.

Manual creation accepts an Idempotency-Key (required with an audience). An immutable
actor-scoped property_creation_receipt stores the digest and exact property FK.
Concurrent identical requests return one result; changed payload returns 409, and
retry rechecks current access. The existing cost-only operation receipt is retained.
The UI offers an unchecked explicit self-grant choice and reason, and retains its
creation key and input across a lost response. Additional team grants use the
existing administration flow.

Migration uses NOWAIT dependencies and refuses rollback in strict state or with
creation receipts. Empty PREPARATION rollback/upgrade preserves existing master
rows and domain/grant evidence. The activation predicate remains false. Original
and derived source CRUD/RLS, indirect report/audit/privacy disclosure and reviewed
coverage still gate B1 completion and source activation.
