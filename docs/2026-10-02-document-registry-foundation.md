# Shared original registry foundation

Status: locally verified first B2 foundation after accepted B1 (`2ffe69b`), through migration 0092. B2 remains incomplete.

The first B2 increment adds canonical typed references to existing invoice,
property and mandate originals. It does not copy files, change released columns,
assign sources to new scopes or grant access. Each registry row has exactly one
same-organization adapter FK, with one row per original, immutable hash/MIME/size
and registration timestamp. Existing upload paths create their registry row in
the source transaction through database adapters; migration backfills existing
originals under bounded source-table locks.

Registry disclosure delegates to the original table's actual row policies in an
invoker helper after checking the current organization and live principal under
the shared ACL lock. It introduces no independent grants or combined-audience
approximation. Runtime access is read-only; paginated metadata and detail routes
hold the same live-principal boundary. Responses contain typed identities and
content metadata, never storage locations, source text or preview bytes. Original
export continues through the already authorized source-specific routes.

Source identity and byte metadata cannot drift away from the immutable reference.
The existing retained-source semantics must remain intact; migration and regression
checks cover update/delete behavior as well as original bytes and released data.
A populated registry is retained on downgrade and repaired forward. No production
migration is performed in this task.

Verification (2026-10-02):

- The initial API regression failed with the expected missing route, then passed.
- The expanded registry/RLS/mandate/invoice run passed 27 checks; one new test
  failed because its fixture referenced a nonexistent foreign-organization key.
  The fixture now uses an unrelated UUID, which the exact-source guard rejects.
- The corrected migration test, extended invoice link/revoke test and declaration
  upload API suite passed all 56 checks (`/tmp/hv-document-registry-followup.log`).
  Existing passing checks remain valid; only affected tests were repeated.
- Evidence covers all three automatic adapters, distinct equal-content originals,
  exact metadata backfill without source mutation, duplicate/foreign adapter
  rejection, immutable identity/content, runtime write and private-helper privilege
  denial, selected-scope and revoked access, archival, atomic failed insertion,
  signed cursor boundaries, and populated downgrade refusal/empty round trip.
- Deployment script regression passed with expected head 0092. This is a local
  fake-Docker probe, not a deployment.
- Independent read-only implementation review found no concrete blocker. No UI
  changed in this increment; the API does not expose storage URIs or bytes.

Registration time records registry creation, not historic upload or custody time.
Legacy hash/MIME/size values are preserved verbatim. A failed database registration
rolls back source metadata; this does not claim transactional object-store cleanup.

Later B2 increments add logical container/page references only with verified page
bounds, upload quarantine/scanning, at-most-100 intake manifests with separate
bounded file requests, jobs/leases/outbox, extraction revisions/evidence, reconnect
status and the inbox UI. This initial register alone does not satisfy B2 acceptance.
