# M2 Design: Costs, Standard Chart of Accounts & GoBD Voucher Flow (Belegfluss)

As of: 2026-06-01 · Version 0.1 (design, before implementation)
References: [architecture.md](architecture.md) §3 (GoBD immutability), §5.4 (I2-bearing entities) · [archived roadmap](archive/roadmap.md) M2 · SDD v0.2 §5.3/§6.4, §6.5, §6.6, §7, §8/§9, §14, §15, §16
Status: **Draft for confirmation by the human — no application code, no migration, no commit**
ADR for this document: [adr/0003-gobd-immutability-mechanism.md](adr/0003-gobd-immutability-mechanism.md)

> This document builds on the foundations laid in `architecture.md` §3 and §5.4 and turns them into an **implementable** specification for M2 (costs & GoBD voucher flow). It makes the correctness- and architecture-critical decisions (immutability mechanics, version chain, state machine, storage abstraction) and hands a work-through checklist to the implementer.
>
> **Binding force:** The SDD (v0.2), the roadmap, `architecture.md` and ADRs 0001/0002 are the source of truth and remain **untouched**. This document is additive. Decisions that the human should confirm are marked **[DECISION]**; open SDD §18 items are marked **[OPEN]**.
>
> **No self-certification:** This design makes voucher (Beleg) storage *transparent, traceable and auditable* and discloses its assumptions. It cannot guarantee that the storage is audit-proof or court-proof in a specific case. Before the GoBD/GDPR (DSGVO)-relevant parts go into production (M9 at the latest), a professional sign-off by a tax advisor/specialist lawyer is planned.

---

## 0. Scope and Connection to M0/M1

**M2 exit (roadmap):** "An uploaded PDF invoice is recognized as a proposal, confirmed, archived immutably and is relevant for settlement." This criterion is the benchmark.

M2 builds on already implemented and verified foundations (58 tests green) and adopts their conventions **unchanged**:

| Existing foundation (M0/M1) | How M2 uses it |
| --- | --- |
| RLS pattern: `ENABLE`+`FORCE`, four policies per tenant table (`org_isolation_select/insert/update/delete`) with `NULLIF(current_setting('app.current_org_id', true), '')::uuid`, fail-closed | Every new tenant table gets exactly this pattern (own migration, analogous to `0002`/`0004`). |
| `app_rw` grant pattern; `audit_event` append-only via `GRANT SELECT, INSERT` + omitted DELETE policy + coverage test | Model for voucher immutability (ADR-0003). |
| `TENANT_TABLES` registry + `test_rls_coverage.py` (every table must be classified, otherwise the test goes red) | Extended with the M2 tables; `cost_category` goes into `TENANT_TABLES` **or** `GLOBAL_TABLES`, depending on the Assumption A-1 decision. |
| Repository layer (`TenantRepository`, explicit `organization_id` filter in addition to RLS) | All M2 repos inherit from it; append-only repos analogous to `AuditRepository` (no `update`/`delete`). |
| `AuditEvent` + `AuditRepository` | M2 writes voucher/cost/approval events through them (§6). |
| Money/share convention: `NUMERIC(14,2)` for EUR, `NUMERIC(12,6)` for shares, `Decimal` on the application side; UUID PK; `TIMESTAMPTZ`; `ondelete="RESTRICT"` on FKs to `organization` | Adopted throughout. |
| Temporal EXCLUDE constraints (btree_gist) as a model for DB-enforced integrity | Reused for version uniqueness of vouchers (§4). |
| Swappable `PdfRenderer` protocol (`app/core/pdf.py`) | Model for the storage abstraction (§5) and for the OCR extraction abstraction (§3.5). |
| `mandate_type`/`usage_type` as `String` + `CHECK (... IN (...))` constraint (no DB enums) | M2 uses the same String+CHECK convention for all enumerations (status, distribution key, apportionability). |
| `decode_session_token` returns `uid`/`oid`/`role`; `require_write_dep` (OWNER_ADMIN|CLERK) | Voucher approval is a write operation → `require_write_dep`; READ_ONLY must not approve. |

**New tables in M2:** `cost_category`, `cost_entry`, `invoice_document`. Optionally (see §3.5) an extraction helper table `document_extraction` — recommendation: **not** as a separate table for now, but as a JSONB column on `invoice_document` (rationale in §3.5).

**Not in M2:** Settlement snapshots (`settlement_run`/`settlement_line`) — M3/M4. E-invoice parsing (XRechnung/ZUGFeRD) — M5 (M2 only specifies the interplay with the state machine so that M5 can dock on). Payments — M6.

---

## 1. Standard Chart of Accounts (`cost_category`)

### 1.1 Domain Source and Scope

SDD §7: a **fixed** standard chart of accounts (Standardkontenplan) for phase 1; custom categories are not created (SDD §3 non-goal: "no freely configurable chart of accounts"). Each category brings **defaults** for apportionability (Umlagefähigkeit) and distribution key that can be manually overridden per cost item (SDD §7, §8). `architecture.md` §16/§1.2 requires "cost categories as **versioned** system data".

The defaults reflect **legal requirements** (SDD §17: BetrKV, BGB §556, WEG §16, CO2KostAufG …). That is exactly why they are versioned: if the legal situation changes (e.g., cable/broadband apportionability since 2024, SDD §7.2), a **new version** of the chart of accounts may be created without already settled cost items retroactively losing their default provenance. This is the bridge between "fixed plan" (no user-created entries) and "versioned" (the law changes).

### 1.2 [DECISION] Global vs. Copied Per Org (Assumption A-1)

`architecture.md` Assumption A-1 leaves this open. **Recommendation: a global, versioned master data set (no `organization_id`), in `GLOBAL_TABLES`** — analogous to the `organization` table.

Rationale:
- SDD §3/§7 **explicitly excludes** org-specific categories in phase 1. A plan copied per org would be complexity (RLS, seed per org, drift between orgs) without any domain value as long as orgs may not customize the plan.
- "Versioned" is **not** achieved through per-org copies but through a `version` axis on the global table (see 1.3). A cost item references the concrete category version that applied at the time of capture — this makes the provenance audit-proof without multiplying the plan.
- Read access for all orgs, write access only via migration/seed (system role). This corresponds exactly to the treatment of other global system master data in ADR-0001/`architecture.md` §2.5.

**Consequence for `test_rls_coverage.py`:** `cost_category` is added to `GLOBAL_TABLES` and the test `test_global_tables_have_no_rls` covers it (no RLS). **If** the human overturns A-1 (orgs may customize/version the plan), `cost_category` instead becomes organization-scoped (`organization_id`, RLS, in `TENANT_TABLES`) and is seeded per org when the org is created — the model rework is limited to this one table, because `cost_entry` only points to a category row via FK anyway. **This decision should be made before M2 implementation.**

### 1.3 Schema `cost_category` (recommendation: global)

