# Invoice intake custody and upload admission

Status: accepted local increment 0082 on baseline 20f9b65. Source activation stays
disabled; B1 and M-B–M-G remain incomplete.

Record the current uploader for new invoice originals without assigning historical
NULL creators. Freeze identity, content/storage metadata and lineage independently
of release. Strict standalone originals belong to their known live uploader;
linked originals retain their exact financial audience. Pause retains authorized
read/export but blocks upload, replacement, linking and processing effects.

An actor stamp alone cannot establish upload custody: raw runtime SQL could alias
a known legacy locator. After actually accepting bytes into storage, trusted Python
records a narrow immutable upload admission using the existing isolated app_review
connection. Ordinary app_rw/app_worker cannot mint admissions or read their private
table. The admission binds document ID, organization, current actor and exact initial
storage/content/lineage/cost metadata. Runtime INSERT requires that exact admission
and rechecks live permission. The trusted API process/review credential remains a
trust boundary, as in semantic declaration review; the DB does not prove Python
executed or withstand compromise of that credential. No new credential is copied
or provisioned. An aborted outer transaction may retain an unused admission/blob;
it grants no alternate metadata, actor or original ID authority.

New roots identify themselves at version 1. Successors must extend exactly one
same-tenant predecessor/root/version, require its current preparation authority,
and retain its cost or validated correction context. Serialize lineage checks.
An unknown standalone historical original cannot gain a successor or first cost
link in any mode. Known standalone linking requires its uploader and current
financial preparation authority; existing links cannot be rewritten or removed.
Use independent new intake for unknown historical standalone files. No historical
provenance or byte authenticity is inferred.

Storage entry checks precede put/read/verification. Worker attempts inherit the
same parent decision and retain private cancellation after revocation. Verify
streaming authorization lifetime rather than assuming generator construction reads
bytes. Preserve upload→extract→reviewed cost linking and linked legacy corrections.

The admission connection reads current role/mode without waiting on the ACL lock
held by its caller. Otherwise a revocation queued between those connections could
create an application-level wait cycle. The final insert holds ACL then financial
input locks and rechecks current authority. Its SELECT policy evaluates the actual
new row for INSERT RETURNING; resource lookup alone cannot see that row yet.
Retry, versioning, worker claim/finish and trusted cancellation share this lock order.

Verification on disposable PostgreSQL 16 and Python 3.13:

- Initial custody/pause tests reproduced both missing boundaries. Subsequent
  strict upload-to-cost coverage reproduced the INSERT RETURNING lookup failure.
- 118 invoice, financial boundary/creation, extraction worker, strict finance and
  RLS cases passed after production repairs. Existing linked corrections and
  duplicate active-source constraints remain covered.
- The final 12 custody cases passed after additional test-only coverage, including
  strict direct linked upload, denied upload before storage, private role fencing,
  exact admission payload rejection, unknown history retention, concurrent version
  chaining, pause before storage, real extraction and idempotent first linking.
- Two-connection regressions observe actual PostgreSQL waits for retry ordering,
  streaming revocation through the final lazy chunk, and admission behind a queued
  revocation. Populated downgrade refuses evidence loss; empty round-trip preserves
  function identity and leaves activation disabled.
- Fake-Docker deployment/worker cleanup probe, shell syntax and diff checks passed.
  Independent read-only review accepted the production changes subject to the
  direct linked-upload regression, which subsequently passed. No frontend change
  or browser rerun is claimed.

Historical invoice bytes are not authenticated retroactively. The isolated review
credential is now required for ordinary invoice intake as well as declaration
review. Its absence fails the upload without a committed invoice; storage cleanup
of an unused blob/admission remains a separate retention-aware maintenance concern.
