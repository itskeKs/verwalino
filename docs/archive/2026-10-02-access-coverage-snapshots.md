# Reviewed access coverage snapshots

Baseline: 28314ed. Accepted local migration 0083. This milestone records reviewable
evidence and remediation proposals; it does not enable source activation.

An administrator can create an immutable, versioned snapshot of the organization's
current audiences and retained sources. Snapshot items contain typed IDs, exact
scope/domain/parent references, current effective grants and bounded finding codes.
They exclude names, addresses, filenames, locators, amounts, extracted values and
unrestricted source reasons. Private structural fingerprints bind the snapshot to
its underlying evidence without publishing per-source content hashes.

Coverage has three distinct meanings: an explicit audience exists; provenance or
retention disposition has been reviewed; and an operation has a complete usable
input set. A quarantined original can satisfy disposition review while still
blocking calculations or knowledge answers. One live reader never proves the
intended team's prepare/approve/export authority. Historical unknown custody stays
unknown. Proposed setup actions reuse existing explicit workflows and grant no
authority by themselves.

The manifest includes properties and contacts, their separate domains/grants,
accounting scopes and five typed financial bindings, invoice/mandate/property
originals, imports and derived relationships, meeting/history topology, dunning
context/dispositions, current membership and reviewed knowledge-budget policy.
Private fingerprints also cover structural graph fields and retained admission
evidence. Dependent histories, recipients, processing and copied results expose
their exact parent IDs with explicit unresolved combined-authority review findings;
the first checker does not claim to compute their complete projected strict access.
Successful PREPARATION compatibility access is never projected strict readiness.
Bound proof failures propagate to imports and property-derived rows. This is a
structural review manifest, not the complete readiness predicate for activation.

Capture takes the exclusive organization ACL lock before financial-input locks,
rechecks the current administrator, and materializes the manifest once. Pages use
stable typed-ID order, actor/snapshot-bound signed cursors and 50/200 limits.
Capture and review use actor-scoped UUID idempotency keys; a replay preserves the
original result, and changed request content conflicts. A review accepts the exact
snapshot digest and rechecks its evidence; stale grants, expiry, topology or policy
require a new snapshot. Review receipts are append-only and cannot manufacture
source admission, quarantine dispositions or team grants. Acknowledgment records
inspection, not acceptance of proposed grants or resolution of individual findings.
The supplied policy expectation is API configuration data, not a database-attested
deployment configuration; activation must independently establish that correspondence.

The next activation milestone must independently recompute complete readiness,
validate deployment/budget correspondence, and transition/audit under the same
exclusive boundary. It must cover new-organization bootstrap and strict
pause/resume without restoring compatibility. Until then, both the internal
readiness predicate and the API's activation availability remain false.

Acceptance: exact IDs and grants without protected content; unknown and quarantined
sources distinguished; proof validity rather than receipt presence; role/tenant
denial through actual runtime roles; deterministic materialized pagination;
response-loss replay, stale evidence and expiry conflict; immutable private
evidence; concurrent capture/revocation; migration retention and UI recovery.

## Verification and remaining scope

Two initial endpoint tests failed before implementation. The expanded coverage/RLS
suite passed 14 cases. The related source/classification/quarantine/audit run passed
81 cases and exposed one older audit fixture that still expected an unclassified
financial draft to remain visible in strict mode. It now explicitly classifies
the financial context and contact audiences; the corrected focused run passed
21 cases. The final 29 coverage/audit/RLS cases passed after the privacy repairs.

Independent review found a public projection based on `_id` column suffixes also
included protected vendor tax identifiers. Explicit per-family UUID/FK allowlists
replace that projection. A hidden vendor with tax/VAT/note sentinels is checked
through every page. Worker lease tokens and request keys are excluded. Malformed
legacy JSON reference arrays expose only UUID strings and an explicit invalid-data
finding. The reviewer confirmed the repair without rerunning the shared DB tests.

Six component/page tests passed. Chromium verified 57 synthetic entries, default
50-item pagination, an acknowledgment gated on loading all items, exact capture
and review replay after aborting committed HTTP responses, receipt persistence on
reload, failed-page clearing/recovery and 390px mobile layout. Desktop/mobile
screenshots were inspected. No page errors were observed. The harness initially
started Vite from the wrong directory; restarting from `frontend` restored its
Tailwind configuration, with no production change required.

Frontend lint/build and fake-Docker deployment/worker cleanup checks passed; the
existing bundle-size warning remains (approximately 652 kB). No deployment occurred.
The API/Vite validation processes were stopped after the browser run.

This milestone supplies durable structural review evidence and links to existing
setup workflows. Explicit grant-change proposals/acceptance, complete strict
combined-authority readiness, authenticated deployment-policy correspondence,
new-organization bootstrap and atomic activation/pause/resume remain outstanding.
B1 and the full M-A–M-G plan are not complete.
