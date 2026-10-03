# Durable extraction of logical intake sources

Migration 0098 queues each logical intake source exactly once, including existing
sources. The separate extraction stage preserves inspection-only queue semantics.
The shared runner selects it explicitly with `intake_extraction`; production
compose is unchanged. Source candidates and proposals remain unreviewed and do
not create or confirm financial entries.

Each job records the logical creator and original batch scope. Worker commands
restore that identity and check current prepare/original access before reading
bounded stored bytes and again before completion. Source hash and byte size are
checked before parsing. Revocation or pause cancels the attempt; no result is
committed. Private job, attempt and retry tables have forced restrictive RLS and
no ordinary runtime DML. Only the actual worker login can claim or complete.

Claims use short transactions, 180-second leases and fresh fencing tokens, with
at most three lifetime attempts. This leaves margin around the isolated local
processor's 120-second deadline. Expired attempts are reconciled before reclaim.
History, located fields, completion and the extraction outbox event commit in one
transaction. Cancellation drains only active bounded processor work; database
waits remain cancellable through the existing shared helper.

`extraction_revision` now has mutually exclusive typed invoice-attempt and
logical-intake-attempt arms. Existing invoice history remains readable. Located
field evidence retains page quotations or standalone XML element paths, extraction
method and unverified validation status. The original's current RLS governs both
history and fields. Database completion checks source identity, selection, field
types, confidence and locator shape/quote membership; semantic parsing stays in
the isolated processor. No copied storage URL is exposed by history or status.

The logical processing endpoint exposes current state and live retry availability.
Retry requires current authority, a reason, the expected fence and an idempotency
key. Exact replay returns the retained receipt without restarting completed work.
Failed/unavailable/timed-out/canceled work can retry within its lifetime budget;
successful or reviewable proposals cannot silently be replaced. All retained
history and retry evidence participates in access coverage; queued/running work
blocks disposition acceptance. Populated downgrade refuses evidence deletion.

Source history retains its bounded latest-200 view. A validated
`logical_document_id` filter retrieves every revision for one child (at most
three), including older children beyond that source-wide limit. A child from
another source is rejected.

## Local evidence

An initial implementation check exposed a PL/pgSQL variable/column ambiguity;
renaming the identity loop variable repaired it. Six initial integration checks
then passed. The expanded suite passed 64 checks and exposed one XML test fixture
that supplied item metadata at the batch level. The corrected fixture and history
filter were qualified with all 23 intake/history checks passing. Independent
review prompted the history filter and found no remaining concrete blocker after
its focused re-review.

Ten final boundary/shared-runner checks passed, including actual worker-role
malformed SQL completion attempts, atomic rollback, stale replay and active-work
gating. Other checks cover concurrent claims, lease exhaustion, permission
revocation during extraction, stored-byte corruption, exact retry replay, XML
locators, guarded downgrade/backfill with preserved function identity, RLS and a
201-revision discovery fixture. Unchanged successful registry/intake/outbox/RLS
checks from the expanded run were reused. The fake-Docker deployment probe passed
with head 0098; no deployment or provider call occurred.

The inbox still needs to display this stage and its proposal history. Full B2
workflow/runtime qualification and real malware-scanner qualification remain
outstanding. This milestone does not accept B2 or the implementation plan.
