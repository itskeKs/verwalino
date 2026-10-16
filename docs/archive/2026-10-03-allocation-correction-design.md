# Atomic corrections of confirmed allocation sets

## Problem and supported first boundary

Migration 0109 deliberately rejects individual corrections of adopted split costs. The legacy single-cost correction path creates a review draft and immediately sets the old row's successor; active financial readers exclude superseded rows. Reusing that path for a split would hide parts of the confirmed charge before a complete replacement is confirmed. The grouped path must keep every old member active until the entire reviewed successor commits.

The next increment supports an administrative correction of one previously adopted whole-original allocation in the same property and WEG/MIET scope. Reuse the existing field/context/allocation/treatment review chain, but require a new current allocation/treatment basis and an explicit correction review. New costs may have a different number of lines and different reviewed amounts; exact signed conservation is against the new reviewed invoice fields. The old source bytes, costs, treatment, audit and published metadata remain retained. Cross-scope movement, a different replacement original, cross-uploader review of private intake and cross-original credit relationships need separate extensions; do not admit them accidentally through this command.

## Proposed storage and command boundary

Add an immutable `allocation_correction` command receipt linking one exact predecessor set to one new complete set, with an explicit predecessor, private source basis, reviewed impact digest, reason, acknowledgments, actor and request key/digest. Seal one successor per set and retain the property/scope/original identity across the chain. Extend allocation sets with typed correction lineage; preserve uniqueness of the initial adoption of an original/hash/logical document while admitting only sealed successors in the same chain. Arbitrary second adoptions must remain impossible.

Use the existing organization ACL/financial lock order. Prepare requires current private source prepare/export, published predecessor read/prepare/export, destination prepare/confirm and exact current review heads. Check authority before historical replay. Fresh acceptance validates the full previous membership, current chain head, new current treatment and amounts, still-supported category/context policy, duplicate conflicts outside this own-source correction chain and a freshly verified source-byte attestation. A changed impact, source basis or predecessor fails before writes.

In one transaction: create the new set and every confirmed member cost, append the immutable correction receipt and source revision, set every old member's successor to the new set's primary cost, and append complete audit/impact records. The forward pointer represents a replacement set, not a one-to-one monetary correspondence. The UI must show all replacement members rather than imply that one old line maps to the new primary amount. Narrow typed admission checks may permit only these predecessor-pointer changes; other updates/deletes of confirmed members stay forbidden. Deferred membership checks prevent partial commits. Exact retry returns the historical receipt without advancing or reapplying any chain.

A new published invoice-document revision may reuse the verified original bytes and storage identity while pointing at the new set's primary cost. Preserve the original chain root and extend exactly one current version; permit only the previous record’s transition from active to superseded with its superseded timestamp; keep its content immutable and set successor retention no earlier than the predecessor’s retention. The private intake source does not gain a broader audience. Existing single-primary compatibility pointers remain meaningful per published revision.

## Reader and downstream audit required before implementation

Audit every allocation lookup that currently assumes one row per original/logical document/hash. In particular, `allocation_adoption_result`, mapping source options, duplicate evaluation, exception-completion outcome selection, source custody/admission, retention facts and access coverage must select the true current correction head when current state is intended, while retaining all historical identities and exact receipts. Never filter by audience first and fall back to an older visible head.

Active cost readers already exclude `superseded_by_id`; atomic complete replacement must make their totals correct immediately, with no intermediate gap or double count. Historical financial snapshots keep the original IDs and amounts. Existing settlement snapshots store exact IDs under `inputs.cost_positions`, with additional computation/line references. Determine all actual dependency forms before writing impact logic. Include old and new periods in candidate impact discovery so changes to dates or eligibility cannot miss a newly affected draft.

The correction preview lists accessible affected drafts to refresh and finalized runs requiring a separate correction. Hidden dependencies must block with a generic result rather than disappear from the acknowledgement. Retain typed immutable impact/follow-up evidence; final outputs are never silently recomputed. A draft refresh and a finalized replacement need evidence-based applicability/resolution rules, not a generic manual dismissal. Separate exact cost consumption, potential changed calculations over the union of old and new periods, coverage renewal, and explicit source-evidence review. Coverage hashes include all visible costs within the selected accounting scope, including historical rows; scope-specific plans and asset reports can therefore require new coverage without implying wrong monetary results. Inspect retained coverage source-document references separately. Pin full parent state, snapshots, successors and coverage revisions in the digest. A concurrent compute, finalize or attestation must invalidate the preview.

Follow-up completion requires verifiable evidence: a refreshed draft computation with current coverage after correction, or a finalized replacement/applicability determination supported by the appropriate workflow. A pointer to a replacement draft alone is insufficient. Payment and dunning descendants are not automatically rewritten.

Implement and qualify the read-only readiness/impact evaluator first (0113), then add the atomic command on its validated boundary. The preview itself creates no corrections, costs, source versions or completed follow-ups. Bound discovery to 100 relevant financial parents, 1,000 retained coverage declarations per parent and 2 MB per snapshot; hidden, unclassified relevant, malformed or oversized dependencies block generically. Return no partial dependency list on failure.

