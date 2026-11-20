# ADR 0017: Model-based document reading with two model roles

- Status: accepted
- Date: 2026-11-20
- Issues: [#65](https://github.com/itskeKs/verwalino/issues/65) (owner decision of 2026-11-10, epic), [#499](https://github.com/itskeKs/verwalino/issues/499) (first implementation); builds on [ADR 0014](0014-provider-neutral-ai-runtime.md)

## Context

Three import paths read scans with local Tesseract before a model interprets the text. The owner's three-page
comparison found all six checked ownership shares with a small image-capable model and none with corrected
Tesseract: a motivation, no quality guarantee. A separate OCR service is superseded.

## Decision

- **Two model roles, one provider.** An organization chooses a document reading model („Dokumente
  einlesen“, `document_model`) and an extraction model („Informationen extrahieren“, `model`, also
  used for property questions). Both use the organization's one provider, endpoint, timeout and
  encrypted key; no second credential, no third selector. One model may fill both roles.
- **Nothing is chosen for the organization.** `document_model` is NULL after the upgrade, never defaulted
  from the extraction model or a price list. Declaration import needs both and says in German what is
  missing; uploaded originals stay available.
- **Model reading replaces Tesseract path by path** and keeps no fallback: declaration import (#499, done),
  invoices (#500), inbox (#501), then removal of the runtime (#502). Structured e-invoices stay deterministic.
- **No fallback, provisional output.** A model that cannot take the image, a refusal, a malformed answer
  or a failed call ends the job with a typed code and the usage reported; the application never switches
  model or provider and never falls back to Tesseract. A transcript copies what is visible and is no fact;
  review, approval and source evidence keep their guards.

## The reading stage behind the provider boundary

`DocumentReadingProvider.read_page` takes one rendered PNG page (source identity, page number) and the
reading model and returns a `PageTranscript` (ordered `TEXT`, `TABLE` and `UNREADABLE` blocks, the marker
`[unreadable]`) with the call's `ProviderUsage`; the OpenAI, compatible and Anthropic transports implement it.
One page is one request; an unsupported image is recognised by the status 415 or, for a 400 or 422, by the
structured identifiers of the provider's error object that name the image part, never by message text; any
other rejected request keeps the code of an extraction pass. Prompt `reading-v1`, schema `reading-1`; extraction prompt `declaration-v4`. Routing is
unchanged, as the owner decision names none: pages with fewer than `declaration_text_min_chars` characters in
the text layer go to the model, the others keep the deterministic text path.

## State as implemented (#499)

- Each call is reserved before and metered in one ledger by the model that made it (`read_page` for reading);
  both stages count against the import and monthly budgets. Usage of failed calls stays; an unknown usage
  keeps its reservation. A finished page transcript is saved with its settled usage
  (`declaration_page_reading`, 0196); a resumed job reads and pays for no page twice and keeps the two
  model identities of its first run. Transcripts are kept as long as the import's documents (until the
  organization is erased) and are not part of the organization export, so after a restore the evidence
  re-check of a scanned import is unavailable.
- The cost view lists the stages apart, then what no ledger entry attributes to a stage („Nicht zugeordnet“),
  then the total; the lines add up to the total. Failure codes have German texts on the import
  screen (original kept, nothing applied, what to do). The browser twin chooses two models in the
  settings and runs offline providers; no live provider run belongs to this ADR.
- Consequences: organizations with import enabled choose a reading model before their next import;
  reading costs a model call per page; unknown prices stay visibly unpriced and unenforced.
