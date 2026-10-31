# Inbox review chain

The inbox preserves the uploaded original. Processing proposes fields; only
explicit human review and final adoption create confirmed costs. This page owns
the operational flow for [issue #35](https://github.com/itskeKs/verwalino/issues/35).

## Flow and ownership

| Step | API / worker and durable records |
|---|---|
| Upload | `POST /api/intake-batches` declares the manifest; `POST /api/intake-batches/{batch}/items` receives exact bytes. [IntakeService](../../backend/app/services/intake_service.py) stores the original and calls the guarded database commands. Records: `intake_batch`, `intake_item`, `intake_upload_admission`, `intake_original`, `workflow_job`, `outbox_event` (`intake.received`). No cost exists. |
| Inspect | [workflow.process_once](../../backend/app/workers/workflow.py) claims `workflow_job` with a fence and records `job_attempt`. [Inspection](../../backend/app/core/intake_inspection.py) scans the exact original with ClamAV and checks structure, MIME and bounds. A clean completion creates `document_ref` and `intake.inspected`; blocked/rejected originals remain quarantined. |
| Deliver and extract | [outbox.process_once](../../backend/app/workers/outbox.py) claims `outbox_delivery` and creates the initial `logical_document` plus `intake_extraction_job`. [intake_extraction.process_once](../../backend/app/workers/intake_extraction.py) records `intake_extraction_attempt`, `extraction_revision`, `field_evidence` and `document.extracted`. Text PDFs/XML use the existing deterministic local parser; image-only documents need local German OCR. Proposals remain unverified. |
| Review | The reviewer reads the original, confirms its [partition](../../backend/app/api/source_partitions.py), selects an [assignment](../../backend/app/api/intake_items.py), corrects [invoice fields](../../backend/app/api/invoice_reviews.py), compares [context](../../backend/app/api/context_decisions.py), and reviews [allocation](../../backend/app/api/allocation_proposals.py) and [treatment](../../backend/app/api/allocation_treatments.py). Records: `source_partition_revision`, `assignment_proposal`, `invoice_review_revision`, `context_decision`, `allocation_proposal`/`allocation_proposal_line`, `allocation_treatment_review`/`allocation_treatment_line`. These steps create no financial entries. |
| Adopt | `GET /api/allocation-treatments/{review}/adoption-preview`, then `POST /api/logical-documents/{logical}/allocation-adoptions` with the current preview digest and explicit cost/original-disclosure acknowledgements. [AllocationAdoptionService](../../backend/app/services/allocation_adoption_service.py) verifies the original's hash/size and attests integrity through `app_review`; guarded adoption atomically creates `allocation_set`, confirmed `cost_entry`, the released `invoice_document` and `document_cost_link`. Values come from the reviewed revisions. |

The [runner](../../backend/app/workers/workflow_runner.py) defaults to exactly
`intake_inspection`, `outbox`, `intake_extraction`. It discovers organizations
each round and serially rotates organization/stage pairs, polling every two
seconds. One `run_once()` dispatches at most one non-idle stage; `--once` is one
such tick, **not** a complete document pipeline. `--stage` can explicitly select
other existing adapters; the inbox service never schedules invoice/declaration
extraction or contacts a provider.

Claims and completions use the existing tenant-scoped SQL functions, fenced
leases and bounded attempt budgets. An interrupted lease can be reclaimed;
completed jobs are not duplicated. A scanner/processor failure is parked with a
structured error, not treated as success. Repair the dependency, inspect the
item's current fence, and use `POST /api/intake-batches/retry` or
`POST /api/logical-documents/{logical}/retry-extraction` when `can_retry` allows it.
Refusing the final confirmation creates no cost and leaves the review pending;
there is no separate persisted review-rejection command.

## Run and verify

Service/profile, signature maintenance and environment are documented in
[DEPLOY.md](../../DEPLOY.md#inbox-worker). No runtime provider secret is needed.
The worker needs the isolated `HV_DATABASE_URL_WORKER` role and the same
`HV_DOCUMENT_STORAGE_ROOT` as the API, mounted read-only. Its startup/healthcheck
checks the worker role and queue access, storage and scanner availability without
claiming jobs. SIGTERM drains bounded processing; unfinished work keeps its lease.

For a synthetic local observation, first acquire `/tmp/verwalino-testdb.lock` as
described in [CONTRIBUTING.md](../../CONTRIBUTING.md#running-the-tests), load
`scripts/test-env.sh`, and run `scripts/dev-local.sh` against `:55432`. The API is
`:18000`, Vite is `:13000`; never use the production API on `:8000`. Stop the
launcher and all its children before releasing the lock. The inbox worker needs
ClamAV with readable, current signatures; missing signatures must not be faked.

The executable [scenario](../../features/inbox_review_chain.feature) uploads a
synthetic text invoice, drives real runner ticks in-process, reviews a corrected
amount and checks the confirmed cost through HTTP. It reuses the existing clean
inspection double; the real local text parser runs with OCR forbidden. This is
deterministic API regression coverage, not a real-scanner deployment test.
