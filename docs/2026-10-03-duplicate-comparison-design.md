# Scoped duplicate comparisons

## Intended next boundary

Add read-only duplicate candidates and immutable human comparisons between two exact invoice-field reviews in the same organization and accounting scope. This is evidence for later adoption, not a global duplicate-clearance flag. It does not create costs, discard intake receipts, release sources or widen original audiences. Existing originals are still visible only under their current custody rules; inaccessible candidates must not contribute names, IDs or counts to a response.

Candidate discovery considers the latest review for each logical document, choosing that latest revision before access filtering. A later inaccessible revision must never resurrect an older visible candidate. Match only within the selected scope and distinguish:

- Equal original content hash and equal logical page interval (or the same standalone-XML extent): repeated source bytes/range.
- Equal reviewed seller name (trimmed, whitespace-normalized and lowercased using PostgreSQL) and document number (edge-trimmed, with case and punctuation preserved), document kind, currency, signed gross amount and service interval: probable business duplicate. These human-reviewed strings are not a verified supplier identity, so this remains a suggestion requiring comparison.
- Equal bytes/range with conflicting reviewed document kinds: field-review conflict; do not discard a credit note as an invoice duplicate.

Distinct page ranges in one multipage original are not automatically hash duplicates. Every returned comparison exposes both exact review IDs and source references under the intersection of their current audiences. Candidate lists are bounded/paginated; no inaccessible totals or cross-organization hash lookup are exposed.

## Human evidence and applicability

A comparison pins both reviews and logical documents, one shared scope, an explicit reason and acknowledgement. A canonical unordered logical pair identifies its immutable predecessor chain, while oriented subject/reference IDs preserve the direction of `same_document` or `replacement_requested`. Concurrent reviewers use an expected prior comparison UUID. Actor/request identity gives historical replay after current authority checks.

New writes require both original prepare capabilities and destination prepare, actual interactive runtime and unpaused state. They recheck both current review/proposal/source bases under the existing advisory lock order. Reads and replay require both complete original/scope audiences. New field reviews or changed candidate evidence require a new comparison; old judgments remain historical facts.

For identical bytes/range, `distinct_documents` cannot authorize a second charge. A deliberate additional contextual reference remains a non-primary supporting relationship. A conflicting invoice/credit type requires corrected field review before a duplicate disposition. `replacement_requested` records intent only; a proper source/cost correction chain must be established before any financial effect. A credit relationship between unlike document kinds is a separate typed operation.

## Downstream obligations

Financial adoption must re-evaluate the relevant candidate set under serialization, including retained adopted evidence once that ledger exists. An absent or empty *visible* candidate list is not proof of global uniqueness. New candidate receipts/reviews arriving after comparison must prevent stale adoption. Decisions must match the exact pair and revisions; one comparison never clears other duplicates.

The shared-exception adapter needs a typed duplicate dependency rather than rewriting immutable assignment blockers or treating a context decision as duplicate clearance. Exception reads must include every referenced source audience. This integration, source publication, tax treatment and adoption remain explicitly pending until their contracts are implemented and qualified.

## Qualification

Test same bytes/range versus disjoint pages; changed bytes with equal reviewed identity; invoice/credit conflict; distinct legitimate invoices; exact-pair stale revisions; reversed pair orientation and concurrent reviewers; zero hidden counts/IDs under source revocation, target revocation and another organization; current-authority historical replay; actual role/pause, immutability, FK provenance, rollback, bounded candidate paging, coverage and guarded downgrade. No test may claim financial duplicate prevention before adoption consumes this evidence atomically.


## Design review refinements

Invoice-review heads are selected before scope, audience and matching filters. Canonical comparison chains are per scope and unordered logical pair; their heads are selected before audience filtering. A comparison in another scope does not supersede this chain. Self-pairs are forbidden. The server derives and retains match reasons, both hashes/extents, a normalization version (`pg16-lower-space-v1`; database lowercasing, not Unicode full case folding) and field disagreements from exact immutable reviews. Identical-source amounts/dates/VAT disagreements remain adoption blockers even when a person records that both references represent the same document.

Candidate cursors bind organization, actor, subject review, scope and normalization version. The future private adoption check must serialize against all matching evidence, including inaccessible and already adopted sources. It must also define readiness for unprocessed receipts; comparing only human-reviewed candidates cannot establish global uniqueness. An inaccessible unresolved collision remains blocked with a generic response. No pair API result grants clearance.

## Backend qualification (2026-10-03)

Migration 0106 and the candidate/comparison API retain evidence only. Eight new tests cover same bytes/range, distinct PDF pages, standalone XML, changed bytes, case-sensitive document numbers, field disagreements, reversed pair races, historical replay, hidden sources/heads, scope revocation, actual roles, pause, rollback, provenance FKs, coverage and guarded downgrade. `/tmp/hv-duplicates-qualification.log`: 32 passed including allocation/context/field-review regressions. `/tmp/hv-duplicates-migrations.log`: nine prior migration guards passed. `/tmp/hv-duplicates-preview.log`: eight passed after adding advisory current prepare capability. The fake deployment probe passes with head 0106; no deployment was performed. Independent read-only review found no code blocker; its per-scope chain documentation correction is incorporated.

## Comparison interface qualification

The source page now offers bounded candidate paging and a side-by-side comparison of both exact field reviews, with links to their pinned source/extraction pages. Verdict, reason and acknowledgement are explicit. Identical originals cannot be marked distinct or replacement; invoice/credit conflicts remain read-only. Historical replacement intent states its stored direction. Field disagreements remain visible and are not corrected by a same-document judgment.

Unknown submissions keep the original oriented body, predecessor and request key. Both retained reviews and pair history are reauthorized separately; denial of a newer candidate context cannot discard an independently authorized retry, while denial of either retained source clears its local draft. Stale drafts require explicit closing and reselection.

`/tmp/hv-duplicates-ui-tests.log`: 25 passed (eight comparison tests plus field-review/source-page integration); build and lint passed with the existing large-bundle warning. Independent review cleared both repaired findings. `/tmp/hv-duplicates-browser.log` passed against real API and extraction with injected clean inspection: same-source candidate, explicit keyboard save, lost committed response/retry producing one comparison, acknowledgement reset, no cost changes, mobile width and local clearing after simulated reference denial. Desktop and mobile pair/form viewport screenshots were inspected. `/tmp/hv-duplicates-mobile.log` confirms the additional mobile capture; all task-started servers/browsers were stopped. This qualifies the comparison interface, not financial duplicate prevention or B3 completion.
