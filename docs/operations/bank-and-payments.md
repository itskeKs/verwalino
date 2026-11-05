# Bank and payments

Bank-file import and assignment, CSV lists, dunning, and monthly rent figures.

## Missing payments

Opening a missing payment under `/zahlungen/:paymentId` shows exactly
“Zahlung nicht gefunden.”. The detail API retains HTTP 404 with a `detail` string;
a foreign organization's payment returns the same response. The payment list and
detail load handlers display API messages without the “ApiError:” class prefix,
while keeping their existing German sign-in notices and non-API fallbacks.
The [missing-record journey twin](../../features/journeys/missing_weg_objects.feature)
checks the detail error at desktop and phone widths.

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
The browser review/confirmation flow is [an executable feature](../../features/bank_statement_browser.feature),
run with `make test-browser`; parsing and duplicate imports are covered by
[the API feature](../../features/bank_statement_import.feature).
For write roles, the transactions page offers „Vorschlag übernehmen“ (accept
suggestion), „Aufteilen“ (split), and „Ignorieren“ (ignore). Suggestions that
require review open the split dialog; unclear amounts must be explicitly split
completely there.

For a debit, “Aufteilen” opens “Bestehende Kostenposition”. Select an active,
confirmed cost of the bank account's property and choose “Kostenposition zuordnen”.
The picker shows the remaining bank coverage and the original total, with the
remaining amount after this assignment. A −60.00 EUR debit can cover a 60.00 EUR
cost completely, or part of a 120.00 EUR cost; another −60.00 EUR debit then covers
the remainder. The complete bank transaction links to one cost, without creating
a cost or a payment. The transaction becomes “Zugeordnet”; yearly expenses still
count that confirmed cost once. Positive transactions, other properties or
accounting scopes, unconfirmed/superseded costs and excessive coverage are refused.
Later classification of legacy matched records also refuses different cost/bank scopes.
In a one-to-one cost correction, the original bank links and audit history remain
in place. Each successor inherits the assigned amount from earlier versions. For
example, after assigning a −60.00 EUR debit to a 150.00 EUR cost, correcting and
confirming it at 120.00 EUR leaves 60.00 EUR open for another debit. A correction,
draft edit or confirmation below the inherited coverage is refused. “Verwerfen”
is refused for a correction that carries bank coverage. The bank picker uses the remaining amount of the current confirmed version.

When correcting a reviewed allocation, load “Korrekturvorschau” and use
“Bankumsätze übernehmen” to select a new part for each old part that already
has bank coverage. For a 119.00 EUR invoice changing from 59.00 + 60.00 EUR to
19.00 + 40.00 + 60.00 EUR, select the third new part for a debit covering the
old second part. The third part then has nothing open; the first two still
have 19.00 and 40.00 EUR open. The debit remains linked to the original cost.
Each selected successor must cover the sum inherited from its selected
predecessors. If a new debit makes the preview stale, reload it before confirming.

Concurrent assignments serialize and repeating the same cost assignment succeeds
without covering it twice. A failed assignment keeps the selected cost; use
“Kostenpositionen neu laden” to refresh a conflicting remaining amount.

“Zuordnen als” also offers “Zahlung (z. B. Erstattung oder Rücklagenentnahme)” for
the existing tenant-refund and reserve-payout booking paths. Incoming transactions
keep the payment split. Cost matching does not alter bank reconciliation totals,
reserve accounting or the cost original. The bank link and its financial fields
are immutable. A released scoped reserve draft retains its cancellation history
when the debit is reassigned to a cost. Supported organization archives restore
ordinary links and remaining coverage, including links classified on one side only
and retained cancelled drafts. Readable exports record explicit reviewed successor
links; reviewed-document archives still require private provenance to import.
During organization erasure, the matched bank evidence inherits its cost's
retention deadline; an unmatched line gains no retention from this feature.

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
Sparkasse CSV-CAMT is recognised by its `Auftragskonto`,
`Beguenstigter/Zahlungspflichtiger`, `Kontonummer/IBAN`, `Waehrung`, and `Info`
headers, alongside the required date and amount columns. This profile also accepts
`TT.MM.JJ` dates, preserves counterparty names and IBANs, requires `Waehrung` to be
`EUR` and `Info` to be „Umsatz gebucht“, and checks every row's `Auftragskonto`
against the selected bank account. Pending, unknown or empty statuses are refused.
The [synthetic acceptance statement](../acceptance/data/sparkasse-2026-01-10.csv)
has 31 transactions; its 800.00 EUR receipt on 07.04.2026 retains Anna Beispiel's
name and IBAN for a lease suggestion. Uploading it again adds no transactions.
Unknown/ambiguous formats and invalid rows abort the entire import
with a clear message; no rounding or silent field truncation.