| Column | Type | Meaning |
| --- | --- | --- |
| `id` | `UUID` PK | as everywhere. |
| `plan_version` | `SMALLINT NOT NULL` | Version of the overall chart of accounts (1 in phase 1). Allows later legal changes as a new version. |
| `code` | `String(50) NOT NULL` | stable domain key, e.g. `GRUNDSTEUER`, `KALTWASSER`, `CO2_KOSTEN`, `INSTANDHALTUNG`. Independent of language/UI. |
| `label` | `String(255) NOT NULL` | Display name, e.g. "Grundsteuer / öffentliche Lasten". |
| `cost_group` | `String(20) NOT NULL` | `UMLAGEFAEHIG` \| `GEMISCHT_PRUEFEN` \| `NICHT_UMLAGEFAEHIG` (corresponds to SDD §7.1/§7.2/§7.3). `CHECK` constraint. |
| `tenant_apportionability_default` | `String(24) NOT NULL` | Tenant operating-cost default as a controlled vocabulary (see 1.4). |
| `owner_billing_relevant_default` | `BOOLEAN NOT NULL` | Column "owner settlement yes" (Eigentümerabrechnung ja) from SDD §7 (consistently `true` in the plan, but modeled explicitly). |
| `owner_distribution_key_default` | `String(20) NULL` | Default distribution key for the owner settlement (controlled vocabulary, §1.5). |
| `tenant_distribution_key_default` | `String(20) NULL` | Default distribution key for the tenant settlement. NULL if not apportionable. |
| `reserve_relevant` | `BOOLEAN NOT NULL DEFAULT false` | marks reserve categories (maintenance reserve, Erhaltungsrücklage) for the separate WEG presentation (SDD §11, M4). |
| `legal_hint` | `String(500) NULL` | Hint text (e.g., "limited apportionability since 2024"), for UI/plausibility. |
| `sort_order` | `SMALLINT NOT NULL DEFAULT 0` | fixed order in lists/PDF. |
| `is_active` | `BOOLEAN NOT NULL DEFAULT true` | allows retiring a category in a new plan version without deleting it (history integrity). |
| `created_at` | `TIMESTAMPTZ` | as everywhere. |

Constraints: `UNIQUE (plan_version, code)`; `CHECK (cost_group IN (...))`; `CHECK (tenant_apportionability_default IN (...))`; `CHECK (owner_distribution_key_default IS NULL OR owner_distribution_key_default IN (...))` (analogous for tenant). **No** `organization_id`, **no** RLS (global master data set).

`cost_entry` references `cost_category_id` (FK). Because an existing row is never changed in content but at most replaced via a new `plan_version`, the category version chosen at capture time remains audit-proof. **[DECISION]** whether `cost_entry` additionally writes `plan_version` redundantly (recommendation: yes, as a denormalized audit anchor — cheap, and makes settlements independent of later plan versions).

### 1.4 Controlled Vocabulary: Apportionability (Tenant Operating-Cost Default)

Derived directly from SDD §7.1–§7.3 (String + CHECK, no DB enum, consistent with M0/M1):

| Value | Meaning | SDD source |
| --- | --- | --- |
| `UMLAGEFAEHIG` | generally apportionable to tenants | §7.1 |
| `UMLAGEFAEHIG_EXTERN_PRUEFEN` | apportionable, but via external settlement (heating/hot water/metering service) — avoid double billing | §7.1/§7.2 (heating, hot water, metering service) |
| `GEMISCHT_PRUEFEN` | mixed, shares must be carved out (caretaker, Hausmeister: remove administration/repair share) | §7.2 (caretaker) |
| `TEILWEISE_PRUEFEN` | only a share is apportionable (CO2: tenant share only, 10-stage model) | §7.2 (CO2) |
| `MANUELL_PRUEFEN` | only with a concrete lease basis (other operating costs; cable/broadband since 2024) | §7.2 |
| `NICHT_UMLAGEFAEHIG` | never operating costs (administration, maintenance, reserve, special assessment …) | §7.3 |

Important: these values are **defaults/flags**, not an automatic posting decision. The engine (M3) decides, depending on the effective value *per cost item*, whether/how to apportion; for `*_PRUEFEN` values a deliberate user decision is required (suggestion tool, SDD §8/§9 — "no automatic posting without approval").

### 1.5 Controlled Vocabulary: Distribution Keys (SDD §8)

`MEA` · `WOHNFLAECHE` · `BEHEIZTE_FLAECHE` · `EINHEIT` · `PERSONENZAHL` · `VERBRAUCH` · `EXTERNE_ABRECHNUNG` · `MANUELLER_BETRAG`.

This is exactly the SDD §8 list. Owner and tenant settlements can carry **different** keys per cost item (SDD §8 example: owner by MEA, tenant by living area/external). That is why there are separate default columns in `cost_category` (1.3) **and** separate effective columns in `cost_entry` (§2). The *semantics* of each key (how it distributes) belong to M3 (distribution engine) — M2 only stores the selection.

### 1.6 Seed/Migration of the SDD §7 Tables

The plan is created as a **data seed in its own Alembic migration** (not in a fixture, not at runtime), so that it is versioned, reviewable and identical in every environment — the same discipline as with RLS policies (ADR-0002).

- Migration `0005_cost_category` (proposal): `CREATE TABLE cost_category` + `INSERT` rows for **all** categories from SDD §7.1, §7.2, §7.3 with `plan_version = 1`, `code`, `label`, `cost_group`, default apportionability and default distribution keys **exactly according to the SDD tables**. Example rows (full list in the migration):

  | code | cost_group | tenant_apport_default | owner_dist_default | tenant_dist_default | reserve |
  | --- | --- | --- | --- | --- | --- |
  | `GRUNDSTEUER` | UMLAGEFAEHIG | UMLAGEFAEHIG | `MEA` | `WOHNFLAECHE` | false |
  | `KALTWASSER` | UMLAGEFAEHIG | UMLAGEFAEHIG | `VERBRAUCH` | `VERBRAUCH` | false |
  | `HEIZUNG_ZENTRAL` | UMLAGEFAEHIG | UMLAGEFAEHIG_EXTERN_PRUEFEN | `EXTERNE_ABRECHNUNG` | `EXTERNE_ABRECHNUNG` | false |
  | `HAUSMEISTER` | GEMISCHT_PRUEFEN | GEMISCHT_PRUEFEN | `MEA` | `WOHNFLAECHE` | false |
  | `CO2_KOSTEN` | GEMISCHT_PRUEFEN | TEILWEISE_PRUEFEN | `EXTERNE_ABRECHNUNG` | `EXTERNE_ABRECHNUNG` | false |
  | `KABEL_BREITBAND` | GEMISCHT_PRUEFEN | MANUELL_PRUEFEN | `EINHEIT` | `EINHEIT` | false |
  | `WEG_VERWALTERKOSTEN` | NICHT_UMLAGEFAEHIG | NICHT_UMLAGEFAEHIG | `MEA` | NULL | false |
  | `INSTANDHALTUNG` | NICHT_UMLAGEFAEHIG | NICHT_UMLAGEFAEHIG | `MEA` | NULL | false |
  | `ERHALTUNGSRUECKLAGE` | NICHT_UMLAGEFAEHIG | NICHT_UMLAGEFAEHIG | `MEA` | NULL | true |
  | `SONDERUMLAGE` | NICHT_UMLAGEFAEHIG | NICHT_UMLAGEFAEHIG | `MEA` | NULL | false |

  The exact default distribution keys per row follow the SDD §7 column "standard distribution". Where the SDD names several options ("area or MEA"), the seed picks a sensible default (for owners usually `MEA`, for tenants usually `WOHNFLAECHE`); the user can override per cost item. **[DECISION]** confirm the concrete default for each ambiguity — this is a domain determination, not a technical one. *(With A-1 = per org: identical seed, but executed per org on creation and organization-scoped.)*
