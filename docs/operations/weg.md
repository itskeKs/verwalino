# Homeowners' associations (WEG)

Special levies and PDF invitations to owners' meetings.

## Missing economic plans and annual settlements

A missing economic-plan detail shows “Wirtschaftsplan nicht gefunden.”; a missing
WEG settlement detail shows “WEG-Abrechnung nicht gefunden.”. The settlement's
input-owner read uses the same explanation when its run is missing. These reads
retain HTTP 404 and the existing `detail` string, including for foreign organizations.
The plan and WEG list/create pages and detail views display `ApiError.message`,
so the text appears without the technical “ApiError:” prefix. Existing German
sign-in notices and non-API error fallbacks remain unchanged.
The [missing-record journey twin](../../features/journeys/missing_weg_objects.feature)
checks the visible detail explanations at 1280 px and 360 px; the
[organization-isolation feature](../../features/organization_isolation.feature)
checks that foreign financial records remain indistinguishable from missing ones.

## Co-owner shares in the Wirtschaftsplan and the annual settlement

Enter the „MEA-Anteil“ of every co-owner of a unit in the unit's Eigentumshistorie as a fraction
(0,5 / 0,5) or as percent (50 / 50); the annual settlement divides the unit's share among the
owners active on the same days by these values as ratios, span by span when an owner joins or
leaves during the year. A sole owner needs no share. If the active shares of a unit add up to
neither 1 nor 100 (0,6 and 0,3, or three times 33,33), „Berechnen“ and „Finalisieren“ stop with a
„Fehler“ naming the unit and the period („Die Miteigentumsanteile der Eigentümer von Einheit 2
ergeben vom 01.01.2026 bis 31.12.2026 zusammen 0,9 statt 1 oder 100.“); add the missing co-owner or
correct the shares and calculate again. The Wirtschaftsplan applies the same rule to the owners of its
first day: „Berechnen“ and „Finalisieren“ on the plan stop with a „Fehler“ naming the unit, the plan
start and the sum („… ergeben zum Planbeginn am 01.01.2026 zusammen 80 statt 1 oder 100. …“) and say
to correct the shares with „Korrigieren“ in the unit's Eigentümer-Historie; „Finalisieren“ calculates
again from the current history, so shares changed after the last „Berechnen“ are caught there too.
A plan that was finalized earlier stays as it is.

A share or start date typed wrongly is corrected in the unit's „Eigentuemer-Historie“: „Korrigieren“
on the row opens „Eintrag korrigieren“ with the start date and the „MEA-Anteil“; saving marks the old
entry „Korrigiert“ (with who corrected it and when) and adds the corrected entry in its place.
The old entry stays visible but no calculation reads it any more; settlements and plans that are
already finalized stay as they are, drafts and later calculations use the corrected values. An
entry that was already corrected cannot be corrected again, correct the new entry instead; a
correction that would overlap another entry of the same person is refused with the reason.

