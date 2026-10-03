# Trusted computation evidence for correction follow-ups

## Required outcome

An allocation correction records open downstream effects. Completion must distinguish a draft that was actually recomputed with current inputs from a manually edited draft, and a finalized replacement from a pointer to an unfinished replacement. Historical financial outputs and completion receipts remain immutable; current applicability is derived separately.

The existing computation audit events retain totals, while draft snapshot and line tables are writable by the application role. A timestamp, stored hash, new coverage declaration or `superseded_by_id` therefore cannot independently establish the required computation. Do not backfill trusted receipts from those historical records.

## Computation receipt boundary

Add a private immutable trusted computation attestation and a consumed receipt with typed organization/parent/coverage references. The trusted `app_review` connection attests only after the server has successfully executed the supported engine and written its exact snapshot and relational lines in the outer transaction. Bind the attestation to organization, actor, parent, operation, engine version, complete persisted-state digest, source fingerprint, caller backend PID/transaction and a short expiry. The application role cannot mint it or insert a receipt directly.

The outer runtime command reauthorizes the complete financial context under the existing ACL → financial-input → parent-row lock order. It consumes the exact proof once and seals the stored snapshot, relational lines, coverage and parent context. A deferred seal rejects changing the computed output or its inputs later in the same transaction. The separate trusted registrar must not wait for the caller's ACL/financial/parent locks or reference uncommitted parent/coverage rows; typed foreign keys belong on the consumed outer-transaction receipt.

Use one versioned database fingerprint for preparation, consumption and later validity. Include the source rows used by the supported engines, exact parent/linked-plan context, coverage evidence, category/policy identity and current allocation heads. Privately hashing additional property inputs is conservative; it must not disclose inaccessible rows. Bound oversized state and validate authorization before exposing any receipt. The existing coverage policy remains mandatory and retains its own current-source revision check.

Instrument BK, WEG annual settlement, asset report and economic-plan compute/finalize paths. A preview creates no receipt. Rollback must leave no consumed receipt. Old records without proof stay readable but cannot complete a correction impact until a supported new computation or finalized replacement exists.

## Subsequent impact completion

For an originally draft impact, require a valid receipt for the exact current parent produced after the correction. For an originally finalized impact, follow a bounded cycle-checked successor chain to a current finalized successor, validating property, scope, run type, period and recipient/context identity. A draft successor is not completion.

Where an old coverage declaration referenced an ancestor source, require explicit source-review acknowledgment and the current published correction source in the exact declaration consumed by the trusted computation. Plans and asset reports contain manually reviewed inputs: renewed processing proves no automatic monetary adjustment.

Store immutable completion receipts and separately evaluate their current validity. New allocation heads, source inputs, coverage, parent/successor state, output/lines or source-review basis invalidate applicability. Reauthorization failure hides evidence without erasing history. The UI must show open, completed and subsequently changed states without a generic manual dismissal.

## Qualification before acceptance

Exercise actual engine compute/finalize for all four types; raw runtime proof/receipt forgery; wrong actor, tenant, parent, operation, PID/transaction and expired proof; changed snapshot/lines/context/coverage/input after attestation; rollback and concurrent source/grant changes; immutable evidence and guarded migration roundtrip. Follow-up qualification additionally covers stale draft computation, unfinished/final successors, source evidence renewal, changed current heads and exact historical replay.

## Accepted computation foundation (migration 0115)

Implemented the private attestation and consumed receipt for all eight compute/finalize paths. Capture the input basis before engine and display-name reads, locking the bounded source rows and exact mandate with `FOR SHARE NOWAIT`; compare that basis after engine persistence. The retained state covers parent, relational lines, exact coverage, complete property inputs, related contact identities, catalog, organization and linked plan. Preview does not mint evidence.

Independent review identified a post-read contact/catalog race and an ambiguous parent comparison when settlement and plan UUIDs collide. Both are repaired and have decisive regression cases. A further root check found that `SET CONSTRAINTS ALL IMMEDIATE` could force an insertion-only seal before later mutations. Source/output mutation triggers now recheck each transaction's latest exact typed receipt. Follow-up independent static review found no remaining concrete gap in these repairs; the reviewer did not execute tests.

Qualification receipts:

- `/tmp/hv-computation-regression.log`: 90 passed, comprising 23 new evidence cases and existing financial-input policy, rental and WEG engine flows. New cases cover all four actual engines, preview exclusion, snapshot/line/source/coverage tampering, rollback, exact runtime roles, wrong actor/tenant/parent/operation/PID/transaction, expiry, real contact ABA serialization, busy source refusal, typed-parent collision, early constraint forcing, immutable retention, manifest inclusion and migration roundtrip.
- `/tmp/hv-computation-migrations.log`: 18 predecessor rehearsals passed; one failed because its disposable cleanup retained correction records from an earlier case. The repaired rehearsal then passed alongside the correction retention rehearsal in `/tmp/hv-computation-scope-regression.log`.
- `/tmp/hv-computation-scope-regression.log`: 78 passed across scoped financial creation, boundaries, source input/disclosure and historical report access. Four failures shared an obsolete cleanup that deleted a now-registered original; the test now isolates that retained original on its own property. `/tmp/hv-computation-source-repair.log`: all 16 source-input cases passed, including those four.
- `/tmp/hv-head0115-probe.log`: fake-Docker production script probe passed with all head checks at 0115. No deployment or external system change occurred. No frontend behavior changed in this milestone.

The access preparation manifest includes both private evidence tables. Downgrade refuses any retained proof or receipt and preserves function identity on an empty roundtrip. Historical financial outputs are not backfilled into trusted evidence. This accepts the computation foundation only: correction-impact completion and the remaining B3/M-B scope are still outstanding.
