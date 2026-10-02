# Logical document foundation

Migration 0095 adds immutable, unreviewed logical sources over registered intake
originals. It is a B2 foundation, not acceptance of B2 or a split-invoice workflow.
It has no accounting effect and grants no source access.

## Contract

`POST /api/logical-documents` takes a registered original ID, inclusive page
bounds, a reason and an idempotency key. The database derives the original hash
and successful inspection attempt. PDF/image ranges must fit the exact verified
page count. XML retains the complete structured original with null page bounds.
Existing invoice/property/mandate adapters without this verified inspection
provenance cannot create children through this command yet.

Each logical source references its original registry row and exact inspection
attempt through organization-bound foreign keys. No bytes are copied, and no
storage URL is returned. Rows remain `unreviewed`; overlapping candidate ranges
are possible and do not constitute an accepted partition or complete invoice.
Exact duplicate ranges are rejected. Source-level creation is capped at 200
children. Source-local listing returns at most those 200 rows.

Creation rechecks uploader identity, original cleanliness, live read and prepare
rights, selected scope and pause state while holding the existing intake locks.
The request key binds the exact original, range and reason. Replays return the
same child; changed payloads conflict. Concurrent identical commands commit one
child and one immutable outbox event. The event carries an internal effect key;
delivery remains future B2 work.

Read policies follow the original registry's actual RLS. A candidate range never
narrows disclosure or creates independent authority to the container. Revocation
hides both original and child; pause stops new writes while retaining permitted
reads. Existing administrative coverage includes child evidence in its dependency
digest, invalidating prior reports without expanding the original audience.

## Verification

Local PostgreSQL 16 tests use synthetic documents and explicit test inspectors.
They do not qualify actual malware scanning. The first contract tests failed
with the expected missing route before implementation. After implementation, a
test-only UUID/text bind mismatch was corrected.

The focused logical/intake/registry suite passed 26 tests against 0095, covering
private access, page bounds, XML, pause/revocation, idempotent races, immutable
evidence, raw-write denial, forged hash/page count rejection and migration
roundtrips. The fake-Docker deployment probe passed at required head 0095; no
deployment occurred. Existing UI checks from the inbox commit remain valid
because no frontend files change here.

Populated downgrade refuses to discard logical sources or their events. Empty
downgrade restores the preceding coverage function body while preserving its OID.
The separate coverage-invalidation probe passed: a new child changes the digest,
rejects review of the earlier report and leaves the original audience unchanged.
Independent read-only review found a missing entry in the exhaustive RLS test
registry. The table is now registered and has the established deny-insert policy.
The final logical/RLS suite passed 13 tests after that repair. The earlier 26-test
suite and deployment probe remain valid for their unaffected behavior.

Next work is versioned extraction and field evidence, processor adapters,
outbox delivery, and B2 qualification. This API does not provide reviewed scoped
extracts, attachment decoding, accepted document partitions or human field
overrides; those must be explicit later contracts.
