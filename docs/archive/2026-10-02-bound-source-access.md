# Strict resolution of classified bound originals

Migration 0057 integrates the retained source proofs into the prepared document
and import resolvers. It does not enable source RLS or organization activation.
PREPARATION keeps compatibility behavior; ENFORCED and PAUSED require proof for
an original already associated with a property. The strict state transition gate
remains false until all source, indirect disclosure and effect paths are complete.

A private metadata predicate checks the actual original and its exact current
link topology. QUARANTINED classification dominates every older proof. Multiple
links, a linked import without an APPLIED receipt, or a conflicting destination
fail closed. An ADMITTED classification must match its immutable property,
uploader, hash and full context digest, including source-link role/order and
import state. Earlier authorized attachment, contemporary unbound review and
explicit new-property publication remain accepted against their exact original,
import and property. No later grant fabricates an earlier admission.

Historical proof is separate from present reader authority. Revoking the reviewer
or uploader's current grant does not erase durable admission; the consuming actor
still needs their own current property capability. The helper is not executable
by the application role. Its SECURITY DEFINER functions use a fixed search path
and row_security=off, so an incorrectly restricted owner errors instead of hiding
quarantine or graph rows. Import resolution checks metadata directly; document
resolution can delegate to the import without a recursive policy cycle.

Classification now changes strict source authority. Its API and service acquire
an exclusive organization ACL lock before shared checks or source row locks. A
statement trigger also protects direct SQL inserts. This serializes a decision
with in-flight source reads and prevents shared-to-exclusive lock upgrades in the
HTTP command. Exact retry behavior and canonical immutable receipts remain intact.

The existing derived-source and whole-graph Q&A checks inherit the new predicate.
A quarantined expected original blocks the entire provider input even when normal
RLS hides that original. A queued incompatible legacy source is cancelled before
worker source loading. Financial whole-input checks, source CRUD policies,
export/audit/privacy closure and reviewed activation coverage remain outstanding.

Migration takes NOWAIT locks on originals, imports, links, all three retained-proof
tables and source state. Downgrade refuses ENFORCED or PAUSED state. In PREPARATION
it restores earlier resolvers while retaining classification evidence, allowing a
controlled code rollback without erasing the decisions.
