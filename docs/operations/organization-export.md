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

Failed self-hosted imports remove only previously absent destination files,
before the database rollback releases exclusive import admission. Existing files
remain untouched. Deferred integrity checks run before the importer returns, so
their refusals use the same protected cleanup path. Hosted imports retain the
storage layer's transaction and filesystem locking. Ordinary unlimited storage
operations do not acquire hosted quota locks or change their cleanup behavior.

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
cannot restore an organization whose erasure has already committed. Cancellation
changes only the status; the confirmation timestamp, original deadline and
retention snapshot remain as evidence. Direct runtime UPDATE permits only
`cooling_off` → `cancelled`. Guarded database entry points handle request and
exact-name confirmation, compute the retention snapshot and set the deadline
from the database clock plus seven days. Runtime SQL cannot supply a deadline or
mark an organization erased; execution belongs to the operator.

The database write guard takes **no advisory lock**. It reads
`organization_deletion.status` with a plain SELECT on each statement's visible
state, without a transaction-local cache. Under READ COMMITTED, a write that
commits before the state change is visible is allowed; once cooling-off commits,
every later domain write is refused. Already-running statements are not drained.
Every organization table carries the lifecycle trigger. Audit insertion,
reset-token cleanup and lifecycle decisions are explicit exceptions inside the
trigger; lifecycle UPDATE additionally uses the cancellation-only transition
guard. The schema contract fails if a new organization table lacks the trigger.
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
still has to match its pre-erasure hash, except for the personal event/receipt
payload changes described below, which are checked against the expected scrubbed
row instead.

| Table | Scrubbed columns |
| --- | --- |
| `tenant_profile`, `lease_agreement`, `owner_profile` | `notes` |
| `vendor` | `notes`, `tax_id`, `vat_id` |
| `owners_meeting` | `protocol_text`, `invitation_text` |
| `agenda_item` | `title`, `description` |
| `letter_template` | `name`, `subject`, `body` |
| `property_document` | `deed_reference` (mutable metadata; the original file is unchanged) |
| `property_assignment` | `stable_key`, `designation`, `location`, `description`, `details_json` |
| `property_legal_reference` | `reference_type`, `district`, `cadastral_district`, `field_number`, `parcel`, `sheet`, `details_json` |
| `property_rule` | `search_text` (and generated `search_vector`) |
| `property_rule_source` | `printed_page`, `section`, `quote`; `field_path` becomes NULL so sources on the same page remain distinct rows without a key collision |
| `approved_fact_source` | `search_text` (and generated `search_vector`) |
| `knowledge_budget_policy`, `property_source_security` | `reason` only |

Editable profile, lease, vendor and owner fields are scrubbed even when their rows
must survive as dependencies of immutable evidence or expired vouchers. Their
free text and identifiers are not themselves frozen voucher content. The catalog
inventory below checks every retained text/varchar/JSON column, including parents
kept only by the recursive FK closure. Meeting invitation/protocol text and agenda
titles/descriptions are mutable source content and are scrubbed even when a
registered resolution requires those rows to survive.
`allocation_proposal_line.description` and `context_decision.comparison_note`
are guarded insert-only evidence and remain unchanged.

Retention keys, legal bases, deadlines and content hashes are persisted in the
deletion record. Active-bound person processing flags may change; expired-bound personal fields are
anonymized under ADR-0012 §5. Their structural keys remain for immutable references.
Retained voucher and
source contents do not. The CLI retries post-commit file cleanup for completed
organizations on every invocation, so an interrupted cleanup is recoverable.
Each organization has its own session/transaction. A failure logs its organization
ID, rolls back its database work and continues with the next organization. The
CLI counts a committed erasure once, including when file cleanup fails. It reports
these IDs as **erased, file cleanup pending/retry**, separately from failed database
erasures, and exits **1** if
any organization or post-commit cleanup failed (otherwise **0**). A failed
database erasure stays due in `cooling_off`; rerun `organization-deletion-run` to
retry it, or cancel through the existing owner-admin action before erasure commits.
Database failure never deletes files. Storage errors before commit refuse the
run; a post-commit filesystem error needs a CLI retry while login stays blocked.
Expiry does not automatically purge immutable evidence: that remains a separate
operator/legal decision under [ADR-0012](../adr/0012-gdpr-erasure-vs-gobd-retention.md).
Its provisional retention durations still require the documented domain sign-off.

