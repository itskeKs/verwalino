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

## Complete Q&A source and budget admission (0054)

At base `12ec556`, Q&A validates the expected privileged source graph before
preparation, reservation and answer publication. An ordinary RLS-filtered result
cannot establish completeness. Its final phase resolves only its own immutable
ledger actor through a narrow metadata helper. Grant loss or pause commits a
sanitized fixed-reservation-cost cancellation even when the ledger row is hidden.
The resolver now handles all supported fact subjects, including RULE and the
legal-reference, assignment and document-relation source chains.

Independent review identified and resolved two blockers: unsupported RULE fact
sources and a caller-parameterized budget boolean followed by an unguarded INSERT.
The final implementation has a private audited budget policy, an atomic strict
reservation command using database cap/window/expiry, and an invoker INSERT guard
that requires the trusted command owner's SQL identity. Direct app INSERT,
compatibility-command use under enforcement, private aggregate calls and policy
access are denied. Whole-organization accounting includes hidden properties and
expires eligible reservations without exposing their content. Missing strict
policy blocks admission. PREPARATION retains existing application configuration.

Disposable PostgreSQL 16/Python 3.13 evidence: **8 initial integration cases passed**
after the budget repair. The Q&A/principal/RLS boundary run passed **72 cases** and
found a missing required date in the new RULE fixture. After correcting the
fixture, the expanded source/enforcement/search run passed **71 cases**, including
RULE, concurrent budget admission, canonical policy audit, exact window/expiry,
missing policy, direct SQL denial, paused/revoked phases, hidden source rejection,
hidden organization spend, migration round-trip/refusal and three NOWAIT locks.
Two historical search-migration rehearsals initially encountered retained test
source evidence. Their guarded disposable cleanup was made explicit, as was the
seed-user dependency that a focused run exposed; **both repaired cases passed**.
Previously passing checks with unchanged production code are reused. Independent
read-only review accepted the repair delta. No UI change, browser rerun or live
provider call is claimed.

Strict activation still returns false. A reviewed matching budget policy becomes
an additional activation prerequisite. General source RLS, original binding and
legacy classification, financial completeness and indirect disclosure closure
remain necessary; this does not complete B1.

## Strict original publication and source-link topology (0055)

At base `63734d1`, six initial tests demonstrated absent first-binding enforcement
and audit. Strict first binding now requires current destination approval, exact
retained audience proof and the matching APPLIED import receipt with the same
approver. Bound INSERT is denied; a no-op update does not duplicate the canonical
audit. Shared ACL statement locks precede row locks. The guard reads actual state
with `row_security=off`, preventing a hidden-state compatibility fallback.
Strict link insertion locks the original and rejects already-bound or second-link
topology. Existing source identity/content/binding immutability prevents UPDATE
from bypassing that insert check.

**10 focused cases passed**, including real new-property API approval and complete
original binding, existing attachment/current-review proof, missing proof/right/
applied receipt, pause, direct bound INSERT and second links before/after binding.
The compatibility and boundary run passed **111 cases**, including existing
approval, source review/admission/context, publication audiences, concurrent
binding, populated strict downgrade refusal, migration round-trip, both NOWAIT
dependencies and a temporarily misconfigured non-bypass definer owner. Independent
read-only review accepted the code delta; final competing binding/link and worker/
Q&A integration checks are recorded below. All checks use the guarded disposable
PostgreSQL 16 database and Python 3.13; no external provider or UI change.

This increment supplies first-publication proof enforcement without retroactively
classifying any already-bound legacy source. Broader CRUD/principal policies,
financial completeness, indirect disclosure closure and reviewed activation are
still mandatory. No source activation or B1 completion is claimed.

The final **51 binding/worker/Q&A cases passed**, including competing binding and
extra-link transactions. Earlier unchanged passing compatibility evidence is reused.

## Bound-original current classification (0056)

At base `b4ff59c`, the new classification workflow records an immutable current
ADMITTED or QUARANTINED decision. A current administrator needs explicit property
read/prepare/approve rights. Admission additionally requires known own custody,
active bytes and coherent quiescent topology. Unknown or mixed custody exposes
neither original bytes nor filenames and can only be marked for future quarantine.
The metadata-only page helper remains usable when ordinary source RLS withholds
those originals. Preview separately checks admissibility before normal ORM/storage
and byte-integrity verification. No historical owner or admission is backfilled.

Two initial API tests failed on absent routes. **31 first boundary cases passed**.
Independent review found mutable classified link role/order, incomplete ACL lock
ordering and a preview path for a known-uploader mixed source. The repairs freeze
all link mutations, include link role/sequence in the context digest, take statement
ACL locks before link rows and require admissible context before preview. The next
run passed **67 cases** and found three variable-name mistakes in historical test
adaptations. After repair, **27 focused cases passed**, including those three
historical rehearsals. These also cover raw SQL actor denial and canonical digest/
clock, receipt immutability, late rollback, identical concurrent retries, mixed
source storage denial, actual two-connection ACL waits with import/link NOWAIT
probes, five migration dependency locks, populated rollback refusal/empty roundtrip,
and both paused dispositions. Earlier unchanged passing checks are reused.
Independent read-only review accepted the repair delta.

