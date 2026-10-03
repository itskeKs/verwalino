# Property management implementation plan

Date: 2026-10-01. Planning/code inspection baseline: `be99a3c` (clean tree).
Status: planned, **no product functionality implemented by this document**.

## 1. Scope, authority and evidence

Deliver the [product/UX/technical design](2026-10-01-product-ux-technical-design.md)
through independently reviewable local milestones. Preserve FastAPI, PostgreSQL
RLS, React, existing pure accounting engines, immutable records and document
storage. Both landlord and professional presentations use the same domain and
permissions. Minimize typed fields through source-backed proposals, contextual
prefill and shared exception resolution; financial confirmation stays explicit.

Read alongside the [code audit](2026-10-01-product-audit-and-parity-plan.md),
[authenticated reference review](2026-10-01-reference-software-review.md),
[roadmap](roadmap.md), [architecture](architecture.md),
[receipt flow](m2-gobd-belegfluss.md), [rental engine](m3-abrechnungs-engine.md),
[WEG accounting](m4-weg-abrechnung.md) and [payments](m6-zahlungen-mietkonto.md).
The October design supersedes historical bank/portal exclusions and manual-first
UX, but does not resolve historical specialist questions. Existing ADR invariants
continue to apply. M-A–M-G below are delivery milestones, not historical M0–M9.

- **O:** RF observations retain their completed/draft/observed/unverified depth.
  The review process is complete; exhaustive reference coverage is not. RF09
  receipt save, RF12 CSV import and RF31 downloads remain unverified. RF18–RF19
  and RF14–RF16 establish drafts, not signing or finalization.
- **R:** public-source capability baseline already recorded in the design.
- **D:** target decisions in this plan, including inference, source-backed
  automation and stricter completeness. Contextual filing is not inference.
- **V:** specialist, provider or product release decisions in section 7.

All work below is future implementation scope. This planning task authorizes
documentation only. No browser reinspection, cleanup of remaining TEST records,
provider activation, message, push, deployment or SSH is required. Later live
actions require their own authorization; fake adapters and synthetic fixtures
allow local progress. Do not change declaration models/prompts/provider policy.

## 2. Verified code map and constraints

Full paths in this document are repository-relative. Backend shorthand beginning
`api/`, `models/`, `repositories/`, `services/`, `core/`, `engine/`, `workers/`,
`integrations/` or `exports/` is relative to `backend/app/`; unqualified page names
are relative to `frontend/src/pages/`. Other abbreviated filenames inherit their
explicit parent in the same sentence. **Existing** paths below were inspected;
**new** paths in work packages are proposals, not delivered modules.
Every new API needs registration in `backend/app/main.py`, model registration in
`backend/app/models/__init__.py`, an org-scoped repository, migration/RLS coverage,
and corresponding `frontend/src/api/types.ts` / `endpoints.ts` updates. Do not
assume generated TypeScript or a browser test runner already exists.

| Area | Existing anchors and concrete implication |
| --- | --- |
| Upload-first cost | `backend/app/api/cost_entries.py::CostEntryCreate`, `backend/app/services/cost_entry_service.py::create/confirm`, `frontend/src/pages/InvoiceDocumentDetailPage.tsx::handleCostSubmit`: no source ID in create; confirm releases only an attached active invoice. Repair this exact path, retaining cost-first upload. |
| Source persistence | `backend/app/models/invoice_document.py`, `repositories/invoice_document_repo.py`, `services/invoice_document_service.py`: nullable `cost_entry_id`, immutable bytes/hash and version chains; active-for-cost lookup expects at most one row. Blob storage precedes synchronous extraction and DB record creation. Hash storage alone does not deduplicate business effects. |
| Invoice extraction | `backend/app/main.py` installs `RoutingExtractor(PdfTextExtractor())`; `core/pdf_extraction.py` defaults OCR off. Its current fallback renders the PDF without page/time budgets. `core/einvoice.py` handles structured XML/embedded XML; keep that priority. `core/uploads.py` caps file bytes at 25 MiB, not OCR workload. |
| Rental completeness | `backend/app/services/settlement_service.py::_load_engine_input` uses `advance_payments_total or Decimal("0.00")`. `set_advance_payments_derived` uses a year. `api/settlements.py` exposes direct compute/preview/finalize, so UI-only guards fail. |
| Other finalizers | `services/weg_settlement_service.py` has `finalize_weg` and `finalize_asset_report`; `api/weg_settlements.py` defaults reserves/collections to zero/empty. `services/economic_plan_service.py::finalize` uses structured planned inputs. Distinguish an explicitly approved zero budget from absent inputs. |
| Transactions | `core/tenant.py::get_db` owns `session.begin()`; repositories flush. Do not add nested independent commits to atomic create/link/confirm. PDF generation currently follows finalized snapshots; staged artifact finalization is M-D work. |
| Money | `models/bank_account.py` scopes account to property, without a mandate field. `services/payment_service.py` mirrors property from account and sets mandate to None on create; payments have `period_year`. Extend coherent accounting scope and period allocation before imported money. |
| Existing domain | `models/meter.py` contains meters/readings, but no meter router is registered. `models/property_rule.py` and `services/property_knowledge_service.py` expose knowledge, not executable accounting rules. Lease/ownership APIs and `engine/mietkonto.py` supply reusable temporal/obligation foundations. |
| Jobs and lists | `workers/declaration_import.py` provides claims/heartbeats/attempt handling for its fixed pipeline. Generalize infrastructure via adapters. `repositories/base.py` and existing collection pages use whole lists; migrate consumers with server paging, not response-shape surprises. |
| Verification | `backend/tests/conftest.py` resets a disposable DB via migrations; `core/declaration_test_database.py` guards it. `frontend/package.json` has Vitest/lint/build, no E2E command. M-A must supply reproducible browser evidence without pretending Vitest proves integration. |

Migration baseline is `backend/migrations/versions/0028_declaration_pass_ledger.py`.
Allocate fresh revisions at implementation time after checking the actual head.
Never edit historical migrations to change deployed behavior. Preserve
[date conventions](adr/0006-valid-to-konvention.md),
[WEG Soll semantics](adr/0007-weg-abrechnungsspitze-und-wirtschaftsplan.md),
[money separation](adr/0010-vermoegenstrennung-zahlungen.md) and
[immutable evidence](adr/0003-gobd-unveraenderbarkeit-mechanik.md).

## 3. Delivery order and common completion contract

| Milestone / commit units | Dependencies and priority | Exit |
| --- | --- | --- |
| M-A / A1–A4 | First: fix evidence loss and false financial completeness before expanding automation | P0.1–P0.2; A01/A02/A06, relevant A31/A32 |
| M-B / B1–B4 | M-A; scope/grants and job primitives before broad intake or imported money | P1.1–P1.2; A03–A05 and security/rollback |
| M-C / C1–C4 | B1/B2 and M-A; C1 money/obligation foundation precedes C2 recurrence, C3 bank adapter and C4 SEPA | P2.1–P2.4; A07–A10; local file workflows can ship with live providers disabled |
| M-D / D1–D4 | D1 after M-B; D2 needs D1+C1; D3 needs D2; D4 needs D3 and specialist fixtures | P3.1–P3.4; A11–A17; external allocations before internal heating |
| M-E / E1–E5 | E1 after M-B; E2 after C1+E1; E3 after B1+E1; E4 after E3 and document/template foundation; E5 after E4 | P4.1–P4.5 plus F1 foundation; A18–A22/A25 |
| M-F / F1–F7 | F1 foundation starts after M-B; lifecycle F2 after C1; later rows have explicit dependencies below | P5.1–P5.8; A23–A30; finish F1 after E4 |
| M-G / G1–G2 | Instrumentation starts in M-B; final gate follows all supported M-C–M-F journeys and decisions | A31–A33, parity and professional release evidence |

This is dependency order, not a staffing or calendar estimate. Independent D1/E1/F1
work can follow M-B without waiting for live banking. C4 depends on C1's minimal
obligations, not the full F2/F3 lifecycle. P5.3/P4.4 form a staged dependency:
F1 first provides document registry/folders/versioned templates locally; E4 uses
them for delivery; F1 then adds inbound email. There is no cyclic release gate.
Do not silently drop provider/mobile/internal-calculator scope because it is gated;
record it as outstanding and limit release claims accordingly.

Each named commit unit may be split into schema/service and UI commits when both
are coherent behind a disabled flag. Exit means an executable journey, its named
failure path, permission checks and accepted evidence; schema or navigation alone
is insufficient. Commit only owned paths after staged-diff review. Keep one writer
per workspace. Obtain independent review at nontrivial integrity/security boundaries.

