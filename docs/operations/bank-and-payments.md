# Bank and payments

Bank-file import and assignment, CSV lists, dunning, and monthly rent figures.

## Bank transactions and file import

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
The local browser flow, including a second import with zero new transactions,
is repeatable with `scripts/acceptance/t19_bank_import_browser.cjs` against `scripts/dev-local.sh`.
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
The real browser flow `scripts/acceptance/t22_bank_assignment_browser.cjs`
checks gross rent, partial payment, ignoring, second-tab conflict, and the
tenant ledger values persisted after reload against `scripts/dev-local.sh`.

## Payment and cost lists as CSV

Both lists offer **CSV herunterladen** (download CSV) with the same active filters as the
display. Payments support account, lease, tenant, owner,
payment type, period year, and status server-side; costs combine status **and** property.
`/api/payments/export.csv` and `/api/cost-entries/export.csv` additionally check
export permissions on all selected sources and scopes, again just before
output. A single non-exportable row prevents the entire file; permitted
partial scopes need no artificial whole-property access.

UTF-8 BOM, semicolon, CRLF, decimal comma, and text formula protection apply as with the
[property annual overview](statements.md#property-annual-overview). Payment files contain
the original amount and a separate signed balance amount. **Listensaldo** (list balance) and **Listensumme Brutto** (list total, gross)
refer exclusively to the displayed rows; depending on the filter, these may
also include drafts, reversal lines, or superseded costs, not a booked account balance.
Summation remains accurate to the cent without JavaScript floating-point arithmetic. Loading,
filter changes, and late responses cannot trigger a stale file.
`scripts/acceptance/t40_csv_exports_browser.cjs` verifies real downloads with
a payment list balance of 650 EUR and a cost gross total of 125 EUR including exclusion cases.

## Default interest on dunning notices (Verzugszinsen)

Both dunning notice forms require an explicitly reviewed „Basiszinssatz (%)“ (base interest rate).
Without a configured default the field stays empty; see
[dunning interest defaults](../../DEPLOY.md#dunning-interest-defaults).
The app claims no official or automatically current
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
Rollback restrictions are documented under
[migration compatibility](../../DEPLOY.md#migration-compatibility-00290139).
Issuance alone confirms no receipt. The real UI/PDF flow
is repeatable with `scripts/acceptance/t35_dunning_interest_browser.cjs`.

## Dunning run with selected drafts

T53 introduced this workflow.

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
unchanged. The [UI acceptance procedure](../../DEPLOY.md#selected-dunning-batches-t53-local-head-0135) verifies three
arrears of 850 EUR each and exactly three drafts totaling 2,550 EUR, including
mobile abort, reload, and unchanged source data.

## Monthly rent KPIs

T52 introduced this workflow.

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
`uv run pytest tests/test_dashboard_kpis.py`; see the
[local UI acceptance procedure](../../DEPLOY.md#monthly-dashboard-rent-kpis-t52).
