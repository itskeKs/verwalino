# ADR 0017: Model-based document reading with two model roles

- Status: accepted
- Date: 2026-11-20
- Issues: [#65](https://github.com/itskeKs/verwalino/issues/65) (owner decision of 2026-11-10, epic), [#499](https://github.com/itskeKs/verwalino/issues/499) (first implementation); builds on [ADR 0014](0014-provider-neutral-ai-runtime.md)

## Context

Three import paths read scans with local Tesseract before a model interprets the text. The owner's
authorized three-page comparison found all six checked ownership shares with a small image-capable
model and none with corrected Tesseract. It motivates the change; it is no quality guarantee. The
earlier proposal of a separate OCR service is superseded.

## Decision

- **Two model roles, one provider.** An organization chooses a document reading model
  ("Dokumente einlesen", `document_model`) and an extraction model ("Informationen extrahieren",
  `model`, also used for property questions). Both use the organization's one provider, endpoint,
  timeout and encrypted key; there is no second credential and no third selector. One model may fill
  both roles.
- **Nothing is chosen for the organization.** `document_model` is NULL after the upgrade and never
  defaulted from the extraction model or a price list. Declaration import needs both; without the
  reading model it refuses to run and says so in German. Uploaded originals stay available.
- **Model reading replaces Tesseract path by path.** A path that reads with a model has no Tesseract
  step and no fallback: declaration import (#499), invoices (#500), inbox (#501), then removal of the
  runtime (#502).
- **Structured e-invoices stay deterministic.** XRechnung and ZUGFeRD data, also embedded in a PDF, use
  the existing parser without a model call.
- **No fallback.** A model that cannot take image input, a refusal, a malformed answer or a failed call
  ends the reading of that page with a typed error and the usage the provider reported. The application
  never switches model or provider and never falls back to Tesseract.
- **Output stays provisional.** A transcript copies what is visible; it is not a fact. Extraction,
  review, approval and source evidence keep their guards.

## The reading stage behind the provider boundary

`DocumentReadingProvider.read_page` (`declarations/provider.py`) takes one rendered PNG page (bytes,
media type, source identity, original page number) and the reading model id. It returns a
`PageTranscript` (ordered `TEXT`, `TABLE` and `UNREADABLE` blocks, tables as rows of cells, the inline
marker `[unreadable]`) with the `ProviderUsage` of the extraction passes. The OpenAI, compatible and
Anthropic transports of ADR 0014 implement it on the same bounded request path;
`create_reading_provider` binds one to `document_model`. One page is one request: the per-call output
ceiling is far below the transcript of a stack of dense pages, a malformed page costs one page, and a
failed page is read again alone. Prompt (`reading-v1`) and schema (`reading-1`) are versioned
constants; the caller, never the model, assigns the page identity. A model that cannot take the image
is recognised by the HTTP status of the rejected request (`PROVIDER_IMAGE_INPUT_UNSUPPORTED`), never
by message text. `FakeDocumentReadingProvider` records which model was asked for which page.

Consequences: organizations with import enabled choose a reading model before their next import;
reading costs a model call per page, and unknown prices stay visibly unpriced and unenforced.
