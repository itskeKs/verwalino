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
