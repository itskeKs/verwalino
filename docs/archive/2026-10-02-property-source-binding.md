# Strict first binding of property originals

Migration 0055 closes first-binding and link-topology bypasses while organization
source enforcement remains dormant. PREPARATION preserves compatibility; the
activation-readiness function remains false.

In ENFORCED, a document must be created unbound. Its first property binding requires
current property approve authority, exact immutable attachment/current-review or
new-property publication proof, and the corresponding APPLIED import receipt with
that target and approver. A direct INSERT of an already-bound document is denied.
PAUSED denies a new binding. The successful binding emits a canonical audit in the
same transaction; an identical no-op update emits no duplicate audit. Existing
content, origin, parent and completed-binding immutability remain authoritative.

The statement guard acquires the shared organization ACL lock before row locks.
The trusted row guard reads actual organization state with row security disabled,
so a misconfigured definer cannot hide state and silently select compatibility.
Missing actor/tenant context cannot authorize a strict binding. Document locking
serializes first binding with new links. In strict mode, an original cannot gain a
second import link, nor can an already-bound original be attached to another
import. The application has no trigger-function execution privileges.

This is a publication and topology boundary, not a complete source CRUD policy.
All already-bound originals still require legacy classification before activation;
this migration neither backfills custody nor treats a historical association as
proof. Whole-source read/write policies, indirect financial/audit/privacy/export
closure and reviewed organization coverage remain required. The future activation
command must enforce those gates together.

Migration dependencies use NOWAIT on original documents and import/document links.
Strict organization state refuses downgrade. Preserve the boundary and repair
forward; do not erase state or restore compatibility to bypass a refusal.
