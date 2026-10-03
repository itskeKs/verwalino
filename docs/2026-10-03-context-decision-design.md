# Typed manual context decisions

Implementation boundary following invoice field reviews (0103). This note records the implemented context comparison boundary; financial adoption remains pending.

## Purpose

A person may explicitly compare a document with its intended accounting context. Existing property data has internal IDs and address/name fields but no verified customer-reference mapping. Therefore this operation records a `manual_document_comparison` human assertion. It must not be described as automatic matching or machine-verified source evidence.

One immutable decision pins the current assignment proposal, that proposal's exact reviewed partition and extraction/hash, and the latest human invoice-field review belonging to it. It stores a bounded human quotation, claimed locator and comparison note, plus the whitelisted destination/property snapshot the person reviewed. The reviewed service period must lie within the accounting scope, with existing inclusive validity semantics. Initially only supported WEG/MIET contexts qualify. Unsupported SEV remains blocked.

A visual locator must name a page inside the selected logical range. A standalone XML locator must identify that same logical original; a human-entered path is a claimed locator, not a parser-validated match. Require explicit acknowledgement of the selected source and destination comparison. A supplier's address is not automatically a property address, and no fuzzy match or new global mapping is created.

## Authorization and snapshot stability

Preview, command and retained snapshot reads require current original access, target scope access and property-master access. Scope prepare does not authorize copied property details by itself. Commands additionally require original/target prepare, actual `app_rw`, unpaused source security, immutable idempotency and optimistic predecessor checks. Historical replay checks current authority before returning its original receipt.

Recompute an explicit snapshot of necessary property identity/address and scope definition, rather than serializing arbitrary whole rows. Compare its digest with the preview under locks that protect those rows. Existing ACL/financial advisory locks alone are insufficient for mutable property address edits. Use bounded `FOR SHARE NOWAIT` row locking after the existing advisory lock order and translate lock contention into a retryable conflict; this avoids waiting behind a row writer that may itself be waiting for an advisory lock. Test both possible property-edit/decision transaction orders. Scope unit membership is already frozen once grants exist; include only necessary scope definition/identity rather than personal owner data. Any later adoption rechecks snapshot applicability after subsequent edits.

## Exact exception disposition

Keep a separate immutable context decision. A typed `context_reviewed` event may point to the exact `scope_hint_conflict` dependency and decision, with the case's exact expected event UUID. Decision and event insert atomically. Do not turn the case into a blanket resolved flag: a case retains multiple proposal dependencies and a disposition of one must not resolve siblings.

Advisory actions preserve the current typed disposition reference. A new blocked proposal adds its dependency, advances the case version, opens workflow attention and clears the active aggregate disposition reference; historical decisions remain readable under their complete audience. Workflow reopening changes attention only and must be labelled accordingly. Applicable disposition is derived for the selected proposal and current review/snapshot; superseded cases must not globally block unrelated successors.

No assignment row is rewritten. No cost is created or confirmed, source audience expanded, original released, mapping learned, duplicate cleared or credit relationship established. Later allocation/adoption evaluates the exact decision, proposal, review, current metadata and remaining gates.

## Decisive qualification

- Two proposals sharing one case: only the exact dependency is dispositioned.
- Concurrent property edit and submission: stable accepted snapshot or explicit conflict; no stale silent acceptance or deadlock.
- Property permission revoked while scope permission remains: no retained-address disclosure or successful replay.
- New field-review/partition/extraction/proposal basis: stale new command rejected; authorized historical replay unchanged.
- New dependency after a decision: attention opens; earlier assertions survive without applying to the new proposal.
- Missing/wrong locators, changed snapshots, unsupported scope and invalid periods rejected.
- Actual-role boundary, current original and destination revocation, pause, rollback, immutable composite provenance, coverage invalidation, and guarded downgrade.

Independent design review recommended this separate decision plus exact-dependency event boundary and identified property-audience and master-data locking requirements. The backend implementation is recorded below.

## Implemented backend boundary (0104)

The ledger, preview/detail/history APIs and atomic context event are implemented. Supported destinations currently require `whole_property=true` and type WEG/MIET. The exact immutable field review carries the source hash, extraction and partition provenance; the context ledger references that review with a composite proposal/source/scope foreign key. Snapshot reads require current property-master read permission in addition to original and scope access. Cases containing a retained context decision also require that complete audience.

Property mutation additionally has an existing **row-level** financial-inputs advisory-lock trigger. An edit that completed its trigger holds the financial lock: a context command waits, then compares the committed snapshot. The inverse ordering can leave a property writer holding its row while waiting for that advisory lock; the command's `FOR SHARE NOWAIT` rejects that inversion and its transaction rolls back. Tests exercise both normal orders and this inversion, observing actual PostgreSQL wait state.

The command retains a claimed page/XML locator, quotation and comparison note. It neither verifies that quotation against a parser nor recognizes property identifiers automatically. Later snapshot changes do not rewrite old receipts. Downstream applicability and financial adoption remain pending.

### Backend qualification

- Disposable PostgreSQL 16, actual `app_rw` API commands: nine context tests pass (`hv-context-preview`), including exact shared-case dependency, stale snapshot/review, optimistic concurrency, replay, period rejection, property-only and target revocation, pause, background-role rejection, complete transaction rollback and injected event-insert failure.
- PostgreSQL lock tests observe advisory waiters and qualify editor-first, decision-first and row/advisory inversion without relying on elapsed time alone (`hv-context-locks`, eight tests before the ninth period/concurrency test was added).
- Related 0101–0103 tests passed in `hv-context-qualification` (25 passes; the sole failure was a test's incorrect immediate-conflict expectation). Older intake/registry/partition/outbox migration and runtime checks passed in `hv-context-final` (75 passes; the sole failure was the new inversion test's UUID/text parameter type, subsequently corrected and passed).
- Empty downgrade/upgrade preserves manifest function identity; retained decisions refuse downgrade; complete provenance foreign keys and coverage digest changes are qualified.
- Fake-Docker deploy/verification head gate passes at 0104 (`hv-context-deploy`). No deployment occurred.
- Independent static review found no production blocker. The reviewer requested the additional audience, lock-order and atomic rollback checks above.

## Comparison UI qualification

The source evidence page now includes a context comparison panel below invoice field review. It shows the retained property/scope snapshot, requests an explicit page or claimed XML path, quotation, comparison note and acknowledgement, and labels saved records as human assertions. A stale snapshot/review/case version requires explicit replacement of draft inputs. Unknown POST outcomes retain the exact body, key and predecessor for replay across parent refreshes.

An independent UI review found a cached-draft audience gap when parent context disappeared or a readable successor replaced an inaccessible predecessor. The repair requires a successful preview for the draft's own proposal before showing its copied facts or enabling replay. Missing parent context retains the request privately; property-only denial clears the local draft without falsely denying the original. The reviewer found no remaining actionable issue in the repair.

- Forty related component/page tests pass (`hv-context-ui-verified`), including both audience regression cases, source-page integration, unknown replay and stale input replacement.
- Production build and lint pass (`hv-context-ui-build-final`, `hv-context-ui-lint-final`); the existing large main-chunk warning remains (approximately 746 KB).
- Actual local browser/API test passes after the audience repair (`hv-context-browser-final`): real extraction with synthetic clean inspection, keyboard submission, lost committed response and exact replay with one retained record, reload acknowledgement reset, changed property snapshot conflict, unchanged cost count, mobile width and simulated property-only denial clearing. Desktop/mobile screenshots were inspected.
- This evidence does not qualify production scanning or financial adoption. Task-started browser/API/Vite processes were stopped after qualification.
