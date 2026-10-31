# M4 Design: WEG Settlement — Economic Plan (Wirtschaftsplan), Annual Settlement with Settlement Balance (Abrechnungsspitze), Asset Report (Vermögensbericht)

Status as of: 2026-06-02 · Version 0.1 (design, before implementation)
Reference: [sdd-property-management-mvp.md](sdd-property-management-mvp.md) v0.2 §6.3, §6.9, §7.3, §8, §10, §11, §13, §14, §16, §17, §18 · [archived roadmap](archive/roadmap.md) M4 · [architecture.md](architecture.md) §3 (immutability), §5.1 (conventions), §5.3/§5.4 (entities) · [m3-abrechnungs-engine.md](m3-abrechnungs-engine.md) (engine, `run_type`, snapshot, golden tests)
ADRs for this document: [adr/0003-…](adr/0003-gobd-immutability-mechanism.md) (reused) · [adr/0004-pro-rata-daily-allocation.md](adr/0004-pro-rata-daily-allocation.md) (reused, M4 resolves the plan basis left open there) · [adr/0005-rounding-largest-remainder.md](adr/0005-rounding-largest-remainder.md) (reused) · **[adr/0007-weg-settlement-balance-and-economic-plan.md](adr/0007-weg-settlement-balance-and-economic-plan.md) (new)** · **[adr/0008-asset-report-mvp-scope.md](adr/0008-asset-report-mvp-scope.md) (new)**
Status: **Draft for confirmation by the human — no application code, no migration, no commit**

> This document specifies the **WEG outputs** (M4): the **economic plan** (forward-looking Hausgeld advance payments; Hausgeld = monthly owner contribution), the **Hausgeld/annual settlement with settlement balance (WEMoG, § 28 II WEG)**, and the **asset report (§ 28 IV WEG)**. M4 is the **owner side** of the settlement core; M3 (operating cost statement for tenants, 227 tests, verified live including PDF) is the tenant side. **M4 does not reinvent the distribution engine — it extends it with an owner mode.**
>
> **Binding force:** The SDD (v0.2), roadmap, `architecture.md`, `m3-abrechnungs-engine.md`, and ADRs 0001–0006 are the source of truth and remain **untouched**. This document is additive (new doc + ADR-0007/0008). Decisions the human should confirm are marked **[DECISION]**; open SDD §18 / domain points are marked **[§18/domain]** (not decided unilaterally; provisional, clearly marked assumption; domain acceptance recommended).
>
> **No self-certification:** This design makes the economic plan, annual settlement, settlement balance, maintenance reserve (Erhaltungsrücklage) development, and asset report *transparent, recomputable, and auditable* and discloses every assumption. It **cannot** guarantee that a specific WEG settlement is court-proof, audit-proof, or resolution-proof. The domain correctness (in particular the settlement balance under WEMoG, the separation of allocable (umlagefähig)/non-allocable/reserve, the scope under § 28 IV, the § 35a EStG disclosure) must be accepted by a **tax advisor (Steuerberater) / specialist attorney for WEG law** before productive use.

---

## Property settlement periods (#68)

Economic plans default to `property.fiscal_year_start_month` using the shared pure
period functions. `plan_year` identifies the start year: an April plan for 2025
runs from 1 April 2025 through 31 March 2026. Explicit `period_start` and
`period_end` are honoured, including short periods for a mid-year accounting scope.
Omitted dates default to the property period. The plan form prefills dates from
the property's period offers.

**Property access limitation:** a user without property read access must enter
the period; the period offer needs property access. Missing plan dates return
422 with "Abrechnungszeitraum angeben: Beginn und Ende", after pause, parent and
accounting-scope checks. Explicit dates need no access to the property's start
month; twelve-month and straddling validation still applies. The organization-bound
month getter is deferred to #95's migration. No migration is added here.

When the property is readable, explicit dates must fit within its period, or bridge exactly the gap
between the last finalized, non-superseded plan and the next property period.
For example, after finalizing 1 January–31 December 2025 and changing the start
month from January to April, a transition plan for 1 January–31 March 2026 closes
the gap before the regular 1 April 2026–31 March 2027 plan. The transition can be
finalized and used for a WEG settlement. Reversed periods, periods longer than
twelve months and dates outside these boundaries return 422.

