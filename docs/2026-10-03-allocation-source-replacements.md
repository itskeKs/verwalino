# Changed-source allocation replacement

## Contract

Migration 0122 allows a reviewed whole original to replace a confirmed whole-original allocation in the same property and accounting scope. The reviewed document kind must stay the same. Revised totals and service periods are taken from the new complete review; every new line must still conserve its reviewed invoice total and satisfy the existing treatment rules.

The explicit workflow selects an accessible retained predecessor, compares old and new complete allocations and downstream effects, then confirms all costs, permanent source disclosure and open follow-up obligations. Candidate discovery matches normalized supplier, invoice number, kind and currency, without requiring the old gross amount or service period to stay unchanged. The replacement command and receipt provide an oriented, immutable decision. A prior advisory `replacement_requested` duplicate comparison does not itself authorize money.

`allocation_set.predecessor_id` remains the financial chain. The sealed `transition_kind=source_replacement` establishes a new physical source claim, even though the allocation has a financial predecessor. Every earlier claim remains permanent. A later correction of the same source inherits its immediate predecessor's claim. Physical alias and missing-claim checks recognize both initial roots and replacement boundaries. Physical equality retains its original meaning.

The atomic command retains the established cost membership, supersession, audit, invoice version, source retention and impact rules. It verifies the newly selected original bytes through a transaction-bound review-role attestation. An old proof cannot authorize different bytes. Both original invoice versions remain accessible under their live source permissions, with nondecreasing retention.

Only exact invoice reviews retained in the selected financial ancestry are exempted from business-duplicate admission for the replacement. New unadopted revisions, unrelated reviewed sources, unknown raw receipts, legacy sources and physical overlaps remain subject to the existing guards. Historical commands replay their exact receipt; changed bodies conflict. Each logical intake source resolves to the current financial head after checking the caller's current access. Historical receipts remain exact; historical source mapping assertions do not silently migrate to another source.

## Implementation and UI

The separate preview, preparation and command use the established correction transaction with an explicit discriminator on both allocation and receipt. Ordinary correction authority remains bound to the same logical source. Public API routes are:

- `GET /api/allocation-treatments/{review}/replacement-candidates`, with bounded cursor pagination.
- `GET /api/allocation-sets/{predecessor}/replacement-preview?treatment_review_id=…`.
- `POST /api/allocation-sets/{predecessor}/source-replacements`, with exact `If-Match` and idempotency key.

The intake view offers an explicit predecessor selector, retained original link, old/new amount comparison and full effect preview. It freezes the predecessor, review, command body, route and key during an unknown response. While that command is pending, alternative financial commands are disabled. A definitive access denial removes cached source details and invalidates late POST responses; a transient read failure preserves recoverable selection.

Downgrade refuses retained changed-source financial evidence. Empty round trips restore the earlier definitions and grants. Deployment scripts gate on head 0122; only the fake deployment probe is authorized here.

## Qualification

- Eleven DB/API cases passed in `/tmp/hv-source-replacement-security.log`: unchanged/revised totals, permanent claims, original bytes, source versions, A→B→same-source B2→C, exact historical replay, stale preview, authority/revocation, integrity failure, four injected rollback stages with concurrent exact replay, unrelated/unadopted duplicate evidence, ordinary-route denial, immutable evidence and migration restoration.
- An additional real financial computation case passed in `/tmp/hv-source-replacement-followup.log`: an obligation from the original source completes only against the latest of two subsequent replacements; an old-source reupload with a different reviewed invoice number stays blocked. The same run passed the visual migration test followed by the corrected historical follow-up fixture (three cases total).
- The broader shared regression passed 142 of 143 cases in `/tmp/hv-source-replacement-regression.log`. The remaining historical backfill fixture encountered retained visual allocations from an earlier module before reaching its intended older migration guard. It now starts with an explicit clean allocation boundary before creating its own retained data; the failing order was reproduced and passed in the three-case follow-up run. No application change was needed for that fixture failure.
- Twenty-six focused component cases passed in `/tmp/hv-source-replacement-ui-final.log`, including frozen unknown commands, stale review reset and access-denial versus transient-error behavior. A render-time ref assignment reported by lint was moved into an effect; focused lifecycle recheck is recorded separately.
- Independent read-only review found the cached-source display issue and confirmed its repair. No remaining concrete backend blocker was found. Review did not substitute for execution evidence.
- Build, lint and fake deployment probe passed; the build retains the existing large-bundle warning. No deployment occurred.

- Eighteen additional historical-chain tests, not already covered by the shared regression, passed in `/tmp/hv-source-replacement-historical.log`. The six replacement component lifecycle cases passed after the effect adjustment in `/tmp/hv-source-replacement-ui-lifecycle.log`. Final lint is `/tmp/hv-source-replacement-lint-final.log`; final build is `/tmp/hv-source-replacement-build-qualified.log`; fake deployment probe is `/tmp/hv-source-replacement-deploy-probe.log`.

- Actual fresh-source desktop/mobile browser qualification passed in `/tmp/hv-source-replacement-browser-final.log`: a 119.00 invoice is replaced by a 149.00 reviewed invoice, an intentionally lost committed response is replayed with identical route/body/key/precondition, both PDF byte streams remain exact, and both logical inputs resolve to the new financial head. No page errors or horizontal overflow were observed. Screenshots are `/tmp/hv-source-replacement-desktop.png` and `/tmp/hv-source-replacement-mobile.png`; the final mobile capture waits for the sidebar transition to finish. Local API/Vite processes were stopped afterward.

The first browser attempt stopped before login because the local Vite harness was launched from the repository root instead of the frontend directory; correcting the harness working directory restored the stylesheet configuration. An intermediate successful journey captured the mobile sidebar mid-animation; the final fresh-source run includes the explicit transition wait. Neither harness issue required an application change.

## Explicit limits

This increment accepts same-property/scope original-to-original replacement. Replacement involving retained visual sources, cross-scope movement and a conserved allocation of a single invoice across multiple scopes remain open. Duplicate exception completion still requires a typed replacement disposition; a replaced source is not misrepresented as physically equal to its successor. Typed credit-note relationships and remaining B3 actions are also pending. B3, M-B and the overall M-A–M-G plan remain incomplete.
