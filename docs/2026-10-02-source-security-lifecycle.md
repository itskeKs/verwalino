# Explicit source security lifecycle

Status: locally verified 0091 increment after accepted 0090 (`9c7987c`).
B1 remains incomplete pending final integration qualification; no rollout occurred.

The next increment exposes administrative status and atomic transitions from
PREPARATION to ENFORCED, ENFORCED to PAUSED, and PAUSED to ENFORCED. No transition
returns to PREPARATION. Explicit expected mode, changed timestamp and predecessor
receipt prevent stale requests, including an ABA cycle returning to the same mode.
An immutable receipt binds the actor, request digest, predecessor and selected
coverage/disposition evidence. Replays return historical evidence without applying
the transition again, after checking the current administrator.

Activation/resumption requires a fresh actual-connection runtime attestation,
current reviewed checker-v3 coverage, exact accepted disposition/digests, no blockers
and no active work. The selected snapshot must be newer than the last state change;
mode and reason alone cannot distinguish successive pause/resume cycles. The
command holds exclusive ACL then financial-input locks throughout validation,
receipt insertion, state change and existing state-change audit.

Readiness is private and transaction-bound: only the current command's private
ENFORCED authorization for the actual backend PID/xid and current actor can
satisfy the trigger. Its expected prior mode/timestamp must still match. Selected evidence is checked
once before minting authorization. Authorization is deleted within the command;
the final immutable receipt records the resulting state timestamp. Later commands
compare live state with that receipt to reject unlogged divergence. The receipt and
state commit together or neither does. A mutable GUC
or a historical accepted snapshot cannot independently authorize a transition.
Transition receipts are not added to the coverage manifest, avoiding a circular
staleness dependency during the command.

Pausing requires the current administrator and exact prior state but does not
require budget attestation or coverage availability. It preserves strict reads and
stops effects according to the accepted runtime gates. Status is likewise readable
without the budget registrar. New organizations follow the same explicit empty-org
budget/team review and activation sequence; no automatic role-derived grants or
organization mode changes are introduced.

Verification covers empty-org bootstrap, retained strict denial, current budget and
source drift, old evidence after state cycles, cross-org/current-role denial, raw
runtime and cross-transaction proof rejection, replay after pause, concurrent same
and different keys, audit rollback, pause on registrar failure, private evidence,
migration round trips and strict/populated downgrade refusal. Existing synthetic
mode tests must restore the exact prior readiness definition before integration.

## Local acceptance evidence

The API and administrative UI implement explicit selected-evidence activation,
independent pause and fresh-evidence resumption. The UI separates historical
receipts from the current mode, retains a stable request after response loss,
requires a reason and new acknowledgment, and resets all confirmation state on
selection changes, including a newly loaded object for the same acceptance ID.
Historical inventory mode is labeled as belonging to its collection time.

Backend: two expected missing-route failures preceded implementation, then the two
initial lifecycle tests passed. The final regression run passed all 60 tests across
lifecycle, existing source enforcement, retained disposition and coverage. Tests
include empty-organization activation and explicit first-property audience, retained
original denial, pause/read-versus-write behavior, source/budget/work drift, raw and
cross-transaction proof rejection, concurrent keys, current-role/cross-tenant denial,
replay after pause and registrar outage, repeated-mode snapshot rejection, audit
rollback, private tables/functions, guarded downgrade and exact function restoration.
Timezone-qualified request timestamps normalize to UTC microseconds; equivalent
instants replay across Honolulu/Tokyo sessions. Unlogged state changes conflict even
when the caller has observed their latest timestamp. The first expanded attempt
found a disposable-reset FK dependency and an older migration fixture retaining
synthetic budget rows; both fixture issues were repaired before the passing run.

Frontend: 21 component tests passed, including delayed same-ID reselection changing
the displayed mode without carrying the previous acknowledgment. Lint and production
build passed; the existing large-bundle warning remains. The fake-Docker deployment
contract passed at head 0091. Local browser evidence passed activation with a lost
response and exact replay, pause, stale-evidence rejection, fresh resumption, reload,
and 390px layout without overflow or JavaScript errors. Desktop/mobile screenshots
were inspected. A targeted browser follow-up verifies same-ID reselection clears
acknowledgment and requires a new explicit confirmation. Independent reviews found
and resolved timestamp normalization, same-ID acknowledgment reset and contradictory
UI wording; no remaining production finding was reported.

Evidence: `/tmp/hv-source-lifecycle-{red,first,regression,ui,lint,build,deploy,browser,browser-reselection}.log`.
Only the disposable local database was activated. Existing preview/disposition
`activation_available=false` fields remain non-authoritative: activation is a
separate command that independently verifies its complete selected evidence.
B1 end-to-end qualification and B2–G remain outstanding.
