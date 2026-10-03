# Balanced allocation proposals

## Next implementation boundary

Add immutable, versioned cost breakdown proposals for one logical document and one supported whole-property accounting scope. Each revision pins one current context decision and therefore the exact assignment, invoice field review, source partition and extraction/hash. This increment creates no `cost_entry`, source release, publication audience or financial approval.

This is a foundation for allocation-set adoption, not completion of B3. A single-scope proposal cannot be presented as multi-object allocation. Cross-scope sets need a separately reviewed context for every destination; the current assignment chain selects one scope and must not be repurposed to invent such authority.

## Conservation and provenance

A proposal contains 1–100 ordered lines. Each line records a required description, explicit active cost category/version, signed gross decimal and optional VAT decimal. The total gross equals the reviewed document gross exactly in cents. Zero-gross lines are rejected. Every line has the document's sign; mixed debit/credit allocation is unsupported. Known header VAT requires known line VAT, matching sign/bounds and exact conservation. Unknown header VAT requires unknown line VAT; it must never silently become zero. No automatic rounding or float transport is allowed. Line amounts use unscaled numeric with explicit finite, magnitude and exact-cent checks, so direct SQL cannot round before validation.

The command verifies line shape, finite exact-cent strings, category/version and conservation. Deferred database constraint triggers independently verify a complete line set and totals, including direct privileged inserts; immutable parent and child rows preserve the accepted proposal. Composite foreign keys bind the context decision and field review to the same logical source and destination. The immutable expected line count and unique contiguous positions seal later membership too. Category ID and version use a composite foreign key; code/label are retained under a bounded category row lock, without adopting any category tax/distribution defaults. No legacy backfill is invented because this ledger records new explicit proposals only.

## Current basis and access

Reads inherit the exact context decision's original, scope and property-master audience. Commands require actual interactive runtime, live original/scope prepare, unpaused state, immutable request identity and an expected predecessor. Authorized historical replay returns its original receipt before freshness checks. A new proposal requires the latest context decision, current assignment/partition/extraction/field review, and unchanged property/scope snapshot under the established advisory and bounded row locks. An invisible successor denies new writes without revealing its identity. Advisory exception workflow does not revoke an otherwise unchanged exact context decision.

## Financial adoption still requires

- Hash/business duplicate and credit/replacement decisions with evidence-scoped audiences and race-safe checks.
- Explicit supported cost eligibility, distribution and tax treatment; missing or unsupported treatment blocks adoption.
- Atomic allocation-set approval, one source release, unique per-line cost links and protection against later extra full-amount costs through all entry points.
- Source integrity verification, original publication/disclosure across all destinations, and preservation of existing released evidence and legacy primary-link guarantees.
- Separate immutable correction sets after adoption; no draft edit or partial split confirmation may bypass those rules.

## Qualification

Exercise positive invoice and negative credit conservation, off-by-cent totals, excess precision, unknown-versus-zero VAT, invalid category versions, empty/oversized lines, stale source/context/property snapshots, concurrent writers, immutable historical replay, current rights and pause, actual-role restrictions, rollback, deferred direct-SQL imbalance rejection, coverage invalidation and guarded downgrade. Existing context qualification remains valid where code and contracts are unchanged.

## Implemented backend and qualification (0105)

The immutable proposal/line ledger, authorized monetary preview, history/detail and command APIs are implemented. Category code/label are retained explicitly, and new commands require an active category. Historical replay retains its original category evidence even after deactivation. The internal current-context helper is not callable by runtime roles; the public write command enforces the interactive role and current prepare authority.

- `hv-allocation-qualification`: 31 PostgreSQL/API tests passed across new allocation, context, field-review and exception behavior. This includes direct SQL parent-only commit and late-line rejection, exact-cent storage, provenance, actual roles, two concurrent writers, one immutable retry effect, property-only revocation, pause, stale context/property/field review, category deactivation, atomic second-line failure, manifest invalidation and guarded downgrade.
- `hv-allocation-migrations`: eight older populated/empty migration qualifications passed after adapting their rollback setup to 0105.
- `hv-allocation-deploy`: fake-Docker head/cleanup regression passed at 0105; no deployment occurred.
- Independent static design and implementation review found no remaining production blocker. Its membership-sealing, category/version, zero-line, numeric-rounding and deferred-commit concerns are reflected in the implementation and tests.

No cost, approval, duplicate disposition, source release or source audience is created by this increment. UI and downstream financial adoption remain separate work.

## Allocation proposal UI qualification

The source page now provides explicit single-scope cost-category lines, descriptions, signed amounts, separate gross/VAT sum checks, reason and acknowledgement. Integer cents (`BigInt`) avoid binary floating-point arithmetic. Unknown header VAT keeps every line unknown; category defaults never silently determine eligibility or tax treatment. New contexts/stale versions require explicit draft replacement. Unknown POST results remain frozen and require authorization for their exact retained context before display/replay.

- `hv-allocation-preview`: eight backend tests passed after adding the authorized monetary preview and its property-revocation assertions.
- `hv-allocation-ui-tests`: 25 component/source-page tests passed, including exact decimal boundaries, negative gross/VAT splits, unknown VAT, unknown-response replay, stale input retention and denied-predecessor/readable-successor isolation.
- `hv-allocation-ui-build` and `hv-allocation-ui-lint` passed; the existing large main-chunk warning remains (approximately 756 KB).
- Independent static UI review found no actionable defect.
- `hv-allocation-browser`: actual local API/extraction (synthetic clean inspection), 59.00 + 60.00 = 119.00 split, unknown VAT, lost committed response with exact replay and one retained proposal, reload acknowledgement reset, keyboard save, mobile width, no new costs, and simulated context denial clearing passed. Desktop/mobile screenshots were inspected.

Production scanner, cross-scope allocation, tax/duplicate decisions and financial adoption are still outside this acceptance.
