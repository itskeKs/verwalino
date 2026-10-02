# Explicit access grant proposals

Baseline: ddb4d92. Migration 0084 adds snapshot-bound administrative proposals.
Accepted local increment. This increment does not activate
source enforcement or establish complete projected strict readiness.

An administrator first inspects an immutable coverage snapshot, then specifies up
to 100 exact audience/member changes. Financial, property and contact audiences
remain separate. Every change states the prior unrevoked grant ID (or explicitly
no grant), the complete replacement capability set, and an optional absolute expiry.
An empty capability set means explicit revocation. Roles never create proposals or
grants automatically. The UI shows replacement semantics, previous capabilities
and IDs, and the intended new rights before a separate acceptance acknowledgment.

Saving a proposal creates no authority. Acceptance rechecks the current administrator,
the unchanged snapshot and all target principals/predecessors under the exclusive
organization ACL lock before financial-input locks. All revocations, replacement
grants and the immutable acceptance receipt commit together. The proposal digest
binds normalized targets, capabilities and absolute UTC expiries. Relative dates
and timezone-less timestamps are rejected by the database command itself.

Capture/proposal/acceptance keys remain separate. The same acceptance key returns
its original receipt even after a resulting grant is revoked; it never recreates
that grant. A changed request under the same key conflicts. A different acceptance
against an already accepted or stale proposal conflicts. Reload recovers the saved
proposal and receipt without relying on a browser-held key. The receipt records a
past change, not a promise that its grants remain effective today.

Private immutable proposal and acceptance tables have forced restrictive RLS and
no runtime table privileges. Only current app_rw administrators can use the command
surface, including replay and readback. Audit records attach to the existing
admin-only coverage snapshot. Retained grants remain visible with empty effective
capabilities after membership removal, so an administrator can revoke them without
restoring membership. Creation/replacement still requires active membership and
capabilities allowed by its current role.

A populated downgrade is refused. Empty downgrade/upgrade preserves historical
0083 behavior. Existing grant APIs remain available; using them invalidates pending
snapshot-bound proposals through the evidence fingerprint. This milestone supplies
explicit grant-change acceptance, not acceptance of unknown provenance, historical
source ownership or unresolved combined input authority. A fresh snapshot is needed
after acceptance. Activation, bootstrap and strict pause/resume remain outstanding.

## Verification

The initial test attempt was blocked by a missing imported fixture and then a SQL
CASE syntax error; neither is counted as a functional red test. The first working
API run passed two cases. Expanded proposal/coverage/RLS checks passed 23 cases.
Independent review identified ambiguous direct-command timestamps, a reload/mutation
race and retained grants hidden after membership removal. These were repaired with
explicit DB normalization, mutation blocking during reload and retained-grant
visibility. The expanded repaired run passed 26 cases; its injected-failure test
needed a qualified table name under the definer search path. The final focused
11 proposal tests passed, including actual partial-write rollback, concurrent
acceptance replay and differing Honolulu/Tokyo session timezones. Earlier valid
coverage/RLS results are reused; shared data visibility changed only as described.

Ten component/page tests passed, including durable retries, stale errors, failed
read recovery and a delayed reload. Frontend lint and build passed; the existing
bundle warning remains (approximately 660 kB). Fake-Docker deployment checks passed
with the expected head advanced to 0084; no deployment occurred. Independent
read-only review confirmed the repaired delta, with no remaining blocker in this
proposal scope.

Chromium exercised explicit changes in all three domains, proposal and acceptance
replay after aborting committed responses, mandatory acknowledgment, receipt reload
and the 390px mobile layout. Desktop/mobile screenshots were inspected and no page
errors or horizontal overflow were observed. The first harness attempt used an
overly exact label selector for a wrapped native select; using its text label
resolved it without a product change. API/Vite processes were stopped afterward.
