# Organization export

Organization exports are portable product data, separate from the paired
PostgreSQL dump and document backup described in [DEPLOY.md](../../DEPLOY.md).
They contain personal data and originals. Store and transfer them privately.

## Current delivery boundary

The owner-administrator export/download API and the “Daten exportieren” action
on the admin Users page are implemented. Organization
import, deletion, their controls and the fresh-install acceptance are still pending
issue #23. This archive is not currently an importable backup or a claim that
every organization can be reproduced from administrator-visible rows alone.

Two transfer constraints need an explicit resolution before implementing import:

- Entity UUID primary keys are global, so source and destination organizations
  cannot coexist in the same database with identical entity IDs. A same-database
  rehearsal would need consistent entity-ID remapping, beyond remapping the
  organization ID. A separate fresh installation can preserve entity IDs.
- Administrator RLS and column privileges deliberately hide private intake,
  allocation, computation and visual provenance. Original registration also
  generates immutable document-reference IDs. Reproducing the full graph needs
  a reviewed transfer contract that preserves those protections; ordinary raw
  insertion cannot restore it unchanged. Export does not bypass those controls.

The maintainer must perform the real export-to-fresh-self-host-install acceptance
once import is available. A rehearsal inside the shared test database cannot
substitute for that installation check.

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

## Version-one format

The archive contains only regular files. It preserves no host paths, filesystem
owners or source permissions. All metadata is UTF-8.

| Member | Contract |
| --- | --- |
| `manifest.json` | `format: verwalino-organization-export`, integer `version: 1`, creation timestamp, application version, release schema head, source organization ID/name, `entity_order`, `row_counts`, member `sha256` map, document-to-entity mapping and visibility exclusions |
| `entities/<entity>.jsonl` | One JSON object per row, newline terminated; an empty visible entity has an empty file |
| `documents/<document UUID>` | Verified original bytes, without filename or host-path dependencies; the manifest maps the UUID to its entity |
| `csv/payment.csv`, `csv/cost_entry.csv` | Companions from the existing CSV renderers, retaining their German headers, exact amounts, UTF-8 BOM and spreadsheet-injection protection |
| `README.md` | Embedded format and visibility explanation |

SHA-256 covers every member except `manifest.json`, which cannot hash itself.
It is an integrity check, not proof that an archive was issued by a trusted party.
Document content and recorded size/hash must agree before an archive is published.
A missing or corrupt original refuses the whole export; no partial artifact is
published. UUIDs remain strings. Top-level numeric database columns are exact
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
`plan_version`; a future importer must resolve these against its seeded catalog.

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
