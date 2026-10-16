# Isolated declaration review validation runtime

Status: implemented and verified locally. Baseline 6ce4f5a; migration 0073.

## Decision and trust boundary

Keep canonical correction parsing and complete graph validation in Python, behind
a dedicated app_review login used only by the two review mutation routes. Unlike
the worker role, this role does not inherit app_rw: give it explicit SELECT access
to the review graph and execution of narrow review commands only. It has no table
write privileges, ownership, elevated flags, parent memberships or delegates.
The ordinary web and extraction worker logins cannot assume it or execute review
commands. Missing configuration fails closed; no fallback to the ordinary pool.

This is a boundary against arbitrary SQL under app_rw/app_worker, not protection
against compromise of the API process or its review credential. The review runtime
is trusted for semantic computation, as the extraction worker is trusted for
provider processing. PostgreSQL independently enforces current actor/source access,
phase/revision, permitted effect shape, transaction binding and canonical audit;
it does not claim to independently execute or prove the Python validator.

## Atomic effects

The dedicated HTTP dependency derives tenant/actor from authenticated context and
rechecks the live writer under the ACL lock. Keep one transaction across graph
locks, canonicalization, candidate command, complete revalidation and final issue
command. Any failure rolls back decisions, revision, audit and issue changes.

The review decision command establishes a private receipt for the actual review
login, organization/import, actor, backend PID, transaction ID and resulting
revision. It privately retains pre-command resolution metadata. The issue command
requires that context and current authority. Existing issue effects can only change
OPEN/RESOLVED for the managed code set; identities, code, severity, message and
source context are immutable. Only the two deterministic allocation BLOCKER kinds
may be inserted, with database-owned messages and actor/time metadata. No deletions.
The command preserves previous resolution timestamps only using its private prior
metadata after the trusted validator reports the issue resolved again.

## Delivery checks

Raw web/worker correction and issue bypasses must fail. Exercise cross-import and
cross-tenant inputs, forged GUCs, missing/expired transaction context, stale revision,
role drift, private-table access, pool reuse, concurrent reviews, current authority,
invalid API correction and failure after the decision command. Preserve full review,
approval, managed issue and strict amendment HTTP behavior. Provision only disposable
test roles locally; update bootstrap/Compose/runbook and simulated deployment gates
without deploying. Activation stays false pending acceptance and remaining B1 work.

## Verified implementation

A narrow graph-lock command acquires row locks on the review role's behalf, so it
needs no UPDATE grants merely to issue SELECT FOR UPDATE. Runtime and migration
preflight reject unexpected table/column writes and reads outside the four review
graph tables, in addition to unsafe flags, memberships, ownership and public-schema
CREATE. Only the mutation routes use the new dependency; list/preview/approval keep
their existing ordinary connections. Config uses SecretStr and no fallback.

The repository converts pending ORM issue effects into a closed typed command under
no_autoflush, expiring existing rows and expunging unsaved inserts. PostgreSQL owns
identity, code/severity/message restrictions and resolution timestamps. The runtime
computes the semantic outcome; neither a caller GUC nor the ordinary login can
substitute for that runtime. Replays at current revision retain existing semantics;
durable request-key replay is still a separate API requirement.

Two red tests reproduced the ordinary-role correction and issue-resolution bypasses.
The first 32 tests passed existing review behavior and RLS. Expanded command/API and
adversarial coverage passed 66 cases. Full role/context/migration/approval/source and
worker-effect compatibility passed 150 cases. The final 42-case run passed direct
privilege drift, strict HTTP, private RLS and expiry-after-decision rollback: an
expired receipt returns HTTP 409 and leaves candidate, revision and audit unchanged.
No duplicate Python/SQL semantic engine was introduced.

A bounded architecture consultation recommended the separate trusted runtime for
the stated ordinary-SQL threat; independent code review found no actionable blocker.
The fake-Docker deployment regression, shell syntax and rendered synthetic Compose
credential separation passed. Head gate is 0073. No deployment, production role
provisioning or external mutation was performed. The current source activation
predicate remains false; remaining B1 work and M-B–M-G are still outstanding.
