# Hosted retention and end of service

**Draft, 12 November 2026. Not approved for launch.** This is the operator procedure and capability
record. Customer wording is in the website's German Nutzungsbedingungen and AV-Vertrag; English
is a translation. `OPEN` items must be resolved in the signed arrangements before customer data
is accepted. Legal sources: [S01, S06 and S08](hosted-legal.md#primary-sources-and-dates).

## Return, access and deletion

1. Verify the requesting administrator's authority and organization privately. Agree return or
   deletion under Article 28(3)(g), any continuing archival instruction, and applicable holds.
   Do not equate the customer's accounting duty with the operator's own independent legal duty.
2. Before ending access, obtain a fresh organization export and verify its contents and exclusions
   with the customer. The download is `.tar.gz`, containing UTF-8 JSONL entity rows, a manifest
   with counts/mappings/SHA-256, original document bytes, CSV for payments/cost entries and a README.
   It is not encrypted by the export function; use an authenticated confidential transfer and
   secure customer storage. It is not the full database backup or a complete tax audit package.
3. The owner-admin sees/exports only authorized data. Hidden provenance, credentials, AI settings
   and global authentication state are excluded. Missing/corrupt originals refuse export; import
   can fail on private provenance, custody constraints or size limits. Arrange and test lawful
   supplementary retrieval before promising a complete migration. Never send a multi-tenant dump.
4. Confirmation of organization deletion requires the exact organization name and starts seven
   days of read-only access. Export, logout and cancellation remain available. Seven days is the
   earliest execution date, not a guaranteed completion date. The operator command executes due
   erasures; it is not a continuously scheduled product job. Cancellation remains possible until
   erasure commits, including after the deadline when execution is delayed.
5. Execution disables accounts/credentials, minimizes mutable fields and removes unretained data;
   file/export cleanup follows database commit and must be retried after failures. Protected
   originals, financial/audit/source evidence and dependencies remain. No customer login or
   read-only account remains after execution. Restricted retrieval is operator maintenance only.
6. Give the customer a private completion record: instruction, actual completion date, erased
   categories, retained categories and exact basis/deadline, outstanding cleanup/backups, next
   review date and retrieval contact. Do not certify complete deletion while copies remain.

Evidence: [organization export and erasure](organization-export.md),
`backend/app/services/organization_export_service.py:26`,
`backend/app/services/organization_deletion_service.py:291`,
`backend/app/organization_deletion.py`, and [DEPLOY](../../DEPLOY.md).

## Duration register

| Category | Current source behavior | Required decision / operational control |
| --- | --- | --- |
| Active account | Remains until deactivation/organization erasure; no age-based account closure found | `OPEN Q08`: contract end vs account access; proposed operator termination notice of 90 days with export available until the agreed end, customer ordinary termination at any time; validate staffing before committing |
| Transient organization export | 24-hour validity; permission changes may invalidate earlier; replacement removes previous artifact; opportunistic cleanup plus operator cleanup command | Schedule cleanup and monitor success. Expiry does not prove physical removal at exactly 24 hours |
| Confirmed organization erasure | Seven-day cooling-off; operator execution thereafter | `OPEN Q08`: proposed daily execution, completion normally within 24 hours after the deadline, escalation on failure. This is a proposed service target, not implemented scheduling |
| Retained evidence and dependencies | Catalog derives protection from guards/permissions and dependency closure. Stored deadlines or provisional year-end plus eight-year fallback. No automatic expiry purge | `OPEN Q07`: classify each category against actual customer duties, correct excessive/insufficient periods and define a deliberate safe expiry process. A guard or provisional deadline is not legal justification |
| Retained personal data | Voucher-bound identities stay restricted while deadlines apply; other master data is neutralized; identifiers/frozen originals may still identify people | Do not describe the entire remaining organization as anonymous; review remaining identifiable evidence and continuing lawful basis |
| Server backups | 14 daily/8 weekly unlabelled generations and newest 10 labelled; pruning tied to backup runs, no fixed maximum labelled age | `OPEN Q04`: off-site location, encryption, actual schedule, all-copy maximum age. Proposed maximum 90 days including labelled copies, with separately justified holds; requires an implemented policy and verified cleanup |
| Signup requests | 24-hour token, confirmed row removed; unconfirmed rows eligible for cleanup seven days after expiry, best effort every 100th signup | `OPEN Q05`: enforce a maximum elapsed retention independent of traffic; do not promise automatic deletion on day eight |
| Reset requests / throttle state | Reset token one hour; bounded opportunistic cleanup; throttle entries eligible after one day without update | Verify configured jobs and measured deletion; token expiry is not row expiry. Record precise reset-row criteria before publishing a maximum |
| Server logs, tracker, support and contracts | No universal deployed time limit established | `OPEN Q05/Q14`: proposed routine technical logs 14 days, tracker 30 days, closed support cases 90 days; individually documented incident/claim or statutory exceptions. Validate necessity, implementation and contract evidence periods |

## Accounting retention and service closure

AO §147 and HGB §257 distinguish generally ten-year books/accounts, eight-year vouchers and
six-year correspondence, with category-specific start dates, exceptions and extensions. The
customer determines applicability with its tax/legal adviser. A statutory expiry is not a
license to destroy records still covered by a lawful hold, nor is “GoBD” a reason to keep all
personal data indefinitely. For electronic records, preserve usability, relevant metadata,
relationships, original structured invoice data and documentation of changes and procedures.
Sources: [S06](hosted-legal.md#primary-sources-and-dates).

`OPEN Q07`: choose an agreed restricted archival service with verified retrieval and expiry, or
a verified transfer followed by deletion when legally permitted. The current product retains
protected records, but does not implement a complete archive service or final purge. Counsel
must settle the continuing processing instruction/contract, the treatment of any operator-specific
legal obligation, and the response to a customer's deletion instruction which cannot currently
be fulfilled. Do not launch by relabelling a product limitation as a legal duty.

`OPEN Q08`: agree a funded shutdown/migration process and a named successor or archive custodian
if the operator ceases business. The proposed 90-day notice does not replace immediate security
measures or mandatory rights. Offer export and verify readability/retrieval before access ends;
do not promise that re-import into a new installation always works. The customer needs a tested
route to tax-authority access where applicable, including AO §147(6), not only downloadable PDFs.

## Backup restoration and holds

Backups currently share the source server; they do not protect against losing that server. The
documented server format also includes unencrypted private configuration. `OPEN Q04`: establish
separate encrypted copies, key custody, restore testing and age-bounded rotation; never publish
configuration, credentials or actual storage addresses in this repository.

For each erasure, keep a private minimal tombstone/hold ledger outside the restored dataset.
After recovery, reconcile later deletions and revoked access before reopening service, then
verify that retained records remain restricted. Access backups only for recovery or an approved
legal purpose. Record exceptional holds with category, authority, justification, review/end date
and accountable person; release them promptly when the basis ends. These are proposed operating
controls requiring appointment and rehearsal, not assertions that restoration already performs them
(Articles 5, 28, 32; [S01](hosted-legal.md#primary-sources-and-dates)).
