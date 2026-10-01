# Bounded master-data selectors

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
