# Explicit audience creation during declaration publication

Status: implemented as an optional preparation command; source policy activation remains gated.
Baseline: `cef3aa2`.

A NEW_PROPERTY approval may include an explicit audience command: label, reason
and a bounded list of named user grants. The current active administrator must own
the unassigned import and its originals. The command must explicitly grant that
actor read, prepare and approve; other members receive only the submitted rights.
No role, creator or financial permission implies a grant. Ordinary compatibility
approval remains available until complete source-policy activation; it creates no
publication authorization proof and is not represented as an enforced workflow.

The approval route acquires the organization ACL lock exclusively before any
source row lock, because audience creation writes grants. It validates the full
review and source topology, creates the destination, domain and explicit grants,
records the applied receipt, then binds originals and writes derived rows. Every
step, including grant audits, commits in the request transaction. Any failure
rolls back all effects. This command does not call providers.

Three nullable import fields retain the normalized approval request digest and
exact publication domain/grant IDs. Tenant/property/domain and actor/grant
composite FKs retain the exact authorization chain. A database guard allows new
publication proof only during a NEW_PROPERTY transition from NEEDS_REVIEW by its
active administrator/creator, with a current explicit approve/prepare grant and
only single-import, active, unbound creator-owned originals. Proof fields and the
applied receipt are immutable; legacy applied rows cannot acquire proof later.
The attached source set is frozen after an evidenced publication.

The source resolver recognizes this guarded receipt during the transaction's
brief applied-but-unbound phase. It checks creator/source identity and the durable
receipt, while requiring the viewer's own current destination grant. Later
revocation of the publishing actor never erases the published audience proof.

Retry validates current destination access before returning a saved result.
The normalized digest includes the explicit audience and draft revision; changed
input under a reused key conflicts. Existing applied receipts without a digest
retain their compatibility semantics and cannot replay a new audience command.
Grant creation is never repeated on replay. Populated rollback retains evidence
and refuses to remove the new boundary. No historical authorization is backfilled.

Acceptance: API new-property approval, exact named grants and audit evidence,
source readability before binding, foreign/other creator and non-admin denial,
changed-payload replay, revoked approver replay, late-failure rollback, raw SQL
proof forgery/retrofitting and source-set mutation denial, historical rollback,
and browser confirmation/retry with a synthetic fixture. Existing amendment and
compatibility approval evidence must remain valid. Full source RLS, historical
admission review and downstream authorization are separate activation gates.
