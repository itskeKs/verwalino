# Intake stage and proposal view

Migration 0099 projects inspection, exact-event outbox preparation and logical
extraction into the existing batch status contract. A clean inspection stays in
processing while preparation or extraction is pending. Only completed proposals
become available for review; confirmation remains zero. Mixed child outcomes
retain processing/review/failure counts. Original download and inspection retry
rights still come from the existing source checks.

The inbox links to a source view with up to 200 logical children. Selecting a
child fetches its processing state and complete bounded revision history in
parallel, then the retained revision details. The view displays normalized field
proposals, original page numbers, literal quotations, extraction method, confidence
and standalone XML element paths. Repeated/uncertain support stays unlocated.
Prior failed attempts remain available. Nothing in this view confirms a value or
creates a cost entry.

The explicit extraction retry keeps its exact key, expected fence and reason
across uncertain responses and refresh. A later attempt fence resets the form
for a new explicit request. Aborted reads and late replies from an unmounted
child cannot replace the selected child's state. A denied read clears the source
selection and proposal data; an unavailable fresh read hides cached field data
and retry actions. Polling is bounded and stops when work is terminal or a read
fails.

## Verification

The backend stage journey and intake suite passed 18 checks after updating three
older inspection-only assertions to expect processing until the later stages
complete. Five migration/backfill/downgrade probes passed with the new wrapper,
and the fake-Docker head-0099 probe passed. Independent review found no concrete
blocker in the state projection or UI boundaries.

Eleven inbox/source UI checks passed initially. The source suite then passed all
six checks after adding a delayed retry-denial/child-switch regression. The
frontend build, lint, final test-file lint and TypeScript check passed. The
existing bundle-size warning remains (about 699 kB minified).

Rendered QA used local Vite/API servers at `127.0.0.1:13000`/`:18000`, the
disposable PostgreSQL database, a synthetic invoice and actual local extraction.
The Browser plugin was unavailable; existing bundled Playwright 1.62.1 and cached
Chromium were used without installation. The first dev-server start used the
wrong working directory and failed Tailwind configuration lookup; restarting
from the frontend directory repaired the harness.

The flow was inbox upload → injected clean inspection → unavailable extraction
→ keyboard retry → actual extraction → retained current/prior history → simulated
404 clearing. Page identity, meaningful content, absence of a framework overlay,
console health before the intentional fault, keyboard interaction and mobile
width checks passed at 1440×1000 and 390×844. Screenshots were visually inspected:
`/tmp/hv-intake-source-desktop.png` and `/tmp/hv-intake-source-mobile.png`.
The test servers were stopped afterward.

The clean inspection was injected because real ClamAV qualification is still
unavailable locally. Source-access revocation was exercised against the real
database in the preceding worker milestone; the browser check injected a 404
only to verify rendering cleanup. Full B2 runtime qualification and the later
assignment/financial-review journeys remain outstanding.
