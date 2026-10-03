# Immutable exception evidence and advisory workflow

Migration 0102 adds the first reusable exception ledger for blocked manual assignment proposals. This is a B3 foundation, not acceptance of B3 or domain resolution.

A case is deduplicated by organization, target accounting scope, cause and extraction revision. Supported causes are conflicting intake scope hints and unsupported SEV scope selection. Each dependency retains the exact blocked proposal. Composite foreign keys bind organization, scope, extraction and case; a trigger additionally verifies that the cause appears in the proposal's blockers. Cases, dependencies and events are immutable.

The proposal transaction records each new dependency and an `evidence_added` event atomically. A migration backfill applies the same operation to historical blocked proposals. System evidence events have no human actor, request key or claim; the proposal retains its original actor and timestamp. Backfill does not invent historical review. Repeating the same dependency adds nothing. New evidence advances the case version, opens attention and clears any snooze while retaining an advisory claimant.

The interactive command supports claiming for oneself, releasing one's own claim, snoozing until a future timezone-qualified instant, and reopening. Every action needs a reason, immutable idempotency key and exact expected event UUID. Claims do not confer authority or create an exclusive financial lock. A currently authorized person can replace an advisory claimant with an explicit action and version check. No generic resolve command exists; `resolution_state` remains `unresolved` throughout. Snooze expiry changes the effective attention projection without rewriting history.

Commands require the actual interactive database role, current original prepare authority and current target read/prepare authority. Checks precede replay. Equivalent timezone representations have the same request digest. A historical replay returns its original receipt and does not reinstate its old workflow state. Source-security pause blocks writes, including replay. ACL and financial-input advisory locks serialize rights changes, proposal evidence and manual actions in the established order.

All case, dependency and event reads inherit current original-and-target access. Lists and continuation pages reveal no inaccessible counts; cursors bind actor, organization and workflow filter. The current private original audience remains in force. This foundation does not yet expand access to a second clerk. Retained dependencies are historical evidence; future typed adoption must evaluate the exact applicable proposal and case versions instead of treating every superseded case as a universal blocker.

The access-coverage manifest includes all three tables. Populated downgrade is refused; empty downgrade restores the earlier manifest definition in place. Runtime roles cannot directly insert, change or delete evidence.

## Qualification

Initial red API tests failed at the missing endpoint. Initial implementation: 10 API/RLS checks passed (`/tmp/hv-exceptions-first.log`). The expanded boundary run passed 24 checks and exposed three test-fixture mistakes (invalid source-security mode, repeated grant insertion, duplicate dependency masking the intended foreign-key check); those fixtures were corrected. The corrected qualification passed 12 checks, including historical migrations (`/tmp/hv-exceptions-qualification.log`). The expiry and atomic blocked-proposal rollback check passed (`/tmp/hv-exceptions-expiry.log`). Fake-Docker deployment-script regression passed for head 0102 (`/tmp/hv-exceptions-deploy.log`); no deployment occurred. Independent read-only review found no production blocker. Earlier passing checks were reused.

Still outstanding: exception UI, typed proposal review and adoption, financial allocations, duplicate decisions, mapping versions, and the other B3 acceptance requirements. B2 production scanner/runtime qualification remains open.
