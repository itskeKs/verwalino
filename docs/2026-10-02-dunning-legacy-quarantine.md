# Explicit quarantine disposition for unclassified dunning history

Status: accepted local increment; baseline cfbccd0, migration 0080. B1 remains incomplete.

Unknown legacy dunning context cannot be reconstructed from recipient names,
amounts or today's scope assignments. Provide administrators a bounded opaque
inventory and an explicit permanent quarantine decision on the current row digest.
The decision records current actor/time/reason and freezes the row without changing
its legal snapshot, recipient, financial amount, status or provenance. It grants
no scope or content access and prevents later insertion of invented scope context.

Retained issued notices remain inputs to dunning-history completeness and privacy
retention checks. Reviewed quarantine must never reset escalation history or make
retained documents disappear from privacy blockers. Existing PREPARATION behavior
for not-yet-reviewed rows remains; strict source access still denies unknown context.
After explicit quarantine, ordinary metadata/PDF/audit access is denied in every
mode. New properly scoped notices and current grants continue unchanged.

Use shared ACL then financial-input lock order before locking the row. Private
append-only decisions have no runtime table privilege; narrow commands require
actual app_rw and current administrator authority. UUID request replay checks live
authority and exact digest/reason; one decision per notice. Page default50/max200,
signed tenant/actor cursor. UI confirmation explains the persistent escalation and
retention blockers and offers no admission, deletion or historical PDF recovery.

Acceptance: stale digest rejects mutable draft races; concurrent same-key replay
returns one receipt/audit; no reparenting, metadata/PDF/audit leak or raw mutation;
issued legacy remains an escalation/privacy blocker after review; negative tenant,
role and worker access; current scoped dunning remains valid; populated downgrade
refuses evidence loss; lost-response retry and responsive browser journey.


## Verification

Two initial HTTP cases failed before implementation. The initial 20-case run and
final expanded 58-case backend run passed on disposable PostgreSQL 16 through the
runtime role. Coverage includes stale digest rejection, one receipt/audit under
concurrent replay, altered-body conflict, bounded signed paging, tenant/admin/worker
fences, ordinary PDF/audit hiding, row/context immutability, retained issued-history
and privacy blockers, current scoped creation and populated downgrade refusal.
The activation predicate remains false.

Eleven frontend quarantine/preparation tests passed. Lint, production build,
shell/deployment simulation passed; the existing bundle warning is approximately
641 KB. Independent static review found no actionable issue. Local Chromium passed
explicit confirmation, loss of a committed response followed by identical key and
receipt replay, persisted disposition after reload, failed-refresh clearing and
recovery, and desktop/mobile rendering without page errors or horizontal overflow.
Both captures were visually inspected. No provider, deployment or live data was used.

Operator entry: access preparation → old dunning review (`/mahnquarantaene`).
Quarantine is a recorded administrative decision, not historical admission or a
resolution of the remaining B1 activation and wider M-A–M-G acceptance gates.
