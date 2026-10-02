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

## Strict bound-source resolution (0057)

The prepared strict resolver now honors the classifications above. QUARANTINED
always denies source access in ENFORCED and PAUSED state, including when earlier
admission evidence exists. ADMITTED must match the original hash, property,
uploader and exact current source context. Existing valid attachment, unbound
review and explicit new-property publication evidence remain valid. A historical
property association alone is insufficient. Current reader grants remain required
independently of the retained reviewer's former grant.

Classification commits take the exclusive organization ACL lock before source
rows, serializing decisions with in-flight authorization. Q&A checks the complete
expected graph and refuses partial input when a denied original is hidden by RLS.
Workers reject incompatible bound sources before loading them. These are staged
strict-mode integrations: ordinary compatibility access is unchanged and there is
still no supported activation command. Full indirect disclosure/effect coverage
and source CRUD/RLS remain required.

Migration 0057 refuses busy proof/source dependencies immediately and refuses
rollback while any organization is ENFORCED or PAUSED. A PREPARATION rollback
retains classifications and restores the earlier prepared resolvers.

## Original export authority (0058)

Every property-original PDF endpoint now checks explicit export authority before
loading the document or storage bytes. In strict state, ordinary source access
needs both current read and export rights and valid source proof. Review previews
retain their narrow known-own-original exception so an original can be inspected
before admission, but also require export rights on the exact destination domain.
A QUARANTINED decision blocks strict review preview even for the original uploader.
PAUSED keeps these strict read/export checks; it does not widen access.

Both review lists report current preview availability. They hide the PDF link when
export is unavailable. PREPARATION retains existing preview compatibility. All
successful paths still read storage once, verify length and hash, and return those
same bytes. The ACL lock remains held during that bounded read. Migration rollback
refuses strict state; no activation capability is added.

## Financial calculation input (0059)

Strict calculations and coverage attestations require read access to the complete
property-source graph independently of financial scope grants. An unproven or
quarantined expected original blocks calculation even when RLS hides it. This is a
conservative property-wide check, including historical sources. It neither calls
Q&A providers nor depends on their budget policy. Pausing source effects also stops
these calculations. Existing compatibility behavior is retained in PREPARATION.

The database guards calculation snapshots, result lines and coverage declarations
as well as the service preflight. Coverage authority derives from the referenced
settlement/plan; its declared property must match. Computed draft reparenting checks
both audiences. Eight migration dependencies use NOWAIT; strict state refuses
rollback. Current stored-result, report and privacy disclosure closure remains a
separate prerequisite, and source activation remains unavailable.

## Stored financial disclosure (0060)

In strict and paused state, computed/final settlement and plan rows, their lines,
and coverage declarations require complete current source READ authority. Parent
lookups use trusted metadata, and coverage must match its actual parent's property.
Only empty draft parent metadata retains its existing financial visibility.
Snapshot contents and hashes remain unchanged. PAUSED permits authorized stored
reads while calculation and attestation effects stay blocked.

All four financial PDF routes additionally require exact financial EXPORT and
whole-source EXPORT before line loading or rendering. Unbound legacy outputs
cannot be exported in ENFORCED/PAUSED until reviewed into an accounting scope;
PREPARATION retains legacy compatibility. Eight dependencies use NOWAIT locks and
strict state refuses rollback. This closes these stored output tables and PDF
routes; it does not complete general reports, privacy/audit or source activation.

## Canonical property and unit access (0061)

Strict property/unit lists, details, selectors and raw SQL require their current
data-domain grant and complete source READ authority. Unproven or quarantined
sources also hide copied master fields. Editing additionally requires prepare;
PAUSED preserves reads but denies master writes. Units cannot be moved to an
unauthorized property by raw SQL.

New strict properties require an explicitly chosen audience. Domain/grants and the
property commit atomically; an administrator role alone grants no source access.
The manual creation form offers an unchecked choice with a reason. Its request key
survives retry; audience creation requires that key. Reviewed declaration publication
uses the same audience-before-parent ordering. An admin can prepare a data domain
for a known property ID without obtaining its source fields.

The new property creation receipt is immutable and actor-scoped. Rollback refuses
to remove retained receipts or strict authority. Keep source activation disabled
until the remaining original/derived source and indirect disclosure boundaries are
closed and legacy coverage is reviewed.

## Derived source records (0062)

Strict import children use current parent source READ/prepare authority. Canonical
facts and relations use the current property audience plus the complete expected
source READ graph; writes require approve and an APPLIED matching origin. Typed
subjects and referenced originals retain their own source checks. Existing column
privileges and immutable origin/receipt guards remain in place.

