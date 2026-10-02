# Source authority for stored financial results

Migration 0060 adds restrictive SELECT policies to settlement runs/lines, economic
plans/lines and coverage declarations. In ENFORCED and PAUSED source state, copied
results require the current actor to read the complete expected property-source
graph, including units and historical sources. Existing financial and tenant
policies remain independently applicable. Only a draft parent with no snapshot
retains its metadata visibility; children and declarations always check their
actual parent. Coverage also verifies its declared property against that parent.
No stored snapshot, hash or financial amount is rewritten.

The trusted graph enumerator uses a fixed search path and row_security=off, so
filtered visible inputs cannot establish completeness. It is private to the
migration owner. Q&A delegates to it with effect checks enabled; disclosure uses
read/export checks without enabling effects during PAUSED. Existing calculation
and attestation guards continue to reject paused effects.

All four PDF families (rental settlement, WEG annual settlement, asset report and
economic plan) check financial EXPORT and complete source EXPORT before loading
result lines or rendering. In strict or paused state an unbound legacy financial
output cannot be exported until its accounting scope is reviewed. Its stored
result may still be read under the existing financial boundary and complete source
READ authority. PREPARATION preserves legacy unbound export compatibility. A bound
output always requires its exact financial EXPORT grant, even in PREPARATION.
Current grants and proof remain protected by the transaction's shared ACL lock.

Migration locks eight dependencies with NOWAIT and refuses rollback while any
organization is ENFORCED or PAUSED. PREPARATION roundtrip retains stored evidence.
No activation command is introduced. Source CRUD/RLS, audit/privacy/report closure,
reviewed legacy coverage and atomic activation remain B1 prerequisites.
