# Source audit disclosure

Status: implemented locally; 153 integration checks passed; independent review accepted without actionable findings.
Migration: 0065. Baseline: 808d178. Source activation remains unavailable.

## Boundary

Audit payloads can retain copied source text after the original row becomes
unreadable. Organization isolation and financial classification alone do not
protect that text. Strict audit SELECT now resolves an explicit typed resource
registry through the resource's ordinary RLS policies. This is a SECURITY INVOKER
function with fixed search path and row security enabled. It cannot use the audit
creator or an administrator role to bypass source access. Canonical facts,
originals, import review children and stored Q&A retain their existing source
rules. Revocation, expiry, quarantine and incomplete graphs therefore apply to
historical audit copies as well. Authorized reads remain possible in PAUSED mode.

Financial output audits require the complete property source graph even when
an empty draft parent is otherwise readable. Existing financial scope, mandate,
dunning, reconciliation and effect-control audit policies remain restrictive.
A missing or foreign reference and an unknown resource kind fail closed. No
history or payload is rewritten. Missing resources do not acquire an implicit
historical reader; a future archival disclosure workflow needs its own contract.

Configuration audit kinds require current administrator authority and an existing
same-organization configuration record. Internal source-security and knowledge
budget tables retain their lack of direct app SELECT permission. A separate,
closed two-kind boolean helper verifies only existence and administrator access;
it returns neither source content nor configuration records.

## Append behavior and migration

AuditRepository flushes pending business changes, then appends with an inline Core
INSERT without RETURNING. The database still supplies event time and enforces
append-only and tenant rules. Appending a cancellation or future typed event does
not depend on being able to read it afterward. Existing callers discard the
return value; create now explicitly returns no result. Ordinary audit reads
continue through RLS. No broad definer read or extra table privilege is added.

The migration locks audit, control and supported resource dependencies with NOWAIT.
Rollback is refused while any organization is ENFORCED or PAUSED. A preparation
round-trip preserves the full serialized audit history. PREPARATION retains the
previous compatibility audience.

## Evidence and remaining work

Four initial regression failures demonstrated audit leaks for another creator,
revoked canonical sources, missing references and unknown source kinds. Eighteen
focused audit/original-source tests then passed. A control-metadata regression
exposed the intentionally absent direct table SELECT grant; the bounded helper
addresses that case. The final integrated run passed 153 cases across source audits, originals, derived
rows, privacy completeness, financial disclosure, M2 receipt flows and M8 meetings.
The disposable DB initially contained the earlier uncommitted migration without
the new helper; its downgrade failed at setup. Tolerant function removal supports
that development state, and the subsequent clean round-trip and integration passed.
Logs: `/tmp/hv-source-audit-{red,first,controls-red,integrated,integrated-repaired}.log`.

Person/history and meeting audit kinds intentionally remain unavailable in strict
mode until those source boundaries are implemented. This is a preactivation
restriction, not completed functionality for those areas. Audit writes remain
possible. Their ordinary tables, indirect reports and worker lifecycle integrity
still require integration before B1 activation. B2–G remain outstanding.
