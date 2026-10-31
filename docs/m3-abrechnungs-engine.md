# M3 Design: Distribution & Settlement Engine (Operating Cost Statement for Tenants)

As of: 2026-06-01 · Version 0.1 (design, before implementation)
Reference: [sdd-property-management-mvp.md](sdd-property-management-mvp.md) v0.2 §6.9, §7, §8, §11, §12, §14, §15, §17, §18 · [archived roadmap](archive/roadmap.md) M3 · [architecture.md](architecture.md) §3 (immutability), §5.1 (conventions), §5.4 (I2 entities) · [m2-gobd-belegfluss.md](m2-gobd-belegfluss.md) (`cost_entry`, service period, separate distribution keys, `apportionable_tenant_amount`)
ADRs for this document: [adr/0003-…](adr/0003-gobd-immutability-mechanism.md) (reused) · [adr/0004-pro-rata-daily-allocation.md](adr/0004-pro-rata-daily-allocation.md) (new) · [adr/0005-rounding-largest-remainder.md](adr/0005-rounding-largest-remainder.md) (new)
Status: **Draft for confirmation by the human — no application code, no migration, no commit**

> This document specifies the **distribution/settlement engine** (M3) down to the calculation mathematics. According to the roadmap, M3 is the largest, riskiest, and most correctness-critical building block. The scope of M3 is the **operating cost statement (Betriebskostenabrechnung) for tenants** (SDD §6.9, §8, §11); however, the engine is designed so that **M4** (annual settlement of the homeowners' association (WEG) with settlement balance (Abrechnungsspitze), economic plan (Wirtschaftsplan), asset report (Vermögensbericht)) **reuses** it rather than reinventing it.
>
> **Binding status:** The SDD (v0.2), roadmap, `architecture.md`, `m2-gobd-belegfluss.md`, and ADRs 0001–0003 are the source of truth and remain **untouched**. This document is additive. Decisions that the human should confirm are marked **[DECISION]** (decision); open SDD §18 items are marked **[OPEN/§18]** (open; not decided unilaterally; a provisional, clearly marked assumption is used for test cases; professional sign-off recommended).
>
> **No self-certification:** This design makes the settlement *transparent, reproducible, and auditable* and discloses every assumption made. It **cannot** guarantee that a specific settlement will withstand court or audit scrutiny. The professional correctness of the distribution, apportionment, and rounding rules in an individual case must be signed off by a **tax advisor/attorney specializing in the field** before productive settlements are run.

---

## Property settlement periods (#68)

`property.fiscal_year_start_month` defines twelve months from the first of that month;
January remains the default. `engine/periods.py` supplies pure inclusive bounds for a
containing date or a start year, and labels such as `2025` or `2025/2026`. The tenant-statement deadline
monitor uses the same functions. No migration is required.

`GET /api/properties/{property_id}/settlement-periods?year=2025` offers start years
2023–2026 with labels and dates; without `year`, the offers are the three most
recent completed property periods, newest first, starting with the period before
the one containing today (`period_for_date`). The statement and batch forms adopt
these dates and still permit free periods. A reversed or over-twelve-month period
returns 422. Creation and finalization return 409 when the new period overlaps a
visible finalized, non-superseded run for the same lease and type, unless both
period bounds are identical (repeat/correction runs). For finalized bounds
`[a, b]` and new bounds `[s, e]`, the conflict predicate is
`a <= e AND b >= s AND (a != s OR b != e)`. Contained and containing periods,
shared starts or ends and overlap on a boundary day all conflict. Mixed batches
report conflicting leases as per-lease errors, create the other drafts, and
continue skipping identical existing runs. An entirely conflicting batch retains
409. Drafts never block creation. Short transition
periods remain possible after a property's start month changes. An
inclusive period from 29 February 2024 through 28 February 2025 is twelve months
and is accepted.

Period-conflict responses use `detail` objects with
`code: "settlement_period_conflict"` and the unchanged German `message`.
Mixed-batch errors retain their German `errors[].error` text and carry the same
code in `errors[].code`.

**Lease-bounded statements (#122):** a complete readable property-period request
is intersected with the target lease's active span at creation. Batch creation
intersects its selected period separately for each overlapping lease; repeats
match the resulting clipped dates. A finalized legacy run that already covers
that clipped span is skipped with a German reason; partial overlaps remain batch
errors and single creation retains the overlap 409. A wider legacy **draft** covering
the clipped span causes a batch overlap error; only finalized covering runs receive
this skip. Identical existing drafts are still skipped. Stored lease bounds are half-open
`[valid_from, valid_to)`, so the inclusive statement ends on `valid_to - 1 day`.
Free dates must be entirely inside the lease, including for scope-only clerks
without property read access. A free period outside the lease, or a property
period with no lease overlap, returns 422 with
`code: "settlement_period_outside_lease"` and
`message: "Der Abrechnungszeitraum muss innerhalb der Mietdauer liegen."`.
The single-statement picker intersects offers with the selected lease, hides
empty intersections and displays the clipped dates beside the period label.
Changing the lease resets the dates; free dates and delayed-response protection
remain available.

**Short-window sources (#122):** time-based cost positions use actual service
days (ADR-0004); there is no separate monthly cost-period type. The engine builds
the complete service-period tenancy/vacancy cover and balances exact key × time
weights once through `distribute_amount` (ADR-0005). A run selects only its
intersection with each balanced cell. When a run cuts a cell, the rounding module
uses the difference of floored cumulative day shares; the cell's last day retains
its remaining cents. Adjacent windows therefore recover the complete source
allocation, including vacancy attributed to the owner, independently of creation
order. Full-year statements without a tenant change retain their in-period
allocation. A 3,650-euro annual source in
2025 carries 900 euros in January–March; January–September and October–December
carry 2,730 and 920 euros. External metering-service amounts remain explicit
occupancy amounts (the undated-input rule is stated in §5.2).

### Sources crossing the property-period boundary (#122)

Cross-boundary service periods are now apportioned to the selected property period.
Previously, the whole source could be charged in both years. For building insurance
of 3,650 euros covering 1 July 2024–30 June 2025, calendar-year statements now
charge 1,840 euros in 2024 and 1,810 euros in 2025, totaling 3,650 euros once.
An April–March property period follows the same rule (for example, an annual
calendar-year property-tax source spans two such periods). This is an intentional
change: recomputed drafts and corrections of these older runs can shift. Existing
finalized snapshots remain frozen.

### Full-property allocation and within-unit splitting (#122)

Building-wide consumption weights use unit quantities from the full configured
property settlement period, independently of the lease's statement window. Each
source's cost pool retains its invoice service period, intersected with the property
period when it crosses that boundary. A source outside the lease's statement window
contributes zero. The unit's tenant/vacancy split is restricted to that source period;
a short statement selects its balanced cells afterward.
Area, heated area, units, MEA, persons and fixed manual amounts split by days through
the central rounding module. Consumption quantities are measured over the property
period. An interim reading on the outgoing tenant's last occupied day **or** the
incoming tenant's first day denotes the same half-open boundary. More than one
reading across these two dates is ambiguous and requires manual entry with a clear
reason; no arbitrary reading is selected. Exact interim readings split its consumption;
missing interim readings split the known unit consumption by days, by analogy to
§9b HeizkostenV. Other units need only their own period readings. Manual period
consumption without interim readings uses the same day fallback, with a reason
shown in suggestions and frozen in the automatic source snapshot. Interim readings
are excluded from the nearest property-period start/end selection: a 14 December
tenant-change reading cannot replace a missing year-end reading and manufacture a
zero for the successor. Missing or ambiguous period quantities require manual input
with a reason; they are not estimated. Manual unit consumption always denotes the
**whole property-year quantity**, including for clipped statements. Person counts
are per-unit property-year weights and must be entered consistently in every run
belonging to that property year.

For two units using 1,000 units each and a 1,000-euro water pool, A (January–September),
B (October–December), and C (all year) receive 373.97 / 126.03 / 500.00 euros with
the day fallback, totaling 1,000.00 euros. Interim quantities of 600 / 400 in the
changed unit instead yield 300.00 / 200.00 / 500.00 euros. The other unit's
interim pattern never changes these building shares. Heating keeps its separate
base/consumption pools as described in §5.1. Cumulative cent selection means all
successive leases plus vacancy reproduce each unit's full-period share exactly,
regardless of statement creation order. No second run-window factor is applied to
already selected consumption/heating shares. Free dates crossing a configured
property-year boundary select from each full property-year allocation separately;
advance payments are deducted once. The frozen `inputs.property_periods` retain
the separate quantities, cost pools and selected results. External provider amounts
still apply once to the selected statement.

For a December-only water invoice of 100 euros with the same annual unit weights,
A receives 0.00 euros and B/C receive 50.00 euros each. Two half-year invoices of
600/400 euros yield A/B/C 400.00 / 100.00 / 500.00 euros with the day fallback.

**Current overlap limitation:** the check runs under the acting user's normal
row-level security. Overlaps with runs in other accounting scopes that the user
cannot see are not detected. The existing financial-input lock serializes creation and finalization,
but does not widen visibility. Follow-up #95 must add a database constraint to enforce
the rule across accounting scopes; this change adds neither a migration nor a
privileged helper. This limitation also applies to economic plans and WEG runs.

**Property access limitation:** a user without property read access must enter
the period; the period offer needs property access. Financial creation checks
pause mode, parents and accounting scope before resolving or validating the
period. A scope-only clerk can create drafts with explicit dates without reading
the property's start month. Missing plan dates return 422 with
"Abrechnungszeitraum angeben: Beginn und Ende". Users who can read the property
retain its default period. The organization-bound month getter is deferred to
#95's migration; no migration is added here.

**Other period rules and limitation:** economic plans honour explicit dates within
the readable property period or exactly closing the gap from the last finalized
plan to the next property period; scope-only clerks supply their own dates,
subject to the same twelve-month and overlap checks. Offered plan dates remain
editable for mid-year scopes and gap-closing transitions. Year-only NK and
Hausgeld advances are split across the twelve months of their assigned calendar
year. A statement credits only the months it covers in that year, including
months in the second calendar year of a non-calendar period. The rounding module
floors each monthly share to cents and assigns the remainder to December, so
the last period covering that year receives the remaining cents. For 1,200 euros,
January–June and July–December credit 600 euros each; April–December credits
900 euros. For 1,000 euros, January–November credit 83.33 euros each and December
83.37 euros. A full calendar year still credits the full amount. Month shares
do not depend on other drafts or finalization order; later statements cannot
change earlier finalized shares. If a statement boundary splits a month, that
month's share is prorated by its days using cumulative cent boundaries, so
adjacent partial-month statements cannot credit that share twice. The NK finalization freshness check uses the
same derivation. **Month-assigned advances (#136)** use the same cumulative day
rule on their assigned calendar month: group the active booked payments by
addressee, type and month, then credit only the days covered by the statement.
For twelve 100-euro payments, January–14 March credits 245.16 euros and
15 March–December credits 954.84 euros, exactly 1,200 euros together. Whole
months and periods starting on the first remain unchanged. NK and paid Hausgeld
share this engine rule; ledger filtering is unchanged. See the
[WEG period rules](m4-weg-abrechnung.md#property-settlement-periods-68). The economic
plan deadline in `fristen_service.py` is still calendar-based for non-January
properties and remains a follow-up; it is not changed by #68.
The per-property year overview and Anlage V stay on the calendar year.

Automatic form prefill only applies while both date fields are empty. A delayed
period-offer response preserves dates the user has already entered; choosing an
offered period explicitly still sets both dates.

Derived tenant advances use the assigned payment months across both calendar years;
the reference year may be any year touched by the period. Changing the property
setting only changes future suggestions; stored runs and finalized snapshots retain
their dates, amounts and hashes.

## 0. Scope, exit criterion, connection to M0–M2

**M3 exit (roadmap):** "An operating cost statement (BK-Abrechnung) for a mixed-use property with a tenant change is **reproducibly correct** and **formally complete**." This criterion is the objective yardstick. "Reproducibly correct" is demonstrated by the **synthetic golden tests** (§9) with **hand-calculated expected results**; "formally complete" by the mandatory BGH disclosures in `settlement_line`/PDF (§8).

**M3 builds on verified foundations (M0/M1/M2, 141 tests green) and adopts their conventions unchanged.** The engine is a **consumer** of the M1/M2 data, not an extension of it:

| Existing foundation | How M3 uses it |
| --- | --- |
| `cost_entry` with `service_period_from/_to`, separate `owner_/tenant_distribution_key`, `tenant_apportionable`, `apportionable_tenant_amount`, active chain (`status='confirmed' AND superseded_by_id IS NULL`) | **Sole** cost source of the engine. Only **active, confirmed** entries are settled (m2 §3.4). |
| `unit` (`area`, `heated_area`, `mea`, `usage_type`) | Distribution base quantities per unit. |
| `ownership_history` (temporal, EXCLUDE, `mea_share`, gaps/co-ownership allowed) | Owner assignment per day → vacancy addressee (M3), owner sphere (M4). |
| `lease_agreement` (temporal, EXCLUDE per unit, `base_rent`, `advance_payment`) | Tenant periods per unit; gap = vacancy. `advance_payment` = default advance payment (§7). |
| `cost_category` (global, versioned, `tenant_apportionable_default`, `reserve_relevant`) | Categorization, separation of apportionable/non-apportionable/maintenance reserve (Erhaltungsrücklage) (M4-relevant; M3 only "apportionable?"). |
| RLS pattern (`ENABLE`+`FORCE`, four policies, `NULLIF(current_setting('app.current_org_id', true),'')::uuid`, fail-closed) | `settlement_run`/`settlement_line` get exactly this pattern (migration analogous to `0007`). |
| ADR-0003 trigger (`BEFORE UPDATE/DELETE`, status-aware, per-column `IS DISTINCT FROM`) | **Reused** for `settlement_run`/`settlement_line`, freeze condition `status='finalized'` (ADR-0003 §3). |
| `TenantRepository`, atomic service transaction (`cost_entry_service.confirm`), `AuditRepository`, swappable `PdfRenderer` (WeasyPrint) | Templates 1:1 for `SettlementRepository`, `settlement_service.finalize`, audit, PDF. |
| Money `NUMERIC(14,2)`/`Decimal`, shares `NUMERIC(12,6)`, UUID PK, `TIMESTAMPTZ`, string+CHECK instead of DB enum, FK `ondelete=RESTRICT` to `organization` | Adopted throughout. |
| Migrations `0001`–`0008` exist | M3 adds **`0009`–`00xx`** additively (order in §10). |

**Not in M3:** WEG annual settlement/settlement balance (Abrechnungsspitze), economic plan, asset report (M4 — this engine is prepared for it, §1.3). Payments/tenant ledger (Mietkonto) (M6 — M3 captures the advance payment total **manually**, §7). E-invoice (M5). Dunning (M7).

**Architectural principle (SDD §16, strict here):** The **calculation engine is a layer of pure functions** (`Decimal` in/out, no DB, no HTTP, no I/O), separate from persistence (snapshot), API, and PDF layout. This is the prerequisite for testability and for reuse in M4 (§10).

---

## 1. Data model: `settlement_run` + `settlement_line`

### 1.1 Role and lifecycle

A **`settlement_run`** is *one* settlement run for *one* addressee group in *one* settlement period (e.g., "operating cost statement, apartment 3, tenant Müller, 2025"). It passes through exactly two states:

```
   draft  ──finalize──▶  finalized   (IMMUTABLE from here, ADR-0003)
     │                        │
     │ (recalculate,          │ Correction needed?
     │  change inputs,        │  → NEVER mutation
     │  discard)              ▼
     └───────────────  new settlement_run version (cancellation/recalculation),
                        supersedes_id → old run  (see 1.6)
```

- **`draft`:** freely recalculable; `snapshot_payload` is overwritten on every (re)calculation; nothing is frozen; no external effect.
- **`finalized`:** the calculated state is frozen as an **immutable snapshot** (SDD §16, architecture.md §3.1/§5.4). `snapshot_payload` + `snapshot_sha256` are locked against mutation by the ADR-0003 trigger from here on. This is the state that goes to the tenant as a PDF. Later master-data changes (tenant change recorded retroactively, MEA corrected) do **not** alter a finalized settlement (that is the entire purpose of the snapshot).

### 1.2 Why a snapshot (and not "calculate live from the master data")

SDD §16: "All final settlement results … are stored as **immutable snapshots** so that later master-data changes do not alter old documents." A sent operating cost statement is a legally binding document; it must remain exactly reproducible even if a lease is corrected years later, a unit is repurposed, or the chart of accounts is migrated to `plan_version 2`. The snapshot freezes **all** inputs *and* all calculated intermediate and final values. The `snapshot_sha256` makes subsequent tampering detectable (tamper evidence, analogous to `invoice_document.content_sha256`, m2 §4.5).

### 1.3 Type discriminator — BK now, WEG/plan/asset report reserved (M4)

`settlement_run.run_type` (string + CHECK) carries the settlement type. **M3 implements only `BK_MIETER`**; the remaining values are **reserved in the vocabulary** so that M4 can use the same table/engine without a schema break:

| `run_type` | Meaning | Milestone |
| --- | --- | --- |
| `BK_MIETER` | Operating cost statement for one tenant (SDD §6.9, §11) | **M3** |
| `WEG_JAHRESABRECHNUNG` | Hausgeld/annual settlement with settlement balance (Abrechnungsspitze) (WEMoG) | M4 (reserved) |
| `WIRTSCHAFTSPLAN` | Economic plan with Hausgeld proposals | M4 (reserved) |
| `VERMOEGENSBERICHT` | Asset report § 28 IV WEG | M4 (reserved) |

> **[DECISION]** From a professional standpoint, the economic plan and the asset report are not a "settlement with expected/actual per cost category" but plan or inventory documents. Recommendation: **same `settlement_run` shell** (reuse the snapshot/immutability/audit mechanism) with its own `run_type` and a type-dependent `snapshot_payload` structure; whether `settlement_line` fits them or a dedicated detail structure within the payload makes more sense will be decided in M4. M3 blocks nothing.

### 1.4 Schema `settlement_run` (tenant-bound, RLS, immutable from `finalized`)

| Column | Type | Meaning / rationale |
| --- | --- | --- |
| `id` | `UUID` PK | as everywhere (`gen_random_uuid()`). |
| `organization_id` | `UUID NOT NULL` FK→`organization` (`RESTRICT`) | I1, RLS. |
| `property_id` | `UUID NOT NULL` FK→`property` (`RESTRICT`) | Property reference. |
| `management_mandate_id` | `UUID NULL` FK→`management_mandate` (`RESTRICT`) | Mandate reference (anchor for segregation of funds (Vermögenstrennung), architecture.md §2.6). |
| `run_type` | `String(24) NOT NULL` | `BK_MIETER` (M3) among others (§1.3). CHECK. |
| `period_start` | `DATE NOT NULL` | Start of settlement period (mandatory BGH disclosure, §8). |
| `period_end` | `DATE NOT NULL` | End of settlement period. `CHECK (period_end >= period_start)`. |
| **Addressee (BK: exactly one tenant/lease):** | | |
| `unit_id` | `UUID NULL` FK→`unit` (`RESTRICT`) | for `BK_MIETER`: the settled unit. NULL for property-wide runs (M4). |
| `lease_agreement_id` | `UUID NULL` FK→`lease_agreement` (`RESTRICT`) | for `BK_MIETER`: the settled lease/addressee. *(Multiple leases of the same unit in the period → multiple `settlement_run`s or one run with multiple addressees — see [DECISION] 1.5.)* |
| `tenant_profile_id` | `UUID NULL` FK→`tenant_profile` (`RESTRICT`) | denormalized addressee (audit/display). |
| **Expected/actual (Soll/Ist) (§7):** | | |
| `advance_payments_total` | `NUMERIC(14,2) NULL` | Sum of the operating cost advance payments made by the tenant in the period — **manually capturable in M3** (M6 replaces this with the real `Payment` sum). |
| `advance_payments_source` | `String(12) NOT NULL DEFAULT 'MANUAL'` | `MANUAL` (M3) \| `DERIVED` (M6, from payments). CHECK. Makes the origin audit-proof. |
| `total_cost` | `NUMERIC(14,2) NULL` | Sum of the apportioned expected shares (redundant header value; must be == Σ `settlement_line.amount` of the expected lines). |
| `balance` | `NUMERIC(14,2) NULL` | `total_cost − advance_payments_total`; > 0 = additional payment due, < 0 = credit (§7). |
| **Snapshot/immutability (ADR-0003):** | | |
| `status` | `String(12) NOT NULL DEFAULT 'draft'` | `draft` \| `finalized`. CHECK. Freeze condition: `finalized`. |
| `snapshot_payload` | `JSONB NULL` | **Canonical, complete** calculation basis + result (§1.7). NULL in a fresh `draft` before the first calculation; set from the first calculation; frozen from `finalized`. |
| `snapshot_sha256` | `String(64) NULL` | SHA-256 over the **canonical** JSON serialization of `snapshot_payload` (§1.8). Set at `finalize`. Tamper evidence. |
| `engine_version` | `String(20) NOT NULL` | Version of the engine logic (e.g., `m3.1.0`) that produced the snapshot — reproducibility across code changes. |
| `finalized_at` | `TIMESTAMPTZ NULL` | Time of finalization. |
| `finalized_by` | `UUID NULL` FK→`user` (`SET NULL`) | Who finalized (audit). |
| `superseded_by_id` | `UUID NULL` FK self (`RESTRICT`) | on cancellation/recalculation: points to the successor run (correction chain, §1.6 — **forward** pointer like `cost_entry`, E-4 convention). |
| `created_at`/`updated_at` | `TIMESTAMPTZ` | as everywhere. |

Constraints: `CHECK (period_end >= period_start)`; `CHECK (run_type IN (...))`; `CHECK (status IN ('draft','finalized'))`; `CHECK (advance_payments_source IN ('MANUAL','DERIVED'))`. **RLS** four policies (analogous to `0007`). Indexes: `organization_id`; `property_id`; `(organization_id, run_type, status)`; `(property_id, period_start, period_end)`; `lease_agreement_id`; `superseded_by_id`.

### 1.5 Schema `settlement_line` (belongs to the run's snapshot)

A `settlement_line` is **one reported item** of the settlement: usually one cost category (total costs + key + tenant share), plus special lines (advance payment, balance). It carries **all mandatory BGH disclosures per item** (§8) in structured form — not just the final amount — so that the PDF and machine verification are complete.

| Column | Type | Meaning |
| --- | --- | --- |
| `id` | `UUID` PK | |
| `organization_id` | `UUID NOT NULL` FK→`organization` (`RESTRICT`) | I1, RLS. |
| `settlement_run_id` | `UUID NOT NULL` FK→`settlement_run` (`RESTRICT`) | Membership. |
| `line_type` | `String(20) NOT NULL` | `COST` (cost category) \| `ADVANCE_PAYMENT` (advance payment deduction) \| `BALANCE` (additional payment/credit) \| `SUBTOTAL` (optional, e.g., Σ apportionable). CHECK. |
| `cost_category_id` | `UUID NULL` FK→`cost_category` (`RESTRICT`) | for `COST`: the cost category. |
| `cost_category_code` | `String(50) NULL` | denormalized (audit-proof, independent of plan versions). |
| `label` | `String(255) NOT NULL` | Display name of the line. |
| `distribution_key` | `String(20) NULL` | **Applied** distribution key (BGH requirement: "apportionment key per item"). CHECK against the §8 vocabulary. |
| `total_amount` | `NUMERIC(14,2) NULL` | **Total costs of the cost category** in the period (BGH requirement), before distribution. |
| `distribution_base_total` | `NUMERIC(12,6) NULL` | Denominator of the key (e.g., total living area, total MEA, sum of consumption) — makes the calculation path traceable. |
| `distribution_base_share` | `NUMERIC(12,6) NULL` | Numerator of the addressee (e.g., their living area/their consumption). |
| `time_share_days` | `INTEGER NULL` | occupied days (ADR-0004) — for intra-year items. |
| `time_share_total_days` | `INTEGER NULL` | Days of the service period (denominator of the time apportionment). |
| `amount` | `NUMERIC(14,2) NOT NULL` | **The rounded amount attributed to the addressee** (ADR-0005). For `BALANCE` the balance (signed). |
| `sort_order` | `SMALLINT NOT NULL DEFAULT 0` | fixed order in the PDF. |
| `note` | `String(500) NULL` | Note (e.g., "extern abgerechnet" (settled externally), "nur Mieteranteil CO2" (tenant share of CO2 only), "zeitanteilig 73/365 Tage" (pro rata 73/365 days)). |
| `created_at` | `TIMESTAMPTZ` | |

Constraints: `CHECK (line_type IN (...))`; `CHECK (distribution_key IS NULL OR distribution_key IN (...))`. **RLS** four policies. Indexes: `organization_id`; `settlement_run_id`; `(settlement_run_id, sort_order)`.

> **Double truth, deliberately:** `settlement_line` is the **relational, queryable** form of the result; `snapshot_payload` (JSONB on the run) is the **canonical, hashed** form. Both are produced in the same `finalize` transaction from the **same** engine result. A test (§9, invariant) verifies that they are consistent (Σ `settlement_line` == payload totals). Rationale: `settlement_line` conveniently supports the PDF and reporting; the payload hash carries the immutability guarantee. The `settlement_line` rows of a `finalized` run are locked against mutation by the same ADR-0003 trigger (freeze condition on the run; see 1.6).

### 1.6 Immutability via ADR-0003 (reused pattern, no new mechanism)

ADR-0003 §3 has already **earmarked** `settlement_run`/`settlement_line` for "the same trigger mechanism with freeze condition `status='finalized'`". M3 redeems this without inventing anything new:

- **`settlement_run`** (freeze condition `OLD.status = 'finalized'`): `BEFORE UPDATE/DELETE` trigger following the pattern of `trg_cost_entry_immutability` (migration `0008`). Content-bearing columns (all except `superseded_by_id`, `updated_at`) are locked after `finalized` via `IS DISTINCT FROM`; the **only** permitted change is setting `superseded_by_id` (cancellation/recalculation chain, analogous to `cost_entry` E-4). DELETE of a `finalized` run → always an exception. `draft` runs are freely editable/deletable (recalculation).
- **`settlement_line`** (freeze condition: the **parent run** is `finalized`): The trigger checks via `SELECT status FROM settlement_run WHERE id = OLD.settlement_run_id`. If the run is `finalized`, **any** row change/deletion is forbidden (a finalized settlement has no "permitted" row mutation — correction = new run). While the run is `draft`, rows are free (recalculation replaces them).
- **Grant backstop:** Giving `settlement_line` **no DELETE grant** for `app_rw` is *not* sensible, because `draft` recalculation must delete rows. Therefore: full CRUD grant + trigger as the sole status-dependent lock (like `cost_entry`, not like `invoice_document`). **[DECISION]** confirm: `settlement_run`/`settlement_line` = trigger-only (no grant revocation), because both have an editable `draft` state.

**Correcting a finalized settlement = new run (cancellation/new):** A sent, incorrect operating cost statement is **not** changed. A new `settlement_run` (new calculation) is created; the old one is pointed at it via `superseded_by_id` and remains preserved unchanged (GoBD, SDD §14). Whether the correction appears professionally as "cancellation + new" or "corrected second settlement" is a later/professional question; the **model** supports both via the chain.

### 1.7 Content of the `snapshot_payload` (canonical, complete, self-explanatory)

The payload must contain **everything** needed to reproduce and trace the settlement **without recourse to mutable master data**. Structure (conceptual; exact keys fixed in the implementation and versioned via `engine_version`):

```jsonc
{
  "engine_version": "m3.1.0",
  "run_type": "BK_MIETER",
  "period": { "start": "2025-01-01", "end": "2025-12-31", "days": 365 },
  "property": { "id": "...", "name": "...", "management_type": "MISCH" },
  "addressee": { "lease_agreement_id": "...", "unit_id": "...", "unit_number": "3",
                 "tenant_name": "...", "lease_periods_in_scope": [ {"from": "...", "to": "..."} ] },
  "inputs": {
    // FROZEN copies of all master data/costs used -- not just IDs:
    "units": [ { "id": "...", "number": "...", "area": "75.00", "heated_area": "70.00",
                 "mea": "210.500000", "usage_type": "WOHNUNG" }, ... ],
    "lease_periods": [ { "unit_id": "...", "from": "...", "to": "...", "advance_payment": "150.00" }, ... ],
    "vacancy_periods": [ { "unit_id": "...", "from": "...", "to": "...", "owner_profile_id": "..." }, ... ],
    "cost_entries": [ { "id": "...", "category_code": "GRUNDSTEUER", "gross_amount": "1200.00",
                        "service_period": {"from":"...","to":"..."}, "tenant_distribution_key": "WOHNFLAECHE",
                        "tenant_apportionable": "UMLAGEFAEHIG", "apportionable_tenant_amount": null }, ... ],
    "consumption_readings": [ ... ],     // for VERBRAUCH key
    "external_amounts": [ ... ],         // for EXTERNE_ABRECHNUNG (heating/water), per unit
    "advance_payments_total": "1800.00",
    "advance_payments_source": "MANUAL"
  },
  "computation": [
    // the complete calculation path per cost item:
    { "cost_entry_id": "...", "category_code": "GRUNDSTEUER",
      "total_amount": "1200.00", "distribution_key": "WOHNFLAECHE",
      "time_share": { "days": 365, "total_days": 365, "factor": "1" },
      "key_base_total": "300.000000", "key_base_share": "75.000000",
      "exact_share": "300.0000000000", "rounded_amount": "300.00",
      "largest_remainder_cent_applied": false }, ...
  ],
  "result": {
    "lines": [ /* exactly the settlement_line contents */ ],
    "total_cost": "1450.00",
    "advance_payments_total": "1800.00",
    "balance": "-350.00"    // credit
  }
}
```

The inputs are frozen **as a copy** (not as a reference), because the very purpose of the snapshot is independence from later master-data changes (§1.2). Co-ownership/`mea_share`, commercial flag, etc. are carried along to the extent they enter the calculation.

### 1.8 Canonical serialization & hash (determinism is mandatory)

For `snapshot_sha256` to be reproducible and thus usable as tamper evidence, the JSON serialization must be **canonical**:

- Keys **sorted**, fixed separator, no whitespace drift; UTF-8.
- Money/shares as **strings** in the payload (e.g., `"300.00"`, `"210.500000"`), **never** as a JSON `number` (avoid float drift) — fixed scale per field type.
- Date/time values as ISO strings (dates as `YYYY-MM-DD`).
- Order of lists deterministic (stably sorted by IDs).

The hash is formed over exactly this canonical byte representation (a helper function `canonical_json(payload) -> bytes`, reusable for M4 and for the optional `audit_event` hash chain, m2 §6). **Determinism test** (§9): finalizing the same `draft` inputs twice produces the same hash (ADR-0005 guarantees the deterministic rounding that makes this possible).

---

## 2. Distribution algorithm — mathematics per distribution key

All eight keys are stored in `cost_entry.tenant_distribution_key` (M2, SDD §8) as a controlled vocabulary: `MEA · WOHNFLAECHE · BEHEIZTE_FLAECHE · EINHEIT · PERSONENZAHL · VERBRAUCH · EXTERNE_ABRECHNUNG · MANUELLER_BETRAG`. M2 stores only the *selection*; **M3 defines the semantics** (how distribution works). Owner and tenant settlements may carry **different** keys per cost item (SDD §8); for the BK statement M3 uses the **`tenant_distribution_key`** and the **`tenant_apportionable`** sphere (§6); M4 will later use `owner_distribution_key`.

### 2.1 Common basic principle (weight vector → central distribution function)

Every key reduces to **a weight vector** `wᵢ ≥ 0` over the units/addressees participating in the key. The total amount `G` to be distributed is then **always** distributed via the **one** central function `distribute_amount(G, [w₁..wₙ])` (ADR-0005, largest remainder). The distribution mathematics is thus **one** function plus "how do I compute the weight vector" per key:

| Key | Weight `wᵢ` per unit i | Denominator `Σw` | Source |
| --- | --- | --- | --- |
| `MEA` | `unit.mea` (or `ownership_history.mea_share` for co-ownership) | Σ MEA of all participating units | M1 `unit`/`ownership_history` |
| `WOHNFLAECHE` | `unit.area` | Σ areas | M1 `unit.area` |
| `BEHEIZTE_FLAECHE` | `unit.heated_area` | Σ heated areas | M1 `unit.heated_area` |
| `EINHEIT` | `1` per participating unit (per-unit head count) | Number of units | derived |
| `PERSONENZAHL` | Number of persons in the unit in the period | Σ persons | **[OPEN/§18]** data source (see 2.3) |
| `VERBRAUCH` | Measured consumption of the unit (`meter_reading` difference) | Σ consumption | M1 `meter`/`meter_reading` |
| `EXTERNE_ABRECHNUNG` | — (no weight; **direct** per-unit amount) | — | external values (§5) |
| `MANUELLER_BETRAG` | — (no weight; **direct** per-unit amount) | — | manual input |

### 2.2 Area/MEA/unit keys (weighted distribution)

`MEA`, `WOHNFLAECHE`, `BEHEIZTE_FLAECHE`, `EINHEIT` are pure **weighted** distributions:

```
unit_share_i (exact) = G * wᵢ / Σw          (Decimal, unrounded)
→ distribute_amount(G, [wᵢ]) yields the rounded, sum-exact cent amounts (ADR-0005).
```

- **Denominator = sum of the participating units**, not "all units of the property blindly". Who "participates" follows from the cost item and the settlement sphere (e.g., parking spaces without living area do not participate in a living-area key, having weight 0; a commercial unit may be excluded depending on the advance deduction (Vorwegabzug) **[OPEN/§18]**, §6.3).
- **Missing base quantity** (`area`/`mea` is NULL) for a participating unit → **hard error/escalation**, **no** silent 0 (that would redistribute costs unnoticed). The engine validates before calculating (§2.6).
- **MEA with co-ownership:** If a unit with multiple fractional owners participates (`ownership_history.mea_share`), the **owner** sphere (M4) is split further by `mea_share`. For the **tenant BK** (M3), the addressee is the unit's tenant; the unit's MEA is its weight (fractional ownership is irrelevant here, because the tenant uses the whole unit).

### 2.3 `PERSONENZAHL` — key defined, data source open

The distribution mathematics is identical to 2.2 (weight = number of persons in the unit). **[OPEN/§18]** is the **data source**: M1 does not model a time-dependent number of persons per unit/lease. Options: (a) a field on `lease_agreement` (number of persons, possibly with its own small history for intra-year changes), (b) a dedicated small temporal table. **Provisional assumption for M3 test cases:** the number of persons is a per-lease value (constant over the lease period); intra-year changes in the number of persons are **not** represented in M3. Clearly marked as an assumption; professional/product clarification before a productive person-count key. The engine accepts the number of persons as **input** (pure function), so the data-source decision does not touch the engine.

### 2.4 `VERBRAUCH` (consumption-based)

Weight = measured consumption of the unit in the configured property settlement period,
including for a clipped lease statement, determined from `meter`/`meter_reading` (M1:
date, reading, period) as the **difference** (end reading − start reading). Each invoice
retains its service-period cost pool (§3). Mathematics as in 2.2 (weight = consumption
quantity). Special cases:

- **Meter replacement / missing reading** in the period: **[OPEN/§18]** — estimation/substitute procedure is a professional matter (HeizkostenV/metering-service logic) and **not** automatic in M3. Provisionally: for `VERBRAUCH`, the engine requires complete, plausible start/end readings for each participating unit; if anything is missing → escalation, no silent estimation.
- **Heating/hot water** do **not** run through the `VERBRAUCH` key in phase 1, but through `EXTERNE_ABRECHNUNG` (prepared externally in compliance with HeizkostenV, SDD §6.9/§7.1, §5 of this document). `VERBRAUCH` is intended for own meters (e.g., cold water) that the system maintains itself (SDD §6.2.1).

#### Periodic meter prefill since phase 2 (T31)

Without an explicit manual value, the unambiguously nearest start and end reading within
±31 days each is used per unit. The difference stays exact to four decimal places; there
is neither interpolation nor rounding to whole units. Equidistance, duplicate nearest
readings, identical/reversed reading dates, or decreases require manual entry. The
automatic path requires exactly one matching unit meter (`meter_type`, trimmed/uppercased,
equal to the cost category code) and a single relevant consumption type. Multiple possible
replacement meters, common-area meters, and different consumption categories are not
silently combined. A missing unit must not disappear from the distribution denominator.

The draft retrieval `GET /api/settlements/{id}/consumption-suggestions` uses READ scope
permissions with a complete source/classification check. The UI shows the actual reading
IDs, dates, and values; automatic prefills are not sent back as manual overrides. Explicit
manual values, including 0, take precedence. Compute and Finalize determine the basis
afresh under the existing financial lock. Only values actually used automatically are added
to `inputs.meter_consumption_sources` in the snapshot; pure manual-value snapshots and old
finalized settlements remain unchanged. Final views do not query current suggestions. The
existing cost editors convert their UI selection into canonical DB keys via
`costVocabulary`; result lines show their human-readable names.

### 2.5 `EXTERNE_ABRECHNUNG` and `MANUELLER_BETRAG` (direct per-unit amounts)

These two keys **do not distribute** — they adopt an **already fixed amount per unit/addressee**:

- `EXTERNE_ABRECHNUNG`: the amount comes from an externally prepared settlement (metering service for heating/water), per unit (§5). The engine assigns the tenant share directly to the addressee (no key distribution). For a tenant change within the period: the external metering service usually already supplies values **separated per usage period** (interim reading on move-out). **[OPEN/§18]** how exactly this per-tenant split comes in (§5); provisionally: one per-(unit, usage period) amount.
- `MANUELLER_BETRAG`: an amount deliberately set per unit by the case worker (special case). The engine adopts it directly.
- **Invariant here too:** for a per-unit distribution, the **sum of the per-unit amounts must be == total amount of the cost item** (plausibility check, otherwise escalation) — otherwise the reported total would be inconsistent. For `EXTERNE_ABRECHNUNG`, "the total amount" is the tenant-relevant part of the external settlement (§5).

### 2.6 Up-front validation (before every calculation — fail-loud)

The engine checks **before** calculating and **escalates** instead of silently producing something wrong:

1. Every `cost_entry` to be distributed is **active & confirmed** (m2 §3.4) and belongs to the property; its service period **overlaps** the settlement period (otherwise it does not belong in this settlement).
2. For every weighted key: all participating units have the **required base quantity** (no NULL for `area`/`mea`/`heated_area`/consumption); `Σw > 0`.
3. For per-unit keys: sum of the unit amounts == item total amount (±0 cents).
4. Tenant periods + vacancy periods cover the settlement period per unit **without gaps & without overlaps** (follows from the M1 EXCLUDE; the engine nevertheless checks defensively, §3.4).
5. `tenant_apportionable` sphere consistent (no `*_PRUEFEN` without a determined tenant share, §6).

Violations → structured error with item/unit reference (no partial snapshot). The engine is deliberately **strict** here: better a clear escalation than a plausible-looking, wrong settlement.

---

## 3. Pro rata periods (core correctness) — tenant change & vacancy

This is the most difficult and audit-critical part (roadmap: "BK statement for a mixed-use property with **tenant change**"). It combines three time axes: the **settlement period** (on the `settlement_run`), the **service period** per cost item (`cost_entry`, M2), and the **tenant/owner periods** per unit (`lease_agreement`/`ownership_history`, M1).

### 3.1 Yardstick: day-accurate (ADR-0004)

Binding: day-accurate, day-inclusive, exactly as `Decimal` (ADR-0004). Time share of a period in a cost item:

```
factor = days(service_period ∩ period) / days(service_period)
       = (min(SP.to, P.to) − max(SP.from, P.from) + 1) / (SP.to − SP.from + 1)
```

(0 if there is no overlap). The sum of the factors of all gap-free, overlap-free periods across the service period = **exactly 1** (ADR-0004 invariant).

### 3.2 Two-stage distribution: first time, then key — exactly chained

For a cost item whose service period overlaps several tenant/vacancy periods of a unit, the **exact** amount attributed to the **addressee** (= a specific lease) is:

```
exact_addressee = G * (wᵢ / Σw)            // key share of unit i
                    * factor_period        // time share of the addressee period on the item
```

Both factors are chained **exactly** (unrounded); the **largest-remainder rounding applies once at the end** across all final addressee amounts of the item's complete service-period cover (ADR-0005, order discipline). Never round in between. A short run then selects the balanced cells' covered days using the cumulative cent boundaries described above; it never normalizes the entire source amount onto a shorter cover.

**Important — order of the distribution universes:** A cost item is usually distributed **across the whole property** (all units by key), not only across the settled unit. For a **single** tenant BK (`BK_MIETER`, one lease), only the part that falls to **this** unit and **this** tenant period is of interest. The engine therefore conceptually computes:

1. **Key share of the unit** in the total item (universe = all participating units of the property).
2. **Time split of this unit share** across the tenant/vacancy periods **within the unit** in the settlement period.
3. For the BK statement, **the** period of the settled lease is picked out; vacancy periods fall to the **owner** (§3.3) and do not appear in the tenant BK.

This keeps the distribution across the whole property consistent (sum over all units/tenants/vacancy = G), even if only one unit is settled.

### 3.3 Vacancy → owner (SDD §6.9)

A **gap** between two leases of a unit (or before the first / after the last lease, within the settlement period) is **vacancy**. The pro rata cost share of the unit attributable to vacancy days is borne by the **owner** of the unit in this sub-interval (`ownership_history`):

- In the **tenant BK** (M3), the vacancy share does **not** appear (the tenant pays only their own days).
- The vacancy share is nevertheless **not lost**: it is part of the overall distribution (sum over tenants + vacancy + other units = G) and lands with the owner. In M3 it is reported/held as an **owner residual item** (in the payload as a `vacancy_periods` assignment); the *charging* of the owner is the subject of the WEG/owner settlement (M4) or the rental-property owner view. M3 ensures that **no cent disappears through vacancy** (invariant, §9).
- **Property-based invariant (§9):** "vacancy share lands with the owner" = for every item, `Σ(tenant shares of all units) + Σ(vacancy shares) + Σ(shares of unsettled spheres) == G` holds.

### 3.4 Half-open DB intervals vs. day-inclusive engine calculation (edge case)

The M1 EXCLUDE constraints use `daterange [from, to)` (upper bound exclusive); however, `valid_to`/`service_period_to` are professionally **inclusive** days. The engine calculates day-inclusively (ADR-0004). At the **read boundary** (repository → engine input) it is interpreted consistently: `valid_to = 2025-06-30` means "occupied **up to and including** 30.06.". The day-accurate switch (move-out 30.06., move-in of successor tenant 01.07.) yields:

- Tenant A: days … through 30.06. (inclusive).
- Tenant B: days from 01.07.
- **no** gap (no vacancy on 30.06. or 01.07.), **no** overlap.

This is an **explicit golden/edge test** (§9), because an off-by-one here misallocates exactly one day of costs. Also tested: change on the first of the month, change across the year boundary (service period in the prior year, invoice in the following year — the item belongs in the correct year via `service_period`, not `invoice_date`; SDD §14 / m2 §2.2).

### 3.5 Multiple leases of the same unit in the settlement period

If the tenant changes during the year, there are **two** addressees (two leases) for **one** unit. **[DECISION]** Modeling: recommendation — **one `settlement_run` per lease/addressee** (each tenant gets their own BK statement covering their days). The M3 exit ("BK for a mixed-use property with tenant change") is met by producing two `settlement_run`s for the **same unit** (previous and successor tenant), which together split the unit shares (excluding vacancy) day-accurately. The alternative (one run, multiple addressee lines) would be possible, but is impractical for delivery to tenants (each tenant receives *their* document). To be nailed down during implementation.

---

## 4. Rounding — commercial + largest remainder (ADR-0005)

### 4.1 Binding mechanics

Fully specified in [ADR-0005](adr/0005-rounding-largest-remainder.md). Short version of the guarantee that is correctness-critical here:

- A **single** pure function `distribute_amount(total: Decimal, weights: list[Decimal]) -> list[Decimal]` rounds money to cents. Every distribution (every key, every time split) runs through it.
- Algorithm: exact shares → **round down** to cents → award the remainder `k` whole cents by **largest fractional remainder** (deterministic tie-break) → **`Σ cᵢ == total` exactly**.
- **Order:** compute exactly across all stages (time × key), round **once at the end** — never in between.

### 4.2 Why this is the core correctness rule

"Σ of the shares == total costs of the cost category" is a **formal BGH requirement** and an arithmetic self-evident fact that naive cent rounding **violates** (333.33 × 3 ≠ 1000.00). Largest remainder makes the equality a **property of the function**, not a coincidence. This is provable as a property-based test over random amounts/weights (§9) and is the most objective single correctness statement of M3.

### 4.3 Aggregation across multiple cost categories

The tenant's **total additional payment** is `Σ(rounded tenant shares of all cost categories) − advance payments`. Since each cost-category distribution is exact in its sum **on its own** (Σ tenant shares per item consistent with the reported item total), the already rounded tenant shares add up to the total **without renewed rounding** (cent + cent = cent). There is **no** second rounding stage on the total — this prevents a second drift.

### 4.4 Edge cases of the function (tested)

- **`Σw = 0`** (no valid weight): defined error/escalation (§2.6 validation), **no** silent 0 distribution.
- **Negative total amount** (credit note/correction item): largest remainder works sign-consistently (round down toward −∞ vs. commercial rounding — pin down the precise definition in ADR-0005/the implementation; tested).
- **One addressee** (n=1): trivially `[total]`.
- **Remainder k=0** (everything divides evenly): no extra cents, all floor values.

---

## 5. CO2 split (10 tiers) & external heating/water settlement

### 5.1 Principle: values come in, the engine only distributes/adopts

Phase 1 does **not** produce heating/hot-water and CO2 values itself (SDD §6.9, §7.1/§7.2): the metering service supplies HeizkostenV-compliant per-unit values; the CO2 split (CO2KostAufG, 10-tier model by kg CO₂/m²·a) is either **adopted from the external heating cost statement** or computed from entered characteristic values. **Only the tenant share is apportionable** (SDD §6.9). M2 provides for this: `tenant_apportionable = TEILWEISE_PRUEFEN` (CO2) or `UMLAGEFAEHIG_EXTERN_PRUEFEN` (heating/water), `tenant_distribution_key = EXTERNE_ABRECHNUNG`, and the field **`apportionable_tenant_amount`** (the amount explicitly determined as the tenant share).

**Addendum T45 (pure engine, BK connection only in T46):** `heizkosten.py` distributes
given heating/hot-water pools, separated according to §9, with a 50–70 % consumption share
and a 30–50 % base share. For heating, area/heated area/room volume is chosen explicitly;
hot water uses residential/usable area (§§7/8 HeizkostenV). The confirmed §7 special
building case forces 70 %. Costs and units are distributed with the existing
largest-remainder function in stable UUID order. For explicitly non-consumption-based
billing (`consumption_recorded=False`), the TENANT context reports 15 % of the respective
affected user share as a reduction under §12(1) (HALF_UP per user); reduction + payable
amount = gross share. The reduction amount is not apportioned. WEG_OWNER does not receive
this reduction. Individual missing readings, mixed incomplete measurement data, or zero
denominators still lead to escalation, not to silent estimation or zero consumption. The
engine does not assess §11 exceptions, does not invent a §9 cost separation, and does not
compute additional 3 % reductions. Existing external settlements and CO2 delimitations
remain unchanged; T45 itself does not switch any UI/PDF path.

**T46 – internal BK distribution:** `heating_inputs` activates exactly one separate pool
per confirmed `HEIZUNG_ZENTRAL`/`WARMWASSER_ZENTRAL` cost source. Simultaneous
external/manual monetary amounts for this source are not permitted. The engine uses its
gross amount or the explicitly delimited partial apportionment amount; a spanning service
period is delimited with central cent distribution. The existing DB key is retained; UI and
PDF explicitly label internally computed lines as "HeizkostenV intern".

All visible units and gapless tenant/vacancy periods participate. The base-cost basis is
area/heated area or explicit volume × days; under
[§9b HeizkostenV](https://www.gesetze-im-internet.de/heizkostenv/__9b.html) consumption
costs use the building's full-period consumption denominator. Exact interim readings
split the changed unit's consumption-dependent share. The final reading of the previous
user is the opening reading of the subsequent user. Without an interim reading, known
unit-period consumption is split by degree-days if available, otherwise by days; the
current engine has no degree-day inputs, so it uses days. Basic costs always split by
days. This does not estimate missing unit-period consumption under §9a: missing or
ambiguous period readings still require transparent manual values. A 10,000-euro pool
with 70% consumption and the 600/400/1,000 quantities from §3 gives A/B/C
3,221.92 / 1,778.08 / 5,000.00 euros, totaling exactly 10,000.00 euros. Vacancy
retains the gross owner share; only for tenants, in the explicitly chosen
non-consumption-based mode, is the §12 deduction reported.

Compute stores `extra_inputs` in the draft, preview does not. A missing request body uses
the stored intent, explicit empty arrays remove it. Automatic sources are read afresh every
time; Finalize freezes the intent, measured values/IDs, base/consumption pools, bases, and
gross/reduction/payable amount before the hash. The renderer reads only this data. GET
`/{run_id}/heating-consumption` is live-, source-, and financial-scope-protected and
blocked for final runs. No new schema, no implicit combined heating-system pool, and no
change to existing metering-service/CO2 snapshots.

### 5.2 External heating/water settlement (`EXTERNE_ABRECHNUNG`)

The engine **does not distribute**, but adopts the **per-unit tenant share** directly as an item of the unit concerned (§2.5). For a tenant change: separate external values per usage period (interim reading). In the BK line, `note = "extern abgerechnet (Messdienst)"` (settled externally (metering service)) and `distribution_key = EXTERNE_ABRECHNUNG` are reported (BGH: notice of external values, SDD §11).

Undated external amounts explicitly describe the selected run, and are charged in
full to its single matching tenant slice. Entering the same undated amount again in
another free subwindow charges it again; the engine does not infer a shared annual
provider amount. For successive occupancy periods supply separate dated provider
amounts. This rule is unchanged by #122.

> **[OPEN/§18] Input path for external values.** SDD §18 explicitly lists as open: "How external heating/water settlements are entered exactly: amount per unit, PDF attachment, total sum and plausibility check; adoption of the CO2 split value." M3 **does not decide this unilaterally**. The engine accepts external values as **structured input** (pure function); *how* they are captured/stored (a dedicated small table `external_heating_statement` per `cost_entry`/unit/usage period vs. JSONB on the `cost_entry`) is a [DECISION]/§18 question.
> **Provisional assumption for M3 test cases (clearly marked):** External values are available as a per-(unit, usage period) tenant share (`external_amounts` in the payload, §1.7); the engine sums them per unit/addressee and checks plausibility against the item total amount. This assumption is **not** legally certified; the concrete input path and HeizkostenV compliance must be signed off professionally (metering service/tax advisor).

### 5.3 CO2 split (10 tiers, tenant share only)

The CO2KostAufG tier model determines **what percentage** of the CO2 costs the **landlord** bears (increasing with the building's CO₂ intensity, kg CO₂/m²·a) — the remainder is the tenant share. **M3 does not compute the tier model itself** (that is professionally delicate and part of the external heating cost logic). Instead:

- The **tenant share** of the CO2 costs enters the cost item as **`apportionable_tenant_amount`** (adopted from the external settlement or determined by the case worker from the characteristic values, M2). The engine apportions **only** this tenant share (the landlord share stays with the owner and does not appear in the tenant BK).
- For `tenant_apportionable = TEILWEISE_PRUEFEN`, `apportionable_tenant_amount` is the **authoritative** amount to be apportioned (instead of `gross_amount`). The engine **requires** it (validation §2.6/§6); if it is missing → escalation, no silent apportionment of the total amount.

> **[OPEN/§18] CO2 tier calculation.** Whether the system should compute the 10-tier classification (kg CO₂/m²·a → landlord %) **itself** or **always** adopt the tenant share from the external settlement is open (SDD §6.9 names both paths). M3 **provisionally assumes** the **adoption path** (tenant share as input `apportionable_tenant_amount`), because this cleanly separates the engine from the CO2 domain logic and trusts the external, HeizkostenV/CO2KostAufG-compliant source. A dedicated tier calculation would be a delimited building block to be tested separately and **signed off professionally** (recommendation: its own step, not mixed into the M3 distribution core). Clearly marked as an assumption.

---

## 6. Mixed/review-required costs — effective values from the cost item

### 6.1 Principle: the engine apportions nothing automatically

SDD §7.2/§8/§9: for `GEMISCHT_PRUEFEN` (caretaker), `TEILWEISE_PRUEFEN` (CO2), `MANUELL_PRUEFEN` (other operating costs, cable/broadband since 2024), `UMLAGEFAEHIG_EXTERN_PRUEFEN` (heating/water/metering service), the entire amount must **not** be apportioned automatically. The **effective** values (amount to apportion, key, apportionability) are already fixed in the **confirmed cost item** (M2: `tenant_apportionable`, `tenant_distribution_key`, `apportionable_tenant_amount`) — through a **deliberate user decision** at `confirm` (m2 §2.3, "no silently waving through defaults"). **M3 computes only with these effective values** and decides nothing anew.

### 6.2 Concrete handling per sphere in M3

| `tenant_apportionable` | Amount to apportion (tenant) | Engine behavior |
| --- | --- | --- |
| `UMLAGEFAEHIG` | `gross_amount` | normal key (§2). |
| `UMLAGEFAEHIG_EXTERN_PRUEFEN` | per-unit tenant share (external) | `EXTERNE_ABRECHNUNG` (§5.2). |
| `GEMISCHT_PRUEFEN` (caretaker) | `apportionable_tenant_amount` (the extracted apportionable part) | apportions **only** this part; if it is missing → escalation. |
| `TEILWEISE_PRUEFEN` (CO2) | `apportionable_tenant_amount` (tenant share) | §5.3. |
| `MANUELL_PRUEFEN` (other/cable) | `gross_amount` **only if** the user deliberately confirmed the item as apportionable (otherwise it does not belong in the tenant BK) | apportions if confirmed; otherwise not included. |
| `NICHT_UMLAGEFAEHIG` | — | does **not** appear in the tenant BK (belongs in the owner/WEG settlement, M4). |

**Caretaker share (`GEMISCHT_PRUEFEN`):** The administration/repair/maintenance share is extracted **up front** (in the cost item); M3 apportions the remaining apportionable part stored in `apportionable_tenant_amount` according to the selected key. The engine checks that an `apportionable_tenant_amount` is set for `GEMISCHT_PRUEFEN` (otherwise escalation).

### 6.3 Commercial advance deduction (Vorwegabzug) — [OPEN/§18]

SDD §18: "Whether and how commercial units require an **advance deduction (Vorwegabzug)** (different apportionment logic, VAT option)." In mixed use (residential + commercial), an **advance deduction** of the costs attributable to commercial use **before** distribution to the apartments may be legally required (if commercial use causes significantly higher costs). **M3 does not decide this unilaterally.**

- **Provisional assumption for M3 test cases (clearly marked):** **No** automatic advance deduction in M3. `unit.usage_type = GEWERBE` exists in the model and is carried in the snapshot; a commercial unit participates in the distribution under the **selected key** like any other unit (e.g., by area). A desired advance deduction is represented **manually** in M3 (a dedicated cost item for the commercial share with `MANUELLER_BETRAG`/exclusion of the apartments), not computed automatically.
- The **automatic** advance-deduction logic (and VAT option for commercial use) is a delimited building block to be signed off professionally (recommendation: later specification round / with tax advisor). Recorded here, **not guessed away**.

---

## 7. Expected/actual (Soll/Ist): advance payments → additional payment/credit

### 7.1 Calculation

```
expected (total_cost)      = Σ rounded tenant shares of all apportionable cost categories
actual (advance_payments)  = sum of the tenant's operating cost advance payments in the period
balance                    = total_cost − advance_payments_total
   balance > 0  → additional payment (tenant pays)
   balance < 0  → credit (tenant is refunded)
```

`balance`, `total_cost`, `advance_payments_total` are on the `settlement_run` (header) **and** as `settlement_line` (`ADVANCE_PAYMENT`, `BALANCE`) for PDF/verification.

### 7.2 Origin of the advance payment total — [DECISION], M3 manual

Payments (M6) do not exist yet (roadmap: M6 depends on M3). Therefore the **advance payment total is captured manually in M3** (roadmap M3 explicitly: "advance payment total initially manual"). Determination:

- **Field on the settlement run per tenant:** `settlement_run.advance_payments_total` (`NUMERIC(14,2)`), set by the case worker when creating/calculating the `draft`. `advance_payments_source = 'MANUAL'`.
- **Prefill aid (no automatism):** The UI **may** offer a suggestion from `lease_agreement.advance_payment` × pro rata months/days of the period; however, the **authoritative** value remains the manually confirmed amount (what was actually paid can deviate — arrears). The engine calculates with the confirmed `advance_payments_total`, not with the expected advance payment from the contract.
- **M6 transition prepared:** From M6, `advance_payments_source = 'DERIVED'` and the value is drawn from captured `Payment`s (roadmap M6: "real additional payment/credit instead of a manual advance payment total"). The field/`source` discriminator makes the switch clean, without a schema break.

> **[DECISION]** Storage location of the manual advance payment total: **field on the `settlement_run`** (recommended, determined here) vs. a dedicated small table. Recommendation: field on the run — it belongs semantically in the frozen snapshot (what was taken as paid in the settlement is part of the immutable document). Confirm.

### 7.3 Proposing a future advance payment (phase 2, T28)

The historical M3 plan in §7.2 must be distinguished from today's actual path: M6 can
derive booked NK payments; T28 changes neither this actual nor a finalized run.
`GET /api/settlements/{id}/advance-proposal` returns, for an active finalized BK run, the
frozen tenant cost share divided by its frozen tenancy duration within the settlement
period. Whole calendar months count as one, partial months as occupied days/calendar days
of the month. Calendar weights stay exactly rational; money stays Decimal and is
commercially rounded to the cent once. Example: January through 05.02.2024,
0.17 EUR / (1 + 5/29) = 0.15 EUR. A missing, negative, or incoherent historical basis
results in 409, no zero assumption.

The detail page shows the amount and basis; readers see no adoption control. POST to the
same URL accepts only `valid_from` and, after live role, scope, and history-prepare checks,
adds an immutable full rate via the shared T23 service. The base rent is obtained from the
rent history valid up to the effective date. Contract boundaries and duplicate effective
dates remain protected; the shared financial/line lock, audit, and rollback apply. The new
rate is committed before HTTP 201. The original contract, settlement snapshot/lines, and
booked payments are not changed. The real UI verification case finalizes receipt-bound
tenant costs of 1,200 EUR, adopts 100 EUR from 01.01.2027, and checks all 24 tenant ledger
items: annual expected amount (Soll) 9,600 instead of 10,200 EUR with the actual (Ist)
unchanged at zero.

---

## 8. Formal mandatory disclosures (BGH) + PDF layout

### 8.1 Mandatory BGH disclosures → where they are carried

SDD §11 lists the formal minimum requirements (BGH case law for tenant settlements). Each is stored in structured form in `settlement_run`/`settlement_line` (not only in the PDF), so that they are machine-verifiable **and** immutable in the snapshot:

| Mandatory BGH disclosure (SDD §11) | Carrier in the model |
| --- | --- |
| Property and **settlement period** | `settlement_run.property_id`, `period_start`/`period_end` |
| Addressee and unit reference | `settlement_run.unit_id`/`lease_agreement_id`/`tenant_profile_id` + payload addressee |
| **Total costs per cost category** | `settlement_line(line_type='COST').total_amount` |
| **Applied distribution key per item** | `settlement_line.distribution_key` (+ `distribution_base_total`/`_share` for the calculation path) |
| **Calculation of the addressee's share** | `settlement_line.distribution_base_share` / `time_share_days` / `amount` (+ payload `computation[]` full calculation path) |
| **Deduction of the advance payments** | `settlement_line(line_type='ADVANCE_PAYMENT')` + `advance_payments_total` |
| **Additional payment or credit** | `settlement_line(line_type='BALANCE')` + `settlement_run.balance` |
| Notice of external settlement values | `settlement_line.note` (e.g., "extern abgerechnet") |

This makes "formally complete" (M3 exit) a **verifiable** property: a test (§9) asserts that all of these fields are present/consistent for a `BK_MIETER` run.

> **[OPEN/§18] Exact PDF structure.** SDD §18: "Exact PDF structure for the operating cost statement …" is open. M3 provides **all data** for a complete PDF; the **concrete layout** (column arrangement, texts, footnotes, objection-deadline notice per § 556 III BGB) is an [OPEN/§18]/design question. Provisionally, a functional, BGH-complete standard layout is assumed; the final layout is to be agreed with the product/domain side.

### 8.2 PDF pipeline (WeasyPrint, M0 infrastructure)

PDF generation uses the swappable pipeline set up in M0 (`app/core/pdf.py`, `PdfRenderer` protocol, default **WeasyPrint**). M3:

- generates **HTML** from the **finalized** `settlement_run`/`settlement_line` (never from `draft` — the sent document must correspond to the frozen snapshot) via a template (e.g., `app/templates/bk_abrechnung.html`), then `render_pdf(html)`.
- The PDF layer is **strictly separated** from the engine (SDD §16): it only reads the finished result and recalculates **nothing**. Test: PDF values == `settlement_line` values (no drift between calculation and presentation).
- WeasyPrint is deterministic (same input → same PDF), which fits the snapshot philosophy.

Since T47, a finalized BK settlement can be explicitly sent to the authorized tenant
contact via SMTP. Source/contact export and the separate financial-scope capability `send`
are checked live. Sending changes neither the run nor the lines/snapshot; the PDF
attachment actually sent is retained in the existing document storage with a hash and an
append-only send attempt under the run ID. The history opens these exact bytes instead of
re-rendering and also checks the original contact export permissions. SMTP acceptance is
not proof of delivery; unclear/open attempts are not retried automatically.

---

## 9. Synthetic golden tests (objective M3 yardstick)

There are **no** real sample settlements. The test cases are constructed **synthetically** from the legal rules + **hand-calculated** expected results. Two kinds of tests, both binding for M3 acceptance:

### 9.1 Golden tests (hand-calculated expected results, exact cent values)

Each golden test is a complete scenario **worked out by hand**, with the expected result (every cent) stored in the test. Mandatory scenarios:

1. **Trivial area distribution:** 1 property, 3 apartments (75/50/25 m²), 1 cost category (property tax 1200.00 €/year), full year, no change. Expected: 600/400/200 €. Tests key + Σ == total.
2. **Rounding case (largest remainder):** 1000.00 € ÷ 3 equal units → 333.34 / 333.33 / 333.33 (Σ = 1000.00). Tests that **no** cent is lost and that the tie-break is deterministic.
3. **Intra-year tenant change (core):** 1 apartment, tenant A 01.01.–30.06., tenant B 01.07.–31.12.; cost category full year. Expected: day-accurate split (181/365 vs. 184/365 in 2025, no leap year), two `settlement_run`s. Tests ADR-0004 + day-inclusive switch (§3.4).
4. **Vacancy:** 1 apartment, tenant A 01.01.–31.03., then vacancy 01.04.–31.05., tenant B from 01.06.; cost category full year. Expected: A bears 90 days, B bears 214 days, **61 days of vacancy with the owner** (not in tenant BK). Tests §3.3 + invariant "vacancy → owner".
5. **Mixed-use property with tenant change + vacancy + multiple cost categories/keys + CO2 (exit scenario):** mixed-use property, multiple units (one rented with an intra-year change, one owner-occupied/owner, one temporarily vacant, one commercial unit), cost categories with **different keys** (property tax by area, waste by unit, cold water by consumption, heating external, **CO2 with tenant share from `apportionable_tenant_amount`**, caretaker `GEMISCHT_PRUEFEN` with extracted share, WEG administrator costs `NICHT_UMLAGEFAEHIG` → must **not** appear in the tenant BK). **Fully hand-calculated.** This is the **direct representation of the M3 exit criterion**.
6. **Service period across the year boundary:** invoice with `invoice_date` in January 2026, `service_period` December 2025 → belongs in the 2025 settlement (via `service_period`, not `invoice_date`). Tests SDD §14.
7. **External settlement with interim reading on tenant change:** heating costs external, separate per-tenant values. Tests §5.2 (direct adoption, no key distribution).
8. **Snapshot/hash determinism:** scenario 5 finalized twice (on identical inputs) → **identical** `snapshot_sha256`. Tests §1.8 + ADR-0005 determinism.

Each golden test checks **not only the final total**, but the **individual `settlement_line` amounts, key base values, and time shares** against the hand calculation — otherwise a compensating double error could slip through.

### 9.2 Property-based invariants (Hypothesis, over random inputs)

These catch classes of errors that individual examples miss (architecture.md §6: pytest + Hypothesis was chosen precisely for this). Binding invariants:

- **I-A "sum of the shares = total costs":** For random `total ∈ [−10000, 10000]` €, random weight vectors (n ∈ [1, 50], `wᵢ ≥ 0`, `Σw > 0`): `Σ distribute_amount(total, w) == total` **exactly** (in cents). The most fundamental invariant (ADR-0005).
- **I-B "no cent is lost / non-negativity of the sub-cent distribution":** Each share deviates by **at most 1 cent** from the rounded-down exact share; extra cents awarded == `k`.
- **I-C "time shares sum to 1":** For random gap-free, overlap-free period decompositions of a service period: `Σ factor == 1` exactly (ADR-0004).
- **I-D "vacancy share lands with the owner / completeness":** For random mixed scenarios, for each item: `Σ(tenant shares) + Σ(vacancy shares) + Σ(other spheres) == G` (nothing disappears).
- **I-E "determinism":** The same input twice → bit-identical canonical payload and identical hash.
- **I-F "non-apportionable never appears in the tenant BK":** `NICHT_UMLAGEFAEHIG` items appear in no `BK_MIETER` `settlement_line`.
- **I-G "consistency payload ↔ settlement_line":** Σ/values in `snapshot_payload.result` == Σ/values of the `settlement_line` rows.

These invariants are the **objective, model-independent** yardstick: they hold for *every* valid input, not just for the invented examples.

---

## 10. Implementation checklist

> M3 is built as a correctness-critical step, because the distribution engine is correctness-critical. The order is **engine first (pure functions + tests), then persistence/snapshot, then API, PDF last** (roadmap: "engine first here, then the PDF"). **Prerequisite:** the human has confirmed the [DECISION] decisions (closing section) and ADRs 0004/0005. No commit before that.

### 10.1 Engine as pure functions (first, highest test density)

- [ ] `app/engine/` (new package, **deliberately separate** from `services`/`api`/`models` — SDD §16): no DB, HTTP, or ORM imports; exclusively `Decimal`, `date`, dataclasses in/out.
- [ ] `app/engine/rounding.py`: `distribute_amount(total: Decimal, weights: list[Decimal]) -> list[Decimal]` (ADR-0005, largest remainder, deterministic tie-break). Secure it with property-based tests (I-A/I-B) **first**, before anything builds on it.
- [ ] `app/engine/periods.py`: day-accurate time arithmetic (ADR-0004) — `day_count(from, to)`, `overlap_days(p1, p2)`, `time_factor(service_period, period)`, `split_into_tenant_and_vacancy_periods(settlement_period, leases) -> list[Period]` (with owner as addressee for vacancy). Tests I-C + day-inclusive edge cases (§3.4).
- [ ] `app/engine/distribution.py`: per key, the weight vector computation (§2) + the two-stage chaining (time × key, §3.2). Inputs as frozen dataclasses (mirror of the payload `inputs`).
- [ ] `app/engine/settlement.py`: orchestrates a `BK_MIETER` calculation → `EngineResult` (lines + totals + calculation path) as a pure data structure. **Fail-loud validation** (§2.6).
- [ ] `app/engine/canonical.py`: `canonical_json(payload) -> bytes` + `sha256_hex(bytes)` (§1.8) — deterministic, sorted, money as string.
- [ ] Golden tests (§9.1) as `tests/engine/test_golden_*.py` with hand-calculated expected values; property-based (§9.2) as `tests/engine/test_invariants.py` (Hypothesis).

### 10.2 Persistence & snapshot (afterwards)

- [ ] Models `app/models/settlement_run.py`, `settlement_line.py` (style of `lease_agreement.py`/`cost_entry.py`: mixins, `Numeric`, `Mapped`, string+CHECK). Money `Decimal`.
- [ ] **Migration `0009_settlement_tables`:** `settlement_run` + `settlement_line` with CHECK constraints, FKs (`RESTRICT` to `organization`), indexes (§1.4/§1.5), `superseded_by_id` (forward, E-4 convention).
- [ ] **Migration `0010_settlement_rls_policies`:** four policies per table exactly in the `0007` pattern (`NULLIF(current_setting('app.current_org_id', true),'')::uuid`, fail-closed). Both tables with a DELETE policy (both have an editable `draft` state → full CRUD grant; [DECISION] 1.6).
- [ ] **Migration `0011_settlement_immutability_triggers`** (ADR-0003 §3, pattern of `0008`): `BEFORE UPDATE/DELETE` trigger for `settlement_run` (freeze condition `status='finalized'`, only permitted change `superseded_by_id`) and `settlement_line` (freeze condition: parent run `finalized` → every mutation forbidden). Trigger source extensively commented; `downgrade()` drops trigger + function.
- [ ] Extend `TENANT_TABLES` in `tests/test_rls_coverage.py` with `settlement_run`, `settlement_line`.
- [ ] Repository `app/repositories/settlement_repo.py` (style of `cost_entry_repo.py`): active query (`superseded_by_id IS NULL`), read aggregation of the `cost_entry` active chain + `unit`/`lease`/`ownership` as engine input.

### 10.3 Service (snapshot/finalize atomic)

- [ ] `app/services/settlement_service.py` (style of `cost_entry_service.py`): `create_draft`, `compute` (runs the engine, writes `snapshot_payload` + `settlement_line` in `draft`, **overwritable**), `set_advance_payments` (§7), `finalize` (**atomic**, analogous to `confirm`), `create_correction_run` (cancellation/new, §1.6).
- [ ] `finalize` as **one** transaction: final engine calculation → set `snapshot_payload` → compute `snapshot_sha256` → (re-)write `settlement_line` → `status='finalized'`, `finalized_at/by`, `total_cost`/`balance` → `audit_event` (`settlement.finalized`) → commit. From the commit on, the ADR-0003 trigger takes effect. Rollback on any error (no half-finalized run).
- [ ] `audit_event` actions: `settlement.created`, `settlement.computed`, `settlement.finalized`, `settlement.superseded` (via the existing `AuditRepository`).

### 10.4 API (API-first, RBAC as in M0/M2)

Since phase 2 (T27), `POST /api/settlements/batch` supplements the single creation with an
atomic batch run with property, inclusive period, and optional accounting scope. Only
contracts of the authorized scope units that overlap the period are created via
`create_draft`; active BK runs with identical contract/property/period/scope are skipped
unchanged. Response: `created`, `skipped` with contract/run ID, and, when needed, `errors` with
contract ID and the overlap message. A conflicting lease does not roll back
other eligible leases; an entirely conflicting batch retains 409. The existing
organization-wide financial lock serializes parallel batch runs; other errors still
roll back the audit and already created drafts. Source/scope permissions are checked before an empty
response. No computation, coverage, or finalization, and no invented advance payments: new
financial values stay `null`.
Under `/abrechnungen`, "BK-Sammellauf" (BK batch run) guides through property, period, and
scope; result counts and list are refreshed, and the same period is repeatable.

Implemented BK behavior: `POST /api/settlements/{id}/finalize` ends the DB transaction through `Depends(get_db, scope="function")` **before** the HTTP success response. Thus an immediately following PDF request already sees the finalized snapshot; drafts remain blocked with HTTP 409. The regression test checks the stored status from an independent DB session at `http.response.start`, since ASGITransport alone waits for the later request cleanup and thereby masks this race condition.

- [ ] `app/api/settlements.py`: `POST` (create run, `require_write_dep`), `POST /{id}/compute`, `PATCH /{id}/advance-payments`, `POST /{id}/finalize`, `POST /{id}/correction`, `GET` (list/detail, all roles read), `GET /{id}/pdf` (finalized → PDF stream). READ_ONLY: GET only. Foreign ID → **404** (not 403), as in M0/M2. Register the router in `app/main.py`.

### 10.5 PDF (last, strictly separated)

- [ ] `app/templates/bk_abrechnung.html` (all mandatory BGH disclosures §8.1) + `app/services/settlement_pdf.py`, which renders **only** from the finalized `settlement_run`/`settlement_line` (`render_pdf`, WeasyPrint). **No** recalculation. Test: PDF values == `settlement_line` values.

### 10.6 Definition of Done for M3 (handover to M4)

- [ ] Engine package pure (no DB/HTTP imports), `Decimal` throughout, one central `distribute_amount`.
- [ ] **Golden tests §9.1 green** (hand-calculated expected values, incl. exit scenario 5) — the objective "reproducibly correct" yardstick.
- [ ] **Property-based invariants §9.2 green** (I-A through I-G), esp. "Σ shares == total costs", "Σ time shares == 1", "vacancy → owner", determinism.
- [ ] `settlement_run`/`settlement_line` with RLS (four policies, fail-closed) + ADR-0003 trigger (`finalized` immutable) + snapshot hash.
- [ ] `finalize` atomic; `audit_event` for all operations; correction = new run (never mutation).
- [ ] Mandatory BGH disclosures complete in `settlement_line`/PDF (formally complete); PDF recalculates nothing.
- [ ] RLS coverage test + `test_tenant_isolation` extended with the M3 tables; **all 141 existing tests stay green.**
- [ ] M3 exit objectively met: BK statement for a mixed-use property with tenant change + vacancy is **reproducibly correct** (golden test 5) and **formally complete** (mandatory-disclosures test).
- [ ] No **[DECISION]**/**[OPEN/§18]** item "guessed away" unmarked; provisional assumptions clearly labeled; professional sign-off (tax advisor/attorney specializing in the field) recommended.

---

## 11. Assumptions made (M3)

| # | Assumption | Impact if wrong | Clarify with |
| --- | --- | --- | --- |
| **M3-A1** | Time share **day-accurate** (ADR-0004), not month-accurate. | Month-accurate would yield a different cent allocation on change; engine method + tests would have to be adapted. | Human/tax advisor (ADR-0004) |
| **M3-A2** | Rounding **commercial + largest remainder** (ADR-0005); Σ == total exactly. | A different prescribed rounding → adapt the central function. | Human/tax advisor (ADR-0005) |
| **M3-A3** | One `settlement_run` **per tenant/lease** (tenant change = two runs per unit). | One-run-multiple-addressees would change model/PDF. | Human (§3.5) |
| **M3-A4** | Tenant share for CO2/heating arrives as **input** (`apportionable_tenant_amount`/external); the engine does **not** compute the tier model itself. | An own 10-tier calculation would be a delimited additional building block to be signed off professionally. | Tax advisor/metering service (§5) |
| **M3-A5** | **No** automatic commercial advance deduction in M3; commercial use participates by key; advance deduction manual if needed. | Automatic advance deduction is its own building block. | Tax advisor/attorney specializing in the field (§6.3) |
| **M3-A6** | Advance payment total **manual** on the run (`advance_payments_source='MANUAL'`); M6 replaces it with `DERIVED`. | M6 connection prepared via the `source` discriminator. | (Roadmap M6) |
| **M3-A7** | Number of persons (`PERSONENZAHL` key) as a per-lease value, constant over the lease period (M1 does not model this time-dependently). | A time-dependent number of persons would need a model extension. | Product (§2.3) |
| **M3-A8** | External values and unit-period consumption must be **complete & plausible**; missing → escalation. Missing interim readings split known consumption by days (§3). | Substitute/estimation procedure (HeizkostenV) is its own professional building block. | Tax advisor/metering service (§2.4/§5) |

---

## 12. Summary of the open decisions (for the human)

| # | Item | Type | Recommendation | When |
| --- | --- | --- | --- | --- |
| **E3-1** | Time yardstick day-accurate vs. month-accurate | [DECISION] | **day-accurate** (ADR-0004) | before M3 implementation |
| **E3-2** | Rounding mechanics | [DECISION] | **commercial + largest remainder** (ADR-0005), Σ==total exactly | before M3 implementation |
| **E3-3** | One run per tenant/lease vs. one run with multiple addressees | [DECISION] | **one run per tenant** (§3.5) | at implementation |
| **E3-4** | Storage location of manual advance payment total | [DECISION] | **Field on the `settlement_run`** (`advance_payments_total` + `_source`) (§7.2) | at implementation |
| **E3-5** | `settlement_run`/`settlement_line`: trigger-only (no grant revocation, because `draft` is editable) | [DECISION] | **Trigger-only**, full CRUD grant (§1.6) | at implementation |
| **E3-6** | `run_type` shell for WEG/plan/asset report in the same table | [DECISION] | **yes, same shell**, detail payload per type in M4 (§1.3) | M4 (reserve now) |
| **O3-1** | Input path of external heating/water values (amount/unit, PDF, plausibility) | [OPEN/§18] | not unilaterally; provisionally per-(unit, usage period); tax advisor/metering service | before production use |
| **O3-2** | CO2 10-tier: compute ourselves vs. adopt tenant share | [OPEN/§18] | provisionally **adopt** (`apportionable_tenant_amount`); own tier logic = separate building block requiring sign-off | before production use |
| **O3-3** | Commercial advance deduction (different apportionment logic, VAT option) | [OPEN/§18] | provisionally **none** automatically; manual if needed; tax advisor/attorney specializing in the field | later round |
| **O3-4** | Exact PDF structure of the BK statement (layout, objection-deadline notice) | [OPEN/§18] | data fully ready; layout with product/domain side | before production use |
| **O3-5** | Data source/logic `PERSONENZAHL`, consumption substitute procedure | [OPEN/§18] | provisional assumptions (M3-A7/A8); clarify professionally | before production use of these keys |

> **No self-certification (stance):** This design makes the distribution, the pro rata periods, the rounding, and the expected/actual calculation (Soll/Ist) **transparent, deterministic, and reproducible** (golden tests + property-based invariants as the objective yardstick). It **cannot** guarantee that a concrete BK statement withstands court or audit scrutiny in an individual case. The professional correctness of the rules (esp. CO2 tiers, commercial advance deduction, external heating costs, retention) must be signed off by a **tax advisor/attorney specializing in the field** before productive use.

---

## 13. What this document deliberately does NOT decide

- **WEG annual settlement / settlement balance (Abrechnungsspitze) (WEMoG), economic plan, asset report (§ 28 IV WEG):** M4 — reuses this engine (`owner_distribution_key`, owner sphere, `reserve_relevant` separation) and the same snapshot/trigger mechanism; `run_type` is reserved (§1.3).
- **Own CO2 10-tier calculation, commercial advance deduction, consumption substitute procedure:** delimited building blocks requiring professional sign-off (§5/§6/§2.4, SDD §18).
- **Real payments/tenant ledger (Mietkonto) (advance payment `DERIVED`):** M6 (§7.2).
- **Exact PDF layout / objection-deadline texts:** SDD §18 (§8.1).
- **§ 35a EStG disclosure** in owner settlements: M4/SDD §18.
- **Final legal sign-off:** tax advisor/attorney specializing in the field — not replaceable by this document.
