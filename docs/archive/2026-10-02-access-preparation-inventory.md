# Aggregate access preparation inventory

Status: implemented and verified; baseline 32878df, migration 0076.

Provide administrators with a current, tenant-scoped overview of structural
preparation gaps before legacy remediation and reviewed activation. A bounded
aggregate response contains counts only: property/contact domains and current
readers, original review records, unclassified financial resources, legacy dunning
contexts and mandate originals. It contains no source names, IDs, amounts, quotes,
filenames or personal data. Definer visibility avoids falsely reporting zero gaps
when ordinary source RLS hides rows. Live administrator authority is rechecked under
the shared ACL lock both in the HTTP transaction and in the database helper.

The counts describe existing records; receipt presence is not a new custody proof
and current reader presence is not approved team coverage. The overview does not
create grants, classify sources or enable enforcement. Activation remains unavailable.
The UI links to existing explicit setup/review journeys and explains unsupported
legacy remediation. Failed refreshes hide stale counts and remain retryable.

Acceptance: exact tenant-only counts for synthetic hidden sources; no payload leak;
role demotion/deactivation and direct SQL denial; grant expiry/revocation reflected;
UI accessible read-only overview, failed-refresh recovery and desktop/mobile browser
checks. Existing role/source restrictions and migration rollback remain intact.


Verification: the initial two HTTP tests failed before the endpoint existed.
The first expanded run passed 16 backend/RLS checks; final inventory checks passed
11 cases plus the separately rerun representative legacy/roundtrip case. Three
UI tests, frontend lint/build, shell syntax and the simulated deployment check
passed. The build retains the existing bundle-size warning (632 kB). Chromium
verified desktop and 390 px layouts, live refresh, a simulated 503 hiding old
counts, retry recovery and clerk UI/API denial. Screenshots were inspected;
mobile capture waits for the existing navigation transition to finish. The
available Playwright runtime was used because the browser-testing plugin was
unavailable. No runtime page errors. Test servers were stopped afterward.

Representative fixtures include bound/unbound unknown originals, an issued legacy
notice, an unclassified mandate original and an unbound bank/payment. Setup-only
failures (closed fixture transaction, guarded scoped bank and invalid payment enum)
were corrected without production changes. Snapshot hashes survive a reversible
0076 downgrade/upgrade; activation remains false. Independent review reported no
findings. Inventory categories are advisory and may overlap.

Operator entry: Users → Bestand und Freigaben vorbereiten. Refresh obtains a new
live snapshot. Follow the explicit domain/source/scope setup links; do not interpret
zero counts as permission to activate. Legacy remediation, approved team coverage,
role proposals and reviewed activation remain subsequent B1 work.
