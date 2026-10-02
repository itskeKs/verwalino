# Prepared shared property-data audiences

Migration 0045 introduces explicit shared property-data domains and per-user grants.
This is configuration preparation only. Existing source and master-data policies
remain in compatibility mode. Every domain API response declares
`enforcement_status: PREPARATION`, and the administration screen states that these
entries do not yet restrict existing access. Do not advertise a source-security
rollout from these tables or grants.

Administrators open `/gebaeudedaten-rechte` from Users. Create one immutable domain
per property with a label and reviewed reason. No creator/admin/financial-scope
grant is added automatically. Prepare each team member's capabilities separately:
read, prepare, approve and export. Read is mandatory; only active writers can
exercise prepare/approve, while an explicitly granted active read-only member may
read/export. Financial confirm/send permissions are not part of this domain.

Configuration mutations revalidate the active administrator and hold the exclusive
organization ACL lock. Domain identity and original grant metadata are immutable.
Revocation is one-way, records the actual actor and database time, and retains the
grant. An expired but unrevoked grant must be explicitly revoked before replacement.
Every mutation writes its audit evidence in the same transaction, including raw
application-role writes. Tenant-safe FKs prevent cross-organization references.

## API and lost responses

- `GET /api/property-data-domains/page` supports bounded signed cursor pages and an
  optional property filter. Administrators see configuration; other active members
  see only explicitly granted domains. Read routes recheck the live principal in
  their data transaction.
- `POST /api/property-data-domains` requires a UUID `Idempotency-Key`. Same actor,
  key and normalized body return the original domain; changed input conflicts.
  One property cannot acquire a second domain with a fresh key.
- `GET /api/property-data-domains/{id}` returns the permitted domain metadata.
- `GET /api/property-data-domains/{id}/grants/page` is administrative grant history.
- `POST /api/property-data-domains/{id}/grants` requires a UUID key, active member,
  reviewed reason, capabilities and optional future timezone-aware expiry.
- `POST /api/property-data-domains/{id}/grants/{grant_id}/revoke` is idempotent and
  requires the exact domain/grant pair.

The UI retains a command key after an interrupted response. Grant replay returns
its current revoked state and never restores access; a replacement needs a new
explicit command. Authorization is checked before replay. Creation and grant keys
are namespaced by operation and initiating administrator.

The capability helper intentionally does not read financial grants. Conversely,
financial checks never read property-data grants. Complete source/derivative,
worker, audit, privacy-export and indirect-view coverage is required before a
future activation operation can be offered; see the property-data-domain design.
Populated downgrade refuses to erase prepared evidence. Repair forward. No live
provider or production activation is included.

Migration 0047 makes property/source row identity, creator and parent context
immutable before policy activation. An import's mode and target cannot be edited;
create a separate explicitly reviewed import for a different destination. An
applied import retains its original destination and approval receipt. Historical
null uploaders are not inferred from queue actors or approvers. This prerequisite
does not enable source permissions and does not certify cross-link consistency.
A populated downgrade refuses to remove these guards. Pause the relevant writers
and forward-repair through a reviewed migration; do not drop triggers to perform
an operational reparenting. Original bytes and source evidence remain preserved.

Migration 0048 validates the complete tenant edge of Unit → Property and import
edges of review issues and evidence. An evidence row's added import ID is copied
exactly from its immutable candidate parent. Its document must already be linked
to that same import. Older insert callers may omit the import ID; an invoker-rights
trigger fills it from the visible candidate, then the same foreign keys apply.
The worker refuses to claim work until the expanded evidence column is present.

Run this migration in a quiet window. It takes its final table locks with NOWAIT;
an active reader or writer makes it fail immediately and roll back. Quiesce traffic
before retrying. Its preflight reports counts of foreign-property units,
cross-import issues and evidence without a matching import/document link. Any
nonzero count aborts before schema expansion. Reconcile such rows explicitly;
do not create speculative links to make the preflight pass. Original data and
unknown ownership are unchanged by a failed migration. Populated downgrade refuses
to remove the strengthened constraints; forward repair remains the rollback path.
These edges do not grant source access or activate domain enforcement.

