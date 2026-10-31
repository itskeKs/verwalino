# Meters and readings

Meter APIs, reading history, and consumption inputs for operating cost statements.

## Meter API

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