**6 final UI tests, lint and production build passed**; the existing bundle-size
advisory reports approximately 618 KB. Actual local Chromium acceptance passed at
1440×1000 and 390×844: exact document-ID search, verified original bytes, renewed
confirmation after decision changes, server commit followed by simulated lost
response, identical retry, stable receipt navigation/reload, and quarantine-only
unknown custody with no preview. Mobile form width/content were both 358px and the
screenshots were visually inspected. The first browser launch used the wrong
working directory for Tailwind configuration; restarting the task Vite server from
`frontend` resolved it. Final evidence is in `/tmp/hv-classification-acceptance/`;
the fixture and browser scripts are `/tmp/hv-classification-browser-fixture.py` and
`/tmp/hv-classification-browser.cjs`. API/Vite/browser processes were stopped.
All database work used disposable PostgreSQL 16, Python 3.13 and fake local PDFs;
no external provider was called.

The UI explicitly says these are staged decisions and do not yet restrict current
compatibility access. Classification-aware source resolution, quarantine dominance,
source CRUD/RLS, complete indirect disclosure/effect checks and reviewed activation
coverage still gate B1 completion.

## Strict bound-source proof resolution (0057)

At base `5560667`, two test-first failures demonstrated acceptance of a bound
legacy original without proof and a quarantined original with older attachment
proof. The new private bound-source predicate rejects both outside PREPARATION.
Exact retained attachment/review/publication and ADMITTED classification remain
usable with the consuming actor's current rights. Quarantine dominates all proof.
Document and import resolution use the same metadata predicate without recursion.
Classification authority changes acquire exclusive ACL before source row locks.

**19 initial binding/new cases passed**, followed by **50 expanded cases** covering
Q&A source enforcement, classification retries and source locks. **150 further
regression cases passed** across source access/admission/review/enforcement,
publication and knowledge search/query. One migration assertion incorrectly
expected the real readiness gate inside its deliberately substituted disposable
enforcement context; correcting that test expectation produced a **passing focused
rerun**. No production change was needed for that failure. Reuse unchanged passing
evidence; UI behavior is unchanged by this backend-only increment.

Evidence includes earlier proof retention and NEW publication after binding,
reviewer versus reader revocation, current classification through the production
API, actual concurrent ACL waiting before import/document row locks, wrong definer
owner failing closed, application execution denial for private helpers, seven
NOWAIT migration dependencies, strict rollback refusal and PREPARATION roundtrip
retaining classification rows. A hidden quarantined original blocks derived
source access and the entire Q&A request with zero provider calls/reservations.
An incompatible queued bound original is durably cancelled before extraction.
The independent read-only reviewer found no actionable security/correctness issue.
Logs: `/tmp/hv-bound-source-{red,first,boundary,regression,migration-final}.log`.

Strict activation remains unavailable. Source CRUD/RLS, financial whole-input
checks, copied snapshots, export/audit/privacy closure and reviewed coverage still
gate B1 completion; this does not claim a complete source authorization rollout.

## Imperative original export checks (0058)

At base `97bd02e`, **five expected test-first failures** showed that ordinary bound
PDFs, import draft PDFs, unbound admission review and bound classification review
could reach storage without strict export permission, including quarantined review.
The new boolean export helper checks current org/actor, a closed purpose allowlist,
ordinary proof/read/export or the exact known-own review exception plus destination
export. Strict quarantine dominates that exception. All four services call it
before ORM source loading/storage. Review lists expose current preview availability.

**58 first focused cases passed**. After adding list flags, migrations and denial
coverage, **112 cases passed** with two pre-existing API worker journeys returning
IDLE in the combined suite. Both passed in isolation. Inspection found shared
fixture queues containing earlier queued test rows; the bounded worker correctly
visits/cancels those before later jobs. Those two journeys now use fresh tenant
queues, with organization enumeration restricted to that test tenant while retaining
real claim/principal/lease/pipeline behavior. The complete combined rerun passed
**114 cases**. No worker production behavior changed. **12 UI tests, lint and build
passed**; the existing bundle-size advisory remains (~618 KB).

Tests prove denial before storage, one verified read on success, current grant
revocation, strict PAUSED reads, cross-tenant/other-custody denial, purpose spoofing,
preview flag consistency, five NOWAIT dependencies and rollback refusal/roundtrip.
Independent review found no actionable export or purpose-bypass issue. Logs are
`/tmp/hv-source-export-{red,first,final,worker-isolated,final-isolated,ui,lint,build}.log`.
The prior desktop/mobile layout evidence remains valid; this increment changes a
link's availability and has dedicated rendering coverage rather than a new browser
layout acceptance claim. Source CRUD/RLS and full activation closure remain open.