Migration 0049 prepares a common typed source-access resolver without attaching it
to any source policy. Existing reads remain in compatibility mode. Unassigned
creator ownership never authorizes publication; property-bound access requires an
explicit live domain grant. Mixed-property originals, multi-import originals and
unknown unbound creators are denied by the resolver and must be reconciled before
activation. Its internal actor-parameter functions are not executable by the
application role. There is no supported actor-impersonation or policy-toggle API.
The integration and atomic publication prerequisites are recorded in the
[property-source resolution design](../2026-10-02-property-source-access-resolution.md).
For an existing-target draft whose original is still unbound, the prepared resolver
requires an exact durable attachment-admission receipt from migration 0050. A
live uploader's explicit target prepare grant is captured when the original is
attached; losing that grant later does not remove the target audience's access.
Every viewer still needs their own live grant. Legacy attachments receive no
receipt from later grants. Direct SQL inserts cannot manufacture historical proof;
the receipt guard accepts only the nested attachment trigger. Historical sources
need a separately reviewed reconciliation workflow before activation.

Migration 0050 takes final table locks with NOWAIT; quiesce relevant traffic before
retrying a busy migration. Receipt, source link and canonical admission audit are
atomic and immutable. A receipt also fixes the original's eventual destination.
Populated downgrade refuses to remove proof. Preserve it and forward-repair.
No source-policy activation is included.

## Explicit new-property publication (0051)

An administrator who created an unassigned declaration import can optionally
prepare its new property audience in the approval dialog. The unchecked choice
explicitly requests read, prepare, approve and export for that administrator,
with a label and reviewed reason. Additional members can be added afterwards
through the existing domain administration. The API also accepts a bounded list
of explicit named grants. The publishing actor must explicitly receive read,
prepare and approve; no automatic grant is implied by administration or creation.

Destination, domain, grants, immutable publication proof, originals, derived facts
and audits commit together. If any step fails, all changes roll back. This option
does not activate source RLS. Legacy compatibility approval continues without
publication authority proof. A new audience command cannot retrofit an already
applied import. Historical source review remains a separate prerequisite.

After submitting, the dialog retains the exact confirmation for a retry and
disables editing it. To change the instruction, cancel and reopen confirmation.
New approval receipts require identical normalized payloads, including the draft
revision; a changed payload under the same key returns 409. Replaying evidenced
publication requires a current destination approve grant and never recreates
revoked grants. Earlier receipts without digests retain their compatibility
replay behavior, but cannot accept a new audience command.

Migration 0051 takes final NOWAIT locks on imports, import/document links and both
domain/grant FK parents. Busy readers or writers cause immediate refusal; quiesce
traffic before retrying. Populated downgrade refuses to erase proof or digests.
Worker readiness checks all added import columns before claims. Preserve evidence
and repair forward rather than dropping guards or widening policies.

## Current review of older unbound originals (0052)

Use **Quellenfreigaben** to inspect an eligible own original, its exact target,
revision and hash. Open the original, enter a reason and explicitly confirm the
current disclosure. Eligibility requires known original ownership and current
read, prepare and approve rights on that target. Sources with uncertain custody,
multiple links, a bound property, active work or an outstanding lease are excluded.
A separate review receipt records this decision; it never reconstructs historical
attachment authority. The original and financial rights are unchanged.

After an unconfirmed response, retry the same frozen command. A confirmed conflict
requires **Liste neu laden**, a fresh inspection and confirmation. The resulting
receipt has a stable detail link and remains subject to current source read rights.
Revoking the original reviewer's grant does not erase the accepted disclosure;
each viewer independently needs their current grant. Receipt, canonical audit and
exact source/grant chain are retained. Wrong-target binding and a second import
link for a reviewed source are rejected.

Migration 0052 uses NOWAIT locks on imports, links, original documents and both
domain/grant parents. Quiesce relevant traffic before retrying a busy migration.
Populated downgrade refuses; preserve proof and repair forward. This prepares
admission without activating general source RLS. Unknown or already-bound legacy
custody still needs explicit classification before organization activation, and
universal proof-required binding must be installed with that enforcement.

## Internal enforcement staging (0053)