- `downgrade()` deletes the seed rows and the table.
- **Idempotency/extension:** A future legal change arrives as a **new migration** with `plan_version = 2` and new rows — old rows remain untouched (versioning, 1.1).

---

## 2. Cost Item (`cost_entry`)

### 2.1 Domain Source

SDD §5.3/§6.4 (fields), §14 (service period, not only the invoice date), §8 (separate distribution keys), §9 (status). Organization-scoped (`organization_id`, RLS — four policies as in M1).

### 2.2 Schema `cost_entry`

| Column | Type | Meaning / rationale |
| --- | --- | --- |
| `id` | `UUID` PK | |
| `organization_id` | `UUID NOT NULL` FK→`organization` (`RESTRICT`) | Organization isolation (I1), RLS. |
| `property_id` | `UUID NOT NULL` FK→`property` (`RESTRICT`) | Property reference (SDD §6.4). |
| `management_mandate_id` | `UUID NULL` FK→`management_mandate` (`RESTRICT`) | optional mandate reference (SDD §6.4) — anchor for segregation of funds (architecture.md §2.6). |
| `cost_category_id` | `UUID NOT NULL` FK→`cost_category` (`RESTRICT`) | Category from the standard chart of accounts. |
| `cost_category_plan_version` | `SMALLINT NOT NULL` | denormalized audit anchor (see 1.3) — makes the settlement independent of later plan versions. |
| `vendor_id` | `UUID NULL` FK→`vendor` (`RESTRICT`) | Vendor/invoice issuer (SDD §6.2/§6.4). Nullable because drafts from OCR may only have the vendor assigned after review. |
| `invoice_date` | `DATE NOT NULL` | Invoice date (SDD §6.4). |
| `service_period_from` | `DATE NOT NULL` | **Start of the service period (Leistungszeitraum)** (SDD §6.4/§14 — core for pro rata distribution in M3, *not* the invoice date). |
| `service_period_to` | `DATE NOT NULL` | End of the service period. `CHECK (service_period_to >= service_period_from)`. |
| `gross_amount` | `NUMERIC(14,2) NOT NULL` | Gross amount (I4, never float). Sign allowed (credit notes). |
| `vat_amount` | `NUMERIC(14,2) NULL` | VAT (USt) amount, if available (SDD §6.4 — mainly from e-invoice, M5). |
| `vat_rate` | `NUMERIC(5,2) NULL` | VAT rate in percent (e.g., 19.00), if available. |
| `document_number` | `String(100) NULL` | Voucher number (SDD §6.4). |
| `owner_distribution_key` | `String(20) NULL` | **Effective** distribution key for the owner settlement (SDD §8) — prefilled from the category default, manually overridable. CHECK against the §1.5 vocabulary. |
| `tenant_distribution_key` | `String(20) NULL` | **Effective** distribution key for the tenant settlement — separate from the owner key (SDD §8). |
| `tenant_apportionable` | `String(24) NOT NULL` | **Effective** apportionability (tenant), prefilled from the category default, overridable. CHECK against the §1.4 vocabulary. |
| `apportionable_tenant_amount` | `NUMERIC(14,2) NULL` | for `TEILWEISE_PRUEFEN`/`GEMISCHT_PRUEFEN`, the tenant share explicitly determined to be apportionable (e.g., CO2 tenant share, carved-out caretaker share). NULL = whole amount/no amount, depending on `tenant_apportionable`. **[OPEN]** the exact modeling of the advance deduction/share is an M3 detail (SDD §18: caretaker/CO2 split); M2 keeps the field ready. |
| `status` | `String(16) NOT NULL` | Voucher flow status: `draft_extracted` \| `needs_review` \| `confirmed` \| `rejected` (SDD §9). CHECK. Default `needs_review` on manual entry? → see 2.3. |
| `source` | `String(12) NOT NULL` | Origin: `MANUAL` \| `PDF_IMPORT` \| `E_INVOICE` (e-invoice only in M5, value reserved). CHECK. |
| `confirmed_at` | `TIMESTAMPTZ NULL` | Time of approval (transition to `confirmed`). |
| `confirmed_by` | `UUID NULL` FK→`user` (`SET NULL`) | Who approved (audit/traceability). |
| `superseded_by_id` | `UUID NULL` FK self (`RESTRICT`) | on correction: points to the successor cost item (version chain, §3.4). |
| `created_at`/`updated_at` | `TIMESTAMPTZ` | as everywhere. |

Indexes: `organization_id`; `property_id`; `(organization_id, status)` (frequent filter: "open proposals"); `cost_category_id`; `(property_id, service_period_from, service_period_to)` (M3 pulls costs per property/period). FK columns indexed each.

**Rationale for service period NOT NULL (SDD §14):** The distribution engine (M3) computes pro rata periods on owner/tenant change **based on the service period**, not the invoice date (an invoice issued in January for December service belongs to the prior year). A missing service period would undermine the core correctness of M3 — hence a mandatory field. For OCR drafts where the period has not yet been recognized, the application keeps the record in status `needs_review`; approval to `confirmed` requires a valid period (validation rule, 2.3).

### 2.3 Status-Dependent Mandatory Fields

In draft (`draft_extracted`/`needs_review`) fields may be incomplete (OCR may not deliver everything). The **transition to `confirmed`** is the point at which domain completeness is enforced (application validation in the confirm service, §3.3):
- `cost_category_id`, `gross_amount`, `invoice_date`, `service_period_from/to`, `tenant_apportionable`, and at least one distribution key per relevant settlement sphere must be set/plausible.
- For `*_PRUEFEN` apportionability: deliberate user confirmation of the effective value (no silently waving defaults through).

> **[DECISION]** Default status on **manual** entry: `needs_review` (conservative, supports four-eyes review) vs. a directly editable draft that the same user may immediately set to `confirmed`. Recommendation: manual entry starts as a normal, editable record; `confirmed` is always an **explicit** second step (never implicit on save), so that "relevant for settlement" remains a deliberate act (in the spirit of SDD §9).

---

## 3. Voucher Flow State Machine (SDD §8/§9)

### 3.1 States