### Catalog survivor contract

The catalog test covers **every** text/varchar/JSON column of retained roots,
conditional vouchers, identity rows and their recursive FK parents. There is no
column-name filter. Each column must be scrubbed (including the personal-payload
contract below) or appear in this explicit survivor inventory. New tables and
columns therefore require a classification. These are field-level outcomes;
this inventory does not authorize additional retention or rewriting evidence.

The table includes technical enums, keys, digests and storage identities as well
as personal content. Voucher/original and guarded review content remains under
the existing retention decision. Mutable structural source fields that survive
are described explicitly. `person` remains verbatim only while a voucher binding
is unexpired; `user` values are always replaced as described below. The legal
register's `challenge_annotation` remains editable legal evidence under ADR-0011,
not an immutable field; a separate minimization policy remains an owner decision.

| Table | Surviving text/JSON columns | Reason or transformation |
| --- | --- | --- |
| `access_coverage_review` | `manifest_digest` | Technical operation/status/error codes, keys and digests; personal event payloads are scrubbed separately. |
| `access_coverage_snapshot` | `manifest`, `manifest_digest`, `request_digest` | Retained source, scope or review evidence and its technical provenance. |
| `access_disposition_acceptance` | `assessment`, `assessment_digest`, `manifest_digest`, `reason`, `request_digest` | Retained source, scope or review evidence and its technical provenance. |
| `access_grant_acceptance` | `proposal_digest`, `results` | Retained source, scope or review evidence and its technical provenance. |
| `access_grant_proposal` | `changes`, `manifest_digest`, `proposal_digest` | Retained source, scope or review evidence and its technical provenance. |
| `accounting_scope` | `label`, `origin`, `scope_type` | Retained source, scope or review evidence and its technical provenance. |
| `accounting_scope_binding` | `origin`, `reason` | Retained source, scope or review evidence and its technical provenance. |
| `allocation_correction` | `impact_snapshot`, `preview_digest`, `reason`, `request_digest`, `source_variant`, `transition_kind` | Retained source, scope or review evidence and its technical provenance. |
| `allocation_correction_completion` | `basis`, `basis_digest`, `reason`, `request_digest` | Retained source, scope or review evidence and its technical provenance. |
| `allocation_correction_context` | `context`, `parent_digest` | Retained source, scope or review evidence and its technical provenance. |
| `allocation_correction_impact` | `snapshot` | Retained source, scope or review evidence and its technical provenance. |
| `allocation_integrity_attestation` | `content_sha256`, `request_digest`, `verification_method` | Retained source, scope or review evidence and its technical provenance. |
| `allocation_proposal` | `allocation_kind`, `financial_state`, `reason`, `request_digest` | Retained source, scope or review evidence and its technical provenance. |
| `allocation_proposal_line` | `category_code`, `category_label`, `description` | Retained source, scope or review evidence and its technical provenance. |
| `allocation_set` | `content_sha256`, `duplicate_snapshot`, `financial_state`, `kind`, `reason`, `request_digest`, `source_variant`, `transition_kind` | Retained source, scope or review evidence and its technical provenance. |
| `allocation_source_claim` | `source_sha256` | Retained source, scope or review evidence and its technical provenance. |
| `allocation_treatment_line` | `documentary_basis`, `expense_intent`, `markers`, `owner_distribution_key`, `tax_intent`, `tenant_distribution_key`, `tenant_treatment` | Retained source, scope or review evidence and its technical provenance. |
| `allocation_treatment_review` | `financial_state`, `marker_policy`, `reason`, `request_digest`, `snapshot`, `snapshot_digest` | Retained source, scope or review evidence and its technical provenance. |
| `approved_fact_source` | `approved_value_json`, `field_key`, `printed_page`, `quote`, `section`, `status`, `subject_kind`, `value_fingerprint` | Retained source, scope or review evidence and its technical provenance. |
| `assignment_proposal` | `method`, `reason`, `request_digest`, `state` | Retained source, scope or review evidence and its technical provenance. |
| `attachment` | `archive_reason`, `content_sha256`, `mime_type`, `original_filename`, `storage_backend`, `storage_uri`, `title` | Retained original content, metadata and storage identity. |
| `audit_event` | `action`, `entity_type`, `prev_event_hash` | Technical operation/status/error codes, keys and digests; personal event payloads are scrubbed separately. |
| `bank_account` | `account_type`, `bank_name`, `bic`, `iban`, `label` | Required account identity of retained financial records. |
| `bank_transaction` | `counterparty_iban`, `counterparty_name`, `import_hash`, `purpose`, `status` | Retained bank evidence and reconciliation context. |
| `context_decision` | `comparison_note`, `method`, `quotation`, `request_digest`, `snapshot`, `snapshot_digest`, `xml_path` | Retained source, scope or review evidence and its technical provenance. |
| `cost_entry` | `document_number`, `owner_distribution_key`, `source`, `status`, `tenant_apportionable`, `tenant_distribution_key` | Frozen voucher content, accounting classifications and computation provenance. |
| `coverage_declaration` | `component`, `reason`, `source_document_ids`, `source_revision`, `state`, `values` | Retained source, scope or review evidence and its technical provenance. |
| `declaration_extraction_run` | `error_code`, `harness_version`, `model_name`, `prompt_version`, `provider`, `schema_version`, `status` | Technical operation/status/error codes, keys and digests; personal event payloads are scrubbed separately. |
| `declaration_import` | `approval_idempotency_key`, `approval_request_digest`, `approval_result_json`, `current_stage`, `error_code`, `error_message`, `lease_owner`, `mode`, `status` | Retained source, scope or review evidence and its technical provenance. |
| `declaration_import_document` | `document_role` | Retained source, scope or review evidence and its technical provenance. |
| `declaration_import_pass` | `safe_metadata` | Retained source, scope or review evidence and its technical provenance. |
| `declaration_queue_request` | `operation` | Technical operation/status/error codes, keys and digests; personal event payloads are scrubbed separately. |
| `declaration_review_context` | `prior_resolutions` | Retained source, scope or review evidence and its technical provenance. |
| `declaration_review_receipt` | `operation`, `request_digest` | Retained source, scope or review evidence and its technical provenance. |
| `document_cost_link` | `link_role` | Retained source, scope or review evidence and its technical provenance. |
| `document_ref` | `content_sha256`, `mime_type`, `source_kind` | Retained source, scope or review evidence and its technical provenance. |
| `dunning_batch` | `request_digest` | Technical operation/status/error codes, keys and digests; personal event payloads are scrubbed separately. |
| `dunning_notice` | `frozen_display_names`, `included_items`, `interest_snapshot`, `note`, `status` | Frozen voucher content, accounting classifications and computation provenance. |
| `dunning_quarantine_disposition` | `reason`, `snapshot_digest` | Retained source, scope or review evidence and its technical provenance. |
| `dunning_scope_context` | `origin`, `reason` | Retained source, scope or review evidence and its technical provenance. |
| `duplicate_comparison` | `evidence`, `financial_state`, `reason`, `request_digest`, `verdict` | Retained source, scope or review evidence and its technical provenance. |
| `duplicate_exception_dependency` | `cause`, `evidence` | Retained source, scope or review evidence and its technical provenance. |
| `economic_plan` | `engine_version`, `snapshot_payload`, `snapshot_sha256`, `status` | Frozen voucher content, accounting classifications and computation provenance. |
| `economic_plan_line` | `cost_category_code`, `cost_group`, `distribution_key`, `label`, `line_type`, `note` | Frozen voucher content, accounting classifications and computation provenance. |
| `exception_case` | `cause` | Retained source, scope or review evidence and its technical provenance. |
| `exception_case_event` | `action`, `request_digest`, `resolution_state`, `workflow_state` | Retained source, scope or review evidence and its technical provenance. |
| `exception_completion` | `basis`, `basis_digest`, `comparisons`, `dispositions`, `policy`, `reason`, `request_digest` | Retained source, scope or review evidence and its technical provenance. |
| `exception_completion_member` | `disposition` | Retained source, scope or review evidence and its technical provenance. |
| `exception_dependency` | `cause` | Retained source, scope or review evidence and its technical provenance. |
| `extraction_revision` | `content_sha256`, `error_code`, `payload`, `processor_version`, `schema_version`, `status`, `validation_status` | Retained source, scope or review evidence and its technical provenance. |
| `field_evidence` | `bbox`, `field_name`, `locator_status`, `method`, `quotation`, `validation_status`, `value`, `value_type`, `xml_path` | Retained source, scope or review evidence and its technical provenance. |
| `financial_computation_attestation` | `operation`, `state`, `subject_kind` | Retained source, scope or review evidence and its technical provenance. |
| `financial_computation_receipt` | `operation` | Technical operation/status/error codes, keys and digests; personal event payloads are scrubbed separately. |
| `intake_batch` | `request_digest` | Technical operation/status/error codes, keys and digests; personal event payloads are scrubbed separately. |
| `intake_extraction_job` | `error_code`, `status` | Technical operation/status/error codes, keys and digests; personal event payloads are scrubbed separately. |
| `intake_extraction_retry` | `reason` | Retained source, scope or review evidence and its technical provenance. |
| `intake_item` | `client_item_key`, `content_sha256`, `filename`, `mime_type` | Retained source, scope or review evidence and its technical provenance. |
| `intake_original` | `content_sha256`, `mime_type`, `storage_backend`, `storage_uri`, `storage_version` | Retained original content, metadata and storage identity. |
| `intake_upload_admission` | `payload_digest` | Technical operation/status/error codes, keys and digests; personal event payloads are scrubbed separately. |
| `invoice_document` | `content_sha256`, `extraction_payload`, `mime_type`, `original_filename`, `processing_error_code`, `processing_status`, `status`, `storage_backend`, `storage_uri`, `storage_version` | Retained original content, metadata and storage identity. |
| `invoice_processing_attempt` | `error_code`, `processor_version`, `status` | Technical operation/status/error codes, keys and digests; personal event payloads are scrubbed separately. |
| `invoice_review_revision` | `content_sha256`, `currency`, `document_kind`, `document_number`, `reason`, `request_digest`, `review_scope`, `seller_name` | Retained source, scope or review evidence and its technical provenance. |
| `invoice_upload_admission` | `payload_digest` | Technical operation/status/error codes, keys and digests; personal event payloads are scrubbed separately. |
| `letter_document` | `body`, `content_sha256`, `recipient_name`, `storage_uri`, `subject` | Retained original content, metadata and storage identity. |
| `logical_document` | `content_sha256`, `origin`, `reason`, `request_digest`, `review_state` | Retained source, scope or review evidence and its technical provenance. |
| `management_mandate` | `label`, `mandate_type` | Required mandate identity and type. |
| `mandate_document` | `archive_reason`, `content_sha256`, `doc_type`, `mime_type`, `original_filename`, `scope_reason`, `storage_backend`, `storage_uri`, `title` | Retained original content, metadata and storage identity. |
| `mandate_legacy_observation` | `snapshot_digest` | Retained source, scope or review evidence and its technical provenance. |
| `mandate_quarantine_disposition` | `reason`, `snapshot_digest` | Retained source, scope or review evidence and its technical provenance. |
| `mapping_version` | `identifier_kind`, `identifier_value`, `policy`, `reason`, `request_digest`, `source_basis`, `state` | Retained source, scope or review evidence and its technical provenance. |
| `master_import_batch` | `request_digest` | Technical operation/status/error codes, keys and digests; personal event payloads are scrubbed separately. |
| `membership` | `role` | Historical role classification; account access is disabled. |
| `operation_receipt` | `key`, `operation`, `request_digest` | Technical operation/status/error codes, keys and digests; personal event payloads are scrubbed separately. |
| `outbox_delivery` | `consumer`, `error_code`, `status` | Technical operation/status/error codes, keys and digests; personal event payloads are scrubbed separately. |
| `outbox_event` | `effect_key`, `event_type` | Technical operation/status/error codes, keys and digests; personal event payloads are scrubbed separately. |
| `owners_meeting` | `location`, `meeting_type`, `status` | Meeting location and lifecycle classifications; invitation/protocol text is scrubbed. |
| `payment` | `direction`, `note`, `payment_type`, `status` | Frozen voucher content, accounting classifications and computation provenance. |
| `person` | `bank_name`, `bic`, `city`, `company_name`, `email`, `first_name`, `iban`, `last_name`, `phone`, `street`, `zip_code` | Identity fields survive only with an unexpired voucher binding; otherwise anonymized. |
| `person_data_domain` | `label`, `reason_code` | Retained source, scope or review evidence and its technical provenance. |
| `person_data_grant` | `reason_code` | Retained source, scope or review evidence and its technical provenance. |
| `property` | `city`, `management_type`, `mea_scale_status`, `name`, `street`, `zip_code` | Required property identity/address and structural classifications. |
| `property_assignment` | `assignment_type`, `status` | Structural type/status; editable identifiers and text are scrubbed. |
| `property_creation_receipt` | `request_digest` | Technical operation/status/error codes, keys and digests; personal event payloads are scrubbed separately. |
| `property_data_domain` | `label`, `reason` | Retained source, scope or review evidence and its technical provenance. |
| `property_data_grant` | `reason` | Retained source, scope or review evidence and its technical provenance. |
| `property_document` | `content_sha256`, `doc_type`, `mime_type`, `original_filename`, `storage_backend`, `storage_uri`, `title` | Retained original content, metadata and storage identity. |
| `property_document_relation` | `relation_type`, `status` | Retained source, scope or review evidence and its technical provenance. |
| `property_knowledge_query_run` | `error_code`, `model`, `outcome`, `provider`, `status` | Provider/model/status and structured error metadata; no question/answer text is stored here. |
| `property_legal_reference` | `status` | Structural status; editable reference text is scrubbed. |
| `property_rule` | `effect_json`, `scope_json`, `stable_key`, `status`, `summary`, `title`, `topic` | Retained source, scope or review evidence and its technical provenance. |
| `property_rule_relation` | `relation_type` | Retained source, scope or review evidence and its technical provenance. |
| `property_source_admission_review` | `content_sha256`, `reason`, `request_digest` | Retained source, scope or review evidence and its technical provenance. |
| `property_source_classification` | `content_sha256`, `decision`, `reason`, `request_digest`, `source_context_digest` | Retained source, scope or review evidence and its technical provenance. |
| `property_source_security` | `mode` | Security-mode enum; mutable reason is scrubbed. |
| `resolution` | `majority_type`, `result`, `wording` | Registered resolution wording and voting classifications. |
| `resolution_register_entry` | `challenge_annotation`, `location`, `wording` | Legal register wording/location and mutable challenge annotation (ADR-0011). |
| `runtime_budget_attestation` | `policy`, `policy_digest` | Retained source, scope or review evidence and its technical provenance. |
| `runtime_budget_review` | `policy`, `policy_digest`, `reason`, `request_digest` | Retained source, scope or review evidence and its technical provenance. |
| `scope_control_event` | `mode` | Technical operation/status/error codes, keys and digests; personal event payloads are scrubbed separately. |
| `scope_grant` | `reason` | Retained source, scope or review evidence and its technical provenance. |
| `scope_migration_exception` | `code`, `evidence` | Retained source, scope or review evidence and its technical provenance. |
| `scope_payment_reconciliation` | `reason`, `request_digest` | Retained source, scope or review evidence and its technical provenance. |
| `scope_payment_reconciliation_item` | `source_digest` | Retained source, scope or review evidence and its technical provenance. |
| `settlement_line` | `cost_category_code`, `distribution_key`, `label`, `line_type`, `note` | Frozen voucher content, accounting classifications and computation provenance. |
| `settlement_run` | `advance_payments_source`, `engine_version`, `run_type`, `snapshot_payload`, `snapshot_sha256`, `status` | Frozen voucher content, accounting classifications and computation provenance. |
| `source_partition_revision` | `content_sha256`, `reason`, `request_digest` | Retained source, scope or review evidence and its technical provenance. |
| `source_security_authorization` | `previous_mode`, `target_mode` | Retained source, scope or review evidence and its technical provenance. |
| `source_security_transition` | `assessment_digest`, `manifest_digest`, `previous_mode`, `reason`, `request_digest`, `target_mode` | Retained source, scope or review evidence and its technical provenance. |
| `unit` | `building_part`, `location`, `number`, `usage_type` | Required unit identity/location and usage classification. |
| `user` | `display_name`, `email`, `password_hash` | Replaced by pseudonymous email/display name and unusable password sentinel; no original contact or credential survives. |
| `visual_disclosure_attestation` | `content_sha256`, `request_digest` | Retained source, scope or review evidence and its technical provenance. |
| `visual_disclosure_event` | `kind`, `request_digest` | Technical operation/status/error codes, keys and digests; personal event payloads are scrubbed separately. |
| `visual_extract_artifact` | `content_sha256`, `reason`, `renderer_profile`, `request_digest`, `source_sha256`, `storage_backend`, `storage_uri`, `storage_version` | Retained source, scope or review evidence and its technical provenance. |
| `visual_extract_attestation` | `payload`, `request_digest` | Retained source, scope or review evidence and its technical provenance. |
| `workflow_job` | `error_code`, `kind`, `status` | Technical operation/status/error codes, keys and digests; personal event payloads are scrubbed separately. |

