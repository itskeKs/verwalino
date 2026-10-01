# Product audit and reference software parity plan

Date: 2026-10-01. Inspected baseline: `1d35f7d`; working tree was clean.

## Decision and scope

Retain the existing accounting and property-management core. Build the missing
workflows around it, with minimum data entry as a product requirement throughout
the reference software parity work. Serve both private landlords and professional managers
with hundreds of properties through one domain model and progressively disclosed
controls. Functional parity comes before further product expansion.

This assessment and backlog do **not** mean parity has been implemented. Public
Reference software documentation establishes a provisional feature baseline, not exhaustive
screen-by-screen acceptance. During this original code audit the app opened only
at its login page; no external accounts were modified at that stage. A subsequent
[authenticated reference review](2026-10-01-reference-software-review.md)
on the same date exercised user-authorized synthetic records and documented
RF01–RF31, saved results, drafts and unverified boundaries. That review updates the
reference evidence, not the implementation status below. No production deployment
was performed and no real customer documents were sent to a model provider.

The historical SDD deliberately excludes bank imports, portals and automatic
posting. Those exclusions no longer describe the requested product destination.
Existing release/approval safeguards remain in force until replaced by explicitly
designed, tested policies; automatic preparation and assignment should remove work
without silently inventing missing facts or finalizing incorrect accounts.

## What exists

“Implemented” below means a code path and applicable automated coverage exist.
It does not mean live provider quality, professional acceptance or production
capacity has been established.

| Capability | Current evidence | Remaining work |
| --- | --- | --- |
| Organizations, mandates, users and roles | Tenant-scoped repositories, PostgreSQL RLS, admin/clerk/read-only roles, auth and isolation tests | Object/mandate-scoped staff permissions, professional team workflow and simpler landlord onboarding |
| Properties, units, persons, owners and leases | API, UI, temporal lease/ownership constraints | Bulk migration, richer rental lifecycle, co-tenants and versioned rent changes |
| Invoice documents | PDF text extraction, category suggestions, XML UBL/CII and ZUGFeRD routing, hashes and version chains | Scan activation, automatic assignment, document-to-cost linkage repair, duplicate workflow, line splitting |
| Operating-cost settlement | Deterministic distribution, day-based occupancy/vacancy, rounding, preview/finalize, immutable snapshots and PDF generation | Completeness assessment, stored consumption inputs, automatic payment selection and portfolio runs |
| WEG | Annual accounts, economic plans, asset reports, owner history and payment derivation paths | Complete asset/bank reconciliation and professional reference-case acceptance |
| Payments | Manual bank-account/payment records, rent ledger, open items and cancellation paths | Bank feed/file import, reconciliation rules, partial/multiple matches and SEPA workflow |
| Dunning and deadlines | Notice generation/PDF/history, deadline views | Automated work queues, delivery and escalation workflows |
| Meetings and resolutions | Meetings, agenda, resolution register and PDF services | Validate full professional meeting lifecycle; do not infer coverage from module names |
| Declaration import and building knowledge | Asynchronous extraction, review, approval, source evidence and approved-fact search | Representative provider evaluation and connection of approved rules to executable settlement rules |
| Privacy and record integrity | Export/anonymization paths, audit events and immutable financial records | Outstanding specialist acceptance in historical roadmap; no certification claim |

Code anchors: `backend/app/api/`, `backend/app/services/`, `backend/app/engine/`,
`frontend/src/App.tsx`, `backend/tests/engine/` and the `test_m1_*`–`test_m9_*`
test modules. The detailed findings below distinguish working primitives from
unfinished user journeys.

## Concrete gaps in the requested journeys

### Upload: file → recognized data → assigned document → linked draft

1. **Ordinary scanned invoices are not OCR-enabled by default.**
   `backend/app/main.py` registers `RoutingExtractor(PdfTextExtractor())`;
   `backend/app/core/pdf_extraction.py` defaults `ocr_fallback=False`. Image-only
   invoices therefore return empty extraction through this path. The declaration
   import's separate OCR pipeline does not solve ordinary invoice intake.
2. **Upload-first cost creation loses the source relationship.**
   `frontend/src/pages/InvoiceDocumentDetailPage.tsx::handleCostSubmit` calls
   `createCostEntry(costForm)` without the uploaded document ID. The API's
   `CostEntryCreate` has no source-document field or attachment step. Consequently,
   confirming that new cost cannot release the original unattached document.
   `CostEntryService.confirm` releases only an already attached document. Existing
   flow coverage in `test_m2_gobd_belegfluss.py` creates a cost first and uploads
   with `cost_entry_id`; that inverse flow does not protect this UI journey.
3. **Recognition is assistive and narrow.** PDF invoice fields use German text
   heuristics; structured invoices have a separate parser. The extraction contract
   has no property/unit/mandate candidate assignment, document-class routing or
   multi-position output. Parsing an XML invoice is not evidence of full
   EN-16931/XRechnung conformance validation.