```
        (Upload PDF / e-invoice)                   (manual entry)
                 │                                        │
                 ▼                                        ▼
        ┌─────────────────┐  uncertain / critical  ┌──────────────┐
        │ draft_extracted │ ─────────────────────▶ │ needs_review │
        └────────┬────────┘                         └──────┬───────┘
                 │  (user reviews / corrects fields; voucher content editable)
                 │                                         │
                 │  reject                                 │ reject
                 ▼                                         ▼
            ┌──────────┐                              ┌──────────┐
            │ rejected │ ◀────────────────────────────│ rejected │
            └──────────┘                              └────┬─────┘
                                                           │ confirm  (approval)
                                                           ▼
                                                   ┌───────────────┐
                                                   │   confirmed   │  ← CONTENT FROZEN
                                                   └───────┬───────┘
                                                           │ Correction needed?
                                                           │  → NEVER mutation
                                                           ▼
                                          new cost_entry version (status=needs_review/confirmed),
                                          Original.superseded_by_id → new row  (see 3.4)
```

### 3.2 Allowed Transitions (Precise)

| From | To | Condition | Effect |
| --- | --- | --- | --- |
| `draft_extracted` | `needs_review` | Recognition uncertain / critical category | Content remains editable. |
| `draft_extracted` | `confirmed` | Mandatory fields complete (2.3), user confirms | **Freeze** (3.3). |
| `draft_extracted` | `rejected` | User discards | Record inactive, is retained (no hard delete in the voucher-bearing path). |
| `needs_review` | `confirmed` | as above | **Freeze**. |
| `needs_review` | `rejected` | discard | inactive. |
| `confirmed` | — | **no** further status change by mutation | Correction exclusively via a new version (3.4). |
| `rejected` | (draft) | optional "resume" | **[DECISION]**: allow, or rejected = final? Recommendation: `rejected` can be brought back and edited **as long as no voucher has been finalized**; not after finalization. |

Every transition produces an `audit_event` (§6). Transitions not in the table are forbidden (the service checks; a DB trigger blocks the critical ones, ADR-0003).

### 3.3 "Frozen From `confirmed`" — What Exactly Does That Mean

The **content-bearing part** of the cost item (amount, VAT, category, vendor, date, service period, distribution keys, apportionability, property/mandate) is **immutable** from `confirmed` on — DB-enforced via a `BEFORE UPDATE` trigger (ADR-0003). At the same time, **if** a voucher is attached, the `active` version of its `invoice_document` is **released** with `released_at`/`released_by` and thereby also frozen (§4). "Confirm of the cost item" and "release of the voucher" are the same transaction (§3.6).

Pure status/chaining fields (`status` from `confirmed`→`superseded` marking via `superseded_by_id`) are the **only** permissible change to a frozen row — the trigger permits exactly these columns and nothing else (ADR-0003 §1).

### 3.4 Correction = New Version (Instead of Mutation, SDD §14)

If a `confirmed` cost item has to be corrected (wrong amount, wrong category):
1. A **new** `cost_entry` row is created (own `id`) with the corrected values, status `needs_review` (or directly `confirmed` if the correction is unambiguous), `source` = `MANUAL`.
2. The old row is pointed to the new row via `superseded_by_id`. Its content remains **unchanged** (frozen). It is no longer counted as active in settlements — the active logic selects the most recent row of a chain without `superseded_by_id`.
3. The associated voucher, if it is also corrected (new file), is attached as a **new `invoice_document` version** (§4.4); it references the corrected/applicable cost item.
4. `audit_event`: `cost_entry.superseded` (old) + `cost_entry.created`/`confirmed` (new), with reference.

**Active definition (for the M3 engine):** A chain starts with the first row and runs via `superseded_by_id`. "Active" is the single row of the chain with `superseded_by_id IS NULL` **and** status `confirmed`. This is the row that is settled. *(Symmetric to the voucher version chain, §4.3 — deliberately two adjacent but separate chains: a domain-level `cost_entry` correction chain, and a file-/document-level `invoice_document` version chain.)*

> **[DECISION]** `superseded_by_id` (forward pointer, proposed here) vs. `supersedes_id` (backward pointer as with `invoice_document` in architecture.md §5.4). Recommendation: for `cost_entry` use **forward** (`superseded_by_id`), because the active query "WHERE superseded_by_id IS NULL" is the most frequent, most performant query; for `invoice_document` it stays with the **backward** pointer `supersedes_id` (architecture.md §5.4), where the version history is the guiding principle. Both are valid; what matters is that **one** direction is used consistently per table. Pin this down during implementation.

### 3.5 Interplay With Assisted PDF Import/OCR (SDD §8/§9) — Interface Only

The **extraction implementation** (OCR/parsing) is done during implementation; the M2 design only fixes the interplay:
- On upload, an `invoice_document` (file + SHA-256 stored) is created **immediately** in version state `active`, but **without** `released_at` (not yet released), together with a `cost_entry` in status `draft_extracted` carrying the extracted field suggestions.
- The extraction is encapsulated behind a **narrow, swappable interface** (protocol) — exactly the pattern of `PdfRenderer` (`app/core/pdf.py`): `DocumentExtractor.extract(file_bytes, mime) -> ExtractionResult`. Default: a local OCR/parsing stub implementation; later swappable for an external service or a local model (SDD §16). E-invoice (M5) is a **second** implementation of the same interface (structured fields instead of OCR guessing, SDD §6.5) — so M5 docks on without a state machine change.
- The extraction result (recognized fields + confidences + raw-text features) is stored as a **JSONB column `extraction_payload` on `invoice_document`**, **not** as a separate table. Rationale: 1:1 with the document, no lifecycle of its own, no cross-entity query needed; a separate table would be unnecessary normalization. *(If several extraction runs per document are to be compared later, this can be split out into a `document_extraction` table — then organization-scoped with RLS. Not needed for M2.)*
- A confidence threshold decides the start status: low confidence / critical category → `needs_review` instead of `draft_extracted`. The exact threshold is an implementation question; **no** suggestion is ever automatically `confirmed` (SDD §9, §3 non-goal: "no automatic posting without approval").

### 3.6 Approval Transaction (Atomic)

Confirming a cost item with an attached voucher is **one** DB transaction (one `get_db` session, one `session.begin()`):
1. Validation of the mandatory fields (2.3); on violation → 422, no status change.
2. `cost_entry.status = 'confirmed'`, `confirmed_at = now()`, `confirmed_by = uid`.
3. If a voucher is present: `invoice_document` (active version) `released_at = now()`, `released_by = uid`, compute `retention_until` (§7), **(re-)verify** `content_sha256` against the stored file (§4.5).
4. `audit_event`: `cost_entry.confirmed` and (if a voucher is present) `document.released`.
5. Commit. From the commit on, the immutability triggers (ADR-0003) apply: the rows are frozen.

If one of the steps fails, the whole transaction rolls back — there is **no** state "voucher released, cost item not" or vice versa.

---

## 4. `invoice_document` + Immutability Mechanics (Core, I2)

