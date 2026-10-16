# Relationship history source access

Status: locally verified and independently accepted. Migration 0067 follows
87c7067. Source activation remains disabled; B1 is incomplete.

## Boundary

Lease and ownership history rows disclose occupation, dates, rents, deposits and
ownership shares. Strict CRUD policies resolve their immutable unit to the current
property source audience. READ requires the complete source graph; effects require
PREPARE. Actual profile and person rows must have the same organization. Existing
financial selected-unit restrictions remain additional SELECT constraints. PAUSED
retains reads and refuses effects. Parent/profile IDs and organization identity
cannot be reparented, even in PREPARATION. Profile organization/identity and person
links are likewise immutable, so a profile edit cannot silently transfer historical
relationships and retention obligations to another person; temporal values retain their existing
half-open conventions and exclusion constraints.

HTTP history writers and readers revalidate their principal in the data transaction.
A proposed-row preflight returns 404 before unauthorized ORM creation. Direct SQL
is independently constrained by row-context policies. This supports first-row
INSERT RETURNING without pretending the row already exists. Existing financial
input serialization is retained; source ACL statement locks precede row effects.

## Completeness

A trusted private boolean checks expected lease/ownership graphs, including every
unit/profile/person organization edge. It extends the complete property source
predicate used by financial preparation, stored disclosure, master data, Q&A and
meeting consumers. Historical mixed rows are preserved and fail closed for the
affected property; filtering them cannot establish vacancy or complete inputs.
This metadata check is non-recursive and returns no source payload.

Privacy completeness additionally enumerates the person's expected historical
relationships, including inactive history, before deciding status, retention,
erasure or export. Each property needs READ or EXPORT as appropriate. Malformed
relationships cannot be mistaken for absence of a current contract. The existing
post-discovery completeness check also catches grants expiring during active
relationship queries. No retention duration or legal decision rule is changed.

History audit reads inherit ordinary history RLS. The migration explicitly switches
the audit policy to the new function OID after wrapping earlier typed audit checks.
Preparation compatibility is retained. Private graph and privacy helpers remain
unavailable to the application role. Strict rollback is refused; preparation
round-trip must preserve history and audit hashes.

## Evidence and limits

Four initial red tests reproduced unrestricted history reads, writes with a source
READ grant, privacy access without the expected history audience, and a mixed
historical graph accepted as complete. The first history/privacy/M3/M4/M9 suite
passed 83 tests. A separate expanded run passed 83 history/financial-input/master/
profile/account-statement/meeting cases. Two additional expected red cases showed
that profile-to-person reassignment could otherwise redirect durable history;
profile identity triggers now close that path. The subsequent 71-case profile,
history, privacy and master run passed.

Review examined concurrent erasure and new history. Existing migration 0031 already
serializes history effects with the privacy financial-input lock; no competing
lock protocol is added. A two-connection regression nevertheless showed that a
writer waiting on that lock could commit a new active history after erasure had
completed. The write policy now rechecks current `anonymized_at` and rejects that
post-wait effect. Historical READ/EXPORT remains available under current grants.
The final updated suite passed 72 history/profile/master/privacy cases. The
two-connection case also passed alone after adding explicit pre-wait source
authority and non-anonymized-person assertions. Independent review accepted the
boundary without remaining actionable findings. No frontend change was required.
Logs: `/tmp/hv-history-source-{red,first,expanded,profile-red,final,race-red,
reviewed,race-isolated}.log`.

Person/profile tables still need explicit contact custody and source audiences;
this milestone only checks their identity/coherence when used by histories. No
historical creator or sharing permission is inferred. That work, remaining indirect
reports, worker lifecycle integrity, reviewed legacy remediation and activation
coverage remain prerequisites for B1 completion. Later M-B–M-G scope remains open.