## Complete financial source input (0059)

At base `bb1b2f7`, **two expected red cases** demonstrated a rental calculation
without current source authority and a plan calculation ignoring an RLS-hidden
unproven original. Strict calculation/attestation entry now requires the complete
property source graph plus units before ordinary input queries. Five database
output/coverage tables also guard raw snapshot, line and attestation effects.
Current financial scope authority remains independently required.

The initial run passed 16 cases before an existing concurrency test timed out: it
expected correction creation to begin before the financial lock, but earlier scope
integration takes that lock first. Its failed hook left background requests active;
the task-owned pytest process was stopped. The repaired test observes the actual
PostgreSQL advisory wait and always releases/awaits both requests. The resulting
**39-case boundary run passed**. Initial expanded cases exposed shared module seed
properties retaining source grants; explicit disposable cleanup fixed that test
isolation. **Six expanded source cases passed**.

Independent review found a raw cross-property coverage bypass: a declaration could
refer to subject A while supplying source-authorized property B. The trigger now
derives both source targets from the actual parent, rejects a mismatching declared
property and checks both audiences on computed draft reparenting. Dedicated raw SQL
regressions cover both settlement and plan. Computed parent deletion requires the
source gate; only a blank draft retains the early return. The repair was accepted
by independent review.

The final **99-case run passed**, covering the new source suite, financial input
policy, M3 rental and M4 WEG/plan/asset flows, and original-binding rehearsals. Cases
include all four financial families succeeding after explicit read grants, denial
and revocation before writes, strict pause, hidden original completeness, direct
snapshot/line/coverage write denial, cross-property forgery, ACL waiting before a
parent row lock, eight NOWAIT dependencies and strict rollback refusal/roundtrip.
No UI or engine arithmetic changed. Logs: `/tmp/hv-financial-source-{red,first,
boundary,focused,repair,final}.log`.

The preflight conservatively requires the whole property-source graph. Source
CRUD/RLS, complete copied-snapshot and report/privacy/audit disclosure handling,
and reviewed activation coverage still gate B1 completion.

## Stored financial disclosure (0060)

At base `23c088f`, two expected red cases showed copied plan/settlement sources
readable without current source rights. Restrictive SELECT policies now cover
parents, lines and coverage using trusted complete-source and exact-parent checks.
The first expanded run passed 86 cases and failed one obsolete coverage count
assertion: denied coverage is now hidden, not deleted. The repaired assertion
verifies zero in strict mode and the retained row after PREPARATION restoration.
A subsequent 67-case boundary run passed, including scoped statements, financial
boundaries, raw writes and RLS coverage.

Independent review identified unbound legacy PDFs bypassing financial EXPORT.
A regression first reproduced PDF 200 instead of 404. The repair denies unbound
outputs in ENFORCED/PAUSED until reviewed into an accounting scope. Bound outputs
require exact financial EXPORT separately from source EXPORT. Fifteen disclosure
cases passed, including all four PDF families, financial read-only denial, retained
stored reads in PAUSED, eight NOWAIT dependencies and strict refusal/roundtrip.
The reviewer accepted the repaired delta without remaining concrete findings.

The final combined run passed **82 cases**: disclosure, complete financial source
input, M3 rental and M4 WEG/plan/asset flows. Earlier valid statement/RLS evidence
was reused; no frontend changes require UI checks. Raw insertion regressions use
captured PREPARATION fixtures rather than SELECT from newly hidden rows, so the
write guard remains exercised. Logs: `/tmp/hv-financial-disclosure-{red,first,
boundary,final,unbound-red,export-repair,accepted}.log`.

This protects the named financial output tables and PDF routes. Source CRUD/RLS,
audit/privacy/general report closure and reviewed activation coverage remain open;
B1 and source activation are not complete.

## Canonical master data and explicit creation (0061)

At base `a8c027a`, four expected red tests demonstrated missing direct property/unit
read and write barriers, implicit creation without an audience, and raw reparenting.
Restrictive policies now enforce the current property capability. Independent
review additionally confirmed that copied master fields require the complete source
READ graph: two further expected red tests showed unproven/quarantined source facts
still visible. The repaired predicate hides and protects those fields while allowing
ADMITTED evidence. PREPARATION stays compatible; PAUSED keeps authorized reads.

New properties can prepare their explicit domain/grants before the parent, using a
deferred composite FK within the same transaction. An own read/prepare grant is
mandatory; declaration publication retains its additional approve requirement.
Manual creation has an immutable typed actor-scoped receipt and rechecks access on
replay. The old generic-looking receipt was found to be cost-only; its FK is retained.
A narrowly admin-only existence helper supports setup by known property ID without
returning source fields. Tests prove failed parent creation rolls back all audience
rows, concurrent retries create one result, changed requests conflict, raw writes
cannot choose another audience, and strict/populated migration rollback refuses.

