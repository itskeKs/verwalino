# Strict mandate original access

Status: implemented and verified; baseline b462fe7, migration 0077.

The 0042 mandate original adapter permits unclassified compatibility access when
no financial selector is set. That fallback must not survive ENFORCED/PAUSED source
mode. Strict originals require their existing explicit accounting scope and its
current READ plus requested capability. This change does not infer a legacy scope
from a mandate/property/uploader and does not alter originals. Existing scoped
mandate originals already carry immutable, explicit scope provenance; no property
source admission record is fabricated for this separate document family.

Replace the existing helper in place so all RLS and audit dependencies use the
same function OID. Require a live principal, tenant coherence and a recognized
capability; unknown scope is allowed only in PREPARATION compatibility. PAUSED
allows authorized reads/exports and rejects preparation effects. Upload must run
this check before storage; RLS rechecks at metadata insertion. Existing financial
pause and grant checks continue to compose. A strict downgrade must refuse to
widen access, and a preparation roundtrip must preserve original/audit hashes.

Acceptance: strict legacy metadata/content/audit and raw SQL edits are denied;
unscoped uploads never touch storage; scoped uploads work in ENFORCED; PAUSED
keeps authorized exports and denies uploads/archive; revoked/expired/inactive
readers cannot recover access. Existing preparation behavior remains compatible.
Activation remains false. Reviewed legacy remediation is separate subsequent work.


Verification: strict legacy disclosure and unscoped storage access initially failed
as expected. A separate pause test demonstrated archive returning 200 while paused.
After correction, 15 focused/RLS checks passed; the expanded run passed 24 cases
with one invalid synthetic expiry fixture. Final 47 enforcement, mandate-document,
RBAC and tenant-isolation cases passed after correcting the fixture timestamps.
The initial pause fixture also needed a valid replacement grant (READ required,
one active grant per user/scope); no production behavior was changed to fit tests.
Raw SQL insertion, hidden metadata/audit, retained originals, revocation, deactivation,
expiry, same-OID roundtrip and strict downgrade refusal are covered. Preparation
compatibility and existing scoped expiry-during-storage checks passed. Independent
review found no issue. Shell syntax, staged diff checks and fake-Docker deployment
checks passed; no external deployment or UI change was performed.

Rollback: a preparation-only downgrade restores the prior helper without touching
original/audit rows. Any ENFORCED or PAUSED organization blocks downgrade. Keep
source pause and repair forward once strict mode is eventually approved. The
activation predicate remains literal false and B1 is incomplete.