For every schema change: expand → evidence-based backfill → reconcile counts/hashes/
money → enforce constraints → switch readers/writers. Add tenant RLS, composite
scope FKs, uniqueness and indexes before enabling effects. Test with `app_rw`, not
only migration privileges. Unknown legacy scope/coverage becomes a recorded
exception; no heuristic reparenting or rewriting released records. Existing finals
remain readable with their original hash/schema. Migrations and feature rollback
must preserve new records; prefer disabling writers and forward repair over dropping
evidence. Rehearse rollback on a sanitized copy at each boundary (A32).

APIs use decimal strings, authenticated org, explicit scope, source/revision IDs,
stable result/status links and actionable errors. Financial writes need idempotency
plus business uniqueness; changed payload under a reused key returns 409. Replay
rechecks current access. Revisions reject stale actions with 409. Errors retain
user input. A source link cannot grant source access. Low-input claims must measure
typed fields/corrections and include excluded/failed items in denominators.

## 4. M-A: first executable milestone (P0)

Implement A1 → A2 → A3 → A4. Each is reviewable and commit-ready after its focused
checks. No external decision prevents local implementation of these bounded repairs.
V03 affects specialist release of period policy; unsupported cases remain blocked.

### A1 — Atomic upload-first cost creation (P0.1)

**Change:** extend existing `CostEntryCreate` with optional `source_document_id`.
Keep manual cost creation legal without a receipt; imported-source creation must
carry a valid source. Add `Idempotency-Key` handling for this operation via **new**
`backend/app/models/operation_receipt.py`, `repositories/operation_receipt_repo.py`
and `services/idempotency_service.py`. Key by org/actor/operation/key with request
digest and stable result; still enforce source uniqueness independently of key TTL.

1. Add failing API fixtures to `backend/tests/test_m2_gobd_belegfluss.py` for upload
   first, cost first, replay, conflicting key, simultaneous creates and foreign-org
   source. Use existing invoice fixtures and in-memory/fake store helpers.
2. Extend `CostEntryService.create` and `InvoiceDocumentRepository` with a locked,
   org-scoped source lookup. Validate active revision, unreleased/unclaimed source,
   destination property/category/vendor/mandate coherence before creating anything.
   Allocate draft, attach source, write audit and operation receipt in the request
   transaction. Same key/digest replays result; a different attempt claiming an
   already linked source returns its permitted conflict context, never another cost.
3. Migration: operation receipts with RLS; add `UNIQUE (organization_id, id)` to
   `cost_entry` before replacing the single-column source FK with
   `(organization_id, cost_entry_id) → cost_entry(organization_id, id)`.
   Check/reconcile foreign-org legacy links before validating that FK; nullable
   `cost_entry_id` must still allow an unattached upload. Add a unique active
   primary receipt per cost after checking legacy rows. The existing
   one-active-per-chain index is not one-active-per-cost. Record ambiguous legacy
   candidates in a reconciliation report; block enabling the constrained writer
   until resolved. Do not guess links from amount/name or mutate released originals.
4. `api/invoice_documents.py`, `services/invoice_document_service.py` must enforce
   the same invariant on cost-first upload/version replacement. Keep confirmed
   cost corrections and document version chains intact; never reassign a released
   source to a new unrelated cost. Migration 0008 already freezes `cost_entry_id`
   on released sources; preserve and test that trigger. Any additional constraint
   belongs in a new migration, never an edit to historical migration 0008.
5. Extend `frontend/src/api/types.ts`, `endpoints.ts` and
   `pages/InvoiceDocumentDetailPage.tsx` to send the source and retain a stable key
   across retry; show saved result and linked cost after reload. Update
   `CostEntryDetailPage.tsx` and `SettlementDetailPage.tsx` source navigation if
   needed, reusing authenticated content endpoints. Confirmation retains the hash
   recheck and atomic release/audit; hash failure rolls back all effects.

**Acceptance:** A01 upload → draft → confirm → account source access works with
exact uploaded bytes. Two concurrent attempts yield one cost/link. Read-only and
foreign-org actors cannot create or fetch it (A31). Cost-first/version/correction
fixtures continue to pass. No change to engine arithmetic. P1 later introduces
multi-cost allocations; P0 intentionally supports one primary receipt and cost.

**Checks:** focused M2 API/DB tests, tenant/RLS tests for new tables, storage-integrity
failure fixture; directly attempt a foreign-org source→cost link and prove the
composite FK rejects it independently of API lookup filtering. Also prove a null
link and a valid same-org link remain allowed. **New**
`frontend/src/pages/InvoiceDocumentDetailPage.test.tsx`
proves source/key submission, retained form and retry outcome. Rollback disables
new upload-first creation, keeps receipt/link reads and uniqueness; never restore
the broken writer while permitting imported cost confirmation. No provider blocker.

### A2 — Bounded ordinary-invoice OCR (P0.2)

**Files:** modify `backend/app/main.py`, `core/config.py`, `core/pdf_extraction.py`,
`core/extraction.py`, `core/einvoice.py`, `services/invoice_document_service.py`,
`api/invoice_documents.py`; add **new** `core/invoice_ocr.py`. Extend invoice list/
detail status UI and API types; keep declaration pipeline behavior untouched.

1. Before enabling OCR, test a real synthetic image-only invoice and a text/XML/
   embedded-XML control. Check configured OCR Python extras and Tesseract German
   language/Poppler availability; missing dependencies produce an actionable
   `ocr_unavailable` status. The root `Dockerfile` already includes OCR extras,
   Poppler and German Tesseract: verify the actual worker runtime rather than add
   a redundant dependency stack. No persistent installation is part of this plan task.
2. Store the original and durable document/status before heavy extraction. Separate
   upload persistence from extraction transactions; the current `get_db` transaction
   must finish before OCR begins. Implement a minimal durable invoice-processing
   attempt table/service in **new** `models/invoice_processing_attempt.py`,
   `services/invoice_processing_service.py` and `workers/invoice_extraction.py` with claim,
   timeout, fencing and retry; adapt it to B2's shared job runner later. Do not
   create an untracked in-process task or keep a DB transaction open during OCR.
3. Provisional D limits: existing 25 MiB upload limit; validate PDF structure and
   reject encrypted/corrupt/over-200-page files; invoice OCR at most 20 pages,
   200 dpi, one rendered page at a time, 10 s per page, 120 s total, one OCR process
   per worker and a bounded queue. Larger invoices remain stored and need review
   or a supported alternative, never truncated into a complete extraction. Bound
   image pixel expansion, terminate timed-out subprocesses and discard partial
   financial proposals unless explicitly labeled incomplete. Tune only with evidence.
4. Add extraction outcome/error code, processor version, source page and attempt
   provenance; distinguish unsupported/unavailable/timeout/needs-review/success.
   Preserve human edits on retry. Keep XML routing ahead of OCR; confidence cannot
   authorize financial confirmation. No document text in operational logs.

**Acceptance/checks:** A02 scan returns reviewable fields and original; XML bypasses
OCR; timeout/dependency absence/encrypted/oversize/worker restart have explicit
outcomes and no duplicate drafts. Extend `test_pdf_extraction.py`,
`test_m5_einvoice.py`, `test_upload_limits.py`; add **new**
`backend/tests/test_invoice_extraction_worker.py`. Run a real local OCR fixture as
well as fake-timeout tests before claiming OCR enabled. Frontend status/retry test
proves a failed extraction is not a completed cost. A01 regression evidence is
rerun where upload semantics change. Migration adds attempts/status without
rewriting released extraction payloads. Flag rollback stops new extraction claims,
keeps originals/attempt history visible and permits manual review.

### A3 — Unknown financial inputs block every relevant finalizer (P0.2)

**Files/data:** add **new** `backend/app/models/coverage_declaration.py` and
`services/financial_input_policy.py`; extend `models/settlement_run.py` only where
needed for a draft evidence reference. Prefer append-only scoped coverage records
over modifying historic finalized rows. Extend `api/settlements.py`,
`api/weg_settlements.py`, `api/economic_plans.py`, their three services,
`services/payment_service.py`, and `engine/snapshot.py` for evidence serialization.
Keep pure-engine numeric inputs unchanged; validate completeness before invoking
them. Extend the corresponding settlement/WEG/asset/economic-plan detail pages.

1. Define coverage as scope, subject/component, interval, source refs, state,
   amount if known, attestor/time and revision. Manual totals require a reason and
   coverage declaration; explicit zero requires the same evidence. Contractual
   advance 100 plus no receipts is unknown. An empty collection is not evidence.
2. Remove rental None→zero coercion. Require known amount and coverage for complete
   compute/finalize; allow saving an incomplete draft. If provisional cost-only
   preview is retained, suppress payable balance/final PDF and mark blockers.
   The simplest M-A implementation returns structured completeness errors for
   full compute/preview while preserving the draft editor.
3. Derived full-year NK totals require matching year and affirmed coverage of the
   relevant accounts/lease interval. Year-only legacy data cannot establish a
   custom subperiod; require evidenced manual replacement until C1 allocations.
   Manual replacement never adds to derived money. Paid-through migration
   assertions have a distinct origin and cannot generate booked cash.