## Qualification and current status

Required cases include 2-to-3 and signed-credit corrections, changed totals, unchanged active money before acceptance, exact conservation, atomic rollback after each effect, competing predecessors, lost-response replay after another correction, no extra same-source adoption, blocked partial/member SQL writes, source-byte failure, live role/grant/pause changes, hidden downstream dependencies, source revision custody, unchanged finalized snapshots, stale completion receipts, mapping behavior, private evidence retention, guarded downgrade and actual UI/browser retries.

Design reviewed in a bounded read-only architecture consultation. The read-only preview and bounded atomic command are qualified below. Evidence-based downstream completion and full B3 remain incomplete.


## Qualified 0113 readiness milestone

0113 implements only the read-only correction preview at `GET /api/allocation-sets/{id}/correction-preview?treatment_review_id={id}`. It requires the exact private source, published predecessor preparation/export and destination confirmation rights, plus a new current complete allocation/treatment in the same property and scope. It shows both complete signed allocations and distinct downstream effects. Ordinary adoption still refuses another charge for an already consumed original. No correction, follow-up completion, money or published revision is written.

The evaluator discovers retained cost identifiers and all source-document references privately before returning an authorized complete list. Unsupported, unclassified, inaccessible, oversized or overflowing dependencies yield one generic blocker and an empty list. A bounded helper validates retained reference containers and UUIDs before recursive traversal; ambiguous oversized snapshots elsewhere in the organization also conservatively block discovery. Economic-plan snapshots use their actual `document_type` discriminator; settlement and asset snapshots use `run_type`. Coverage renewal alone does not assert an incorrect financial amount. Final snapshots and their hashes remain unchanged.

Independent architecture and implementation reviews repaired three completeness/validation problems (old references beyond a truncated coverage window, malformed position members, bounds applied after traversal), and a follow-up found the economic-plan discriminator mismatch. Regression fixtures cover each. The UI explicitly loads the preview, clears prior details on refresh or changing authority/basis, aborts obsolete responses, links each exact accessible parent, and makes no financial confirmation available.

### Evidence

All checks ran locally with synthetic data, PostgreSQL 16 at the disposable test endpoint, Python 3.13 and the existing frontend runtime. No external deployment occurred.

| Check | Result and scope |
| --- | --- |
| New database/API cases | 16 distinct cases qualified across focused runs: signed 2-to-3 comparison, no additional costs/publication/proofs, consumed-source adoption refusal, distinct dependency effects, changed parent/coverage/finalization digests, live authority, hidden/unclassified/unsupported/oversized/overflow blocking, old retained reference beyond 1,001 entries, actual plan builder snapshots, unchanged finals, private helper grants and function-preserving migration roundtrip. |
| Related backend and migrations | 57 distinct cases qualified: adoption, mappings, exception completions and all 17 changed predecessor migration tests. Initial combined run had 55 passes, one transient PostgreSQL SSL-upgrade connection failure and one migration-test isolation failure. The latter now truncates synthetic mapping evidence inside its rolled-back savepoint; both cases passed the focused rerun. |
| UI | 9 tests passed across correction preview and adoption integration; deliberate load, effect descriptions, correct links, stale-response cancellation, denial cleanup and no partial dependency display. |
| Build and lint | Both passed; the existing approximately 819 KB bundle-size warning remains. |
| Deployment script probe | Fake-Docker regression passed with exact head 0113; no deployment. |
| Browser | Actual local upload, injected-clean inspection/extraction, human review and adoption produced the fixture. A new three-line review and two financial parents exercised real API/UI preview, keyboard access, 390 px mobile without horizontal overflow, denial cleanup and unchanged active costs. The browser harness initially pressed Enter before the button became enabled; resuming the same fixture with readiness waiting passed. Desktop/mobile screenshots were inspected. The computed-plan snapshot branch was separately qualified with the actual backend builder. |

Relevant source/test/script fingerprint: `1d943593f523a4d7c98de2a7dff09f12946b384e5a85a10cdd00644847327c66`.

The grouped correction command, immutable impact/follow-up records, source-version replacement, exact historical replay and complete downstream resolution remain the next increment. The acceptance list above does not accept those future mutations, full B3, or M-B.

## 0114 command implementation sequence

The next command uses `If-Match` for the exact predecessor set and a separate idempotency key, treatment review, 0113 preview digest, reason and three explicit acknowledgments (whole replacement costs, original disclosure, downstream follow-ups). Authority is checked before historical replay. Fresh preparation requires the current predecessor and new current treatment, recomputes the exact preview under the existing ACL/financial lock order and obtains a new transaction-bound original-byte attestation.

Add a nullable predecessor reference to `allocation_set`; initial-adoption uniqueness becomes partial on predecessor-null rows, while a unique predecessor prevents branches. Add immutable private correction receipts and typed impact rows with organization-bound foreign keys to their settlement or plan parents. The impact rows seal the exact complete preview list and prevent referenced drafts from disappearing without a supported follow-up workflow. Fresh correction never marks these impacts resolved.