If a finalized Wirtschaftsplan covers the period, a corrected start can make the Hausgeld Soll refuse the
unit („Die Eigentumszeiträume oder Miteigentumsanteile von Einheit 2 passen nicht zum finalisierten
Wirtschaftsplan 2026 …“): the plan froze its owners at its start. Open the plan under „Wirtschaftspläne“,
„Korrektur erstellen“ and finalize the correction plan; it is computed from the corrected history, and the
Hausgeld Soll answers again. A co-owner who joins after the plan start stays refused whatever the plan
says: correct such a start to the plan start. The correction dialog gives the same hint before saving. Thirds stored with six decimals (0,333333) are accepted. The
Hausgeld Soll and special levies divide by the same ratio but do not stop on a wrong sum, so they can
already exist while the annual settlement is refused. Rules and values:
[M4 §4.3.3](../m4-weg-abrechnung.md#433-co-owner-shares-are-ratios-inside-their-unit-404).

## Special levies backend (Sonderumlagen)

`POST /api/special-levies` creates a one-time special levy with `property_id`,
explicit `accounting_scope_id`, positive `amount` accurate to the cent, `due_date`, and
required `resolution_id`. The resolution must belong to the same organization and
property and have the recorded result `ANGENOMMEN`; votes and quorum are not
recalculated. Missing/null references return 422 with
“Eine Sonderumlage braucht einen Beschluss.” and create no obligation or audit row.
Complete WEG property scopes are supported;
incomplete MEA/ownership bases and partial scopes are explicitly rejected.
The distribution uses the owners on the due date, for co-ownership their
positive shares within the unit, and the existing cent-exact remainder distribution.
10,000 EUR on MEA 300/300/400 yields 3,000/3,000/4,000 EUR.

Amounts, due dates, scopes and shares remain immutable after creation. Historical
unlinked levies remain usable in accounts and dunning and show “ohne Beschlussbezug”.
`PUT /api/special-levies/{id}/resolution` accepts an adopted same-property resolution
once. Retrying the same reference returns the persisted levy; replacement returns
409 and clearing is refused. The link does not recompute any allocation.
Native organization archives and operator restores retain their historical INSERT
semantics. Import selects each archived levy’s explicit scope for its native
INSERT checks and then restores the prior context; ordinary service/API creation
always requires an adopted reference. Later MEA changes
do not recalculate old levies; a later change of the ownership history does move the unit's amount
(see below). `GET /api/special-levies` reads with an optional
property/scope filter under actual scope and source permissions; incomplete
access is not output as a complete empty list. A levy is read only in its own scope, so once an
organization holds one, a read without an accounting scope is refused with the German 409
of classified data („Wählen Sie einen eindeutigen Abrechnungskreis …“) wherever it could
include a levy: the organization-wide overviews (the Fristen and their context, the Mahnlauf,
the open items of all leases), every account or new notice of one tenant lease or one owner
that touches a property with classified finance, the levy's own property included, and the
annual overview or the bank matching of a property that has classified finance or an accounting
scope of its own.
`GET /api/fristen/context` answers `requires_accounting_scope: true`, so the Mahnlauf offers the
scope selector. The account or a new notice of one tenant lease or one owner whose properties
have no classified finance (no scope-bound inputs, no levy), such as a property without an
accounting scope, is answered as before the levy (#739). A lease belongs to the property of its
unit; an owner is judged by every property of its uncorrected ownership entries, payments, plan
lines and levy allocations. The read of one property is answered the same way when the property
has no classified finance and no accounting scope, so there is nothing to select: the annual
overview of such a property (`GET /api/reports/property-year` and the readers that share it) and
the matching of the bank transactions of its unscoped bank accounts show what they showed before
the levy (#816), and so do its downloads: the two CSV exports of the annual overview
(`GET /api/reports/property-year.csv` and `.../anlage-v.csv`) and the DATEV sources, preview and
file (`GET /api/reports/datev/sources`, `POST /api/reports/datev/preview` and `/datev.csv`) have
the content they had before the levy (#840). So do the SEPA routes of a bank account of such a
property that no accounting scope is bound to (`/api/payments/sepa/...`: the account page, the
creditor, the mandates and their change or revocation, the preview and the collection file): the
property of the account is named, so the direct debits of its tenants can still be prepared (#867).
A property with a scope of its own is still read and exported through that scope, and without it is
refused. The decision is one database function,
`report_scope_source_access(org, sphere, capability, lease, owner, property)` (migrations 0193 and
0199, the sixth argument is optional), next to the three-argument function that the source
policies keep calling. Reads, the two kinds of download and the SEPA routes ask it through
`FinancialScopeService.report_sources_open`, the downloads with the capability `export` and the
property they concern, the SEPA routes with the capability `read` and the property of the bank
account. The downloads of the levy's own property and of a property that has classified finance or
a scope of its own are refused without the scope as the reads are, and so are the SEPA routes of an
account bound to no scope on such a property (404 „Complete rent sources not accessible“); an
account that is bound to a scope is served through that scope, as before. The function still
refuses an unscoped export that names no property, and the same holds for a SEPA call that would
name none, though none exists: every SEPA route names one bank account, and a bank account always
belongs to a property. A caller without access to the levy's scope gets no levy data from any SEPA
route: the page, the preview and the file of an account without a scope hold only that account, its
tenants and their mandates, and the account bound to the levy's scope is not found (404 „Bank
account not accessible“). The levy itself is exported only
through its scope, with export access to it and to its sources, so a caller without that access gets
no levy data: without the export right on the scope the answer is 404 „Accounting scope not
accessible“, and with it but without access to the scope's sources 404 „Complete report sources not
exportable“ for the CSV exports and „Complete financial export sources not accessible“ for DATEV.
Reads with the levy's scope are unchanged. The existing
owner account counts the share once as `SONDERZAHLUNG` in the
due month, even without an economic plan; regularly booked special payments
reduce the open amount. Under `/sonderumlagen`, property and read scope are
selected; write roles explicitly select the creation scope as well as amount
and due date plus “Beschluss *”. The levy links to the meeting date and TOP, using
the frozen register date when available. Its register entry shows
“Sonderumlage 1.200,00 €, fällig 01.07.2026” with a link back to the selected scope.
These summaries use the existing financial/source read grants; reading a register
does not itself disclose levy amounts. The page shows the counting shares with owner and
unit names and uses the existing account/dunning pages. Without an
economic plan, a WEG property and its scope can be explicitly selected there.
The annual settlement counts the levy as an additional Soll of the owner: the levies that fall due
within the settlement period (both days inclusive) reduce the Abrechnungsspitze, their booked
`SONDERZAHLUNG` payments appear as “Sonderumlage gezahlt”, and what is still owed or overpaid joins
the “Zahlbetrag” ([M4 §2.4](../m4-weg-abrechnung.md#24-origin-of-expected-advances-booked-hausgeld-and-the-payment-amount),
ADR-0007 amendment of 2026-11-15, subject to confirmation by a WEG specialist). The costs a levy
financed are distributed as usual; no cost is linked to a levy.
The allocation follows the ownership on the due date. Recording a sale after the levy was created
(`PATCH /api/ownership/{id}` for the seller's end and `POST /api/ownership` for the buyer), moving a
start or end across the due date or correcting a co-owner's share moves the unit's amount of that
levy to the owners of the due date, co-owners by their shares, to the cent, in the same transaction
and with the audit entry `special_levy.allocation_moved`. Allocation rows stay immutable: the old rows
get a marker (who and when, table `special_levy_allocation_supersession`) and stop counting, the levy
list, the owner account, dunning, the Fristen monitor and the annual settlement read the counting rows
only. A levy due before the change stays with the seller; payments already booked stay with the owner
who paid; an issued dunning notice of the seller is not rewritten, and the buyer's first notice for the
levy is level 1. When the sale is recorded in two steps nothing moves until the buyer is added (the
annual settlement refuses the ownership gap meanwhile). An
actor without read and prepare access to the levy's accounting scope cannot record a change that
would move that levy (409 `ownership_levy_access`); a change that moves nothing is accepted. The organization export carries the superseded
rows with their markers and an import restores them. A levy that was already allocated to a
seller before this rule is not rewritten by the upgrade; the annual settlement refuses it
(`weg_levy_owner_mismatch`). Every write to the unit's ownership history runs the move, also a `PATCH`
with an unchanged end date, and moves such a levy when the owners or shares of its due date differ.
A draft owner dunning notice written before a move is refused at issue (409 `dunning_levy_moved`) when
the Soll of one of its levy items differs from what the owner now holds of the levies due that day
(another owner, or another share); levies the notice does not contain are not looked at. Delete the
draft and create the notice again.
Special levies become overdue only after their actual due date (plus grace period);
payments of the same month are applied once to the oldest due date.
`features/special_levy.feature` exercises the ordinary API and browser flow at
1280 and 360 px: 1,200 EUR split by 600/400 MEA yields 720/480 EUR, due 1 July 2026,
with the adopted roof-repair resolution from 15 May under TOP 3. The earlier
`scripts/acceptance/t33_special_levies_browser.cjs` predates mandatory resolution
selection; the feature is the current creation-path check.

## Invitation to the owners' meeting

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

### Dates in the resolution register

Each meeting resolution entered into the Beschluss-Sammlung stores its meeting date
and place as immutable copies. The page and newly generated PDF extracts show
“Datum der Versammlung” first and “Eingetragen am” second. The latter is the actual
registration day; registering an older meeting does not backdate the entry.
Existing entries receive their meeting date during the reviewed 0167 upgrade,
without changing their entry dates, wording, locations, numbering or references.
Stored PDFs remain unchanged. Written and court resolutions remain unsupported.