4. Route rental finalize, WEG finalize, asset-report finalize and economic-plan
   finalize through the shared policy. Remove request defaults that hide omission:
   distinguish omitted paid-owner totals/reserve movements/bank balances/assets
   from explicit known zero or attested not-applicable. Plans require an approved
   budget-input declaration, not actual-payment coverage. Preserve WEG cost minus
   plan Soll; missing paid information must not be silently shown as zero arrears.
5. Recheck evidence revisions under lock at finalization; invalidate coverage when
   underlying payments reverse or relevant facts change. Freeze coverage/source
   metadata in new snapshots. Corrections require current evidence. Existing
   final snapshots are served unchanged; legacy drafts with numeric totals but no
   provenance require explicit confirmation, never a backfill to “known”.

**Acceptance/checks:** A06 fixture compares 1,200 known NK, unknown coverage and
evidenced zero. Only known alternatives finalize. Direct legacy endpoints cannot
bypass the policy. Missing WEG reserves/owner payments, asset inventory and plan
declarations fail with resolvable codes. Extend `test_m3_settlement.py`,
`test_m4_weg.py`, `test_m6_zahlungen.py`, snapshot tests and **new**
`backend/tests/test_financial_input_policy.py`; add detail-page tests for Missing
versus 0.00. Cover year boundary, stale declaration, reversal and explicit manual
replacement. Migration/backfill rehearsal preserves old hashes (A32). Rollback
keeps the finalization guard active; disable finalization rather than reverting
to a version that treats unknown as zero. V03 governs specialist period acceptance.

### A4 — Integrated repaired-baseline acceptance and handoff

Add **new** `docs/acceptance/m-a-repaired-baseline.md` with fixture setup, exact
steps/results, revision/toolchain and remaining limits. Use a local browser with
synthetic orgs; no reference account or live provider. Record upload-first text,
scan, XML and embedded-XML paths through confirmed cost and draft/final account
PDF, source-byte/hash access, failed save/retry and second-org denial. Browser
coverage is required even if initially a reproducible manual script; if automated,
add a bounded E2E harness explicitly rather than cite a nonexistent npm script.

Run the touched backend/frontend checks once after the integrated changes; retain
valid focused evidence and rerun only affected checks. A4 exits when A01/A02/A06
and relevant A31/A32 pass, all legacy bypasses are covered and independent review
has no unresolved integrity blocker. This is repaired baseline, not P1 intake,
full readiness, invoice conformance certification or parity.

**Concrete start for a later authorized implementation session:** check clean/tree
ownership and migration head, open A1's source files and add the upload-first API
regression. Use `backend/tests/conftest.py`'s guarded disposable DB only; never run
its reset against an ordinary development/customer database. With the local test
profile already configured and verified, focused commands are:

```sh
# From backend; existing approved test environment, USER excluded.
env -u USER .venv/bin/python -m pytest -q tests/test_m2_gobd_belegfluss.py
# From frontend; after the new A1 test exists.
env -u USER npm test -- src/pages/InvoiceDocumentDetailPage.test.tsx
env -u USER npm run lint
env -u USER npm run build
```

Check prerequisites using `infra/docker-compose.declaration-test.yml` and
`docs/operations/declaration-imports.md`; do not invent credentials. Before CLI
authentication or persistent setup, read the applicable CLI-session instructions.
Record a failing new regression first, then implement A1, inspect/stage only its
files and commit e.g. `fix: link uploaded invoice atomically to its cost draft`.
Continue A2/A3/A4 as separately verified commits; do not start P1 before M-A exit.

## 5. Subsequent commit units

Each unit below inherits section 3's migrations, permissions, idempotency, checks
and rollback contract. File families named **new** include model/repository/API/
service modules where required, with the same basename; names may be refined
without changing the interfaces or acceptance. Proposed tests live in
`backend/tests/` or alongside the corresponding frontend page. Every unit requires
one positive journey, one failure/retry path and negative scope access, in addition
to the decisive tests below. Open V gates block release of the affected behavior,
not unrelated local work.

### M-B — Assisted intake, scoped access and master import

**B1 — Accounting scopes, grants and targeted selectors.** Foundation for P1–P5;
after M-A. Extend `models/management_mandate.py`, `models/bank_account.py`,
`core/tenant.py`, `core/rbac.py`,
`repositories/base.py` and property/person/unit APIs. Add **new** `accounting_scope`
and `scope_grant` module families plus `services/authorization_service.py`.
Represent org/mandate/property/type/effective unit scope separately from user
presentation preferences. Existing `models/property_assignment.py` describes
physical/special-use assignments, not staff ACLs; do not repurpose it for grants.
Add capabilities for prepare/confirm/approve/send/export.
Extend account/payment composite coherence without reparenting immutable money;
use immutable sidecar associations for legacy booked rows that cannot be altered.
Unresolvable mixed-property history remains quarantined. Migrate proven simple
scopes and existing roles; require explicit scoped team grants before team rollout.
Cover search, details, downloads, jobs and bulk effects; recheck revocation.
Add cursor pages (50 default/200 max, deterministic sort+ID) and migrate
`PropertiesPage.tsx`, `PersonsPage.tsx`, selectors and `UsersPage.tsx` incrementally
without breaking old consumers. Tests: `test_rbac.py`, `test_tenant_isolation.py`,
`test_rls_coverage.py` plus **new** `test_scope_grants.py`, intra-org account mismatch
DB tests, revoked-job/download tests and selector paging. A31/A32, RF02/RF24.
V05 approval policy and V06 sensitive access remain explicit. Rollback pauses scoped
writers rather than widening access to the old org-only policy.

Local B1 acceptance (2026-10-02): the supported WEG/rental foundation and actual
activation/pause/resume journey are qualified through migration 0091. See
`acceptance/b1-activated-qualification.md` and `2026-10-02-b1-closure-status.md`.
V05/V06, unsupported SEV and production rollout remain separate release limits.
This does not accept B2–B4 or M-B as a whole.

**B2 — Shared originals, intake jobs and outbox.** P1.1 after B1/A2. Add **new**
`document_ref`, `intake_batch`, `intake_item`, `extraction_revision`, `field_evidence`,
`outbox_event` and `job_attempt` module families, `services/intake_service.py`,
`api/intake_batches.py`, `workers/workflow.py`; adapt A2 and existing
`services/property_document_service.py`, `services/mandate_document_service.py`,
`services/invoice_document_service.py` and declaration worker at their boundaries.
Typed unique registry FKs reference existing sources without duplicating bytes.
Logical children retain container/page ranges; no dangling polymorphic source IDs.
Implement the design's intake create/item/status contracts, per-item idempotency,
quarantine/MIME/malware/page limits, at-most-100 batch manifest and 202/status links.
Extend `core/uploads.py`'s explicit upload-route matcher and proxy limits for the
new per-item endpoint; test oversized bodies before multipart parsing as well as
the shared bounded file reader. A batch is multiple bounded requests, not one
100-file request that bypasses the existing 25 MiB cap.
PostgreSQL jobs/outbox use short scoped claims, leases/fencing, effect keys and
bounded attempts. In **new** `frontend/src/pages/InboxPage.tsx` and
`IntakeBatchPage.tsx`, preserve received/processing/review/confirmed/failed counts
across reconnect. Backfill registry adapters with uniqueness; reconcile interrupted
attempts before moving A2 to shared jobs. Rollback stops claims, leaves status and
source reads intact. A03/A25/A31/A32; RF09/RF10. Tests: mixed transfer failure, tab
close/reconnect, worker kill before/after commit, unauthorized original fetch and
malicious payload fixtures. Keep declaration-specific contracts/evals passing only
where shared mechanics change. No live provider needed; V06 gates disclosure.

Local B2 progress (2026-10-02): migration 0092 adds the verified typed original
registry and read-only metadata API. See `2026-10-02-document-registry-foundation.md`.
Migration 0093 now adds the locally verified backend upload/quarantine/inspection
path, private manifests, fenced attempts, explicit retry and transactional outbox
records; see `2026-10-03-intake-quarantine-delivery.md`. Migration 0094 adds private
inbox headers and live action availability; the inbox UI now supports bounded
uploads, interrupted-transfer recovery, reconnect and explicit retry. See
`2026-10-03-intake-inbox-delivery.md`. Migration 0095 adds immutable unreviewed
logical children bound to verified page ranges and the original audience; see
`2026-10-03-logical-document-foundation.md`. Migration 0096 adds immutable invoice
extraction revisions and explicit unlocated field evidence through the existing
A2 worker; see `2026-10-03-extraction-revision-foundation.md`. An optional serial
runner now adapts the existing inspection, invoice and declaration queues while
preserving their leases and reconciliation; see `2026-10-03-shared-workflow-runner.md`.
Migration 0097 adds fenced internal inspection-event delivery with exact attempt
provenance, whole-source candidate reuse and atomic completion; see
`2026-10-03-internal-outbox-delivery.md`. A bounded local logical-source processor
now produces page/quote and standalone XML element evidence; see
`2026-10-03-logical-intake-extractor.md`. Migration 0098 connects it to fenced
durable jobs, typed history, current source authority and explicit retry; see
`2026-10-03-durable-intake-extraction.md`. Migration 0099 and the source proposal
view connect durable stage counts, per-child history, evidence and explicit retry
to the inbox; see `2026-10-03-intake-proposal-view.md`. Actual process-death recovery
and a bounded Linux text/timeout probe add partial runtime evidence; see
`2026-10-03-intake-runtime-qualification.md`. Shared production-runtime activation
and real scanner qualification remain outstanding; this does not accept B2.
Reviewed partitions and contextual/financial acceptance belong to B3 below.

