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

## Durable attachment admission (0050)

An actual existing-target draft attachment now captures immutable evidence when
its uploader is a live writer with the target's explicit prepare grant. The
receipt references the exact tenant, import/document link, property/domain and
actor/grant using composite foreign keys. A database audit event commits or rolls
back with the attachment. Application sessions may only read authorized receipts;
updates/deletes and direct insertion, including a valid later-grant migrator
insert, are rejected. The nested attachment trigger is the sole capture path.

The prepared resolver requires this receipt for an unbound existing-target
original. Uploader revocation no longer withdraws properly admitted team sources;
the viewer's own live grant remains mandatory. Legacy uploads without proof stay
unadmitted even if granted later. Wrong destination binding and removal of a
receipt's source link are blocked. Populated downgrade preserves evidence by
refusing; an empty schema round-trip remains supported.

Evidence at base `4581375` plus this increment: initial receipt coverage failed
before the table existed; the first real API-upload integration passed. The
boundary suite passed 32 cases and exposed a missing explicit deny-insert RLS
policy required by the coverage test. After repair, 12 admission/RLS cases passed.
Independent review identified the valid later-grant direct-insert bypass. After
restricting receipt insertion to the nested capture trigger and adding that
regression, **34 admission/resolver/link/RLS cases passed** on disposable
PostgreSQL 16/Python 3.13. No source-policy activation or UI change is claimed.
Explicit historical review, publication bootstrap and complete downstream
authorization remain activation prerequisites.

## Atomic publication ordering

At base `4c7b04c`, publication now creates the NEW destination first, flushes the
applied import receipt/result next, then binds sources and persists derived rows.
Existing-target publication uses the already locked target. Two integration
observers initially failed on the old NEEDS_REVIEW state; they now inspect the
actual database receipt before source mutation and prove another transaction
still sees the original draft (and no new destination). The entire sequence is
owned by the request transaction, including canonical audit publication.

The first approval regression run passed 67 cases and exposed a lost local
destination variable in NEW fact-source display labels. The repository now loads
the exact tenant destination before applying sources. **44 NEW/order cases passed**
after repair; the unchanged existing-target cases from the first run are reused.
Existing failure injection tests prove a late failure rolls back the destination,
receipt, bindings and derived graph. Independent review accepted the repair.
No schema, UI, bootstrap grant or source-policy activation is included.

## Explicit publication audience (0051)

At base `cef3aa2`, NEW_PROPERTY approval accepts an optional explicit audience
command. The administrator must own the intake sources and explicitly name their
read/prepare/approve grant. Destination/domain/grants, applied receipt, original
binding, derived facts and canonical audits commit together. Typed composite FKs
retain the exact publication grant. Database guards enforce current creator and
destination authority, immutable proof and published source links. The resolver
supports the guarded NEW applied-but-unbound transition while checking each
viewer's live grant. Compatibility approval remains without authority proof.

Approval obtains the exclusive ACL lock before source/shared locks. Normalized
payload digests reject changed replay payloads; current destination authorization
precedes replay of evidenced publication. The browser keeps the exact submitted
payload after a lost response. No source-policy activation is claimed.

Backend evidence on disposable PostgreSQL 16/Python 3.13/app_rw: the initial API
case failed on the missing command, then passed after schema/service work and an
array-type correction. A boundary run passed 27 cases and found one missing test
feature flag. A wider run passed **170 cases** and found two old tests expecting
changed-revision replay plus one catalog-session dummy signature. The replay
tests now assert 409 for changed payload and 200 for the identical request. The
dummy accepts catalog query parameters. **18 final focused cases passed**, covering
those repairs, concurrent commands, valid-ID raw SQL denials, late rollback,
revocation, migration round-trip/readiness and both referenced-table NOWAIT
contention cases. Independent review accepted the parent-lock repair. The 170
unchanged passing cases are reused.

Frontend evidence: **12 component cases, lint and production build passed again
after the browser-discovered form repair**. The existing large-bundle advisory
remains. The Browser plugin/skill was absent, so the installed Playwright runtime
and Chromium were used against local API 18000/Vite 13000 and a synthetic reviewed
import. The temporary Vite server first used the wrong working directory, causing
a Tailwind configuration error; restarting from frontend fixed the environment.
The next browser run exposed inline field layout and unstable implicit labeling
of a populated textarea. Explicit label associations, separate full-width fields
and a bounded scrollable dialog repaired this actual UI issue.

The final browser loop passed at 1440×1000 and 390×844: correct page/title, meaningful
content, no framework overlay, required explicit choice/reason, no horizontal
dialog overflow (358px content/358px viewport), and a successful exact retry after
the server committed but its response was deliberately aborted. Database-backed
API reads confirmed exactly one domain and one explicit grant. No page exceptions
occurred; console errors were limited to expected unauthenticated session probes
and the deliberate failed response. Screenshots and the compact report are kept
outside the repository under `/tmp/hv-publication-audience-acceptance/`.
Task-started browser, fixture, API and Vite processes were stopped afterwards.

