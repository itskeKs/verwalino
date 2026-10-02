# Shared workflow dispatch adapters

The optional `app.workers.workflow_runner` entry point dispatches intake
inspection, ordinary invoice extraction and declaration imports through one
serial, rotating worker loop. Existing queues, attempts, leases and provenance
remain authoritative. No jobs are copied into a second queue and no schema
migration is needed for this adapter increment.

## Operation

The CLI requires explicit `--stage` selections and either one or more
`--organization` arguments or `--all-organizations`. `--once` stops after at most
one non-idle stage result. A stage with no work does not block other stages.
Organizations and stages rotate after each attempted slot, including failures.
Duplicate organization/stage selections are deduplicated. Multiple concurrent
ticks on one runner remain serialized.

The invoice adapter uses the existing attempt budget and fencing. A live legacy
lease is left alone; an expired attempt is reconciled by the existing claim code
before a new attempt runs. Old completions cannot overwrite the reclaimed work.
The declaration adapter adds an explicit organization restriction to the existing
worker without changing its legacy global-discovery entry point. Its schema
guard, lease heartbeat, run budget, permission cancellation and pipeline remain
in force. The provider is constructed lazily only for an explicitly selected,
enabled declaration stage. No provider request occurs during module import or
adapter construction.

The shared dispatcher holds no database transaction around processor work.
Adapter and organization-discovery failures emit fixed operational codes rather
than exception text. Schema-not-ready remains a visible stage result. Local
inspection/OCR processors drain their shielded thread work on cancellation because
canceling a thread await does not stop its subprocess. The dispatch slot stays
held until the processor completes; repeated cancellation does not release it
early. The boundary wraps only the processor call; queue/database waits remain
cancellable, and a cancelled processor's result is not committed. Graceful
shutdown can wait for the remaining local processor deadline (inspection 90
seconds; invoice extraction 120 seconds). The declaration adapter retains its cooperative
pipeline/heartbeat cancellation cleanup.

## Verification and limits

The first test invocation was rejected by the disposable-database environment
guard before any test ran; the corrected invocation passed three dispatcher
tests. The expanded declaration/shared-worker suite passed 90 tests and exposed
two later global-worker failures caused by this test's deliberately unclaimed
foreign fixture. Marking only that synthetic fixture terminal after its isolation
assertion restored the ordered shared/declaration slice: 27 passed.

Independent review identified cancellation of a live thread as a hole in the
serial guarantee. The drain boundary and an actual blocking-thread test were
added. The initial repair shielded the entire adapter; focused review found that
this could ignore cancellation during an unbounded database lock wait. The
shared helper now wraps only the actual processor thread in the two local
workers, with a real advisory-lock cancellation test. The final runner, intake
and invoice suite passed 39 checks. The cleanup-only lock-test follow-up passed
one check. Focused independent review found no remaining concrete blocker. Existing
successful declaration metadata/effect/command checks remain applicable because
the repair changes only local processor cancellation and fixture cleanup.

The integration checks exercise an actual local invoice processor, preserving a
live lease and then reconciling an expired attempt with one committed extraction
revision, plus a real declaration claim with an injected local pipeline proving
that another organization's queued work is untouched. No live provider or
deployment was used. The original entry points and production compose command
remain available; enabling the shared runtime in production is not accepted by
this increment. Scanner qualification, common intake extraction/outbox consumers
and G1 capacity/recovery qualification remain outstanding.