4. **The user still chooses context and creates the cost manually.**
   `openCostForm` explicitly starts with `property_id: ""` and `vendor_id: null`.
   The invoice list uploads only `files[0]`. Invoice, mandate-document and
   declaration uploads are separate workflows. The list-page promise that upload
   creates a cost draft is ahead of the backend's actual behavior.
5. **Content-addressed storage is not business deduplication.** SHA-256 storage
   exists, but upload creates a fresh document record; repeat uploads need an
   explicit duplicate/replacement decision and idempotent draft creation.

### Settlement: property + period → complete draft → exception review

1. Existing service aggregation loads the property's units, leases, owners and
   confirmed cost entries. That is substantial reusable implementation.
2. A run is created for one lease, with property, lease and dates selected by the
   user (`SettlementsPage.tsx`). There is no portfolio batch preparation workflow.
3. Advance-payment derivation exists as an opt-in endpoint; `MANUAL` remains the
   default. Missing advances become zero in `_load_engine_input`. A completeness
   layer must distinguish “verified zero” from “not collected”. Derivation currently
   takes a year argument; arbitrary settlement-period reconciliation needs design.
4. Consumption and person counts come from request `unit_metrics`; external and
   manual distributions come from request extras. `Meter`/`MeterReading` models
   exist but no registered meter API/UI or automatic reading-to-settlement path
   was found. Heating-provider amounts require an import/persistence workflow.
5. The engine can consume an already determined tenant-apportionable amount.
   A full automatic CO2 allocation calculation was not found; a CO2 label and
   partial-amount support must not be reported as a completed CO2 calculator.
6. Approved declaration rules are available as building knowledge, but the
   settlement loaders do not query `PropertyRule` or approved rule repositories.
   Rule extraction is not yet automatic application of special distribution rules.
7. Calculation validates supplied inputs, but cannot establish that all annual
   invoices, payments and readings were collected. Add readiness checks before
   presenting a complete draft or permitting finalization.

## Provisional reference software parity inventory

Baseline sources were accessed on 2026-10-01. “Missing” means no corresponding
operational module was found in the inspected API/model/UI inventory; it is not a
claim that every supporting primitive is absent.

| Reference capability | Local status | Delivery group |
| --- | --- | --- |
| Property/unit/contact records and ownership | Implemented core; bulk import and richer portfolio views missing | P1, P5 |
| Lease management, deposits, rent changes, index/stepped rent, handover | Basic lease and deposit fields; advanced lifecycle missing | P5 |
| Invoice capture, document folders, email intake, stationery | Partial invoice and mandate-document handling; universal intake missing | P1, P5 |
| Bank connection and automatic rent/transaction assignment | Missing; manual payment foundation exists | P2 |
| Income/expense bookkeeping, recurring expenses, loans and cashflow | Cost/payment primitives; broader finance workflows missing | P2, P5 |
| Operating-cost accounts and meter management | Settlement core exists; meter workflow and data collection missing | P3 |
| Dunning and reminders | Notices/PDF exist; communication workflow missing | P4 |
| Tasks/cases and deadlines | Deadline monitor exists; general case management missing | P4 |
| Tenant portal, announcements and messages | Missing | P4 |
| DATEV with receipts, EÜR, Anlage V and finance exports | Missing; financial PDFs/privacy export are narrower features | P5 |
| Contract generation/templates/signing and handover protocols | Missing | P5 |
| SEPA export, postal delivery and listing integration | Missing | P2, P4, P5 |
| Mobile companion and push notifications | Responsive web app exists; native companion/push parity not delivered | P5 |
| Settings, account/security and role administration | Basic administration exists; full parity needs authenticated comparison | P5 |

Sources: feature categories,
rental lifecycle,
documents,
finance,
meters,
operating-cost accounts,
bank matching,
tenant portal,
DATEV export,
mobile scope,
integrations.

The integration documentation explicitly describes manual assignment of incoming
documents to objects, units and bookings. Universal automatic assignment is thus
our requested improvement, not demonstrated reference software behavior. Its bank, DATEV,
SEPA, postal and listing integrations are separate parity deliverables.

WEG accounting and governance remain essential for this product's professional
audience. Do not infer full professional WEG equivalence from the reference software's landlord
feature list or its ability to record externally supplied WEG accounts.

## Delivery order and acceptance

### P0 — Repair and establish complete reference journeys

- Fix upload-first source attachment atomically, preserving tenant boundaries,
  original-document integrity and idempotency. Check `invoice_documents.py`,
  `cost_entries.py`, their services and `InvoiceDocumentDetailPage.tsx`.
- Activate a bounded, observable OCR path for invoices; failures remain actionable.
- Replace misleading completion states with explicit “missing data” states.
- Add representative browser/API journeys: upload → attached cost → confirmation
  → settlement → PDF. Include scanned/structured invoices and a second tenant.
- Acceptance: original receipt is linked and released correctly; repeated actions
  cannot double-book; incomplete data cannot masquerade as complete accounting.