Historical reviewed admission, source RLS and complete worker/Q&A/financial/audit/
export integration remain unfinished; this is a preparation increment, not B1 exit.

## Contemporary source admission review (0052)

At base `9b83f49`, a separate immutable receipt records an explicit present-day
review of a previously unadmitted original. The known uploader must have current
read/prepare/approve rights for its exact existing target. Single-link topology,
quiescence, lease expiry, revision and immutable hash are checked under ACL,
import and document locks. Database-generated digest/time and canonical audit
commit atomically. No 0050 historical attachment receipt is fabricated. Current
viewers still require live grants. Receipt detail is available through current
receipt RLS; original bytes use authorized ordinary ORM/storage reads.

**86 backend cases passed**, covering this boundary, earlier source/domain
migration rehearsals, publication and RLS coverage. **22 focused cases passed**
after adding inter-query pending freshness and concurrent second-link tests.
Evidence includes direct SQL actor/grant/hash/revision denial, no unauthorized
storage reads, reader/uploader revocation distinctions, partial-import denial,
identical concurrent commands, atomic receipt/audit rollback, immutable receipts,
wrong binding/second-link denial, private proof-helper privileges and NULL denial,
all five NOWAIT dependencies, populated downgrade refusal and empty round-trip.
Python 3.13/PostgreSQL 16/app_rw; disposable schema only. Earlier unchanged passing
cases are reused. Independent review accepted the backend and UI recovery delta.

**Seven frontend cases, lint and production build passed** on the final UI;
the existing bundle-size advisory remains. Confirmed conflicts require explicit
list refresh and new source confirmation, which creates a new request key/body.
Ambiguous responses retain the exact frozen command. The Browser plugin remains
unavailable; installed Playwright/Chromium tested the actual API on local 18000
and Vite on 13000. The first browser script used a relative URL with an API request
context; correcting that test URL enabled the actual flow. Visual inspection led
to clearer field borders and checkbox/button spacing, then the full browser loop
and frontend checks passed again.

Browser evidence at 1440×1000 and 390×844 proves actual original-byte access,
explicit confirmation/reason, no horizontal form overflow (358/358px), server
commit followed by deliberately lost response, exact key/body retry, and a stable
receipt surviving navigation/reload. No page exceptions; console errors only
expected session probes and deliberate aborted response. Captures and compact
report: `/tmp/hv-reviewed-source-acceptance/`. No real provider was called.

This does not classify unknown custody or already-bound legacy sources. Universal
proof-required first binding and complete legacy classification are mandatory
activation gates. Source RLS, workers/Q&A, financial completeness, indirect exports
and atomic reviewed organization activation remain unfinished; B1 is not complete.

## Dormant organization state and worker integration (0053)

At base `9f6ce8f`, internal PREPARATION/ENFORCED/PAUSED state integrates declaration
work with the shared source resolver. No app state writes or activation endpoint
exist; the private readiness gate returns false. Strict state is installed only
by a guarded disposable-test substitution. Shared ACL authorization and exclusive
state changes serialize. Once strict state exists it cannot return to compatibility
or be deleted; pause keeps strict reads and rejects prepare/approve effects.

Queue start and replay now check the same gate-aware prepare permission. Worker
claim and every fenced phase resolve the immutable receipt actor, never the
process's current actor. Existing safe cancellation checks that resolver and
retains cancellation/status/usage evidence when source rows become invisible.

The first state/RLS run passed seven tests and exposed the registry's required
explicit denied-insert policy; adding it retained zero app table privileges.
**34 worker/RLS cases passed.** The expanded suite passed **83 cases** and found a
test-observer issue: PostgreSQL statistics were cached across polling statements.
Clearing that observer snapshot proved the actual advisory-lock wait; the repaired
focused test passed. Independent review highlighted a possible filtered-state
fallback with a misconfigured definer owner. The mode-only definer now sets
`row_security=off`, so this fails with an RLS error instead of widening to
PREPARATION. **17 final focused cases passed**, including real Clerk state lookup,
claim and paused cancellation, an actual temporary non-bypass owner, direct SQL
unadmitted-source denial, paused claim/heartbeat/provider/publication cancellation,
privileged process versus revoked receipt actor, state lock serialization,
immutable rollback refusal/empty round-trip and all four NOWAIT dependencies.
Earlier unchanged passing regression evidence is reused; independent review accepted.

All checks used Python 3.13, disposable PostgreSQL 16 and ordinary app_rw sessions
for enforcement, with fake providers only. No UI change or production activation.
Q&A complete-input/budget/cancellation integration is next; general source policies,
legacy classification and reviewed activation remain blocked.