Import identities are independent of file name, encoding, and column order.
Multiple identical bookings are retained. Without a bank-side
transaction ID, overlapping statements with a differing number of otherwise
identical bookings cannot be distinguished unambiguously; this limit must not
be presented as reliable cross-bank duplicate detection.

For Sparkasse files imported before this profile was supported, manually expanding
the years allowed an import but left the counterparty name and IBAN empty. Those
fields are part of the import identity: importing the original statement now can
create duplicates of those historical rows. Existing hashes are not rewritten and
no retrospective matching is performed. Review earlier imports before uploading
an overlapping statement; duplicate detection applies to repeated imports with
the newly recognised profile.

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
The [bank assignment browser feature](../../features/bank_assignment.feature) checks gross rent,
partial payment, ignoring and rent account values after reload (`make test-browser`).
Concurrent assignment conflicts remain covered by API tests.

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

Creating an individual tenant or owner notice opens its saved draft immediately.
The successful creation response commits the draft before the detail request can
start; no reload or second creation is needed. Amounts, dates, interest and the
immutable issued notice retain their existing calculation and snapshot rules.

Both dunning notice forms require an explicitly reviewed „Basiszinssatz (%)“ (base interest rate).
Without a configured default the field stays empty; see
[dunning interest defaults](../../DEPLOY.md#dunning-interest-defaults).
The app claims no official or automatically current
interest rate. `GET /api/dunning/settings` returns the default for signed-in members.
An explicit decimal string `base_interest_rate` overrides it; old
API calls without it and without configuration remain interest-free, explicit `null`
disables the calculation. Existing letters/PDFs are not recalculated as a result.

The calculation per open item is `amount × (base + 5) / 100 × calendar days / 365`,
starting the day after the actual due date, with a single `ROUND_HALF_UP` cent rounding. Fees
and interest are not charged interest. Example: 700 EUR January 2026 rent, due 6 January, reference
2 March, base 2.50%, 55 days → 7.91 EUR interest and 707.91 EUR total with fee 0. Due date, reference date,
days, base/annual interest rate, and amounts are saved and shown in detail/PDF;
later configuration or payment changes do not recalculate the issued dunning notice.
New `KALTMIETE` and `NK_VORAUSZAHLUNG` interest rows additionally freeze the first
interest date and explicitly show `Zinsbeginn: TT.MM.JJJJ` in the detail and PDF.
Older rows without this date retain their existing content and identical PDF
rendering; no historical date is inferred. Heating, WEG and one-off rows keep
their existing presentation and calculation.
Rollback restrictions are documented under
[migration compatibility](../../DEPLOY.md#migration-compatibility-00290148).
Issuance alone confirms no receipt. The real UI/PDF flow
is repeatable with [the dunning interest browser feature](../../features/dunning_interest.feature)
using `make test-browser`.

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
unchanged. [The dunning batch browser feature](../../features/dunning_batch.feature) verifies three
arrears of 850 EUR each and three drafts totaling 2,550 EUR, including
mobile cancellation and reload (`make test-browser`). Source immutability remains covered by API tests.

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


## Rent due dates and holiday coverage

New rent calculations stamp `KALTMIETE` and `NK_VORAUSZAHLUNG` with the third
working day under [§ 556b(1) BGB](https://www.gesetze-im-internet.de/bgb/__556b.html).
Saturday does not count ([BGH VIII ZR 129/09](https://juris.bundesgerichtshof.de/cgi-bin/rechtsprechung/document.py?Art=pm&Blank=1&Datum=2010-7&Gericht=bgh&file=dokument.pdf&linked=urt&nr=52857)).
Set **Bundesland** in the property's creation/edit form. Empty means nationwide
holidays; existing properties keep this empty state until explicitly edited.
A saved change affects newly derived charges, never frozen issued notices/PDFs.
WEG dues, one-off claims, rates and arrears aggregation retain their existing rules.
The separate `HK_VORAUSZAHLUNG` line retains its previous due-date rule pending
owner confirmation; it is not covered by this change.

For April 2026, the counted days are 1, 2 and 7 April. Good Friday (3 April),
Easter Monday (6 April) and the weekend are excluded. Payment in full on 7 April
produces no reminder or interest. A 100 EUR shortfall is due on 7 April and
accrues interest from 8 April (0.02 EUR on that day at a 2.50% base rate).
Day-exact initial-month amounts stay unchanged. Count the three working days
inclusively from the later of month start and lease start, even when the deadline
falls in the next month or year. A lease starting Saturday, 11 April 2026 has
counted days 13, 14 and 15 April; no reminder or interest is due through 15 April.
Clipping a report to mid-month or changing the rent does not restart the count.

The small pure calendar uses fixed and Gregorian Easter dates, without a new
holiday dependency. Nationwide: New Year, Good Friday, Easter Monday, 1 May,
Ascension, Whit Monday, 3 October and both Christmas days. Statewide additions:

| Holiday | States |
| --- | --- |
| 6 January | Baden-Württemberg, Bavaria, Saxony-Anhalt |
| Corpus Christi | Baden-Württemberg, Bavaria, Hesse, North Rhine-Westphalia, Rhineland-Palatinate, Saarland |
| Assumption (15 August) | Saarland |
| Reformation (31 October) | Brandenburg, Mecklenburg-Vorpommern, Saxony, Saxony-Anhalt, Thuringia; Bremen, Hamburg, Lower Saxony and Schleswig-Holstein from 2018 |
| All Saints (1 November) | Baden-Württemberg, Bavaria, North Rhine-Westphalia, Rhineland-Palatinate, Saarland |
| Easter Sunday and Whit Sunday (already excluded as Sundays for rent) | Brandenburg |
| Repentance Day | Saxony; Wednesday before 23 November |
| Women's Day (8 March) | Berlin from 2019; Mecklenburg-Vorpommern from 2023 |
| Children's Day (20 September) | Thuringia from 2019 |

Sources: [official state overview](https://www.hamburg.de/resource/blob/134374/7a4a9de67fd46caec0947c11bf23316d/bsb-feiertage-data.pdf),
[Bavarian FTG Art. 1](https://www.gesetze-bayern.de/Content/Document/BayFTG-1),
[Lower Saxony's dated Reformation rule](https://www.mi.niedersachsen.de/themen/allgemeine_angelegenheiten_inneren/feiertagsrecht/feiertagsrecht-60368.html),
[Women's Day introductions](https://www.deutsche-rentenversicherung.de/DRV/DE/Ueber-uns-und-Presse/Presse/Meldungen/2024/240306_frauentag_feiertag_frei?nn=6a097debca84ce5471c1aa2d),
[Thuringian parliament, 28 February 2019](https://parldok.thueringer-landtag.de/ParlDok/dokument/70291/140_plenarsitzung.pdf).
Reformation introductions are also recorded by the
[Bremen legislature](https://www.transparenz.bremen.de/metainformationen/gesetz-ueber-die-sonn-gedenk-und-feiertage-vom-12-november-1954-145882?template=20_gp_ifg_meta_detail_d),
[Hamburg parliament](https://www.buergerschaft-hh.de/parldok/dokument/64307/bilanz_der_freien_museumsoeffnung_fuer_alle_am_tag_der_reformation.pdf)
and [Lower Saxony's government announcement covering Schleswig-Holstein](https://www.stk.niedersachsen.de/startseite/presseinformationen/landesregierung-schlaegt-reformationstag-als-neuen-feiertag-vor-164249.html).
Saxony's holiday list is in [SächsSFG](https://revosax.sachsen.de/vorschrift/3997).
The calendar also includes [nationwide Reformation Day in 2017](https://www.mi.niedersachsen.de/startseite/aktuelles/presseinformationen/reformationstag-ist-im-jahr-2017-ein-arbeitsfreier-gesetzlicher-feiertag--148206.html)
and Berlin's [one-off 8 May in 2020/2025 and 17 June 2028](https://www.berlin.de/aktuelles/9188766-958090-einmaliger-feiertag-am-8-mai-2025.html).
This is the modern statewide calendar with those dated changes, not an archive
of historical legislation before 1995. Municipality-only holidays (Augsburg's
Peace Festival, Bavarian Assumption, local Corpus Christi in Saxony/Thuringia)
cannot be inferred from a state and are excluded. No municipality is guessed
from an address; contracts requiring additional local review remain outside
this calendar's coverage.