Early integrated runs passed 74 and 112 cases with one historical rehearsal failure
each. The 0055 rehearsal now unwinds its new 0061 trigger dependency inside its
rollback-only savepoint. The 0023 negative-MEA rehearsal previously attempted a full
downgrade through later populated immutable source migrations. It now applies the
actual 0023 upgrade to isolated TEMP pre-migration tables and verifies retained
negative history plus rejection of a new negative row. The final combined run passed
**189 cases** across master access, source proof/binding, declaration publication and
approval, MEA, financial source input/disclosure and RLS registration. Independent
review accepted the repaired boundary. Logs: `/tmp/hv-master-access-*.log` and
`/tmp/hv-master-source-proof-red.log`.

**16 UI tests passed**, with the two property-form tests rerun after checkbox spacing
repair. Final lint/build passed; the existing ~619 KB bundle advisory remains.
Regular Playwright was used because the Browser plugin/skill is unavailable. The
first temporary Vite launch used the wrong working directory and produced a CSS
overlay; restarting from frontend resolved it. The final Chromium loop passed at
1440×1000 and 390×844 under synthetic strict state: explicit unchecked audience,
retained input after a committed create's simulated lost response, exact body/key
retry, and one durable property/domain after reload. Both screenshots were visually
inspected; mobile form width/content were 316/316 px. No runtime errors or unexpected
HTTP errors remained. Script and evidence: `/tmp/hv-master-browser.cjs`,
`/tmp/hv-master-acceptance/`. Task API/Vite/browser processes stopped and synthetic
strict state was removed; the activation predicate remains false. Five publication
denial cases passed after correcting their rollback assertion to the target property
ID. A final 15-case master/MEA run passed after removing a redundant unit preflight.

This milestone covers property/unit master boundaries and creation. Original and
derived source CRUD/RLS, audit/privacy/report closure and reviewed activation
coverage remain required. B1 and the full M-A–M-G delivery are not complete.

## Derived source rows and review preflights (0062)

At base `fc5aa15`, expected red cases demonstrated other creators reading review
children, source facts remaining visible without grants, read-only grants writing
canonical rows and an inaccessible review appearing as an empty draft. The new
migration covers fifteen SELECT boundaries and fourteen effect boundaries using
actual row metadata, complete expected graphs, typed provenance and current roles.
Service checks happen before child loading, approval/replay or upload storage.

The initial integrated run passed 104 cases with two failures: the existing
PREPARATION explicit-publication replay check needed retention, and a strict test
fixture needed an explicit creation audience. Both were repaired. Further focused
runs exposed historical assertions relying on actorless or partial graph reads;
these now set the actual actor or capture known IDs before strict filtering, and
assert that whole-graph denial hides all copied facts. No production guard was
weakened. The strict existing-amendment fixture explicitly records its required
individual review.

The compatibility run passed **136 cases**, including strict existing-property
amendment and replay, import API, new approval, immutable source contexts and
original exports. The final RLS/bound/derived run passed **49 cases**, with one
obsolete quarantine assertion repaired separately; that focused case then passed. Coverage includes strict
upload/worker first inserts, all seven review-child types, all seven canonical
types, stored Q&A PAUSED read/revocation, sixteen NOWAIT dependencies and strict
rollback refusal plus PREPARATION round trip. Independent reviewer accepted the
bounded 0062 diff without remaining findings. No frontend changes or UI checks
were required. Logs: `/tmp/hv-derived-{integration,targeted,final-focus,
compatibility,accepted,quarantine}.log`.

Original/import row RLS, purpose-bound review exceptions, indirect disclosure and
reviewed activation coverage remain open. Source activation stays disabled; B1
and the full M-A–M-G implementation are not complete.

## Ordinary source rows and explicit review channels (0063)

At base `1008290`, four expected red tests reproduced ordinary cross-creator
original/import visibility, forged creators, and review exceptions sharing the
ordinary row channel. Eight restrictive policies now cover source CRUD; actual
row metadata supports first inserts/returning and preserves publication ordering.
Separate bounded metadata and purpose-allowlisted original functions keep legacy
admission/classification usable without broadening ordinary reads. The first four
focused cases passed. The initial integration passed 114/122; eight historical
assertions were updated for actor-scoped reads, RLS-rejected zero-row updates and
the new migration dependency. The repaired boundary run passed 45 cases.

Independent review found an unlinked-original claim attack: a preparer could link
another creator's hidden original into their own draft by known UUID. A new raw
SQL regression first failed to reject that link. The repair checks prepare for
both parent and original under ACL → import → original locks, retaining existing
single-link guards. A second expected red reproduced prepare-only forgery of
APPLYING approval metadata; that context now requires approve. The reviewer
accepted both repairs and the bounded milestone.

