# Operating the Declaration of Division (Teilungserklärung) Import and Building Knowledge (Gebäudewissen)

The declaration of division import is an asynchronous workflow that requires review.
`DeclarationPipeline` is the fixed, deterministic manager: it runs a bounded local
precheck, reads every page without a text layer with the reading model, runs fixed
extraction passes, local validations and at most one bounded vision recheck. It is not a
freely autonomous agent. Document pages, page transcripts and model
responses are **untrusted data**; they receive no tools, no action sink and no
permission to change data. Only **Human Approval** promotes a reviewed draft into
canonical property, unit or rule data.

Building knowledge shows only approved facts, rules, document relationships and page
evidence. The lexical PostgreSQL search works without a model call. The optional Q&A
feature returns exclusively a cited answer drawn from a bounded retrieval of approved
evidence and can be switched off separately.

When declaration import is disabled, as it is on a portable self-host install,
opening a new draft, uploading a PDF to an existing draft or starting an import
shows: “Der Import von Teilungserklärungen ist für Ihre Organisation nicht
eingerichtet. Bitte wenden Sie sich an Ihre Administration.”

## Operating Flow and Model Matrix

```text
PDF -> local precheck (structure, text layer, limits)
       -> each page without a text layer: rendered locally, one at a time, as PNG
          -> reading model ("Dokumente einlesen") -> page transcript
       -> extraction passes on text-layer text and transcripts -> local evidence/domain validation
       -> RouterDecision(NONE | HUMAN_REVIEW | VISION_RECHECK)
       -> only selected, locally rendered PNG pages to the extraction model
       -> re-validation -> NEEDS_REVIEW -> Human Approval
```

Which model does what is the organization's choice in Einstellungen (ADR 0017); no model
is named here by the product:

