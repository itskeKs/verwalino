# Reviewed admission of previously unadmitted originals

Status: implemented and independently reviewed in migration 0052; backend, UI
and browser evidence recorded in acceptance/b1-property-data-preparation.md.
No source-policy activation.
Baseline: `9b83f49`.

## Purpose and authority

Migration 0050 proves authorization at actual attachment. It must never be
backfilled using a later grant. A separate immutable review receipt records a
present-day decision to admit an older unbound original into its existing target
property audience. This is contemporary source disclosure, not reconstructed
historical authorization.

The reviewer must be the original's immutable known uploader, an active writer,
and hold current explicit read, prepare and approve rights in the exact target
property domain. Administrative roles do not substitute for source ownership or
those capabilities. Another administrator cannot disclose this source. Unknown
uploaders, mixed targets, multiple import links, archived originals and bound
originals remain classification exceptions. Supporting unknown historical custody
requires a separate evidenced recovery design; do not infer it here.

The exact original must have one link to an EXISTING_PROPERTY import, with its
immutable target matching the domain. The import must be quiescent (DRAFT,
NEEDS_REVIEW, FAILED or CANCELLED, with no live lease). The request supplies the
reviewed source hash, observed import revision and a nonblank reason. Recheck all
context under the shared ACL lock and import row lock before recording anything.
A stale revision/hash, active work, changed principal or grant loss fails without
a receipt. Receipt and canonical audit commit together.

## Proof and access

Use a separate `property_source_admission_review` table with tenant, exact link,
target/domain, current reviewer/grant, observed revision/hash, reason, request key,
request digest and database creation time. Composite FKs retain the exact scope
and grant chain. One review per original; retain the current attachment proof
unchanged if it already exists. App writes go through a checked insertion guard;
updates/deletes are denied. Grant revocation never erases an accepted review.
Populated downgrade refuses to erase this evidence.

Require database uniqueness on `(organization_id, document_id)` and
`(organization_id, reviewed_by, request_key)`, plus indexes for link/domain/grant
lookups. The guard compares the recorded hash to the immutable original hash,
observed revision to the current import revision, exact reviewer/link/target/domain/
grant context, and expiry against the database clock. The receipt cannot be a
generic current-grant record detached from a checked source decision.

The common import resolver accepts either actual-attachment proof or this distinct
review proof for an unbound existing-target original. It still verifies all source
links and the viewer's live target grant. Admitting one original never grants
access to another unadmitted source or makes a mixed import visible.

A narrowly typed pending-review boolean may expose an uploader's own original
for review when the exact target rights above exist. It does not expose the
import's extracted payload, other originals or another uploader's metadata.
Future document RLS must include this explicit own-source review path; import
source policies remain unchanged until all originals qualify. Original bytes
must be read through normal authorized ORM/storage paths, never a bulk definer
payload reader. Read-only members cannot use this administrative-effect flow.

Separate own-source read eligibility from quiescent insertion eligibility. The
read predicate must not depend on the absence of a review, nor on quiescence:
otherwise a committed review would become unreadable during a replay or while
another original still blocks whole-import access. Pending listing separately
filters absent proof and quiescent imports. Receipt reads allow the normal source
audience or this narrowly authorized own-source review path.

The review command does not bind documents. Reject a mismatching eventual binding
and a second import link for a reviewed original immediately. Prepare a private,
definer-only binding-proof boolean checking exact document/import/target and an
attachment/review receipt (or the separate guarded NEW publication receipt). It
must not become a public source-access shortcut. Universal proof-required binding
remains a mandatory activation gate: install its rejecting trigger when source
enforcement is integrated, and classify every already-bound legacy original.
The current compatibility first-bind path remains a known pre-activation exception;
this review increment does not certify or activate it. Unknown or mismatched bound
provenance must block activation instead of bypassing admission review.

## Commands and presentation

Provide bounded cursor listing of the actor's eligible own originals, using safe
source/import/target IDs and normal permitted source metadata. A dedicated source
review page shows target, original hash, original preview/download and the reason
field. Explain that this action admits the original to the property's prepared
audience; until activation, it does not narrow existing compatibility access.

POST requires an idempotency key. Retry rechecks current source and destination
access before returning the existing immutable review. Changed payload under a
reused key conflicts. A fresh key cannot create a second review for the original.
After a concurrent uniqueness conflict, recheck present own-source and destination
authority before loading a receipt; identical key/digest returns it, a fresh key
conflicts. Replay does not require the import to remain quiescent after commit.
Preserve the entered reason and exact submitted command across lost responses.
No source facts, original creator, document binding or financial grant changes.

## Acceptance and remaining gates

Exercise a real attachment without a grant, later exact grants, explicit reviewed
admission and source resolution for another target reader. Prove no access before
review, no inferred 0050 attachment receipt, separate audit/proof types, current
viewer denial after revocation, uploader grant revocation preserving admission,
raw SQL creator/target/grant denial, foreign tenancy, stale context, active worker
refusal, mixed-source denial, atomic rollback and concurrent/retried commands.
Rehearse populated rollback refusal and empty-schema round-trip. Browser evidence
must include original review, reason retention and lost-response replay.

This bounded flow does not resolve unknown custody or deliberate cross-property
sharing. Coverage/activation must report those exceptions without widening access.
Full source RLS, worker/Q&A authorization and durable cancellation, financial
completeness, audit/privacy/indirect exports and reviewed organization activation
remain mandatory before claiming B1 exit.