An intermediate validation attempt failed at test setup because the disposable
DB held the earlier uncommitted 0063 schema without the newly added trigger. The
actual new guard was installed in that disposable database before the harness
performed its clean downgrade/upgrade. No production migration or source records
were changed. The following expanded run passed **123 cases**, covering originals,
derived records, binding, import API and queue-principal behavior. Logs:
`/tmp/hv-original-{source-red,source-first,source-integration,source-repaired,
link-red,approval-red,source-reviewed,source-consumers}.log`.

Database-enforced lifecycle/progress/lease transition hardening is an explicit
pre-activation follow-up; existing queue actors and application fencing remain.
Indirect privacy/audit/report disclosure and reviewed legacy coverage are still
open. Source activation remains false. B1 and the full implementation are unfinished.

The consumer run passed **85 cases** across master data, stored financial
outputs, Q&A, bound sources, RLS coverage and explicit publication. Two historical
checks needed adaptation: quarantined worker cancellation is now inspected after
leaving synthetic strict mode while asserting the strict row remains hidden, and
the 0051 rehearsal unwinds 0063's policy dependency before removing publication
columns. These changes do not alter production authority. No frontend code changed;
existing UI rendering checks were reused, with new review contracts verified at API
and database boundaries.
The two adapted consumer cases passed in the final focused run
(`/tmp/hv-original-source-last-repairs.log`). All 0063 checks now have passing
applicable evidence; no unresolved review blocker remains within its stated scope.

## Privacy source completeness (0064)

At base `fc62b79`, six expected red tests demonstrated that finalized rental/plan
outputs hidden by source RLS disappeared from retention status and export. Direct
request-deletion/anonymize calls could then treat retained evidence as absent.
The failing mutation tests ran in rolled-back transactions, preserving fixture PII.
The new private enumerator checks every expected recipient property through the
complete source READ/EXPORT graph in addition to the existing financial predicate.
The first regression run passed 48 privacy/financial/dunning cases.

Three more expected red cases reproduced expiry during binding discovery and a
stale authenticated administrator continuing after demotion. The service now repeats
completeness after ordinary binding reads, retaining EXPORT for exports; all privacy
routes recheck the current administrator under the data transaction's ACL lock.
The expanded run passed **74 cases** including all eighteen new completeness,
expiry, principal, private-function, rollback and NOWAIT cases, plus M9 privacy,
financial scope, dunning and RBAC. Independent review accepted the bounded change
without actionable findings. Logs: `/tmp/hv-privacy-source-{red,first,phase-red,
accepted}.log`. No frontend changes were required.

This closes retained outputs hidden by 0060. Person/profile/history, audit/general
report disclosure, raw worker transition hardening and reviewed activation coverage
remain open. Source activation stays false; B1 and M-A–M-G remain incomplete.

## Typed audit disclosure (0065)

At base `808d178`, four expected red tests reproduced copied audit payload leaks.
The typed allowlist now inherits ordinary source RLS, with complete graph access
for financial output history even when an empty draft parent is visible. Missing,
foreign and unknown targets fail closed. Current administrators can read control
metadata through a bounded boolean without receiving internal table SELECT rights.
AuditRepository appends without RETURNING so hidden events can still be retained.

The focused source/original run passed 18 tests. A separate control-metadata red
case exposed missing direct internal table privileges and led to the narrow helper.
The final integrated run passed **153 cases** covering audit, original and derived
sources, privacy, financial output disclosure, M2 receipts and M8 meetings. It also
verified strict rollback refusal, preparation round-trip history preservation and
NOWAIT locks. An intermediate setup failure came from the disposable database's
earlier uncommitted migration; the clean repaired run passed. Logs are recorded
in [the boundary note](../2026-10-02-source-audit-disclosure.md). Independent review
accepted the milestone without actionable findings.

Person/history and meeting audit kinds remain hidden in strict mode until their
source policies are integrated. Ordinary history/meeting/report surfaces, worker
lifecycle integrity and activation coverage remain open. Source activation stays
false, and B1 plus subsequent M-B–M-G work remain incomplete.

## Meeting source boundary (0066)

At base `7d846aa`, four expected red tests demonstrated meeting source read/write
and both PDF export bypasses. Four-table CRUD policies now require current source
READ/PREPARE, with APPROVE for register effects and EXPORT for both PDFs. Trusted
expected graph validation rejects mixed historical relationships; immutable audience
and parent identities prevent reparenting. The optional agenda association remains
within the same meeting and retains its existing delete-to-null behavior.

The first 46-case run passed; the expanded source/M8/audit run passed **51 cases**.
Strict positive lifecycle, audit visibility, paused export, revoked reads, register
approval, malformed legacy graphs, raw context attacks and expiry during PDF
discovery are covered. A separate run passed **45 migration and isolation cases**,
including all seven NOWAIT dependencies, history-preserving preparation round-trip
and strict rollback refusal, RLS coverage, RBAC and tenant isolation. Independent
review accepted the boundary without actionable findings. Evidence is linked in
[the meeting source note](../2026-10-02-meeting-source-access.md).

