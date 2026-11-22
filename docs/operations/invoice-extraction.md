# Ordinary invoice extraction

Uploads persist immutable source bytes, an invoice document and a queued attempt
in the request transaction. The HTTP response means saved, not extracted or
confirmed. Existing released/legacy extraction payloads are not backfilled.
Version uploads also enqueue the new revision. The worker prepares a proposal of
ten fields; it books no cost and approves nothing, and a person reviews every
proposal before anything is posted.

## Two paths

The worker first inspects the original locally, in a killable child process
(`core/invoice_pdf.py`), and decides which path reads it.

- **Structured invoices stay local.** XRechnung XML and PDFs with embedded
  ZUGFeRD/Factur-X data are validated and read by the deterministic parser
  (processor version `invoice-local-v1`). No provider is built and nothing leaves
  the host; the organization's AI settings do not matter. The parser also reads the
  payee's account from the payment data (UBL `PayeeFinancialAccount`, CII
  `PayeePartyCreditorFinancialAccount`) into the tenth field, see below.
- **Unstructured PDFs go to the organization's two models**
  (`declarations/invoice_pipeline.py`, [ADR 0017](../adr/0017-model-document-reading-replaces-tesseract.md),
  processor version `invoice-model-v1`). A page with a text layer of at least 40
  non-whitespace characters is used as it is. Every other page is rendered with
  Poppler, one page at a time, and read by the **document reading model**
  („Dokumente einlesen“) into a transcript. The **extraction model**
  („Informationen extrahieren“) then fills the ten fields (vendor, invoice date, number,
  gross amount, VAT amount and rate, service period, the vendor's IBAN) and proposes one
  seeded cost category. There is no Tesseract step, no fallback and no pattern
  matching on what the models read; a model that cannot take images ends the attempt
  with a reason, it never switches model or provider. Confidence per field is the
  model's own, and a value the invoice does not state is empty.

## The vendor's IBAN: a tenth field for review

The proposal carries `vendor_iban`, the account the invoice asks to pay into, beside the nine fields: a value
(null when the invoice states none) and a certainty, like its neighbours. The invoice page shows it as
„IBAN des Rechnungsstellers“ in groups of four, for a person to compare with the invoice before paying. Nothing else
reads it: it is not copied into the cost position, matches no bank line and creates no payee.

**A wrong IBAN is worse than none.** The extraction model reads it as the invoice prints it (with or without
spaces, in either case); the typed answer is then normalised (no spaces, upper case) and kept only when it is a valid
IBAN by the one validator of the project (`core/iban.py`, also the SEPA export's): a SEPA country, its length and
the mod-97 check digits. Anything else (a misread digit, a truncated value, text, an IBAN of a country outside the SEPA
area) is stored as not stated, with certainty 0 and without failing the proposal or costing a repair request; the
value is never logged. A structured invoice takes only the payee's account (never the payer's of a direct debit), and
states none when its payment data names no valid IBAN or two different ones. The IBAN is not part of the overall
certainty of the proposal and has no evidence row. A proposal stored before the field existed reads as not stated.

The field lives in the stored proposal (`invoice_document.extraction_payload`, a JSON document) and in the revision
the worker records through `record_invoice_extraction_revision`, whose installed function accepts the optional key
(0197 changes the function; no further revision). The prompt and schema versions are `invoice-v2` and `invoice-2` since the inbox shares the contract (#501): the
model also names the page and the passage each stated value was found on, which the invoice path ignores and the inbox
checks against the pages of its document. That source is advisory: a page that is not a whole number from 1 to 200, a passage
that is empty or longer than 300 characters or one half of the pair alone is no source (the field reads as if none were stated),
and never fails an answer that is valid without it; the values keep their strict validation. The processor version `invoice-model-v1` stays: a proposal stored under
`invoice-v1` is not rewritten.

## Opt-in: nothing is sent without it

An unstructured invoice reaches a provider only when the organization's AI import
switch is on („Teilungserklärungen importieren und Rechnungen auswerten“), a key is
stored, an extraction model is chosen and the provider confirmation was given **with the text
that names invoices** (confirmation text version 2 or 3; one table in `services/ai_settings_service.py` maps each text version to the kinds it names, and every path asks it). A scan also needs the reading model; a PDF whose
pages all have a text layer needs only the extraction model. An upgrade turns nothing on.

An organization that confirmed the first text (version 1) keeps its declaration import
unchanged. Its invoices stay on the host, and the invoice says in German that the
organization has to confirm again (`ai_invoice_confirmation_missing`), until an administrator
ticks the confirmation once more in Einstellungen; the key is not entered again. Without a
switch, key or model the attempt ends `unavailable` with `ai_import_disabled`; a scan without a
reading model ends with `ai_document_model_missing`; a key that cannot be decrypted
with `ai_key_unreadable`. In each case no provider is built, the original is kept and no proposal
exists; the manual path (**Kostenposition erstellen**) stays open.

**An installation that does not process invoices says nothing about them.** Where
`HV_INVOICE_EXTRACTION_ENABLED` is `false` (the operator's choice for an installation that processes no invoices) the settings card
„KI für Ihre Organisation“ is about the declaration import and the documents of the inbox ([inbox flow](inbox-review-chain.md#reading-with-the-organizations-models)), which every
installation reads with the organization's models: its switch reads „Teilungserklärungen importieren sowie Dokumente im
Posteingang auswerten“, and neither the description nor the confirmation text names invoices (the settings answer carries
`invoice_processing_enabled` and `inbox_processing_enabled` for this). A confirmation given there is recorded as text
version 4, which names declarations and inbox documents but no invoices, because that is the text the organization saw. If
invoice processing is switched on later, that confirmation does not cover invoices: none is sent until an administrator
has seen and confirmed the text that names them (`ai_invoice_confirmation_missing`, text version 3), exactly as for an
organization that confirmed before invoices were covered; its inbox documents stay covered.

**The opt-in is read again before every request.** The reservation of each request (the transaction that
also extends the lease and checks the monthly limit) re-reads the organization's settings under the budget lock that every
settings save holds, with the same decision the worker takes before the run. Turning the switch off, losing the
confirmation that names invoices, removing the reading model of a scan or making the key unreadable while an invoice
is being read therefore stops it before its next request, with the code above; what was already requested stays
in the ledger and on the usage card.

**A replaced destination or model is read again too.** The clients of a run keep the provider, endpoint, key,
reading model and extraction model it started with. The same re-read compares them with what the organization holds
now; if any of the five differs (a different provider, a different base URL, a replaced key, another reading or
extraction model), the run stops before its next request and nothing is sent to either destination. The attempt ends
`unavailable` with `ai_settings_changed`; the requests already made stay in the ledger and on the usage card. Saving the
same settings again is no change (the key is compared by its value, not by its stored token). A new start reads the
document again, costs again and uses the current settings.

**What changed with this path.** Without the opt-in, a PDF with a text layer no longer gets a locally read
proposal: it ends `unavailable` with `ai_import_disabled` (or `ai_invoice_confirmation_missing`), as a scan does, and the
amounts are entered by hand. Earlier releases read such a PDF with a local pattern matcher. Structured invoices
(XRechnung, ZUGFeRD/Factur-X) are unaffected: they are still parsed locally, with or without the opt-in.

## Bounds, metering and the budget

Per attempt (documented constants in `declarations/invoice_pipeline.py` and `core/invoice_pdf.py`, no
configuration keys): at most **10 pages** and **60,000 characters** of text (text layers and
transcripts together), **300,000 input and 64,000 output tokens**, **0.25 USD**, and 6,000 (reading)
or 4,000 (extraction) output tokens per request (ten fields with a value, a page and a passage of at most 300 characters each
answer in about 1,250 tokens; the rest of the allowance is the model's reasoning). Pages are rendered at 180 dpi, at most 20 million
pixels each, within 30 seconds per page and 120 seconds of local work; waiting for a model is not
counted. A larger original ends `needs_review` or `unsupported` with `invoice_page_limit`,
`invoice_ai_limit` or `image_pixel_limit` before the request that would pass the bound. The text bound
(`invoice_text_limit`) is checked after every page: once the text of the pages read so far passes 60,000
characters no further page is read or paid, but the pages read until then were requested and stay in the ledger. The upload limit is 25 MiB, and the local inspection refuses PDFs of more than 200 pages
(`pdf_page_limit`), encrypted or invalid PDFs.

Every request is reserved before it leaves and settled with its usage in the ledger
`invoice_model_call` (migration 0197): in short transactions of its own, under the organization's
budget lock, with the monthly limit checked at the reservation. Failed requests keep their usage; a
usage the provider did not report stays at the reservation, so unknown is never booked as zero: only a request
that provably never left costs nothing: a failure raised before anything was sent, or one that failed while the name was resolved, in
the endpoint guard, while connecting or in the TLS handshake (`ProviderFailure.not_sent`, decided from the phase of the
request and the HTTP library's error types, never from message text). It is settled at zero (error `PROVIDER_REQUEST_NOT_SENT`),
the attempt ends with `provider_unreachable`, and neither the usage card nor the monthly total counts it, so retries against an
unreachable provider never use up the limit; a timeout, a reset or any other failure after the request may have been written
stays at its reservation. The declaration import gives the reservation of such a request back the same way. A worker shutdown while a
request is out settles it the same way (error `WORKER_CANCELLED`) and leaves the attempt to be reclaimed after its lease;
a request lost with a killed worker process stays reserved and counts at its full reservation, with no later settlement. Both stages count towards the same
monthly total as the declaration import (`organization_ai_monthly_usage`), so no invoice obtains
calls outside the budget. A reached limit ends the attempt with `organization_ai_budget_exceeded`
before the next request is sent; a model without a known price is not limited, as for the import.
Each reservation also extends the worker's lease and re-checks that the person who started the attempt
may still work on the document: a lost permission cancels the attempt (`scope_permission_changed`), and a lost lease or a
released or replaced original ends it before the request is sent.

Requests run outside any database transaction, one invoice at a time per worker. Everything is
organization-scoped (forced row-level security, composite foreign keys): another organization
neither sees the usage of an invoice nor can read its ledger rows.

## Retry costs again

An attempt that ends in a failure can be started again with **Verarbeitung erneut starten** (at
most three attempts per original). Page transcripts are not stored, so a new attempt reads every
scanned page again and pays for it again; the invoice's usage card adds up all attempts, including
failed ones. The explanation of a failure that followed a request says so; an attempt that ended with
`provider_unreachable` sent nothing, so starting it again costs nothing by itself. The original is never
changed and no cost is posted.

## What the invoice screen shows

Under its status line the invoice screen explains in German how the processing ended, from one map
of reason codes (`frontend/src/lib/invoiceProcessing.ts`; a test fails for a stored code without a text).
The card „Verbrauch der KI-Verarbeitung“ lists the requests of the invoice per stage and model
(`GET /api/invoice-documents/{id}/model-usage`), also after a failure; an unreported usage is
shown as reserved and a model without a price as „kein Preis bekannt“, never as 0,00 USD.
The ledger is working data: it is **not part of the organization export**, and the retention catalog
classifies it `delete`, so it goes with the organization when that is erased (see
[organization export](organization-export.md)).

## Worker and deployment

Run a worker in the same application environment and storage root, explicitly
selecting the authorized organization UUIDs:

```sh
env -u USER python -m app.workers.invoice_extraction --organization ORGANIZATION_UUID
# Repeat --organization for round-robin processing of configured organizations.
# Add --once for one bounded pass.
```

The worker uses the isolated app_worker login and tenant context. Claims commit before source reading or
extraction. A private child process performs all parsing; results commit in a new
transaction only if the attempt token and lease still match. A crash
expires the lease; a later claim records timeout and makes the next attempt,
up to three attempts per original. A late worker cannot publish. Human cost edits
are never changed, and release/supersession cancels publication of proposals.

Local limits for the inspection: 25 MiB input; valid, unencrypted PDFs of at most 200 pages; 120 seconds
for the child process, which also bounds processing after a worker crash. Linux additionally caps
the child address space at 1 GiB. A page that cannot be read ends the attempt and publishes no financial
proposal. Outcome records carry processor
version and source page numbers; these are extraction provenance, not accounting
approval or per-field semantic proof.

The worker needs the PDF renderer (`pdftoppm`) and **no Tesseract**; its healthcheck checks storage,
the worker role, queue access and the renderer, and calls no provider
(see the [development environment setup](../../CONTRIBUTING.md#development-environment)). Queue admission is serialized per
organization, default 100 active documents (`HV_INVOICE_QUEUE_LIMIT`, 1–1000).
Disabled extraction or full queues save the original with an explicit manual-review
status and no new queue entry. Each worker handles one invoice at a time. This is not yet the shared fair-job
infrastructure of M-B or measured professional capacity of M-G.

To reach the organization's provider the worker needs the session secret (the key is stored encrypted under it)
and the same provider route as the declaration worker.

**Which process processes invoices.** Exactly one per installation, so that no waiting invoice is claimed by two
processes and none waits for a process that does not exist. The rule is one function, `claims_invoices` in
`core/invoice_claimant.py`; each stack file gives it the worker kind and the service's own value of
`HV_INVOICE_EXTRACTION_ENABLED`, and `tests/test_invoice_claimants.py` pins the result for every stack file.

| Stack | The one process that processes invoices | The other workers |
| --- | --- | --- |
| `docker-compose.prod.yml` | the dedicated `invoice-worker` (`backend` and `egress` networks like `worker`, `HV_SESSION_SECRET`; [DEPLOY.md](../../DEPLOY.md#invoice-worker-t10)) | the declaration worker `worker` has the value fixed to `false`; so has the `inbox-worker` |
| Portable stack (self-hosted installations, the hosted server) and staging | the `declaration-worker`, in its own loop ([DEPLOY.md](../../DEPLOY.md#invoice-processing-in-the-portable-stack)) | the optional `inbox-worker` has the value `false` and its default stages leave invoices out |
| Any stack with the operator's switch off | nobody: the app says processing is switched off and the settings card names no invoices | |

**The declaration worker's loop.** Each round the declaration worker takes one step of the declaration import, as before,
and then claims and processes at most one waiting invoice (`DeclarationImportWorker._process_one_invoice`), trying the
organizations in rotation. The two kinds of work take turns, so neither starves the other; a long job of one delays the
other by its duration. The invoice step is the invoice worker's own: claim, lease (150 seconds), bounds, metering and
the opt-in are unchanged. A failure of one invoice, one organization or the declaration step is logged with its type
and place only (`INVOICE_ERROR`, `INVOICE_DISCOVERY_FAILED`, `UNEXPECTED`) and ends neither the round nor the loop, and
a round without a ready declaration schema claims no invoice. SIGTERM cancels the loop, as for the invoice worker: a
request that is out is settled at its reservation (`WORKER_CANCELLED`), and the claimed invoice or import keeps its
lease and is read again once it has expired. The worker keeps its 768 MiB: it runs one task at a time, and an invoice
is bounded to 10 pages rendered one at a time.

The authenticated retry endpoint rechecks org/write access and document state;
it reuses an active attempt and cannot exceed the attempt budget. Failure never
removes the original or creates a cost. Details expose status and processing
history; manual review remains available. Logs do not contain extracted text;
document text and transcripts leave the host only to the organization's configured provider.

Rollback: set `HV_INVOICE_EXTRACTION_ENABLED=false` (portable stack: export it and let the installer recreate the
services; production: the invoking environment) to stop new claims, keep
originals, attempt history and the usage ledger readable and use manual review. Stop workers before
an application rollback. Do not downgrade a populated database: the downgrade of 0197 refuses while a
model call record or an `invoice-model-v1` proposal exists. Storage/queue
reconciliation and resource/fairness metrics remain part of M-B/M-G acceptance.

Since migration 0081, the invoice worker requires `HV_DATABASE_URL_WORKER` for the
existing isolated `app_worker` login. Supply it through the worker secret store,
as for declaration workers. The web service must not receive this credential.
The CLI uses the worker connection and has no app_rw fallback; queue metadata
commands reject the web login. Local worker tests likewise use the worker pool.

## Upload and review

Under **Belege** (vouchers), upload a synthetic invoice and open **Details**:
„Gespeichert · wartet auf Verarbeitung“ (stored, awaiting processing) changes to „Felder vorbereitet · bitte
prüfen“ (fields prepared, please review); amount and date appear without manually entering the results.
The extraction books no costs and does not automatically approve any voucher.

For the local launcher, worker CLI, and repeatable browser check, see
[the invoice-worker runbook](../../DEPLOY.md#invoice-worker-t10).
