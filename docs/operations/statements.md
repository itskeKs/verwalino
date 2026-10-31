# Statements

This page covers heating calculation and BK integration (T45/T46), annual property
figures (T39), BK batch drafts (T27), advance-payment proposals (T28), and prior-year
economic plan proposals (T54).

## HeizkostenV calculation engine

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
area/volume × days. Allocation always uses the full property-period pool and
building consumption, before selecting a lease's clipped statement. Exact interim
readings ([§9b](https://www.gesetze-im-internet.de/heizkostenv/__9b.html)) split only
the changed unit's consumption-dependent share; other units need no reading at that
change date. Declared manual quantities remain supported (at most six decimal places).
Missing interim readings split known unit-period consumption by degree-days if
available, otherwise by days. The engine currently has no degree-day model, so the
fallback is days; basic costs always split by days. Missing period consumption still
returns 422 and is never replaced with zero or estimated building consumption.
For a 10,000-euro pool, 70% consumption, two equally sized units each using 1,000,
and A/B using 600/400 in the first unit, the statements are 3,221.92 / 1,778.08 /
5,000.00 euros. Successive leases plus vacancy retain the unit/property totals to the
cent. Adjacent free windows select cumulative cents from the balanced occupant cells.
The period share of a cost source spanning multiple periods is allocated to the cent in advance;
for partial allocation, the confirmed tenant amount remains authoritative. Vacancy bears the
gross owner share without tenant reduction. Missing/ambiguous values return 422.

Calculating saves the input intent in the existing draft snapshot; reload
restores it. Without a request body, saved inputs are reused,
an explicitly empty request deletes them. Preview saves nothing. Automatic
readings are resolved fresh until finalization; afterwards the configuration,
measurement vouchers, and separate base/consumption/reduction amounts are immutable in the snapshot.
The PDF opened in the UI reads only this frozen calculation path.
No §9 cost separation, §9a estimate, or legal certification is claimed.
Deployment constraints are documented under the
[deployment boundary](../../DEPLOY.md#deployment-boundary-and-accepted-head).
`uv run pytest tests/test_m3_settlement.py -k heiz` verifies the backend path;
`scripts/acceptance/t46_heizkosten_browser.cjs` verifies the real UI and PDF contents.

## Property annual overview

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

The **Anlage V** panel under **Berichte** maps this per-property year overview to
form detail lines via `GET /api/reports/property-year/anlage-v`.
`GET /api/reports/property-year/anlage-v.csv` exports the mapping as CSV. Both use
the same property, year and optional accounting scope. This is a
"Zuordnungshilfe, keine Steuerberatung"; "Zeilennummern je Formularjahr prüfen"
(check line numbers for each form year). The mapping ignores the Umlagefähigkeit
split and lists `NACHZAHLUNG` under "Nicht zugeordnet": a Nachzahlung can come from
a tenant or an owner, so its origin needs separate review.

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

## BK batch run

Under „Abrechnungen“ (settlements), „BK-Sammellauf“ (BK batch run) creates the missing drafts for the overlapping leases after a deliberate property, period, and
scope selection.
`POST /api/settlements/batch` takes `property_id`, `period_start`, `period_end`,
and optionally `accounting_scope_id`; the response contains `created` and `skipped`
(with lease/run ID). Existing active BK runs for the same lease, period,
and scope are skipped unchanged, even if already finalized. A finalized legacy
run covering the lease's clipped span is also skipped, with a German `reason` in
the skipped item. Partial overlaps retain the structured conflict; single creation
keeps its overlap 409.
The shared financial lock prevents duplicate parallel batch runs; errors
roll back all new drafts and audit rows. Org, source, and
scope permissions also apply for an empty result; partial scopes restrict the units.
Nothing is calculated or finalized: actual advance payments remain
unknown (`null`) until determined via the existing flow.
`scripts/acceptance/t27_settlement_batch_browser.cjs` verifies three UI leases,
three drafts, and the identical repeat without new runs.

## Proposing future NK advance payment

An active finalized BK statement shows the suggestion from its frozen
tenant cost share per settled month. Whole calendar months count as one,
partial months by occupied days; the calendar ratio remains exact until the final
commercial cent rounding. Current costs, credit/arrears, and paid
advance payments do not replace this basis. Missing historical inputs lead
to an error instead of an invented rate.
`GET /api/settlements/{id}/advance-proposal` returns costs, monthly basis, and amount
as decimal strings. „Übernehmen ab“ (apply from) and „Vorauszahlung übernehmen“ (apply advance payment) send exclusively `valid_from` via POST
to the same URL. The adoption uses the existing
[rent changes with effective date](tenancies-and-rent.md#rent-changes-with-effective-date)
workflow: a full rent history row with the base rent valid on the effective date and the new
advance payment, without overwriting the lease. Duplicate effective dates return 409;
write, source, and scope permissions apply, the history is saved before HTTP 201.
Finalized snapshots, booked payments, and the actual amounts (Ist) remain unchanged.
`scripts/acceptance/t28_advance_proposal_browser.cjs` verifies 1,200 EUR / 12 = 100 EUR
and the tenant ledger 2027 after explicit adoption: 700/100 instead of 700/150.

## Economic plan from prior-year actuals

T54 introduced this workflow.

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
automatically create again. The [UI-only acceptance](../../DEPLOY.md#previous-year-plan-proposals-t54-no-new-migration)
verifies plan 2027 from actuals 2026,
7,000 EUR costs + 6,000 EUR separate reserve as well as edited 7,100.25 + 6,500 EUR
and desktop/smartphone reload.