No E5 legal voting/process completion is claimed. Person/profile/history, indirect
reports, worker lifecycle integrity and activation coverage remain open. B1 is still
incomplete; source activation remains false and later M-B–M-G work remains.

## Relationship history source boundary (0067)

At base `87c7067`, four expected red cases reproduced ordinary history disclosure,
writes under READ alone, privacy access without expected history sources, and mixed
historical context accepted as complete. History CRUD now resolves current unit/
property authority and same-organization profile/person identity. The complete
source predicate validates expected history coherence before financial, master,
Q&A and meeting consumers can treat filtered rows as complete inputs. Privacy also
checks every expected relationship property, including inactive history.

Initial privacy/M3/M4/M9 integration passed **83 tests**, and expanded history,
financial-input, master, profile, account-statement and meeting coverage passed
**83 tests**. Two additional red cases exposed profile-to-person reassignment;
identity triggers now prevent it. The next focused run passed 71 cases.

Independent review prompted a two-connection check of erasure and history writes.
The existing 0031 financial-input lock already serialized them, but the regression
confirmed a waiting writer could create an active relationship after erasure. The
write predicate now checks current person state after that wait. Final history/
profile/master/privacy verification passed **72 cases**, and the race passed alone
with explicit positive pre-wait authority assertions. Review accepted the boundary
without remaining findings. Migration round-trip preserves history/audit hashes,
strict downgrade is refused, and all nine NOWAIT dependencies are covered.

See [history source evidence](../2026-10-02-history-source-access.md). Person/profile
contact custody, other source/indirect report coverage, worker lifecycle integrity
and activation evidence remain open. Source activation is false; B1 and later
implementation milestones remain incomplete.

## Independent contact audience preparation (0068)

An exact Person now has a separate immutable audience with explicit read, prepare
and export grants. No administrator, property or financial permission creates a
contact grant. Current role, deactivation, expiry and revocation apply immediately.
The database generates the opaque label; API and DB accept only controlled reason
codes. Canonical audit contains no client-supplied label or free-text reason.

Evidence on the disposable PostgreSQL 16 database: 7 initial and 30 expanded
backend checks passed. The combined contact/property/audit run passed 60 cases;
its one failure was an older migration rehearsal which attempted to remove
publication columns while later policies still referenced them. Explicitly
unwinding 0063/0062 within that rollback-only rehearsal repaired it; its isolated
rerun passed. This includes 31 contact cases covering raw metadata forgery,
concurrent replay, cursor context, current principals, audit disclosure, safe
round-trip and five NOWAIT dependencies. No production migration was weakened.

Four frontend tests, lint and production build passed (existing bundle-size
warning). Chromium on the real local API verified create with a lost committed
response and same-key retry, a team grant, revoke, reload persistence, desktop and
390px mobile layout. Native select options require an attached-state wait and the
mobile capture waits for the sidebar transition to finish. Visual inspection found
the shared flex shell retaining its intrinsic width; adding min-width: 0 lets the
content shrink while the grant table scrolls locally. A repeat browser journey
verified the form and main area fit the viewport. No browser page errors.
Independent review accepted the implementation with no actionable findings.

Person/profile source RLS and consumer completeness are still required. Source
activation remains false; this preparation increment does not complete B1.

## Contact source access and authoritative privacy (0069)

Contact creator custody, exact audience RLS and expected financial/dunning contact
consumers now form an independent source boundary. Privacy-state mutations use a
current-admin database command with trusted retention facts and ACL/financial-input
serialization. Ordinary SQL cannot forge privacy state. Waiting cost writers also
recheck contact state after erasure. Current API capabilities drive contact controls;
stale detail and denied-repeat export data is removed from the UI.

Final focused backend verification passed **95 tests**; frontend verification passed
four detail/domain tests plus one export-denial regression, lint and build (existing
bundle warning). Real strict Chromium journeys passed creator, reader, preparer and
revocation behavior with inspected desktop/mobile captures. Migration and three
multi-connection concurrency cases passed. See [detailed evidence](../2026-10-02-contact-source-access.md).

Source activation is still false. Worker lifecycle integrity, remaining indirect
source coverage, legacy remediation and activation evidence remain open. B1 and the
later implementation milestones are not complete.

## Worker runtime and lifecycle commands (0070)

The declaration worker has a separate RLS-constrained login. Web app_rw cannot
claim work, renew ownership, submit worker state or assume the worker role. Strict
raw processing state/budget updates are rejected; closed commands enforce current
queue actor, source permission, lease token/generation/expiry and allowed stages.
The DB owns scheduling and completion revisions. Runtime and migration checks
reject unexpected role inheritance, delegate membership and elevated attributes.

