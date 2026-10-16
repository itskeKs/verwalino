# Meeting source access

Status: local implementation verified and independently accepted. Migration 0066 follows
7d846aa. Source activation remains disabled; this is part of B1, not E5 completion.

## Source and effect contract

Meeting metadata, agenda text, resolution wording and immutable register copies
share the property's source audience. Strict CRUD policies cover all four tables.
READ requires a current property READ grant and the complete expected source
graph. Ordinary meeting/agenda/resolution edits require PREPARE; entering or
annotating an immutable register entry requires APPROVE. Organization role alone
confers none of these capabilities. PAUSED retains authorized reads and exports
while denying effects. Existing register immutability and deletion protections stay
in force. Audience and parent identity fields cannot be reparented, even in
compatibility mode; the optional agenda association remains mutable only within
the same meeting under strict validation.

A private trusted boolean validates the expected meeting graph, including tenant
consistency and resolution/agenda/meeting/register-property agreement. It reads
actual parent relationships without RLS filtering or payload projection. Unknown
or mixed historical relationships fail closed for the property's meeting graph;
they are preserved for reviewed remediation. Strict writes validate proposed row
metadata, supporting first INSERT RETURNING without relying on an already visible
row. Shared organization ACL locks precede statement effects.

## API and exports

HTTP readers and writers revalidate live membership in the data transaction.
Service preflights return a bounded 404 before loading inaccessible records or
performing effects. Table policies also protect direct SQL. Protocol and register
PDF routes require whole-source EXPORT as well as READ before gathering records,
before rendering, and before releasing the rendered bytes. A missing property or
hidden/incoherent child graph cannot become a misleading partial or empty PDF.

Typed meeting audit events become readable only under the corresponding source
predicate. The wrapper explicitly updates the audit policy after renaming its
prior function because PostgreSQL policies preserve function OIDs. PREPARATION
retains the former audit behavior, including deleted entity history. Strict missing
entity audit history remains hidden pending a distinct archival disclosure contract.

## Verification and limits

Four initial red cases reproduced unauthorized ordinary reads, writes with a read
only grant, and both PDF export bypasses. The first focused suite passed 46 tests,
including existing M8 lifecycle, immutability and PDF behavior plus audit regression
coverage. The expanded suite passed 51 cases covering strict lifecycle, audit reads, pause,
revocation, approval-only register effects, mixed legacy graphs, raw reparenting
denial and grant expiry during export discovery. A separate run passed 45
migration/RLS/RBAC/tenant-isolation cases, including full meeting/audit history
preservation across a preparation round-trip, strict rollback refusal and all
seven NOWAIT dependencies. Independent review accepted the bounded change without
actionable findings. No frontend changes were needed. Logs:
`/tmp/hv-meeting-source-{red,first,expanded,migration}.log`.

This does not implement voting/proxy legal rules or complete E5. Person/profile/
history and indirect reports, worker lifecycle hardening, reviewed legacy coverage
and the activation gate remain open, as do later implementation milestones.
