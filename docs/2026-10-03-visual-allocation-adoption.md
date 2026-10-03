# Financial adoption of reviewed visual sources

Status: migration 0121 and the explicit visual financial UI are implemented and locally qualified. This increment does not complete M-B/B3 or the broader M-A–M-G plan.

## Contract

Adopt one reviewed logical invoice, with its balanced single-scope allocation and complete explicit treatment review, using an exact visual derivative as its permanently retained financial source. Distinct logical invoices in disjoint ranges of one original can be adopted in different authorized scopes. Splitting the same invoice amount between multiple scopes requires a separate conserved multi-scope allocation group and is not implied by independent page adoption.

The publisher selects a current disclosure of an artifact matching the exact logical member, reviewed partition, clean inspection and allocation destination. Original custody/export, destination read/prepare/confirm, and the applicable disclosure read/export rights are checked live. The command pins treatment revision, disclosure, artifact hash, preview digest, expected financial head and idempotency key. It requires separate financial confirmation and acknowledgment that this exact derivative becomes a retained financial source. A previously granted transient disclosure is not blanket financial approval.

The service verifies both original bytes and stored derivative bytes. Independent trusted proofs bind both observations to the actor, request digest, backend process and transaction. Atomic completion rechecks current basis and authority, claims the canonical physical interval, creates all confirmed costs and their bindings, creates one released financial invoice-document bridge, and retains the exact reviewed treatment/provenance. Any failure rolls back the entire financial effect; orphaned trusted observations remain private evidence and cannot authorize another transaction.

The financial invoice document stores only the exact derivative bytes/hash, a neutral filename and explicit visual-source semantics. It does not copy private intake payloads, other pages, source filename or original storage location. The financial copy remains under financial source grants and retention after transient disclosure withdrawal. The UI explains this before adoption. Existing whole-original adoption remains an explicit separate operation.

## Identity and compatibility

Keep `allocation_set.content_sha256` as the canonical original hash. Revoke runtime SELECT on that column so destination-only readers cannot obtain private original identity; existing service DTOs already select other columns explicitly. `invoice_document.content_sha256` and its registry reference identify the exact released file. Expose the source variant and add private artifact/disclosure and derivative-proof bindings rather than silently redefining an established hash field.

Existing original adoption keeps its whole-original invariant and indexes. Visual roots use the 0119 claim's exact logical interval. Root uniqueness must be replaced only where necessary with source-variant-aware indexes plus sealed, organization-wide overlap checks. Whole-original claims conflict with every page claim; overlapping and reuploaded ranges conflict regardless of scope, grants, source ID or regenerated artifact. Corrections inherit the root claim and cannot change canonical hash, range, source variant or destination. The existing primary-cost compatibility pointer and complete `document_cost_link` membership remain sealed.

Physical consumption and business duplication are separate checks. Duplicate discovery must distinguish exact physical copies, overlapping physical ranges and disjoint documents. A shared container hash alone does not mean two disjoint invoices are the same document. Equal reviewed business identity still requires an explicit current comparison; hidden matches block generically. Existing raw/uninspected and legacy source blockers remain until their exact relevant range can be proven. Direct cost confirmation or original/known-artifact upload must not bypass a consumed root via an alternate entry point.

## Required coordinated changes

- Introduce shared current-allocation eligibility without weakening the existing whole-original wrapper. Visual adoption additionally requires the exact current disclosure and retained-byte proofs.
- Branch `adoption_bridge_admission` and `validate_allocation_set_membership` by sealed source variant. Verify derivative storage/hash/size, neutral filename, lineage, publisher, release timestamp and retention exactly. Never use a session flag as authority.
- Extend permanent claim admission and correction membership to derive the exact original interval from sealed visual provenance. Do not accept caller-selected hashes or ranges.
- Update adoption/correction duplicate snapshots, original and known-artifact conflict guards, and exception completion's connected components/current outcome selection. Resolve financial outcomes by canonical physical identity and true correction head; reject ambiguous matches before filtering by audience.
- Keep correction kind `intake_reviewed` so `CostEntryService.correct` continues to require complete-set replacement. Publish the derivative hash in financial audit payloads and a neutral filename in the invoice bridge.
- Preserve canonical source semantics in mapping proofs and trusted computation fingerprints. Check every published-source reader, registry adapter, retention/privacy path and financial dependency snapshot against the final variant.
- Extend corrections with exact retained visual source handling and the existing downstream impact/completion workflow. Revoking the transient disclosure must not make permanent financial evidence disappear or authorize replacement with original bytes.
- Add deliberate visual-source choice to the financial UI, with exact-file inspection, financial and retention acknowledgments, frozen uncertain commands, live refresh, correction continuation and accurate historical-receipt wording.

## Qualification

Use real source preparation, rendering, stored byte verification and API transactions. Cover two disjoint invoices in one PDF, separate authorized destinations, exact signed totals, original and private metadata denial, no extra primary charge, original-versus-page and overlapping/reuploaded/regenerated-artifact attempts, business duplicate comparisons, stale partitions/disclosures, withdrawal races, grant loss, strict acknowledgment types, changed proofs, all transaction failure stages and concurrent exact/conflicting requests. Cover historical retry after withdrawal/correction, permanent financial source availability, source-version retention, exception outcome and opt-in mapping behavior, actual engine fingerprints, migration/backfill/rollback, and two-user desktop/mobile browser use. Reuse unchanged evidence only where the final code preserves its assumptions.

## Implementation and reviewed boundaries

The visual commands have their own strict payloads and routes. They cannot reuse the whole-original disclosure acknowledgment. Published financial DTOs expose `source_variant` and the derivative invoice reference; the canonical original hash, artifact, disclosure and byte-proof columns remain unavailable to runtime readers. Existing whole-original correction endpoints refuse visual predecessors. Visual correction keeps the original artifact and publication binding even after transient withdrawal.