**B3 — Assignment, duplicates, splits and shared exceptions.** P1.1 after B2.
Add **new** `assignment_proposal`, `mapping_version`, `exception_case`,
`document_cost_link` / allocation-set models and services,
`api/intake_items.py`, `api/exceptions.py`, `services/assignment_service.py`,
`services/document_allocation_service.py`; extend cost confirmation through this
adapter. Add **new** `ExceptionDetailPage.tsx` and source/proposal review components.
Resolve stable IDs/address/context with reasons; hints cannot override contrary
evidence. Version learned mappings only after opt-in. Hash/business duplicates,
credit notes, line splits and replacements require explicit resolutions.
Deduplicate exceptions by scope/cause/source revision; advisory claims plus
optimistic concurrency preserve two clerks' edits. Balanced allocation-set approval
confirms reviewed split costs atomically, releases source once and prevents later
extra full-amount costs. Supporting evidence does not become a second primary
charge. Adapt A1's single-primary compatibility pointer/readers rather than dropping
its uniqueness safeguard prematurely. Cross-mandate original access requires every
destination grant; scoped extracts need reviewed disclosure. Migration backfills
one-cost allocation sets only from proven links, preserving released columns.
Rollback prevents new allocation acceptance, retains adopted allocations/corrections.
A03/A04, RF09/RF10/RF25; tests for cents conservation, credit sign, split crash,
duplicate race, altered invoice, ambiguous address, no cross-scope auto-assignment
and source→cost retries across entry points. V01/V03 block unsupported tax treatment.

B3 foundation progress: migration 0100 adds immutable reviewed visual-page
partitions with exact source binding, complete nonoverlapping coverage, live
authority, optimistic concurrency and retained revision history. See
`2026-10-03-reviewed-source-partitions.md`. The source UI now supports explicit
range creation and partition review with conflict/reconnect handling. Assignment,
exceptions, duplicate decisions and financial allocations remain outstanding; this does not
accept B3.

Migration 0101 adds manual context proposals pinned to exact partition and
extraction revisions, with current source/destination rights and immutable
conflict markers and a manual proposal/history interface; see
`2026-10-03-manual-assignment-proposals.md`. Automated identity resolution,
proposal review/adoption and shared exceptions remain open.

Migration 0102 adds immutable exception cases, exact proposal dependencies and versioned advisory workflow events. New evidence reopens attention; claim, release and snooze never resolve a blocker. Source-and-target authority is rechecked before historical replay. Backfill, rollback, concurrency, expiry, RLS and migration qualification are recorded in `2026-10-03-exception-workflow.md`. The exception list/detail UI now preserves unknown commands and links exact source evidence; browser and mobile qualification are recorded there. Typed resolution/adoption remains outstanding. This does not accept B3.

Migration 0103 records immutable human invoice field reviews on one exact current proposal/source basis, with explicit sign/date/decimal validation and preserved original assertions. It produces no financial or resolution effect. Qualification and remaining boundaries are recorded in `2026-10-03-invoice-field-reviews.md`; the explicit field-review UI and its retry/mobile/browser checks are included. Typed context resolution and balanced allocation/adoption remain outstanding.

Migration 0104 adds immutable manual context comparisons tied to the exact current invoice-field review and assignment proposal. Its typed exception event dispositions only the corresponding hint-conflict dependency; the shared case remains unresolved. Property-aware retained-snapshot access, snapshot concurrency, transaction rollback and migration qualification are recorded in `2026-10-03-context-decision-design.md`. Financial adoption and full B3 acceptance remain outstanding.

Migration 0105 records immutable single-scope allocation proposals with exact signed gross/VAT conservation, sealed line membership and category/version provenance. It pins one current context decision and does not create costs or release a source. The remaining adoption, duplicate, tax and cross-scope gates are recorded in `2026-10-03-allocation-proposal-design.md`. B3 remains incomplete.

Migration 0106 adds same-scope duplicate candidates and immutable oriented comparisons on exact field reviews, with audience intersection, latest-head discovery, signed pagination and historical retry. The side-by-side UI and backend/browser qualification are recorded in `2026-10-03-duplicate-comparison-design.md`. Results remain evidence only; typed duplicate exceptions, tax/eligibility, source publication and financial adoption remain outstanding. B3 remains incomplete.

Migration 0107 connects derived duplicate evidence to typed shared-case dependencies and automatic comparison observations. Full retained-source audience intersection and all-source prepare protect case reads/actions; comparisons remain evidence only and cases remain unresolved. Backend, migration, UI and browser qualification are recorded in `2026-10-03-duplicate-exception-integration.md`. Tax/eligibility, source publication, financial adoption and cross-scope allocation remain pending.

Migration 0108 adds immutable explicit treatment reviews for every exact allocation line, preserving signed tenant cents, deliberate distribution choices, unresolved values and server-derived unsupported/conflict markers. The UI requires new choices rather than inheriting category defaults and preserves authorized exact retries. Backend, migration, UI and browser receipts are recorded in `2026-10-03-allocation-treatment-review.md`. This grants no financial adoption, source publication or tax/legal eligibility; B3 remains incomplete.

Migration 0109 adds atomic confirmation of supported whole-original, single-scope reviewed allocations and a typed published source bridge. All member costs retain exact signed treatment; every source/retention reader follows sealed links. Private duplicate and transaction-bound integrity checks prevent duplicate charges and partial effects, while explicit original disclosure preserves separate private intake custody. Backend, UI, migration and browser qualification are recorded in `2026-10-03-allocation-adoption-design.md`. Cross-scope/page disclosure, grouped corrections/replacements, automated identity/opt-in mappings and typed overall case completion remain open; this does not accept B3 or M-B as a whole.

Migration 0110 adds explicit exception completion backed by immutable confirmed outcomes and a bounded whole-component evaluator. Every retained dependency and current duplicate pair receives an explicit disposition; new relevant evidence reopens effective attention even without a new case event. The UI distinguishes historical receipts from current applicability, reauthorizes retained retries and requires explicit review. Qualification is recorded in `2026-10-03-exception-completion-design.md`. Cross-scope/page disclosure, grouped corrections/replacements, automated identity resolution and opt-in versioned mappings remain open; this does not accept B3 or M-B as a whole.

Migration 0111 exposes exact authorized source pages for bounded read-only context suggestions. Labelled property identifiers, meter numbers and complete service addresses are compared only with authorized master data; alternative scopes and unmatched source assertions remain visible. The UI requires explicit copying and separate manual submission, preserving explanations and unknown requests. Qualification is recorded in `2026-10-03-context-candidate-design.md`. Automatic assignment, opt-in versioned mappings and the other remaining B3 transitions are not accepted by this increment.

Migration 0112 adds explicitly opted-in, versioned property-ID/meter-number hints backed by a current adopted source, exact page proof, an explicit service interval and reuse consent. Private origin evidence is separated from sanitized descriptors; all retained target rights are checked, revocation remains possible after uploader departure, and historical retries never change the current head. Reuse annotates manual candidates without financial or resolution effects. Backend, UI, concurrent master-data, migration and actual browser qualification are recorded in `2026-10-03-opt-in-context-mapping-design.md`. Automatic draft attachment, customer/contract aliases, cross-scope disclosure and grouped corrections/replacements remain open; this does not accept B3 or M-B as a whole.

Migration 0113 adds a read-only, complete correction-readiness preview for a new whole-original allocation in the same property and scope. It compares all old/new signed lines, distinguishes actual cost consumption, possible computation changes, coverage renewal and explicit source-evidence review, and blocks hidden or unsupported dependencies without partial results. Its private discovery, exact dependency digest, actual snapshot compatibility and browser qualification are recorded in `2026-10-03-allocation-correction-design.md`. Atomic replacement and evidence-based follow-up completion remain open; this does not accept B3.

