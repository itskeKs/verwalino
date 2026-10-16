# Projected access coverage, checker version 2

Migration 0087 adds a materialized combined-authority preview to access preparation.
A report no longer infers strict access from PREPARATION visibility or separate
users' grants. Each audience entry evaluates one current member against the
relevant financial scope, complete property sources, contacts, original custody,
parent relations and exact referenced documents.

## Behavior and boundaries

The existing grant audience is retained. `references.strict_audience` adds only
user UUIDs and fixed capability names. The UI distinguishes these projected rights
from individual grants and describes missing combined readers in German. Source
content, names, money, storage locations and private manifest hashes are not added
to public references. Full records are loaded only inside a private, allowlisted
security-definer evaluator.

Six remaining original/import, meeting and financial-classification predicates
share their checked implementation with private actor/mode helpers. Existing
signatures, OIDs, ownership, ACLs and function settings remain intact. Appending
private arguments to the import-row helper preserves its existing defaulted
creation argument; omitted evaluation context fails closed. An applied import
still converts preparation into an approval requirement.

The combined evaluator deliberately includes all expected financial report
contacts and complete property sources. Scope/classification, parent/property
coherence and exact invoice references must agree. An authorized parent does not
supply authority over an unrelated original. Active invoice attempts are also
checked against their recorded initiator and requested financial scope. Unknown,
malformed or unsupported resources do not produce readers. Organizational mandate
metadata reports its read gate only; no mandate mutation capability is inferred.

Capabilities describe combined authorization, not whether a business operation
can currently complete. Immutable records, financial completeness, provider
availability and lifecycle conditions still apply. Quarantine, provenance,
processing and unsupported-adapter findings remain visible. The report explicitly
retains `DEPLOYMENT_POLICY_ATTESTATION_REQUIRED`: API-supplied budget configuration
is not yet trusted deployment evidence. Activation remains unavailable and the
activation readiness function remains false.

## Evidence lifetime

Version 1 manifests and receipts remain byte-for-byte materialized. New reports
use checker version 2; old reports remain readable and replays return their saved
results. New review/proposal actions against obsolete evidence require recapture.
The manifest also hashes the independent financial pause history, so changing it
invalidates a current report even when existing grants remain unchanged.
Organization-wide capture rejects a selected financial scope. Projection never
changes the current actor or security mode.

Downgrade refuses retained version 2 reports. With no such reports it restores
version 1 generation and exact original predicate bodies. The guarded disposable
roundtrip preserves historical version 1 data and original function identities.

## Acceptance evidence

- Three new integration checks first failed, then passed after implementation.
- The expanded API/DB run passed 58 checks; two test-fixture failures were corrected
  (SQLAlchemy JSON literal binding and the added opaque reference field).
- The focused follow-up passed 15 checks, covering combined readers, stale financial
  pause evidence, missing parents, matching/mismatched parent properties, meetings
  in all three modes, exact migration/ACL preservation, retained snapshots,
  own/other-actor/missing/malformed invoice sources and paused effect capabilities.
- A separate real applied-import/source test passed, including prepare-versus-approve
  semantics and original-source private projection with invalid-mode denial.
- Nine frontend tests, lint and production build passed. The existing approximately
  662 kB bundle warning remains. The fake-Docker deployment regression probe passed.
- A local Playwright journey passed capture response-loss replay, checker version 2,
  denied-to-authorized preview, immutable old report, reload and 390 px mobile view.
  No page errors or horizontal overflow occurred; desktop/mobile screenshots were
  inspected. The browser used synthetic local data only.
- Independent read-only review found the parent/property audience mismatch; the
  implementation and paired positive/negative fixture now close it. No activation
  or complete B1 claim follows from this increment.

Local logs: `/tmp/hv-coverage-v2-{red,first,expanded,repaired,import,ui,lint,build,deploy,browser}.log`.
Browser harness: `/tmp/hv-coverage-v2-browser.mjs`. Screenshots:
`/tmp/hv-coverage-v2-desktop.png` and `/tmp/hv-coverage-v2-mobile.png`.