### P1 — Unified intake and assignment

- One bulk drop zone for invoices, contracts, declarations, notices, readings and
  external accounts. File type and document purpose select bounded processors.
- Reuse storage, source evidence, asynchronous processing and review patterns;
  avoid forcing all document classes into the declaration schema.
- Resolve organization/mandate/property/unit/vendor/period from stable identifiers,
  addresses and known relationships. Automatic assignment requires unambiguous
  evidence; conflicts appear once in a shared exception queue.
- Store proposed fields, source locations and explanations; support multi-line
  allocation, replacement/version handling and duplicate detection.
- Acceptance: a sanitized mixed batch produces linked drafts without retyping known
  data, while ambiguous addresses, unknown units and duplicates are held visibly.
  Measure correction rate, manual fields and time per document by document class.

### P2 — Transaction ingestion and reconciliation

- Add idempotent bank-file imports, then a bank-connection provider adapter.
- Separate imported transactions from ledger allocations. Support partial/overpaid
  rent, multiple rents per transfer, refunds, reversals and recurring expenses.
- Reconcile confirmed allocations into rent ledgers, open items and advances;
  add SEPA export with an explicit review/release workflow.
- Acceptance: repeated imports preserve totals; payments never cross mandates;
  a reference year's bank/ledger balances reconcile without duplicate allocations.
- Live bank connections and external actions require their separate authorization.

### P3 — Automatic settlement preparation

- Persist meter/readings and external heating allocations; connect confirmed
  property rules through deterministic, versioned rule execution.
- Add property/period readiness assessment: expected invoices, unassigned costs,
  readings, occupancy, distribution keys, payment coverage and anomalies.
- Prepare all relevant lease/owner drafts from one property/year selection;
  reuse deterministic engines and immutable final snapshots. Reconcile exact
  periods, show sources and offer grouped exception review.
- Acceptance: reference cases for rental, WEG, mixed use, SEV, vacancy, changes
  during the year, special keys and corrections reconcile to approved results.
  Complete cases require no re-entry; incomplete cases identify specific gaps.

### P4 — Communication and daily operations

- Add cases/tasks with responsibilities, deadlines and document context.
- Add tenant/owner access, shared documents, messages, templates, delivery history
  and postal integration. Keep audience permissions separate from staff roles.
- Acceptance: recipients only see authorized records; duplicate jobs cannot send
  duplicate notices; undelivered items remain visible. Use fake delivery in tests.

### P5 — Close the remaining reference feature gaps

- Add lifecycle features, import/export, finance reports, DATEV, templates/signing,
  listing integration and mobile/push coverage from the parity inventory.
- Before each submodule, turn its official reference behavior into a bounded
  acceptance list, using RF01–RF31 where applicable; close the reference review's
  remaining coverage with suitable fixtures, accounts and provider access.
- Acceptance: each parity row has a runnable user journey, ownership of edge cases
  and evidence. A navigation item or data model alone does not count as delivery.

### Scale and professional acceptance — continuous, final release gate

- Core list APIs currently use unbounded `list_all`; `SettlementsPage` loads whole
  property/lease/unit/person collections and filters partly in the browser.
  Replace with server-side search, stable pagination and targeted selectors.
- Add bulk/background processing, progress/retry controls, concurrency protection,
  scoped staff rights and portfolio exception queues alongside P1–P3.
- Provisional performance fixture: 500 properties, 10,000 units, multiple years of
  documents/payments and concurrent users. Agree response/throughput targets before
  measuring; no capacity claim is established by current unit tests.
- Complete restore drills, operational monitoring and specialist reference-case
  review. The user's qualification as a certified manager is a target persona;
  it is not software certification.
- Private-landlord UI: guided setup, inferred single-property defaults, two primary
  actions (upload and prepare accounts). Professional UI: saved portfolio filters,
  assignments, batch actions and exceptions, using the same backend invariants.

## Verification evidence

Baseline and dependency assumptions: existing local dependencies; Node 26.0.0,
Python 3.13.13; PostgreSQL 16 in the repository's disposable test configuration on
port 55432. Backend tests reset only that isolated database. CI uses other runtime
versions, so local results do not substitute for CI or production acceptance.

- `frontend`: `npm test -- --reporter=dot` — passed, 56 tests across 7 files.
- `frontend`: `npm run lint` — passed.
- `frontend`: `npm run build` — passed; emitted a ~560 kB minified-chunk warning.
- `backend`: `.venv/bin/python -m pytest -q` with explicit disposable-database
  configuration — passed, 1,256 tests passed and 2 skipped in 169.41 seconds.
  Includes database/API, tenant-isolation, engine, extraction and PDF tests.
  Skipped checks are not counted as evidence of working behavior.
- No fresh production/browser end-to-end, load, live bank, provider-quality,
  specialist/legal or third-party conformance validation was performed.

Only assessment/planning documentation is changed by this milestone. Feature
implementation and the acceptance milestones above remain outstanding.
