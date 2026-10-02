# B1 shared property-data preparation

Base: `95bf32f`. Migration 0045, explicit setup API and administrative UI. This
increment prepares one shared-data domain per property and explicit team grants;
it does not activate source/master-data restrictions or complete B1.

The initial domain-creation test failed because the new route was absent. Backend
checks use disposable PostgreSQL 16 and Python 3.13. They cover no implicit admin
grant, no cross-inheritance with financial grants, same-key replay/concurrency,
revocation without resurrection, replacement, correct domain/grant pairing,
read-only/deactivated/expired roles, invalid capabilities, tenant fences,
immutable database evidence, canonical audit actor, populated downgrade refusal
and empty downgrade/upgrade with rollback restoration. Read-snapshot regressions
cover demotion/deactivation on both detail and paged metadata.

The RLS inventory was expanded for the two new tenant tables. Existing accounting
scope API cases passed and remain valid after changes restricted to the new read
routes/tests. UI checks cover lost create/grant replies and durable revocation.
Frontend lint and production build passed; the existing bundle-size warning now
reports approximately 603 KB. Final **17 domain tests**, **7 RLS inventory tests**,
and **3 UI tests** passed. Two test assumptions were repaired: host/database clock
skew required waiting against the stored database expiry, and a demoted admin
retained a legitimate explicit grant from another case. The tested inaccessible
domain remains absent; authorized domains must not disappear.

Independent review accepted the increment after page/detail routes adopted the
live-member transaction guard. Local Chromium acceptance passed Users → domain
setup → committed response lost → identical retry → explicit grant → durable
revocation. The page states preparation and separate financial rights, has no page
errors/framework overlay, and fits a 390px viewport (visible/content width 390px).
The mobile screenshot was visually inspected. Browser plugin was unavailable;
regular Playwright was used. Reproducible script:
`scripts/acceptance/b1_property_data_domains_browser.cjs`; sanitized result:
`docs/acceptance/b1-property-data-browser.json`. API/Vite processes were stopped
after verification. No live providers or external systems are used.

## Worker scheduling prerequisite (0046)

On base `b869a2e`, two red cases added a temporary test-only source RLS policy.
They reproduced an actorless claim silently missing eligible work and a hidden
revoked lease returning no result instead of committing cancellation. The worker
now obtains only a single tenant-local queue candidate's four fence fields from a
narrow definer helper, resolves the immutable queue actor, then reads the import
through ordinary RLS. Lease checks likewise verify metadata before actor/source
access, including a database-clock check after row-lock waits. No original bytes,
questions, candidates, approval results or error messages cross the metadata path.

**37 principal/worker cases passed** after implementation. **79 metadata/pipeline
cases passed** after adding bounded/foreign-tenant metadata, observed row-lock
expiry, migration round-trip and readiness coverage. Existing due-time selection,
concurrent claims, lease recovery, stale fences, conservative budgets and provider
publication checks remain covered. Independent read-only review accepted the
increment. There are no frontend changes or browser reruns for this prerequisite.

This does not enable source RLS. Future domain policy activation must add the exact
property-data permission to `declaration_queue_actor` and cancellation semantics;
an otherwise live writer must not bypass a source policy. The metadata helper
retains the existing trusted worker-clock due-selection input; durable effects
remain fenced using the database clock. Startup now requires both metadata helper
functions as well as lease/principal columns.

## Immutable source origins (0047)

Before enabling property-data authorization, migration 0047 freezes row identity,
organization and creation time for Property, Unit, property documents, imports,
import/document links, candidates, evidence, issues and extraction runs. It also
freezes their existing creator/parent relationships. Import mode and target cannot
be rewritten; unknown historical uploaders remain unknown. Document publication
retains its existing one-way unassigned-to-property binding. Applied imports keep
their destination, approver, approval time, status and idempotent result; an
existing-property import must publish to its declared target.

This adds no read-policy activation, creator backfill or inferred grant. Parent
immutability does not prove that all historical/new links are mutually coherent;
complete graph classification, insertion authorization, destination publication,
worker and audit/export closure remain required. Existing immutable derived-fact
and rule guards and column-limited relation grants are retained.

Evidence at base `7b80693` plus this increment: two initial uploader cases failed
(the known-uploader case reached its existing FK; the valid unknown-to-known
assignment was allowed). **39 source-context and job-principal cases passed** on
the disposable PostgreSQL 16 database through `app_rw`. Coverage includes direct
reparenting attempts, ordinary metadata updates, unknown historical creators,
one-time document binding, frozen applied receipts and wrong-target publication.
A migrator-only nested transaction proves populated downgrade refusal and empty
schema downgrade/re-upgrade without removing committed source evidence. The test
harness clears only its guarded disposable data before a full schema reset.