Six initial red cases reproduced raw lifecycle forgery. Worker/principal coverage
passed 50 cases. Expanded pipeline/source/API coverage passed 161 cases; two older
fixtures needed current actor setup and a later-migration unwind, and their isolated
rerun passed. Command/strict-pipeline/approval/review/migration coverage passed 120
cases. The local fake-Docker deployment regression passed with worker/web credential
separation and the packaged migration head gate. No deployment was performed.

The known NEEDS_REVIEW revision exception remains intentionally visible in a test:
a raw preparer can increment it without candidate/audit effects. Worker child-row
fencing and atomic review commands remain follow-up work. Source activation stays
false and B1 is incomplete. See [worker command design and evidence](../2026-10-02-declaration-worker-commands.md).

The final worker boundary run passed **35 focused cases**, including role graph and
attribute drift, PostgreSQL 16 inheritance-edge options, strict extraction to review,
and retained contact privacy checks. Independent review accepted this bounded
increment with the documented review residual. Rendered Compose also confirms
that the API receives no worker connection credential.

## Worker output transaction fencing (0071)

Strict writes to extraction runs, pass metadata, candidates, evidence, issues and
page counts require a private database-owned receipt for the actual worker login,
current transaction and current lease. Expiry and queue-source authority are
rechecked under the ACL and import locks. Web callers and forged GUCs cannot mint
worker authority. Cancellation serializes with output writes and invalidates the
old lease; only narrow trusted cleanup commands bypass the worker trigger.

Compatibility verification passed 195 cases, with three fixture repairs passing a
14-case focused rerun. Private-table RLS, expiry, cross-transaction reuse, direct
pass/evidence attacks, human-review forgery, cancellation and migration locks are
covered. Independent review found no new blocker. Deployment-script simulation
passed with head 0071. See [details](../2026-10-02-declaration-worker-effects.md).

The NEEDS_REVIEW candidate/issue/revision compatibility remains a separate open
boundary. Activation remains false; B1 and M-B–M-G are not complete.

## Atomic human review decisions (0072)

Human decision fields, actor/timestamps, candidate versions, draft revision and
canonical review audit now change through one database command. Strict raw writes
to those fields and review audit are rejected, including from the worker login.
Commands recheck current human/source authority, exact candidate sets, revision,
evidence/comment requirements and the conservative bulk policy. Managed resolved
issues reopen on change; Python must revalidate the complete graph in the same
transaction. Stale ORM instances are expired before reading the result.

Review/approval/source compatibility passed 153 cases; a 34-case focused run passed
fixture repairs and a transient connection rerun, concurrency, strict HTTP review,
rollback and migration checks. Independent review accepted the bounded increment.
See [design, limits and evidence](../2026-10-02-declaration-review-command.md).

Complex correction semantics and raw managed-issue resolution remain application
compatibility boundaries. Current-revision exact replay is preserved; durable
idempotency-key replay is not implemented by this command. These remain explicit
activation blockers. Source activation remains false and B1 is incomplete.

## Isolated semantic review runtime (0073)

Canonical correction parsing and full graph revalidation now run in a separate
app_review transaction on the two review mutation routes. The role has only explicit
review-graph reads and narrow commands, no inherited roles/table writes/ownership
or elevated attributes. Web and worker SQL cannot assume it, invoke its commands,
submit unchecked corrections or resolve managed issues. A private DB context binds
issue outcomes to the actor/import/revision/backend/transaction established by the
review command. Only controlled managed resolutions and allocation BLOCKER inserts
are permitted; source identities and messages cannot be rewritten by a caller.

The semantic runtime is trusted Python code; the database does not claim to prove
Python executed or resist compromise of the API process/review credential. This
closes the ordinary-runtime SQL bypass while retaining one authoritative validator.
The 32/66 focused/expanded runs, 150 compatibility cases and final 42 role/expiry/
strict HTTP/RLS cases passed. Independent review accepted the boundary. Rendered
synthetic Compose and fake deployment checks passed; no deployment occurred.
See [design, trust contract and evidence](../2026-10-02-declaration-review-runtime.md).

Durable review request-key replay, remaining indirect consumers, legacy reconciliation
and reviewed activation evidence still remain. Activation is false and B1 is not
complete; the full M-A–M-G implementation continues.

### Durable review request retries (0074)

Optional UUID Idempotency-Key headers preserve legacy API compatibility while the
first-party single/bulk review UI always sends a retained key. Private actor/operation
receipts commit with the full review transaction; identical retries return the saved
response after checking current source READ/PREPARE and live writer authority.
Bulk UUID selection order is canonical. Changed request content returns 409; newer
review decisions are never overwritten by replay. Missing keys keep stale-revision
behavior. Downgrade refuses to erase completed receipts.

