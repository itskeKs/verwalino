# Organization export

Organization exports are portable product data, separate from the paired
PostgreSQL dump and document backup described in [DEPLOY.md](../../DEPLOY.md).
They contain personal data and originals. Store and transfer them privately.

## Delivery boundary

Owner-administrators can export, import into an empty organization, and request
organization erasure. **What the admin may see is what is exported.** Export
uses administrator RLS and column privileges; there is no full-transfer definer.
Missing private provenance can therefore make an archive non-importable. The
importer refuses missing dependencies with a German explanation and a collapsed
technical detail block containing entity, source IDs and count;
it never silently drops them or borrows another organization's rows.
Every hashed `entities/` file must appear in `entity_order`; an unlisted entity
file is refused with 422 before restoration, rather than silently skipped.

The same-database round trip uses synthetic organizations A and B. The maintainer
must still rehearse a real export into a fresh self-host installation; the local
checks do not start that stack or replace installation acceptance.

## API and lifetime

`POST /api/organizations/export` returns a gzip-compressed tar archive directly.
`X-Export-ID` identifies it; `GET /api/organizations/export/{id}` downloads the
same artifact. Both routes require the live `OWNER_ADMIN` role and derive the
organization exclusively from the authenticated session. Foreign and unknown
export IDs both return 404. Responses use private, no-store caching and an
attachment disposition. No public URL or bearer download token is issued.

The audit event commits before an artifact is atomically published or bytes are
disclosed. Unpublished crash remnants also expire during cleanup.
One creation per organization per minute is allowed; simultaneous creation is
refused. Download checks current export visibility again and refuses a stale
artifact when previously exported values or permissions have changed. Request
a new export in that case. A replacement removes the previous artifact after
commit; a failed replacement preserves it. An open download can finish even
when a replacement removes its pathname.

Artifacts live under
`HV_DOCUMENT_STORAGE_ROOT/.organization-exports/<organization UUID>/<export UUID>.tar.gz`
with private directory/file permissions. This separate directory is not the
original-document store and does not consume duplicate original-document quota.
Artifacts expire after 24 hours; creation opportunistically removes expired
artifacts. Operators must also invoke `python -m app.organization_export cleanup`
regularly, including when no new exports are requested. The command opens no
database and never removes original documents. Audit events remain append-only.

## Version-two format (version one remains importable)

The archive contains only regular files. It preserves no host paths, filesystem
owners or source permissions. All metadata is UTF-8.

| Member | Contract |
| --- | --- |
| `manifest.json` | `format: verwalino-organization-export`, integer `version: 2`, creation timestamp, application version, release schema head, source organization ID/name, `entity_order`, `row_counts`, member `sha256` map, document-to-entity mapping and visibility exclusions |
| `entities/<entity>.jsonl` | One JSON object per row, newline terminated; an empty visible entity has an empty file |
| `documents/<document UUID>` | Verified original bytes, without filename or host-path dependencies; the manifest maps the UUID to its entity |
| `csv/payment.csv`, `csv/cost_entry.csv` | Companions from the existing CSV renderers, retaining their German headers, exact amounts, UTF-8 BOM and spreadsheet-injection protection |
| `README.md` | Embedded format and visibility explanation |

SHA-256 covers every member except `manifest.json`, which cannot hash itself.
It is an integrity check, not proof that an archive was issued by a trusted party.
Document content and recorded size/hash must agree before an archive is published.
A missing or corrupt original refuses the whole export; no partial artifact is
published. Rows with a UUID primary key carry `source_id`: the original identity survives
repeated transfers even when `id` changes. Rows with composite natural keys keep
those constituent keys; no synthetic primary key is invented. Version-one rows
use `id` as their source identity. UUIDs remain strings. Top-level numeric database columns are exact
strings, dates/timestamps are ISO 8601, and booleans/null retain their JSON types.
Numbers embedded in JSON snapshots retain their numeric type and precision.

