# Evidence-based allocation correction follow-up completion

## Contract

Migration 0115 proves actual server computations. The next boundary consumes that evidence to complete individual retained allocation correction impacts. It must not dismiss an impact by reason, timestamp, parent pointer or manually edited snapshot alone.

Capture immutable parent identity/context for each impact at correction time: property, exact scope, mandate, period, financial family, recipient, linked plan and plan-year/due-day context. The existing correction basis retains a complete parent digest but not all context values. Historical context can be recovered only when the current complete parent exactly matches that retained digest; otherwise completion stays blocked. Do not silently infer historical recipient/context from a changed draft.

An originally draft impact requires a current trusted computation for its exact parent after the correction; if it later finalized and acquired a successor, use the same validated final-successor chain. An originally finalized impact requires a bounded, cycle-checked chain to a different current finalized successor, preserving context and scope at every step. A changed linked WEG plan is permitted only through its own bounded same-context finalized-successor chain. A draft successor remains open. Current source/coverage/output equality must match the consumed computation receipt. Source evidence review requires explicit acknowledgment and the current published allocation-head source in the exact coverage declaration consumed by that computation. Manually reviewed plan/report inputs remain explicit user responsibility.

A completion command uses the exact preview digest and a retained idempotency key. Store an immutable typed completion receipt linking impact, current allocation head, computation receipt, actor, reason and reviewed source basis. Historical retry returns that original receipt after live access checks. Current applicability is derived independently; new heads, inputs, outputs, declarations or successor changes make it stale. Keep correction receipts unchanged and expose follow-up status through a separate endpoint.

The UI should show open, completed and changed-since-completion states, with links to the actual financial parent. Keep an unknown submission body/key intact through transient read failures and permit exact retry. Source review and completion are explicit actions.

## Qualification

Cover actual postcorrection recomputation, stale/manual output refusal, finalized successor versus draft pointer, context/recipient/scope mismatch, chain cycles/bounds, exact coverage source renewal, later correction/recomputation invalidation, immutable historical replay, tenant/role/grant boundaries, rollback/concurrency and guarded migration roundtrip.

## Integration repairs

The original scoped coverage SELECT policy looked up the newly inserted declaration by its own ID through a STABLE helper. INSERT RETURNING could not see that new row in the helper snapshot and failed for an explicitly selected financial scope. Migration 0116 authorizes the declaration directly through its typed financial parent instead. Attestation takes ACL and financial locks before the parent row lock.

FinancialCoverageReview previously always sent an empty source list. It now offers paged, currently released source versions for the subject property and exact financial scope, with explicit selection and original links. Source/reason/input changes reset acknowledgment. A fresh source read clears stale selections; actual authorization denial clears copied source data; loading disables save. Pagination retains previously validated selections while permission remains available.

Independent review found that migration backfill must not use a tenant-GUC-dependent helper across organizations, and that the first source-selector implementation retained invisible old selections on refresh. Both are repaired. The two-organization/no-GUC backfill test admits unchanged exact parents and excludes changed ones; version-replacement and denied-refresh UI tests exercise selection/reset behavior. Static review did not execute tests.

## Accepted follow-up boundary (migration 0116)

Implemented private retained context and immutable completion receipts, separate current applicability, exact historical retry and deliberate completion/source acknowledgments. Downgrade refuses retained context or completions. The access manifest includes both private tables. Historical contexts are backfilled only from an unchanged exact full-parent digest and typed scope binding; changed legacy parents remain unproven.

Qualification uses the disposable PostgreSQL 16 instance, Python 3.13, the local frontend toolchain and synthetic records. All checks below completed successfully unless an initial fixture repair is explicitly recorded; existing unchanged passing evidence was reused:

Accepted code/test/script content fingerprint: `f8114f7b99d49acb7a7280d298cdcffec40fa2f765eda5c27d76a1c3d4023b0d` (37 changed paths, sorted path + NUL + content + NUL). Documentation is excluded. Commands ran from the repository root except frontend checks from `frontend/`; existing local dependency and database preflight evidence remained applicable.

- Thirteen follow-up cases passed across `/tmp/hv-followup-guards.log`, `/tmp/hv-followup-backfill.log`, `/tmp/hv-followup-boundaries.log`, `/tmp/hv-followup-boundaries-repair.log` and `/tmp/hv-followup-final-regression.log`: actual asset-report recomputation, finalized successors (including formerly draft originals), changed output/context/source/coverage rejection, concurrent exact replay, live tenant/grant denial, unchanged-versus-changed two-organization backfill without tenant context, runtime-role isolation, transaction rollback, cycles/101-node chains and later allocation heads. Initial rollback/long-chain fixture errors were repaired and those cases passed.
- `/tmp/hv-followup-coverage-regression.log`: 25 passed, including the then-current nine follow-up cases and 16 existing financial-input policy cases. `/tmp/hv-followup-final-regression.log`: 40 passed, including the new head case, 23 computation-evidence cases and 16 source-input cases.
- `/tmp/hv-followup-migrations.log`: 20 migration rehearsals passed, covering predecessor rollback compatibility through 0115. `/tmp/hv-head0116-probe.log`: fake-Docker head/production-script probe passed; no deployment occurred.
- Linked-plan context helper: six cases passed in `/tmp/hv-followup-linked-plan-final.log`; the scope case passed after correcting its fixture in `/tmp/hv-followup-linked-plan-scope.log`. A finalized current same-context linked successor is accepted; draft, changed due-day context, wrong expected scope, unlinked/nonterminal successor and revoked access are rejected. Earlier fixture errors used a closed setup transaction, the wrong tenant-setting name or attempted a cross-scope successor already forbidden by the existing database guard. This helper qualification does not claim a complete linked-plan correction UI journey.
- `/tmp/hv-followup-ui.log`: 27 tests passed across five component files, including deliberate review, source version replacement, paging, denied refresh, history and unknown-command recovery through a missing parent or newer head. Build and lint passed; the existing bundle-size warning remains.
- `/tmp/hv-followups-browser.log`: actual browser journey passed. Select published source version 2 in coverage, save the declaration, compute the report, explicitly acknowledge source and completion, lose the committed response and subsequent parent read, recover and retry the identical command, then observe completed history after reload and changed applicability after a new coverage declaration. Desktop and 390-pixel mobile screenshots were inspected; no page errors or horizontal mobile overflow occurred.

This accepts the follow-up completion boundary and scoped coverage integration. Full B3/M-B remains incomplete. The WEG/asset-report pages still need correction creation controls; selecting a corrected linked WEG plan is a subsequent integration increment. Cross-scope/page disclosure and the other remaining B3 requirements are not accepted here. No specialist or live-provider release gate is satisfied by these local checks.
