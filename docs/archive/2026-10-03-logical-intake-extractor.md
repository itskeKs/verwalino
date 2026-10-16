# Local extraction of logical intake sources

The new `app.core.intake_extraction` processor reads verified PDF page ranges,
single PNG/JPEG originals and standalone structured invoice XML. It returns
proposals bound to the original hash, byte size and exact verified page bounds.
It does not create jobs, grant original access or confirm financial values.
The durable integration must require a clean inspection before calling it.

PDF parsing and OCR use only the selected pages for proposals; evidence retains
original page numbers. Embedded XML inside a PDF is not selected by this adapter,
because it cannot establish which selected pages that attachment describes.
Standalone UBL/CII XML uses the existing structured parser. This increment keeps
its existing field heuristics and does not claim semantic invoice compliance.

A page quotation is emitted only when one exact extracted line reproduces the
field proposal. Repeated or multiline support remains explicitly unlocated.
Quotes retain text-layer or OCR method; no bounding boxes are invented. XML
locators identify the selected original element with a namespace-qualified,
indexed ElementTree path relative to its root, and retain its literal trimmed
text. Normalized values must match the existing parser's proposal. Every proposal
still requires human review; a locator does not validate financial meaning.

All document parsing, heuristic reproduction and semantic locator checks run
inside a process group with a 120-second deadline and a Linux 1 GiB address-space
limit. Images are structurally checked before OCR. At most 20 selected PDF pages
can require OCR, each with its own existing 10-second deadline. Partial failures
discard all proposed fields. The child receives a small environment allowlist
without user identity, provider keys or inherited Python module paths.

Parent-side IPC checks validate identity, types, size, selected pages and literal
page-quote membership. They do not parse XML/PDF or reproduce invoice heuristics.
Semantic evidence checks stay inside isolation. This trusts the installed child
implementation to report source text honestly; it does not cryptographically
attest OCR output or defend against arbitrary modification of that implementation.

## Verification and integration boundary

The initial import check failed before implementation. The first processor suite
passed 19 checks, and two additional checks passed for actual local PNG OCR and
child environment filtering. Independent review identified parent-side heuristic
work beyond the subprocess deadline and insufficient XML semantic validation.
The repaired suite passed 23 checks, including rejection of invented XML paths
and quotes and a parent-validation test that forbids document/heuristic parsing.
Review also identified launch failure and a timeout/child-exit race in process
cleanup. Guarded launch and unconditional process-group cleanup now return typed
outcomes and reap the child. The final suite passed 25 checks; focused independent
re-review found no remaining concrete blocker. All documents are synthetic; no
provider or external service was used.

The processor is not yet connected to a durable intake extraction job or the
extraction-history API. That integration, real scanner qualification and the
remaining B2 acceptance journeys remain outstanding.