Physical duplicate checks use the bounded ancestry resolver from 0120 and exact logical page intervals. A unique financial outcome is chosen from actual correction heads before audience filtering. Disjoint intervals do not connect exception components solely because they share a container hash. Reviewed business identity remains a separate blocker, including across scopes. Reuploading a retained derivative or regenerating its pages cannot create another financial effect.

The browser form loads paginated private artifacts and their current publications, previews the chosen treatment/publication pair, and requires separate cost and permanent-retention acknowledgments. Unknown POST outcomes retain their immutable logical route, payload and key. Current authorization denial invalidates the command epoch, so a later failed POST cannot restore a discarded retry. A separate sending state prevents read refreshes from unlocking an in-flight financial command. Corrections freeze their source variant, derivative hash and predecessor route.

Historical migration fixtures downgrade 0121 before truncating visual evidence: otherwise the new foreign keys would cascade into original allocations and invalidate the intended historical data setup. Production downgrade refuses retained visual allocations or corrections; fixtures test empty restoration inside rolled-back savepoints.

## Qualification record

- Real rendering/storage/API tests cover exact derivative release, destination-only denial of originals and canonical hashes, retained content after transient withdrawal, historical retry, two disjoint invoices in WEG/MIET scopes, independent correction, and same-business-identity blocking.
- Independent original/visual proof mismatches and cross-transaction reuse are refused. Injected failure at every adoption and correction financial stage leaves no partial money or successor. Concurrent exact commands return one retained result.
- Private SQL columns/helpers and downgrade refusal are verified. Physical derivative regeneration and reupload are rejected; unused disjoint pages remain unconsumed.
- Exception completion chooses the correct disjoint financial outcome. Explicit private mapping evidence remains bounded to the reviewed original page. An actual asset-report computation, visual correction, renewed coverage, recomputation and acknowledged follow-up verify changed source fingerprints and current engine evidence.
- Independent static reviews covered SQL privacy/integrity and UI response ordering. The discovered late-response UI race was fixed and has a deferred-response regression test.
- Focused UI tests verify exact publication/hash payloads, partial-document availability, source inspection link, retention acknowledgment, stale refresh, denial clearing, unknown retries and retained visual correction routing.

Local evidence logs are recorded at milestone completion. No deployment or external system change is part of this qualification.

### Local check receipts (2026-10-03)

Base revision: `1a44af6`; changed surface: migration 0121, visual adoption/correction API and service, source-choice/correction UI, regression fixtures and migration-head gates. Environment: disposable PostgreSQL 16 on loopback, Python 3.13 backend environment, Node/Vite/Vitest frontend, headless Chromium; synthetic users/documents only. Provider configuration and `USER` were excluded from test processes. No live provider or production deployment was exercised.

| Check / working directory | Result and evidence |
| --- | --- |
| Visual financial DB/API / repository root | 22 distinct cases passed across focused runs. Logs: `/tmp/hv-visual-allocation-security.log` (17 passes; two initially incorrect HTTP-status expectations), `/tmp/hv-visual-allocation-proofs.log` (both corrected expectations passed), `/tmp/hv-visual-allocation-outcomes.log` (disjoint outcome/business-identity cases plus 0118 retention recheck), `/tmp/hv-visual-allocation-followup.log` (mapping/actual engine and physical reupload). |
| Existing financial regression / repository root | 162 of 163 passed in `/tmp/hv-visual-allocation-regression.log`; the historical backfill fixture failure was corrected and included in the successful historical-chain run below. Unchanged passing functional evidence was reused. |
| Historical migration chains / repository root | All 27 changed-chain tests passed, exit 0: `/tmp/hv-visual-allocation-historical-chains.log`. Covers the corrected backfill and 0118 orphan-observation fixtures. Earlier narrower migration run had one now-corrected fixture failure. |
| Frontend components / `frontend` | 20 tests passed, exit 0: `/tmp/hv-visual-finance-ui-qualified.log`, including late POST after authorization denial and retry with no current review. |
| Frontend build and lint / `frontend` | Both passed, exit 0: `/tmp/hv-visual-allocation-build-final.log`, `/tmp/hv-visual-allocation-lint-final.log`. Existing bundle-size warning remains. |
| Fake deployment cleanup/head probe / repository root | Passed, exit 0: `/tmp/hv-visual-allocation-deploy-probe.log`; no deployment. |
| Actual two-user browser / repository root | Complete fresh-source flow passed, exit 0: `/tmp/hv-visual-allocation-browser-final.log`. Real UI field/context/allocation/treatment review, visual financial adoption with lost 201, exact replay after destination withdrawal, original/canonical-hash denial, permanent derivative download, first visual correction after withdrawal, desktop/mobile width, no page errors. |

Browser setup first needed a Python quoting correction and a native-select locator correction. An intermediate run stored its correction but timed out waiting for the confirmation; reload verified the retained receipt, a continuation succeeded, and the final complete run explicitly started without a prior correction receipt and passed. Only the final fresh-source run is counted as complete browser acceptance. The visual source in that browser fixture is one page; the real multipart WEG/MIET behavior is covered by DB/API tests, with partial-document source choice also covered by component tests.

Screenshots: `/tmp/hv-visual-allocation-desktop.png`, `/tmp/hv-visual-allocation-mobile.png`, `/tmp/hv-visual-financial-reader-mobile.png`. Local API and Vite processes are stopped after qualification. These receipts accept the bounded visual-source increment only; the implementation plan retains the broader B3 and release gates.
