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

Design reviewed in a bounded read-only architecture consultation. Implementation and qualification are in progress; no grouped correction command is accepted yet. Full B3 remains incomplete.


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
