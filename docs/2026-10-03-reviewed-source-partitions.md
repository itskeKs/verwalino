# Reviewed source partition foundation

Migration 0100 adds an immutable, explicitly reviewed selection of logical sources
for one clean intake original. This is a B3 backend foundation, not assignment,
duplicate resolution, financial allocation or B3 acceptance.

A partition revision binds the original reference, immutable hash, exact clean
inspection attempt, verified page count, reviewer, reason and predecessor. Members
have composite foreign keys to both the revision and logical source in the same
organization and original. Each selected visual page must occur exactly once:
missing pages, overlapping candidates, repeated IDs and foreign-source members
are rejected. Standalone XML has one whole-source member and no invented pages.
The 200-member bound follows the existing source candidate bound.

`GET /api/document-refs/{id}/partition` returns the current revision and live
review capability. `POST` requires a reason, selected logical IDs, explicit
`visual_pages_reviewed: true`, `Idempotency-Key`, and `If-Match` containing the
quoted current revision UUID (or `"none"` for the first review). Missing
preconditions return 428; a changed predecessor or changed replay returns 409.
Member order is canonicalized. A replay returns its original revision even after
a later review; it never makes the old revision current again. Historical reviews
remain readable by exact ID at `/partition/{revision_id}`.

The shared intake lock serializes review with source and authority transitions.
Every call, including replay and historical reads, revalidates the current clean
original's recorded-uploader/scope audience. Preparation capability and active
write mode are required for review. Other administrators do not gain the private
original merely because they know a revision ID. Only the actual `app_rw` session may publish a human review; background roles
cannot do so by restoring a reviewer context. Runtime roles cannot mutate
revision/member tables directly or execute the private serialization helper.
Reviews and members invalidate access-coverage manifests; a populated downgrade
refuses to destroy them. Original bytes, candidates, extraction jobs and old
reviews remain unchanged.

The acknowledgement concerns visual-page coverage only. It does not certify that
each range is a complete invoice, approve fields, assign context, release money,
or review embedded PDF XML/attachments. Those attachments remain in the original;
existing extraction does not bind them to page children. Future adoption must
reference an exact partition revision/member and extraction revision. Replacing
a partition must not retarget previously adopted drafts or costs. The original's
`logical_document.review_state` remains an immutable candidate marker; consumers
must use partition membership rather than treating every candidate as accepted.

The source-review UI, contextual proposals, shared exceptions, learned mappings,
duplicate decisions and balanced financial allocation remain subsequent work.

## Verification

The initial three behavior tests failed against the absent endpoint and passed
once implemented. The expanded source/registry/extraction/outbox suite passed 45
checks; four older downgrade rehearsals correctly hit the new retention guard.
Their synthetic partition data is now cleared only inside rollback savepoints.
The focused repair/inbox/source run passed 27 tests. After independent review,
actual background-role denial and RLS policy qualification passed all 14 focused
checks, including seven source-partition tests. The fake-Docker deployment-script
probe passed with the 0100 head gate; no deployment occurred.

Coverage includes invalid/gapped/overlapping/foreign selections, XML singleton,
exact replay after replacement, racing initial and replacement reviews, historical
membership, revocation and pause, transaction rollback, runtime DML denial,
coverage invalidation, and guarded migration roundtrip preserving function OIDs.