The broader pipeline/review/new-property approval/existing-property approval/import
API run passed **221 cases**. Its one historical migration rehearsal initially
failed on the deliberately populated downgrade guard. The fixture now clears its
disposable source rows before its destructive setup and round-trips only 0024/0025
before restoring head. That case passed after repair; **25 source-context cases**
also passed on the final production code. The unchanged 221 passing cases and 14
principal cases were reused. Independent read-only review accepted the increment.
No UI or provider behavior changed; no browser or live-provider run is claimed.

## Tenant and import source links (0048)

Unit now uses the tenant/property composite FK. Review issues may refer only to
candidates of their own import. Evidence carries an import ID mechanically copied
from its immutable candidate parent and references both that candidate and an
actual document link in the same import. Its import context cannot subsequently
change, and referenced source links cannot be deleted. General issues with no
candidate remain supported. The old weaker FKs are replaced, keeping implicit ORM
joins unambiguous. The invoker-rights insert bridge supports old callers without
adding source visibility. Worker readiness requires the expanded evidence column.

The migration checks legacy edges before expansion, reports only mismatch counts
and never invents missing links. It takes final DDL-strength locks with NOWAIT,
so a busy source graph fails immediately and releases partial locks. A populated
downgrade cannot remove the stronger boundary. Source RLS, destination grants,
multi-import sharing policy and audited activation remain unfinished.

Evidence at base `5deb8c0` plus this increment, PostgreSQL 16/Python 3.13/app_rw:
three initial cross-tenant/import cases failed because the invalid inserts were
accepted. After implementation, **105 source-link/pipeline/review cases passed**.
A boundary run passed **187 cases** covering worker, master data, tenant isolation,
approval and import API paths. Its stale FK-name assertion was updated to check
the new composite keys; one fixture teardown encountered a transient PostgreSQL
SSL-upgrade connection rejection. After database reachability was confirmed,
**13 final focused cases passed**, including both affected cases, exact historical
backfill, three counted legacy-mismatch refusals, populated rollback refusal,
worker readiness and two-connection reader/writer lock contention. The unchanged
passing cases were reused. The initial duplicate-FK join ambiguity and title-based
fixture lookup were repaired before the 105-case pass. Independent read-only
review accepted the final lock repair. No UI or live-provider change/run.

## Prepared common source-audience resolver (0049)

The typed `property_source_access` function returns a boolean for the authenticated
transaction actor. It is not yet attached to source policies. Private explicit-
actor helpers are unavailable to `app_rw` and PUBLIC; future queue integration may
use them only from a trusted definer with the immutable receipt actor. The existing
domain helper delegates to the same live principal/grant logic without changing
its public signature or OID.

Resolution covers property/unit, original/import/review/run/pass/queue rows,
approved facts/references/assignments/rules/relations and Q&A accounting. Creator
ownership permits unassigned intake and preparation, never publication. Applied
or existing-target imports require explicit domain capabilities. Import evaluation
uses attached originals' local metadata, so document delegation cannot recurse
through an import/document cycle. Mixed destinations, multiple import links and
unknown/mismatching unbound creators fail closed. Derived provenance checks its
source import's destination as well as original access. Invalid polymorphic fact
subjects also fail closed. No administrator or financial grant implies access.

Initial missing-resolver coverage failed before implementation. **18 initial
resolver/domain cases passed**, then **11 expanded resolver cases passed** on
PostgreSQL 16/Python 3.13 through `app_rw`. These cover creator and target audiences,
live role/deactivation, revoked grants across source/derived/ledger types,
unsupported topology, foreign/unknown resources, private-helper privileges and a
populated migration round-trip preserving explicit domain grants. A test query
needed an explicit OID cast to select PostgreSQL's privilege-check overload; no
production repair resulted from that fixture failure. The valid existing domain
API evidence is reused. No source-policy activation, worker/Q&A domain adaptation,
publication bootstrap, indirect-export closure or complete B1 claim is made.

Independent review found that a known uploader alone did not prove admission to
an existing target audience. The resolver now conservatively requires that
uploader's live target `prepare` capability while the original is unbound. The
new regression proves denial without that grant, admission after an explicit
grant, denial on revocation and stable destination access after reviewed binding.
**28 final resolver/domain cases passed** after the repair; independent review
accepted it. Durable admission/remediation remains an activation prerequisite,
so this temporary conservative rule is not represented as a finished team workflow.