The stable product order is organization, management mandates, properties, units,
persons, tenant/owner/vendor profiles, leases/rent history, ownership, global cost
categories, costs, bank accounts/transactions, payments, settlement runs/lines,
economic plans/lines, special levies/allocations, dunning, meters/readings, letter
templates/documents, invoice/attachment/property/mandate/intake originals, document
references, users, memberships and audit events. Additional visible provenance
entities follow alphabetically. The manifest gives the authoritative order and
counts for this archive. The file order is not a promise that raw database inserts
in that order are sufficient: users, cyclic references and generated provenance
need an import adapter. Global categories retain their stable `code` and
`plan_version`; the importer resolves these against its seeded catalog.

## Visibility, credentials and limits

The data snapshot is one SQL statement as the ordinary administrator connection,
with transaction-local organization/user context, SELECT RLS and column grants.
Existing source export capabilities apply in addition to read visibility.
Foreign-key dependents are omitted when their required parent is not exported;
this also prevents readable child text from circumventing a parent's export
restriction. Audit subjects and the source identities in import/dunning receipts
must also survive filtering. Audit events without an exported typed subject are
omitted. Prepared mail originals are included only with their export-authorized
document and original recipient. Recount-only paths do not grant file access.
No privileged data connection or definer aggregation exports rows.

The manifest lists inaccessible entities and excluded column names. It never
reports hidden row IDs or hidden counts. Password hashes, authentication/reset
tokens, worker lease tokens and session generations are excluded, including
credential fields in structured audit payloads. Global authentication/setup/signup
and throttle tables are outside the export. Original files are copied unchanged;
their uploaded content can itself contain sensitive information.

Metadata rows are currently buffered in memory. Archive output and individual
documents spill to private temporary files beyond 8 MiB, and downloads stream
in 64 KiB chunks. Large asynchronous export jobs remain pending; do not infer
that the current direct-download path has been validated for arbitrarily large
self-host databases. The current storage backend is local filesystem only.

Organization deletion must remove these transient exports as well as reconcile
personal data under [ADR-0012](../adr/0012-gdpr-erasure-vs-gobd-retention.md).
Retained originals, frozen financial records, audit/provenance and their required
dependencies cannot be erased or rewritten to make an import/deletion test pass.

## Import and refusal rules

`POST /api/organizations/import` accepts multipart field `file` and requires the
live `OWNER_ADMIN` role. `/api/organizations/me` reports `import_available` for
administrators and the current deletion state for members. The admission check
counts every domain table, including rows hidden by source RLS: any domain row
returns **409**. Existing users, memberships and audit events alone do not make
a new organization non-empty. The admission function reveals only this boolean
and collisions of administrator-supplied UUIDs, never foreign row contents.

The importer checks the format, safe regular-file members, exact manifest/member
hash coverage, row counts, organization identity, and the release's Alembic
ancestry. The same or an older known schema is accepted; newer/unknown heads are
**422**. Version 1 and 2 are accepted. Hashes provide integrity, not issuer
provenance. Compressed input is limited to the existing 25 MiB upload limit (26 MiB raw multipart envelope), expanded input to 512 MiB and
metadata to 32 MiB, decoded metadata to a conservative 64 MiB memory budget,
individual JSON rows/manifest to 2 MiB, 20,000 rows and 10,000 regular members,
and each original to 64 MiB. Gzip and PAX/GNU extension headers are refused before their
payload is read; official v1/v2 exports do not use them. Larger imports need a worker-based transfer increment.

Every UUID collision is consistently remapped. Collision-free entity IDs are
preserved (including on a fresh installation). The organization always maps to
the destination. Global cost categories resolve by `code` and `plan_version`.
References, including UUIDs in structured snapshots, follow the same map.
Frozen snapshot hashes are verified against the source payload and recomputed
for the newly mapped payload before insertion; source originals remain exact bytes.
Imported source identities are recorded in the immutable import audit receipt
and re-exported as `source_id`; imported historical receipts do not override the
new local receipt. The latest local completion receipt takes precedence. Request identifiers are regenerated consistently. The target organization keeps its ID; its descriptive row is restored from the
archive. Its existing administrator account is kept. Users match normalized
email addresses within the destination only. Other users are inactive with an
unusable password; an administrator must reset their password and activate them
before login. A different bootstrap mailbox remains an additional local account;
round-trip count comparisons use a matching bootstrap mailbox and exclude the
new local import audit receipt. Contact creation records the importing administrator as the new
custodian, without claiming the historical author performed a local operation.

