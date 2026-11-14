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

## Co-owner shares in the annual settlement

Enter the „MEA-Anteil“ of every co-owner of a unit in the unit's Eigentumshistorie as a fraction
(0,5 / 0,5) or as percent (50 / 50); the annual settlement divides the unit's share among the
owners active on the same days by these values as ratios, span by span when an owner joins or
leaves during the year. A sole owner needs no share. If the active shares of a unit add up to
neither 1 nor 100 (0,6 and 0,3, or three times 33,33), „Berechnen“ and „Finalisieren“ stop with a
„Fehler“ naming the unit and the period („Die Miteigentumsanteile der Eigentümer von Einheit 2
ergeben vom 01.01.2026 bis 31.12.2026 zusammen 0,9 statt 1 oder 100.“); add the missing co-owner or
correct the shares and calculate again. Thirds stored with six decimals (0,333333) are accepted. The
economic plan, the Hausgeld Soll and special levies divide by the same ratio but do not stop on a
wrong sum, so they can already exist while the annual settlement is refused. Rules and values:
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
always requires an adopted reference. Later MEA/ownership changes
do not recalculate old levies. `GET /api/special-levies` reads with an optional
property/scope filter under actual scope and source permissions; incomplete
access is not output as a complete empty list. The existing
owner account counts the share once as `SONDERZAHLUNG` in the
due month, even without an economic plan; regularly booked special payments
reduce the open amount. Under `/sonderumlagen`, property and read scope are
selected; write roles explicitly select the creation scope as well as amount
and due date plus “Beschluss *”. The levy links to the meeting date and TOP, using
the frozen register date when available. Its register entry shows
“Sonderumlage 1.200,00 €, fällig 01.07.2026” with a link back to the selected scope.
These summaries use the existing financial/source read grants; reading a register
does not itself disclose levy amounts. The page shows the fixed shares with owner and
unit names and uses the existing account/dunning pages. Without an
economic plan, a WEG property and its scope can be explicitly selected there.
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