Stored Q&A reads use current whole-source READ, including during PAUSED. Parent
preflights prevent hidden children from appearing as empty review drafts; direct
uploads check prepare before storage. Existing-property approval and applied
replays check approve. Metadata-only row contexts support first inserts without
widening access. All sixteen migration dependencies use NOWAIT, and strict state
refuses downgrade. Original/import row policies and purpose-specific review paths
remain outstanding; source activation is still unavailable.

## Ordinary originals and imports (0063)

Strict ordinary rows now require current custody/destination authority for read
and prepare, with approve for applied-import changes and first original binding.
New drafts must identify the current creator and begin unapplied. PAUSED retains
reads and rejects original/import effects. The API checks creation before ORM
writes, and statement ACL locks precede row locks.

Legacy review uses separate bounded metadata and purpose-only original readers;
it never adds an exception to ordinary source SELECT. Admission pages use signed
actor/query cursors and stable UUID order. Review previews retain explicit target
export, exact custody and quarantine checks, and verify stored bytes once. All six
migration dependencies use NOWAIT, strict state refuses rollback, and compatibility
round trips preserve sources. Indirect disclosure and reviewed activation coverage
remain open; source activation is still unavailable.

## Privacy retention completeness (0064)

Retention status, erasure review and privacy export now check the expected finalized
settlement/plan recipient graph independently of ordinary source-filtered rows.
Every expected property's complete source READ or EXPORT authority is required in
addition to financial access. Discovery rechecks completeness after loading bindings
so grant expiry cannot turn hidden records into apparently absent retention.
All privacy routes recheck the current administrator inside the data transaction.
PAUSED retains authorized inspection/export and existing erasure rules; originals
and retention periods are unchanged. Eight NOWAIT dependencies and strict rollback
refusal protect the boundary. Person/history, audit/report and activation work remain.

## Audit source disclosure (0065)

Strict audit readers must be able to read the referenced typed resource through
its ordinary RLS policies. Financial output audits also require the complete source
graph for an otherwise visible empty draft. Unknown, missing and foreign references
remain hidden; audit author identity and configuration administration do not grant
source access. Authorized reads survive PAUSED mode. Audit writes remain append-only
and do not require returning the newly written row.

Administrator control metadata uses exact known record identities. Internal control
tables retain their lack of direct app SELECT rights. Person/history and meeting
audit kinds are preactivation exclusions until their policies are integrated.
No event history is rewritten, and strict rollback refuses to remove this boundary.
See [audit disclosure evidence](../2026-10-02-source-audit-disclosure.md).

## Meeting source boundary (0066)

Strict meeting, agenda, resolution and register readers require the complete
property source audience. PREPARE governs ordinary edits; APPROVE governs register
entry and annotation. PAUSED allows authorized reads/exports and refuses effects.
Both PDF routes require READ plus EXPORT throughout collection/rendering/release.
Mixed historical parent relationships block the affected meeting graph, including
exports, instead of silently removing rows. Preserve those records for reviewed
remediation; never fix their audience by guessing another property.

Meeting audit events now use the same source contract. Strict missing/deleted
references remain hidden. Audience/parent identities cannot move; optional agenda
associations retain same-meeting lifecycle behavior. Existing register immutability
and history survive migration and rollback rehearsal. See
[meeting boundary evidence](../2026-10-02-meeting-source-access.md).

## Relationship history source boundary (0067)

Strict lease and ownership history reads require complete property source READ;
changes require PREPARE and a non-anonymized person. Expected profile/person/unit
organization coherence is checked before consumers can treat the source graph as
complete. Preserve malformed history for reviewed remediation instead of interpreting
filtered rows as vacancy. Person-linked privacy decisions and exports require all
expected history properties, including inactive relationships.

History audience IDs and profile-to-person identity cannot be reassigned. Existing
financial-input serialization remains in force; a writer released after anonymization
is rejected using current person state. PAUSED retains authorized reads while
stopping effects. History audit entries inherit the same ordinary source boundary.
See [history evidence](../2026-10-02-history-source-access.md); contact custody and
full person/profile access remain pending before activation.

## Contact audience preparation (0068)

Use Team → Kontaktdaten-Rechte to prepare an independent audience for an exact
contact. Administrative setup gives no implicit contact permission. Property and
financial grants remain independent. Only READ, PREPARE and EXPORT are available;
READ is required and current READ_ONLY members cannot exercise PREPARE.

Domain labels are generated from the contact UUID. Select a controlled reason
code; do not put personal data in administration metadata. Contact selection uses
ordinary person reads. Grant replacement requires revocation and a new request;
retrying a committed request never restores revoked rights. Expiry uses DB time.
Configuration and actor evidence are immutable. Populated configuration or strict
source mode blocks downgrade; do not remove evidence to force production rollback.

This is preparation only. Person/profile source policies and downstream contact
completeness remain follow-up work. The activation gate remains false. See the
[contact authorization design](../2026-10-02-contact-data-domain-design.md).
