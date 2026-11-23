# Inbox review chain

The inbox preserves the uploaded original. Processing proposes fields; only
explicit human review and final adoption create confirmed costs. This page owns
the operational flow for [issue #35](https://github.com/itskeKs/verwalino/issues/35).

## Flow and ownership

| Step | API / worker and durable records |
|---|---|
| Upload | `POST /api/intake-batches` declares the manifest; `POST /api/intake-batches/{batch}/items` receives exact bytes. [IntakeService](../../backend/app/services/intake_service.py) stores the original and calls the guarded database commands. Records: `intake_batch`, `intake_item`, `intake_upload_admission`, `intake_original`, `workflow_job`, `outbox_event` (`intake.received`). No cost exists. |
| Inspect | [workflow.process_once](../../backend/app/workers/workflow.py) claims `workflow_job` with a fence and records `job_attempt`. [Inspection](../../backend/app/core/intake_inspection.py) scans the exact original with ClamAV and checks structure, MIME and bounds. A clean completion creates `document_ref` and `intake.inspected`; blocked/rejected originals remain quarantined. |
| Deliver | [outbox.process_once](../../backend/app/workers/outbox.py) (the inbox worker) claims `outbox_delivery` and creates the initial `logical_document` plus `intake_extraction_job`. |
| Extract | [intake_extraction.process_once](../../backend/app/workers/intake_extraction.py), run by the **declaration worker** ([below](#which-process-runs-which-stage)), records `intake_extraction_attempt`, `extraction_revision`, `field_evidence` and `document.extracted`. A structured original (XRechnung, ZUGFeRD/Factur-X) uses the deterministic local parser and never reaches a model and needs no decision how it divides; an unstructured PDF, PNG or JPEG is read by the organization's two models ([below](#reading-with-the-organizations-models)), a single page at once, an original of more than one page only after a person has confirmed how it divides ([below](#a-multipage-original-waits-for-the-division)). No Tesseract, no pattern matching. Proposals remain unverified. |
| Review | The reviewer reads the original, confirms its [partition](../../backend/app/api/source_partitions.py), selects an [assignment](../../backend/app/api/intake_items.py), corrects [invoice fields](../../backend/app/api/invoice_reviews.py), compares [context](../../backend/app/api/context_decisions.py), and reviews [allocation](../../backend/app/api/allocation_proposals.py) and [treatment](../../backend/app/api/allocation_treatments.py). Records: `source_partition_revision`, `assignment_proposal`, `invoice_review_revision`, `context_decision`, `allocation_proposal`/`allocation_proposal_line`, `allocation_treatment_review`/`allocation_treatment_line`. These steps create no financial entries. |
| Adopt | `GET /api/allocation-treatments/{review}/adoption-preview`, then `POST /api/logical-documents/{logical}/allocation-adoptions` with the current preview digest and explicit cost/original-disclosure acknowledgements. [AllocationAdoptionService](../../backend/app/services/allocation_adoption_service.py) verifies the original's hash/size and attests integrity through `app_review`; guarded adoption atomically creates `allocation_set`, confirmed `cost_entry`, the released `invoice_document` and `document_cost_link`. Values come from the reviewed revisions. |

## Which process runs which stage

The [runner](../../backend/app/workers/workflow_runner.py) of the inbox worker defaults to exactly
`intake_inspection` and `outbox`: the file inspection with the malware gate, and the delivery that creates the documents
and their extraction jobs. It discovers organizations each round and serially rotates organization/stage pairs, polling
every two seconds. One `run_once()` dispatches at most one non-idle stage; `--once` is one such tick, **not** a complete
document pipeline. `--stage` can explicitly select other existing adapters; the inbox service never schedules
invoice/declaration extraction. The runner has **no extraction stage** and the inbox worker has neither the session
secret nor a route to a provider: it never reads a document with a model.

The extraction stage reaches the organization's provider, so in every stack the **declaration worker** runs it: its loop
gives the stage a turn after the declaration and the invoice step (one document per round, the organizations in rotation,
so no kind of work starves another; a failure is logged by type and place and ends neither the turn for the other
organizations nor the loop; a shutdown cancels the loop and leaves a claimed job to its lease and fence). Which worker kind
runs which model work in an installation is decided in one place, `backend/app/core/invoice_claimant.py`
(`runs_inbox_extraction`, next to the rule for invoices of #814); it needs no switch of its own, so production's declaration
worker, whose invoice switch is off because its stack has a dedicated invoice worker, runs the inbox stage too. A contract
test per stack (`backend/tests/test_invoice_claimants.py`) reads the three Compose files and proves which service runs it
(`worker` in `docker-compose.prod.yml`, `declaration-worker` in the portable and the staging stack) and that the inbox
worker has no session secret and no network with a route out; `backend/tests/test_inbox_model_stage_process.py` proves that
the inbox worker's process loads neither the stage nor the pipeline nor a provider transport, so no document can end
`ai_key_unreadable` because the process that looked at it could not decrypt the organization's key.

## Claims, completions and retries

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
- **Model stage.** Run by the declaration worker. [IntakeModelPipeline](../../backend/app/declarations/intake_pipeline.py) reads the pages without a text layer
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
  regard to case and spacing and only at the boundaries of the passage's words, numbers and identifiers („Ista“ is not in
  „Distanz“, „AG“ not in „Tagesgeld“, „1“ not in „Rechnung 12“), an amount as its digits with either decimal separator,
  thousands separators, currency and its sign (312.00 is „312,00 EUR“, „€ 312,00“; three digits after a lone separator are
  thousands in German writing, so „312.000“ is 312,000 and no passage of 312.00, and after a lone comma they state no value), a date by day, month and year in ISO, German numeric or month-name spelling
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
  `model_extraction_failed`, `provider_unreachable` (a request that never left the host: settled at zero, not on the usage
  card), `invoice_page_limit`, …); the screens explain each in German from one map
  (`frontend/src/lib/invoiceProcessing.ts`) and `POST /api/logical-documents/{logical}/retry-extraction` stays open within
  its three attempts that made a request (below). Usage already incurred stays visible.
- **No Tesseract.** Neither this path nor the image has it ([#502](https://github.com/itskeKs/verwalino/issues/502)): an
  unstructured original is read only by the organization's models, and the stage needs only the PDF renderer.

### A multipage original waits for the division

A multipage upload is not sent as a whole (owner decision of 2026-11-22 on #501). The inbox keeps its existing split and
confirmation workflow and its states: the reviewer divides the original into documents (`POST /api/logical-documents`, „Weiteren
Seitenbereich anlegen“) and confirms the division with the source partition (`POST /api/document-refs/{ref}/partition`, „Seiten zu
Belegen zusammenstellen“, „Aufteilung speichern“); the partition that lists the one document of all pages is the confirmation
"this is one document". Until then nothing of the original is requested from a provider.

- **Decision.** For a logical document of an original of more than one page the claimed job carries `split_state`
  (`intake_split_state`, migration 0198): `decided` when the latest confirmed partition of the original lists the document,
  `awaiting` while no partition is confirmed, `excluded` when the latest one leaves it out (the unsplit whole after the
  split). A single-page original and an XML original need none (`decided`).
- **Waiting.** After its local stage, an unstructured document that is not `decided` is parked by `hold_intake_extraction`: the
  job gets the status `awaiting_split` with the reason `awaiting_split` or `not_in_split`, no provider is built and no request is
  made. The order of refusals is the opt-in, then the decision, then the page bound. A structured original is parsed before
  this and never waits. The parked job cannot be retried: there is nothing to retry, and the screens show nothing to press.
- **The limit of three attempts bounds paid work.** An attempt counts only when it made a provider request:
  `intake_attempts_used` (migration 0198) is the one place that says how many of the three a document has used, and the claim
  (`attempt_limit`), the retry and the status (`attempt_number`, `can_retry`) read it. A request counts when the ledger holds a
  row of the attempt that is not settled as `PROVIDER_REQUEST_NOT_SENT`; a run that was abandoned (its lease ran out and it was
  claimed again by itself) counts as well, so that the automatic claims stay bounded. A park, a refusal for a missing opt-in,
  confirmation, model or key, a budget that was exhausted before the first request and a provider that could not be reached use
  none of the three, so a document that was refused twice and parked is still read once its division is confirmed. The retry
  stays the user's act; nothing queues a document again by itself. The job's own `attempt_number` counts the claims (the number
  of the attempt row), and the source page names „Versuch N von höchstens 3“ only once an attempt has made a request.
- **After the decision.** `review_source_partition` puts the parked documents the new partition lists back in the queue and marks
  the other parked ones `not_in_split`; each listed document is then read and extracted on its own, with only its own pages,
  each page read once. The unsplit whole is never sent after the split. A later confirmation that lists it again sends it
  (the reviewer's decision); the renewal before every paid request (`renew_intake_extraction`) refuses a claim whose
  document is no longer listed, so a division confirmed during a run stops the spending. Such a run does not fail: the worker
  hands the claim to `hold_intake_extraction`, which parks the document as `not_in_split` (or queues it again if the new
  division lists it); what the run already requested stays on the usage card, and the text of `not_in_split` says that nothing
  more is sent instead of claiming that nothing was.
- **What the screens say.** The batch page counts the original under „Zur Sichtung“ (it is the reviewer's turn, nothing failed),
  names the stage „Aufteilung ausstehend“ and says in German what to do and that nothing was sent; the source page calls the
  state „Wartet auf die Aufteilung“ and says the same next to the division panel
  ([DESIGN.md](../../DESIGN.md#inbox-processing-outcomes-evidence-and-usage-501)).

### Memory of the stage

The declaration worker runs the stage in a container of 768 MiB (`mem_limit` in every Compose file) and starts a child for
the local parse of a PDF (`core/intake_extraction.py`) and one for the decode of a PNG or JPEG (`core/image_reading.py`); poppler's
`pdftoppm` renders a scanned page in a process of its own. Each of the two children has an address-space limit,
`CHILD_MEMORY_BYTES` in `core/document_limits.py`: the container's memory less `WORKER_RESERVE_BYTES` for the worker process
itself (768 − 256 = 512 MiB), so that a document that needs more is stopped by its child's own bound and not by the container,
whose kernel ends whichever process it chooses. There is no configuration key. `backend/tests/test_inbox_stage_memory.py`
reads the three Compose files and fails when the constants and the container drift apart; on Linux it also reads the limit of
each started child from `/proc`.

Measured on 2026-11-22, resident memory in MiB of a worker process tree (`DeclarationImportWorker`, one turn of the stage, the
offline fake providers on the test database, the fake reading stage building the base64 and JSON body a real client would hold;
`ps` sampled every 25 ms for the tree, `getrusage` for each process's own peak). It was a macOS process: its resident memory
is not that of the Linux container, no address-space limit applies there (the children ran unbounded, which is the need) and
the page cache and the tmpfs of the container are not in the figures. The worker alone is about 120 MiB after its imports.

| Original (the inspection and stage bounds at their maximum) | Result | Worker peak | Largest child | Tree at its peak |
| --- | --- | --- | --- | --- |
| PDF of 10 scanned pages, each 1609 pt square (16.2 million pixels at 180 DPI), 24.2 MiB | read and extracted | 348 | `pdftoppm` 113 | 454 |
| PDF of 10 pages with text (57,000 characters) | extracted | 133 | local parse 143 | 266 |
| JPEG of 20 million pixels, 22.4 MiB | read and extracted | 259 | image decode 251 | 436 |
| PNG of 20 million pixels | read and extracted | 126 | image decode 221 | 343 |
| PDF of 200 scanned pages, 23.9 MiB | ends `invoice_page_limit` | 177 | local parse 82 | 248 |
| PDF of 200 pages with about 1 million characters (0.15 MiB) | ends `invoice_text_limit` after 27 s | 122 | local parse 1,993 | 2,115 |

What a document can ask for therefore fits the container, except one thing: the local parse extracts the text of every
selected page before it checks the text bound of 60,000 characters, and `pdfplumber` keeps what it extracted, so a long text
PDF of up to 200 pages needs about 2 KiB of memory per character. Such a document used to end at the former limit of 1 GiB or
at the container's, whichever came first, and now ends at the child's bound of 512 MiB: the child runs out of memory and the
extraction records `invalid_document` (the child's general failure), as it would at any bound it runs into. Checking the bound while the pages are read would end it as
`invoice_text_limit` without that memory; that is a change of the local parse, not made with the limits.

## Meldungen of a batch

A batch the signed-in user uploaded is one of their „Meldungen“ (the entry in the top bar of every page and `/meldungen`, issue #872)
while a file of it waits, is processed or has failed, and the row leads to the batch page (`/posteingang/<batch>`); it is nobody else's
Meldung: another user of the organization, an administrator included, and another organization are told nothing. The row names the batch
(„Dokumentenstapel“, the number of its files, „Eingang vom“ and the time), a badge for the state („Wartet auf Verarbeitung“, „Läuft“,
„Verarbeitung fehlgeschlagen“) and „Stufe:“ with the stage in the words of the batch page („Dateiprüfung“, „Dokumentvorbereitung“,
„Auswertung“). Which state of a file is a Meldung is decided for every `status` and `stage` of `intake_batch_status` in
`INTAKE_FILE_STATES` (`services/meldungen_service.py`):

| File (batch page) | Meldung |
|---|---|
| „Empfangen“ (`received`, waits for the inspection) | waits |
| „In Verarbeitung“ (`processing`: inspection, preparation or extraction runs) | runs |
| „Gesperrt / fehlgeschlagen“ (`failed`: inspection, delivery or extraction) | failed |
| failed in the extraction for a reason of the organization's AI setup (`AWAITS_SETUP`, which the batch page does not call a failure) | waits |
| „Datei fehlt“ (`waiting`, the upload of the file is missing) | none |
| „Zur Sichtung“ (`review`: documents wait for review, or „Aufteilung ausstehend“) | none |

The batch is in the most urgent state of its files (failed, then running, then waiting) and names the least advanced stage among them. A batch
whose files are all at rest leaves the list; a failed file keeps its batch in it until the file is asked for again in the way the batch page
offers (the inspection or the extraction is requested again, which makes the file wait or run again). Nothing is stored: the state of a file is
derived from the same rows as the batch page's. The source is the database function `intake_batch_progress` (migration 0205), because the
intake tables are private to the application's role; it follows the rules of `intake_batch_status` and a test takes a batch through every
stage with the real workers and compares the Meldung with the batch page's own answer at each step. It reads the newest 50 batches of the
user through `ix_intake_batch_owner` and the files through their keys, in one statement; a failure of a batch older than the newest 50 is not
named. It adds no worker, no process and no poll of its own: the frame's poll (every 20 seconds while a Meldung waits or runs, ended after 15
minutes without change) covers it.

## Run and verify

Service/profile, signature maintenance and environment are documented in
[DEPLOY.md](../../DEPLOY.md#inbox-worker). The inbox worker needs no provider secret and has none; the declaration worker that
runs the model stage has the session secret and the provider route in every stack.
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
providers: a four-page scan that waits, is split into two documents and read from their own pages (or confirmed as one
document and read whole), a single page processed at once, a structured e-invoice without any model call, the
confirmation that predates the inbox, a missing opt-in and a provider failure with its usage. A real provider
belongs to no automated run.
