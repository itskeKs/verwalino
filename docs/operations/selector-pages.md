# Master data and bounded selectors

Master-data editing, ending, deletion, and paginated selection.

## Bounded master-data selectors

`GET /api/properties/page`, `/api/persons/page`, `/api/users/page` and
`/api/units/page` return `{items, next_cursor}`. `limit` defaults to 50 and must be
1–200. `q` is a literal, case-insensitive search string (maximum 100 characters);
SQL wildcard characters remain literal. Units optionally filter by `property_id`.
Rows sort by normalized display key and UUID, so equal names are not skipped.

Continuation cursors are signed, expire after 24 hours and are bound to the
organization, actor, endpoint, search and filter. Invalid/reused cursors return
422. Each request reevaluates current database visibility; a cursor is not an
access grant or a frozen database snapshot. Existing list endpoints keep their
response shape while their consumers migrate incrementally.

Property/person/user pages use the new contracts. The property selector on bank
accounts searches a bounded page and preserves an existing selection by fetching
that one property's detail. Late responses from a previous query are discarded.
Bank-account display names use a tenant-filtered join instead of loading every
property. Mobile tables scroll inside their own container.

Verification: four PostgreSQL API paging tests passed, including 55 equal names,
50→5 continuation, bounds, tampering, query/organization mismatch and empty
foreign results. Frontend hook tests cover continuation deduplication and stale
responses; affected frontend modules passed 32 tests. Lint/build passed (the
existing approximately 569 kB bundle warning remains). Independent review found
no additional paging issue. Synthetic browser checks passed for 50→55 unique
rows, user list, empty contact search, selected property retention and 390 px
mobile layout; see `docs/acceptance/evidence/b1-selectors-browser.json`.

Reproduce with the disposable pytest seed, then run
`scripts/acceptance/b1_selectors_fixture.py` using the required disposable database
settings. This fixture refuses other database targets. Run the API on localhost
18000 and Vite from the frontend directory on localhost 13000 with its API proxy
pointing to 18000. Run `scripts/acceptance/b1_selectors_browser.cjs` with
`HV_PLAYWRIGHT_PACKAGE` and `HV_CHROMIUM_EXECUTABLE` pointing to existing installed
runtimes. Screenshots stay in `/tmp/hv-b1-acceptance` by default. No installation,
deployment or external account is required.

## Deleting unused erroneous entries

T56 introduced this workflow.

In property, unit, and person details as well as on a lease, there is an explicit delete dialog
for users with write permission. Without incoming references
and with current source-prepare permission, exactly the chosen record is
removed; linked data is never deleted along with it. For references, 409 states
the reason, for example a lease or a rent history. Source approvals,
creation/import receipts, audit, and saved financial snapshots
are also protected references. Existing RLS/privacy locks remain authoritative.
A person with a tenant/owner/vendor profile is already referenced.
The [UI-only acceptance](../../DEPLOY.md#unused-master-deletion-t56-local-head-0137) verifies cancel,
actual deletion of an empty unit, and the visible lease reason of a
used unit after desktop/phone reload; original leases remain identical.

## Editing and ending master data

T55 introduced this workflow.

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
The [UI-only acceptance](../../DEPLOY.md#editable-master-metadata-t55-local-head-0136) verifies the real
rename, 1984 / 1.234,56 m² and unchanged sources after desktop/phone reload.
