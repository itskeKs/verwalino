# Versioned extraction evidence foundation

Migration 0096 records each newly completed A2 invoice extraction as an immutable
revision of the shared original reference. Retries no longer leave only the
latest proposal available. Existing invoice processing states and the latest
`extraction_payload` remain compatible with their consumers.

## Boundary and provenance

The existing fenced worker completes its attempt, updates the latest proposal,
appends an extraction revision and its field rows, and records an outbox event in
one transaction. The recording command requires the actual worker runtime,
current initiator authority, matching fence, unexpired lease, terminal attempt
state and an exact match to the accepted source payload. A failed transaction
rolls back all of those effects. Domain cost values are never written by this
adapter, so manually corrected costs survive reprocessing.

Each revision retains its typed original and invoice-attempt foreign keys,
immutable original hash, processor and schema versions, outcome, error code and
proposal. Field evidence retains typed decimal, date and text values with optional
processor confidence. The existing parser supplies whole-source page hints but
does not supply reliable field-specific page/bounding-box/XML-path evidence.
Those hints stay in the processor payload; field locators are explicitly
`unlocated` and field validation is `unverified`. Typed validation is not invoice,
tax, source-completeness or accounting approval.

The private IPC result is validated before acceptance. Invalid shapes, non-finite
or malformed numeric fields, invalid dates, unsupported processor versions and
invalid source-page hints become a bounded `processor_failed` outcome without
proposals. A corresponding database boundary validates schema and typed values.
Historical payloads are retained in their existing location; no unverifiable old
attempt history is invented or backfilled.

## Reads, events and rollback

`GET /api/document-refs/{id}/extractions` returns bounded revision headers without
large payloads. `GET /api/extraction-revisions/{id}` returns a revision and its
fields. Both inherit the original registry's actual source RLS. Revoking source
access also hides derived evidence. There is no writable evidence endpoint.

The shared outbox gains a typed `document_ref_id` arm for `document.extracted`;
intake events retain their existing original foreign key. A constraint requires
exactly the appropriate reference for each event type. Evidence and its event
commit together under one unique effect key. Event delivery is still outstanding.
Coverage dependency digests include revisions and fields. Populated downgrade
refuses to erase this evidence; empty rollback restores the earlier function body
without changing its OID.

## Verification and remaining work

The first API contract test failed at the missing history route before
implementation. Five focused tests then passed for append-only retries,
cross-organization denial, unlocated field labels and malformed-result handling.
The expanded extraction, existing invoice worker/custody, financial boundary,
logical source, registry, intake and RLS suite passed 88 checks and exposed one
old roundtrip fixture that needed to clear the new evidence inside its disposable
savepoint. After that fixture repair, 11 focused checks passed, including atomic
finish rollback/replay, preserved manually edited costs, revoked source access,
immutable rows and guarded migration rollback. The deployment simulation passed
at required head 0096 without any real deployment.

Independent read-only review found validator exceptions for exceptionally large
confidence integers, lone surrogates and a PostgreSQL-incompatible NUL string.
Validation now returns false for these values, and worker integration tests cover
conversion to a recorded failure. All seven final malformed-output cases passed.
The 88 unaffected passing checks and deployment simulation are reused; the
validator and failing fixture received focused follow-up checks.

This increment is an existing-worker evidence adapter. It does not move invoice
or declaration jobs to the shared runner, process new intake logical children,
claim precise field locations, provide a field-review UI, or accept B2. Those
remain explicit follow-up work alongside outbox delivery and scanner qualification.