### Decided: personal event and receipt payloads are scrubbed

The owner decision of 2026-10-31 is implemented by the
[ADR-0003 amendment](../adr/0003-gobd-immutability-mechanism.md#amendment--2026-10-31-personal-audit-payloads-during-organization-erasure)
and [ADR-0012 §4](../adr/0012-gdpr-erasure-vs-gobd-retention.md#4-anonymization-anonymize).
After person anonymization, the operator run replaces personal payload references
with `Gelöscht`, including `mail_prepared.email` and `mail_prepared.person_id`
inside `audit_event.payload_diff`. It covers names, addresses, phone and bank
details for persons without a binding or with all bindings expired, plus former
users' email addresses. Nested objects/lists and personal values embedded in free
text are included. Voucher-bound person entries remain unchanged.

The catalog inventory of retained event/receipt/batch/attempt JSON and text
columns identifies these personal-content columns. These are scrubbed separately
from the survivor columns above:

| Table | Payload columns eligible for personal-data scrubbing |
| --- | --- |
| `audit_event` | `payload_diff` (including `mail_prepared` and `mail_completed`) |
| `declaration_review_receipt` | `response` |
| `dunning_batch`, `master_import_batch` | `result` |
| `exception_case_event`, `intake_retry_receipt`, `scope_control_event`, `visual_disclosure_event` | `reason` |
| `financial_computation_receipt` | `state` |
| `intake_batch` | `manifest` |
| `intake_extraction_attempt`, `job_attempt`, `outbox_delivery_attempt` | `result` |

The same inventory excludes technical enums, operations, request keys/digests,
hashes, versions and status fields. `invoice_processing_attempt`,
`operation_receipt`, `outbox_event` and `property_creation_receipt` contain only
such technical/structural fields. `mail_prepared` is an audit action, not a
separate table. Its request/message identifiers, delivery state, preparation and
completion times, PDF hash, storage URI and byte size stay. Original PDFs remain
retained originals; this decision changes payload minimization, not voucher
retention or original bytes.

Only the actual `app_migrator` login with the transaction-local erasure flag can
use the payload-only guard exception during the locked erasure. Rows, event
types, IDs, actor structure and timestamps remain; DELETE is forbidden. Runtime
roles retain no UPDATE/DELETE grants even with the flag. Changed JSON objects
record `_organization_erasure.scrubbed_at`; text fields use the neutral marker
and the run's completion time. The integrity snapshot verifies the expected
scrubbed row and records its post-scrub hash, preserving all non-payload fields.
Historical `prev_event_hash` values stay; the marker records a deliberate payload
discontinuity and does not certify an old hash against changed bytes. Retained
receipts remain operator-only.
