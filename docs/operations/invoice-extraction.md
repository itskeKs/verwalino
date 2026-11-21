# Ordinary invoice extraction

Uploads persist immutable source bytes, an invoice document and a queued attempt
in the request transaction. The HTTP response means saved, not extracted or
confirmed. Existing released/legacy extraction payloads are not backfilled.
Version uploads also enqueue the new revision. The worker prepares a proposal of
nine fields; it books no cost and approves nothing, and a person reviews every
proposal before anything is posted.

## Two paths

The worker first inspects the original locally, in a killable child process
(`core/invoice_pdf.py`), and decides which path reads it.

- **Structured invoices stay local.** XRechnung XML and PDFs with embedded
  ZUGFeRD/Factur-X data are validated and read by the deterministic parser
  (processor version `invoice-local-v1`). No provider is built and nothing leaves
  the host; the organization's AI settings do not matter.
- **Unstructured PDFs go to the organization's two models**
  (`declarations/invoice_pipeline.py`, [ADR 0017](../adr/0017-model-document-reading-replaces-tesseract.md),
  processor version `invoice-model-v1`). A page with a text layer of at least 40
  non-whitespace characters is used as it is. Every other page is rendered with
  Poppler, one page at a time, and read by the **document reading model**
  („Dokumente einlesen“) into a transcript. The **extraction model**
  („Informationen extrahieren“) then fills the nine existing fields (vendor,
  invoice date, number, gross amount, VAT amount and rate, service period) and proposes one
  seeded cost category. There is no Tesseract step, no fallback and no pattern
  matching on what the models read; a model that cannot take images ends the attempt
  with a reason, it never switches model or provider. Confidence per field is the
  model's own, and a value the invoice does not state is empty.

## Opt-in: nothing is sent without it

An unstructured invoice reaches a provider only when the organization's AI import
switch is on („Teilungserklärungen importieren und Rechnungen auswerten“), a key is
stored, an extraction model is chosen and the provider confirmation was given **with the text
that names invoices** (confirmation text version 2). A scan also needs the reading model; a PDF whose
pages all have a text layer needs only the extraction model. An upgrade turns nothing on.

An organization that confirmed the first text (version 1) keeps its declaration import
unchanged. Its invoices stay on the host, and the invoice says in German that the
organization has to confirm again (`ai_invoice_confirmation_missing`), until an administrator
ticks the confirmation once more in Einstellungen; the key is not entered again. Without a
switch, key or model the attempt ends `unavailable` with `ai_import_disabled`; a scan without a
reading model ends with `ai_document_model_missing`; a key that cannot be decrypted
with `ai_key_unreadable`. In each case no provider is built, the original is kept and no proposal
exists; the manual path (**Kostenposition erstellen**) stays open.

## Bounds, metering and the budget

Per attempt (documented constants in `declarations/invoice_pipeline.py` and `core/invoice_pdf.py`, no
configuration keys): at most **10 pages** and **60,000 characters** of text (text layers and
transcripts together), **300,000 input and 64,000 output tokens**, **0.25 USD**, and 6,000 (reading)
or 3,000 (extraction) output tokens per request. Pages are rendered at 180 dpi, at most 20 million
pixels each, within 30 seconds per page and 120 seconds of local work; waiting for a model is not
counted. A larger original ends `needs_review` or `unsupported` with `invoice_page_limit`,
`invoice_text_limit`, `invoice_ai_limit` or `image_pixel_limit` and costs nothing it did not already
cost. The upload limit is 25 MiB, and the local inspection refuses PDFs of more than 200 pages
(`pdf_page_limit`), encrypted or invalid PDFs.

Every request is reserved before it leaves and settled with its usage in the ledger
`invoice_model_call` (migration 0197): in short transactions of its own, under the organization's
budget lock, with the monthly limit checked at the reservation. Failed requests keep their usage; a
usage the provider did not report stays at the reservation. Both stages count towards the same
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
failed ones. The explanation of a failure that followed a request says so. The original is never
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
and the same provider route as the declaration worker. In `docker-compose.prod.yml` the `invoice-worker`
joins the `backend` and `egress` networks like `worker` and receives `HV_SESSION_SECRET` and `HV_DATA_REGION`;
see [DEPLOY.md](../../DEPLOY.md#invoice-worker-t10). **The portable stack, including the hosted server, starts no invoice
worker and keeps `HV_INVOICE_EXTRACTION_ENABLED` off, so there an uploaded invoice is saved and waits for manual entry.**

The authenticated retry endpoint rechecks org/write access and document state;
it reuses an active attempt and cannot exceed the attempt budget. Failure never
removes the original or creates a cost. Details expose status and processing
history; manual review remains available. Logs do not contain extracted text;
document text and transcripts leave the host only to the organization's configured provider.

Rollback: set `HV_INVOICE_EXTRACTION_ENABLED=false` to stop new claims, keep
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