**B4 — Onboarding/master migration and portfolio.** P1.2 after B3. Add **new**
`master_import` / `setup_session` module families, `services/master_import_service.py`,
`api/master_imports.py`, `MasterImportPage.tsx`; extend property/unit/person/lease/
ownership APIs and `PropertyDetailPage.tsx`, `UnitDetailPage.tsx`, `App.tsx`/shell.
CSV/spreadsheet column maps and document candidates produce create/update/skip
diffs with external IDs, provenance, effective dates and dependency preview.
Accept one property transaction at a time; preserve rejected rows and resumability.
Do not merge people by name. Existing contacts without email remain valid.
Landlord guided setup and professional portfolio filters reuse scoped APIs; route
back/deep links keep context. Backfill import keys for imported records only;
pre-acceptance discard is safe, post-acceptance rollback is audited correction.
A05/A28, RF02/RF03/RF05/RF19/RF28; tests include identical reimport, overlapping
history, partial failure, master-change invalidation and bounded queries. No external
blocker; ownership/legal inference is not automatically accepted (V01).

### M-C — Reconciled money

**C1 — File imports, obligations and reviewed matching.** P2.1 after B1/B2 and M-A;
B3 supplies shared exceptions. Extend `models/payment.py`, `models/bank_account.py`,
`services/payment_service.py`, `repositories/payment_repo.py`, `engine/mietkonto.py`
through adapters; add **new** `bank_import`, `bank_transaction`, `obligation`,
`transaction_allocation`, `payment_reconciliation_link` module families and
`api/bank_imports.py`, `api/transaction_allocations.py`. Add **new**
`BankImportPage.tsx`, `TransactionMatchingPage.tsx`; adapt `MietkontoPage.tsx`,
`PaymentsPage.tsx`, `OpenItemsPage.tsx` and `BankAccountsPage.tsx`.
Support declared CSV mappings and a fixture-backed CAMT profile; pin exact supported
versions before enabling each parser. Preview encoding/separator/date/decimal,
unknown columns, row count, debit/credit totals, account/currency and coverage.
Rejected/empty imports create no transactions. Raw facts stay immutable; reliable
provider IDs precede fingerprints, identical real transfers remain distinguishable.
Create stable component/period obligations from existing leases/final economic
plans; keep due, booking, value and credited-obligation dates distinct.
Confirm allocations into existing booked payments once, with remainder and reversal
links. Existing manually booked payments are reconciled, not booked again; match
account/currency/direction/amount/component/period and forbid a second claim.
Paid-through assertions/opening balances remain separate from bank proof. Migrate
year-only data only for supported full-year coverage; C1 extends A3 to exact periods.
Rollback pauses imports/matching, preserving raw data, links and booked money.
A07/A06/A28; RF07/RF08/RF11/RF12/RF23. **New** `test_bank_imports.py` and
`test_transaction_allocations.py` cover 650→600 base+50 NK, 750→700+50 unapplied,
combined flat/garage and months, partial refund/return, concurrent manual adoption,
overlapping imports and balance controls. V03 gates period policy; file parsing
does not need a live bank provider.

**C2 — Recurrence, loans and cashflow.** P2.3 after C1. Add **new**
`recurring_finance`, `loan_schedule` module families, `services/cashflow_service.py`,
`api/finance_forecasts.py` and `FinanceForecastPage.tsx`; extend `DashboardPage.tsx`.
Version approved templates and source loan schedules; stable period keys generate
obligations/cost drafts only. Separate principal/interest/fees/reserve transfers,
forecast/Soll/booked cash and owner scope. Schedule amendments show effective-date
diffs and require acceptance; no retrospective rewrite. Additive migration; cancel
future generation on rollback, preserve existing drafts and approved schedule
versions. A09; RF01/RF23, public baseline for unexercised loan execution. Test month
boundaries, schedule rerun, variable terms, principal exclusion and dashboard
reconciliation. V01/V03 gate financial/tax interpretation.

**C3 — Bank provider connection.** P2.2 after C1 and B2. Add **new**
`integrations/banking.py`, `bank_connection` modules, `api/bank_connections.py`,
webhook ingress and `BankConnectionPage.tsx`. Adapter operations cover consent,
account discovery, cursor fetch, status/revoke and identity reconciliation; provider
credentials use supported secret storage, never browser sessions. Stored account
mapping determines org/scope; signed/replay-deduplicated webhooks never trust payload
org IDs. Pending→booked ambiguity creates a review case, not fuzzy suppression.
Migration adds connection/cursor/receipt history; rollback revokes or pauses future
sync and retains transactions, marking coverage gaps. A08; RF11/RF23 observation
only. Fake tests cover reconnection, expiry, out-of-order webhook, duplicate fetch
and account remapping denial. V04/V06 require chosen provider, contract, institution
coverage, sandbox evidence and separately authorized live activation.

**C4 — SEPA export and reconciliation.** P2.4 after C1's obligation keys; independent
of C3 and full rental lifecycle. Add **new** `sepa_mandate`, `sepa_batch` modules,
`services/sepa_service.py`, `api/sepa_exports.py`, `SepaExportPage.tsx`. Capture
creditor/mandate revision/sequence, eligible due items, immutable batch instruction
IDs, preview totals, approval and artifact hash. Repeated download returns the same
artifact; amended/returned collection is linked explicitly. XML release never
marks an obligation paid. Migrate only evidenced mandates; rollback stops new
release, keeps existing instructions reconcilable. A10; RF27 observed controls.
Test bank-supported XML schema/profile, duplicate export, revoked mandate,
return/retry and money invariants. V03/V04 and specialist mandate/pre-notification
requirements block live release, not fake/schema-backed implementation.

### M-D — Property accounts

**D1 — Meter history and external allocations.** P3.1 after M-B. Extend existing
`models/meter.py`; add **new** `repositories/meter_repo.py`, `services/meter_service.py`,
`api/meters.py`, `api/meter_readings.py`, `external_allocation_batch` module family,
`services/external_allocation_service.py`, `MetersPage.tsx` and
`ExternalAllocationsPage.tsx`. Persist units/multipliers/type defaults/topology,
validity/replacement/rollover, recorder/date/photo source and measured/estimated
method approval. First reading 100 m³ supplies no fabricated usage. A reading
revision preserves originals; missing tenant-change boundaries block affected
methods. Provider/WEG statements import period/unit/component totals and source
keys; 60+40 against 100 accepts, 60+50 blocks. Enforce one source mode per component
(external vs internal) and source-key duplicate protection. Backfill existing
readings as unverified where units/provenance are absent; rollback keeps evidence
and stops new acceptance. A11; RF13 plus RF07 unverified method claims.
Tests include 100→160, replacement, rollover, estimate approval, duplicate import,
photos and external/internal collision. V01/V02 block unsupported methods, not
supported external reconciliation.

**D2 — Readiness, source dependencies and executable rules.** P3.2 after C1/D1/B3.
Extend `models/property_rule.py` and property-knowledge boundaries; add **new**
`executable_rule_version`, `source_dependency`, `expected_item` module families,
`services/rule_compiler.py`, `services/readiness_service.py`,
`api/accounting_rules.py`, `api/readiness.py`, `ReadinessPage.tsx` and
`AccountingRulesPage.tsx`. Generalize A3 coverage, keeping legacy finalizers on the
same policy. Use a typed operation vocabulary, source hashes, dates, priority,
parameters, fixture evidence and approval; no free-text/model-generated execution.
Approved knowledge defaults to not evaluated, never activated. Competing rules
block; mid-period activation slices inputs. Readiness covers scope/history, costs,
expected-document applicability attestation, account/advance coverage, readings,
rules, reconciliations and anomalies with affected outputs/resolution links.
Expected items are suggestions; absence alone proves neither completeness nor a
debt. Indexed reverse dependencies stale drafts and create correction cases for
finals. Migration adds versioned dependencies/coverage, never rewrites final
manifests. Rollback disables future activation and keeps historical versions.
A06/A12/A31/A32; RF04/RF14/RF15/RF25. Test knowledge-approved-but-inactive, activated
rule effect, conflict, missing coverage, scope denial and multi-draft invalidation.
V01/V03/V05 gate interpretation, periods and team approvals.

