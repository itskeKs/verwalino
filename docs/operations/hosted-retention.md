# Hosted retention and end of service

**Draft, 13 November 2026. Not approved for launch.** This is the operator procedure and capability
record. Customer wording is in the website's German Nutzungsbedingungen and AV-Vertrag; English
is a translation. Owner decisions Q04/Q05/Q07/Q08 are applied below. Implementation dependencies must be
closed before customer data is accepted; the decisions are not evidence of deployed behavior. Legal sources: [S01, S06 and S08](hosted-legal.md#primary-sources-and-dates).

## Return, access and deletion

1. Verify the requesting administrator's authority and organization privately. Apply the agreed complete export followed by
   deletion under Article 28(3)(g), respecting a lawful instruction to delete without return
   and any specific statutory prohibition on deletion. No archive service is offered.
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
| Active account | Remains until deactivation/organization erasure; no age-based account closure found | Q08: customer may terminate at any time; operator ordinary termination or discontinuation requires 90 days’ notice. Keep export available until service ends. Contract and account closure workflow requires implementation |
| Transient organization export | 24-hour validity; permission changes may invalidate earlier; replacement removes previous artifact; opportunistic cleanup plus operator cleanup command | Schedule cleanup and monitor success. Expiry does not prove physical removal at exactly 24 hours |
| Confirmed organization erasure | Seven-day cooling-off; operator execution thereafter | Implementation dependency: scheduled execution, failure handling and a verifiable completion deadline are required. The owner did not approve a new 24-hour completion SLA |
| Retained evidence and dependencies | Catalog derives protection from guards/permissions and dependency closure. Stored deadlines or provisional year-end plus eight-year fallback. No automatic expiry purge | Q07: complete return then deletion, no hosted archive. Remove product obstacles to lawful final erasure; retain a residual category only under a specific applicable legal duty, with deadline and restricted access. Customer duties or code guards are not an operator storage mandate |
| Retained personal data | Voucher-bound identities stay restricted while deadlines apply; other master data is neutralized; identifiers/frozen originals may still identify people | Do not describe the entire remaining organization as anonymous; review remaining identifiable evidence and continuing lawful basis |
| Server backups | 14 daily/8 weekly unlabelled generations and newest 10 labelled; pruning tied to backup runs, no fixed maximum labelled age | Q04: use Contabo’s managed backup service under the hosting AV agreement. [TO SUPPLY Q04: contracted backup product and retention, including maximum age of all copies.] Verify separation, encryption, access, locations and restoration from contract and tests; no invented 90-day maximum |
| Signup requests | 24-hour token, confirmed row removed; unconfirmed rows eligible for cleanup seven days after expiry, best effort every 100th signup | Q05: automatically delete never-confirmed sign-ups independently of traffic. Implementation dependency: schedule cleanup using the current eligibility boundary (24-hour token plus seven days), bound execution delay and monitor failures; eligibility is not a promised day-eight deletion |
| Reset requests / throttle state | Reset token one hour; rows eligible seven days after expiry or use, with bounded opportunistic cleanup; throttle entries eligible after one day without update | Verify configured jobs and measured deletion; token expiry is not row expiry. Enforce a maximum elapsed retention before publishing one |
| Server logs, tracker, support and contracts | No universal deployed time limit established | Q05: server logs 14 days from creation, error reports 30 days from creation, support 90 days after closure. Implementation dependency: enforce and verify every store/provider. Individually justify incident/claim or statutory exceptions; versioned contract evidence needs a separate purpose-based schedule (Q14) |

## Accounting retention and service closure

AO §147 and HGB §257 distinguish generally ten-year books/accounts, eight-year vouchers and
six-year correspondence, with category-specific start dates, exceptions and extensions. The
customer determines applicability with its tax/legal adviser. A statutory expiry is not a
license to destroy records still covered by a lawful hold, nor is “GoBD” a reason to keep all
personal data indefinitely. For electronic records, preserve usability, relevant metadata,
relationships, original structured invoice data and documentation of changes and procedures.
Sources: [S06](hosted-legal.md#primary-sources-and-dates).

Q07 selects **complete export, then deletion**, with no archive service. Customers arrange any
applicable six/eight/ten-year retention and continuing audit access in their own successor system.
Not every private landlord or WEG is subject to every AO/HGB category. The operator must verify
usable, complete handover before closing access; current permission-filtered return and protected
residual records do not satisfy that commitment. The customer can instruct lawful deletion without
return. Specific operator storage duties require a category, legal provision, deadline and restricted
purpose; they do not create a general archive offer. Final purge remains an implementation dependency.

Q08 selects ordinary termination by the customer at any time and by fogu on 90 days’ notice,
also for discontinuation. Notices go in text form to the contractual contact; customers may use
hello@fogu.app. Keep export available until the end and coordinate handover before access closes.
Mandatory termination for good cause and proportionate urgent security measures remain available;
these must not become a way to evade the notice/return promise. Do not promise successful re-import
into any successor system. A customer needing AO §147(6) access needs usable records, not only PDFs.
The owner must evidence a workable shutdown/handover process; no successor archive custodian is
promised. The seven-day technical erasure cooling-off is distinct from ordinary termination.

## Backup restoration and holds

The documented source backup procedure initially stores copies on the same server. This is not
evidence that the **decided managed service** has been procured or activated. Before launch the
owner must conclude the hosting AV agreement, complete Q04 above and verify database/documents
coverage, configuration protection, separation from server failure, encryption/key custody,
restore tests and time-bounded deletion. Managed backup does not itself prove any of these controls.
Never publish configuration, credentials or actual storage addresses in this repository.

For each erasure, keep a private minimal tombstone/hold ledger outside the restored dataset.
After recovery, reconcile later deletions and revoked access before reopening service, then
verify that retained records remain restricted. Access backups only for recovery or an approved
legal purpose. Record exceptional holds with category, authority, justification, review/end date
and accountable person; release them promptly when the basis ends. These are proposed operating
controls requiring appointment and rehearsal, not assertions that restoration already performs them
(Articles 5, 28, 32; [S01](hosted-legal.md#primary-sources-and-dates)).
