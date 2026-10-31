# Tasks and documents

Operational tasks, stored originals, correspondence, accounting exports, and master-data imports.

Payment and cost CSV lists are covered in [bank and payments](bank-and-payments.md#payment-and-cost-lists-as-csv).

## Tasks API

`/api/tasks` offers organization-bound creating, reading, partial updating, and
deleting of operational tasks. Title is required (max. 255 characters), note at most
10,000 characters; due date is optional, completing and reopening are
permitted. Without `assigned_user_id`, the task is assigned to the current user;
new assignments require an active member of the same organization.
Assignees who later become inactive do not make existing tasks disappear.

The paginated list returns `{items,next_offset}` (default 50, maximum 100) and
combines property, unit, lease, user, completed, and `due_until` filters.
The date limit is inclusive and excludes undated tasks. Unit
and lease derive their parents server-side. To detach a task completely,
all three parent IDs are explicitly set to `null`. Changes require
prepare permissions on old and new sources; global tasks need no
financial scope. OwnerAdmin/Clerk write, Readonly reads, unknown live roles
are rejected. Success responses follow only after the commit. These tasks
are deletable operational data, not immutable financial originals.

Under **Aufgaben** (tasks) (`/aufgaben`), tasks can be filtered, edited,
completed, reopened, and deleted. **Aufgabe anlegen** (create task) in property and unit
takes over the loaded context but saves only after deliberate confirmation.
The assignee is selected from actual organization members;
Readonly members see no write actions.

The dashboard shows open, dated tasks up to and including seven local
calendar days into the future, regardless of their assignee. Undated and
later-due tasks remain available in the task list. **Erledigen** (complete)
removes the task from the widget only after a successful API response.
Tasks and master data remain visible independent of financial deadlines.
`GET /api/fristen/context` checks only whether a deliberate accounting-scope selection
is necessary; bound financial data is neither fetched as organization-wide deadlines
beforehand nor displayed as an empty overview. Access errors remain visible.
`scripts/acceptance/t42_tasks_browser.cjs` verifies real property/unit tasks,
the task due tomorrow including completing and reload, and the explicit
scope selection after a real account creation.

## General document storage

`/api/attachments` manages original files for a property, optionally additionally
for a unit, lease, or person. Upload and archiving require prepare permissions,
metadata requires read permissions, and downloads require export permissions on all linked sources.
Lists are paginated and hide archived entries by default.
Archiving with a justification is final and deletes neither the original nor the metadata;
replacing, re-linking, and deleting are not provided.

Files up to 25 MiB are stored in the existing organization-isolated, content-addressed
DocumentStore. Downloads verify key, SHA-256, and length and deliver
exactly the verified bytes with safe filenames and `nosniff`. Inline display is possible only for
PDF, PNG, and JPEG files detected by their signature; other content
is offered as a download. A failed database operation rolls back
metadata and audit but does not delete possibly shared
original bytes. Rollback restrictions are documented under
[migration compatibility](../../DEPLOY.md#migration-compatibility-00290146).

The „Dokumente“ (documents) section is available in property, unit, and person.
For persons, an accessible property is deliberately selected for an upload;
the person list remains cross-property. „Öffnen“ (open) loads the authorized original,
„Archivieren“ (archive) requires a reason. „Archivierte anzeigen“ (show archived) keeps retained files
visible and readable. Read-only users receive no write actions.
The real UI/PDF test case is `scripts/acceptance/t37_attachments_browser.cjs`.

## Email dispatch

T47 introduced this workflow.

Issued dunning notices, finalized BK statements, and person-specifically addressed
meeting invitations offer an explicit email dispatch. The address
comes exclusively from the authorized contact; no freely entered recipients.
In addition to live write, source, and contact export permissions, for bound
financial documents the own scope capability `send` is required (PDF export is
not sufficient). Reading roles cannot trigger a dispatch.

SMTP is disabled until configured; see [SMTP configuration and local UI/PDF acceptance](../../DEPLOY.md#optional-document-smtp-t47-no-deployment-authorization).

Before SMTP, a dispatch attempt is durably stored in the existing append-only audit on the
document, including recipient, message ID, and a reference to the
exact PDF in the document storage. A retry key prevents duplicate
transmission of the same request. The history distinguishes SMTP acceptance, failure,
and unclear outcome; even an attempt left open after a crash is **not**
proof that no dispatch took place. No automatic retry on unclear
outcome. SMTP acceptance is **not proof of delivery or receipt**; deadlines and receipt
must be checked separately. „Versand-PDF öffnen“ (open dispatch PDF) reads the stored bytes with
hash verification and current source/original contact export permissions, without
recalculating finalized documents.

`uv run pytest tests/test_mail_service.py` verifies fake SMTP and domain/permission paths.

## Letter templates and circulars

T48 introduced this workflow.

Under **Briefe** (letters), plain-text templates with `{{mieter.name}}`,
`{{mieter.adresse}}`, `{{objekt.name}}`, `{{objekt.adresse}}`, and `{{datum}}` can be
saved and edited. Explicitly choose property, template, and reference date and
confirm „Jetzt erstellen“ (create now): all tenants current on that day each receive their own
PDF per person, even with multiple leases only once. Unknown
placeholders are rejected; HTML and scripts remain printed plain text.

Complete property/history and contact export permissions are required;
a hidden recipient blocks the entire issuance. Readers cannot change a
template or create letters. Repeating the same request produces
no duplicates. The history opens stored, hash-verified original bytes;
later template or name changes do not alter them. No automatic
dispatch and no proof of delivery. Tests: `uv run pytest tests/test_letters.py`.
Deployment requirements, rollback restrictions, and UI/PDF acceptance are documented
in the [saved-letter runbook](../../DEPLOY.md#saved-letter-originals-t48-local-head-0132-only).

## DATEV booking batch (Buchungsstapel)

T49 introduced this workflow.

Under **Berichte → DATEV-Export öffnen** (reports → open DATEV export), select property, period, and, where applicable, the
whole-property financial scope. Explicitly specify SKR03 or SKR04, consultant/client number (Berater-/Mandantennummer),
start of fiscal year, general ledger account length, and all account assignments;
there are no prefilled real accounts. Costs are booked per category
against a dedicated clearing account, bank movements against the chosen
payment-type counter-account. Thus the payment of an invoice must not be assigned again
directly as expense. The domain-reviewed accounts must work
**without automatic tax handling**; no VAT splitting or BU keys.

The preview shows individual items and exact totals. The real EXTF download
(header 700, booking batch 13, UTF-8 BOM, semicolon/CRLF) re-checks sources and
export permissions. Changing accounts or sources requires a new preview.
Booked active payments count by value date, confirmed current costs
by invoice date; reversal pairs, superseded costs, and raw bank transactions are not
additionally exported. Zero costs produce no prohibited zero booking.

**Complete current-state output, no differential export:** do not import files
repeatedly or overlapping; after corrections, reconciliation in the
target system is required. A downloaded file is not a successful
DATEV import. No upload or change to original bookings.
The account configuration is entered explicitly per output, not stored
as a global category assignment. Tests:
`uv run pytest tests/test_datev_export.py`.
Deployment details and synthetic Q1 acceptance are documented in the
[DATEV runbook](../../DEPLOY.md#datev-file-export-t49).

## SEPA monthly rent

T50 introduced this workflow.

Under **Zahlungen → SEPA-Lastschrift öffnen** (payments → open SEPA direct debit), explicitly select a running property account and the
authorized MIET financial scope. Capture creditor ID (Gläubiger-ID), name, city/country,
and the existing bank agreement; then register per tenant an actually
signed, externally retained CORE mandate. Explicitly confirm contact IBAN,
signature date, unique reference, FRST/RCUR, and, where applicable, the last use
**submitted to the bank**. A download does not count
as use; rejected/returned collections also belong to the history.
Name/IBAN changes and processing locks block use.

Specify month, collection date, unique message ID, and the
pre-notification actually given (14 days or an expressly agreed shorter period).
The preview uses the existing monthly expected amount (Soll) including rent history,
NK advance payment, and day-accurate partial months. Without a valid mandate, no tenant
is silently omitted. The download re-checks sources/export permissions and
validates **pain.008.001.08** against the unchanged EPC CORE XSD. Non-EEA banks
require explicitly entered BICs; check bank profile and acceptability externally.

**File only, no transmission:** no payment/booking, no automatic switch
FRST→RCUR, and no proof of bank acceptance. Repeated downloads/uploads can
trigger duplicate collections; reconcile IDs and collection processing outside the app.
No predefined real bank data or mandates. Tests: `uv run pytest tests/test_sepa_export.py`.
Deployment requirements, rollback restrictions, and local UI acceptance are documented
in the [SEPA runbook](../../DEPLOY.md#offline-sepa-rent-file-t50-local-head-0133).

## CSV master data import

T51 introduced this workflow.

Under **Objekte → CSV-Stammdaten importieren** (properties → import CSV master data), actually download the CSV template
and fill it as a UTF-8 file (BOM permitted) with semicolons and unchanged columns.
`property`, `unit`, and `person` create new properties, units, and contacts;
`property_key` references a local property key or an authorized
existing property UUID. Areas remain exact decimal values, including `60,25`.
Unknown columns, invalid values, and duplicates are reported with row/field.
No automatically created profiles or leases.

The preview writes nothing. Only after review, explicit approval of the
data scope (if required), and confirmation is the entire import created atomically.
There are no silent self-approvals. Changes require a new
preview; repeating the same request returns the same IDs, after permission revocation
no partial response. Continue unclear network errors with **Import erneut versuchen** (retry import),
do not duplicate through new requests. Tests: `uv run pytest tests/test_master_import.py`.
Import limits, retained batch metadata, rollback restrictions, and UI/download acceptance
are documented in the [CSV import runbook](../../DEPLOY.md#csv-master-data-import-t51-local-head-0134).
