# Property-source access resolution

Status: resolution design refined after independent review; no policy activation.
Baseline: `b73a15a`. This refines the shared property-data domain design.

## Resolution contract

Implement a small allowlisted database resolver before attaching source policies.
It returns only a boolean and takes tenant, typed resource ID and capability.
Unknown kinds, missing rows, cross-tenant IDs, unknown ownership and ambiguous
source contexts fail closed. Configuration administration does not imply source
access. Every path takes the shared organization ACL lock before reading current
membership and exact domain grants. Financial grants never satisfy this resolver.

A property resolves to its unique prepared domain. A unit resolves through its
immutable tenant/property FK. The four capabilities remain read, prepare, approve
and export; live read-only members cannot prepare or approve.

An import's base audience is its applied property, otherwise its immutable
existing-property target, otherwise its immutable creator for an unassigned new
import. Creator ownership permits intake/review preparation and reading/export;
it does not by itself authorize publishing a new property. New-property approval
requires an explicit destination/grant bootstrap command implemented separately.
Existing-property approval requires that destination's approve capability.

Import results may contain text copied from every attached original. Canonical
Property/Unit values cannot retain different source audiences field by field, so
one import must have exactly one audience. An attached property-bound original
must already belong to that import's actual destination. For a NEW unassigned
import, every unbound original must have `uploaded_by = initiated_by`; an existing
property draft may have a different known uploader within its target audience.
Migration 0050 requires durable attachment-admission evidence for an unbound
existing-target original, captured under the uploader's live prepare grant at
attachment. Later grants cannot manufacture historical permission. Revoking the
uploader's grant leaves admitted sources available to other authorized viewers.
Migration 0052 supplies separate contemporary reviewed admission for eligible
known uploaders; it does not reconstruct attachment history. Unknown or bound
legacy custody remains a classification gate. After explicit
publication binds the original, its property audience no longer depends on its
former uploader's membership or grant. Durable admission/remediation is an
activation prerequisite for historical and team-upload workflows.
Unknown uploaders, a mismatching bound property and multiple import links are
classification exceptions even when the current actor holds both domain grants.
Intentional sharing requires a separate reviewed disclosure operation.

Resolve the graph without recursion. The import evaluator reads attached originals'
local metadata and link counts directly; it never calls the document evaluator.
The document evaluator may delegate to its sole import's evaluator, which applies
the original metadata checks above. Property/domain lookup is a terminal check.
Reject invalid topology before granting access. An as-yet-unlinked unbound original
belongs only to its known uploader during atomic intake. A property-bound original
requires its property capability and coherent linked-import context, if linked.
Coverage must identify every unsupported multi-import original before activation.
Do not choose a convenient parent, invent a link or infer a historical creator.

Candidate, evidence, issue, extraction run and queue receipt inherit their exact
import. Provider-pass rows inherit their exact run. Candidate/evidence and
issue/import consistency are enforced by 0048. Source payload reads must not be
replaced by a SECURITY DEFINER bulk read; the resolver exposes only authorization.

Approved facts/rules/references and relations require their destination property's
capability plus authorization for each referenced source import/original. Copied
provenance must not disclose a different audience's source text. Existing property
composite FKs cover unit/document/rule edges, but source-import/property agreement
still needs explicit validation for historical data. Audit read policies resolve
the typed entity through the same contract; unknown source audit kinds fail closed.

## Principal and publication integration

The public boolean wrapper resolves the actor from the authenticated transaction.
A private, non-executable-by-app role helper may accept an explicit actor for the
worker's immutable queue receipt before source rows become visible. This must not
let callers test or impersonate arbitrary actors; only reviewed security-definer
queue functions may invoke that private variant. Both paths use the same live
membership/grant logic and organization fence.

Publication order is a hard activation prerequisite. In one transaction: create
or lock the destination property; create its domain and explicit reviewed grants
or validate the existing destination; persist the applied import destination and
idempotent receipt; then bind originals and create derived rows. All steps roll
back together on failure. Source policies require this order when resolving the
publication destination.
The temporary internal ordering must not create a visible partially published
import. Creator ownership alone never grants publication authority.

The ordering prerequisite is implemented after 0050: the service now creates only
the new destination, flushes the applied import destination/result, then persists
original binding and derived data, all in the request transaction. Existing-target
publication uses its already locked destination. Migration 0051 adds an optional
explicit administrator/creator bootstrap with exact domain/grant proof, immutable
request digest and source-set protection. Its guarded receipt resolves the NEW
applied-but-unbound transition. Compatibility publication still creates no
authorization proof. Full policy integration and historical reviewed admission
remain prerequisites before source-policy activation.

## Integration gate

The resolver alone does not change compatibility reads or activate production
source RLS. It must be exercised with explicit grants, revocation/expiry, unknown
legacy creators, cross-tenant rows, linked originals and provider-principal changes.
Before activation, all table policies, storage export checks, worker/Q&A phase
checks and durable cancellation, destination publication, audit/privacy/indirect
views and financial completeness preflights must use the same boundary. Financial
aggregates must reject incomplete source access rather than compute partial totals.
A reviewed coverage report and atomic organization activation remain mandatory.