Rows are inserted in dependency order in one transaction under ordinary runtime
RLS and integrity guards. Original registration reuses its native validated
trigger; a transaction-local registration map supplies the chosen reference ID
while kind/hash/size and the new registration timestamp still come from the
native original/registration path. Registration time is local import provenance,
not a claim that registration occurred earlier. Files are restored through `put_document`, with upload, storage and
property/unit quotas. Rollback removes newly written files and all inserted rows.
The result reports archive row counts and the source-to-target entity ID map.

Unresolved FK dependencies produce **422** with a German `message` and
`missing_dependencies` entries (`entity`, `source_ids`, `count`), also recorded
in the application import log. No domain rows or files are committed. Cycles and
historical configurations that cannot satisfy current custody/integrity rules
are also refused atomically. In particular, legacy scopes, historical revoked grants and private native invoice-upload
admissions require a dedicated reviewed restoration adapter. Invoice archives
currently list the unavailable `invoice_upload_admission` document IDs and are
refused before writes; ordinary import
must not forge historical authority or private evidence. Database refusals expose
only the entity/source IDs and a stable code, never SQL or target data.

## Organization erasure

`POST /api/organizations/deletion` creates a request and returns a retention
preview by entity/count/latest deadline, erased categories, exact organization
name, a fresh export offer, and the German consequence notice. It returns
aggregates for private evidence, never its hidden row IDs or values. Immediately
before confirmation the UI strongly recommends a fresh export. Confirmation at
`POST /api/organizations/deletion/confirm` requires `organization_name` to match
exactly and recomputes retention. It begins a **seven-day cooling-off period**.
The organization is read-only, with a banner on every signed-in page; export,
logout and owner-admin cancellation remain available. `POST
/api/organizations/deletion/cancel` restores normal operation during this state,
including after the deadline if an operator run has failed or has not happened.
Cancellation and actual erasure serialize on the lifecycle lock; cancellation
cannot restore an organization whose erasure has already committed.

The database write guard takes **no advisory lock**. It reads
`organization_deletion.status` with a plain SELECT on each statement's visible
state, without a transaction-local cache. Under READ COMMITTED, a write that
commits before the state change is visible is allowed; once cooling-off commits,
every later statement is refused. Already-running statements are not drained.
This allows runtime operations that hold an exclusive ACL lock on one connection
and attest through a second connection to complete. `/api/organizations/me` reads
organization/lifecycle state only. Its `import_available` flag describes an
administrator's ability to attempt an import; it does not certify emptiness.
The exclusive ACL lock and domain-table admission scan run only in the import
request, which refuses a nonempty destination atomically.

