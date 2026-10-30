# Tenancies and rent

Rent history, graduated and index-linked rent, security deposits, and ownership changes.

## Rent changes with effective date

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
`scripts/acceptance/t24_rent_change_browser.cjs` verifies the UI flow and all
24 month/type rows: 700/150 through March, 750/150 from April, annual expected amount (Soll) 10,650 EUR.

## Graduated rent (Staffelmiete)

T43 introduced this workflow.

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
financial lock, audit, and immutable history apply as for
[rent changes with effective date](#rent-changes-with-effective-date).

[§ 557a BGB](https://www.gesetze-im-internet.de/bgb/__557a.html) requires
written form and monetary amounts as well as rent unchanged for at least one year.
Percent is merely a calculation aid here; the app replaces neither the agreement nor
the review of the permissible rent level/rent brake (Mietpreisbremse).
Deployment constraints are documented under the
[deployment boundary](../../DEPLOY.md#deployment-boundary-and-accepted-head).
`scripts/acceptance/t43_staffelmiete_browser.cjs` verifies the real end-to-end UI flow.

## Index-linked rent (Indexmiete)

T44 introduced this workflow.

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
The adoption uses the immutable workflow for
[rent changes with effective date](#rent-changes-with-effective-date) and logs the VPI and the
stated receipt in the audit. Live/source permissions also apply to the preview.
Original lease, booked payments, and final snapshots remain unchanged.

[§ 557b BGB](https://www.gesetze-im-internet.de/bgb/__557b.html) requires a
written index agreement and a declaration in text form (Textform). The operator checks prerequisites
and the correct VPI base. The entered date does not prove
delivery; the function neither creates nor sends a declaration.
Deployment constraints are documented under the
[deployment boundary](../../DEPLOY.md#deployment-boundary-and-accepted-head).
`scripts/acceptance/t44_indexmiete_browser.cjs` verifies the real UI flow.

## Security deposits (Kautionen)

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
`scripts/acceptance/t25_deposits_browser.cjs`.

## Change of ownership

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