**D3 — Rental/WEG/SEV settlement packages and corrections.** P3.3 after D2.
Add **new** `settlement_batch`, `settlement_package_revision`, `artifact` modules,
`services/settlement_package_service.py`, `api/settlement_batches.py`,
`api/settlement_packages.py`, `AccountsBatchPage.tsx`, `SettlementPackagePage.tsx`.
Adapt `settlement_service.py`, `weg_settlement_service.py`,
`economic_plan_service.py`, their PDF services and existing account/detail pages.
Freeze permitted selection and collect per property/sphere/period; one package
contains all dependent recipient runs and engine-ready manifest. Reuse pure
`engine/settlement.py`, `weg.py`, `wirtschaftsplan.py`, `vermoegensbericht.py` and
snapshot/canonical/rounding utilities. Source→category→unit explanations include
excluded costs, coverage, days, rounding, plan Soll and separate payment arrears.
SEV imports eligible external WEG components once, avoids underlying-invoice
duplication and cannot grant access to the WEG sphere. Reserves/deposits stay out
of tenant operating costs. Asset reports reconcile bank/reserve/opening/closing
and receivable/payable evidence; incomplete full-WEG scope offers bounded SEV.
Prepare/review/approve uses exact revision and per-mandate V05 policy. Render
candidate PDFs outside transactions, then revalidate/serialize relevant writes
on a property/sphere revision counter and atomically commit all runs, manifests,
approval, hashes and artifact refs. Failure publishes nothing; staged orphan files
are inaccessible and safely retried/cleaned. Legacy finalize routes call this
policy; no bypass. Correction creates new runs/deltas while original PDF/hash and
delivery history remain frozen. Additive migration bridges new drafts; old
snapshots remain version-readable. Rollback pauses new package finalization and
keeps accepted revisions/files available.
A13–A16/A06/A32; RF04/RF14–RF16. Tests: 1,200 water over two 50 m² units gives 600,
302.47 tenant and 297.53 vacancy; advances 500 gives first-tenant balance 100.
Reference supplement: 100 water ×92/365 gives 25.21 tenant and 74.79 vacancy.
Add leap-year/date-boundary tests. WEG 2,400 costs/1,000 Soll each gives 200 each,
with second-owner 200 arrears separately when only 800 paid. SEV eligible/noneligible
and duplicate-source fixture; mixed scope, co-ownership and owner change;
stale finalization 409, crash/render failure, old PDF/hash unchanged and replay.
V01/V03/V05 must close relevant specialist/team release gates.

**D4 — Supported internal heating and CO2.** P3.4 after D1–D3 and approved V02
fixtures. Add **new** `engine/heating.py`, `engine/co2.py`, supported-method schemas,
`services/heating_input_service.py` and method/evidence controls in the account
workspace. Pin jurisdiction/effective-date applicability, exceptions and complete
required inputs before implementing a method. Explicitly select internal method
or accepted provider components; no double counting or generic day-weight fallback.
Version method/input/output in the manifest; migration adds method metadata,
rollback disables new calculation but retains old replay code and results.
A17; no authenticated proof of reference calculator accuracy. Specialist-labeled
golden cases include user change, missing readings/consumption, exceptions and
CO2 tenant/owner split; unsupported cases block or use approved external inputs.
V02 is a hard calculator release blocker; this plan invents no formula or legal rule.

### M-E — Daily collaboration and governance

**E1 — Cases, tasks and deadlines.** P4.1 after M-B. Add **new** `case`, `task`,
`case_template` module families, `services/case_service.py`, `api/cases.py`,
`CasesPage.tsx`, `CaseDetailPage.tsx`; adapt `fristen_service.py` and
`FristenmonitorPage.tsx` to propose stable source-key tasks. Keep business exceptions
linked to cases rather than replacing their blocking policy. Required tasks plus
recorded outcome permit closure; optional unfinished tasks do not block by default.
Manual reopening and template-revision adoption retain history, assignees and due
dates. Snoozing/closing cannot clear accounting checks. Additive migration; rollback
stops generation but preserves cases/logs and source deadlines. A18; RF01/RF17/RF23.
Test one deadline→one task, required/optional completion, outcome, reopen, template
change and unrelated financial blocker. No provider blocker.

**E2 — Dunning preparation.** P4.2 after C1/E1. Extend
`services/dunning_service.py`, `dunning_pdf.py`, `api/dunning.py`,
`models/dunning_notice.py`, `DunningNoticesPage.tsx`, `DunningNoticeDetailPage.tsx`;
add **new** dispute/policy-version and notice-input-revision records. Prepare from
reconciled open items, freeze stage/template/balance and identify disputed items.
E2 can save notices locally; connect release to E4 when available. Recheck balance
and permissions immediately before releasing to delivery; a new payment invalidates
the draft. Append evidence to new notices; preserve old ones and retain disputes
on rollback. A19; public baseline, RF08/RF17 context only. Extend `test_m7_dunning.py`
for new payment/stale notice, dispute pause, stage repetition and no implicit send.
V01/V09 require approved fee/date/process policy; no invented statutory defaults.

**E3 — Tenant/owner portal access.** P4.3 after B1/E1; F1 registry foundations
provide publishable document refs. Add **new** `portal_identity`, `portal_grant`,
`published_document` module families, `services/portal_service.py`, `api/portal.py`,
separate portal frontend shell/pages. Invite a named relationship with audience
preflight, separate identity/verification and explicit grants. Staff role never
becomes a portal grant. Explain eligibility exclusions before invitation; missing
email cannot prevent ordinary contact creation and cannot silently select another
channel. Resident issue/readings enter E1/D1 review; shared documents expose only
approved scoped artifacts. Migration creates no grants from historical relationships
automatically. Rollback revokes/suspends future portal access, keeps required history.
A20/A31; RF05/RF20/RF21/RF24. Test household enumeration, forged download IDs,
owner/tenant segregation, revoked/former occupant policy and queued-job revocation.
V04/V06 gate invitation provider/retention/access production policy; tenant UI
reference and owner scope remain bounded R/O versus D evidence.

**E4 — Messages, announcements and channel delivery.** P4.4 after E3/B2/F1 local
templates. Add **new** `message`, `delivery` module families,
`services/delivery_service.py`, `integrations/delivery.py`, `api/messages.py`,
`api/deliveries.py`, `MessagesPage.tsx`, `DeliveryDetailPage.tsx`. Preview each
recipient/channel, merged content, template/artifact revision and attachment
disclosure before send approval. Separate accepted/delivered/read/failed/unknown.
Recheck grants/recipient eligibility at effect time. Provider idempotency/status
lookup handles unknown outcomes; absent both, hold for reconciliation, never blind
resend. B2 outbox commits with approved intent. Add migrations for immutable payload
and recipient histories; rollback stops unsent jobs, never erases sent evidence.
A21; RF20/RF21/RF23/RF29. Fake email/portal/post tests cover timeout, callback replay,
revoked audience, no-email recipient and delivery failure recovery. V04/V06 gate
live provider/channel evidence and processor/retention decisions.

**E5 — Meetings and immutable resolution history.** P4.5 after E4. Extend
`models/owners_meeting.py`, `agenda_item.py`, `resolution.py`,
`resolution_register_entry.py`, `services/meeting_service.py`, `meeting_pdf.py`,
`api/meetings.py`, `MeetingsPage.tsx`, `MeetingDetailPage.tsx`,
`ResolutionRegisterPage.tsx`; add **new** attendance/proxy/vote records as needed.
Build agenda/document → invitation → attendance/proxy → supported voting → minutes
→ register amendment journey. A resolution can propose a D2 rule interpretation,
never activate calculation automatically. Additive history migration; rollback
keeps numbered originals and append-only amendments. A22; authenticated voting
coverage absent. Extend `test_m8_meetings.py` with approved voting/proxy fixtures,
invalid proxy/duplicate vote, register sequence, invitation audience and correction
PDF. V09/V01 gate supported voting/circular-resolution methods and interpretation.

### M-F — Remaining parity modules

**F1 — Document organization, stationery and inbound email (staged).** P5.3:
local foundation after B2/B3, inbound email after E4. Extend
`services/property_document_service.py`, `services/mandate_document_service.py`,
existing document APIs and `components/knowledge/PropertyDocuments.tsx`; add **new**
`document_folder`, `template_version`, `intake_address` module families,
`api/document_folders.py`, `api/templates.py`, `DocumentsPage.tsx`, `TemplatesPage.tsx`.
Folders/tags are virtual organizational metadata, never scope or ledger ownership.
Freeze stationery/template versions in output artifacts. Later, controlled intake
address tokens feed B2 quarantine with attachment/message effect keys; sender address
is not mandate authority, and forwarding/resent attachments cannot double book.
Backfill virtual folder refs without touching released sources; rollback pauses
email ingestion, preserves registry/folders/template reads. A25; RF09/RF10/RF22/
RF29/RF30. Tests cover inbox/property/cost repeat, folder move preserving links,
spoofed email, attachment retry and template-version rendering. V04/V06 gate live
mail intake and privacy. Local templates need neither E4 nor a provider.

**F2 — Leases, co-tenants, deposits and handover data.** P5.1 after B4/C1/D1;
F1 supports documents. Extend `models/lease_agreement.py`, `api/leases.py`,
`services/payment_service.py`, `UnitDetailPage.tsx`, `PersonDetailPage.tsx`; add
**new** `lease_party`, `rent_schedule_version`, `deposit_movement`, `handover` modules,
`services/lease_lifecycle_service.py`, `LeaseLifecyclePage.tsx`. Explicit draft→
activation with parties/components/dates, base+NK obligations and flat/garage
grouping; prefill from source with contract-only vs master effective-date diff.
Deposit account/movements and disposition stay separate from rent and costs.
Termination/renewal/move-out preserves history and links keys/condition/readings
to D1; signatures attach through F4. Backfill the single current tenant as one
party only from evidence; obligation keys preserve C1 identity during schedule
upgrade. Rollback prevents new lifecycle transitions, never drops parties or
duplicates old obligations. A23/A05/A26; RF03/RF05–RF07/RF19.
Tests: co-tenants one obligation, future activation, overlap rejection, partial
first/last period, termination, deposit return/deductions and historical parties.
V01/V09 gate legal lifecycle terms; draft contract must never activate a second lease.