### 4.1 Domain Source

SDD §6.6 (immutability, checksum, versioning, 8 years), §13 (entity), §16 (revision-proof (revisionssicher) storage, snapshots). `architecture.md` §3.2 (building blocks) and §5.4 (fields). Organization-scoped (`organization_id`, RLS).

### 4.2 Schema `invoice_document`

| Column | Type | Meaning / rationale |
| --- | --- | --- |
| `id` | `UUID` PK | |
| `organization_id` | `UUID NOT NULL` FK→`organization` (`RESTRICT`) | I1, RLS. |
| `cost_entry_id` | `UUID NULL` FK→`cost_entry` (`RESTRICT`) | associated cost item. Nullable only in the brief moment between upload and assignment; set in the normal case. **[DECISION]** whether 1:1 (one voucher ↔ one cost item) or 1:n (one voucher covering several items, e.g., a consolidated invoice). Recommendation for M2: 1:1, plus an optional later split as its own M question; SDD §6.4 speaks of "optional PDF file per cost item" → 1:1 is sufficient. |
| `storage_uri` | `String(1024) NOT NULL` | logical reference to the file in the object store (§5). **Not** the file content. |
| `storage_backend` | `String(16) NOT NULL` | `LOCAL_FS` (M2) \| `S3` (later). Allows migrating the storage without a schema change. |
| `storage_version` | `String(128) NULL` | version identifier of the backend (S3 object version ID / WORM marker); NULL for local FS or a file generation marker. |
| `original_filename` | `String(255) NULL` | Display name at upload. |
| `mime_type` | `String(100) NOT NULL` | e.g., `application/pdf`, `application/xml` (e-invoice M5). |
| `content_byte_size` | `BIGINT NOT NULL` | Size; part of the integrity attributes. |
| `content_sha256` | `String(64) NOT NULL` | **SHA-256 over the file content** (hex). Core of integrity (SDD §6.6). |
| `version` | `SMALLINT NOT NULL DEFAULT 1` | running version number of the logical voucher chain. |
| `supersedes_id` | `UUID NULL` FK self (`RESTRICT`) | Backward pointer to the predecessor version (architecture.md §5.4). First version: NULL. |
| `status` | `String(12) NOT NULL DEFAULT 'active'` | `active` \| `superseded`. CHECK. Per logical chain, **exactly one** `active` row at any point in time (§4.3). |
| `released_at` | `TIMESTAMPTZ NULL` | Release timestamp. NULL = not yet finalized (editable voucher draft). Set = **frozen** (ADR-0003 freeze condition). |
| `released_by` | `UUID NULL` FK→`user` (`SET NULL`) | Who released it. |
| `retention_until` | `DATE NULL` | Retention anchor (§7). Computed at `released_at`. NULL until release. |
| `superseded_at` | `TIMESTAMPTZ NULL` | when this version was superseded. |
| `extraction_payload` | `JSONB NULL` | OCR/parsing result (§3.5), relevant only for proposal/review. |
| `created_at`/`updated_at` | `TIMESTAMPTZ` | |

Indexes: `organization_id`; `cost_entry_id`; `(organization_id, status)`; `content_sha256` (duplicate detection / integrity query); `supersedes_id`. `retention_until` (for the later M9 retention scan).

### 4.3 Version Chain and "Exactly One Active"

- First upload: `version = 1`, `supersedes_id = NULL`, `status = 'active'`.
- Correction: new row `version = N+1`, `supersedes_id = <id of the previous active>`, `status = 'active'`; the previous row is set to `status = 'superseded'`, `superseded_at = now()` (this is the **only** permitted UPDATE of a released row, ADR-0003).
- **DB guarantee "at most one active per chain":** via a **partial unique index** on the chain root. Since `supersedes_id` forms a linked list, a stable `chain_root_id` column is additionally maintained (pointing to the root of the chain; first version = its own `id`), and a
  `CREATE UNIQUE INDEX uq_active_doc_per_chain ON invoice_document (chain_root_id) WHERE status = 'active'`
  ensures that per logical voucher chain two `active` versions never exist. **[DECISION]** `chain_root_id` (denormalized, simple unique index — recommended) vs. an EXCLUDE/trigger over the recursive chain (more complex). Recommendation: `chain_root_id`, because it matches the btree_gist/partial-index style of the code base and makes the active query trivial.

### 4.4 Correcting an Already Released Voucher

Symmetric to §3.4, but at the file level:
1. Write the new file to the object store, compute SHA-256.
2. New `invoice_document` row (`version+1`, `supersedes_id`=old active, `chain_root_id` unchanged, `status='active'`); old row → `superseded`.
3. If required by the domain, a `cost_entry` correction version in parallel (§3.4).
4. `audit_event`: `document.superseded` + `document.version_created` (+ if applicable `document.released` of the new version, if it is released immediately).

The original remains physically and in the DB (8-year retention, §7) — a correction adds, never replaces.

### 4.5 Integrity/Hash Mechanics

- **Computation:** SHA-256 over the exact file bytes at upload; **verified** again at release (§3.6 step 3) — ensures nothing was changed between upload and release.
- **Verification function:** a storage-independent helper function `verify_document_integrity(doc) -> bool` that reads the file from the backend, recomputes the hash and checks it against `content_sha256`. Used: (a) at release, (b) by a later M9 audit/check run, (c) in tests (§8).
- **What the hash does / does not do:** It makes **manipulation detectable** (tamper evidence). It does not prevent it on its own — that is done by the WORM properties of the storage (S3 Object Lock, later) plus the DB immutability (triggers/grants, ADR-0003). Hash + WORM + trigger together yield revision-proofness; none of them alone.

### 4.6 Enforcing Immutability → ADR-0003

The choice of the DB-level mechanism is made in **[ADR-0003](adr/0003-gobd-immutability-mechanism.md)**: a **status-aware `BEFORE UPDATE/DELETE` trigger** as the primary enforcement (because immutability here is *status-dependent*: editable until release, frozen afterwards) **+ a revoked DELETE grant** on `invoice_document` as the second line (voucher files are never deleted). Short version of the trigger rules:
- `invoice_document`: if `released_at IS NOT NULL`, the trigger blocks any UPDATE change to `content_sha256`/`storage_uri`/`storage_version`/`version`/`released_at`/`released_by`/`content_byte_size`; permits **only** `active→superseded` (+ `superseded_at`). DELETE of released rows is always forbidden.
- `cost_entry`: if `status = 'confirmed'`, the trigger blocks any content column change; permits only setting `superseded_by_id` (correction chaining). DELETE of a `confirmed` row is forbidden; drafts/`rejected` can be deleted.

---

## 5. Object Store Abstraction

### 5.1 Principle (Like `PdfRenderer`)

A narrow, swappable interface behind a **protocol** — exactly the pattern of `app/core/pdf.py`. Proposal: `app/core/storage.py`.