The internal organization state remains PREPARATION by default. There is no
application write privilege or activation endpoint, and the readiness function
rejects strict activation until graph closure and coverage are implemented.
Do not replace that function or edit state manually outside the guarded disposable
integration tests. Presence of this table is not evidence of an active boundary.

When strict state is eventually enabled, declaration queue commands and replays
require current source prepare authority. Every worker phase resolves its durable
queue actor, including grant expiry/revocation. PAUSED rejects effects, retains
strict read authorization and durably cancels pending work through its existing
lease fence. Enforced state cannot be deleted or reverted to broad compatibility
access. Migration 0053 refuses populated downgrade; preserve state and repair
forward. Its dependency locks use NOWAIT on organization, user, imports and queue
receipts. The trusted definer role must retain its documented RLS bypass; a
misconfigured mode reader fails rather than silently treating hidden state as
PREPARATION. The application role stays NOBYPASSRLS with no state write privileges.


## Knowledge source and budget integration (0054)

Strict Q&A checks the complete expected source graph before preparation,
reservation and answer publication. Hidden or unauthorized source rows block the
whole request. A grant loss or pause during the provider call records a sanitized
fixed-reservation-cost cancellation even when ordinary ledger reads are withheld.
No provider content is stored in that cancellation path.

Strict reservation requires an internal `knowledge_budget_policy` row with the
reviewed organization cap, window length and reservation duration. These values
must mirror approved deployment configuration before future activation; missing
policy blocks requests. The database owns strict windows and expiry and counts
all organization reservations, including other properties hidden from the actor.
The app cannot read or modify policy or supply a replacement cap/window. Policy
changes retain administrator, reason, database timestamp and audit evidence under
the exclusive ACL lock. There is no new policy setup or activation endpoint.
PREPARATION continues to use existing application settings.

Migration 0054 uses NOWAIT locks on organization, user and Q&A ledger dependencies.
Populated policy or strict state refuses downgrade. Preserve evidence and repair
forward. General source RLS and organization activation remain unavailable pending
the remaining closure and coverage gates; this migration does not complete B1.

## Strict original binding (0055)

Future strict publication requires an unbound original, retained exact audience
proof, a matching applied import receipt and the same approver's current property
approval right. A newly inserted already-bound original is rejected. PAUSED denies
first binding. Strict originals cannot acquire a second import link or be attached
after binding. Successful publication has one canonical audit in its transaction.

This does not classify historical bound originals or enable source RLS. Complete
legacy coverage and the remaining indirect disclosure/effect checks still gate
activation. Migration 0055 takes NOWAIT locks on originals and their import links;
strict state refuses downgrade. Preserve evidence and repair forward.

## Current classification of bound originals (0056)

Use **Altquellen prüfen** for originals already associated with a property. Only a
current administrator with explicit read, prepare and approve rights on that
property-data domain can record a decision. **Quelle freigeben** additionally
requires the known uploader to be that reviewer, an active original and coherent,
quiescent source context. Unknown custody or conflicting/multiple import links
permit only **Quelle sperren**. Such originals have no preview; the list supplies
only bounded identifying metadata and never exposes their filenames or bytes.

Both choices prepare future enforcement. Neither changes current compatibility
access, activates source RLS or retroactively supplies historical custody. The page
states this explicitly. The original, reviewed source links and classification
receipt remain immutable. Link role/order changes also invalidate an outstanding
review and are forbidden after classification. The exact hash, context digest,
reviewer, grant chain, database clock and reason are retained with a canonical audit.
New classification effects are rejected while source state is PAUSED.

After an unconfirmed response, repeat the same frozen command. A confirmed conflict
requires list refresh, renewed inspection and confirmation. Changing the selected
decision clears confirmation. Existing receipts remain subject to current access.
Source metadata uses bounded, actor/query-bound cursor pages; document-ID search
can locate a particular original without searching undisclosed filenames.

Migration 0056 takes NOWAIT locks on originals, imports, links, domains and grants.
Populated downgrade refuses to discard classification evidence. Preserve it and
repair forward. Integrating classification into strict source resolution, making
quarantine dominate earlier proof, and enforcing complete activation coverage are
still subsequent requirements.