**F3 — Rent/advance adjustment, steps and index proposals.** P5.2 after F2/D3.
Add **new** `rent_change_proposal`, `index_observation` modules,
`services/rent_adjustment_service.py`, `api/rent_changes.py`,
`RentAdjustmentPage.tsx`; reuse F1 templates/E4 delivery where notice is required.
Version clause/index series/base/input date, calculation basis and covered period.
Show effect on future schedule and uncertainty; actual activation requires approved
effective date and necessary notice/consent evidence. Settlement advance suggestion
is separate from finalizing the account; show costs/months/coverage before adoption.
No guessed current index or automatic legal effect. Additive proposal migration;
rollback cancels future proposals, leaves activated schedules and old obligations
intact. A24; RF06/RF07/RF16. Test rejected/late notice, corrected index, step retry,
prior obligations unchanged, and 100-water reference fixture with explicitly
reviewed advance basis (do not blindly adopt the observed 8.33). V09/V01 gate process;
V04 applies if a licensed external index source is chosen.

**F4 — Contract generation, signing and handover outputs.** P5.4 after F1/F2/E4.
Add **new** `contract_draft`, `signature_request` module families,
`services/contract_service.py`, `integrations/signing.py`, `api/contracts.py`,
`ContractsPage.tsx`, `ContractReviewPage.tsx`; reuse handover records for rendered
protocols. Merge approved data into versioned templates, display required missing
fields before long review, and explicitly choose contract-only or canonical update.
Freeze exact terms/parties/artifact hash per signing request; changed terms invalidate
the request. Verified callback/status reconciles partial signers and signed copies;
download failure is visible/retryable. No purchase/sign/send implicit in draft save.
Migration stores revisions, never activates leases on backfill. Rollback stops
new requests and keeps signed hashes/evidence. A26/A23/A05; RF18/RF19/RF29.
Test 2-room document-only change leaves unit unchanged; different contract start
leaves active lease intact; missing required data, partial signatures, stale callback
and callback replay. V04/V06/V07/V09 gate provider/signature level/reference scope
and legal templates. Local generation and fake signing can proceed first.

**F5 — Finance reports, DATEV, EÜR and Anlage V.** P5.5 after C1/C2/F1, with D3
for account-based outputs. Add **new** `export_profile`, `export_job` modules,
`services/finance_export_service.py`, versioned `exports/` adapters,
`api/exports.py`, `FinanceExportsPage.tsx`. Map accounting basis, chart/account/tax
codes/effective dates per sphere; preview unmapped lines and missing receipts.
Produce immutable reconciled CSV/ZIP/PDF/XLSX as applicable with artifact receipt,
manifest and stable repeat download; corrections create a new export revision.
Reports project the existing ledger, never create a second ledger or consolidate
mandate cash. Supported VAT profiles must be explicit; RF26's reference limitation
does not authorize an incorrect export. Migration adds versioned mappings/artifacts;
rollback stops releases, preserves accepted outputs. A27; RF25/RF26/RF31.
Test mapped/unmapped VAT, missing receipt, totals, separate mandates, repeat export,
failure/retry and target importer/schema validation. Adviser-approved EÜR/Anlage V
fixtures and target DATEV acceptance are V03/V01 blockers, not replaced by CSV tests.

**F6 — General export/restore and account/security settings.** P5.6 after B1/B4/B2;
provider-revocation controls after the corresponding adapters. Extend `api/users.py`,
`api/auth.py`, `services/dsgvo_service.py`, `UsersPage.tsx`,
`frontend/src/auth/AuthContext.tsx`; add
**new** `services/data_transfer_service.py`, `api/data_transfers.py`, `SettingsPage.tsx`.
Preserve privacy export semantics; add a distinct scoped migration bundle containing
ID relationships, source hashes and document manifest plus import dry-run/diff.
Cover membership/grants, security/session controls, notification preferences,
integration disconnect and job/download revocation. Authentication features such
as 2FA require a separately specified protocol/acceptance before claiming RF22
coverage; observed settings alone do not prove behavior. Additive preference/
export-schema versions; restore to isolated fixtures, never blanket-delete accepted
records to undo an import. Rollback retains granted-policy enforcement and old
bundle readers. A28/A31; RF12/RF22–RF24/RF28. Test round-trip links/hashes,
hidden-mandate omission, revoked sessions/jobs/downloads and corrupted bundle.
V06/V07 gate retention/security feature matrix and settings parity.

**F7a — Listings.** P5.7 after F2/F1/B2. Add **new** `listing` modules,
`integrations/listings.py`, `api/listings.py`, `ListingsPage.tsx`: vacant unit →
approved public fields/photos → disclosure preview → explicit publish/update/
withdraw → reconciled status. Never include private tenant/financial/source metadata.
Migration preserves version/provider IDs; rollback stops future publication while
allowing an authorized withdrawal and status reconciliation. A29; RF03/RF30.
Fake tests cover approved-field allowlist, stale preview, uncertain retry and
withdrawal. V04/V06/V07 require provider license, sandbox and exact publishing scope.

**F7b — Mobile and push.** P5.8: responsive acceptance accompanies B2/D1/E1/E4;
companion-client decision after V07. Extend shared responsive shell/pages and add
**new** push subscription/delivery modules plus `integrations/push.py` only after
client architecture selection. Test small-screen upload/camera, meter entry, case
and message journeys with keyboard/screen-reader focus, interrupted transfer and
reauthentication. Do not promise offline writes without a conflict/security design.
Notifications use non-sensitive payloads and access rechecks; revoked subscriptions
cannot expose finance/document content on lock screens. Additive subscription
migration; rollback cancels pending push and retains ordinary web workflows.
A30; no authenticated mobile coverage. V04/V06/V07 gate provider, privacy and
native-vs-PWA/companion scope; responsive web alone cannot close companion parity.

### M-G — Professional capacity and release evidence

**G1 — Performance, fairness and recovery.** Cross-cutting from B2; final measure
after supported M-C–M-F flows. Extend scoped repository indexes/pagination, job
quotas, artifact streaming and frontend lazy loading; add **new**
`backend/tests/performance/`, `docs/acceptance/professional-capacity.md` and a bounded
sanitized fixture generator. Review `scripts/test-compose-release-restore.sh` and
`infra/docker-compose.declaration-test.yml` before composing isolated recovery
tests; do not run production deployment scripts. Use 500 properties/10,000 units,
five years/1 million documents/2 million transactions, 20 active staff plus workers,
and two adversarial isolation orgs. Preserve design budgets: API page p95 <500 ms,
usable review p95 <2 s, 100 text invoices <2 min/100 five-page scans <10 min,
account collection+calculation <15 min and PDFs <30 min on recorded hardware.
Worker kill/restart at stage boundaries must preserve exactly-once effects;
fairness prevents one org exhausting OCR/render slots. Restore DB and blobs,
validate hashes/control totals and safe outbox replay. Provisional RPO ≤15 min,
RTO ≤4 h require measured V08 approval. Migrations/index rollout benchmark on a
copy; rollback removes new workload/flags, preserves evidence. A31–A33; unit tests
cannot establish these capacity/recovery claims.

**G2 — Quality, usability and parity sign-off.** Add **new**
`docs/acceptance/parity-register.md` with one entry per P-row and linked A fixtures,
RF depth, R/O/D/V classification, supported/excluded cases, implementation commit,
check environment/result and responsible approver. All rows start planned; no
historical passing suite marks a new capability delivered. Evaluate ≥200 sanitized
mixed documents with class/eligibility denominators, first-run versus known context,
wrong-scope assignments, corrections and typed fields. Known supported invoice
target: median zero typed fields, ≥80% prepared without correction, explicit
financial review. Zero wrong cross-scope assignments in release fixtures is
mandatory; ≥99.5% precision needs a larger labeled set and uncertainty reporting,
not a claim inferred from 200 items. Observe ≥5 landlords and ≥5 professional
operators; proposed target ≥50% fewer typed fields with seeded errors still found.
Complete account fixtures need selection/review only; incomplete ones expose every
seeded blocker. Independent accounting/security review, V sign-offs and runnable
row-level evidence decide release. No schema migration; rollback withdraws the
unsupported claim/feature flag, preserving records. V01–V09 and all A rows apply;
untested reference groups remain publicly documented coverage, never full parity.

## 6. Acceptance and reference traceability

