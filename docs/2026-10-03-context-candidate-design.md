# Bounded source-based context candidates

## Scope

Add read-only candidate discovery to the existing manual assignment workflow. Current invoice extraction contains source pages and invoice fields but no trusted canonical customer/contract identifiers. The first adapter recognizes explicitly labelled property UUIDs, meter numbers and service/property addresses from the selected extraction pages. It returns evidence and alternatives; it never writes an assignment, selects an accounting scope, changes a review or creates money. Existing manual assignment and human context comparison remain required. Versioned opt-in mappings and automatic draft attachment are subsequent work, not implicit consequences of this adapter.

## Evidence and authority

Require the exact current reviewed partition member and latest successful/needs-review extraction, with source access. Keep the extraction and partition IDs in every response. Ignore unlabelled supplier addresses, invoice issuer names, fuzzy names and upload hints as matching keys. Recognize only whole labelled lines with bounded values. Preserve exact page, quotation and method. XML sources currently lack page text and return an explicit unsupported result; never scan unrelated raw XML text as a property assertion.

Bound the source signals and accessible comparison domain. If either cap is exceeded, return a generic manual-review result instead of claiming a complete candidate list. Query only organization-local scopes with current read/prepare and property-master read access; enforce active-scope restrictions. Meter candidates additionally require the existing meter read policy. Hidden entities contribute no names, counts, alternatives or match explanations. Do not infer unique identity from a single visible result: every result requires manual review, even when one candidate is shown.

Property UUID matches use exact canonical identifiers. Meter numbers are exact case-sensitive identifiers; duplicated numbers remain alternatives. Address matching uses complete street, postcode and city under conservative Unicode/case/whitespace normalization; it remains supporting evidence. Multiple property matches and an intake-hint disagreement are visible conflicts only when the hint is itself readable. Scope-type/whole-property limitations and proposed period mismatches are explicit candidate blockers. Meter validity is unverified because current master data has no dated topology.

## UI and verification

Display candidate evidence, method, scope, exclusions and all bounded alternatives in the source review. Copy a selected candidate into the editable manual proposal only through an explicit user action, preserving a deliberate explanation; sending the existing proposal remains a separate action. Disable selection on stale source basis, unavailable rights, unknown pending request or a candidate exclusion. An empty or failed candidate read must never clear a manual draft or pending command. Fresh denial removes candidate evidence.

Test exact UUID/meter matches, duplicate meters/properties, labelled address normalization, supplier-address exclusion, contradictory signals, invalid/missing period, source/candidate caps, hidden entities and cross-organization access, stale extraction/partition, read-only money/proposal counts, UI explicit selection, retries and denied refresh. This does not accept B3.


## Implemented boundary

Migration 0111 adds only `assignment_candidate_source`, a source-authorized projection of the exact private intake attempt's selected `{number,text,method}` pages. It inspects the latest extraction before checking usability, validates source hash/range/payload, and accepts no independent attempt identifier. Existing private attempt grants remain unchanged. The API responds with the pinned partition/extraction IDs and never persists a proposal or mapping.

The source bound is 1,000,000 UTF-8 bytes, 1,000 characters per recognized line and 50 recognized assertions. The authorized domain is at most 200 scopes and 500 meters, retrieved with limit-plus-one after authority filtering. Overflow produces no selectable prefix. Own-source assertions with no matching accessible context remain visible without revealing hidden names, IDs or counts. Different scopes on one property remain explicit alternatives. Addresses retain house-number suffixes and meaningful punctuation; case/Unicode/whitespace and comma separators normalize conservatively. Period facts are explicitly labelled as unreviewed extraction values.

The source review copies a candidate only through an explicit action and keeps an existing explanation. The immutable manual assignment command remains separate and retains its original body/key/predecessor after an unknown response. Candidate retrieval failure clears candidate evidence while leaving the manual draft intact. A candidate exclusion disables copying; the manual workflow remains available for deliberate corrected-source review.

## Qualification receipt

- 28 focused backend tests passed (`/tmp/hv-candidates-qualified.log`, 33.69s): 11 new parser/API cases plus existing assignment and completion coverage, excluding the already-qualified unchanged 101-source completion test. Cases include labelled UUID/meter/address matching, supplier-address exclusion, contradictory and unmatched assertions, multiple scopes on one property, hidden-domain caps, meter cap, latest failed extraction, private attempt denial, app_worker/app_review denial, paused source authority, source-basis conflicts, no write effects and 0111 downgrade/upgrade equivalence.
- 78 affected UI tests passed (`/tmp/hv-candidates-ui-qualified.log`). The expanded source-page suite had outdated treatment/adoption mocks; those fixtures were brought into line with existing APIs. The completion test helper now waits for retained-source reauthorization before entering an acknowledgment.
- Frontend production build and lint passed; the existing bundle warning remains.
- Fourteen affected predecessor migration/retention/backfill cases passed (`/tmp/hv-candidates-migrations.log`, 38.28s); 0111 is removed before each earlier roundtrip.
- Fake-Docker deployment/cleanup probe passed with exact head 0111 (`/tmp/hv-candidates-deploy.log`); no deployment occurred.
- Real local API/browser qualification passed (`/tmp/hv-candidates-browser.mjs`, `.log`) with actual extraction and the existing injected clean scanner fixture: two same-property scopes remain alternatives, exact page quotation, explicit candidate copy, an existing explanation preserved, no automatic proposal, separate actual manual POST, committed-response loss/exact retry, copy disabled while outcome is unknown, denied candidates cleared, keyboard interaction and mobile width. Desktop/mobile screenshots were inspected. A local wrapping-button modifier fixes the mobile two-line button height; a focused browser probe verifies content fits the button and viewport (`/tmp/hv-candidates-mobile-check.log`). This is not production scanner qualification.
- Independent read-only review found no concrete implementation blocker. Its requested candidate-to-manual-POST and pending-command qualification passed in the browser.

Automatic assignment, opted-in persistent mappings, cross-scope disclosure, grouped corrections/replacements and overall B3 acceptance remain open.