Creating a plan returns 409 only when its period **straddles a boundary** of a
visible finalized, non-superseded plan for the same property: the periods overlap,
but neither fully contains the other. For finalized bounds `[a, b]` and new bounds
`[s, e]`, this means `a < s <= b < e` or `s < a <= e < b`, including overlap on a
boundary day. Fully contained, fully containing and identical periods are allowed,
including shared start or end dates, subject to the plan-date boundaries above.
Drafts never block creation. WEG annual runs use the same twelve-month validation
(422) and straddling-only rule (409), for their property, type and accounting
context. Superseded documents do not block creation. Checks use normal
row-level security, with the same [cross-scope limitation (#95) as statement
runs](m3-abrechnungs-engine.md#property-settlement-periods-68): an inaccessible
finalized document in another scope can remain undetected. No migration or
privileged helper is added here.

The previous-actuals preview and adoption use the preceding property period, including
its scope boundaries, source digest and day-based cost proration. Paid Hausgeld is
summed over the settlement's assigned months across calendar years using the ledger's
sub-period filtering. Booked, active payments only are included; the existing handling
of payments with a year but no month is narrowed in period-aware derivation: a
year-only payment belongs exactly to the period **starting** in its assigned year.
For an April property, year 2025 is included in 2025/2026 and excluded from
2024/2025. Calendar-year results remain unchanged; ledger filtering is unchanged. Paid advances
remain informational
and do not change the Abrechnungsspitze calculation. Changing the start month never
rewrites existing plans, settlement dates or finalized snapshots. The WEG statement
form initially adopts the selected plan's persisted dates, even if the property's
start month has changed since that plan was created.

**Deadline limitation:** the economic plan deadline in `fristen_service.py` still
uses calendar-year dates for non-January properties. Correcting it is a follow-up,
outside #68. The tenant-statement deadline already follows the property period.

## 0. Scope, Exit Criterion, Connection to M0–M3

**M4 exit (roadmap):** "Economic plan, annual settlement, and asset report for a WEG can be generated as PDF; **amounts are consistent**." This is the objective benchmark. "Consistent" is demonstrated by the **synthetic golden tests** (§9) with a **hand-calculated settlement balance** and by the **property invariants** (Σ owner shares == total costs; MEA (co-ownership share, Miteigentumsanteil) sums coherent; reserve development start + contribution − withdrawal == end).

**M4 builds on a verified foundation (M0–M3, 227 tests green) and adopts its conventions unchanged.** The most important finding from the code review: **the M3 foundation was already built to be M4-ready.** M4 is additive, not a rebuild.

| Existing foundation (M0–M3, live) | How M4 uses it |
| --- | --- |
| `settlement_run.run_type` **CHECK** already contains `WEG_JAHRESABRECHNUNG`, `WIRTSCHAFTSPLAN`, `VERMOEGENSBERICHT` (migration `0009`, reserved) | M4 implements these values; **no schema break, no new `run_type` migration**. |
| `settlement_run` columns `property_id`, `management_mandate_id`, `period_*`, `unit_id` (NULL for property-wide runs), `snapshot_payload`/`_sha256`, `status`, `superseded_by_id`, `engine_version` | M4 WEG runs are **property-wide** (`unit_id`/`lease_agreement_id`/`tenant_profile_id` = NULL); snapshot/hash/correction chain unchanged. |
| `settlement_line` with `line_type` **CHECK** (`COST`, `ADVANCE_PAYMENT`, `BALANCE`, `SUBTOTAL`), `distribution_key`, `total_amount`, `distribution_base_total/_share`, `amount`, `note` | M4 uses the same table; **the `line_type` vocabulary is extended additively** (§3.2, new migration only for CHECK + `addressee_*` columns). |
| `cost_entry.owner_distribution_key` (exists, unused in M3) | **M4 primary input** for the owner distribution per cost item. |
| `cost_category`: `owner_billing_relevant_default` (all `True`), `owner_distribution_key_default` (mostly `MEA`), `reserve_relevant` (**`True` only for `ERHALTUNGSRUECKLAGE`**), `cost_group` (`UMLAGEFAEHIG`/`GEMISCHT_PRUEFEN`/`NICHT_UMLAGEFAEHIG`) | The **separation of allocable/non-allocable/reserve** (§3, §6.3) follows directly from these already seeded fields. |
| `ownership_history` (temporal, EXCLUDE per `(unit, owner)`, `mea_share`, co-ownership + gaps allowed) | Owner addressee per day (day-accurate, ADR-0004); **co-ownership** via `mea_share` (§4). |
| Engine: `distribute_position` **already** distributes each position across **all (unit, time-slice) cells** of the whole property and **attributes vacancy cells to owners** (`vacancy_attribution`, `OwnerPeriodInput` are loaded) | M4 is the **mirrored addressee focus**: not "pick out one tenant cell" but **sum all owner cells per owner** (§4.3). The cell math (time × key, largest remainder) is **identical**. |
| `compute_bk_settlement` as a pure function; `SettlementService` with `create_draft`/`compute`/`preview`/`finalize`/`create_correction_run` (atomic, audit); `build_snapshot_payload` + `canonical_json` + `sha256_hex`; `render_settlement_pdf` (WeasyPrint, computes nothing) | M4 adds **`compute_weg_jahresabrechnung`**, **`compute_wirtschaftsplan`**, **`build_vermoegensbericht`** as pure functions and extends the service/PDF analogously (§10). |
| ADR-0003 trigger (`status='finalized'` → immutable), RLS (four policies, fail-closed), `TENANT_TABLES` coverage test | **Reused** — `settlement_run`/`settlement_line` already have the pattern; M4 adds **no new tables with trigger/RLS** except `economic_plan`/`economic_plan_line` (§1, same pattern). |
| `app/core/temporal.py` (`db_exclusive_to_engine_inclusive`, ADR-0006), day-accurate `periods.py`, `Decimal`/`NUMERIC(14,2)`/`NUMERIC(12,6)` | Adopted throughout, **same read boundary** for `ownership_history`. |

**Not in M4:** Real payments/tenant ledger (Mietkonto) (M6 — the owner's **actual advance payments (Ist-Vorschüsse)** are captured **manually** in M4, analogous to M3 `advance_payments_source='MANUAL'`, §2.4). Bank account balances with booking logic (M6 — in M4 the asset report uses a **manually captured key-date balance**, §6). Meeting/resolution register (Versammlung/Beschluss-Sammlung) (M8 — M4 produces **documents** for passing resolutions but does not pass resolutions itself, SDD §10). § 35a EStG disclosure (SDD §18, [§18/domain], §8.4).

**Architecture principle (SDD §16, strictly enforced here, as in M3):** The **calculation engine remains a layer of pure functions** (`Decimal` in/out, no DB/HTTP/I/O), separate from persistence (snapshot), API, and PDF layout. M4 logic goes into the same `app/engine/` package, not into services.

---

## 1. Data Model

### 1.1 [DECISION] Annual settlement + asset report as `settlement_run` `run_type` vs. separate entity — Recommendation: **same `settlement_run` envelope**

M3 §1.3 (E3-6) already recommended this and **reserved** it in the schema. M4 redeems it. Detailed reasoning:

**Recommendation: Annual settlement (`WEG_JAHRESABRECHNUNG`) and asset report (`VERMOEGENSBERICHT`) are `settlement_run` `run_type`s, not separate entities.**

- **Same immutability/snapshot/audit/correction mechanics.** Both are **finalized, legally binding documents that must be frozen** with exactly the same requirement as the operating cost settlement: immutable snapshot (`snapshot_payload` + `snapshot_sha256`), correction = new run (`superseded_by_id`), GoBD trigger (`status='finalized'`), RLS, audit trail. This is **done** (migrations `0009`–`0011`). A separate entity would have to duplicate the entire mechanism — exactly the anti-pattern that ADR-0003 §3 wanted to avoid.
- **The `run_type` vocabulary is already there** (CHECK in `0009`). No schema-break migration arises.
- **`settlement_line` carries both.** The annual settlement is structurally "total costs per cost category + MEA distribution + expected/actual (Soll/Ist) per owner" — the same line model as operating costs, plus a few new `line_type` values (§3.2). The asset report is a **statement of holdings** — it fits as a small set of `line_type` lines (reserve balance, bank account balance, …) plus a structured `snapshot_payload`; if `settlement_line` were too narrow for the report, the detail structure lives exclusively in the typed payload (the envelope stays the same).

**Accepted deliberately:** `settlement_run` thus carries three very different domain document types (expected/actual settlement, plan, holdings). This is acceptable because (a) `run_type` is the clean discriminator, (b) type-dependent validation lives in the service (not in the schema), (c) the common denominator — a *freezable, hashed, organization-isolated, correctable document* — is exactly what all three need. The alternative (three entities) triples triggers, RLS policies, coverage tests, and correction logic without any domain gain.

> **[DECISION E4-1]** Annual settlement + asset report as `run_type` of the same `settlement_run` envelope (recommended, M3-E3-6 line continued) vs. separate entities. Recommendation: **same envelope**. Confirm.

### 1.2 [DECISION] Economic plan as a separate entity `economic_plan` / `economic_plan_line` — Recommendation: **separate entity (SDD §11/§13 provides for `EconomicPlan`/`EconomicPlanLine`)**

The economic plan is domain-wise **something different** from a settlement and should **not** be forced into `settlement_run`:

- **Forward-looking, no expected/actual comparison.** It has **no** actual costs, **no** advance payment total, **no** balance, **no** `service_period` cost items. It has planned costs per category and, derived from them, **monthly Hausgeld advance payments per owner**. If it were placed in `settlement_run`, that table's core columns (`advance_payments_total`, `balance`, `lease_agreement_id`, …) would remain permanently NULL/meaningless — a sign of the wrong envelope.
- **SDD §11/§13 explicitly names `EconomicPlan`/`EconomicPlanLine` as separate entities.** The data model chapter lists them separately from `SettlementRun`/`SettlementLine`. M4 follows the SDD.
- **Different lifetime/relation to the settlement.** The plan of a year is the **expected-advance (Soll) source** for the settlement balance of the **same** year (§2). That is a clean FK relation `settlement_run.economic_plan_id → economic_plan.id`, not a mixture in one table.

**But: reuse the same cross-cutting mechanics.** `economic_plan`/`economic_plan_line` get the **same** pattern as `settlement_run`/`settlement_line`:
- `organization_id` + RLS (four policies, fail-closed, pattern `0010`),
- `status` (`draft`|`finalized`), `snapshot_payload` JSONB + `snapshot_sha256`, `engine_version`, `finalized_at/_by`, `superseded_by_id` (forward pointer, E-4),
- ADR-0003 trigger (`status='finalized'` → immutable; the only permitted mutation is `superseded_by_id`),
- `TENANT_TABLES` coverage test extended.

This gives the plan the **same immutability guarantee** (a resolved economic plan is a document that must stay frozen) without being forced into the settlement envelope.

> **[DECISION E4-2]** Economic plan as a **separate entity** `economic_plan`/`economic_plan_line` (recommended, SDD §11/§13) vs. a further `settlement_run.run_type`. Recommendation: **separate entity**, same snapshot/trigger/RLS mechanics. Confirm. *(The reserved `run_type='WIRTSCHAFTSPLAN'` stays unused in the CHECK — no harm; alternatively remove it in an additive migration, not necessary.)*

### 1.3 Schema `economic_plan` (new table, organization-scoped, RLS, immutable from `finalized`)

| Column | Type | Meaning / rationale |
| --- | --- | --- |
| `id` | `UUID` PK | `gen_random_uuid()`. |
| `organization_id` | `UUID NOT NULL` FK→`organization` (`RESTRICT`) | I1, RLS. |
| `property_id` | `UUID NOT NULL` FK→`property` (`RESTRICT`) | the WEG. |
| `management_mandate_id` | `UUID NULL` FK→`management_mandate` (`RESTRICT`) | WEG mandate (segregation-of-funds anchor). |
| `plan_year` | `INTEGER NOT NULL` | Fiscal year (usually calendar year; SDD §12). |
| `period_start`/`period_end` | `DATE NOT NULL` | Plan period (usually 01.01.–31.12.). `CHECK (period_end >= period_start)`. |
| `monthly_due_day` | `SMALLINT NULL` | Due day of the monthly Hausgeld (display/later Soll posting (Sollstellung) in M6); purely informational in M4. |
| `reserve_contribution_total` | `NUMERIC(14,2) NOT NULL DEFAULT 0` | planned **total reserve contribution** for the year (not a cost allocation, §3). |
| `status` | `String(12) NOT NULL DEFAULT 'draft'` | `draft`\|`finalized`. CHECK. Freeze condition `finalized`. |
| `snapshot_payload` | `JSONB NULL` | canonical, complete plan basis + result (§5.4). |
| `snapshot_sha256` | `String(64) NULL` | SHA-256 over canonical serialization (as in M3). |
| `engine_version` | `String(20) NOT NULL` | e.g., `m4.1.0`. |
| `finalized_at`/`finalized_by` | `TIMESTAMPTZ` / `UUID NULL` FK→`user` (`SET NULL`) | Audit. |
| `superseded_by_id` | `UUID NULL` FK self (`RESTRICT`) | Correction chain (E-4). |
| `created_at`/`updated_at` | `TIMESTAMPTZ` | as everywhere. |

Constraints: `CHECK (period_end >= period_start)`, `CHECK (status IN ('draft','finalized'))`. RLS four policies. Indexes: `organization_id`; `property_id`; `(property_id, plan_year)`; `superseded_by_id`. Grant: full CRUD (editable `draft`, trigger-only freeze — E3-5 line from M3).

### 1.4 Schema `economic_plan_line` (belongs to the plan's snapshot)

A line is **one planned item** — either a cost category plan, or the reserve contribution, or the per-owner split.

| Column | Type | Meaning |
| --- | --- | --- |
| `id` | `UUID` PK | |
| `organization_id` | `UUID NOT NULL` FK→`organization` (`RESTRICT`) | I1, RLS. |
| `economic_plan_id` | `UUID NOT NULL` FK→`economic_plan` (`RESTRICT`) | Membership. |
| `line_type` | `String(24) NOT NULL` | `PLANNED_COST` (planned cost category) \| `RESERVE_CONTRIBUTION` (reserve contribution, **not** a cost allocation) \| `OWNER_ADVANCE` (per-owner annual/monthly advance) \| `SUBTOTAL`. CHECK. |
| `cost_category_id`/`cost_category_code` | `UUID NULL` / `String(50) NULL` | for `PLANNED_COST`: the planned cost category (code denormalized, audit-proof). |
| `owner_profile_id` | `UUID NULL` FK→`owner_profile` (`RESTRICT`) | for `OWNER_ADVANCE`: the owner. |
| `unit_id` | `UUID NULL` FK→`unit` (`RESTRICT`) | for `OWNER_ADVANCE`: the unit (an owner can have several units → several lines). |
| `label` | `String(255) NOT NULL` | Display name. |
| `distribution_key` | `String(20) NULL` | for `PLANNED_COST`/`OWNER_ADVANCE`: applied key (usually `MEA`). CHECK against the key vocabulary. |
| `distribution_base_total`/`_share` | `NUMERIC(12,6) NULL` | Denominator/numerator (e.g., total MEA / MEA of the unit) — calculation path. |
| `planned_amount` | `NUMERIC(14,2) NOT NULL` | planned annual amount of the line (cost category plan, reserve contribution, owner annual share). |
| `monthly_amount` | `NUMERIC(14,2) NULL` | for `OWNER_ADVANCE`: monthly Hausgeld amount (§5.3, basis decision E4-3). |
| `cost_group` | `String(20) NULL` | `UMLAGEFAEHIG`\|`GEMISCHT_PRUEFEN`\|`NICHT_UMLAGEFAEHIG` (denormalized from `cost_category`, for the plan separation). |
| `sort_order` | `SMALLINT NOT NULL DEFAULT 0` | fixed PDF order. |
| `note` | `String(500) NULL` | Note. |
| `created_at` | `TIMESTAMPTZ` | |

Constraints: `CHECK (line_type IN (...))`; `CHECK (distribution_key IS NULL OR distribution_key IN (...))`. RLS four policies. Indexes: `organization_id`; `economic_plan_id`; `(economic_plan_id, sort_order)`. Grant: full CRUD (trigger-only freeze via parent plan status, exactly like `settlement_line` in `0011`).

### 1.5 Link between annual settlement ↔ economic plan (for the settlement balance)

The settlement balance is `actual − expected`, and the **Soll** is the **resolved economic plan advance payments** of the same year (§2). So that the finalized settlement snapshot refers to its Soll source **audit-proof**:

- New column **`settlement_run.economic_plan_id`** `UUID NULL` FK→`economic_plan` (`RESTRICT`), set for `WEG_JAHRESABRECHNUNG` (NULL for `BK_MIETER`). Additive migration (§10.2). Rationale for `RESTRICT`: a plan that a finalized settlement refers to must not be deleted (GoBD chain).
- Additionally, the **expected advance payments per owner are frozen as a copy** into the settlement `snapshot_payload` (not just the plan ID), so that the settlement stays reproducible **without** recourse to the plan (snapshot philosophy, M3 §1.2). The plan reference (`economic_plan_id`) is the audit/display link; the frozen copy is the calculation/reproduction basis.

> **[§18/domain F4-1] Soll source when no plan was resolved.** There can be WEGs without a formally resolved economic plan for the settlement year (e.g., a new WEG, a late resolution). What is the "Soll" then? Options: (a) Soll = the **actually paid** advance payments (then the "balance" = actual costs − payments made, which approaches a pure expected/actual settlement); (b) mandatory field — no annual settlement without a stored plan. **Not decided unilaterally.** Provisional assumption for M4 test cases: `economic_plan_id` is **required** for `WEG_JAHRESABRECHNUNG`; the expected advance payments per owner come from the plan (captured at least as `draft`). Domain clarification (tax advisor/specialist attorney) before production. See ADR-0007.

### 1.6 What M4 does **not** touch in the existing schema

`settlement_run`/`settlement_line` stay structurally as in M3; M4 adds **additively**: (a) `settlement_run.economic_plan_id` (§1.5), (b) extended `line_type` CHECK on `settlement_line` (§3.2), (c) optional `settlement_line.addressee_owner_profile_id` for the owner assignment (§4.4). Everything via **new** migrations (`0012`+), never by changing `0009`–`0011` (additive discipline, as established by M3).

---

## 2. Hausgeld/Annual Settlement with Settlement Balance (WEMoG) — Core Correctness

This is the most correctness-critical part of M4, analogous to the day-accurate tenant-change logic in M3. It is **legally specific** and easily done wrong (hence the dedicated ADR-0007).

### 2.1 What the Settlement Balance Is Legally (post-WEMoG, § 28 II WEG)

Before the 2020 WEG reform, the **entire annual settlement** was resolved. **Since the WEMoG (§ 28 para. 2 WEG, in force since 01.12.2020)**, **only the adjustment of the advance payments — the "settlement balance" (Abrechnungsspitze) — is the subject of the resolution:**

```
settlement balance (per owner)
   = proportional ACTUAL costs of the owner   (per distribution key/MEA, day-accurate)
   − resolved EXPECTED advances of the owner  (per economic plan of the same year)
```

- **Settlement balance > 0 → additional payment (Nachschuss)** (the owner owes more than the plan provided for).
- **Settlement balance < 0 → adjustment credit / reduction** (the plan Soll budgeted more for the owner than the actual costs came to).

**Important domain distinction (the most common mistake):** The settlement balance is **`actual − expected advance`**, **not** `actual − actually-paid advances`. What the owner has **actually** paid (or not paid) is a **separate** quantity (payment arrears/credit on the Hausgeld account), which M4 shows **for information only** but does **not** mix with the resolution-relevant settlement balance. The subject of the resolution is the balance against the **Soll**; the payment status is an enforcement/dunning matter (M6/M7).

> **[§18/domain F4-2]** This `actual − expected` definition of the resolution-relevant balance is the one customary under the prevailing opinion on the WEMoG; the exact presentation (what counts as "resolved", what as "for information") and the handling of an unresolved plan (F4-1) must be domain-accepted (specialist attorney for WEG law). ADR-0007 records the definition and the assumption; **no self-certification.**

### 2.2 What the Annual Settlement Discloses per Owner

The settlement discloses, per owner, in a **structured** way (in `settlement_line`, not only in the PDF) — so that "consistent" (M4 exit) is machine-checkable:

| Disclosed item | Model representation | Remark |
| --- | --- | --- |
| **Total costs per cost category** (property-wide) | `settlement_line(line_type='COST').total_amount` | Total of the WEG per category. |
| **Applied key per item** | `settlement_line.distribution_key` (+ `_base_total`/`_share`) | Usually `MEA`; category-specific possible (§4). |
| **Owner's MEA share per cost category** | `settlement_line.amount` (rounded, ADR-0005) | the amount attributed to the owner. |
| **Sum of the owner's proportional actual costs** | `settlement_line(line_type='OWNER_COST_TOTAL')` (new, §3.2) + `settlement_run.owner_cost_total` (in the payload) | only **allocable + non-allocable** WEG costs; **excluding** reserve contribution (that is not a cost, §3). |
| **Owner's expected advance payments** (per plan) | `settlement_line(line_type='PLAN_ADVANCE')` (new) | from `economic_plan` (§1.5), copied into the snapshot. |
| **Settlement balance (additional payment/credit)** | `settlement_line(line_type='ABRECHNUNGSSPITZE')` (new) + payload `abrechnungsspitze` | = proportional actual costs − expected advance payments (§2.1). **The amount relevant for the resolution.** |
| **Owner's reserve contribution** | `settlement_line(line_type='RESERVE_CONTRIBUTION')` (new) | disclosed separately, **not a cost item** (§3). |
| **Reserve development** (property-wide) | dedicated `RESERVE_*` lines (§3.3) | Opening/contribution/withdrawal/closing. |
| **(For information) advance payments actually made / payment status** | `settlement_line(line_type='ADVANCE_PAYMENT')` optional + payload | **separate** from the balance (§2.1); in M4 **manual** (§2.4), M6 `DERIVED`. |

### 2.3 Separation of Allocable / Non-Allocable / Reserve (SDD §6.3, §7.3, §11)

Unlike the operating cost tenant settlement (which shows only allocable costs (umlagefähig)), the **owner settlement discloses all three groups**, but **separately** — `cost_category.cost_group` supplies the group directly (already seeded):

- **Allocable (`cost_group='UMLAGEFAEHIG'`)** and **mixed (`GEMISCHT_PRUEFEN`)**: ongoing management costs. For the owner settlement, usually the **`gross_amount`** is distributed (the owner bears the total costs proportionally; the question "which part can be passed on to tenants" belongs to the **tenant** sphere M3, not the owner sphere). [§18/domain F4-3, §4.5]
- **Non-allocable (`NICHT_UMLAGEFAEHIG`)**: administration, maintenance/repair (Instandhaltung/Instandsetzung), legal advice, bank fees, etc. They appear in the **owner** settlement (proportionally), never in the tenant operating cost statement. `owner_billing_relevant_default=True` is seeded for all of them.
- **Reserve (`reserve_relevant=True`, only `ERHALTUNGSRUECKLAGE`)**: the contribution is **not a cost allocation** but **asset building** (§3). It is disclosed **separately** and **not** included in the "proportional actual costs" sum of the settlement balance (§3.2).

The engine sums **three subtotals** per owner (`SUBTOTAL` lines): Σ allocable, Σ non-allocable, Σ reserve contribution — exactly as SDD §11 requires for owner settlements.

### 2.4 Origin of the Expected Advance Payments and (for Information) the Actual Payments — M4 Plan Soll + Manual

- **Expected advance payments (resolution-relevant):** from the linked `economic_plan` (§1.5), per owner/unit, copied into the snapshot. This is the **authoritative** quantity for the settlement balance.
- **Actual payments (for information):** payments do not exist yet (M6). As in M3, a **manually captured** amount per owner is kept (`advance_payments_source='MANUAL'`), which is shown **for information only** (payment status) — it does **not** flow into the resolution-relevant balance (§2.1). From M6 `DERIVED` from real `Payment`s.

### 2.5 Lifecycle, Snapshot, Correction — Identical to M3

`draft → finalize → finalized` (immutable, ADR-0003). Correction = new `WEG_JAHRESABRECHNUNG` run (`superseded_by_id`). `finalize` is **a single atomic transaction** (final engine calculation → freeze expected advance payments → `snapshot_payload` → `snapshot_sha256` → (re)write `settlement_line` → `status='finalized'`/`finalized_at/by` → `audit_event`). This is the **same** `SettlementService.finalize` structure as in M3, only with the WEG compute. Determinism/hash stability as in M3 (ADR-0005).

---

## 3. Maintenance Reserve — Contribution Is Not a Cost Allocation; Disclose Development

### 3.1 Principle: Asset Building, Not Consumption

The **contribution** to the maintenance reserve (formerly "Instandhaltungsrücklage") is **not a management/cost item** but a **saving of common assets**. It is **owner-only** (tenants never bear it — `tenant_apportionability_default='NICHT_UMLAGEFAEHIG'` is seeded for `ERHALTUNGSRUECKLAGE`) and is disclosed **separately** in the annual settlement. It must **not** flow into the "proportional actual costs" sum that enters the settlement balance — otherwise asset building would be counted as consumed costs (domain error).

> **Model consequence:** A `cost_entry` of category `ERHALTUNGSRUECKLAGE` (`reserve_relevant=True`) is treated by the WEG engine **as a reserve contribution** (dedicated `RESERVE_CONTRIBUTION` line), not as `COST`. The distribution to the owners still follows the key (usually MEA), so that each owner sees their contribution share — but it counts into a **different** sum.

### 3.2 New `line_type` Values (Additive CHECK Extension on `settlement_line`)

Migration `0012` extends the `line_type` CHECK additively (old values remain valid):

| new `line_type` | Meaning |
| --- | --- |
| `OWNER_COST_TOTAL` | Sum of the owner's proportional actual costs (allocable + non-allocable, **excluding** reserve) — the minuend of the balance. |
| `PLAN_ADVANCE` | Owner's expected advance payment per economic plan — the subtrahend of the balance. |
| `ABRECHNUNGSSPITZE` | `OWNER_COST_TOTAL − PLAN_ADVANCE` — the resolution-relevant additional payment/credit. |
| `RESERVE_CONTRIBUTION` | Owner's contribution share to the maintenance reserve (separate). |
| `RESERVE_OPENING` | Opening balance of the reserve (property-wide, holdings line). |
| `RESERVE_WITHDRAWAL` | Withdrawal from the reserve during the year (property-wide). |
| `RESERVE_CLOSING` | Closing balance of the reserve (property-wide). |

Existing `COST`/`ADVANCE_PAYMENT`/`BALANCE`/`SUBTOTAL` remain for operating costs (M3) and for subtotals. **No change** to M3 behavior (operating costs do not use the new types).

### 3.3 Reserve Development (Opening Balance → Contribution → Withdrawal → Closing Balance)

The annual settlement (and the asset report, §6) disclose the **development** of the maintenance reserve — a hard consistency invariant:

```
closing balance = opening balance + Σ contributions − Σ withdrawals
```

- **Opening balance** (`RESERVE_OPENING`): reserve balance at the start of the period. **[§18/domain F4-4]** Data source: manually captured key-date value (M4) or closing balance of the prior-year settlement (once prior-year runs exist). In M4 captured **manually** on the run, until M6 makes bank account balances real.
- **Σ contributions**: sum of all `ERHALTUNGSRUECKLAGE` `cost_entry`s in the period (= Σ `RESERVE_CONTRIBUTION` across all owners).
- **Σ withdrawals**: withdrawals from the reserve (e.g., to finance a repair). **[§18/domain F4-5]** In M4 captured **manually** (there is no booking logic yet that automatically marks a repair `cost_entry` as a reserve withdrawal). Provisional: manual withdrawal entry on the run; domain clarification of the link between withdrawal and financed measure before production.
- **Closing balance** (`RESERVE_CLOSING`): computed; the engine **checks** the invariant and escalates on inconsistency (fail-loud, no silent netting).

The reserve is **owner-only**: it appears exclusively in WEG/owner documents, never in the tenant operating cost statement.

> **[§18/domain F4-6] Interest / multiple reserves.** Some WEGs maintain several reserves or interest-bearing accounts. M4 MVP: **one** maintenance reserve (`reserve_relevant`), no interest posting. Extension later; recorded here, not guessed away.

---

## 4. Owner Distribution — MEA Primary, Category-Specific Keys, Day-Accurate, Co-Ownership

### 4.1 MEA as the Primary Key

WEG costs are in principle distributed by **co-ownership share (MEA)** (§ 16 II WEG). The data source is `unit.mea` (weight per unit) or, for co-ownership, `ownership_history.mea_share` (split of the unit's MEA among several fractional owners, §4.3). This is exactly the `MEA` key that the M3 engine already implements (`_unit_weight(unit, "MEA")` = `unit.mea`).

### 4.2 Category-Specific Keys (`cost_entry.owner_distribution_key`)

The WEG can set a **different** key than MEA for individual cost categories **by resolution/declaration of division** (Teilungserklärung) (§ 16 II 2 WEG permits deviating distribution). The model already carries this: **`cost_entry.owner_distribution_key`** (per item) and `cost_category.owner_distribution_key_default` (default per category, mostly `MEA`, for consumption costs `VERBRAUCH`/`EXTERNE_ABRECHNUNG` — seeded). For the WEG settlement, M4 uses the **`owner_distribution_key`** (analogous to M3, which uses `tenant_distribution_key`). The same key vocabulary (`MEA`, `WOHNFLAECHE`, `BEHEIZTE_FLAECHE`, `EINHEIT`, `PERSONENZAHL`, `VERBRAUCH`, `EXTERNE_ABRECHNUNG`, `MANUELLER_BETRAG`) — **the engine can already do all of them.**

### 4.3 Day-Accurate Owner Change + Co-Ownership

- **Owner change within the year** (sale): day-accurate via `ownership_history` (ADR-0004). The seller bears their days, the buyer theirs. This is the **mirrored** tenant-change logic from M3 §3 — the same `time_factor`/cover math, only over `OwnerPeriodInput` instead of `LeasePeriodInput`. **[DECISION E4-4, §4.6]** whether, on an owner change during the year, **one** run per owner period or **one** run with both addressee lines arises (analogous to M3-E3-3).
- **Co-ownership (several `owner_profile`s per unit simultaneously):** `ownership_history` allows several simultaneous owners per unit with `mea_share` (model comment confirms: "Multiple co-owners per unit at the same time ARE allowed"). The MEA share of the **unit** is first used as the weight in the property-wide distribution; the unit's share is then **further distributed by `mea_share`** among the fractional owners (second, exact stage, then largest remainder). This is the **one** additional stage compared to M3 (which could ignore fractional ownership for the tenant operating costs, M3 §2.2).

> **[§18/domain F4-7] `mea_share` sum.** The model does **not** strictly enforce that `Σ mea_share` per unit/point in time == unit MEA (soft validation, real-world data). The M4 engine checks **defensively** and escalates on gross inconsistency (e.g., Σ `mea_share` = 0 when co-ownership exists), but continues to compute normalized for a plausible, slightly deviating sum (documented). Which tolerance is domain-permissible must be accepted.

### 4.3.1 Operating Flow Since Phase 2 (T26)

In the unit, "Eigentum beenden" (end ownership) ends the selected open history row.
`PATCH /api/ownership/{id}` accepts only `valid_to` as the inclusive last day; internally
the upper bound stays exclusive. The follow-up entry receives the next day and the previous
MEA share, but no automatically selected owner. Only its deliberate selection and saving
creates a new row. The write role, existing prepare/source permissions, and the
financial-input lock apply; co-ownership is not banned across the board. Historical master
data and booked payments are not overwritten, finalized settlements remain frozen.

For drafts, `GET /api/weg-settlements/{id}/input-owners` returns the complete authorized
owner set of the settlement period, with READ scope and disclosure checks. The UI uses the
same set as the coverage validation, not merely the advance payment lines of the plan.
Payment details start as unknown; zero amounts require an explicit entry and completeness
confirmation. A seller until 30.06.2026 and a buyer from 01.07. receive 181/184 EUR for
actual annual costs of 365 EUR. This flow is verified in the browser, including voucher
release, finalization, reload, and an opened PDF; the plan is not subsequently changed in
the process.

### 4.4 Addressee Model for the Owner Distribution (Engine Mirror of M3)

The key insight: `distribute_position` **already today** generates, for each item, all (unit, time-slice) cells across the entire property and knows **owner cells** (vacancy → `vacancy_attribution` with `owner_profile_id`). For M4 the **addressee focus** shifts:

- **M3 (tenant operating costs):** "Pick out the cells of the **target lease**; attribute vacancy cells to owners."
- **M4 (WEG annual settlement):** "Distribute each item across all **owner** periods (`ownership_history` cover per unit) by `owner_distribution_key`; sum per `owner_profile`."

This requires **no** new distribution algorithm but a **second cover function** and a **second orchestrator**:
- `build_owner_cover(unit, period, ownership_history)` — analogous to `build_unit_cover`, but the slices are **owner periods** (no tenant/vacancy split; for co-ownership several simultaneous slices with `mea_share`). Day-accurate, gapless (a unit in the WEG always has an owner — a true ownership "gap" would be a data error → escalation).
- `compute_weg_jahresabrechnung(inp)` — distributes, sums per owner, forms subtotals (allocable/non-allocable/reserve), subtracts the expected advance payment → settlement balance; builds the reserve development.

A new optional column `settlement_line.addressee_owner_profile_id` `UUID NULL` FK→`owner_profile` (`RESTRICT`) marks the owner affiliation of a line (NULL for property-wide lines and for operating costs).

### 4.5 [§18/domain F4-3] Owner Sphere: gross_amount vs. Tenant Share

For allocable/mixed cost categories, the **owner** settlement usually distributes the **`gross_amount`** (the owner bears the total costs proportionally by MEA; whether/how much they can pass on to their tenant as landlord is the separate tenant sphere M3). M4 does **not** use `apportionable_tenant_amount` for the owner distribution. For `GEMISCHT_PRUEFEN` (caretaker, Hausmeister), the WEG bears the total costs; the split into allocable/non-allocable is relevant for the **tenant** operating cost statement, not for the WEG-internal MEA distribution. **This assumption must be domain-accepted** (special cases: agreed deviating cost bearing, § 16 II WEG).

### 4.6 [DECISION E4-4] One Run per Owner vs. One Run with All Owners

**Recommendation: one `WEG_JAHRESABRECHNUNG` run per WEG (property-wide), with per-owner lines** (`addressee_owner_profile_id`). Rationale: The WEG annual settlement is, domain-wise, **one** overall document for the community (total costs, total distribution, reserve development), from which the **individual settlement** per owner is generated as a view/PDF. This deliberately differs from M3 (there: one run **per tenant**, because each tenant receives a separate document to be delivered to them and there is no "overall operating cost document"). On an **owner change during the year**, the unit gets two owner line groups (seller/buyer, day-accurate) in the same run. The per-owner PDF filters on `addressee_owner_profile_id`. **Confirm.**

> Schema consequence: `settlement_run` for `WEG_JAHRESABRECHNUNG` has `unit_id`/`lease_agreement_id`/`tenant_profile_id` = NULL (property-wide); the owner assignment lives in `settlement_line.addressee_owner_profile_id`.

---

## 5. Economic Plan — Forward-Looking

### 5.1 Inputs (SDD §10)

- **Planned costs per category**: either **prior-year actuals** (sum of the category's `cost_entry`s in the prior year, as a suggestion) or a **manual plan value**. M4 offers both; the authoritative value is the confirmed plan value per `economic_plan_line(line_type='PLANNED_COST')`.
- **Planned reserve contribution** (`economic_plan.reserve_contribution_total`) — separate, not a cost allocation (§3).
- **Distribution key per cost category** (usually MEA; category-specific possible, §4.2).

T54 refines the prior-year template: the currently confirmed full WEG gross amount by
service period, multi-year items by inclusive days per item, HALF_UP to the exact cent;
credits netted, superseded/unconfirmed items excluded. Reserve-relevant categories yield
only a separately confirmable reserve proposal. Negative net categories, inactive or
ambiguous versions are not silently omitted. Existing M4 plans distribute exclusively by
MEA; historical cost distribution keys are not adopted automatically. Prior-year read and
target-year prepare are separate, complete permission checks. The confirmed prefill is
persisted in the uncomputed draft and remains editable after reload; the source digest and
original IDs are retained as provenance in the later normal computation. No automatic
data-basis attestation, cost change, finalization, or resolution.

### 5.2 Outputs (SDD §10)

- **Plan amount per category** (`PLANNED_COST` lines, + subtotals allocable/non-allocable/reserve).
- **Share per unit/owner** (`OWNER_ADVANCE` lines, by MEA).
- **Monthly Hausgeld proposal** per owner (`monthly_amount`, §5.3).
- **Share of allocable/non-allocable costs** and **reserve share separately** (via `cost_group` + `RESERVE_CONTRIBUTION`).

The economic plan is **not** a WEG resolution — it produces proposals/documents for passing a resolution (SDD §10); the resolution is documented in M8 (resolution register, Beschluss-Sammlung).

### 5.3 [DECISION E4-3] Basis of the Advance Payments — Month-Based vs. Day-Accurate

**ADR-0004 explicitly left this open:** "For **planned** monthly Hausgeld advance payments (not actual cost distribution), a month-based basis may be domain-wise desired (12 equal monthly installments). This is a **different** quantity than the actual cost distribution and is decided separately in M4." M4 now decides it:

**Recommendation: month-based for the plan (12 equal monthly installments); day-accurate remains the basis for actual costs.**

```
annual_advance_owner    = (Σ planned costs + planned reserve contribution) * MEA share
monthly Hausgeld amount = annual_advance_owner / 12   (commercially rounded, to the cent)
```

Rationale: Hausgeld is a **monthly constant installment payable in advance** — the practical and domain-wise expected basis is **12 equal monthly installments**, not day-accurate daily amounts (unlike the retrospective actual cost distribution of the annual settlement, which remains day-accurate — ADR-0004). An owner change during the year does not affect the **plan** (the plan plans for the unit; who pays in which month is a payment/transition question for M6, possibly with a notarial cost arrangement on sale). The **12-installment rounding** runs through the same `distribute_amount` discipline (largest remainder over 12 installments, so that Σ monthly installments == annual advance exactly — no cent drift over the year).

> **[DECISION E4-3]** Plan advance payments **month-based** (12 equal installments, recommended) vs. day-accurate. ADR-0007 records the decision + rationale + the explicit separation "plan = month-based / actual settlement = day-accurate". Confirm.

### 5.4 Economic Plan Snapshot

`economic_plan.snapshot_payload` is canonical like the M3 payload: frozen plan inputs (planned costs per category, reserve contribution, keys, MEA basis per unit) + result (per-owner annual/monthly advance). Hashed (`snapshot_sha256`) → a resolved/finalized plan is reproducible and immutable (same mechanics as M3 §1.8).

---

## 6. Asset Report (§ 28 IV WEG) — MVP Scope [§18/domain, not unilateral]

### 6.1 What § 28 IV WEG Requires

Since the WEG reform, the administrator (Verwalter) must, **after the end of the calendar year**, prepare an **asset report** containing:
1. the **status of the maintenance reserve**, and
2. a **statement of the essential common assets** (essentially: bank account balances, receivables, and liabilities of the community).

SDD §11 confirms: "The asset report contains **at least** the status of the maintenance reserve and a statement of the essential common assets (receivables/liabilities of the community)."

### 6.2 What M4 Can Deliver Cleanly Now vs. What Stays Manual/Later

A **strict delimitation** is called for here — the current data state (M0–M3) determines what works automatically. **I am not deciding the domain scope unilaterally** (SDD §18, ADR-0008):

| Component (§ 28 IV) | Data situation today | M4 MVP handling |
| --- | --- | --- |
| **Status of the maintenance reserve** | **Available/derivable** (§3.3: opening balance + contributions − withdrawals = closing balance; contributions from `ERHALTUNGSRUECKLAGE` `cost_entry`s). | **Works now** — from the reserve development (§3.3). Opening balance/withdrawals manual in M4 (F4-4/F4-5). |
| **Bank account balances** | **`bank_account` exists as an M1 anchor but has NO balance/booking columns** (model comment: "model anchor: schema + FK only; full implementation in M6"). No bank statement import (SDD non-goal). | **Manual key-date balance per account** in M4 (input path §6.3); real balances with booking logic only in **M6**. **[§18/domain F4-8]** |
| **Receivables** (e.g., Hausgeld arrears) | **Not modeled** (open items = `OpenItem` is M6/M7). | **Manual entry** or **open** in M4 (§6.4). **[§18/domain F4-9]** |
| **Liabilities** (e.g., outstanding supplier invoices) | **Not directly modeled** (a `cost_entry` is a cost item, not a payment status; payments M6). | **Manual entry** or **open** in M4 (§6.4). **[§18/domain F4-9]** |

### 6.3 Input Path for Bank Account Balances in M4 (Proposal, Not Unilaterally Final)

`bank_account` has no balance column. Two clean options, **without** anticipating M6:

- **(A) Key-date balance in the asset report snapshot (recommended for the M4 MVP):** When creating the `VERMOEGENSBERICHT` run, the clerk (Sachbearbeiter) captures a **manual key-date balance** (as of period end) per `bank_account`. This is frozen into the `snapshot_payload` and disclosed as a `BANK_BALANCE` line. **No** schema addition to `bank_account` (that belongs to the M6 booking logic). Advantage: does not anticipate M6; the report remains a frozen key-date voucher.
- **(B) Balance column/movement table on `bank_account`:** premature — that is M6 scope (payments/tenant ledger (Mietkonto)). **Not** in M4.

> **[§18/domain F4-8]** Input path for bank account balances: recommendation (A) manual key-date balance in the report snapshot. Domain acceptance needed on whether a manually captured key-date balance suffices for § 28 IV in the MVP (it is an administrator statement without automatic account reconciliation — bank import is explicitly a non-goal of phase 1, SDD §3). ADR-0008.

### 6.4 Receivables/Liabilities in M4

**[§18/domain F4-9] — not decided unilaterally.** Since neither open items (`OpenItem`, M6/M7) nor the payment status of supplier invoices (M6) are modeled, M4 **cannot determine** receivables/liabilities **automatically**. Options, clearly put up for domain decision:
- **(a) Manual capture** as free items in the asset report run (`RECEIVABLE`/`PAYABLE` lines, frozen into the snapshot) — the MVP-pragmatic way.
- **(b) Deliberately postpone to later (M6/M7)** and show in the asset report a **clearly marked placeholder** („Forderungen/Verbindlichkeiten: in dieser Ausbaustufe manuell zu ergänzen / folgt mit M6" — receivables/liabilities: to be added manually at this stage / follows with M6) instead of feigning an incomplete automatism.

**Recommendation (for confirmation):** **(a) manual capture** in the report snapshot for the MVP, with a clear note in the PDF that these values are **administrator statements** and not an automatic receivable/liability balance derived from bookings. This way § 28 IV is formally servable without feigning correctness that the data cannot provide. **Domain acceptance required** on whether that suffices for the MVP.

### 6.5 Asset Report Data Model

As `settlement_run` `run_type='VERMOEGENSBERICHT'` (§1.1), property-wide (`unit_id` etc. NULL), with holdings `settlement_line`s (additive `line_type` values, §3.2/§6): `RESERVE_OPENING`/`RESERVE_CONTRIBUTION`(Σ)/`RESERVE_WITHDRAWAL`/`RESERVE_CLOSING`, and for the report: `BANK_BALANCE`, `RECEIVABLE`, `PAYABLE` (in the same CHECK extension). The `snapshot_payload` carries the complete, frozen statement. **No** expected/actual (Soll/Ist) logic (no `balance`).

> **[§18/domain F4-10] Key date/period of the asset report.** § 28 IV: "after the end of the calendar year". M4 takes the **key date = `period_end`** (usually 31.12.). Whether a deviating fiscal year is permissible/customary must be domain-accepted.

---

## 7. Mixed Property — Cleanly Separate Owner Side (M4) and Tenant Side (M3)

A mixed property (`property.management_type='MISCH'`) remains **one** property; the same unit produces **both** settlements **independently** (SDD §5):

- **Tenant side (M3, done):** per rented unit one `BK_MIETER` run per lease (allocable costs only, `tenant_distribution_key`, `apportionable_tenant_amount`). Vacancy → owner residual item (M3 `vacancy_attribution`).
- **Owner side (M4):** **one** `WEG_JAHRESABRECHNUNG` run for the whole property (all cost groups, `owner_distribution_key`/MEA, settlement balance, reserve), per-owner lines.

**Separation discipline (invariant I4-M):** The same `cost_entry` flows into **both** spheres, but via **separate** fields and **separate** engine modes:
- The tenant sphere reads `tenant_distribution_key` + `tenant_apportionable` (+ possibly `apportionable_tenant_amount`) → M3 engine.
- The owner sphere reads `owner_distribution_key` (+ `cost_group`/`reserve_relevant`) → M4 engine.
- A `NICHT_UMLAGEFAEHIG` item (e.g., administrator costs) appears in the WEG settlement (owner), **never** in the tenant operating cost statement — this is already M3 invariant I-F and is mirrored in M4: reserve contribution and non-allocable costs **never** in `BK_MIETER` lines.

There is **no** artificial splitting of the property; the two runs are independent `settlement_run`s with different `run_type`, both from the same property, both from the same `cost_entry`s, but via disjoint fields.

---

## 8. PDF Outputs (WeasyPrint Pipeline)

Three new PDFs via the **WeasyPrint** pipeline created in M0 and proven in M3 (`app/core/pdf.py`, `render_pdf`). **Strictly separate from the engine** (SDD §16): the PDF layer reads only the **finalized** result (lines + snapshot/display names), recomputes **nothing** (test: PDF values == line values, like M3 `settlement_pdf`).

### 8.1 Hausgeld/Annual Settlement (per Owner)
Formal WEG requirements (SDD §11): property + settlement period; owner + unit(s); **total costs per cost category** (property-wide); **applied key** per item; owner's **MEA share** per cost category; **separate subtotals** allocable/non-allocable/reserve; **expected advance payments** (plan); **settlement balance** (additional payment/credit, resolution-relevant, highlighted); **reserve development**; snapshot checksum/engine version (as in M3). Note that the subject of the resolution is the settlement balance (WEMoG).

### 8.2 Economic Plan
Plan amount per category (+ subtotals allocable/non-allocable/reserve); per-owner annual advance + **monthly Hausgeld amount**; reserve contribution separate; note „Vorschlag/Unterlage für die Beschlussfassung, kein Beschluss" (proposal/document for passing a resolution, not a resolution) (SDD §10).

### 8.3 Asset Report
Status of the maintenance reserve (with development); bank account balances (key date); receivables/liabilities (manual, with origin note, §6.4); key date = `period_end`. **Clearly marked note** on which values are manual administrator statements (no automatic derivation from bookings) — transparency instead of feigned correctness.

### 8.4 [§18/domain F4-11] Exact Layouts + § 35a EStG
As in M3 (O3-4): the **exact PDF layouts** for all three WEG documents are a [§18/domain]/product question (SDD §18 names "exact PDF structure … Hausgeld settlement and asset report"). M4 provides **all data** in structured form; the final layout (including a possible **§ 35a EStG disclosure** of household-related services/craftsman services per owner — SDD §18 explicitly open) must be agreed on the domain side. **Provisionally** a functional, WEG-complete standard layout without § 35a disclosure; the § 35a disclosure is a delimited add-on module that requires domain acceptance.

---

## 9. Synthetic Golden Tests (Objective M4 Benchmark)

There are **no** real WEG sample settlements. Test cases are **synthetic**, derived from the legal rules + **hand-calculated** expected results (every cent), exactly like M3 §9. Both test types are binding for M4 acceptance; they run against the **pure engine** (highest test density) and against the service/PDF.

### 9.1 Golden Tests (Hand-Calculated Expected Results)

1. **Trivial MEA distribution:** WEG, 3 units (MEA 500/300/200 of 1000), 1 non-allocable cost category (administrator costs 3000,00 €/year, MEA), full year, no change. Expected: 1500/900/600 €. Tests the owner MEA key + Σ == total.
2. **Settlement balance (core, hand-calculated):** WEG, 2 units (MEA 600/400), actual costs total 10.000,00 € (MEA → 6000/4000); economic plan expected advance payments 5500/4200. Expected: balance owner A = 6000 − 5500 = **+500,00 € additional payment**; B = 4000 − 4200 = **−200,00 € credit**. Tests §2.1 exactly (actual − expected, **not** actual − paid).
3. **Separation of allocable/non-allocable/reserve:** WEG with one cost category from each group (garden maintenance `UMLAGEFAEHIG`, administrator `NICHT_UMLAGEFAEHIG`, maintenance reserve contribution `reserve_relevant`). Expected: three separate subtotals per owner; reserve contribution **not** in `OWNER_COST_TOTAL`/balance. Tests §3.
4. **Reserve development:** opening balance 20.000,00 €, contribution 6.000,00 €, withdrawal 8.000,00 € (repair) → closing balance 18.000,00 €. Tests the invariant start + contribution − withdrawal == end; escalation on an artificially inconsistent closing balance.
5. **Owner change within the year (day-accurate):** unit sold 30.06.→01.07. (2025, not a leap year), one MEA cost category for the full year. Expected: seller 181/365, buyer 184/365 of the unit share; Σ = unit share (no phantom day, ADR-0004/0006 mirror). Tests §4.3.
6. **Co-ownership:** unit with two fractional owners (`mea_share` 0,6 / 0,4), MEA cost category. Expected: unit share further distributed exactly by 0,6/0,4, largest remainder sum-exact. Tests §4.3 second stage.
7. **Mixed property dual disclosure (close to exit):** mixed property; the same `cost_entry` set **independently** produces a `BK_MIETER` run (rented unit, allocable only) **and** a `WEG_JAHRESABRECHNUNG` run (all units/owners, all groups, balance, reserve). Tests §7: administrator costs **only** in WEG, never in operating costs; Σ consistency of both spheres.
8. **Economic plan → monthly installments:** planned costs 11.999,00 € + reserve contribution 1,00 € = 12.000,00 €, MEA 600/400 → annual advance 7200/4800 → monthly installments 600,00/400,00 €; and an "uneven" case (annual advance 7201,00 € → 12 installments via largest remainder, Σ installments == 7201,00 €). Tests §5.3 + ADR-0007 basis.
9. **Snapshot/hash determinism:** scenarios 2 + 7 finalized twice (identical inputs) → identical `snapshot_sha256`. Tests the M3 §1.8 mechanics for M4 payloads.

Each golden test checks **not only the final total** but the **individual line amounts, key base values, subtotals, balance, and reserve development** against the hand calculation (otherwise a compensating double error slips through — M3 discipline).

### 9.2 Property-Based Invariants (Hypothesis, over Random Inputs)

- **I4-A "Σ owner shares == total costs":** per item `Σ over owners distribute_amount(...) == G` exactly (ADR-0005, mirrored to owners).
- **I4-B "MEA sums coherent":** Σ of the MEA weights used == total MEA basis; for co-ownership, `Σ mea_share` of a unit consistent with the unit MEA (within tolerance, F4-7).
- **I4-C "Settlement balance == actual − expected":** for random actual costs + plan Soll, per owner `settlement balance == owner_cost_total − plan_advance` exactly; **and** `Σ settlement balances == Σ actual costs − Σ expected advances` (no drift across owners).
- **I4-D "Reserve development consistent":** `closing balance == opening balance + Σ contribution − Σ withdrawal` exactly; reserve contribution **never** appears in `OWNER_COST_TOTAL`.
- **I4-E "Owner time shares == 1":** for random `ownership_history` decompositions of a unit, the owner time factors sum exactly to 1 over the period (ADR-0004, mirrored).
- **I4-F "Reserve/non-allocable never in tenant operating costs":** in no `BK_MIETER` run does a `RESERVE_*` or `NICHT_UMLAGEFAEHIG` item appear (mirror of M3 I-F, secures the sphere separation §7).
- **I4-G "Plan monthly installments sum-exact":** `Σ over 12 monthly installments == annual advance` exactly per owner.
- **I4-H "Determinism" + "Consistency payload ↔ lines":** same input twice → identical canonical payload + hash; Σ/values of `snapshot_payload.result` == Σ/values of `settlement_line`.

These invariants hold for **any** valid input (not only the examples) and are the objective, model-independent benchmark.

---

## 10. Implementation checklist

> M4 is built as a correctness-critical step, because the settlement balance and the owner distribution are correctness-critical. Order as in M3: **engine first (pure functions + tests), then persistence/snapshot, then service, then API, PDF last.** **Prerequisite:** the human has confirmed the [DECISION]s (§11) and ADR-0007/0008. No commit before that. **All 227 existing tests stay green** (additive).

### 10.1 Engine as Pure Functions (First, Highest Test Density)
- [ ] Extend `app/engine/types.py`: `OwnerCover`/`build_owner_cover` inputs; `WegSettlementInput` (property-wide, `cost_positions` with `owner_distribution_key`, `reserve_relevant`/`cost_group` per item, `owner_periods` with `mea_share`, plan expected advance payments (Soll) per owner, reserve opening balance/withdrawals); `WirtschaftsplanInput`; `VermoegensberichtInput`. **Reuse**, do not duplicate: `UnitInput`, `OwnerPeriodInput`, `distribute_amount`, `time_factor`, `CoverPeriod` concept.
- [ ] `app/engine/periods.py`: `build_owner_cover(unit_id, period, ownership_history)` — day-accurate owner slices (co-ownership: several simultaneous slices with `mea_share`), gapless (ownership gap → escalation, no vacancy concept here). Tests I4-E + day-inclusive changes (mirror of the M3 edge cases).
- [ ] `app/engine/weg.py` (new): `compute_weg_jahresabrechnung(inp) -> WegResult` — distributes each item across the owner cover by `owner_distribution_key` (two stages for co-ownership), sums per owner, forms subtotals (`cost_group`), treats `reserve_relevant` as reserve contribution (not costs), subtracts the plan Soll → **settlement balance**, builds the reserve development. **Fail-loud validation** (ownership gap, missing MEA, inconsistent reserve, missing plan Soll). Uses `distribute_amount` (ADR-0005).
- [ ] `app/engine/wirtschaftsplan.py` (new): `compute_wirtschaftsplan(inp) -> PlanResult` — planned costs + reserve contribution → per-owner annual advance (MEA) → **12 monthly installments** (largest remainder, E4-3). Tests I4-G.
- [ ] `app/engine/vermoegensbericht.py` (new): `build_vermoegensbericht(inp) -> AssetReportResult` — reserve development + (manual) bank account balances + (manual) receivables/liabilities into holdings lines. No distribution math; only aggregation + invariant check (reserve).
- [ ] Extend `app/engine/snapshot.py`: `build_weg_snapshot_payload`, `build_plan_snapshot_payload`, `build_asset_report_payload` (canonical, money/ratios as strings, sorted — **reuse** `_m`/`_q` + `canonical_json`/`sha256_hex`). `run_type`/document type in the payload.
- [ ] Golden tests (§9.1) `tests/engine/test_golden_weg_*.py` (hand-calculated); property-based (§9.2) `tests/engine/test_invariants_weg.py` (Hypothesis).

### 10.2 Persistence & Snapshot (Afterwards)
- [ ] Models `app/models/economic_plan.py`, `economic_plan_line.py` (style of `settlement_run.py`/`settlement_line.py`: mixins, `Numeric`, `Mapped`, String+CHECK). Money as `Decimal`.
- [ ] **Migration `0012_m4_settlement_line_types`:** additive extension of the `settlement_line.line_type` CHECK by the new values (§3.2/§6.5); **new columns** `settlement_line.addressee_owner_profile_id` (FK→`owner_profile`, `RESTRICT`, NULL) and `settlement_run.economic_plan_id` (FK→`economic_plan`, `RESTRICT`, NULL). **The `run_type` CHECK stays unchanged** (values already in `0009`). `downgrade()` resets CHECK/columns.
- [ ] **Migration `0013_economic_plan_tables`:** `economic_plan` + `economic_plan_line` (CHECKs, FKs `RESTRICT` to `organization`, indexes §1.3/§1.4, `superseded_by_id` forward). Full CRUD grant for `app_rw` (trigger-only freeze, E3-5 line).
- [ ] **Migration `0014_m4_rls_policies`:** four policies per new table exactly in the pattern of `0010` (`NULLIF(current_setting('app.current_org_id', true),'')::uuid`, fail-closed).
- [ ] **Migration `0015_m4_immutability_triggers`:** ADR-0003 trigger (pattern `0011`) for `economic_plan` (freeze condition `status='finalized'`, only permitted change `superseded_by_id`) and `economic_plan_line` (parent plan `finalized` → every mutation forbidden). Trigger source text commented in detail; `downgrade()` drops trigger + function. *(For `settlement_run`/`settlement_line` the trigger is already in `0011` — the new columns `economic_plan_id`/`addressee_owner_profile_id` may need to be added to the "content-bearing, frozen from finalized" column list of the existing trigger: **update the trigger function in `0012` via `CREATE OR REPLACE`**, additive.)*
- [ ] Extend `TENANT_TABLES` in `tests/test_rls_coverage.py` by `economic_plan`, `economic_plan_line`.
- [ ] Repository `app/repositories/economic_plan_repo.py` (style of `settlement_repo.py`): active query (`superseded_by_id IS NULL`), read aggregation (prior-year actuals per category as plan suggestion). Extend `SettlementRunRepository` by the WEG read aggregation (owner/MEA data per property — `ownership_for_property` already exists; add `owner_profiles_for_property` if needed).

### 10.3 Service (Snapshot/Finalize Atomic)
- [ ] `app/services/weg_settlement_service.py` (or extend `SettlementService` by WEG paths, style of `settlement_service.py`): `create_weg_draft`, `compute` (WEG engine; load + freeze the plan expected advance payments (Soll) from `economic_plan`), `set_reserve_inputs` (opening balance/withdrawals, F4-4/F4-5), `finalize` (atomic, analogous to M3), `create_correction_run` (reversal/new). **Read boundary `db_exclusive_to_engine_inclusive` for `ownership_history` as in M3** (ADR-0006 — the helper exists).
- [ ] `app/services/economic_plan_service.py`: `create_draft`, `compute` (plan engine), `finalize` (atomic), `create_correction_run`.
- [ ] `app/services/asset_report_service.py` (or within the WEG service): `create_draft`, `set_balances`/`set_receivables_payables` (manual, F4-8/F4-9), `compute`, `finalize`.
- [ ] `audit_event` actions: `settlement.weg.*`, `economic_plan.*`, `asset_report.*` (via the existing `AuditRepository`).

### 10.4 API (API-First, RBAC as in M0/M2/M3)
- [ ] `app/api/economic_plans.py`: `POST` (create plan, `require_write`), `POST /{id}/compute`, `POST /{id}/finalize`, `POST /{id}/correction`, `GET` (list/detail), `GET /{id}/pdf`. READ_ONLY: GET only. Foreign ID → **404**.
- [ ] WEG annual settlement + asset report: integrate into `app/api/settlements.py` (same `settlement_run` envelope) — new endpoints/parameters for `run_type=WEG_JAHRESABRECHNUNG`/`VERMOEGENSBERICHT`, or a dedicated router `app/api/weg_settlements.py`. Register the router in `app/main.py`.

### 10.5 PDF (Last, Strictly Separate)
- [ ] `app/templates/weg_jahresabrechnung.{html,css}` + `app/templates/wirtschaftsplan.{html,css}` + `app/templates/vermoegensbericht.{html,css}` and `app/services/*_pdf.py` (style of `settlement_pdf.py`) that render **only** from the finalized run/plan + lines (`render_pdf`, WeasyPrint). **No** recomputation. Tests: PDF values == line values.

### 10.6 Definition of Done for M4 (Handover to M5/M9)
- [ ] Engine extension pure (no DB/HTTP imports); `distribute_amount`/`time_factor` **reused**, not duplicated; `Decimal` throughout.
- [ ] **Golden tests §9.1 green** (hand-calculated, incl. settlement balance scenario 2, mixed-property dual disclosure scenario 7).
- [ ] **Property-based invariants §9.2 green** (I4-A to I4-H), esp. "Σ owner shares == total costs", "balance == actual − expected", "reserve development consistent", "reserve/non-allocable never in tenant operating costs".
- [ ] `economic_plan`/`economic_plan_line` with RLS (four policies, fail-closed) + ADR-0003 trigger (`finalized` immutable) + snapshot hash; existing `settlement_*` trigger additively extended by the new columns.
- [ ] `finalize` atomic (all three document types); `audit_event` for all operations; correction = new run (never mutation).
- [ ] WEG/plan/asset report PDF recomputes nothing; formal mandatory WEG information complete.
- [ ] RLS coverage test + `test_tenant_isolation` extended by the M4 tables; **all 227 existing tests stay green** (M3 operating costs unchanged).
- [ ] M4 exit objectively met: economic plan, annual settlement (with hand-calculated settlement balance), and asset report for a WEG can be generated as PDF; amounts **consistent** (golden + invariants).
- [ ] No **[DECISION]**/**[§18/domain]** item "guessed away" unmarked; provisional assumptions clearly labeled; domain acceptance (tax advisor/specialist attorney for WEG law) recommended.

---

## 11. Summary of Open Decisions (for the Human)

| # | Item | Type | Recommendation | When |
| --- | --- | --- | --- | --- |
| **E4-1** | Annual settlement + asset report as `run_type` of the same `settlement_run` envelope | [DECISION] | **same envelope** (M3-E3-6 line; mechanics done) | before M4 impl |
| **E4-2** | Economic plan as separate entity `economic_plan`/`economic_plan_line` | [DECISION] | **separate entity** (SDD §11/§13), same snapshot/trigger/RLS mechanics | before M4 impl |
| **E4-3** | Basis for plan advance payments: month-based (12 installments) vs. day-accurate | [DECISION] | **month-based** (actual settlement stays day-accurate, ADR-0004) | before M4 impl (ADR-0007) |
| **E4-4** | WEG annual settlement: one run per WEG (per-owner lines) vs. one run per owner | [DECISION] | **one run per WEG**, property-wide, `addressee_owner_profile_id` | at impl |
| **F4-1** | Soll source when no plan was resolved | [§18/domain] | provisional: plan required; otherwise clarify domain-wise | before production (ADR-0007) |
| **F4-2** | Settlement balance == actual − expected advance payment (not − paid) | [§18/domain] | so (prevailing opinion on WEMoG); specialist attorney for WEG law to accept | before production (ADR-0007) |
| **F4-3** | Owner sphere distributes `gross_amount` (not tenant share) | [§18/domain] | so; accept special cases § 16 II WEG | before production |
| **F4-4/5** | Data source for reserve opening balance + withdrawals | [§18/domain] | M4 manual; M6/booking later | before production |
| **F4-7** | `mea_share` sum tolerance for co-ownership | [§18/domain] | check defensively, compute normalized; tolerance domain-wise | before production |
| **F4-8** | Input path for bank account balances (asset report) | [§18/domain] | manual key-date balance in the snapshot (no M6 anticipation) | before production (ADR-0008) |
| **F4-9** | Receivables/liabilities in the asset report | [§18/domain] | manual capture with origin note (or deliberately later) | **decide domain-wise** (ADR-0008) |
| **F4-10/11** | Key date/layout/§ 35a EStG disclosure | [§18/domain] | data ready; layout + § 35a domain/product | before production |

> **No self-certification (stance):** This design makes the economic plan, annual settlement, settlement balance, reserve development, and asset report **transparent, deterministic, and recomputable** (golden tests + property-based invariants as the objective benchmark). It **cannot** guarantee that a specific WEG settlement is court-proof, audit-proof, or resolution-proof in an individual case. The domain correctness (esp. the WEMoG settlement balance, separation of allocable/non-allocable/reserve, the scope under § 28 IV, § 35a EStG) must be accepted by a **tax advisor/specialist attorney for WEG law** before productive use.

---

## 12. What This Document Deliberately Does NOT Decide

- **Real payments/tenant ledger (Mietkonto) / Hausgeld account balances (actual advance payments `DERIVED`, bank account booking balances):** M6 (§2.4, §6.2).
- **Open items / receivables-liabilities automatism:** M6/M7 (§6.4 — in M4 only manual/open, to be accepted domain-wise).
- **Meeting / resolution register** (the economic plan is a document, not a resolution): M8 (SDD §10/§6.10).
- **§ 35a EStG disclosure** in owner settlements: SDD §18, delimited add-on module requiring acceptance (§8.4).
- **Exact PDF layouts / mandatory WEG texts:** SDD §18 (§8.4).
- **CO2 10-tier self-calculation, commercial-use pre-deduction (Gewerbe-Vorwegabzug):** M3 legacy items (M3 §5/§6), the owner side distributes `gross_amount` (§4.5).
- **Final legal acceptance:** tax advisor/specialist attorney for WEG law — cannot be replaced by this document.