Primary A ownership below expands the design's grouped scenarios into executable
units. The full design scenario and reference review acceptance deltas remain
normative; this table does not narrow them. A31 security and A32 migration apply to
every relevant unit, not only the listed primary owners. RF evidence informs tests;
it is never evidence that our implementation passed.

| A ID | Primary unit / package | Decisive fixture or failure | RF evidence (where present) |
| --- | --- | --- | --- |
| A01 | A1/A4, P0.1 | Both upload orders, one linked/released source and cost, retry | RF09/RF10 (receipt save unverified) |
| A02 | A2/A4, P0.2 | Text/scan/XML/embedded XML, OCR timeout/unavailable | RF09 (OCR cancelled) |
| A03 | B2/B3, P1.1 | Mixed batch, duplicates, context ambiguity, durable retry | RF09/RF10 |
| A04 | B3, P1.1 | Split/credit conservation, proposal revision and clerk race | RF09/RF25 controls only |
| A05 | B4/F2/F4, P1.2 | Reimport identities, effective diff, contract-only edit | RF02/RF03/RF05/RF19/RF28 |
| A06 | A3/D2/D3, P0.2/P3.2/P3.3 | Known 1,200 vs unknown vs verified zero; no legacy bypass | RF07/RF08/RF14/RF15 |
| A07 | C1, P2.1 | Partial/over/combined money, repeat import, manual adoption | RF07/RF08/RF11/RF12 |
| A08 | C3, P2.2 | Consent expiry/reconnect, webhook and pending/booked identity | RF11/RF23 controls only |
| A09 | C2, P2.3 | Recurrence retry, loan components, forecast versus cash | RF01; loan execution unverified |
| A10 | C4, P2.4 | XML/profile, stable instruction, return; export is not paid | RF27 controls only |
| A11 | D1, P3.1 | First reading/100→160, replacement, provider 60+40 vs 60+50 | RF13 |
| A12 | D2, P3.2 | Inactive knowledge, activated version, unsupported/conflict | RF04/RF25 controls only |
| A13 | D3, P3.3 | Rental vacancy 600/302.47/297.53, leap year, 25.21/74.79 | RF14–RF16 draft |
| A14 | D3, P3.3 | WEG 200 difference each, separate 200 arrears, owner changes | RF04 controls; professional WEG unverified |
| A15 | D3, P3.3 | SEV eligible components once, mixed-scope denial | No exercised reference scenario |
| A16 | D3, P3.3 | Stale 409, atomic PDFs, correction leaves old hash/history | RF16 draft only |
| A17 | D4, P3.4 | Specialist heating/CO2 cases, unsupported method blocks | RF07 notice does not validate method |
| A18 | E1, P4.1 | Required/optional tasks, outcome/reopen, blocker remains | RF01/RF17 |
| A19 | E2/E4, P4.2 | Payment after notice review stops release, dispute pause | No completed reference dunning |
| A20 | E3, P4.3 | Audience eligibility, household denial, former resident | RF05/RF21/RF24 boundary |
| A21 | E4, P4.4 | No-email eligibility, fake timeout/replay, unknown delivery | RF20/RF21/RF23/RF29 |
| A22 | E5, P4.5 | Voting/proxy/minutes and immutable register amendment | No authenticated meeting acceptance |
| A23 | F2/F4, P5.1 | One co-tenant obligation, deposit, termination/handover | RF03/RF05/RF06/RF07/RF19 |
| A24 | F3, P5.2 | Index/step and advance basis, explicit effective adoption | RF06/RF07/RF16 |
| A25 | F1/B2/B3, P5.3 | Folder moves, resend, spoofing, frozen stationery | RF09/RF10/RF22/RF29/RF30 |
| A26 | F4, P5.4 | Prefill scope, missing data, terms/signature revision mismatch | RF18/RF19 draft; RF29 |
| A27 | F5, P5.5 | VAT profile block, reconciled artifacts/importer, failed download | RF25/RF26/RF31 unverified delivery |
| A28 | F6/B4/C1, P5.6 | Scoped export/restore, CSV preview and stable outcome | RF12/RF22/RF23/RF28 |
| A29 | F7a, P5.7 | Approved public fields only, reconciled withdraw/retry | RF03/RF30 boundary |
| A30 | F7b, P5.8 | Camera/keyboard journeys, revoked and private push | Mobile reference unverified |
| A31 | B1/E3/F6/G1, all | Two orgs + two grants within org, every access/effect surface | RF24 proves no rights matrix |
| A32 | Every migration; G1 | Hash/money/history preservation, unknown backfill, rollback | Target migration policy, not O |
| A33 | G1/G2 | Full fixture latency, fairness, restore and report | Large reference portfolios unverified |

RF coverage index (all observations are assigned, none becomes a delivery claim):
RF01→C2/E1; RF02→B4; RF03→B4/F2/F7a; RF04→D2/D3;
RF05→B4/F2/E3; RF06→F2/F3; RF07→A3/C1/D1/F2/F3;
RF08→A3/C1; RF09→A1/A2/B2/B3/F1; RF10→B2/B3/F1;
RF11→C1/C3; RF12→C1/F6; RF13→D1; RF14→D2/D3;
RF15→A3/D2/D3; RF16→D3/F3; RF17→E1; RF18→F4;
RF19→B4/F2/F4; RF20→E3/E4; RF21→E3/E4; RF22→F1/F6;
RF23→C2/C3/E1/E4/F6; RF24→B1/E3/F6; RF25→B3/D2/F5;
RF26→F5; RF27→C4; RF28→B4/F6; RF29→F1/E4/F4;
RF30→F1/F7a; RF31→F5.

## 7. Decisions and release blockers

No answer is needed to finish planning or begin A1 in a later implementation task.
Record each decision in the parity register with owner, chosen scope, fixture,
date and approver. An unresolved gate cannot be waived by a successful unit test.

| Gate / owner | Needed before | Bounded default and required evidence |
| --- | --- | --- |
| V01 — accounting/legal specialist | Relevant D2/D3/E5/F2/F5 release | Preserve engine invariants; review cost eligibility, commercial/VAT, WEG addressee/liability, reserves, rounding and mixed scope. Unsupported cases block; approved golden fixtures required. |
| V02 — specialist + product | D4 internal methods | D1 reconciled provider allocations/explicit CO2 evidence first; approve applicability, exceptions and reference calculations before enabling internal methods. |
| V03 — accountant | C1/D2/D3 periods and C4/F5 exports | Exact allocation periods; legacy unknown remains unknown. Decide year-boundary/manual coverage, accounting basis, account/tax mappings, schema versions and target importer acceptance. |
| V04 — product/integration owner | Each C3/C4/E3/E4/F1/F4/F7 live step | Local file flows and fake providers first. Choose contracts, supported institutions/channels, credentials, idempotency/status/webhook semantics and sandbox evidence; obtain separate live-action authorization. |
| V05 — organization admin | First team financial finalization | Landlord self-review; professional preparer/reviewer separation per mandate. Version/audit policy and test self-approval denial under team mode. |
| V06 — privacy/security + specialist | Portal/provider production use | Restricted source access, no inferred retention duration. Resolve former-resident access, processor region, holds, originals/derived/staging/portal deletion and signed artifact retention. |
| V07 — product | Full parity claim or companion/signing/settings scope | Preserve RF depths, public baseline for unexercised groups. Decide mobile native/PWA, signature scope and account/security matrix with bounded acceptance; responsive web is initial scope only. |
| V08 — operations/product | Professional capacity launch | Provisional section 5 G1 hardware/load/SLO/RPO/RTO and per-org budgets; measured load and restore report, not extrapolated test counts. |
| V09 — specialist/product | E5 voting and F2/F3/F4 legal processes | Only reviewed voting/proxy/circular and rent-change/notice/template methods; proposals carry no automatic legal effect. |

## 8. Verification and documentation handoff

For later implementation, retain a receipt per milestone: source revision or changed
content fingerprint, fixture version, toolchain, scoped command/check, result,
limits, reviewer and V decisions. Run focused unit/DB/API checks for changed
contracts, browser journeys for integration, schema/validator/specialist checks
for their distinct claims. Run broader regressions when shared engines/auth/jobs
change; do not repeat unchanged passing evidence solely because a commit was made.
Tests using fake providers do not prove live delivery. Every failed/unavailable
check remains visible, never silently counted as passed.

Historical audit receipts at application baseline `1d35f7d` (1,256 backend tests,
two skipped; 56 frontend tests; lint/build with bundle warning) remain historical
evidence only. They were not rerun for this plan and validate none of its future
features. The planning review checks P0–P5, A01–A33, RF01–RF31 and V01–V09 coverage,
dependency consistency, existing versus proposed paths, relative local links and
Markdown/diff hygiene. Independent read-only review is appropriate; no application
build, DB reset, live service or new reference inspection is needed.

Implementation handoff starts at A1, then proceeds only through the applicable
milestone exits. Report commit IDs, checks and remaining feature/release gates;
never mark the entire product complete when only the next milestone is accepted.
