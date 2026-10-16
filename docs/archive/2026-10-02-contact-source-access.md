# Contact source access and privacy commands

Status: accepted contact-source increment; B1 and activation remain incomplete.
Baseline 6f9f59a; migration 0069. Source activation remains disabled.

## Boundary

New contacts retain the actual creator recorded by the database. Existing creator
NULL remains unknown. Explicit contact domains replace creator fallback, including
after revocation. Current READ, PREPARE and EXPORT grants govern Person and its
tenant/owner/vendor profiles. Organization, identity, creator and profile-to-person
links are immutable. Paused source mode preserves reads and rejects effects.
Profile and contact HTTP routes revalidate their principal inside the transaction.
History writes require both destination property rights and source contact READ.
History calculations that use identity references retain their existing property
and financial boundary; contact details require the independent contact audience.

Financial consumers enumerate expected contact identities separately from the
property/legal source graph. Hidden profiles cannot establish complete occupants,
owner names or frozen addressees. Existing stored results and exports retain exact
financial, property-source and contact rights. General property/legal reads do not
inherit these contact requirements. Dunning additionally checks its exact recipient
before reads, changes and export. Audit copies follow the corresponding ordinary
source reader. Pure calculation engines remain unchanged.

## Privacy effects

Application-role SQL cannot update privacy-state columns. The service delegates
to a typed SECURITY DEFINER command; it cannot mint an authorization GUC or receipt.
The command uses fixed search_path and ACL → financial-input → person lock order,
then checks current administrator authority, contact READ/PREPARE, source state,
existing trusted financial/history completeness, active relationships and expected
retention bindings. It recomputes outcome from database facts and rechecks current
rights immediately before mutation. It writes only Person and canonical audit.
Anonymized contacts cannot be repopulated by ordinary contact writes.

Retention dates mirror the existing provisional eight-year year-end fallback and
explicit invoice retention dates. This introduces no new legal retention decision.
Raw direct command tests, API parity, cross-tenant/current-principal cases, expiry,
concurrency and migration rehearsals are required before acceptance. Remaining
indirect views and worker lifecycle closure still block source activation and B1.

## Reference effects and browser behavior

Strict cost writes require a readable, non-anonymized vendor contact. The database
checks again after acquiring the shared financial-input serialization lock;
a writer waiting behind an erasure cannot create a new reference afterwards.
Dunning preparation applies the same active-contact requirement. Source consumption
requires contact READ; destination effects retain their own PREPARE/APPROVE checks.
Historical redacted records remain readable/exportable under their existing rights.

Person responses include current `can_prepare` and `can_export` capabilities from
a bounded database query. Contact/profile controls use these values. A new detail
load removes prior PII and ignores old asynchronous responses. The privacy panel
is unmounted during reload; a denied repeat export clears its previous contents.
An owner selector whose contact expires between queries returns a retryable
conflict instead of an incomplete choice page or an internal error.

## Verification

- Initial direct contact/profile/privacy tests failed on the intended missing
  boundaries. Three further red cases reproduced hidden/anonymized vendor linkage
  and dunning preparation after erasure.
- Final focused integration: **95 passed** across contact source access, M9 privacy,
  RLS coverage, M2 costs and scoped dunning. Includes three multi-connection tests:
  ACL revocation waits for privacy; expiry during a financial wait denies erasure;
  a cost writer waiting behind anonymization is rejected after commit.
- Earlier broad history/financial/M3/M4 integration passed 126 cases; one fixture
  cleanup failure was repaired and verified in subsequent contact runs. Retained
  valid receipts are not treated as another full-suite run.
- Migration round-trip preserves unknown legacy custody, refuses populated custody
  downgrade and checks eight busy dependency locks. The disposable test harness
  tolerates earlier undeployed 0069 shapes during teardown; production data is not
  reset. Source activation remains false.
- Four contact/detail frontend tests and one denied-export regression passed.
  Lint and production build passed; the existing bundle-size warning remains.
- Real Chromium against the local API passed strict creator, READ-only, PREPARE and
  revocation journeys. Desktop/mobile screenshots were inspected; no page errors.
  Test servers stopped and synthetic strict state was removed.

Independent backend review accepted the privacy command and contact boundaries.
The follow-up accepted live capability metadata and distinct retention entity
counts; its stale export retry finding was fixed and regression-tested.

The final closure review accepted reference lock ordering, dunning effects, downgrade
and the export retry fix without actionable findings. A final isolated real expiry
case passed: losing READ between the initial Person load and capability query
returns 409 without the loaded PII. This is one additional passing case after the
95-case run, not a repeated full-suite claim.