```text
@runtime_checkable
class DocumentStore(Protocol):
    def put(self, key: str, data: bytes, *, content_type: str) -> StoredObjectRef: ...
    def get(self, key: str) -> bytes: ...
    def exists(self, key: str) -> bool: ...
    # deliberately NO delete() in the phase 1 protocol -- vouchers are never deleted.
    # A later, retention-driven removal (M9) is a separate,
    # privileged operation, not part of the normal voucher path.

@dataclass(frozen=True)
class StoredObjectRef:
    backend: str          # "LOCAL_FS" | "S3"
    uri: str              # -> invoice_document.storage_uri
    version: str | None   # -> invoice_document.storage_version (S3 version id / WORM marker)
    sha256: str           # for cross-validation against the DB
    byte_size: int
```

- **M2 default `LocalFsDocumentStore`:** writes under a configured root directory, path **content-addressed** and organization-partitioned, e.g., `/<root>/<organization_id>/<sha256[:2]>/<sha256>.bin`. The file is set read-only after writing (chmod) as a weak WORM substitute; the real immutability is carried by the DB (ADR-0003) plus S3 Object Lock later. **Never** writes over an existing file (same hash = identical content → idempotent; different content would get a different hash and thus a different path).
- **Later `S3DocumentStore`:** S3-compatible with **Object Lock (WORM, compliance mode)** + **versioning** (SDD §16, architecture.md §1.1). `storage_version` holds the S3 version ID. EU region (architecture.md A-6).
- Selection via config (`settings`), not hard-wired in the voucher code — engine swap as with the renderer (`get_store()`/`set_store()`).

### 5.2 Where the File Lives, How the DB References It

- **File:** exclusively in the object store. Never in the DB (no BLOB) — the DB holds only metadata + hash + `storage_uri`/`storage_version`/`storage_backend`.
- **Reference:** `invoice_document.storage_uri` (+ backend + version). Content addressing (path = hash) gives a second, implicit integrity coupling: different content cannot lie "secretly" under the same path.
- **Organization partition in the path:** additional defense against cross-org mixing at the file system level — consistent with organization isolation (I1), even though authorization runs via the DB/RLS.

### 5.3 Confidentiality (Brief)

Vouchers contain personal data (GDPR). Local storage is located in the EU (architecture.md A-6); for S3, additionally server-side encryption. No public access; delivery to the UI only via authenticated, organization-checked API endpoints (the file is streamed by the backend, not via a public URL).

---

## 6. Audit Trail Usage (Existing `audit_event` From M0)

M2 is the first milestone in which `audit_event` is used **intensively** (architecture.md §5.4). Written via the existing `AuditRepository` (append-only). Logged operations (`action` values, `entity_type` = `cost_entry` or `invoice_document`):

| action | when | payload_diff (excerpt) |
| --- | --- | --- |
| `cost_entry.created` | Creation (manual or from import) | source, category, gross_amount, status |
| `cost_entry.updated` | Field change in draft | changed fields (before/after) |
| `cost_entry.status_changed` | every status change | from→to |
| `cost_entry.confirmed` | Approval | confirmed_by, snapshot of the approved fields |
| `cost_entry.rejected` | Reject | reason (optional) |
| `cost_entry.superseded` | Correction chain | superseded_by_id (new version) |
| `document.uploaded` | Upload | filename, mime, sha256, byte_size |
| `document.released` | Release (together with cost_entry.confirmed, same tx) | released_by, sha256, retention_until |
| `document.superseded` | new voucher version | new_version_id, version |
| `document.integrity_verified` | Hash check run | result (ok/fail) |

- **Who/when/what** (SDD §6.6 traceability): `actor_user_id` from the session context (`uid`), `occurred_at` set by the server, `entity_type`/`entity_id`, `payload_diff` as JSONB.
- **Tenant binding:** `audit_event.organization_id` from the context; RLS active (M0). Append-only remains preserved through revoked UPDATE/DELETE grants (M0, `test_rls_coverage.py`).
- **Hash chain (optional, architecture.md §3.2d):** `prev_event_hash` is already in the schema (M0). **[DECISION]** whether the chain is already **populated/enforced** in M2 (each new event hashes the previous one per `organization_id`) or only hardened in M9. Recommendation: in M2 **fill the field consistently** (cheap: SHA-256 over the canonically serialized predecessor event per org), but leave verification/enforcement as M9 hardening; this way a gap-detecting chain exists from M2 on, without overloading M2. This is not a blocking point.

---

## 7. GoBD Requirements Mapping and GDPR Integration

### 7.1 GoBD → Building Blocks

| GoBD requirement (SDD §6.6, §16, §17) | How M2 satisfies it |
| --- | --- |
| **Immutability** | `BEFORE UPDATE/DELETE` triggers on `cost_entry` (from `confirmed`) and `invoice_document` (from `released_at`), + revoked DELETE grant on vouchers (ADR-0003). Correction only as a new version (§3.4/§4.4). |
| **Integrity** | SHA-256 per voucher (`content_sha256`), verified at release and by check run (§4.5); content-addressed storage (§5.2); later S3 Object Lock/WORM. |
| **Traceability** (who/when/what) | `audit_event` for all voucher/cost/approval operations (§6); `confirmed_by`/`released_by` directly on the entities; optional hash chain. |
| **Completeness** | Append-only version chains (no gaps through deletion); `status` field instead of hard delete; `rejected` is retained; the partial unique index "exactly one active" (§4.3) prevents orphaned/duplicate active states. |
| **Findability / machine evaluability** | structured, indexed metadata in the DB (property, vendor, date, service period, category); file findable by content address; export is a pure read query (no special format needed in M2). |
| **8-year retention** (BEG IV) | `retention_until` per voucher, set at release (§7.3). **Nothing** is removed before expiry. |
| **Procedural documentation (Verfahrensdokumentation)** (SDD §6.6) | This document + ADR-0003 + the documented state machine (§3) form the procedural documentation of the voucher flow (receipt → proposal → approval → settlement). |

### 7.2 GDPR Integration (architecture.md §3.3 Confirmed)

M2 does **not** change the concept laid down in M0/M1, but confirms and makes it concrete for vouchers:
- **A voucher remains subject to retention:** A released voucher (including the name/address of the invoice addressee contained in it) must be retained unchanged for 8 years. A GDPR erasure request does **not** erase the voucher; the statutory retention obligation is the legal basis against the erasure claim (restriction of processing instead of erasure).
- **Personal master data proper remain erasable/restrictable:** The `person` master data (M1: `deletion_requested_at`, `processing_restricted`, `anonymized_at`) are separate from the voucher and can be erased/anonymized/restricted without touching the voucher substance. `cost_entry` references the vendor via FK (`vendor_id`) — not by copying master data in —, so a later restriction of the master data does not break the voucher metadata.
- **Dividing line:** The addressee printed within the voucher file is part of the voucher (subject to retention); the master data proper held in `person` are erasable/restrictable. This separation is exactly architecture.md §3.3.

### 7.3 [OPEN] Retention Start Date Per Voucher Type (SDD §18)

