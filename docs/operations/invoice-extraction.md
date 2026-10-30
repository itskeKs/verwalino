# Ordinary invoice extraction

Uploads persist immutable source bytes, an invoice document and a queued attempt
in the request transaction. The HTTP response means saved, not extracted or
confirmed. Existing released/legacy extraction payloads are not backfilled.
Version uploads also enqueue the new revision. No remote provider is involved.

Run a worker in the same application environment and storage root, explicitly
selecting the authorized organization UUIDs:

```sh
env -u USER python -m app.workers.invoice_extraction --organization ORGANIZATION_UUID
# Repeat --organization for round-robin processing of configured organizations.
# Add --once for one bounded pass.
```

The worker uses the isolated app_worker login and tenant context. Claims commit before source reading or
extraction. A private child process performs all parsing; results commit in a new
transaction only if the attempt token and 150-second lease still match. A crash
expires the lease; a later claim records timeout and makes the next attempt,
up to three attempts per original. A late worker cannot publish. Human cost edits
are never changed, and release/supersession cancels publication of proposals.

Limits: 25 MiB input; valid, unencrypted PDFs of at most 200 pages; OCR only for
invoices up to 20 pages, 200 dpi, 20 million pixels per page, one rendered page at
a time, 10 seconds combined rendering/OCR per page, 120 seconds total. A separate
child alarm also bounds processing after a worker crash. Linux additionally caps
the child address space at 1 GiB. Partial OCR failures publish no financial
proposal. XML and embedded XML precede OCR. Outcome records carry processor
version and source page numbers; these are extraction provenance, not accounting
approval or per-field semantic proof.

Runtime prerequisites are documented in the [development environment setup](../../CONTRIBUTING.md#development-environment).
Missing runtime dependencies yield `ocr_unavailable`. Queue admission is serialized per
organization, default 100 active documents (`HV_INVOICE_QUEUE_LIMIT`, 1–1000).
Disabled extraction or full queues save the original with an explicit manual-review
status and no new queue entry. Each worker handles one child at a time. This is not yet the shared fair-job
infrastructure of M-B or measured professional capacity of M-G.

The authenticated retry endpoint rechecks org/write access and document state;
it reuses an active attempt and cannot exceed the attempt budget. Failure never
removes the original or creates a cost. Details expose status and processing
history; manual review remains available. Logs do not contain extracted text.

Rollback: set `HV_INVOICE_EXTRACTION_ENABLED=false` to stop new claims, keep
originals and attempt history readable and use manual review. Stop workers before
an application rollback. Do not downgrade a populated database. Storage/queue
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
[the invoice-worker runbook](../../DEPLOY.md#invoice-worker-t10-alpha-active-since-2026-10-24).