The operator runs `organization-deletion-run` after the deadline; see
[DEPLOY.md](../../DEPLOY.md#organization-erasure-and-retained-record-access).
The durable status becomes `deleted_pending_retention`. Every account is
inactive, credentials/contact data are neutralized, old sessions are invalidated,
and **nobody, including the former owner, can sign in**. Retained records have no
former-owner access path; only the operator can retrieve them during retention.

| Data | Implemented treatment |
| --- | --- |
| Confirmed costs, booked payments, finalized settlements/plans and their lines, issued dunning | Retain unchanged; explicit retention date or ADR-0012's provisional calendar-year-end + eight years |
| Saved originals (including attachments/issued letters), registry entries | Retain unchanged with original bytes; existing immutable-original guards stay in place |
| Audit, immutable source/security/allocation/computation evidence, resolution register | Retain unchanged; no privilege or trigger bypass for erasure |
| Required parent/profile/lease/ownership/property/bank dependencies | Preserve structural rows/FKs through the latest dependent deadline; only voucher ancestry binds a person's readable fields |
| Persons without a binding or with every known binding expired | Preserve IDs/structural references; neutralize all ADR-0012 contact/address/bank fields and mark anonymized |
| Users and organization label | Preserve account IDs for audit references; neutralize contacts/credentials, deactivate everyone and neutralize the organization label |
| Mutable retained configuration text | Replace with `Gelöscht` or empty JSON; keep IDs/FKs, and replace assignment stable keys with `Gelöscht <row UUID>` to preserve uniqueness |
| Other unretained mutable rows, drafts, notes | Delete in dependency order; any database guard failure rolls back the transaction |
| Unreferenced document files and transient exports | Remove only after successful database commit; never remove retained URIs |

`organization_retention_catalog()` derives coverage from every public table
carrying `organization_id`, its primary key, runtime DELETE privileges, DELETE
policies and enabled DELETE triggers. Missing keys are unclassified and stop the
plan. Reviewed authorization/locking triggers do not imply immutable content;
unknown DELETE triggers conservatively retain their table. Conditional financial
guards keep confirmed/booked/finalized/issued rows (and adopted costs or quarantined
dunning history). Other delete-protected rows keep their structure and guarded content; mutable configuration text is minimized as listed below. The
planner closes over required foreign-key ancestors. The schema contract checks
every organization table, including newly created tables and newly attached guards.
Live `sepa_creditor`/`sepa_mandate` configuration is a deliberate mutable-consent
category: absence of a runtime DELETE action alone does not turn unused consent
into a retention binding. Their DELETE triggers only acquire input locks, so
unused rows are deleted; a required retained dependency still keeps them.

These formerly omitted tables are all **retained verbatim**, including existing
reason text, labels, identities, capabilities and source/context references:

| Tables | Delete protection |
| --- | --- |
| `person_data_domain`, `person_data_grant` | `protect_person_data_setup`: domain content is immutable; grants permit only one-way revocation |
| `property_data_domain`, `property_data_grant` | `protect_property_data_setup`: domain content is immutable; grants permit only one-way revocation |
| `dunning_scope_context` | `protect_dunning_context`: insert only |
| `property_source_admission` | `protect_property_source_admission`: insert only |
| `special_levy`, `special_levy_allocation` | `guard_special_levy`: append only; allocation basis frozen at creation |

These guards remain unchanged. Their references preserve a person's row and FKs,
but do **not** justify retaining readable person master data. Person deadlines
propagate separately from structural retention, starting only at confirmed or
adopted costs, booked payments, finalized settlements/plans and their lines,
issued or quarantined dunning, retained invoice originals and issued letter
documents. The closure follows their actual ancestors, plus the existing legacy
WEG/plan ownership fallback. Only a person reached from one of these voucher
categories with an unexpired voucher deadline remains verbatim and restricted.
Every other person is anonymized, including persons referenced by domains,
grants, dunning contexts or levy allocations. A new configuration reference
cannot extend an expired voucher's personal deadline. No immutable domain,
grant, source receipt or levy is rewritten.

The operator sets `SET LOCAL app.organization_erasure = 'on'` inside the erasure
transaction. Four authorization functions allow DELETE only when both
`current_user` **and** `session_user` are `app_migrator` and that flag is on:
`authorize_bound_financial_write`, `authorize_dunning_write`,
`guard_financial_source_input`, `guard_strict_financial_resource`.
The login check matters because these functions are `SECURITY DEFINER`.
`app_rw` cannot use the flag to acquire authority. `authorize_bound_payment_write`
fires only on UPDATE; erasure never updates payments, so it needs no exemption.
Declaration effect/review guards already admit their migration owner. Other
authorization-bearing DELETE guards also protect immutable evidence and are
left intact; their rows are retained.

The mutable policy guards `guard_knowledge_budget_policy` and
`guard_property_source_security` additionally admit a **reason-only UPDATE**
under the same operator checks; all other columns must be identical. Their
DELETE refusals and all financial/evidence immutability guards remain effective.
Migration 0147 replaces these six function bodies and removes the exact inserted
clauses on downgrade, restoring the previous definitions, ownership and grants.
No trigger is disabled. The setting expires with the transaction.

The following mutable retained fields are scrubbed. Hash verification excludes
these intended changes and their derived search vectors; other retained content
still has to match its pre-erasure hash.

| Table | Scrubbed columns |
| --- | --- |
| `letter_template` | `name`, `subject`, `body` |
| `property_assignment` | `stable_key`, `designation`, `location`, `description`, `details_json` |
| `property_legal_reference` | `district`, `cadastral_district`, `field_number`, `parcel`, `sheet`, `details_json` |
| `property_rule` | `search_text` (and generated `search_vector`) |
| `property_rule_source` | `printed_page`, `section`, `quote`; `field_path` becomes NULL so sources on the same page remain distinct rows without a key collision |
| `approved_fact_source` | `search_text` (and generated `search_vector`) |
| `knowledge_budget_policy`, `property_source_security` | `reason` only |

Retention keys, legal bases, deadlines and content hashes are persisted in the
deletion record. Active-bound person processing flags may change; expired-bound personal fields are
anonymized under ADR-0012 §5. Their structural keys remain for immutable references.
Retained voucher and
source contents do not. The CLI retries post-commit file cleanup for completed
organizations on every invocation, so an interrupted cleanup is recoverable.
Each organization has its own session/transaction. A failure logs its organization
ID, rolls back its database work and continues with the next organization. The
CLI prints the completion count and failed organization IDs separately and exits **1** if
any organization or post-commit cleanup failed (otherwise **0**). A failed
database erasure stays due in `cooling_off`; rerun `organization-deletion-run` to
retry it, or cancel through the existing owner-admin action before erasure commits.
Database failure never deletes files. Storage errors before commit refuse the
run; a post-commit filesystem error needs a CLI retry while login stays blocked.
Expiry does not automatically purge immutable evidence: that remains a separate
operator/legal decision under [ADR-0012](../adr/0012-gdpr-erasure-vs-gobd-retention.md).
Its provisional retention durations still require the documented domain sign-off.

### Owner decision: personal content remaining in immutable evidence

In addition to voucher/original content and the audit payloads below, these exact
table/column pairs can retain personal text or JSON in guarded evidence. Their
immutable columns cannot be scrubbed. The resolution register's mutable legal
annotation is distinguished below. IDs, hashes, fixed enums and numeric/date fields are omitted
from this content inventory. A table entry lists each surviving column of that
table explicitly; it does not authorize rewriting any of them.

| Table | Guarded personal-content columns remaining verbatim |
| --- | --- |
| `person_data_domain` | `label`, `reason_code` |
| `person_data_grant` | `reason_code` |
| `property_data_domain`, `property_data_grant` | `reason` (`property_data_domain.label` also remains) |
| `accounting_scope` | `label` |
| `accounting_scope_binding`, `scope_grant`, `scope_control_event` | `reason` |
| `dunning_scope_context`, `dunning_quarantine_disposition` | `reason` |
| `invoice_review_revision` | `seller_name`, `document_number`, `reason` |
| `property_rule` | `stable_key`, `topic`, `title`, `summary`, `scope_json`, `effect_json` |
| `approved_fact_source` | `field_key`, `approved_value_json`, `printed_page`, `section`, `quote` |
| `access_coverage_snapshot` | `manifest` |
| `access_disposition_acceptance` | `assessment`, `reason` |
| `access_grant_proposal`, `access_grant_acceptance` | `changes` (proposal), `results` (acceptance) |
| `source_security_transition` | `reason` |
| `scope_migration_exception` | `evidence` |
| `scope_payment_reconciliation` | `reason` |
| `runtime_budget_review` | `reason`, `policy` |
| `runtime_budget_attestation` | `policy` |
| `property_source_admission_review`, `property_source_classification` | `reason` |
| `coverage_declaration` | `values`, `source_document_ids`, `reason` |
| `financial_computation_attestation`, `financial_computation_receipt` | `state` |
| `allocation_correction` | `impact_snapshot`, `reason` |
| `allocation_correction_completion` | `basis`, `reason` |
| `allocation_correction_context`, `allocation_correction_impact` | `context` (context), `snapshot` (impact) |
| `allocation_proposal` | `reason` |
| `allocation_proposal_line` | `description`, `category_code`, `category_label` |
| `allocation_set` | `duplicate_snapshot`, `reason` |
| `allocation_treatment_line` | `documentary_basis`, `markers` |
| `allocation_treatment_review` | `snapshot`, `reason` |
| `assignment_proposal` | `blockers`, `reason` |
| `context_decision` | `snapshot`, `quotation`, `xml_path`, `comparison_note` |
| `duplicate_comparison` | `evidence`, `reason` |
| `duplicate_exception_dependency` | `cause`, `evidence` |
| `exception_case`, `exception_dependency` | `cause` |
| `exception_case_event` | `reason` |
| `exception_completion` | `basis`, `dispositions`, `comparisons`, `reason` |
| `extraction_revision` | `payload` |
| `field_evidence` | `field_name`, `value`, `bbox`, `quotation`, `xml_path` |
| `intake_batch` | `manifest` |
| `intake_item` | `client_item_key`, `filename` |
| `intake_extraction_attempt` | `result` |
| `intake_extraction_retry`, `intake_retry_receipt` | `reason` |
| `declaration_import_pass` | `safe_metadata` |
| `declaration_review_context`, `declaration_review_receipt` | `prior_resolutions` (context), `response` (receipt) |
| `dunning_batch`, `master_import_batch`, `job_attempt`, `outbox_delivery_attempt` | `result` |
| `logical_document`, `mapping_version`, `source_partition_revision` | `reason` (`mapping_version.identifier_value` and `source_basis` also remain) |
| `visual_disclosure_event`, `visual_extract_artifact` | `reason` (`visual_extract_artifact.renderer_profile` also remains) |
| `visual_extract_attestation` | `payload` |
| `resolution_register_entry` | `location`, `wording` |

`resolution_register_entry.challenge_annotation` also remains as part of the
retained legal register (ADR-0011). Its guard **does allow** setting, updating and
clearing this legal challenge/repeal annotation; it is not an immutable column.
Erasure preserves this legal evidence category, including annotations, rather
than treating it as ordinary configuration. Whether its personal content needs
a separate minimization policy remains an owner/domain decision alongside the
immutable payloads listed here.

Original-document titles, filenames, deed references, archive/scope reasons,
invoice extraction payloads and issued-letter recipient/subject/body also remain
as part of their retained originals. Their exact pairs are
`attachment.title`, `attachment.original_filename`, `attachment.archive_reason`,
`invoice_document.original_filename`, `invoice_document.extraction_payload`,
`mandate_document.title`, `mandate_document.original_filename`,
`mandate_document.scope_reason`, `mandate_document.archive_reason`,
`property_document.title`, `property_document.deed_reference`,
`property_document.original_filename`, `letter_document.recipient_name`,
`letter_document.subject`, `letter_document.body`. Retained structural UUIDs
continue to link this evidence to anonymized person rows.

Organization erasure retains `audit_event` unchanged, including `payload_diff`.
In particular, `mail_prepared` stores the recipient's **email address** and
**`person_id`**, plus request/message identifiers, preparation/completion timestamps,
delivery state and the generated PDF's hash, storage URI and byte size. The retained
PDF may itself contain the recipient's name/address and personalized document
content. Related `mail_completed` receipts retain the request ID, delivery status
and completion timestamp, allowing them to be joined to `mail_prepared`.
These payloads are not scrubbed even when the referenced
unbound person's master-data fields become `Anonymisiert`/NULL; JSON references do
not create a person foreign-key retention binding. Thus anonymizing a person does
**not** erase the recipient mailbox or the link to that person from historical
mail receipts. Other immutable audit payloads can retain historical labels,
reasons, free text and source identifiers as originally recorded.

This preserves immutable delivery/audit evidence under ADR-0003 and the current
ADR-0012 §4 boundary; it is not a decision that every audit field must legally be
retained for that duration. Whether these recipient fields/originals need a
different retention category or a separately reviewed minimization mechanism
remains an **owner/domain decision**. This change leaves audit immutability intact
and documents the surviving personal data for that decision. Former accounts
cannot access these retained receipts; access remains operator-only.