`retention_until` needs a relevant start date + 8 years. **Which date applies per voucher type (invoice date vs. end of the fiscal/calendar year vs. finalization date) is explicitly open in SDD §18** and is **not decided unilaterally** here. M2 keeps the field and the calculation interface ready; the concrete rule is to be clarified with a tax advisor/specialist lawyer (before M9 at the latest).
- **Provisional default calculation for M2 tests, clearly marked as an assumption:** `retention_until = (end of the calendar year of `invoice_date`) + 8 years`. This is a conservative, common reading of the commercial/tax-law deadline calculation (the period begins with the end of the calendar year). **This assumption is provisional** and must be confirmed by the human/professional advisor — it appears here only so that the implementation has a testable function, not as a legal statement.

> **No self-certification:** The retention logic is made transparent and auditable; its legal correctness in an individual case must be signed off professionally.

---

## 8. Implementation checklist

> Architecture/correctness decisions are made in this document + ADR-0003. The implementer implements them and **escalates** only on an open specification question (SDD §18, marked here with **[OPEN]**) or an unresolved **[DECISION]**. **Prerequisite: the human has confirmed (a) A-1 (chart of accounts global vs. per org) and (b) the open [DECISION] points.** Do not commit before then.

### 8.1 Data Model + Migrations (Order Analogous to M0/M1)

- [ ] **Migration `0005_cost_category`:** table (§1.3) + complete seed of all SDD §7 categories with defaults (§1.6). With A-1=global: **no** `organization_id`, **no** RLS. With A-1=per org: `organization_id`, RLS migration, seed per org on creation.
- [ ] **Migration `0006_cost_entry_invoice_document`:** tables `cost_entry` (§2.2) and `invoice_document` (§4.2) with all CHECK constraints (status, distribution key, apportionability, `service_period_to >= service_period_from`), FKs (`RESTRICT` to `organization`, as in M1), indexes (§2.2/§4.2), `chain_root_id` + partial unique index `uq_active_doc_per_chain` (§4.3).
- [ ] **Grants:** `GRANT SELECT, INSERT, UPDATE, DELETE ON cost_entry TO app_rw`; `GRANT SELECT, INSERT, UPDATE ON invoice_document TO app_rw` (**no DELETE** — ADR-0003 backstop, confirm [DECISION]). `cost_category`: `GRANT SELECT ... TO app_rw` (global, read only).
- [ ] **Migration `0007_m2_rls_policies`:** for `cost_entry` (and `invoice_document`) the four policies exactly in the pattern of `0002`/`0004` (`org_isolation_select/insert/update/delete`, `NULLIF(... , '')::uuid`, fail-closed). `invoice_document`: **omit** the DELETE policy (no DELETE grant → analogous to the `audit_event` precedent).
- [ ] **Migration `0008_immutability_triggers`** (own, reviewable step — ADR-0003): PL/pgSQL `BEFORE UPDATE/DELETE` triggers for `cost_entry` (freeze from `confirmed`) and `invoice_document` (freeze from `released_at`), with a precise content-bearing-vs.-permitted column list. Trigger source thoroughly commented. `downgrade()` drops trigger + function.
- [ ] **Extend `TENANT_TABLES` in `tests/test_rls_coverage.py`:** `cost_entry`, `invoice_document` (and `cost_category` → `GLOBAL_TABLES` with A-1=global, otherwise `TENANT_TABLES`). `test_no_unknown_tables` forces this classification anyway.
- [ ] ORM models (`app/models/cost_category.py`, `cost_entry.py`, `invoice_document.py`) in the style of `lease_agreement.py` (mixins, `Numeric`, `Mapped`); money as `Decimal`.

### 8.2 Storage Module

- [ ] `app/core/storage.py`: `DocumentStore` protocol + `StoredObjectRef` (§5.1), `LocalFsDocumentStore` (content-addressed, organization-partitioned, read-only after writing), `get_store()/set_store()` (swap pattern as in `pdf.py`). **No `delete()`** in the protocol.
- [ ] `verify_document_integrity(doc, store)` (§4.5): reads the file, compares SHA-256 with `content_sha256`.
- [ ] Config entry (`settings`) for storage root/backend.

### 8.3 Extraction Interface (PDF Import) — Scaffold + Stub Only

- [ ] `DocumentExtractor` protocol (§3.5) + stub default (returns empty/partially filled `ExtractionResult` with confidences); the real OCR integration is an implementation task, the interface is fixed here.
- [ ] Result → `invoice_document.extraction_payload` (JSONB) + prefilling of the `cost_entry` fields; start status by confidence (`draft_extracted`/`needs_review`); **never** automatically `confirmed`.

### 8.4 State Machine + Services

- [ ] `app/services/cost_entry_service.py` with the allowed transitions (§3.2): `create`, `update_draft`, `confirm`, `reject`, `create_correction_version` (§3.4). Transitions that are not allowed → 409/422; the DB trigger is the second line.
- [ ] `confirm` as an **atomic** transaction (§3.6): mandatory field validation (2.3) → `cost_entry.confirmed` + voucher `released` + `retention_until` + hash verify + `audit_event`, all in one `session.begin()`.
- [ ] `app/services/invoice_document_service.py`: `upload` (file→store, SHA-256, row `active`/not released, `audit_event`), `create_new_version` (§4.4), `verify_integrity`.
- [ ] Repositories in the style of `base.py`/`audit_repo.py`: `CostCategoryRepository` (global, read only — no `organization_id` filter), `CostEntryRepository` (tenant, with active-chain query §3.4), `InvoiceDocumentRepository` (tenant, with active/version query §4.3, **no** `delete`).
- [ ] Writing `audit_event` via the existing `AuditRepository`; optionally fill `prev_event_hash` (§6, [DECISION]).

### 8.5 API (API-First, RBAC as in M0)

- [ ] `app/api/cost_categories.py`: `GET /api/cost-categories` (list, all roles can read).
- [ ] `app/api/cost_entries.py`: `GET` (list/detail), `POST` (create, `require_write_dep`), `PATCH` (edit draft, `require_write_dep`), `POST /{id}/confirm` (`require_write_dep`), `POST /{id}/reject` (`require_write_dep`), `POST /{id}/correction` (new version, `require_write_dep`). READ_ONLY: GET only. Direct access to another org's ID → 404 (not 403), as in M0.
- [ ] `app/api/invoice_documents.py`: `POST /upload` (multipart, `require_write_dep`), `GET` (list/metadata), `GET /{id}/content` (authenticated, organization-checked file stream — no public URL), `POST /{id}/version` (new version), `GET /{id}/verify` (integrity check). **No** DELETE endpoint.
- [ ] Register the router in `app/main.py`.

### 8.6 Tests That PROVE Immutability (Core of the M2 Acceptance)

In the style of `tests/test_tenant_isolation.py`/`test_rls_coverage.py`, with the existing fixtures (`migrator_session`, `app_rw_session`, `client_a_admin/clerk/readonly`, `client_b_admin`). These tests are the objective benchmark for I2:

