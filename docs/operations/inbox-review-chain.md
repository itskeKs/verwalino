# Inbox review chain

The inbox preserves the uploaded original. Processing proposes fields; only
explicit human review and final adoption create confirmed costs. This page owns
the operational flow for [issue #35](https://github.com/itskeKs/verwalino/issues/35).

## Flow and ownership

| Step | API / worker and durable records |
|---|---|
| Upload | `POST /api/intake-batches` declares the manifest; `POST /api/intake-batches/{batch}/items` receives exact bytes. [IntakeService](../../backend/app/services/intake_service.py) stores the original and calls the guarded database commands. Records: `intake_batch`, `intake_item`, `intake_upload_admission`, `intake_original`, `workflow_job`, `outbox_event` (`intake.received`). No cost exists. |
| Inspect | [workflow.process_once](../../backend/app/workers/workflow.py) claims `workflow_job` with a fence and records `job_attempt`. [Inspection](../../backend/app/core/intake_inspection.py) scans the exact original with ClamAV and checks structure, MIME and bounds. A clean completion creates `document_ref` and `intake.inspected`; blocked/rejected originals remain quarantined. |
| Deliver and extract | [outbox.process_once](../../backend/app/workers/outbox.py) claims `outbox_delivery` and creates the initial `logical_document` plus `intake_extraction_job`. [intake_extraction.process_once](../../backend/app/workers/intake_extraction.py) records `intake_extraction_attempt`, `extraction_revision`, `field_evidence` and `document.extracted`. A structured original (XRechnung, ZUGFeRD/Factur-X) uses the deterministic local parser and never reaches a model; an unstructured PDF, PNG or JPEG is read by the organization's two models ([below](#reading-with-the-organizations-models)). No Tesseract, no pattern matching. Proposals remain unverified. |
| Review | The reviewer reads the original, confirms its [partition](../../backend/app/api/source_partitions.py), selects an [assignment](../../backend/app/api/intake_items.py), corrects [invoice fields](../../backend/app/api/invoice_reviews.py), compares [context](../../backend/app/api/context_decisions.py), and reviews [allocation](../../backend/app/api/allocation_proposals.py) and [treatment](../../backend/app/api/allocation_treatments.py). Records: `source_partition_revision`, `assignment_proposal`, `invoice_review_revision`, `context_decision`, `allocation_proposal`/`allocation_proposal_line`, `allocation_treatment_review`/`allocation_treatment_line`. These steps create no financial entries. |
| Adopt | `GET /api/allocation-treatments/{review}/adoption-preview`, then `POST /api/logical-documents/{logical}/allocation-adoptions` with the current preview digest and explicit cost/original-disclosure acknowledgements. [AllocationAdoptionService](../../backend/app/services/allocation_adoption_service.py) verifies the original's hash/size and attests integrity through `app_review`; guarded adoption atomically creates `allocation_set`, confirmed `cost_entry`, the released `invoice_document` and `document_cost_link`. Values come from the reviewed revisions. |

The [runner](../../backend/app/workers/workflow_runner.py) defaults to exactly
`intake_inspection`, `outbox`, `intake_extraction`. It discovers organizations
each round and serially rotates organization/stage pairs, polling every two
seconds. One `run_once()` dispatches at most one non-idle stage; `--once` is one
such tick, **not** a complete document pipeline. `--stage` can explicitly select
other existing adapters; the inbox service never schedules invoice/declaration
extraction, and only the extraction stage can reach a provider, for an unstructured original and under the
organization's opt-in.

Claims and completions use the existing tenant-scoped SQL functions, fenced
leases and bounded attempt budgets. An interrupted lease can be reclaimed;
completed jobs are not duplicated. A scanner/processor failure is parked with a
structured error, not treated as success. Repair the dependency, inspect the
item's current fence, and use `POST /api/intake-batches/retry` or
`POST /api/logical-documents/{logical}/retry-extraction` when `can_retry` allows it.
Refusing the final confirmation creates no cost and leaves the review pending;
there is no separate persisted review-rejection command.

## Reading with the organization's models

The extraction stage (issue #501, [ADR 0017](../adr/0017-model-document-reading-replaces-tesseract.md)) shares the invoice
path's core ([invoice extraction](invoice-extraction.md)): processor `intake-model-v1`, versions `invoice-v2` / `invoice-2`.

- **Local stage.** The isolated child ([core/intake_extraction.py](../../backend/app/core/intake_extraction.py)) checks the
  original, parses a structured one and, for an unstructured one, hands the text layers and sizes of the selected pages of the
  logical document to the worker. A part of a structured PDF ends `needs_review` / `structured_selection_partial`; it is never
  sent.
- **Model stage.** [IntakeModelPipeline](../../backend/app/declarations/intake_pipeline.py) reads the pages without a text layer
  with the reading model and fills the invoice proposal with the extraction model. Only the selected pages of that logical
  document are sent, with the original's page numbers, and nothing else of the original or of another document. Each request
  is reserved before it leaves and settled in `invoice_model_call` (`intake_attempt_id`, migration 0198), within the invoice
  path's bounds (10 pages, 60,000 characters, 300,000 input and 64,000 output tokens, 0.25 USD per document) and the monthly
  limit; the opt-in, the destination and the models are read again before every request. A PNG or JPEG is decoded for the
  reading model in a killable child with an address-space limit and the shared deadline
  ([core/image_reading.py](../../backend/app/core/image_reading.py)), never in the worker process; the pixel bound is checked
  from the header before any decode.
- **Evidence.** A value is located only if its page is one of the document's selected pages, its quotation occurs in the
  text read from that page (`method` `text_layer`, `reading_model`, `xml`) and the quotation carries the value
  (`passage_carries` in [core/intake_extraction.py](../../backend/app/core/intake_extraction.py)): a text as a substring without
  regard to case and spacing, an amount as its digits with either decimal separator, thousands separators, currency and its
  sign (312.00 is „312,00 EUR“, „€ 312,00“), a date by day, month and year in ISO, German numeric or month-name spelling
  (2026-01-15 is „15.01.2026“, „15. Januar 2026“); the suggested category is a choice from the organization's list and not
  printed, so only its place is checked. When in doubt a value is stored `unlocated`. The source page
  shows page and passage per value and the card „Verbrauch der KI-Verarbeitung“
  (`GET /api/logical-documents/{id}/model-usage`, over all attempts).
- **Opt-in and confirmation.** Nothing is sent unless the organization's AI import switch, key, reading and extraction models
  and a confirmation that names inbox documents are in place: text version 3, or version 4 on an installation without invoice
  processing. One table (`CONFIRMATION_TEXTS` in [ai_settings_service.py](../../backend/app/services/ai_settings_service.py))
  maps each text version to the kinds it names; an earlier confirmation is asked once more, without entering the key again.
- **When it does not run.** Without opt-in, model, key, confirmation or budget, or when a provider fails or the settings
  change during the run, the original is retained, nothing is applied, and the document ends `unavailable`, `failed` or
  `needs_review` with a structured reason (`ai_import_disabled`, `ai_inbox_confirmation_missing`, `ai_document_model_missing`,
  `ai_key_unreadable`, `ai_settings_changed`, `organization_ai_budget_exceeded`, `model_reading_failed`,
  `model_extraction_failed`, `invoice_page_limit`, …); the screens explain each in German from one map
  (`frontend/src/lib/invoiceProcessing.ts`) and `POST /api/logical-documents/{logical}/retry-extraction` stays open within
  its three attempts. Usage already incurred stays visible.
- **Not wired yet.** Which process runs this stage in each stack and how it reaches a provider is an open owner decision:
  the inbox worker as shipped has no route to a provider (no provider egress, no session secret), so an unstructured document
  is retained for manual completion until that is decided and built. Tesseract stays in the image until
  [#502](https://github.com/itskeKs/verwalino/issues/502) but no code of this path calls it.

## Run and verify

Service/profile, signature maintenance and environment are documented in
[DEPLOY.md](../../DEPLOY.md#inbox-worker). The worker as shipped needs no provider secret; the model stage needs one only once its wiring is decided (see above).
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
inspection double; the fake models stand in for the provider. This is
deterministic API regression coverage, not a real-scanner deployment test. The
[model scenarios](../../features/inbox_model_processing.feature) and the
[browser twin](../../features/journeys/inbox_model_processing.feature) run the model stage with fake
providers: a four-page scan split into two documents, a structured e-invoice without any model call, the
confirmation that predates the inbox, a missing opt-in and a provider failure with its usage. A real provider
belongs to no automated run.
