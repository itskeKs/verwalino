# Derived source authority

Migration 0062 extends the dormant source-security boundary to persisted review
children, canonical facts and stored Q&A. It does not enable source enforcement or
complete B1. PREPARATION retains existing tenant-scoped compatibility; production
activation remains unavailable.

## Database boundary

Restrictive SELECT policies cover seven import children: document links,
candidates, evidence, issues, extraction runs, queue receipts and provider passes.
They resolve the actual immutable import, including the pass → extraction-run
edge. Strict visibility requires current import source authority. Their existing
write privileges now also require prepare; immutable contexts and append-only
receipts retain their earlier guards.

Seven canonical tables require their current property audience and the complete
expected source READ graph: approved fact sources, legal references, assignments,
rules, rule sources, rule relations and document relations. A hidden, unproven or
quarantined expected source prevents a partial graph from appearing complete.
Canonical effects additionally require approve, an APPLIED originating import
with the same destination, and authority over each referenced original or typed
subject. Supported fact subjects include property, unit, rule, legal reference,
assignment and document relation. Existing composite FKs, column privileges and
immutable origin/lifecycle constraints remain in force.

Policies project only the actual row's authorization metadata. They do not resolve
a newly inserted child by its own ID, so INSERT RETURNING works before that row
is visible to another query. Existing-parent lookups use trusted fixed-search-path
functions with row security disabled; public entry points return booleans only.
Fourteen statement triggers acquire the shared organization ACL before row locks.
UPDATE checks both old and proposed contexts. No user-controlled session flag
bypasses these policies.

Stored Q&A SELECT requires the whole current source READ graph. PAUSED permits
these reads but rejects new source effects. Existing trusted budget reservation,
expiry and fixed-cost cancellation commands continue to own Q&A effects.

## Service boundary

Review lists and previews check the parent import before loading children, so
inaccessible children cannot turn into an apparently empty draft. Candidate
review, import mutations and direct uploads check prepare before row mutation or
storage writes. Existing-property approval and every applied replay check current
approve; an unpublished new-property import checks creator prepare before its
explicit publication audience is created. Existing explicit-audience replay
checks also remain active in PREPARATION.

## Rollback and remaining work

The migration locks all fifteen tables and source-security state with NOWAIT.
Rollback refuses ENFORCED or PAUSED organizations. A PREPARATION round trip is
rehearsed inside a disposable transaction and leaves existing records intact.

Ordinary original/import row policies and purpose-bound review access are the next
boundary. Indirect privacy, audit, report and legacy coverage gates also remain;
no activation, production rollout or B1 completion follows from this increment.
