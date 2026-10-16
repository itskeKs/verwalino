# Reviewed classification of already-bound property originals

Follow-on to 0055. This design covers existing bound originals without inventing
historical custody or relying on their old property association as authorization.
It remains a preparation workflow until complete source-policy activation exists.

Each classification is an immutable present-day decision: ADMITTED or QUARANTINED.
The reviewer needs a live administrator role and explicit read, prepare and approve
rights on the exact property-data domain. ADMITTED additionally requires known
original ownership by that reviewer, an active original and coherent immutable
source topology. A standalone bound original may be reviewed; a linked original
must have exactly one APPLIED import with the same destination and no live lease.
Unknown custody, multiple links or conflicting targets cannot be admitted. Those
sources may receive an explicit quarantine decision without exposing their bytes
or inferring an uploader. Neither disposition changes the original or its parents.

The list exposes only bounded source metadata for the explicitly authorized
property. Original preview is separately restricted to its known uploader with
those exact current rights; it uses ordinary ORM/storage reads and byte verification.
No administrator role alone grants original access. Current rights are checked
before replay or receipt access. Frozen UUID idempotency keys and canonical request
digests provide exact retry after an unconfirmed response. Changed context is a
conflict requiring renewed inspection. Receipt, grant chain, exact hash, source
context, database clock and canonical audit commit atomically.

Lock order is shared organization ACL, linked imports in UUID order, then original.
Recheck the linked set after the original lock; a changed set requires retry rather
than trusting unlocked context. Both dispositions require quiescent linked work.
Once classified, source-link insertion/deletion cannot change the reviewed context.
Rows and source ownership remain immutable; no fabricated historical timestamps or
retroactive attachment proofs are produced.

Future strict access must accept exact earlier durable proof or current ADMITTED
classification and reject QUARANTINED originals. Quarantine dominates other proof.
Whole-input preflights must reject financial/Q&A work whose expected graph includes
a quarantined source instead of using only visible rows. Unknown unclassified
origins block reviewed organization activation. Coverage must include already-bound
and unbound originals, current source topology, grants and required budget policy.
This workflow alone cannot enable enforcement or certify complete B1 coverage.
