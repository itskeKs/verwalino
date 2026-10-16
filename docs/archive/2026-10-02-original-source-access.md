# Original and import source authority

Migration 0063 closes ordinary property-original and declaration-import row
access in ENFORCED/PAUSED. Production activation remains unavailable. PREPARATION
retains existing tenant-scoped compatibility and immutable source guards.

## Ordinary row policy

Eight restrictive policies cover SELECT, INSERT, UPDATE and DELETE of
`property_document` and `declaration_import`. They use the actual row's custody
and destination metadata, so a creator can insert an unbound original or new draft
and receive it in the same statement. They reject creator spoofing. Strict import
creation requires a DRAFT, no applied destination, the current initiating actor,
and prepare authority for the chosen audience. PAUSED allows authorized reads and
rejects all original/import effects.

The import resolver shares one private context implementation for existing rows
and proposed row metadata. It retains exact single-link topology, explicit source
admission and bound-original proof. Known own unbound custody permits read,
prepare and export; it never grants approval. Existing destinations require their
explicit current data capability. Changes to an APPLYING or applied import additionally require
approve. Immutable origin and applied-receipt triggers remain in force.

A first original binding resolves its already-recorded APPLIED receipt, retained
proof and destination before the row changes. The independent 0055 trigger still
requires current approve and records the binding audit. Bound originals retain
0057 classification and quarantine rules. Statement ACL locks precede row locks.
A separate link guard locks the import before the original and requires current
prepare authority over both. Knowing another creator's unlinked source UUID cannot
claim it or poison its immutable topology. No policy depends on an untrusted purpose
GUC or a caller-provided bypass.

## Explicit review channels

Ordinary SELECT does not include a review exception. Admission review instead uses
bounded metadata functions that check the existing review context and pending
state, returning only document ID, title, original filename and hash. Pages use
stable UUID order, literal search, a 201-row database maximum, and signed actor/
organization/query-bound cursors. The service rechecks pending state before
returning each result. Fresh review validates the supplied hash against this
purpose-authorized metadata even when ordinary source rows are hidden.

A separate original reader accepts only ADMISSION_REVIEW or CLASSIFICATION_REVIEW.
It requires the existing exact review export check, including source custody,
destination export and quarantine. The ordinary SOURCE purpose is rejected.
The service reads the resulting storage object once and verifies its immutable
length/hash before returning those bytes. The same channel remains available in
PAUSED for authorized inspection; fresh review effects remain blocked.

## Rollback and rollout

Six dependencies use NOWAIT. Strict state refuses downgrade; a PREPARATION round
trip restores the exact previous import resolver and preserves source rows.
All new definer functions fix the search path and disable row security; the
context resolver is private. Public functions are bounded metadata/boolean or
explicitly authorized single-original readers.

This is a source-audience boundary. The existing worker uses immutable queue
principals and application lease fencing; arbitrary lifecycle/progress/lease edits
by an otherwise source-authorized prepare actor are not fully constrained to
trusted database transitions here. Database transition hardening remains an
explicit integrity follow-up before strict activation.

Indirect disclosure through privacy, audit and general reports, reviewed legacy
coverage and activation checks remain required. This increment does not complete
B1 or the M-A–M-G delivery and enables no production source enforcement.