Evidence: 32 initial backend checks; 104 expanded checks plus two corrected new
fixture expectations; final 11 idempotency/migration cases pass. Two UI RED tests
then 43 UI passes; lint/build, simulated deployment and shell syntax pass. Chromium
proved lost-response retry, identical key/result, one revision and reload persistence
under strict source enforcement. Independent review's bulk-order finding is fixed.
See [review retry implementation](../2026-10-02-declaration-review-idempotency.md).
B1 activation and the remaining overall plan are still incomplete.

### Indirect statement, deadline and dunning sources (0075)

Strict report contexts now require an exact financial scope and complete source READ,
including payment-only contacts. The deadline monitor cannot turn hidden leases into
“No overdue items.” Dunning notice/provenance RLS and PDF/effect entry checks preserve
separate source READ/EXPORT and financial capabilities; PAUSED permits authorized
reads/exports and denies effects. Unknown legacy dunning provenance remains blocked.
Privacy completeness includes hidden issued notices; snapshots are never rewritten.

Evidence: two initial disclosure RED tests, then 29 checks passed; additional contact
revocation and false-zero deadline RED tests fixed. Compatibility112passed plus one
legacy error-order regression repaired; final53 report/deadline/payment tests pass.
Migration roundtrip preserves hashes and refuses strict downgrade. Independent
review, shell syntax and fake-Docker deployment checks pass. See
[report source implementation](../2026-10-02-report-source-access.md).
Activation stays false; legacy reconciliation/coverage and later milestones remain.


### Aggregate preparation inventory (0076)

Administrators can review 18 tenant-only structural counts without source names,
identifiers, amounts or original contents. Live role, expiry and revocation checks
apply. Definer visibility counts hidden legacy rows rather than reporting false
zeroes. No automatic grants, classification, custody proof or activation follows.
Failed UI refresh clears stale counts and supports retry.

Evidence: 16 initial backend/RLS passes; 11 final inventory checks plus a passing
representative legacy/roundtrip case; three UI checks, lint/build, shell syntax and
simulated deployment pass. Chromium desktop/mobile, failed-refresh recovery and
clerk denial passed; independent review found no issue. See
[implementation and limits](../2026-10-02-access-preparation-inventory.md).
B1 and the remaining full plan are incomplete; activation remains unavailable.


### Strict mandate original boundary (0077)

Unclassified mandate originals no longer retain organization-wide compatibility
access in strict source mode. Existing explicit scope grants govern metadata,
bytes and audit visibility. PAUSED retains authorized disclosure and blocks upload
or archive effects. Upload checks the shared helper before storage; database RLS
rechecks it during persistence. Originals and audit history are not rewritten.

Evidence: disclosure/upload/pause RED cases fixed; 15 focused checks, 24 expanded
passes plus a corrected expiry fixture, then 47 enforcement/legacy/RBAC/tenant
checks pass. Same function OID and historical hashes survive preparation roundtrip;
strict downgrade refuses widening. Independent review, shell and simulated deploy
checks pass. See [implementation](../2026-10-02-mandate-source-enforcement.md).
Legacy remediation and B1 activation remain pending.


### Mandate legacy quarantine cutoff (0078)

Historical unclassified mandate uploader/storage metadata was mutable and cannot
justify a known-uploader admission path. A locked migration observes current
metadata hashes without rewriting originals, retains those rows in quarantine,
and prevents further unclassified insertion or legacy mutation in every mode.
Ordinary metadata, bytes and audit remain inaccessible. New operational originals
require an independent upload into an explicitly authorized scope with a reason.
This records contemporary custody; it does not recover historical provenance.

Evidence: backend/UI REDs fixed;31 expanded cases plus12 focused/general mandate
passes after a transient DB setup retry; three UI tests, lint/build, shell and
simulated deployment pass. Populated rollback refuses evidence loss; hashes remain
unchanged. Independent review's obsolete UI claim is corrected. Chromium verified
fresh upload/download and desktop/mobile controls. See
[cutoff and evidence](../2026-10-02-mandate-legacy-quarantine.md).
Administrative disposition review of frozen identifiers remains subsequent work.
B1 activation and the rest of M-A–M-G remain incomplete.


### Administrative mandate quarantine review (0079)

Administrators can page through opaque legacy observations and record continued
quarantine with a closed reason code, observed digest, current reviewer and server
time. Original names, uploaders, storage references and bytes remain hidden.
One private immutable disposition and audit commit together. Current authority is
required before idempotent replay; conflicts never overwrite an earlier decision.
The UI explains that review grants no access and does not authenticate history.

Evidence: two HTTP REDs then9 focused and20 expanded API/RLS/concurrency/role/
paging/downgrade cases pass; seven final UI tests and lint/build pass. Independent
review's stale local receipt-state finding is fixed. Chromium dropped a committed
response and confirmed identical retry key/receipt, reload persistence, failed-load
recovery and desktop/mobile rendering. Shell/simulated deployment checks pass.
See [implementation and operator entry](../2026-10-02-mandate-quarantine-disposition.md).
Activation remains false; B1 and the full plan are not complete.
