# Homeowners' associations (WEG)

Special levies and PDF invitations to owners' meetings.

## Special levies backend (Sonderumlagen)

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
