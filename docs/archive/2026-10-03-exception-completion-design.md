# Evidence-bound exception completion

## Purpose and boundaries

The supported 0109 adapter can confirm costs and publish one exact original, but exception events remain permanently unresolved. Add an explicit completion operation backed by existing immutable financial outcomes. Completion creates no costs, source release, replacement, tax decision or correction. Unsupported transitions stay open with a generic reason.

Use an immutable completion receipt and one private evaluator for both eligibility and current validity. The evaluator must be shared by list filtering, detail, preview and the command. A saved completion is historical evidence; its applicability can become stale after new relevant evidence. Mutable category labels, category activity and property labels do not invalidate an already confirmed financial result. Historical category/context snapshots remain pinned. Revoked authority changes access, not historical finance.

## Complete evidence and dispositions

Seed evaluation with every retained case dependency and source. Follow connected matching current review heads and retained adopted identities, together with same-byte raw intake receipts, including originals not yet in the registry. Bound the component and fail closed when its supported size is exceeded. Never infer uniqueness from an empty visible list. Unknown, inaccessible, unprocessed, replacement-intent, contradictory or unsupported cross-scope evidence blocks completion; private IDs/counts remain private.

Every retained dependency receives a typed disposition. A scope-hint conflict must be fulfilled by the exact adopted context, or explicitly superseded by a named later context and its adoption on the same supported source/scope. Unsupported scope cases cannot be completed this way. Duplicate dispositions operate on the complete connected component: every distinct document has its own exact confirmed allocation; each byte-identical component has exactly one consumed allocation and explicit coverage for every other copy, backed by exact current same-document comparisons. Required distinct-document comparisons pin exact current reviews. Obsolete dependencies are retained and explicitly superseded by named current review/proposal evidence, never omitted. Ambiguous historical transitions remain blocked.

The private canonical basis pins all dependency IDs/dispositions, selected current partition/extraction/proposal/field/context/allocation/treatment heads, comparisons, relevant raw receipt identities/status and published allocation-set IDs. Include a policy version. Exclude resolution events and advisory workflow actions from the evidence digest to prevent self-invalidation. Completion members/provenance use composite FKs and sealed cardinality where relational links are stored; private basis JSON is not generally SELECT-granted.

## Command, authority and state

Preview shows the complete readable evidence/outcome summary, a digest and current event predecessor. The user provides an explicit completion acknowledgment and reason. The actual app_rw command holds lock_intake, checks full-case work authority and all retained receipt audiences before exact historical replay, then validates the current event, evidence digest and dispositions. Append completion receipt and its typed case event atomically. Changed body/key conflicts; exact committed retry remains available after evidence changes subject to current authority.

Claim/release are advisory and preserve a currently applicable completion. Explicit reopen overrides applicability until a new reviewed completion. New source/dependency evidence cannot accidentally restore an old completion. Existing source-driven evidence events remain useful, but an invalidation-trigger network is not the authority: a new raw receipt can invalidate completion without generating a case event. Effective workflow is completed only while the current receipt remains applicable; changed evidence appears open with a generic changed-basis explanation. Historical completion remains visible only to its complete retained audience.

## Qualification

Require context completion, exact same-byte and distinct-document completion, a three-copy component, retained obsolete dependencies, missing/contradictory/replacement comparisons, new raw receipts without case events, changed current source heads, unchanged state after unrelated master-label edits, claim/release inheritance and explicit reopen, all-source access denial, role/pause enforcement, atomic receipt/event failure, concurrent evidence and completion, stale preview, exact retry after staleness, RLS/private columns and migration retention. UI must show completed outcomes and source/cost links, keep unknown commands exact, clear denied evidence, require renewed acknowledgment after a changed preview, and support completed/open filtering and mobile navigation.

Independent architecture review recommended the shared evaluator and immutable receipt model, component-wide duplicate accounting, explicit historical dispositions and separation of completion freshness from new-adoption eligibility. Migration 0110 implements this bounded contract; this milestone does not accept B3 or M-B as a whole.


## Implemented contract and qualification receipt

Migration 0110 records immutable `exception_completion` and sealed `exception_completion_member` rows with a typed `completion_recorded` event. The component cap is 100 documents; recursive consumption stops at 101 and fails closed before member/pair evaluation. More than 1,000 retained duplicate dependencies also blocks completion. Every current matching pair appears in authorized preview and receipt, including pairs beyond the selected case's direct dependencies. Private identities, hashes, raw receipts and canonical basis remain unavailable to ordinary runtime SELECT.

List filtering, detail, preview and command share the current-evidence evaluator. Claim/release preserve applicable completion. Snooze on an applicable completion is rejected until explicit reopen. Historical exact retry never re-closes an explicitly reopened or stale case. A label edit does not invalidate confirmed financial facts. Completion cannot repair unsupported replacement, cross-scope, contradictory, missing or stale financial outcomes.

The case UI shows all readable component members, comparisons and retained dispositions, requires an explicit reason and acknowledgment, links source and confirmed costs, offers completed filtering and historical receipt inspection, and reports changed applicability. Panel refresh reads effective case state independently. An uncertain sent command retains its exact body/key/predecessor through parent unavailability and a null current preview digest, while separately reauthorizing every retained field review. Actual retained-authority denial clears its private draft and pending retry.

Verification on the disposable local PostgreSQL 16 database and real app roles:

- Ten completion integration cases passed, including same/different-byte three-source components, every pair, historical dependency supersession, role/pause/source authority, event rollback, concurrent exact retries, private columns, unchanged-label applicability, hidden transitive sources outside the case dependencies, and the real 101-source cap (`/tmp/hv-completion-qualified.log`, 76.92s).
- Two focused cases passed for stale raw-receipt previews and deferred receipt sealing/retention/empty downgrade-upgrade function equivalence (`/tmp/hv-completion-seal.log`, 9.05s). The raw-receipt case repeats one of the ten above.
- A concurrent completion/new-raw-receipt race passed (`/tmp/hv-completion-race.log`, 8.85s): either serialized outcome leaves the effective case open, and a committed completion remains historically replayable with no extra costs. Together these runs cover all 12 completion tests.
- 149 related backend regression tests passed, including every adjusted predecessor migration roundtrip (`/tmp/hv-completion-regression.log`, 126.24s).
- 72 affected UI tests passed (`/tmp/hv-completion-ui-qualified.log`), including frozen retries after null current digests, independently refreshed validity, denied retained members, historical receipts and explicit renewed acknowledgment.
- Production frontend build and lint passed. The existing bundle-size warning remains (approximately 799 KB before compression).
- Fake-Docker deployment/cleanup probe passed with the exact head 0110 gate (`/tmp/hv-completion-deploy.log`); no deployment occurred.
- Real local API/browser qualification passed (`/tmp/hv-completion-browser.mjs`, `.log`): actual extraction using the existing injected clean scanner fixture, explicit completion acknowledgment, lost committed response and exact retry, receipt/source/cost links, new raw same-byte receipt reopening without a case event, unchanged cost count, completed filtering, keyboard save and mobile width. Desktop/mobile screenshots were inspected. This does not qualify a production malware scanner.

Independent read-only review identified and verified repairs to recursive bounds, omitted public component comparisons, stale UI validity wording and unknown-command retention after newly hidden evidence. No remaining concrete defect was reported in the repaired delta. Local API, Vite and browser processes used for qualification were stopped. Browser fixture URLs are invalidated by subsequent disposable test resets.

Cross-scope/page disclosure, grouped corrections/replacements, automated context matching, opted-in versioned mappings, broader B2 runtime/scanner qualification and the remaining M-B–M-G plan remain open.
