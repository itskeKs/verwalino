# Complete source authority for financial calculations

Migration 0059 adds a staged source preflight to the existing financial computation
context. Financial scope authority remains independently necessary. In strict
source state, the current actor must be able to read the complete expected
property-source graph and property units. The metadata predicate reuses the trusted
whole-graph enumeration established for Q&A; it does not require or consume Q&A
budget policy. PREPARATION preserves compatibility. PAUSED prevents calculation
and attestation effects while the existing source read/export rules stay strict.

This deliberately uses the conservative property-wide source graph, including
historical rows. RLS-filtered visible rows cannot establish input completeness.
An inaccessible or unproven original stops the calculation before input ORM reads,
coverage hashing or output writes. The common calculation context covers rental
compute/preview/finalize, WEG annual computation/finalization, economic plans,
asset reports and coverage attestation. Callers using that context for derived
financial statements inherit the same preflight; this does not complete every
statement, privacy or copied-snapshot disclosure path.

Database triggers independently reject calculation snapshot, line and coverage
writes without complete source authority. Fresh empty drafts retain their existing
creation path. Updating or deleting computed outputs requires the source check;
changing a computed draft's property checks both old and new source audiences.
Coverage derives its source property from its referenced settlement or economic
plan and rejects a different declared property. A client cannot choose a more
permissive property to authorize another subject's coverage. Existing financial
capability checks, completeness declarations and immutable final-record guards
continue to apply.

Statement triggers take the shared organization ACL before row locks; source grant
and classification changes take that lock exclusively. The definer predicate and
trigger use a fixed search path and row_security=off. Missing strict authority
fails closed, including when source RLS hides the denied original. Migration takes
NOWAIT locks on canonical property/unit context, source state and all five output
or coverage tables. Downgrade refuses strict state and retains prior records.

This is an integration milestone, not an activation command. Source CRUD/RLS,
reviewed legacy coverage, copied snapshot access and audit/privacy/export/report
closure remain necessary before source activation and B1 completion.
