# Hausverwaltung

Web application for managing properties, units, cases, and settlement-relevant
data.

## Self-hosting Verwalino (portable Docker first installation)

For a new server or laptop, follow [Install in one command](DEPLOY.md#install-in-one-command)
for the release installer, HTTPS/local variants, checksum/signature verification
and offline bundles. The first release with these assets and the fresh-VM run
remain maintainer acceptance steps. The checkout-based path below is also available.

Prerequisites: Linux or macOS with Bash, OpenSSL, Docker, and Compose v2
(2.24 or later). In the checked-out repository, **not on the existing alpha instance**:

```bash
bash scripts/self-host.sh init
bash scripts/self-host.sh start
```

Init generates five random deployment secrets in `.env.selfhost` (0600).
On a fresh database, `start` prints the browser setup URL and a one-time token.
Open `/setup`, enter that token, your organization, administrator name/email,
and a password twice. Setup signs you in and opens the dashboard. There is no
default password. The token expires after 24 hours; another `start` replaces it.
Only its SHA-256 digest is stored in the database; keep the terminal output
private. Once an organization exists, setup is closed and no token is printed.

The CLI remains an alternative and invalidates any pending token:

```bash
bash scripts/self-host.sh admin --org "Meine Hausverwaltung" --email admin@example.org
```

It prompts twice without echo and passes the password only through stdin.
Locally, the app is reachable only at
**http://127.0.0.1:18080**; PostgreSQL publishes no port.
Running `init` again leaves the file unchanged, and running the admin command again
does not reset an existing password. The existing alpha configuration and the
development/test database are not used.

For a **new** server with its own domain, instead run the following before the first start:

```bash
bash scripts/self-host.sh init --https verwaltung.example.org
bash scripts/self-host.sh start
```

DNS must point to the server; TCP 80/443 must be free and publicly reachable.
Caddy handles HTTPS; sign-in is then possible exclusively via
`https://verwaltung.example.org`. Certificate issuance may still be in progress
after the start. A completed HTTPS/browser acceptance run has not been documented yet.

`status` shows the containers, `stop` shuts them down **without deleting data**:

```bash
bash scripts/self-host.sh status
bash scripts/self-host.sh stop
```

The project name is fixed as `verwalino`; there is one installation per Docker context.
Database and documents live in their own named volumes. **Back up config, database,
and documents together; do not delete volumes.** A different checkout
or config path does not create a second independent installation.

By default, the image is built locally from the checkout; Python/Node/uv are not
required on the host. The release workflow targets **ghcr.io/itskeks/verwalino**
for **linux/amd64 and linux/arm64**, with only `vX.Y.Z` and `vX.Y` tags, provenance, SBOMs and
cosign keyless signatures. The first tag publication and native arm64 install
check still need maintainer acceptance; no published digest is claimed yet.
Once available, copy the index digest from the successful
[Release image run summary](https://github.com/itskeKs/verwalino/actions/workflows/release-image.yaml)
or inspect a release with `docker buildx imagetools inspect ghcr.io/itskeks/verwalino:v1.2.3`
(example version). Before installing, follow
[the signature verification and arm64 checks](DEPLOY.md#published-image-verification):
`cosign verify` must match the release workflow's certificate identity and
the issuer `https://token.actions.githubusercontent.com`. Select the verified
digest at first init:

```bash
bash scripts/self-host.sh init --image 'ghcr.io/itskeks/verwalino@sha256:<64-hex-digest>'
```

Remote image tags are not accepted by `scripts/self-host.sh`. See the
[maintainer release checklist](DEPLOY.md#maintainer-release-checklist) for publication.
AI import, AI Q&A, SMTP, and automatic invoice OCR are disabled in the portable stack;
manual voucher (Beleg) processing remains available.
The T10 invoice worker has been active on the existing alpha since 2026-10-24;
adding it to the portable stack remains outside this installer's scope.
Update/backup automation and a real fresh installation/login run
are still outstanding; this is not yet a general production release.
Details and limits: [DEPLOY.md](DEPLOY.md#portable-docker-first-installation).

## Local setup for development and declarations of division (Teilungserklärungen)

In the backend directory, the development, OCR, and operations dependencies are
installed together:

```bash
cd backend
uv sync --extra dev --extra ocr --extra ops
```

The import of declarations of division is a workflow that requires review. It
never creates canonical building, unit, or rule data without **Human Approval**;
document content remains untrusted data. Operations, data protection, local tests,
and the deliberately blocked live evaluation are described in the
[runbook for declarations of division](docs/operations/declaration-imports.md).

## Local invoice worker (T10)

After the backend setup above and `cd frontend && npm ci`, start
`scripts/dev-local.sh` from the repository root. It uses exclusively the test database
`:55432`, a separate `/tmp/hv-dev-documents.*` storage, and starts the API
`:18000`, the UI `:13000`, and the invoice worker together. Poppler
(`pdftoppm`) and Tesseract with the German language pack (`deu`) are required. Ctrl+C stops all three
processes; originals in the printed local document folder are preserved.
Do not start backend tests in parallel with the UI acceptance run: their fixture resets this
test database.

Under **Belege** (vouchers), upload a synthetic invoice and open **Details**:
„Gespeichert · wartet auf Verarbeitung“ (stored, awaiting processing) changes to „Felder vorbereitet · bitte
prüfen“ (fields prepared, please review); amount and date appear without manually entering the results.
The extraction books no costs and does not automatically approve any voucher.
Repeatable Chrome check: `node scripts/acceptance/t10_invoice_worker_browser.cjs`
(`HV_PLAYWRIGHT_PACKAGE` points to the existing Playwright package if needed;
`HV_CHROMIUM_EXECUTABLE` to Chrome). Optionally, `HV_T10_INVOICE_PDF` reads a
synthetic PDF with **119,00 EUR / 01.06.2025**, for example for an image/OCR test.

The worker CLI `python -m app.workers.invoice_extraction` by default processes
all existing organizations, detects new organizations without a restart, and supports
`--organization UUID` (repeatable), `--once`, and `--healthcheck`. It requires
`HV_DATABASE_URL_WORKER` explicitly, with the isolated role `app_worker`, as well as the
API's storage; no web/review credentials and no provider key.
Production configuration and release limits are described in [DEPLOY.md](DEPLOY.md).

## Changing your own password

After sign-in, „Passwort ändern“ (change password) in the top user bar opens the
dialog for current password, new password, and confirmation. A wrong old password
and an invalid new password are shown in the dialog. After a successful change,
the user is signed out; then sign in again with the new password. The local browser
flow is repeatable with
`scripts/acceptance/t14_password_browser.cjs` against `scripts/dev-local.sh`
(existing Playwright via `HV_PLAYWRIGHT_PACKAGE`, Chrome).

## User management (T57)

Under „Benutzer“ (users), admins edit role and active status or set a new
password (12–128 characters). Deactivation requires a confirmation; deactivating or
downgrading oneself is blocked. A reset sends no email and
never displays existing passwords. Roles take effect on the next request;
read-only hides business write actions, while managing one's own password remains available.

Reset, changing one's own password, and deactivation revoke existing cookies
via a persistent session generation. Reactivation does not revive old cookies.
The live check is performed again under the ACL lock; role/active changes
and non-sensitive audit data are stored atomically. The actual local
desktop/smartphone flow is `scripts/acceptance/t57_users_browser.cjs`.
The authenticated generation is also retained in later knowledge/plan transactions;
the isolated review runtime may only verify sessions, not look up
passwords. Unknown legacy roles are displayed as invalid and rejected at login,
not silently converted into a valid role.

## Bank transactions and file import (Phase 2)

Bank transactions are kept separate from booked payments: amounts are signed
`Decimal` values (credit positive, debit negative), import rows are unique per
account via their import hash. RLS and composite foreign keys bind transaction,
account, and optional payment link to the same organization and the same account.
After booking, this link is also immutable; linked transactions cannot be deleted.

Under „Bankkonten“ (bank accounts), „Umsätze“ (transactions) opens the
account-specific list with status filter and pagination. „Kontoauszug importieren“
(import bank statement) takes write roles through file, preview, and explicit
„Übernehmen“ (commit); after a file change, a new preview is required. CSV,
CAMT-XML, and ZIP can be selected; errors and the actual number of new
transactions are shown. READ_ONLY can read but not import.
The browser review/confirmation flow is [an executable feature](features/bank_statement_browser.feature),
run with `make test-browser`; parsing and duplicate imports are covered by
[the API feature](features/bank_statement_import.feature).
For write roles, the transactions page offers „Vorschlag übernehmen“ (accept
suggestion), „Aufteilen“ (split), and „Ignorieren“ (ignore). Suggestions that
require review open the split dialog; unclear amounts must be explicitly split
completely there.
`GET /api/bank-accounts/{id}/transactions` returns the list with optional
`status`, `offset`, `limit` (default 50, maximum 100), and `next_offset`.

The pure CSV parser supports semicolon-separated files in UTF-8 (including BOM) and CP1252
with German date/decimal formats. Required columns are
`Buchungstag`/`Buchungsdatum`, `Wertstellung`/`Valutadatum`, and
`Betrag`/`Betrag (EUR)`/`Betrag (€)`; optional are name (`Name`, `Gegenkonto Name`,
or `Auftraggeber / Begünstigter`), IBAN (`IBAN`/`Gegenkonto IBAN`),
`Verwendungszweck`, and `Währung` (EUR only). No free-form mapping UI.
Additionally verified are legacy DKB checking-account exports and the semicolon schema since 2023:
[published format definition](https://github.com/siddhantgoel/beancount-dkb/blob/6b9266e54bdbf566dfba63c44aa9cc7657144b8b/beancount_dkb/extractors/ec.py).
This is a traceable open-source format example, not an assurance for
every current DKB export variant or a determination of the bank actually used.
For DKB 2023, the counterparty is chosen by incoming/outgoing direction; pending or
contradictory transactions are rejected, old `Tagessaldo` rows are not booked.
Unknown/ambiguous formats and invalid rows abort the entire import
with a clear message; no rounding or silent field truncation.

Import identities are independent of file name, encoding, and column order.
Multiple identical bookings are retained. Without a bank-side
transaction ID, overlapping statements with a differing number of otherwise
identical bookings cannot be distinguished unambiguously; this limit must not
be presented as reliable cross-bank duplicate detection.

CAMT.053 is supported for `camt.053.001.02` and `.08` as XML or ZIP,
with the same output as CSV. Only booked EUR transactions; `CRDT` is positive,
`DBIT` negative. For batch bookings, the individual amounts must add up exactly to
the total booking, otherwise nothing is committed. Date and timestamps
are taken as the bank-side booking/value date, counterparties and
multiple unstructured remittance information lines are retained. XML DTDs and
entities, unknown versions, as well as ZIP path traversal, symlinks, encrypted
or nested archives are rejected. ZIPs are processed in memory only:
at most 100 entries, at most 25 MiB unpacked in total, and
100,000 transactions; statements of different accounts must not be merged.
An error in one file discards the entire parser run.

`POST /api/bank-accounts/{id}/imports` accepts the multipart field `file`.
With `dry_run=true` (default), only validation takes place: `count`, `new_count`,
`duplicate_count`, `total_amount`, `new_total_amount`, `date_from`/`date_to`,
and at most 100 `transactions` (`preview_truncated` marks the remainder).
Monetary values are output as exact decimal strings. `dry_run=false` commits
atomically and reports the actual `imported_count`; repeated and parallel
commits produce no duplicate rows. Write roles only, accounts of the same
organization only; known source IBANs must match the stored target IBAN.
Without a source IBAN, no automatic account verification is possible; the target account is
chosen deliberately. Faulty files are rejected entirely (422),
file/request sizes are limited (413). The import does not yet create booked
payments and therefore changes neither the rent balance nor finalized documents.

The pure assignment-suggestion engine uses the existing open items of the tenant ledger (Mietkonto)
and owner account. Gross rent is split into base rent and operating costs
(Nebenkosten, NK), known IBANs of third parties prevent a match. Confidence and review notes remain visible
in the result; ambiguous partial payments receive no invented repayment sequence,
but unallocated amounts for manual review. The engine itself does not book.

`GET /api/bank-transactions` returns transactions of authorized accounts with suggestions,
status defaults to `NEU`, explicitly `ALL` for all states,
optional `bank_account_id`, `offset`, `limit`
(50/maximum 100), as `{items,next_offset}`. Suggestions use the actual
expected/actual (Soll/Ist) figures; Hausgeld (monthly owner contribution) only from a matching finalized economic plan (Wirtschaftsplan),
owner payments for other properties are not included.
`POST /api/bank-transactions/{id}/assign` takes `{lines:[...]}` with payment type,
positive amount to the cent, year/month, and lease or owner.
The total must cover the entire transaction. Account, property, direction, and
value date come exclusively from the locked bank transaction. Payments
are booked and linked atomically; errors also roll back audit and status.
`POST /api/bank-transactions/{id}/ignore` ignores without a payment. Both transitions
apply only to `NEU`; repeated or concurrent attempts return 409. A write role as well as, for
bound accounts, `prepare` and, for booking, `confirm` are required; no
additional `approve` permission. The split dialog selects the lease or
owner of the bank account's property, payment type, and period; totals are checked to the cent
without floating-point arithmetic. API errors remain visible, double clicks
do not create a second booking. „Alle“ (all) also shows assigned/ignored transactions.
The [bank assignment browser feature](features/bank_assignment.feature) checks gross rent,
partial payment, ignoring and rent account values after reload (`make test-browser`).
Concurrent assignment conflicts remain covered by API tests.

## Rent changes with effective date (Phase 2)

`GET /api/leases/{id}/rent-changes` returns the chronological history;
`POST` adds a complete rate consisting of `valid_from`, `base_rent`, and
`advance_payment` (finite, non-negative decimal values accurate to the cent).
Effective dates must lie within the lease term and be unique per lease;
conflicts return 409. Existing lease amounts are not overwritten,
history rows are immutable. Org/lease FK, parent RLS, and existing
prepare permissions apply; readers may only retrieve. The shared financial lock
coordinates changes with calculations, ahead of the lease row lock.
Tenant ledger and bank suggestions also load changes made before the query start.
At 700 EUR base rent and 750 EUR from 15.03.2026, March yields a day-weighted
727.42 EUR, April 750 EUR; operating costs (NK) are calculated independently in the same way. Per component
and month, the entire daily sum is rounded first. Booked payments and
finalized snapshots remain unchanged.
In the unit, each lease shows original amounts and rent history. „Miete ändern
ab …“ (change rent from …) opens the dialog with date, base rent, and advance payment; the full rate valid on the selected
date is prefilled. Readers see only the history. Invalid
values are rejected before the POST, server errors retain the inputs;
while saving, further changes and closing are blocked.
[The rent change browser feature](features/rent_change.feature) verifies the UI flow,
the 24-row year and representative boundary months: 700/150 through March, 750/150 from April, annual expected amount (Soll) 10,650 EUR.

## Graduated rent (Staffelmiete) (T43)

In the unit's rent history, „Staffelmiete anlegen“ (create graduated rent) generates 1–50 full
monthly rates starting from the selected first increase date. A positive EUR amount
or percentage is applied per step to the previous base rent and rounded to the cent with
Decimal/ROUND_HALF_UP; the advance payment remains unchanged.
Example: 700 EUR + 30 EUR annually from 01.01.2027 yields five steps
730/760/790/820/850 EUR through 01.01.2031. Calendar dates stay anchored to the start day;
short months are capped at their last day, without subsequent drift.

`POST /api/leases/{id}/rent-changes/staffel` expects `staffel_start_date`,
`staffel_interval_months` (12–120), `staffel_steps` (1–50), and exactly one of
`staffel_increase_amount` (EUR decimal string) or `staffel_increase_percent`
(decimal fraction, `"0.05"` = 5%). The basis is the last rent history row before
the start, otherwise the original lease. A spacing of at least one year is required.
Existing changes from the start onward return 409; invalid values, amount
or date overflow, and steps after the lease end return 422. The transaction
creates either all steps or none. Live write/source permissions, shared
financial lock, audit, and immutable history apply as with T23.

[§ 557a BGB](https://www.gesetze-im-internet.de/bgb/__557a.html) requires
written form and monetary amounts as well as rent unchanged for at least one year.
Percent is merely a calculation aid here; the app replaces neither the agreement nor
the review of the permissible rent level/rent brake (Mietpreisbremse). No new database revision.
`scripts/acceptance/t43_staffelmiete_browser.cjs` verifies the real end-to-end UI flow.

## Index-linked rent (Indexmiete) (T44)

„Indexmiete berechnen“ (calculate index-linked rent) in the rent history expects the old/new consumer price index (VPI) with the same
Destatis base (manual entry, no retrieval) and the date the declaration was received.
The write-free preview shows `previous base rent × new VPI / old VPI`, rounded to the cent with
Decimal/ROUND_HALF_UP; 600 EUR at 110 → 115 yields 627.27 EUR.
Index decreases are also possible. The advance payment is not indexed.

`POST /api/leases/{id}/rent-changes/index/preview` expects `old_index`,
`new_index` (positive finite decimal strings, at most six decimal places)
and `declaration_received_on`. The effective date is the first day of the
month after next; at least one calendar year since the last change of the
base rent is required. A pure advance-payment change does not restart this
period. Future history rows are not replaced (409), dates
outside the lease and invalid calculation values are rejected (422).

Only „Als Mietänderung übernehmen“ (apply as rent change) additionally sends to `/rent-changes/index`
`expected_base_rent`, `expected_advance_payment`, and
`expected_previous_valid_from` from the preview. Under the shared
financial/lease lock, the calculation is redone; a stale preview returns 409.
The adoption uses the immutable T23 path and logs the VPI and the
stated receipt in the audit. Live/source permissions also apply to the preview.
Original lease, booked payments, and final snapshots remain unchanged.

[§ 557b BGB](https://www.gesetze-im-internet.de/bgb/__557b.html) requires a
written index agreement and a declaration in text form (Textform). The operator checks prerequisites
and the correct VPI base. The entered date does not prove
delivery; the function neither creates nor sends a declaration. No migration.
`scripts/acceptance/t44_indexmiete_browser.cjs` verifies the real UI flow.

## HeizkostenV calculation engine (T45)

`app.engine.heizkosten.calculate_heating_costs(pools, context=...)` distributes
separately defined heating and hot-water cost pools. Inputs/results are
frozen dataclasses from `engine/types.py`; all amounts, shares, and measured quantities
are Decimal. Consumption share 50–70%, base share 30–50%; in the explicitly
specified §7 special-building case, 70% consumption is mandatory. Heating base costs
use the specified area/heated area/volume, hot-water base costs
only residential/usable area. The existing largest-remainder function first splits
base/consumption costs and then each subtotal, to the cent, across stable UUIDs.

`consumption_recorded=False` explicitly marks a settlement that is not
consumption-based: the pool is distributed by the specified base basis,
in the context `TENANT` the full affected cost share per user is
reduced by 15% (cent rounding HALF_UP). The reduction and the remaining amount are reported
separately; no reduction amount is redistributed to other users or hot-water pools.
In the explicit context `WEG_OWNER`, this tenant reduction does not apply.
Individual missing values, NaN/Infinity/floats, negative costs/quantities, duplicate
units/pools, or unworkable zero bases are rejected loudly.

Primary sources: [§7](https://www.gesetze-im-internet.de/heizkostenv/__7.html),
[§8](https://www.gesetze-im-internet.de/heizkostenv/__8.html),
[§9](https://www.gesetze-im-internet.de/heizkostenv/__9.html), and
[§12 HeizkostenV](https://www.gesetze-im-internet.de/heizkostenv/__12.html).
The cost allocation between connected systems and exception prerequisites are
specified as inputs, not derived from assumed fuels/temperatures;
no §9a estimate and no additional 3% criteria. T45 is the pure
engine; T46 now connects it with operating costs (Betriebskosten, BK), separate meter sources, and the PDF.

In the operating cost statement (Betriebskostenabrechnung, BK-Abrechnung), „Intern nach HeizkostenV abrechnen“ (settle internally under HeizkostenV)
can be deliberately activated per confirmed heating/hot-water item. Without activation, the external
metering-service statement remains unchanged. Consumption share, §7 special case, base basis, and
meter type are entered explicitly; automatic and manual quantities remain
separate from cold water/other consumption keys. All participating units,
tenant changes, and vacancy periods are visible. Base costs are distributed by
area/volume × days; consumption costs use exact boundary/interim readings
([§9b](https://www.gesetze-im-internet.de/heizkostenv/__9b.html)) or declared
manual values (at most six decimal places), never a silent daily estimate.
The period share of a cost source spanning multiple periods is allocated to the cent in advance;
for partial allocation, the confirmed tenant amount remains authoritative. Vacancy bears the
gross owner share without tenant reduction. Missing/ambiguous values return 422.

Calculating saves the input intent in the existing draft snapshot; reload
restores it. Without a request body, saved inputs are reused,
an explicitly empty request deletes them. Preview saves nothing. Automatic
readings are resolved fresh until finalization; afterwards the configuration,
measurement vouchers, and separate base/consumption/reduction amounts are immutable in the snapshot.
The PDF opened in the UI reads only this frozen calculation path. No new migration,
no claimed §9 cost separation, §9a estimate, or legal certification.
`uv run pytest tests/test_m3_settlement.py -k heiz` verifies the backend path;
`scripts/acceptance/t46_heizkosten_browser.cjs` verifies the real UI and PDF contents.

## Security deposits (Kautionen) (Phase 2)

`/kautionen` shows per lease the agreed expected amount (Soll) from `lease.deposit`, booked
deposits received, actual repayments, and the held balance. For this,
`GET /api/payments/deposits` returns a complete authorized overview; for
classified financial sources, `accounting_scope_id` must be selected explicitly.
The MIET accounting scope (MIET-Kreis) limits the sources, not the year or account type. Missing permissions,
unclassified or incoherent sources produce an error instead of partial figures.
Under „Zahlungen“ (payments), installments are recorded as type `KAUTION`, direction `IN`, positive,
and then booked; an actual repayment is a separate `OUT` booking.
Drafts, reversed originals, and reversal lines do not count. 1,800 EUR expected,
two receipts of 900 EUR, and a 300 EUR repayment yield a 1,500 EUR balance.
The security deposit stays out of the rent balance and the NK derivations. Interest or
an automatic repayment/amortization rule is not assumed; negative
balances with incomplete legacy history remain visible. Historical active
deposits without a complete lease assignment must be corrected (409),
the existing reversal path remains available for that. New/changed/to-be-booked
deposits must match the account's property, lease, and tenant. Booking vouchers and
finalized documents remain unchanged. The real UI test case is
[the deposits browser feature](features/deposits.feature), run with `make test-browser`.

## Change of ownership (Phase 2)

In the unit, „Eigentum beenden“ (end ownership) ends an open assignment with the
inclusive last ownership day (`PATCH /api/ownership/{id}`, `valid_to` only).
The follow-up record is prepared from the next day; the new owner must be
selected and saved deliberately. Owner, unit, start, and co-ownership share (MEA)
of the old row are retained. Write role and existing prepare permissions
as well as the shared financial lock apply; co-ownership remains permitted.
The annual settlement loads its payment inputs via the authorized
`GET /api/weg-settlements/{id}/input-owners` for all owners in the period,
not only from the economic plan. Unknown payments are not zero values:
even confirmed zeros must be entered and reviewed explicitly.
Finalized results are not recalculated from today's ownership data.
`scripts/acceptance/t26_ownership_change_browser.cjs` verifies the sale on
30.06.2026, real voucher-bound annual costs of 365 EUR, the shares
181/184 EUR, reload/finalization, and the opened, content-validated PDF.

## General document storage (Phase 2)

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
original bytes. Migration 0130 refuses a lossy downgrade when
documents exist.

The „Dokumente“ (documents) section is available in property, unit, and person.
For persons, an accessible property is deliberately selected for an upload;
the person list remains cross-property. „Öffnen“ (open) loads the authorized original,
„Archivieren“ (archive) requires a reason. „Archivierte anzeigen“ (show archived) keeps retained files
visible and readable. Read-only users receive no write actions.
The real UI/PDF test case is `scripts/acceptance/t37_attachments_browser.cjs`.

## Property annual overview (Phase 2)

`GET /api/reports/property-year?property_id=…&year=2026&accounting_scope_id=…`
returns income per payment type, expenses per category/allocation flag, and
balance as exact two-decimal money strings. The basis is active booked
payments by value date (`IN` positive, `OUT` negative within the same type) and
active confirmed gross costs by invoice date. Drafts, reversal chains,
superseded costs, and merely imported bank transactions do not count.

The date basis and a domain note are included in the response. Security deposit and other
payment types remain separately visible; no tax treatment is
assumed. `TEILWEISE_PRUEFEN` remains its own flag. This is a
verifiable basis, not a complete Anlage V or tax return.
The report requires full read/source permissions and, where applicable, an
explicit homeowners' association (WEG)/MIET accounting scope covering the entire property and year. Missing
classification or partial access is not output as a complete zero report.

Under **Berichte** (reports) (`/berichte`), property, calendar year, and, where applicable,
scope are chosen deliberately. „Übersicht anzeigen“ (show overview) shows the original groups and
server totals; changing the selection discards old results. „CSV herunterladen“ (download CSV)
uses the same filters, additionally requires export permissions on scope and sources,
and delivers UTF-8 with BOM, semicolon, CRLF, and decimal comma. Texts that look like
formulas are neutralized, negative money values remain numeric. Errors leave
the inputs and the existing report intact. Historical unknown
payment types remain distinguishable by their original code.
`scripts/acceptance/t39_property_year_report_browser.cjs` verifies real UI bookings,
confirmed vouchers, and the actual download: 800 − 125 = 675 EUR.

## Tasks API (Phase 2)

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

## Payment and cost lists as CSV (Phase 2)

Both lists offer **CSV herunterladen** (download CSV) with the same active filters as the
display. Payments support account, lease, tenant, owner,
payment type, period year, and status server-side; costs combine status **and** property.
`/api/payments/export.csv` and `/api/cost-entries/export.csv` additionally check
export permissions on all selected sources and scopes, again just before
output. A single non-exportable row prevents the entire file; permitted
partial scopes need no artificial whole-property access.

UTF-8 BOM, semicolon, CRLF, decimal comma, and text formula protection apply as with the
annual report. Payment files contain the original amount and a separate
signed balance amount. **Listensaldo** (list balance) and **Listensumme Brutto** (list total, gross)
refer exclusively to the displayed rows; depending on the filter, these may
also include drafts, reversal lines, or superseded costs, not a booked account balance.
Summation remains accurate to the cent without JavaScript floating-point arithmetic. Loading,
filter changes, and late responses cannot trigger a stale file.
`scripts/acceptance/t40_csv_exports_browser.cjs` verifies real downloads with
a payment list balance of 650 EUR and a cost gross total of 125 EUR including exclusion cases.

## BK batch run (Phase 2)

Under „Abrechnungen“ (settlements), „BK-Sammellauf“ (BK batch run) creates the missing drafts for the overlapping leases after a deliberate property, period, and
scope selection.
`POST /api/settlements/batch` takes `property_id`, `period_start`, `period_end`,
and optionally `accounting_scope_id`; the response contains `created` and `skipped`
(with lease/run ID). Existing active BK runs for the same lease, period,
and scope are skipped unchanged, even if already finalized.
The shared financial lock prevents duplicate parallel batch runs; errors
roll back all new drafts and audit rows. Org, source, and
scope permissions also apply for an empty result; partial scopes restrict the units.
Nothing is calculated or finalized: actual advance payments remain
unknown (`null`) until determined via the existing flow.
`scripts/acceptance/t27_settlement_batch_browser.cjs` verifies three UI leases,
three drafts, and the identical repeat without new runs.

## Proposing future NK advance payment (Phase 2)

An active finalized BK statement shows the suggestion from its frozen
tenant cost share per settled month. Whole calendar months count as one,
partial months by occupied days; the calendar ratio remains exact until the final
commercial cent rounding. Current costs, credit/arrears, and paid
advance payments do not replace this basis. Missing historical inputs lead
to an error instead of an invented rate.
`GET /api/settlements/{id}/advance-proposal` returns costs, monthly basis, and amount
as decimal strings. „Übernehmen ab“ (apply from) and „Vorauszahlung übernehmen“ (apply advance payment) send exclusively `valid_from` via POST
to the same URL. The adoption uses the existing
T23 path: full rent history row with the base rent valid on the effective date and the new
advance payment, without overwriting the lease. Duplicate effective dates return 409;
write, source, and scope permissions apply, the history is saved before HTTP 201.
Finalized snapshots, booked payments, and the actual amounts (Ist) remain unchanged.
`scripts/acceptance/t28_advance_proposal_browser.cjs` verifies 1,200 EUR / 12 = 100 EUR
and the tenant ledger 2027 after explicit adoption: 700/100 instead of 700/150.

## Special levies backend (Sonderumlagen) (Phase 2)

`POST /api/special-levies` creates a one-time special levy with `property_id`,
explicit `accounting_scope_id`, positive `amount` accurate to the cent, `due_date`, and
optional `resolution_id`. Complete WEG property scopes are supported;
incomplete MEA/ownership bases and partial scopes are explicitly rejected.
The distribution uses the owners on the due date, for co-ownership their
positive shares within the unit, and the existing cent-exact remainder distribution.
10,000 EUR on MEA 300/300/400 yields 3,000/3,000/4,000 EUR.

Header and shares remain immutable after creation. Later MEA/ownership changes
do not recalculate old levies. `GET /api/special-levies` reads with an optional
property/scope filter under actual scope and source permissions; incomplete
access is not output as a complete empty list. The existing
owner account counts the share once as `SONDERZAHLUNG` in the
due month, even without an economic plan; regularly booked special payments
reduce the open amount. Under `/sonderumlagen`, property and read scope are
selected; write roles explicitly select the creation scope as well as amount
and due date. The page shows the fixed shares with owner and
unit names and uses the existing account/dunning pages. Without an
economic plan, a WEG property and its scope can be explicitly selected there.
Special levies become overdue only after their actual due date (plus grace period);
payments of the same month are applied once to the oldest due date.
`scripts/acceptance/t33_special_levies_browser.cjs` verifies the UI creation,
3,000 EUR open, and a real plan-less owner dunning notice including reload.

### Invitation to the owners' meeting (Phase 2)

„Einladung als PDF“ (invitation as PDF) in the meeting detail opens
`GET /api/meetings/{id}/invitation.pdf` in the browser. Each owner-profile identity
entitled at the meeting date receives its own page with name,
postal address, date/place, existing invitation text, and sorted agenda.
Multiple units of the same profile produce no duplicate letters; co-owners
with separate profiles receive separate pages. Property and contact export permissions
are checked before and after rendering; a blocked contact does not produce a
seemingly complete partial output. Missing addresses, empty recipient lists,
or page overflow are rejected with 422, not invented or truncated.

With fewer than 21 calendar days from the export/read date, the shared
backend notice appears in UI and PDF (§ 24 Abs. 4 WEG). It confirms neither dispatch nor
timely receipt. The export changes no meeting, agenda,
ownership history, or existing minutes. The real Chrome flow is repeatable with
`scripts/acceptance/t34_meeting_invitation_browser.cjs`.

## Meter API (Phase 2)

`GET/POST /api/meters` manages property meters and meters optionally
assigned to a unit. Lists can be filtered by `property_id` and `unit_id`;
`GET/PATCH /api/meters/{id}` reads or corrects only
number, type, and location. Property and unit are not reassigned afterwards.
`GET/POST /api/meters/{id}/readings` returns the chronological history and
adds a reading from `reading_date`, `value`, and optionally `period`.
Measured values are non-negative, exact decimal strings with at most ten integer
and four decimal places; responses always contain four decimal places.
Existing readings are not overwritten, identical reading days are allowed.
Org isolation and property master permissions apply to lists, single retrieval, and writing;
read permission alone allows no change, PAUSED receives only the permitted read paths.
Write paths use live role checks, financial lock, and commit before HTTP success.
The meter panel in property and unit (T30) allows creating, correcting
description fields, recording a reading, and the chronological history.
Unit meters remain permanently assigned; property lists also include their meters.
German decimal inputs are normalized exactly. „Verbrauch“ (consumption) shows exclusively
the difference of the last two unambiguous reading days with both dates; on a decrease
or ambiguous reading days, a review is required instead of an invented consumption.
READ_ONLY sees only the history. Failed attempts retain inputs, parallel saving
is blocked, and late responses do not switch the current context.
`scripts/acceptance/t30_meters_browser.cjs` verifies UI creation and reload with
100,0000 → 250,1250 = 150,1250 and a separate unit meter.
The BK statement (T31) adopts, without a manual value, the exact consumption from the
unambiguously nearest reading within ±31 days around the start and end of the period.
The prerequisite is exactly one unit meter with a type matching the cost category,
e.g. `KALTWASSER`. Missing/ambiguous readings, decreases, multiple meters, or
different consumption types require manual entries instead of estimation; common-property
meters are not used as unit meters. Deliberate manual values including
0 take precedence. All participating units need a complete basis.
`GET /api/settlements/{id}/consumption-suggestions` returns authorized
suggestions for drafts with reading IDs, date, and value; the interface shows this basis without sending it
back as a manual value. Calculation and finalization re-read under the financial lock;
the measurement data used is frozen in the immutable snapshot.
`scripts/acceptance/t31_meter_consumption_browser.cjs` verifies, exclusively through the
interface, two units with 30/70 consumption and 100 EUR water costs: two real
settlements yield 30/70 EUR including reload and finalization.

### Default interest on dunning notices (Verzugszinsen) (Phase 2)

Both dunning notice forms require an explicitly reviewed „Basiszinssatz (%)“ (base interest rate).
`HV_DUNNING_BASE_INTEREST_RATE` optionally provides the default; without configuration
the field stays empty. The app claims no official or automatically current
interest rate. `GET /api/dunning/settings` returns the default for signed-in members.
An explicit decimal string `base_interest_rate` overrides it; old
API calls without it and without configuration remain interest-free, explicit `null`
disables the calculation. Existing letters/PDFs are not recalculated as a result.

The calculation per open item is `amount × (base + 5) / 100 × calendar days / 365`,
from the actual due date, with a single `ROUND_HALF_UP` cent rounding. Fees
and interest are not charged interest. Example: 700 EUR, base 2.50%, 60 days →
8.63 EUR interest and 708.63 EUR total with fee 0. Due date, reference date,
days, base/annual interest rate, and amounts are saved and shown in detail/PDF;
later configuration or payment changes do not recalculate the issued dunning notice.
Migration 0129 protects this provenance and refuses its loss
on downgrade. Issuance alone confirms no receipt. The real UI/PDF flow
is repeatable with [the dunning interest browser feature](features/dunning_interest.feature)
using `make test-browser`.

### Deleting unused erroneous entries (T56)

In property, unit, and person details as well as on a lease, there is an explicit delete dialog
for users with write permission. Without incoming references
and with current source-prepare permission, exactly the chosen record is
removed; linked data is never deleted along with it. For references, 409 states
the reason, for example a lease or a rent history. Source approvals,
creation/import receipts, audit, and saved financial snapshots
are also protected references. Existing RLS/privacy locks remain authoritative.
A person with a tenant/owner/vendor profile is already referenced.
The UI-only test `scripts/acceptance/t56_master_delete_browser.cjs` verifies cancel,
actual deletion of an empty unit, and the visible lease reason of a
used unit after desktop/phone reload; original leases remain identical.

### Editing and ending master data (T55)

Bank accounts can be renamed under **Bankkonten → Bankkonto bearbeiten** (bank accounts → edit bank account) and,
after explicit confirmation, ended or reactivated. Historical transactions
remain visible; inactive accounts are not a source for new payments, imports,
or SEPA suggestions. A pure rename changes no SEPA mandates.
IBAN/BIC that have already been used are protected: create a new account instead.
Exact reversal corrections of booked originals remain possible even after ending.

Vendor details can be edited and ended with contact-prepare permission;
new costs may not select inactive vendors. An admin can
edit/end management mandates, but cannot rewrite a definition already
bound by financial scopes (409). This does not concern SEPA mandates.
Properties have optional **Baujahr** (year built) and **Gesamtfläche (m²)** (total area); the area is stored as
Decimal, not invented from units. Empty entries remain unknown.
The UI-only test `scripts/acceptance/t55_master_data_browser.cjs` verifies the real
rename, 1984 / 1.234,56 m² and unchanged sources after desktop/phone reload.

### Economic plan from prior-year actuals (T54)

Under **Wirtschaftspläne → Plan aus Vorjahres-Ist** (economic plans → plan from prior-year actuals), select property, target year, and – for
classified sources – the prior-year read scope and the new WEG prepare scope
separately. The non-writing preview uses current confirmed
gross costs by service period; items spanning several years are rounded to the cent per item
by inclusive days, credits are netted. Superseded/unconfirmed
costs do not count; negative net categories, inactive or ambiguous
categories require review instead of silent omission. Reserves are separate.

Explicitly edit/confirm plan amounts and the reserve suggestion. Only
an uncalculated draft is created, with amounts and category IDs retained after reload;
source changes require a new preview (409). No automatic
data-basis attestation or finalization. Existing costs and old plans
remain unchanged. If the creation response is unclear, check the plan list first, do not
automatically create again. The UI-only flow
`scripts/acceptance/t54_previous_plan_browser.cjs` verifies plan 2027 from actuals 2026,
7,000 EUR costs + 6,000 EUR separate reserve as well as edited 7,100.25 + 6,500 EUR
and desktop/smartphone reload.

### Dunning run with selected drafts (T53)

Under **Offene Posten → Mahnlauf öffnen** (open items → open dunning run), explicitly choose period, reference date, minimum arrears,
and, where applicable, the MIET prepare scope. The preview
writes nothing and contains leases with a principal claim **greater than** the threshold.
Rent history, booked payments, and dunning levels come from the same tenant ledger/
dunning path as for individual drafts. Selection and displayed total must be confirmed;
only drafts are created, no issuance, PDFs, or emails.

Sources changed in the meantime require a new preview (409). If the network response is
unclear, the same job is kept for a manual retry;
its original IDs are returned under renewed complete prepare/source checks,
not recalculated or duplicated. Old issued letters remain
unchanged. [The dunning batch browser feature](features/dunning_batch.feature) verifies three
arrears of 850 EUR each and three drafts totaling 2,550 EUR, including mobile
cancellation and reload (`make test-browser`). Source immutability remains covered by API tests.

### Email dispatch (T47)

Issued dunning notices, finalized BK statements, and person-specifically addressed
meeting invitations offer an explicit email dispatch. The address
comes exclusively from the authorized contact; no freely entered recipients.
In addition to live write, source, and contact export permissions, for bound
financial documents the own scope capability `send` is required (PDF export is
not sufficient). Reading roles cannot trigger a dispatch.

SMTP is disabled without `HV_SMTP_HOST` and `HV_SMTP_FROM`. Further options:
`HV_SMTP_PORT` (587), `HV_SMTP_SECURITY` (`starttls`, alternatively `tls`), together
`HV_SMTP_USERNAME`/`HV_SMTP_PASSWORD` as well as `HV_SMTP_TIMEOUT_SECONDS` (15).
`plain` is permitted exclusively without authentication on loopback for local tests.
No credentials or real recipients are assumed.

Before SMTP, a dispatch attempt is durably stored in the existing append-only audit on the
document, including recipient, message ID, and a reference to the
exact PDF in the document storage. A retry key prevents duplicate
transmission of the same request. The history distinguishes SMTP acceptance, failure,
and unclear outcome; even an attempt left open after a crash is **not**
proof that no dispatch took place. No automatic retry on unclear
outcome. SMTP acceptance is **not proof of delivery or receipt**; deadlines and receipt
must be checked separately. „Versand-PDF öffnen“ (open dispatch PDF) reads the stored bytes with
hash verification and current source/original contact export permissions, without
recalculating finalized documents. No schema change.

`uv run pytest tests/test_mail_service.py` verifies fake SMTP and domain/permission paths.
`scripts/acceptance/t47_mail_browser.cjs` operates only a local SMTP receiver
on `127.0.0.1:11025` without forwarding and verifies the actual UI/PDF flow.

### Letter templates and circulars (T48)

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
dispatch and no proof of delivery. Migration **0132** refuses a downgrade
with stored templates or letters. Tests: `uv run pytest tests/test_letters.py`;
real UI/PDF acceptance: `scripts/acceptance/t48_letters_browser.cjs`.

### DATEV booking batch (Buchungsstapel) (T49)

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
DATEV import. No upload, no change to original bookings, and no new
migration. The account configuration is entered explicitly per output, not stored
as a global category assignment. Tests:
`uv run pytest tests/test_datev_export.py`, real Q1 acceptance:
`scripts/acceptance/t49_datev_browser.cjs`.

### SEPA monthly rent (T50)

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
No predefined real bank data or mandates. Migration **0133** protects
mandate identities and refuses a downgrade with stored SEPA data.
Tests: `uv run pytest tests/test_sepa_export.py`; UI-only acceptance with isolated
launcher: `HV_PYTHON="$PWD/backend/.venv/bin/python" node scripts/acceptance/t50_sepa_browser.cjs`.

### CSV master data import (T51)

Under **Objekte → CSV-Stammdaten importieren** (properties → import CSV master data), actually download the CSV template
and fill it as a UTF-8 file (BOM permitted) with semicolons and unchanged columns.
`property`, `unit`, and `person` create new properties, units, and contacts;
`property_key` references a local property key or an authorized
existing property UUID. Areas remain exact decimal values, including `60,25`.
At most 500 rows or 256 KiB; unknown columns, invalid values, and duplicates
are reported with row/field. No automatically created profiles or leases.

The preview writes nothing. Only after review, explicit approval of the
data scope (if required), and confirmation is the entire import created atomically.
There are no silent self-approvals. Changes require a new
preview; repeating the same request returns the same IDs, after permission revocation
no partial response. Continue unclear network errors with **Import erneut versuchen** (retry import),
do not duplicate through new requests. Revision 0134 holds only immutable,
user-bound result IDs, not the CSV or contact data in the batch.
Tests: `uv run pytest tests/test_master_import.py`; real UI/download flow:
`node scripts/acceptance/t51_master_import_browser.cjs`.

### Monthly rent KPIs (T52)

In the dashboard under **Mietkennzahlen** (rent KPIs), select property, rent month/year, and, where applicable, the
complete MIET accounting scope. Expected (Soll) covers base rent and
advance payments for operating costs including rent history and partial months. Actual (Ist) counts
active booked KALTMIETE/NK payments by assigned rent month, not by
value date; payouts are deducted. Unassigned payments are
reported separately, not silently added to a month.

Arrears are positive net balances per lease from lease start until the end of the
chosen month; credit balances of another lease do not offset them.
Vacancy values today's unit inventory at the end of the chosen month, not
a versioned historical property inventory. Missing permissions, incomplete
history coverage, or incoherent bookings produce errors instead of false zeros.
The overview changes no bookings or leases. Mandatory check:
`uv run pytest tests/test_dashboard_kpis.py`; real UI acceptance:
`node scripts/acceptance/t52_dashboard_kpis_browser.cjs`.

Further documentation:

- [Deployment](DEPLOY.md)
- [Architecture](docs/architecture.md)
- [Roadmap](docs/roadmap.md) and [feature status](docs/feature-status.md); tasks are GitHub issues
- [Declarations of division and building knowledge](docs/operations/declaration-imports.md) — upload, reviewable extraction, approved building rules, and operations.
