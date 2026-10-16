# Mandate legacy quarantine cutoff

Status: implemented and verified; baseline d5f9d1c, migration 0078.

The independent architecture review found that historical NULL-scope mandate
metadata, including uploader and storage locator, was mutable. It cannot establish
current private custody. Do not copy the property-original known-uploader admission
rule: that document family had earlier immutable content protections.

This increment atomically observes and freezes all existing unclassified mandate
originals. Record an immutable, private snapshot digest and observation timestamp;
retain the original metadata and storage reference. This is today's observation,
not authentication of historic bytes or ownership. Deny content/metadata/audit
access through ordinary routes in every mode and refuse further unclassified
uploads before storage and at the database boundary. Existing explicitly scoped
uploads remain the supported path: a current authorized person supplies an
independently held original, selects a scope and records a reason. No old identity
is reparented or treated as proof. The UI must require that explicit scope.

Acceptance: migration captures legacy rows without changing their hashes; raw
update/delete cannot change frozen originals; runtime INSERT of another unclassified
original fails; historical uploader spoofing grants no review/content path;
scoped upload/download/archive and current rights remain valid; populated downgrade
refuses loss of the observation; UI prevents unscoped submission and explains why.

Subsequent work adds paginated administrative disposition review of the frozen
identifiers/digests. No privileged byte-recovery endpoint is introduced. Lost
external originals remain unusable. Retained storage references do not prove bytes
still exist, and no cleanup of referenced storage is authorized. Activation stays
false; B1 and the overall plan remain incomplete.


Evidence: backend pre-storage and frontend missing-scope tests failed before the
change. The first compatibility run passed31 and exposed two fixture adaptations:
privileged retention count is one while runtime visibility is zero; historical
0042 rollback rehearsal now truncates its dependent observation inside its rolled
back disposable transaction. The expanded run passed31 cases with one transient
PostgreSQL SSL connection setup error. The failed case and the updated general
mandate journeys were rerun:12passed. Three frontend tests, final lint/build,
shell syntax and simulated deployment passed (existing632KB bundle warning).

The actual 0077→0078 transition is rehearsed with a synthetic pre-cutoff row:
its JSON is unchanged, observation hash matches, private metadata cannot be read
by runtime administrators or clerks, owner-level mutation fails and populated
rollback refuses evidence loss. Existing strict source and scoped archive/download
checks remain green. Legacy fixtures intentionally emulate pre-cutoff rows with
transactional DDL only in the guarded disposable database; ordinary runtime
insertion remains denied.

Independent review found one contradictory old compatibility sentence in the
page. It is corrected and covered by an assertion. Chromium verified required
scope/reason, new independently supplied original upload and byte-exact download,
desktop/mobile layout and no page errors. A rerun initially hit duplicated test
titles; unique synthetic titles resolved the harness ambiguity. Final screenshots
were inspected and both test servers stopped. No historical blob was read or
verified by the migration. No cleanup or external deployment occurred.