1. **`cost_entry` confirmed is immutable (trigger):** via `app_rw_session` (org context set), change the `gross_amount` of a `confirmed` row with a direct `UPDATE` → DB raises an exception. Proves ADR-0003 at DB level, independent of the service.
2. **`cost_entry` confirmed: DELETE forbidden:** direct `DELETE` of a `confirmed` row → exception. Draft/`rejected` row: DELETE **allowed** (positive test, proves the status-dependent differentiation).
3. **`cost_entry` draft is editable:** `UPDATE` of a `needs_review` row → succeeds. (Positive test against over-locking.)
4. **`invoice_document` released is immutable:** `UPDATE` of `content_sha256`/`storage_uri` of a released row → exception; `active→superseded` → allowed (the only permitted UPDATE).
5. **`invoice_document`: no DELETE grant for `app_rw`:** analogous to `test_audit_event_no_update_delete_for_app_rw` → `information_schema.table_privileges` contains **no** `DELETE` for `app_rw` on `invoice_document` (+ existing `SELECT/INSERT/UPDATE`).
6. **Correction creates a new version (cost_entry):** `confirm` → `create_correction_version` → old row has `superseded_by_id` set + content unchanged; new row with the corrected value; the active query returns exactly the new row.
7. **Correction creates a new voucher version:** `upload`+release → `create_new_version` → old row `superseded`, new `active`, `supersedes_id` correct; the partial unique index allows **no** second `active` per chain (negative test: manual second `active` → unique violation).
8. **Hash verification:** `verify_document_integrity` is `True` for an unchanged file; an artificially modified file in the store (test manipulation) → `False`. Proves tamper evidence.
9. **Hash on upload≠release:** re-verify in the confirm transaction fails if the file differs between upload and release (transaction rolls back, no frozen inconsistent state).
10. **fail-closed / RLS (cost_entry, invoice_document):** session **without** `app.current_org_id` → `SELECT` returns zero rows; client A sees no `cost_entry`/`invoice_document` of org B; direct access to another org's voucher ID → 404. (Extends `test_tenant_isolation.py` with the M2 tables.)
11. **State machine:** disallowed transition (e.g., `confirmed → needs_review` via API) → 409/422; `confirm` without mandatory fields → 422 without status change; READ_ONLY may neither create nor approve → 403.
12. **RLS coverage extended:** `test_rls_coverage.py` green with the new tables (every tenant table RLS+FORCE+policies; `cost_category` correctly as global without RLS, if A-1=global).
13. **Atomicity of the approval:** if writing the `audit_event` in the confirm tx fails (simulated), `cost_entry` stays `needs_review` and the voucher stays unreleased (rollback).
14. **`retention_until` calculation** (with the **provisional-marked** assumption from §7.3): set at release, NULL before. The test checks the calculation, not legal conformity.

### 8.7 Definition of Done for M2 (Handover to M3)

- [ ] Migrations `0005`–`0008` as explicit, reviewable Alembic steps; `downgrade()` clean in each case.
- [ ] `cost_category` fully seeded according to SDD §7; A-1 decision implemented.
- [ ] `cost_entry`/`invoice_document` with RLS (four policies, fail-closed) + immutability trigger (ADR-0003) + grant backstop.
- [ ] Storage module (local, content-addressed, swappable protocol) + hash verification.
- [ ] State machine + atomic approval transaction; `audit_event` for all operations.
- [ ] API: upload, list, confirm, reject, version/correction, file stream, verify — RBAC correct, another org's ID → 404.
- [ ] **Immutability tests 1–14 green** (no UPDATE/DELETE on `confirmed`/`released`; correction = new version; hash verification; fail-closed/RLS) — in addition to the still-green 58 M0/M1 tests.
- [ ] M2 exit objectively met: upload PDF → proposal (`draft_extracted`) → confirm (`confirmed`) → archived immutably (trigger+hash) → relevant for settlement (active query). An end-to-end test covers this path.
- [ ] No open **[DECISION]**/**[OPEN]** left unmarked and "guessed away"; escalations documented.

---

### 8.8 Stored Letter Originals (T48)

Letter templates are editable plain text with a fixed placeholder vocabulary. For each
current tenant contact, issuing a letter freezes the subject, text, source reference, and
PDF reference in `letter_document`. RLS, missing UPDATE/DELETE privileges, and an
immutability trigger protect the originals. The existing content-addressed document
storage and SHA-256 verification are reused; template changes or new master data do not
re-render already issued PDFs. Admitting all recipients before enumeration prevents
unnoticed partial sets; downloads still check the original contact/source export
permissions. A renderer error rolls back all letter rows of the request. This is not a
new decision on the statutory retention period or proof of delivery. Migration 0132
refuses to remove populated template/letter tables.

## 9. What This Document Deliberately Does NOT Decide

- **Distribution/settlement logic (M3):** *how* a distribution key computes, pro rata periods from the service period, CO2/caretaker split. M2 stores only the selection/amounts/share fields; the engine is M3 (a correctness-critical design step).
- **Settlement snapshots (`settlement_run`/`settlement_line`):** M3/M4 — the same immutability trigger (ADR-0003 §3) is reused there.
- **E-invoice parsing (XRechnung/ZUGFeRD):** M5 — docks onto the state machine defined here as the second `DocumentExtractor` implementation.
- **Retention start date per voucher type:** SDD §18, **[OPEN]**, to be clarified on the domain side (§7.3).
- **Exact share/advance-deduction modeling** (caretaker, CO2, commercial): SDD §18, M3.
- **Hash chain hardening / GDPR erasure mechanics:** M9.

---

## 10. Summary of Open Decisions (for the Human)

| # | Item | Recommendation | When |
| --- | --- | --- | --- |
| **E-1** | `cost_category` global vs. per org (Assumption A-1) | **global, versioned** (no RLS) | before M2 impl |
| **E-2** | Immutability mechanics | **status-aware trigger + revoked DELETE grant on vouchers** (ADR-0003) | confirm before M2 impl |
| **E-3** | Revoke DELETE on `invoice_document` for `app_rw` entirely? | **yes** (backstop, never delete vouchers) | before M2 impl |
| **E-4** | Chaining direction `cost_entry` | **Forward (`superseded_by_id`)**; `invoice_document` stays backward (`supersedes_id`) | pin down at impl |
| **E-5** | `chain_root_id` + partial unique index "exactly one active" | **yes** (simple, code-base-conformant) | at impl |
| **E-6** | Default status on manual entry; `confirm` always an explicit second step | editable record, `confirm` never implicit | at impl |
| **E-7** | Concrete default distribution keys for SDD ambiguities ("area or MEA") | Owner usually `MEA`, tenant usually `WOHNFLAECHE`, overridable | domain decision, before seed |
| **O-1** | **[OPEN, SDD §18]** Retention start date per voucher type | not decided unilaterally; provisional test assumption (end of calendar year + 8 yrs) clearly marked; tax advisor/specialist lawyer | before M9 |