Within one transaction, create the successor set and all confirmed costs, seal receipt/impact evidence, advance every predecessor cost pointer, supersede only the old invoice status/timestamp and append the new same-byte published invoice revision. A deferred seal verifies complete predecessor membership, all forward pointers, the identical original/property/scope, new review basis, source chain/version/retention and every impact row. The only admitted changes to old rows are these exact transitions backed by the current transaction’s typed receipt and integrity proof. Raw cost correction and upload routes continue to reject adopted-member edits.

Current readers must choose the true successor head before checking its audience: adoption results, exception-completion hash lookup and fresh mapping activation. Historical receipts retain their exact sets and bodies; retrying after another correction returns that historical receipt without reapplying it. Existing opted-in mappings remain advisory and are not implicitly revoked when the same source allocation changes. Duplicate discovery keeps every historical claim for other documents and excludes only this exact own-original chain for the correction command.

The receipt UI must show all replacement members, the retained source-version link and explicitly open downstream obligations. Evidence-based resolution of those obligations remains a following implementation stage; a settlement successor pointer to a draft cannot count as completed correction work.


## Qualified 0114 atomic correction milestone

0114 adds the reviewed whole-set correction command for the supported same-original/property/scope boundary. Acceptance creates every new confirmed cost, seals the immutable receipt and typed downstream impacts, advances every previous member, and publishes exactly one retained same-byte invoice revision atomically. Current allocation, exception-outcome and fresh mapping readers select the correction head. Historical retries return their original result after subsequent corrections and recheck all required authority. Ordinary adoption still cannot create a second charge.

Dependency discovery includes up to 100 ancestor sets, their cost identifiers, published source versions and service periods. Earlier unresolved effects therefore remain part of subsequent previews, authorization and receipts. Hidden inherited dependencies produce a generic blocker without partial identifiers. Financial snapshots remain unchanged and every recorded follow-up stays open in this milestone.

The UI requires a reason and three separate acknowledgments, shows all replacement costs and the published revision, and retains the exact predecessor/body/key through an unknown POST result, failed parent read and refreshed current head. Confirmed source denial clears retained command state. Reloading displays the current authorized correction receipt and its open effects. A reader lacking correction preparation/confirmation rights can still read the current allocation; the stronger private receipt remains unavailable.

Independent reviews identified and repaired inherited-dependency loss, transient parent-read unmounting of unknown commands and overly strong receipt authorization on ordinary reads. Broad regression found and repaired a PL/pgSQL table-alias/variable collision in the current exception-outcome lookup. A test-only migration rehearsal initially waited on its own uncommitted scope fixture; committing that fixture before the rolled-back rehearsal resolved the wait.

### Evidence

All checks used the local disposable PostgreSQL endpoint and synthetic data. No external deployment occurred.

| Check | Result and scope |
| --- | --- |
| New command/backend cases | 17 distinct cases qualified in focused runs: invoice/credit 2-to-3, changed gross amount and service period, all old members and one active source version, retention, concurrent exact/different-key requests, seven injected rollback stages, stale impacts, acknowledgments, byte failure, live authority/tenant denial, raw-role and transaction-bound proof rejection, immutable evidence, guarded downgrade/empty roundtrip, repeated-correction ancestry and hidden contact disclosure, historical retry after another head, read-only current allocation, and old exception completion becoming inapplicable. |
| Related regression | 57 distinct cases qualified across adoption, exception completion, mappings and 17 predecessor migration rehearsals. The combined run had 46 passes and 11 failures from the exception SQL alias collision; the repaired exception module plus the new current-outcome test passed all 13 cases. The 0113 helper roundtrip and updated 0112 mapping roundtrip also passed focused qualification. |
| UI | 15 tests passed across preview, correction command and adoption integration, including committed-response uncertainty followed by parent GET failure and exact retry against the original predecessor. |
| Build/lint | Passed after final UI changes. Existing bundle-size warning remains (approximately 824 KB). |
| Deployment-script probe | Fake-Docker regression passed with required head 0114; no deployment. |
| Browser | Actual local upload, injected-clean extraction, human review and initial adoption followed by three-line correction. Keyboard confirmation, all acknowledgments, lost committed response, parent read failure, head refresh, identical retry, all old forward pointers, source version 2, retained receipt after page reload and two open impacts passed. Desktop and 390 px mobile screenshots inspected; no horizontal overflow or page errors. Initial harness launch used the wrong Vite working directory and failed CSS loading before login; relaunching in frontend passed. |

Relevant source/test/script fingerprint: `1aed1ec725910a82fd99fad3fe8ec24b44402d42b5a3415a2ba2c86d8eac5c4f`.

The next increment must establish trusted computation evidence before allowing a draft recomputation or finalized successor to complete an impact. A new declaration, audit timestamp, stored hash or pointer to a replacement draft alone is insufficient. Cross-scope/page disclosure, other B3 requirements and M-B remain unaccepted.
