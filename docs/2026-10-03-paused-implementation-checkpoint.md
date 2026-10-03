# Paused implementation checkpoint

Work is paused at the user's explicit request on 2026-10-03. Do not resume implementation, validation, deployment or background monitoring without a new user instruction.

## Last accepted milestone

Commit `dee9657` implements migration 0122: reviewed whole-original source replacement in the same property and accounting scope. It retains both source claims and original files, atomically replaces all costs, preserves the financial version chain and inherited follow-ups, and supports exact historical retry. Earlier accepted commits are `0239bbc` (0121 visual financial sources) and `1a44af6` (0120 physical source aliases).

Qualification is recorded in `2026-10-03-allocation-source-replacements.md`: twelve new DB/API cases across focused runs, 142 shared regression passes plus the repaired historical-fixture recheck, eighteen additional historical-chain passes, component tests, build/lint, fake deployment probe and actual desktop/mobile browser qualification. There was no push or deployment. Local API/Vite servers were stopped.

## Saved work in progress — migration 0123

The next increment adds a typed exception outcome for sources that have been replaced. This work is saved for continuation but is **not an accepted milestone**.

- New migration `backend/migrations/versions/0123_replacement_exception_outcomes.py` discovers sealed replacement chains without redefining physical equality. It retains the old allocation plus foreign keys to the financial head and replacement receipt, expands the full related-source component before checking visibility, and makes completion applicability depend on the exact current chain and review heads.
- `backend/app/services/exception_service.py` returns retained replacement links in historical completion receipts.
- `frontend/src/api/exceptions.ts` and `frontend/src/components/intake/ExceptionCompletionPanel.tsx` distinguish confirmed replacement from a same-document copy.
- `backend/tests/test_replacement_exception_outcomes.py` covers revised amounts, context/duplicate outcomes, later heads, historical replay, changed old-source reviews, unknown raw copies, authorization, a deliberately corrupted completion member and preservation of ordinary completion basis across downgrade/upgrade.

The initial two DB/API cases passed. The expanded run `/tmp/hv-replacement-outcomes-security.log` finished with **6 passed, 1 failed**. The failing case is `test_replacement_completion_live_audience_and_atomic_seal`: the deliberately corrupted member is rejected at transaction commit by `validate_exception_completion` with `CheckViolationError: Exact completion source and adoption membership required`. The test expected an HTTP 422, while the deferred commit exception propagated through the test client. No diagnosis or repair was started after the pause request. The remainder of that test, including its successful retry and later revocation assertions, has not been qualified.

Independent read-only review found no concrete blocker in the inspected implementation. It identified qualification still needed for an inaccessible intermediate replacement, exact comparison of a copy of an already replaced source, later-head/review invalidation and preservation of ordinary completion basis. The expanded tests cover the latter two; the intermediate/copy cases remain open.

## Resume checklist

1. Inspect the failing deferred-seal/API transaction behavior and choose the appropriate assertion or implementation repair based on actual commit/response semantics. Preserve atomic rejection; do not weaken the seal.
2. Add the inaccessible-intermediate and replaced-copy cases, retained-evidence downgrade refusal, focused UI tests and any remaining boundary checks.
3. Advance explicit historical migration test chains and deployment head gates from 0122 to 0123. They have deliberately not been advanced yet.
4. Run proportionate backend, migration, frontend and actual browser qualification. Migration 0123 has no completed frontend build/lint/browser qualification or accepted broad regression receipt.
5. Document and commit a verified milestone only after the remaining checks pass. Continue the wider plan only on the user's instruction to resume.

The disposable local PostgreSQL database is at head 0123 following the focused test run. Backend pytest resets that database at session start; run database tests serially with explicit URLs. No task-owned server or test process remains active. Keep `USER` excluded from child processes and also remove `HV_OPENAI_API_KEY` for tests and local qualification.

B3, M-B and the overall M-A–M-G plan remain incomplete. Beyond this increment, open scope includes visual/cross-scope replacement, a conserved single-invoice allocation across multiple scopes, typed credit-note relationships and remaining exception workflow actions. User-owned untracked `docs/research/` remains untouched.
