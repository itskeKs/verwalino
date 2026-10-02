# Durable declaration review retries

Status: implemented and verified locally; baseline 333a9b6. Migration 0074.

## Contract

The review mutation endpoints accept an optional Idempotency-Key UUID header for
backward compatibility; the first-party UI always supplies it and retains it while
retrying an unchanged request. Missing keys retain revision-based legacy behavior.
Keys are scoped to organization, authenticated actor and operation (single/bulk).
The digest includes import, candidate selection and the complete validated request,
including original revision. Bulk UUID selections are canonicalized as a sorted set
before hashing; reordering the same selection preserves replay. Changed content
under a reused key returns 409.

Successful requests save the exact response in the same transaction as candidate,
revision, audit and issue changes. Replay returns that response even when the
original revision is stale; it does not overwrite newer decisions. Current live
writer and source READ/PREPARE checks run under the ACL and graph locks before
any receipt is disclosed. A lost permission never becomes accessible through a key.
The UI refreshes the graph after success instead of treating the response snapshot
as current global state.

## Boundary and verification

Receipts are private database rows accessed only by a narrow app_review command;
no extra table privileges or generic operation-receipt grants. The trusted review
runtime supplies the validated response. Recording requires its same-transaction
review context and current revision. Serialize by actor/operation/key and import
locks. Preserve receipts on rollback by refusing schema downgrade while any exist.

Prove original-request replay, changed-payload conflicts, concurrent duplicate
requests, failed-transaction retry, later decisions, actor/source revocation,
ordinary-role isolation and single/bulk UI key retention. Reuse accepted 0073
role-isolation evidence where unchanged. Source activation remains disabled.

## Accepted evidence

Two initial backend failures reproduced missing original-revision replay and
changed-payload key reuse. The first 32-case command/API/RLS run passed. Expanded
review command/runtime/API/RLS and adversarial checks passed 104 tests; two new
fixture expectations were corrected (bulk safety needs a nonidentity unit field;
an inactive login correctly returns 401). The final focused 11-case run passed all
idempotency cases, including parallel single/bulk retries, reordered two-candidate
selection, later decisions, authority loss, private role permissions, transaction
failure and receipt-preserving migration rehearsal. No production data was used.

Two UI tests first failed because review calls lacked keys. All 43 review/import
UI tests now pass, including actual PATCH header transport and key reuse/replacement.
Lint passes; production build passes with the existing bundle-size advisory.
Fake-Docker deployment regression and shell syntax pass at head 0074.

Independent review found order-sensitive bulk hashing; canonical UUID sorting and
a reordered replay test address it. No other actionable boundary finding remained.

Real Chromium at localhost:13000 (1440×1000) exercised a strict source review with
an intentionally lost response after the backend commit. The second click sent
the identical key and received the identical response; revision advanced once.
Reload retained the correction. Page identity/content, absence of framework overlay
and page errors, and screenshot inspection passed. Browser plugin unavailable;
regular Playwright used the existing installed runtime. The temporary API/Vite
servers were stopped and the disposable enforcement fixture was cleaned; the
activation predicate remains false. No deployment or external changes performed.