| Purpose | Model | Permitted input | Limit |
| --- | --- | --- | --- |
| page reading (`read_page`, [Reading stage](#reading-stage-adr-0017)) | the reading model, „Dokumente einlesen“ (`document_model`) | one locally rendered PNG page per request | only pages without a usable text layer; no tools, no fallback to another model |
| regular extraction (`inventory`, `extract_core`, `extract_rules`, `reconcile`) | the extraction model, „Informationen extrahieren“ (`model`) | bounded text batches (text layer or transcripts) or structured intermediate results | no images, no tools, no `previous_response_id` |
| Vision recheck | the extraction model | only locally rendered PNG pages selected by `RouterDecision`, plus bounded context | no regular passes with images; a rendering failure is fail closed |
| Eval candidates | Luna `low`, Terra and Luna with Mini router | exclusively the same gold set | no automatic promotion and no production routing |

The local `route_escalation` router uses only reproducible signals. It is not a learned
or online-learning router and uses no model confidence:

| Priority | Local signal | `RouterDecision` |
| ---: | --- | --- |
| 1 | isolated router contract: `PROVIDER_REFUSAL`, `PROVIDER_INCOMPLETE`, `PROVIDER_TIMEOUT` or `PROVIDER_SCHEMA_FAILURE` | `HUMAN_REVIEW` |
| 2 | unknown document or page outside the precheck | `HUMAN_REVIEW` |
| 3 | missing evidence quote on a known, renderable page | `VISION_RECHECK` |
| 4 | low local page quality on a cited/blocker page | `VISION_RECHECK` |
| 5 | domain blocker without a visual signal | `HUMAN_REVIEW` |
| 6 | no signal | `NONE` |

The router sorts and deduplicates pages and limits them with
`HV_DECLARATION_MAX_RECHECK_PAGES`; additional calls remain bounded by
`HV_DECLARATION_MAX_RECHECK_CALLS` and by token, cost and deadline limits. A selected
page that cannot be rendered is not uploaded in an alternative form and leads to Human
Review.

The production pipeline path currently calls the router with
`provider_failure=None`. Provider failures are therefore not redirected to vision,
but follow the existing worker policy directly to `RETRY_WAIT` or `FAILED`. The first
table row documents only the isolated, total router contract: if one of its four
permitted provider failures is passed there, only `HUMAN_REVIEW` is permitted.

## Local Precheck and Evidence-Gate

A maximum of **25 MiB** applies per PDF (`HV_DECLARATION_MAX_PDF_BYTES`), and by
default ten documents and 500 pages per import. The precheck rejects unreadable,
encrypted or active PDFs as well as embedded files. It uses the text layer first; a page with
fewer than `HV_DECLARATION_TEXT_MIN_CHARS` (40) characters of it is read by the reading model
instead. There is no local text recognition in this path. The page count and pixel
bounds of all such pages of all documents of the import are reserved in the precheck, before the
first model call, so that an import over a bound costs no reading at all. The `HV_DECLARATION_OCR_*` keys keep their names and now bound the rendering
for the reading model.

| Area | Default limit |
| --- | ---: |
| render resolution / time per page for reading | 180 DPI / 30 seconds |
| rendered pixels per page (render bound) | 20,000,000 |
| pixels of a page image sent to the reading model | 4,000,000 (a larger render is scaled down, never refused) |
| temporary render data of one page render (released when the render ends) | 256 MiB |
| pages / pixels read per import | 200 / 800,000,000 (counted from the page size at 180 dpi before scaling to the render ceiling: an A4 page counts 3,132,240 pixels, so 200 A4 pages and the rechecks fit; larger formats reach the bound earlier, A3 at page 128) |
| precheck deadline | 600 seconds |
| provider pages / characters per batch | 20 / 80,000 |
| input / output tokens per import | 2,500,000 / 500,000 |
| output tokens per provider call | 24,000 |
| recheck pages / recheck calls | 12 / 2 |
| candidates / estimated import cost | 5,000 / 3.00 USD |

The per-call allowance (`HV_DECLARATION_PROVIDER_MAX_OUTPUT_TOKENS_PER_CALL`) applies to
every model request of an import: reading a page, the three extraction passes, reconciliation
and a recheck. A request gets the smaller of the allowance and what the import has left of its
output tokens, so the last request can get less. Before each request the worker reserves its
worst-case cost, with the output at that allowance and the model's price, and does not send a
request whose reservation would exceed the import's cost limit.

The request timeout is a separate bound, and a long answer needs a long timeout. A model
writes about 90 output tokens per second (measured on staging), so an answer of 24,000
tokens takes about 270 seconds. Unless the organization saves its own **Zeitlimit pro
Anfrage** in its AI settings, a request may take 60 seconds
(`HV_DECLARATION_PROVIDER_TIMEOUT_SECONDS`) with OpenAI or Anthropic and 180 seconds with an
OpenAI-compatible API. An organization whose imports need answers of this length to complete
sets a timeout above 270 seconds; a request that runs out of time fails with
`PROVIDER_TIMEOUT`.

Every **Evidence-Quote** must appear locally on the exact
`(document_key, pdf_page)` given. The validator normalizes Unicode, line breaks,
whitespace and hyphens and requires a non-empty, contiguous match. A page number,
section number or model text is not a substitute for evidence. Error codes such as
`EVIDENCE_QUOTE_NOT_FOUND`, `UNKNOWN_EVIDENCE_DOCUMENT` and
`EVIDENCE_PAGE_OUT_OF_RANGE` contain no document text and must not carry it into the
ledger, logs or tickets either.

Common precheck errors are `PDF_SIZE_LIMIT_EXCEEDED`,
`PDF_PAGE_LIMIT_EXCEEDED`, `PDF_UNREADABLE`, `PDF_ENCRYPTED_FORBIDDEN`,
`PDF_ACTIVE_CONTENT_FORBIDDEN`, `PDF_EMBEDDED_FILES_FORBIDDEN`,
`PAGE_RENDER_UNAVAILABLE`, `PAGE_RENDER_TIMEOUT`,
`OCR_RENDERED_PIXELS_LIMIT_EXCEEDED`, `OCR_IMPORT_PAGE_LIMIT_EXCEEDED` and
`OCR_IMPORT_PIXEL_LIMIT_EXCEEDED`; the render bounds keep their historical `OCR_` prefix.
`OCR_TIMEOUT`, `OCR_UNAVAILABLE` and `OCR_FAILED` are no longer produced; imports that stored
`OCR_TIMEOUT` before 0196 keep it and stay retryable.

### The inventory against the documents of the import

The inventory passes (the first pass over every batch) answer which documents the import holds and how they
relate. The instruction and the request schema tell the model what an entry is: one entry for every uploaded
file, under that file's `document_key` as it is labelled on the pages, and no entry for any other key. A PDF that holds
several deeds (a declaration with its amendments, supplements or plans) is still one entry, typed and titled by its
main deed; a deed inside a file gets no entry and no relation of its own, and the extraction passes still read every
page of the file. What a user sees is that file as one document of the import: the review's document relations come
only from these entries, between two uploaded files, and a relation to a document that was not uploaded is the hint
described below ([Inventory keys](#organization-settings-0178) says how each transport enforces this).

The merged answer is checked against the import's documents right after the passes, before the first
`extract_core` call, so that an answer that cannot belong to the import costs no extraction request (no
reservation is made, none is left in the run's usage). Since the request schema allows only the keys of the
request, this check is the last defence for an answer that got past it:

- An entry whose own `document_key` is not a document of the import fails the import with
  `UNKNOWN_INVENTORY_DOCUMENT`; a document of the import without an entry fails it with
  `MISSING_INVENTORY_DOCUMENT`. Both are final (no `RETRY_WAIT`); the import page explains them in German and
  names the next step (start a new import, contact the operator if it repeats).
- A **relation** (`AMENDS`, `SUPPLEMENTS`, `REFERENCES`) whose `target_document_key` is not a document of the
  import is no failure: a declaration regularly names documents that were not uploaded (an Aufteilungsplan, an
  earlier deed, a Nachtrag), and the name the model gave a target may match none of the uploaded ones. The relation
  is left out of the inventory that every later step reads (reconciliation, evidence validation, the relation
  candidates), so no candidate is created for it, and the run records one issue per left-out relation, code
  `DOCUMENT_RELATION_TARGET_UNMATCHED`, severity `INFO`, bound to no candidate (`candidate_id` is null;
  `details_json` holds `document_key` and `pdf_page` of the relation's evidence where the answer names a page of
  the import). Its German text names the document the relation was read in, the relation in plain words (`ändert`,
  `ergänzt`, `verweist auf`) and the other document as the answer names it, and says that the reference could not
  be matched to an uploaded document and the relation was not taken over; it does not say that the document was
  not uploaded, because the pipeline cannot know that. The review page lists such issues in „Hinweise zum Import“;
  they never block the approval. The evidence of a left-out relation is not validated, so it cannot fail the import.
  The text is not fixed: it quotes the name the model gave for the target (up to 120 characters) and the title or
  file name of the source document. These rows are stored in `declaration_candidate_issue` like the import's other
  evidence, exported and erased with it, and nothing logs them.

## Reading Stage (ADR 0017)

`DocumentReadingProvider.read_page` (`declarations/provider.py`) takes one rendered PNG page
(media type, document key, original page number) and the reading model and returns a
`PageTranscript`: ordered text, table (rows of cells) and unreadable blocks that copy what is
visible and mark what cannot be read (`[unreadable]`) without interpreting it. Prompt `reading-v1`
and schema `reading-1` are versioned constants; the caller assigns the page identity. The
OpenAI, compatible and Anthropic transports of ADR 0014 implement it with the organization's one
provider, endpoint, timeout and key. One page is one request; a failed page is read again alone.

- **Both models are needed.** The import runs only when the organization has the switch, a key,
  the extraction model and the reading model. Without the reading model drafts, uploads and
  starts answer `DECLARATION_IMPORT_DOCUMENT_MODEL_MISSING`, the worker leaves queued imports
  untouched, and a job that nevertheless reaches the reading stage ends with
  `DOCUMENT_MODEL_NOT_CONFIGURED`. Uploaded originals stay.
- **No fallback.** A model that cannot take the image (recognised by the status 415 or, for a 400
  or 422, by the structured identifiers of the provider's error object, its `param` naming the image
  part or an image `code`; never by message text) ends the job with
  `PROVIDER_IMAGE_INPUT_UNSUPPORTED`; any other rejected request with `OPENAI_REQUEST_FAILED`, as in
  the extraction passes; a refusal with `PROVIDER_REFUSAL`, an answer that did not complete with `PROVIDER_INCOMPLETE` and a
  completed but malformed answer with `PROVIDER_SCHEMA_FAILURE`. The application
  never switches model or provider. A reading failure ends the run; the job is `RETRY_WAIT` for
  a retryable code and `FAILED` otherwise.
- **Metering.** Before each call the worker reserves the page at the reading model's price (an
  image from its pixel dimensions by the provider's documented formula) in the same ledger as the
  extraction passes, so reading and extraction count together against the per-import token and
  cost limits and the organization's monthly limit (`IMPORT_TOKEN_BUDGET_EXCEEDED`,
  `IMPORT_COST_BUDGET_EXCEEDED`, `ORGANIZATION_AI_BUDGET_EXCEEDED`). A failed call keeps the
  usage the provider reported; when it reported none, the reservation stays recorded, unless the request provably never
  left the host (`ProviderFailure.not_sent`: the name did not resolve, the endpoint guard refused the address or the
  connection could not be made), which gives its reservation back. An
  unpriced model is not limited and shown as „kein Preis bekannt“. The token limits (2,500,000
  input, 500,000 output) are sized for the 200 dense scanned pages the page limit permits, with the
  derivation beside them in `app/core/config.py`; the cost limit of 3.00 USD stays the money guard
  and is reached first on a dearer model. The upload limit of 25 MiB per PDF bounds a large scan
  first: 200 pages in high resolution can exceed it before the page limit is reached. The
  organization's monthly limit counts a running import at its cap when its extraction model or its
  reading model is priced. A running import therefore holds up to the per-import cost limit (3.00 USD)
  against the monthly limit, and an organization whose monthly limit is lower cannot start an import
  with a priced model: start and retry refuse it with `ORGANIZATION_AI_BUDGET_EXCEEDED`, checked for the
  extraction and for the reading model.
- **Resume.** Each finished page transcript is saved with the call's settled usage in one
  transaction (`declaration_page_reading`, migration 0196; working data of the import, written by
  the worker only). A transcript is kept as long as the import's documents, until the organization
  is erased; nothing deletes it earlier. It is not part of the organization export
  ([export](organization-export.md)), so after a restore the evidence re-check of a scanned import
  in the recorded evaluation is `UNAVAILABLE`. A resumed job neither asks for nor pays for a page it
  already holds. A job keeps the extraction and reading
  model of its first run (`declaration_extraction_run.model_name`, `document_model_name`): a
  settings change applies to new jobs only.
- **Outcome on the import screen.** Every failure above has a German sentence in
  `pages/DeclarationImportPage.tsx` (the one map of job codes to text): what happened, that the
  uploaded original is kept and nothing was applied, and what to do (choose another model, reduce
  the import, contact the operator). An unknown code shows only the generic sentence, never the
  code.
- **Cost view.** `GET /api/declaration-imports/{id}` lists per run the stages `READING` and
  `EXTRACTION` (model, usage status, reported and conservatively recorded tokens, cost or none for an
  unpriced model) built from the pass ledger (`pass_id` `read_page` for reading) and the run row; a
  stage that made no call, such as the reading of a text-layer document, has no entry. A stage shows
  only what its ledger entries report. What the run total holds beyond that (a reservation that no
  entry attributes, for example of a call that was cancelled) is the separate `unassigned` item
  (tokens and cost, or no cost when a model of the run has no price), never assigned to a stage by
  inference; it is absent when zero. The stages and `unassigned` add up to the run's tokens and cost.
  The import and review screens show them as „Dokumente einlesen“, „Informationen extrahieren“ and
  „Nicht zugeordnet“ before the run total.
- **Status of a started import.** The same `GET /api/declaration-imports/{id}` (and the answers of
  `POST …/start` and `POST …/retry`) carries what the import page shows while the import runs:
  `status_label` (the German status line), `started_at` (when the start or the last retry was
  recorded) and `steps`, each with `label`, `state` (`DONE`, `RUNNING` or `WAITING`), `detail` (what
  the step counted, such as „12 Seiten gelesen“) and `elapsed_seconds` (only the running step). There
  is no percentage and no raw stage name: `progress_percent` and `current_stage` stay in the table
  but leave the response. `declarations/progress.py` holds the one table of German labels (every status the
  database allows, every kind of pass the ledger accepts and the steps) and derives the steps from
  recorded data only: the import's status, the passes of its open run (the last run once the import
  has finished) and the saved page transcripts. A step is running only while the status names it
  (`PREFLIGHT` runs „Dateien prüfen“, or „Seiten lesen“ once a page of this run was read; `EXTRACTING`
  runs „Angaben auslesen“; `RECONCILING` runs „Angaben abgleichen“). „Seiten lesen“ is listed only for an
  import of which a page was read, so a text-layer document has no such step. The count of a step is
  the number of recorded rows: transcripts saved for the import (any run) for the reading, finished
  model requests of the open run for the two others (an attempt that failed is not counted). The pass
  ledger row of a call is written when the call has ended, so the page cannot tell which call of a
  stage is in flight, and the page names stages, not single calls. The time of the running step counts
  from the last row the run recorded for an earlier step, or from the start of the run, to the moment
  of the answer; the page counts on from that figure until its next poll. A queued import waits
  („Wartet auf Verarbeitung“), a `RETRY_WAIT` import says that a new attempt is scheduled; a `FAILED` or
  `CANCELLED` import lists no steps and keeps its failure text. A test fails when a status, a stage
  written to `current_stage` or a pass kind has no label
  (`tests/test_declaration_import_progress.py`).

## Provider Failures, Recovery and Safe Telemetry

Content outcomes, transport errors and usage metering are separate classes; they must
not be merged into a single “four outcomes” schema:

| Class | Handling |
| --- | --- |
| `PROVIDER_REFUSAL` | no schema repair and no data retransmission; existing worker policy |
| `PROVIDER_INCOMPLETE` | the provider's answer did not complete; no schema repair, whatever usage is left. The failure keeps the reported usage (every stage books it as consumed, not as the reservation) and, for the declaration passes, the request id and returned model of the pass ledger row; without reported usage the reservation stays booked. An answer that the native OpenAI response reports as cut off at the output limit (`incomplete_details.reason` is `max_output_tokens`) is final, because the same allowance cuts the identical request off again: the failure is not retryable, the declaration worker ends the import `FAILED` after that run (no `RETRY_WAIT`, no new run) and the reading stage of an import ends the same way. Every other incomplete answer (another reason, no reason, the compatible and Anthropic transports) is retryable: `RETRY_WAIT`, a new run after five minutes, at most `declaration_max_attempts` runs. The invoice and inbox stages end the attempt as `failed` for both, whatever the flag says. `POST /api/declaration-imports/{id}/retry` decides by the stored error code, so it still accepts a `FAILED` import with this code; the user's explicit retry pays the requests again |
| `PROVIDER_SCHEMA_FAILURE` | a completed answer that is not the schema's JSON: exactly one schema-only repair with the same bounded input, only when the first answer reported its usage and that leaves room in the call's original output allowance (the repair may use what the first answer left); without usage, or with the allowance used up, no repair, and a final failure |
| `PROVIDER_TIMEOUT`, `PROVIDER_CONNECTION_ERROR`, `PROVIDER_RATE_LIMIT`, `PROVIDER_SERVER_ERROR`, `OPENAI_REQUEST_FAILED` | transport group; retryability follows the individual stable code and the worker policy |
| `PROVIDER_USAGE_INVALID` (concretely `OPENAI_USAGE_INVALID` in the OpenAI adapter) | separate usage metering; cost/usage contract fails closed, no publication |

The worker claims jobs per tenant with a lease, token and generation.
Heartbeats extend only the current generation. Preflight, page rendering for
reading and selected-page recheck rendering run off the event loop, and the reading
requests are awaited while the heartbeat keeps running, so the lease is renewed
during local processing and during a slow model call. They share the existing preflight deadline and resource
budgets. On cancellation or lease loss, the worker waits for the active local
operation and its temporary-file cleanup before releasing its processing slot.
The interrupted operation's result is discarded.

On interruption or a retryable error,
the job moves to `RETRY_WAIT`; late results cannot publish. `POST /api/declaration-imports/{id}/retry` is intended only
for retryable errors; `cancel` locks out the current lease. Before a retry, check
cost/token counters, error class and limits; do not repeat blindly.

An unexpected worker error after claiming a job, or a pipeline error that escapes
the pipeline's own failure handling (`PIPELINE_ERROR`), is logged with the import
ID, exception class and raising module/function only, never exception message
text. Unless the pipeline had already started an extraction run for the attempt,
the worker records a failed extraction run with `WORKER_UNEXPECTED_ERROR` under
the current lease, clears the lease and moves the job to `RETRY_WAIT` for five
minutes. Once the configured attempt limit is reached, the next claim after that
wait fails the job with `WORKER_ATTEMPT_LIMIT_REACHED`. Errors before a claim or
while recording the failure also log only the exception class and location.

## Services, Recovery and Status

For local development, the API and the import worker run separately:

```bash
cd backend
HV_SESSION_COOKIE_SECURE=false ./.venv/bin/uvicorn app.main:app --reload
```

```bash
cd backend
./.venv/bin/python -m app.workers.declaration_import
```

In operation, both services are started with a protected external env file:

```bash
docker compose --env-file /absolute/path/hausverwaltung.env \
  -f docker-compose.prod.yml up -d app worker
```

Before a restore, back up the database and the `documents` volume as a consistent pair
and restore only from the same backup generation. After the restore, check PostgreSQL
integrity and the stored SHA-256 values, then start the API and worker. A one-sided
restore can cause document references and stored content to diverge and is not a valid
recovery path.
Status diagnostics use exclusively metadata such as `status`,
`error_code`, `current_stage`, tokens, estimated cost and attempts —
never document text or provider payloads.

The durable pass ledger has **one row per SDK attempt**. It references
`declaration_extraction_run_id`; the repository call is named
`append_pass_entries`. The pipeline empties its queue only after a
committed checkpoint, fail or publish transition. The table is
tenant-isolated via RLS, append-only, and the database validates exactly 23
safe JSON fields, types, allowlists and the binding of the attempt number.
The Python and DB contracts must therefore be changed together.

Only technical metadata is permitted, such as pass ID, attempt number, model,
reasoning effort, prompt/schema/harness version and hash, token partition,
estimated cost, latency, retry counter, `route_action`, reason codes and
failure class. Not permitted are prompt text, page transcript/PDF text, quote, image,
response body, local path, tenant/property/user ID, headers, API key and
exception text. Status endpoints stay with their aggregated metadata.

## Caching, Costs and Provider Data

Luna places the static developer block before dynamic document data and
uses an explicit cache breakpoint and a 30-minute cache. The
stable key contains only model, pass, and prompt, schema and
harness version/hash. Mini receives **no** explicit caching. Its cache-write multiplier is the documented `1.25` constant in `core/ai_models.py`, not an environment setting.

Costs are calculated conservatively from ordinary input, cached input,
cache write and output tokens. Invalid partitions (cached plus cache-write
tokens above the input tokens) and negative or non-integer counters fail
closed to `OPENAI_USAGE_INVALID`, for a priced and an unpriced model alike.
A priced model without a documented cache-write rate (Mini) that reports cache
writes fails closed to `OPENAI_CACHE_WRITE_UNSUPPORTED`, because its writes
would count at zero. A model without a price (no entry in `core/ai_models.py`;
the settings card says „kein Preis bekannt“) is metered with zero rates, so its
cost stays unknown and there is no estimate that cache writes could
under-state: a response of such a model that reports cache writes is accepted
and no cost is booked, as for every other call of an unpriced model. The pass
ledger stores its input, cached, cache-write and output tokens as reported; the
invoice and inbox ledger (`invoice_model_call`) stores its input and output
tokens. A cheaper estimate does not justify a loss of quality.

An organization administrator enters the OpenAI key in **Einstellungen**. The key
is encrypted at rest using the deployment session secret, never returned by the
API, and never included in logs, product archives or tickets. Rotating
`SESSION_SECRET` therefore makes every stored key unreadable until each
organization re-enters its key (the known 503 message); the declaration import
worker skips such organizations with one `AI_KEY_UNREADABLE` log line per
organization and keeps claiming for the others instead of stalling their queue. The extraction
model is used for extraction, escalation, vision recheck and Q&A; a second model reads the pages
(see [Reading Stage](#reading-stage-adr-0017)). Each request reads the current organization
settings; a job keeps the two model identities of its first run; clients are closed after the
request/job. Only after the local precheck are bounded, permitted page contents (text of
the text layer and, for pages without one, the rendered page image) sent to the provider.
`store=false` is set, **is not ZDR** and triggers no retention commitment.
API data is not used for training by default; without approved extended controls,
abuse monitoring may retain content for up to 30
days. Before production, the DPA (AVV), region/data residency,
retention/ZDR suitability, permissions and GDPR (DSGVO) assessment must be reviewed and
documented.

## Review and Domain Limits

A successful run ends in `NEEDS_REVIEW`, never in application of the draft.
Reviewers compare every fact and every rule with the page evidence, edit
or discard the draft, and only then take responsibility.
Conflicts remain `NEEDS_CLARIFICATION`; the system gives no legal advice. MEA
(co-ownership shares, Miteigentumsanteile) are to be treated as numerator/denominator;
a scale is never silently supplemented or converted.

## Worker Network Boundary

In production, `backend` remains an internal Docker network: database,
migration and bootstrap are exclusively there, the API additionally only in the
shared Traefik network. Only the two workers that call the organization's provider, the declaration
worker and (in the retained local production configuration) the invoice worker, are additionally attached
to the project-local non-internal network `egress`; they are never attached to Traefik and
no service publishes host ports. Their explicit Docker gateway
priorities are `backend: 0` and `egress: 1`: the default route gateway is
therefore in the egress network, and the database remains reachable exclusively via
`backend`.

The OpenAI client is hard-wired to `https://api.openai.com/v1`. The harness has
no URL, browser or tool capability and transmits only the intended
structured output requests. The existing non-billable smoke command now requires
an organization context: `python -m app.workers.declaration_provider_smoke
--organization-id UUID`. It reads that organization's enabled import configuration
through the same effective-settings function and retrieves the selected model only.
It never submits PDF/prompt contents or Responses calls. Missing, disabled or
unconfigured organizations fail with a sanitized message. Live comparison evals
likewise require `--organization-id UUID`; offline fake evals need no organization,
credential or network. Comparison candidates remain evaluation-only inputs.

The non-internal Docker network is not a domain firewall. Anyone who must restrict egress to OpenAI
additionally needs a host/network allowlist. The
isolated Compose verifier additionally checks the running default route against
the Docker egress gateway, but with its placeholder key triggers no
external provider call.

## Local Tests and Gold Evals

Normal tests use only synthetic/redacted data and no network calls.
The ephemeral test database runs only on port 55432:

```bash
docker compose -p hv-declaration-tests -f infra/docker-compose.declaration-test.yml up -d --wait
unset hv_database_url hv_database_url_migrator hv_database_url_sync database_url database_url_migrator database_url_sync
export HV_DATABASE_URL='postgresql+asyncpg://app_rw:app_secret@localhost:55432/hausverwaltung'
export HV_DATABASE_URL_MIGRATOR='postgresql+asyncpg://app_migrator:migrator_secret@localhost:55432/hausverwaltung'
export HV_DATABASE_URL_SYNC='postgresql://app_migrator:migrator_secret@localhost:55432/hausverwaltung'
export HV_ENVIRONMENT=test
cd backend
./.venv/bin/python -m app.evals.declaration_knowledge --fixtures tests/fixtures/declaration_eval_cases.json --provider fake
```

Promotion is fail closed. The gold set requires at least the neutral IDs
`gold-units-mea`, `gold-heating-amendment` and
`gold-adversarial-evidence` as well as the fixed comparison labels `luna_medium`,
`luna_low`, `terra_primary` and `luna_with_mini_router`. Quality must not regress in
any representative dimension; lower costs do not compensate for this. Unlabeled real PDFs yield at most a
descriptive smoke report, **no accuracy claim and no promotion**.

A real PDF smoke test is never to be run automatically and produces only a
`descriptive_only` report: it is not promotion-eligible. It requires a
separate explicit authorization and compares exactly `luna_medium`,
`luna_low`, `terra_primary` and `luna_with_mini_router`.

**Phase A — Activation and values:** Before **any path method**, exactly
`HV_DECLARATION_LIVE_EVAL=1`, a valid `--organization-id UUID`,
`--provider live --live --compare-models`, a non-empty value for
`HV_DECLARATION_REAL_PDF_MANIFEST` and the three positive, caller-verified
Terra rates are checked. If any of these values is missing or invalid,
no path/manifest/PDF/provider access and no transmission takes place.

**Phase B — local external input check:** Only then are the report and
manifest paths checked locally: both must be located externally, the passed manifest
must exactly match `HV_DECLARATION_REAL_PDF_MANIFEST`, comply with case limits and pass the
SHA-256 check per case. In this phase, the manifest and declared PDFs are read and hashed locally. A mismatching hash
prevents capability, adapter, provider and transmission — it does not claim
that the file was never opened. Only after a successful Phase B is the
organization's enabled persisted AI settings loaded through the worker role, then the
capability created and an adapter constructed. No environment API key is read. Manifest, PDFs and report
never belong in the repository.

```bash
cd backend
./.venv/bin/python -m app.evals.declaration_knowledge \
  --fixtures tests/fixtures/declaration_eval_cases.json \
  --provider live --live --compare-models --organization-id UUID \
  --manifest /absolute/external/path/manifest.json \
  --report-dir /absolute/external/path/reports
```

The three Phase A rates are named
`HV_DECLARATION_COMPARE_TERRA_INPUT_USD_PER_MILLION`,
`HV_DECLARATION_COMPARE_TERRA_OUTPUT_USD_PER_MILLION` and
`HV_DECLARATION_COMPARE_TERRA_CACHED_INPUT_USD_PER_MILLION`.
The presence of `HV_DECLARATION_REAL_PDF_MANIFEST` is a Phase A gate; only
in Phase B is its value checked as an absolute external path for equality with the
passed manifest and then validated locally.

## Research Context

The structure follows [Harness Engineering](https://openai.com/index/harness-engineering/):
repo legibility, enforceable boundaries, tests and evals — not autonomy.
The model, vision, structured output, cache and data notes are aligned with the
[Latest model guide](https://developers.openai.com/api/docs/guides/latest-model),
[GPT-5.4 Mini](https://developers.openai.com/api/docs/models/gpt-5.4-mini),
[Vision](https://developers.openai.com/api/docs/guides/images-vision),
[Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs),
[Prompt Caching](https://developers.openai.com/api/docs/guides/prompt-caching)
and [Your data](https://developers.openai.com/api/docs/guides/your-data).
Privacy and operating assumptions must additionally be checked against the
[business data notes](https://openai.com/business-data/) of the chosen
contract. [Evals](https://openai.com/index/evals-drive-next-chapter-of-ai/)
justify the fail-closed promotion instead of a cost decision made by gut feeling.
The routing literature ([RouteLLM](https://proceedings.iclr.cc/paper_files/paper/2025/file/5503a7c69d48a2f86fc00b3dc09de686-Paper-Conference.pdf),
[RouterEval](https://aclanthology.org/2025.findings-emnlp.208.pdf)) motivates
comparison and measurement; without representative labeled data, production
routing deliberately remains deterministic. The document boundary also follows
the recommendations on [prompt injection resistance](https://openai.com/index/designing-agents-to-resist-prompt-injection/).

## Shutting Down Operation Cleanly

Locally started API and worker processes are terminated in their respective foreground with
`Ctrl-C`. Stop the isolated test database only with its exact
project name:

```bash
docker compose -p hv-declaration-tests -f infra/docker-compose.declaration-test.yml down
```

Real user PDFs, manifest data, API keys and provider payloads stay
outside Git, test fixtures, reports, logs and tickets.

## Explicit queue principals (migration 0043)

New start/retry commands require a UUID `Idempotency-Key`. Keep the key when a
response is lost. An identical replay returns its `command_receipt_id` alongside
the current `queue_request_id` and import state; it never queues work again or
replaces a later principal. A fresh command against active work conflicts. The
UI retains its command key across request failures. Queue history is immutable
and tenant-safe; `initiated_by` remains the original draft creator.

Workers resolve the triggering actor from the current receipt, then check active
writer membership under the shared ACL lock before claiming, heartbeat, source
reads, target-data reads, budget reservation/checkpoint and publication. Lease
recovery retains that receipt. Unknown old queue principals are not guessed from
the creator: they are cancelled as `JOB_PRINCIPAL_UNVERIFIED`. A revoked,
deactivated or non-writer actor causes `JOB_PERMISSION_CHANGED`. A currently
authorized writer can explicitly retry these permission cancellations with a new
key; ordinary user cancellation remains terminal. Run budgets still apply.

Cancellation commits separately from the failed effect transaction, fences stale
workers, closes running extraction records, and preserves source originals and
persisted budget reservations. Both pipeline- and heartbeat-origin cancellation
are reported as permission changes. Already-dispatched provider work cannot be
recalled; its output cannot publish after revocation. No database transaction is
held across PDF parsing, page rendering or provider I/O. Populated downgrade refuses to erase
queue history; repair forward. Worker readiness requires the new queue column.

This is an organization-level live-principal prerequisite. It does not grant a
property-source or accounting-scope entitlement, automatically classify old
sources, activate live providers or satisfy the full B1 rollout gate. Browser
acceptance uses synthetic local originals and the production cancellation helper
against the guarded disposable database; it does not run a live provider.

Interactive declaration endpoints also revalidate the current writer role inside
the transaction that handles source access and review/approval. A role downgrade
between initial authentication and this check returns 403; deactivation or missing
membership returns 401. A role change concurrent with an already-authorized short
request waits for that request's shared ACL lock. Subsequent requests use the
changed permissions. Approved property-document metadata/PDF routes require a
currently active member; their unclassified read-only compatibility is retained.
This check does not confer accounting-scope access. The optional knowledge-provider
workflow uses the separate per-phase principal checks described below.

## Knowledge-query principal checks (migration 0044)

Approved knowledge overview, rules and lexical search now use the live-member
request guard. Optional Q&A persists the initiating actor on its content-free cost
ledger and checks active membership separately before source retrieval, reservation
and final answer validation. Read-only members retain current approved-knowledge
compatibility. No database transaction spans provider network I/O.

Deactivation or membership removal during provider execution suppresses the answer
with HTTP 401. A separate cancellation transaction records `QA_PERMISSION_CHANGED`
and conservatively retains the full reserved amount as estimated cost, with a safe
audit event. Already-dispatched provider input cannot be recalled. Historical null
actors stay unknown; an active member can expire a timed-out reservation without
adopting its identity. Downgrade refuses to erase recorded principals; repair
forward. This is an organization-level prerequisite, not accounting-scope source
classification or authorization to enable a live provider.

## Scheduling metadata before source access (migration 0046)

Workers require `next_declaration_queue_metadata` and
`lock_declaration_lease_metadata` at startup. These tenant-fenced helpers expose
only bounded scheduling/fence metadata and perform ACL-before-row locking. The
worker resolves and sets its persisted queue principal before loading the import
through ordinary RLS. A hidden revoked job can still be safely cancelled through
the existing exact-fence cancellation helper; absence from a source query is not
used as evidence that no job exists. Lease expiry is checked with the database
clock after lock waits. Provider I/O remains outside database transactions.

No production source policy is enabled by this migration. Property-data-domain
checks must join queue-principal resolution before that later activation. Do not
remove the source-query policy to make an actorless worker find jobs. Downgrade
removes only the two scheduling helpers and preserves all queue and source data;
updated workers will report schema-not-ready until the helpers are restored.

## Database worker commands (0070)

Declaration workers require their own `app_worker` connection through
`HV_DATABASE_URL_WORKER`. There is no fallback to the web URL. Provision the login
before migration; fresh development/production bootstrap scripts include it. For
an existing production volume use the separately authorized procedure in DEPLOY.md.
Never reset a populated volume to rerun init scripts. The web container must not
receive the worker credential.

`app_worker` inherits only `app_rw` and has no member roles. Neither runtime role
may have superuser, BYPASSRLS, CREATEROLE, CREATEDB or REPLICATION. Worker sessions
validate the role and membership graph; losing inheritance also fails validation.
Claim/renew/transition are worker-only database commands. Manual cancellation
remains a current-user prepare operation. Lease duration is 30–3600 seconds and
database time owns expiry/scheduling. An old token cannot renew or complete work.

Strict processing state and maximum budget cannot be changed with raw runtime SQL.
Reclaim closes abandoned attempts and installs a new fence. Reservation/accounting
corrections remain part of the trusted pipeline; recorded overrun evidence must not
be dropped merely because it exceeds a configured prospective budget.

The existing NEEDS_REVIEW revision increment is still a compatibility exception,
not proof that candidate review and audit happened atomically. Worker child-row
and review-effect command closure remain required before source activation. Do not
infer B1 completion from the accepted lifecycle-command increment.

## Worker output receipts (0071)

Workers require the effect-access helper and private context table at startup.
Each output transaction obtains its current lease through
`lock_declaration_lease_metadata`; only the actual app_worker login receives a
private receipt keyed to that database connection and transaction. Ordinary runtime
SQL cannot read or write the receipt table. A new transaction must validate the
lease again; setting a token GUC is insufficient. Expired receipts are removed in
bounded, nonwaiting batches. The table contains no source text or approval history.

Strict output triggers guard runs, passes, candidates, evidence, issues and page
counts. Manual cancellation/reclaim retains trusted cleanup. Downgrade refuses
ENFORCED/PAUSED organizations and takes NOWAIT dependency locks. Source activation
uses the current guarded lifecycle; review current coverage and
follow the [activation runbook](accounting-scope-setup.md#owner-activation-runbook).

## Atomic review decisions (0072)

Review endpoints use `apply_declaration_review` in their caller transaction. The DB
owns human actor/time, candidate versions, revision and canonical no-value audit;
strict raw writes cannot stamp those independently. The actual worker login is
excluded even though it inherits ordinary runtime function privileges. Expired or
revoked review authority returns a conflict instead of leaking SQL or source data.

Changing a decision conservatively reopens managed resolved issues. The service
refreshes DB-owned state and runs the existing graph validator before committing.
Its issue writes and complex correction semantics are still explicitly untrusted
compatibility boundaries before activation. Downgrade refuses strict mode, takes
NOWAIT locks and preserves decisions/audit history. Release gate is 0072; this code
change does not authorize deployment or source activation.

## Trusted semantic review connection (0073)

Set the API-only `HV_DATABASE_URL_REVIEW` to the independent app_review login.
Missing/unsafe configuration fails closed; ordinary web and worker connections
cannot substitute. The review role must have no inherited roles/delegates, ownership,
elevated flags, public-schema CREATE or unexpected table/column privileges. It reads
only the four review graph tables and executes narrow graph-lock/decision/issue
commands. Existing-volume provisioning requires the separately authorized procedure
in DEPLOY.md; never reset a populated volume to recreate roles.

Review mutation requests keep one transaction for authenticated context, live writer
check, graph locking, canonical correction, decision, revalidation and issue effects.
A private five-minute transaction receipt binds the final command. The repository
submits status-only effects and the DB supplies/restores canonical resolution
metadata. Role/receipt failure rolls back the transaction. Private receipts contain
no source text and expire via bounded nonwaiting cleanup.

This protects the review pipeline against ordinary runtime SQL, not compromise of
the trusted API process or review secret. Worker provider processing never receives
that credential. Source activation requires the current reviewed coverage and
guarded lifecycle;
follow the [activation runbook](accounting-scope-setup.md#owner-activation-runbook).

### Review retry receipts (0074)

Both review mutation endpoints accept an optional UUID Idempotency-Key header.
First-party clients retain the key for an unchanged request after a network failure;
a changed revision, selection, decision or correction starts a new key. Replays
recheck current live writer and source access, then return the original response
without applying another decision. Refresh current review state after success.
Keys are actor/operation scoped; bulk candidate ordering is canonical. Changed
content under the same key returns IDEMPOTENCY_KEY_CONFLICT (409).

The private declaration_review_receipt table is accessed only by the isolated
review runtime command; do not grant direct runtime access. Its response can contain
source-backed candidate/evidence data and must remain protected as source material.
Do not purge receipts to resolve retry conflicts. A schema downgrade with receipts
refuses to discard them; disable writers or forward-repair instead. Source activation
uses the current reviewed coverage, retention/team decision and
guarded lifecycle described in the [activation runbook](accounting-scope-setup.md#owner-activation-runbook).

### Indirect report source coverage (0075)

Strict-mode account statements/open items/deadlines require one reviewed accounting
scope and complete current source READ access, including payment-only recipients.
Missing source access is a denied report, never a partial balance or empty overview.
Dunning snapshots and their provenance follow the same property audience. PDF export
also requires source EXPORT; financial permissions remain independently necessary.
PAUSED keeps authorized disclosure available and blocks dunning effects. Unclassified
legacy dunning notices require reviewed provenance before strict-mode disclosure;
contact access alone is insufficient. Privacy checks retain hidden issued notices
as blockers. Do not remove source policies or rewrite historical notices to resolve
coverage gaps. Activation requires reviewed legacy coverage and the guarded
transition; use the
[activation runbook](accounting-scope-setup.md#owner-activation-runbook).


## Organization settings (0178)

See [deployment and upgrade instructions](../../DEPLOY.md#organization-ai-settings-and-upgrade-to-0178).
The administrator may set a shared monthly import/Q&A limit in USD. Only known
prices are enforced; unknown-price models remain selectable and the page explains
that they are not limited. Known requests explicitly use the standard service tier
and no implicit SDK retry. Monetary unknowns are recorded as unpriced zero estimates,
not as a claim that OpenAI is free. Token/time limits remain bounded. The extraction
harness version is `7` (compatible endpoints `9`, Anthropic fingerprint `v6`), with wire schema version `3`. The adapter converts only
the two known, disjoint `kind` unions to `anyOf` after the SDK's strict-schema
transformation. Parsing, reservations and schema fingerprints use that same
wire model, except for the inventory pass, whose request narrows an entry's
`document_key` to the keys of the request while its fingerprint stays on the
model without that narrowing (see "Inventory keys" below); inherited local
validation remains unchanged. Offline SDK transport
checks verify serialization, not live provider acceptance. Pass metadata states the
reasoning effort that the answered request carried (`default` when none was sent, see below).

The native OpenAI adapter receives each response through the SDK's `responses.create`, with the request body
the SDK's `responses.parse` sent before, and validates usage, refusal and completion before it parses the output
text with the SDK's own parser: `parse` validates the text first, so an answer cut off in the middle of its JSON
raised there and lost its status and usage. The compatible and Anthropic transports are unchanged. The version `5`
changes the harness fingerprint of the native requests and, for `gpt-5.6-luna`, their explicit prompt-cache key, so the
first request of each pass after an upgrade writes a new cache prefix; a stored run keeps the harness version it ran with.

**Reasoning effort (native OpenAI requests).** Every request of the native adapter, in every stage that uses it (the
declaration passes, the page reading, the invoice extraction and the inbox extraction), carries a reasoning effort:
`medium` for `gpt-5.6-luna`, `none` for `gpt-5.4-mini` and `low` for every other model, because a model that reasons
spends its reasoning tokens on the same output allowance as its answer. The effort is a documented constant, not a
setting (an effort passed when the adapter is constructed, which no deployed path does, still wins). When OpenAI rejects exactly that
parameter, the same request is sent once more without it, inside the same call: the rejection is a 400 whose error
object names the request field `reasoning` or `reasoning.effort` and whose `code` is `unsupported_parameter`,
`unsupported_value`, `invalid_value` or `unknown_parameter` (the structured fields, never the message text). The rejected request was not
processed and reports no usage, so the call keeps its one reservation and is settled at the usage of the request that
was answered; a failure of the second request is classified as any other, and a schema repair after the fallback
does not send the parameter again. The rule is the same for every model, so `gpt-5.6-luna` and `gpt-5.4-mini` are retried
without the effort only when their own request is rejected by those structured fields. The pass ledger row states the
effort of the answered request: `low` (or the model's known effort), and `default` for a request that was sent without
one, as after the fallback. The compatible and Anthropic transports never send a reasoning parameter. The version `6`
changes the harness fingerprint and the explicit prompt-cache key of `gpt-5.6-luna` requests in the same way as the
version `5` did; the cache key does not name the effort, so the fallback request keeps the key of the rejected one.

**Inventory keys (harness `7`, schema `3`, prompt `declaration-v5`; issue #876).** Every request of the inventory pass
names the keys it may be answered with. The request of each batch carries the instruction (`INVENTORY_PROMPT`) and a
schema in which an entry's `document_key` allows exactly the `document_key` labels of the pages of that request, so a
batch that holds only some pages of a file still answers with that file's key; `target_document_key` stays free text.
This holds for the native OpenAI, the compatible and the Anthropic transport alike. The reservation is estimated over
that schema, and an answer with another key is a schema failure like any other (one repair request, then
`PROVIDER_SCHEMA_FAILURE`), also from a server that does not enforce the schema, because the answer is validated
against the same type. The fingerprints of the pass (`schema_sha256`, `harness_sha256`, the cache key) stay those of
the static contract: the keys of a request are request data like its pages and differ with every import, and a
fingerprint that followed them could no longer be compared between two imports of the same case (the recorded
evaluation compares them). The pass ledger therefore shows one `schema_sha256` for the inventory of every import.
A run keeps the prompt, schema and harness version it ran with. An import that ran under the earlier versions and is
retried after the deploy opens a new run under the current ones and asks for its inventory again (no model answer is
stored); the earlier run and its ledger rows are not changed. The first request of each pass after an upgrade writes a
new cache prefix.

Prompt version `declaration-v4` (kept by `declaration-v5`) also tells the extraction passes that a page may be a
transcript with `[table]` blocks and `[unreadable]` markers (the reading stage's own prompt
is `reading-v1`). It supplies the exact fixed-core subject-prefix and
field pairs to core extraction, reconciliation and recheck. The instructions
and local validator read one immutable contract; existing subject-key forms,
value-type checks, evidence requirements and wire schemas remain unchanged.
Usage-type instructions require TEXT and the existing canonical values WOHNUNG,
GEWERBE, STELLPLATZ or SONSTIGE, preserving original source wording in raw values
and evidence. Approval validation remains exact; no aliases are coerced.
Descriptive inventory is not duplicated as a normative rule or additional fact.
A genuine source-supported clause may refer to core facts; unclear explicit clauses
retain clarification flags. Source silence does not create a missing-rule issue.
Genuinely out-of-core relevant facts use the existing `additional_facts` shape.
The synthetic contract oracle verifies local decisions and serialized SDK
instructions; it does not establish live extraction quality or acceptance.

SDK parse failures emit `event=DECLARATION_SCHEMA_VALIDATION` in the declaration
worker/container logs. Retrieve this event from those logs; support bundles
deliberately omit its details. It contains only allowlisted exception/error codes,
pass/stage enums and schema-defined location prefixes. The four fixed-core
after-validator codes distinguish unsupported fields, MEA types, denominator
types and area types. Retention is bounded to eight errors, twelve location
segments, array indices from 0 through 9999 and 2048 UTF-8 bytes per event.
Unknown path suffixes and codes are discarded or replaced by fixed fallbacks.
Messages, inputs, contexts, bodies, source text and credentials are excluded.
Durable pass metadata, validation decisions and usage/retry limits are unchanged.

For Luna Q&A, explicit cache mode is sent without breakpoints, disabling implicit cache writes and their surcharge. No cache parameter is added for unverified models.

## Recorded product-import regression checks

`--provider recorded` performs only an actor/organization-scoped, read-only,
repeatable-read collection from the product database. It never reads organization
AI credentials or constructs a provider. The approved synthetic input digest is
checked against its import-bound document and canonical tenant storage key, then
verified against the stored bytes. Local bounded preflight checks evidence;
no source or response content is retained in the report.

Use a **new import/draft**, uploading the same approved Lindenallee scan; do not
retry the retained failed import. An older failed import in the organization is
irrelevant. Eligibility requires this new draft to have no extraction runs, no
queue requests and zero provider attempts. Capture requires exactly one terminal
extraction run and one `START` receipt matching the current queue request. The
job's `attempt_count` counts provider requests: a healthy four-pass import normally
has at least four, so it is deliberately **not** required to equal one.

Start once through the product. Pause the blind checker when review becomes ready,
**before the first accept, correction or rejection**. The coordinator captures the
original pending proposals without showing gold or scores to the checker, then
resumes that same import. Reviewed/applied or ambiguous graphs are refused;
corrected values and the final created property are never model-scoring inputs.
Keep the frozen report: it remains a reusable baseline after review.

The portable runtime contains `backend/app`, not test fixtures. From the reviewed
checkout root, send only the committed synthetic gold through stdin. Set
`EVAL_APP_CONTAINER` to the full app container ID recorded as owned by the
separately authorized disposable installation. Inspect only that ID and verify
that it is running, its Compose project matches the ownership record, its service
is `app`, and its immutable image ID matches the recorded reviewed build:

```sh
docker inspect --type container \
  --format 'running={{.State.Running}} project={{index .Config.Labels "com.docker.compose.project"}} service={{index .Config.Labels "com.docker.compose.service"}} image={{.Image}}' \
  "$EVAL_APP_CONTAINER"
```

After those checks, set `EVAL_ORG`, `EVAL_ACTOR`, `EVAL_IMPORT` and an owned
`EVAL_REPORT` path for that installation and its current actor. `docker exec`
uses the verified running container without requiring installer-derived Compose
variables. These commands do not start extraction:

```sh
recorded_gold() {
  python3 -c 'import json,pathlib; d=json.loads(pathlib.Path("backend/tests/fixtures/declaration_eval_cases.json").read_text()); print(json.dumps({"recorded_import_cases":d["recorded_import_cases"]}))'
}
recorded_check() {
  docker exec -i "$EVAL_APP_CONTAINER" \
    python -m app.evals.declaration_knowledge --provider recorded --fixtures /dev/stdin \
    --organization-id "$EVAL_ORG" --actor-id "$EVAL_ACTOR" --import-id "$EVAL_IMPORT" \
    --case-id lindenallee-12 "$@"
}
recorded_gold | recorded_check --check-eligibility
# After the one manual start reaches untouched review readiness:
recorded_gold | recorded_check > "$EVAL_REPORT"
```

Stdin creates no gold file in the container. Keep reports only in explicitly owned
evidence locations; remove disposable copies after preserving the required result.
To compare saved captures without database or provider access, run from `backend/`
with absolute report paths:

```sh
uv run --locked --extra dev --extra ops python - "$BASELINE_REPORT" "$CANDIDATE_REPORT" <<'PY'
import json, sys
from app.evals.recorded_import import load_recorded_report, compare_recorded_reports
comparison = compare_recorded_reports(load_recorded_report(sys.argv[1]), load_recorded_report(sys.argv[2]))
print(json.dumps(comparison))
raise SystemExit(1 if comparison['regressions'] else 0)
PY
```

Alternatively, `--baseline-report` compares during capture when the validated
baseline file is accessible to that CLI process. Only bounded collector-report
shapes are accepted, never hand-authored raw actual/scoring JSON. Preserve reports
exactly as collected; provenance relies on the trusted product database and the
unedited artifact. Input and gold digests must match. Model, resolved-model,
reasoning, prompt, schema and harness changes are explicit; a difference is not
attributed solely to the model.

Recorded report format `2` uses scorer `recorded-final-output-v2`, independently
of the provider harness version. Comparisons refuse format-1 or differing scorer
reports with `INCOMPATIBLE_RECORDED_SCORER`; preserve older artifacts without
reinterpreting their scores. The independent input/gold data is unchanged.

Reports localize case/stage/assertion outcomes. Success ledger entries establish
execution and parsed-schema success only. Intermediate semantic outputs were not
retained and remain `UNAVAILABLE`; all value assertions are `final_output` on the
original merged proposals. The independently labeled scan checks units 1/2/G1,
their apartment/apartment/parking usage kinds, shares 450/450/100 over 1000 and
source-bound evidence. It contains no governance/allocation rule: hallucinated
true-rule origins fail, while additional-fact notes sharing RULE storage are not
counted as normative rules. Rule-effect recall is not tested. The `blockers`
assertion includes BLOCKER issues and the existing rule/additional-fact
clarification predicate, even when its issue severity is WARNING. Ordinary
pending human review and unrelated warnings do not fail that assertion. Capture
still refuses all reviewed/corrected proposals before using this predicate.
Failed terminal runs cannot
pass merely because earlier provider calls succeeded. Provider-reported totals
are separate from conservative job accounting; unknown usage stays unknown and
costs are estimates, not billing. Comparisons cover observed assertions only,
never automatic promotion or a “geprüft” model badge. Existing four-way live
evaluation gates and runners are unchanged.


New-property approval projects repeated unit citations to one canonical fact
source when document, page, printed page, section and quote are identical and
only the optional field path differs. Every original candidate evidence row
remains immutable. Distinct content or metadata still triggers the existing
collision guard; independent document/page sources remain traceable with their
original deterministic source indexes. This projection applies to new-property
`UNIT_CREATE` proposals.
