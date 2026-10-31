# M6 design: payments, tenant ledger (Mietkonto) & open items

Browser acceptance journeys for bank import/assignment, rent changes, deposits and dunning now live in the `@browser` features; see [the feature runner](../features/README.md#browser-features).

As of: 2026-06-02 · Version 0.1 (design, before implementation)
Reference: [sdd-property-management-mvp.md](sdd-property-management-mvp.md) v0.2 §3 (non-goal: bank import), §5.4 (incoming payments — *note: the SDD numbers them §6.3/§6.7*), §6.3 (bank accounts), §6.7 (rent/payment management), §11 (PDF), §13 (`Payment`/`OpenItem`), §14 (chronology), §16, §17, §18 · [archived roadmap](archive/roadmap.md) M6 · [architecture.md](architecture.md) §2.6 (segregation of funds (Vermögenstrennung)), §5.1 (conventions), §5.3 (`bank_account` anchor), assumption A-4 · [m3-abrechnungs-engine.md](m3-abrechnungs-engine.md) §7 (`advance_payments_source` MANUAL→DERIVED) · [m4-weg-abrechnung.md](m4-weg-abrechnung.md) §2.4 (paid_advances, settlement balance (Abrechnungsspitze)) · ADRs: [0003](adr/0003-gobd-immutability-mechanism.md) (reused), [0005](adr/0005-rounding-largest-remainder.md) (reused), [0006](adr/0006-valid-to-convention.md) (reused), [0007](adr/0007-weg-settlement-balance-and-economic-plan.md) (monthly basis, balance) · **[0009-expected-actual-and-payment-receipt.md](adr/0009-expected-actual-and-payment-receipt.md) (new)** · **[0010-segregation-of-funds-payments.md](adr/0010-segregation-of-funds-payments.md) (new)**
Status: **Draft for confirmation by the human — no application code, no migration, no commit**

> This document specifies **M6** (payments, tenant ledger, open items) down to the arithmetic. M6 is the point where the **flow of money** becomes real and the bridge `advance_payments_source: MANUAL → DERIVED` prepared in M3/M4 is closed: settlements are to be able to **derive the advance payment total from recorded payments** instead of maintaining it manually. **Manual remains as a fallback.**
>
> **Binding status:** SDD (v0.2), roadmap, `architecture.md`, `m3-abrechnungs-engine.md`, `m4-weg-abrechnung.md` and ADRs 0001–0008 are the source of truth and remain **untouched**. This document is additive (new doc + ADR-0009/0010). Decisions the human is to confirm are marked **[DECISION]** (decision); open SDD §18 / domain-expertise points are marked **[§18/domain]** (not decided unilaterally; provisional, clearly marked assumption; domain sign-off recommended).
>
> **Manual recording — no bank import.** SDD §3 is explicit: "No bank import (CAMT/MT940) and no automatic payment reconciliation." M6 records payments **exclusively manually**; the only "automation" is the **deterministic, pure derivation** of recorded payments into settlement figures (§6) — no bank parsing, no matching against bank statements.
>
> **No self-certification:** This design makes expected amounts (Soll), actual amounts (Ist), open items and the settlement integration *transparent, deterministic and reproducible* and discloses every assumption. It **cannot** guarantee that the tenant ledger / Hausgeld (monthly owner contribution) account is accounting-sound or audit-proof in a concrete case (M6 is explicitly **not** financial accounting, SDD §1/§3). Before productive use, the domain correctness (ledger depth, security deposit, GoBD voucher (Beleg) quality of manual payments, trust account segregation) must be signed off by a **tax advisor / specialist lawyer**.

---

## 0. Scope, exit criterion, connection to M0–M5

**M6 exit (roadmap):** "Expected/actual (Soll/Ist) per person is correct; **a settlement pulls recorded payments automatically**." This is the objective yardstick. "Soll/Ist is correct" is demonstrated by the **synthetic golden tests** (§7) with hand-calculated expected/actual/balance values; "settlement pulls payments automatically" by a golden test that checks a **DERIVED** advance payment in an operating cost statement (Betriebskostenabrechnung) against the **manual** variant (bit-identical result, §7.3).

**M6 builds on a verified foundation (M0–M5, 291 tests green) and adopts its conventions unchanged.** M6 is **additive**: it adds `payment` + expected-amount calculation + assignment and closes a prepared connection in M3/M4. The key finding from the code review: **the M3/M4 foundation is already built to be M6-capable.**

| Existing foundation (M0–M5, live) | How M6 uses it |
| --- | --- |
| `bank_account` (M1 anchor): `organization_id`, `property_id`, `account_type` (`LAUFEND`/`RUECKLAGE`/`KAUTION`), IBAN/BIC/bank — **no** balance/booking columns (model comment: "model anchor: schema + FK only; full implementation in M6") | **Pivot of the segregation of funds** (ADR-0010). M6 adds **no** balance field to `bank_account` — balances stay **derived** from `payment` (§1.1). |
| `settlement_run.advance_payments_total` (`NUMERIC(14,2)`) + `advance_payments_source` (`String(12)`, `MANUAL`\|`DERIVED`, listed as a frozen column in the immutability trigger, migration `0011`) | **The prepared bridge.** M6 sets `DERIVED` and fills `advance_payments_total` from payments (§6). **No schema break** — the columns exist and are frozen from `finalized` on. |
| `lease_agreement` (temporal, EXCLUDE per unit, `base_rent`, `advance_payment`, `valid_from`/`valid_to`) | **Expected-amount source for tenants** (base rent + operating cost advance payment), monthly basis, day-accurate partial months (§2). |
| `economic_plan_line(line_type='OWNER_ADVANCE')` with `owner_profile_id`, `unit_id`, `planned_amount` (annual), `monthly_amount` (monthly, ADR-0007 E4-3b) | **Expected-amount source for owners** (Hausgeld advance (Hausgeld-Vorschuss)) from the **finalized** economic plan (Wirtschaftsplan) (§2.2). |
| WEG service: `WegComputeExtras.paid_advances: list[ManualOwnerAdvance]` (per owner, **informational**, MANUAL) and `OwnerAdvanceInput(plan_advance_total)` (expected amount from plan); balance = `actual − expected(plan)` (ADR-0007), `paid_advances` **separate** | M6 replaces the **MANUAL** `paid_advances` with **DERIVED** ones from payments (§6.3). **The settlement balance remains `actual−expected(plan)` — payments do NOT flow into it** (informational payment status only). |
| `cost_entry` pattern: `status` (`draft`→`confirmed`) + `superseded_by_id` (forward pointer) + ADR-0003 trigger (`status='confirmed'` freezes) + reversal as a new version | **1:1 template for `payment`**: `status` (`draft`→`booked`), `reversed_by_id`, ADR-0003 trigger (`status='booked'` freezes), correction = reversal + rebooking (ADR-0009). |
| RLS pattern (`ENABLE`+`FORCE`, four policies, `NULLIF(current_setting('app.current_org_id', true),'')::uuid`, fail-closed, migration `0010`/`0014`) | `payment` gets exactly this pattern (analogous migration). |
| `TenantRepository`/`base.py`, atomic service transaction (`*_service.confirm`/`finalize`), `AuditRepository`, `app/core/temporal.py` (`db_exclusive_to_engine_inclusive`, ADR-0006), `app/engine/rounding.py` (`distribute_amount`), `app/engine/periods.py` (day-accurate) | Templates/building blocks 1:1 for `PaymentRepository`, `payment_service`, `app/engine/mietkonto.py` (pure expected/actual functions). |
| Money `NUMERIC(14,2)`/`Decimal`, ratios `NUMERIC(12,6)`, UUID PK, `TIMESTAMPTZ`, string+CHECK instead of DB enum, FK `ondelete=RESTRICT` to `organization` | Adopted throughout. |
| Migrations `0001`–`0015` present | M6 adds **`0016`–`0019`** additively (order in §8). |

**Not in M6:** dunning / dunning levels / `DunningNotice` (M7 — M6 supplies the *computed* overdue items as input; the dunning persistence is decided by **M7**, not M6, ADR-0009 §1/F6-1). Bank import/matching (SDD §3 non-goal, permanent). Full security deposit accounting with interest (§3.4, [§18/domain]). Bank statement reconciliation.

**Architecture principle (SDD §16, strict here, as in M3/M4):** The **expected/actual/balance arithmetic is a layer of pure functions** (`Decimal`/`date` in/out, no DB/HTTP/I/O), separated from persistence, API and PDF. This is the prerequisite for the testable, deterministic derivation into the settlement (§6) and for the golden tests (§7). M6 logic goes into the same `app/engine/` package, not into services.

---

## 1. Data model

### 1.1 `bank_account` — expand now or leave unchanged?

> **[DECISION E6-5]: Leave `bank_account` structurally unchanged — add NO balance/booking columns.** *(Recommendation)*

Today the anchor has `organization_id`, `property_id`, `account_type` (`LAUFEND`/`RUECKLAGE`/`KAUTION`), IBAN/BIC/bank — and deliberately **no** balance column. That is correct and stays so:

- **The account balance is a derived quantity**, not a stored value: `balance(account) = Σ inflows − Σ outflows` over the `payment` rows of this account (analogous to the derived-expected-amount philosophy, ADR-0009). A stored balance would drift from the bookings (the same two-sources-of-truth trap as with the expected amount) and would be a second state that needs maintenance.
- **M4 (§6.2) already anticipated exactly this:** Today the asset report (Vermögensbericht) uses a **manually recorded reference-date balance** per account (ADR-0008), precisely *because* `bank_account` has no balance column. From M6 on, the same report can **optionally derive the balance from `payment`** (§6.5) — without restructuring `bank_account`.
- **One additive hardening** is needed (not for balances, but for segregation-of-funds coherence, ADR-0010): a **UNIQUE index `(organization_id, id, property_id)`** on `bank_account`, so that `payment` is guaranteed to be bound to the right property via a composite FK (§4). This is not an "expansion" of the account but the prerequisite of trust-safe assignment.

**Optional, additive fields (not mandatory for M6):** `opening_balance NUMERIC(14,2)` + `opening_balance_date DATE` as an **opening balance anchor** (account balance at a reference date from which `payment` movements are carried forward). Rationale: without an opening balance, the derived account balance is only relative to the payments *recorded in the system*, not the real bank balance (payments before the system was introduced are missing). **[DECISION E6-6]** whether M6 introduces this opening balance anchor (recommended: yes, small and additive) or defers it to after M9. It is **not** a booking entry but a property manager's reference-date statement (like the asset report reference-date balance, ADR-0008).

**T55 – Lifecycle without new balances:** `is_active=false` ends use for new payments,
bank imports and SEPA suggestions; historical references remain preserved and
readable. Reactivation is explicitly possible. Metadata is changed under the
live/source prepare permission and the financial lock. A rename leaves SEPA
mandates/fingerprints unchanged; IBAN/BIC that are in use are also protected against
rewriting on the SQL side. A booked reversal that matches the original exactly may
still be created, but not an arbitrary new payment on the inactive account. Service
providers use the same active status for new cost references. Management mandates are
separate from this and keep their existing bound-period protection.

### 1.2 `payment` (new table, tenant-scoped, RLS, immutable from `booked` on)

A `payment` row is **one recorded money movement** (inflow or outflow) against an account, assigned to a counterparty and a purpose/type. It is a **booking voucher (Buchungsbeleg)** (GoBD, ADR-0009): immutable from status `booked` on; correction = reversal + rebooking.

| Column | Type | Meaning / rationale |
| --- | --- | --- |
| `id` | `UUID` PK | `gen_random_uuid()`, as everywhere. |
| `organization_id` | `UUID NOT NULL` FK→`organization` (`RESTRICT`) | I1, RLS. |
| `bank_account_id` | `UUID NOT NULL` FK→`bank_account` (`RESTRICT`) | **Mandatory pivot** (ADR-0010): every payment runs against exactly one account. |
| `property_id` | `UUID NOT NULL` FK→`property` (`RESTRICT`) | Property reference (segregation of funds). **Mirrored from the account**, bound to the account by composite FK (§4) — not freely selectable. |
| `management_mandate_id` | `UUID NULL` FK→`management_mandate` (`RESTRICT`) | Mandate reference (segregation-of-funds anchor, architecture.md §2.6). Derived from the account/property. |
| **Money movement (I4):** | | |
| `direction` | `String(3) NOT NULL` | `IN` (inflow) \| `OUT` (outflow). CHECK. |
| `amount` | `NUMERIC(14,2) NOT NULL` | **Amount, always positive**; the sign is carried by `direction` (clearer than signed amounts; a reversal is its own `IN`/`OUT` row, not a negative amount). `CHECK (amount > 0)`. |
| `value_date` | `DATE NOT NULL` | Value/booking date of the payment (authoritative for period assignment, §6). |
| **Counterparty (exactly one role):** | | |
| `tenant_profile_id` | `UUID NULL` FK→`tenant_profile` (`RESTRICT`) | for a tenant payment. |
| `owner_profile_id` | `UUID NULL` FK→`owner_profile` (`RESTRICT`) | for an owner payment. *(exactly **one** of the two set for person-related types; CHECK §1.4.)* |
| **Purpose/type (controlled vocabulary, §1.3):** | | |
| `payment_type` | `String(24) NOT NULL` | `NK_VORAUSZAHLUNG` \| `HAUSGELD_VORSCHUSS` \| `RUECKLAGENANTEIL` \| `KALTMIETE` \| `SONDERZAHLUNG` \| `NACHZAHLUNG` \| optionally `KAUTION`. CHECK. **The type controls the settlement integration (§6) — it is correctness-critical.** |
| **Assignment (what the payment settles):** | | |
| `lease_agreement_id` | `UUID NULL` FK→`lease_agreement` (`RESTRICT`) | for tenant-related types: the lease whose expected amount the payment serves. |
| `period_year` | `SMALLINT NULL` | Settlement/expected-amount year to which the payment is assigned (for the DERIVED aggregation, §6). |
| `period_month` | `SMALLINT NULL` | optional month reference (1–12) for monthly expected-amount posting; NULL = whole period/lump-sum payment. CHECK 1–12. |
| `settlement_run_id` | `UUID NULL` FK→`settlement_run` (`RESTRICT`) | optional: payment against a specific finalized additional payment/credit (e.g. `payment_type=NACHZAHLUNG` settles the `balance` of an operating cost run). |
| **GoBD/immutability (ADR-0003/0009):** | | |
| `status` | `String(12) NOT NULL DEFAULT 'draft'` | `draft` (recording, editable) \| `booked` (booked, frozen). CHECK. Freeze condition: `booked`. |
| `booked_at` | `TIMESTAMPTZ NULL` | Time of booking. |
| `booked_by` | `UUID NULL` FK→`user` (`SET NULL`) | Who booked (audit). |
| `reverses_id` | `UUID NULL` FK self (`RESTRICT`) | for a **reversal payment**: points to the reversed original payment. |
| `reversed_by_id` | `UUID NULL` FK self (`RESTRICT`) | for the **reversed** payment: points to the reversal row (forward pointer, E-4 convention like `superseded_by_id`). |
| `retention_until` | `DATE NULL` | Retention anchor (8 years, architecture.md §3.3 / ADR-0003 #3) — booking voucher retention; GDPR (DSGVO) interlocking (§5). |
| `note` | `String(500) NULL` | Payment reference/note (e.g. original transfer text, where entered manually). |
| `created_at`/`updated_at` | `TIMESTAMPTZ` | as everywhere. |

Constraints: `CHECK (direction IN ('IN','OUT'))`; `CHECK (amount > 0)`; `CHECK (payment_type IN (...))`; `CHECK (status IN ('draft','booked'))`; `CHECK (period_month IS NULL OR period_month BETWEEN 1 AND 12)`; counterparty/type consistency CHECKs (§1.4); **composite FK** `(organization_id, bank_account_id, property_id)` → `bank_account(organization_id, id, property_id)` (§4, ADR-0010). **RLS** four policies (pattern `0010`). Indexes: `organization_id`; `bank_account_id`; `(property_id, period_year)`; `lease_agreement_id`; `(tenant_profile_id, period_year)`; `(owner_profile_id, period_year)`; `payment_type`; `settlement_run_id`; `reverses_id`/`reversed_by_id`.

### 1.3 `payment_type` — controlled vocabulary [§18/domain, not decided unilaterally as final]

The type is the **control key** for the settlement integration (§6) — hence strictly a CHECK vocabulary, not free text. SDD §6.7 lists the assignment purposes; the M6 vocabulary is derived from them:

| `payment_type` | Counterparty | SDD §6.7 | flows into (§6) |
| --- | --- | --- | --- |
| `KALTMIETE` | Tenant | "Tenant: base rent" | Tenant ledger expected/actual (rent); **not** into the operating cost statement. |
| `NK_VORAUSZAHLUNG` | Tenant | "Tenant: operating cost advance payment" | **Operating cost statement `advance_payments_total` (DERIVED)** (§6.2). |
| `HAUSGELD_VORSCHUSS` | Owner | "Owner: Hausgeld advance payment" | WEG expected/actual **informational**; **NOT** into the settlement balance (§6.3). |
| `RUECKLAGENANTEIL` | Owner | "Owner: reserve share (Rücklagenanteil)" | Reserve actual (informational); **not** into the balance (§6.3/§6.4). |
| `SONDERZAHLUNG` | Owner | "Owner: special assessment/special payment" | Special expected/actual (informational); settlement-neutral unless recorded as a cost item. |
| `NACHZAHLUNG` | Tenant or owner | "… / additional payment" | settles the `balance` of a **finalized** settlement (`settlement_run_id`); settlement-neutral for the *next* period. |
| `KAUTION` *(optional)* | Tenant | "Tenant: security deposit" | Security deposit account holdings; **settlement-neutral** (§3.4). |

> **[§18/domain F6-3] Scope of payment types.** SDD §18 explicitly lists the depth of the tenant ledger and the treatment of security deposits as open. Whether `KAUTION` is already maintained in M6 (a dedicated account type exists: `bank_account.account_type='KAUTION'`) or deferred to after M9, and whether further types are needed (e.g. rent reduction, refund of credit as its own `OUT` type), has **not** been decided unilaterally. **Provisional assumption:** the vocabulary above; `KAUTION` carried as an optional type, but settlement-neutral. Requires domain sign-off.

### 1.4 `open_item` — derived (computed), not persisted [DECISION]

> **[DECISION E6-1]: `open_item` is a derived (computed) object, NOT a persisted table.** *(Recommendation; full rationale in ADR-0009.)*

An open item is the **computed balance view** per (person, period, type):

```
open_item(person, period, type) = expected(person, period, type)  −  Σ assigned actual(person, period, type)
   open > 0  → arrears (person still owes)
   open < 0  → overpayment / credit
   open = 0  → settled
```

- **Soll** (expected amount) is itself derived (§2): from `lease_agreement`/`economic_plan`, monthly basis. **Ist** (actual) is `Σ amount` of the `booked`, **non-reversed** `payment` rows with the same type/period/person.
- `open_item` is computed as a **pure function** (`app/engine/mietkonto.py`) and delivered via a **read-only API endpoint** (list per person/property). No row is materialized per month, there is no generation job, and no second truth that could drift.
- **M7 delimitation (no anticipation):** Dunning levels/history (M7) need a per-item anchor (a dunning status "sticks" to a specific overdue item). M6 supplies the *computed* overdue items as input; whether M7 materializes a small persisted dunning/status record from them (referencing person/period/type) is **[§18/domain F6-1]** and M7's concern. **M6 does not decide the dunning persistence.**

**Counterparty/type consistency (CHECK on `payment`):** tenant-related types (`KALTMIETE`, `NK_VORAUSZAHLUNG`, `KAUTION`) require `tenant_profile_id NOT NULL` and `owner_profile_id NULL`; owner-related ones (`HAUSGELD_VORSCHUSS`, `RUECKLAGENANTEIL`, `SONDERZAHLUNG`) the reverse; `NACHZAHLUNG` allows both (depending on the addressee), but requires that **exactly one** person is set. These CHECKs prevent a payment from being recorded type-inconsistently (basis of the unambiguous DERIVED assignment, §6).

---

## 2. Expected-amount generation (Soll) — monthly basis (consistent with ADR-0007)

The expected amount (Soll) is **derived** (ADR-0009) and computed per (person, period) as a pure function. **Monthly** basis — consistent with ADR-0007 E4-3b (Hausgeld as 12 equal monthly installments) and with SDD §6.7 ("expected-amount posting per period").

### 2.1 Tenant expected amount (per lease)

```
monthly expected without rate change = valid base rent + valid advance payment
                                       (lease_agreement + lease_rent_change)
```

- **Effective-date changes (Phase 2, T23):** `lease_rent_change` adds complete base rent/operating cost rates without overwriting the original contract amounts. Each rate applies from its effective date inclusive; the last previous rate also continues to apply when a query begins later. Several daily segments are summed exactly per month/component and only then rounded; constant rates keep the existing largest-remainder partial-month logic. Example March 2026: 700 EUR through March 14, 750 EUR from March 15 yields 727.42 EUR base rent, operating costs 150 EUR unchanged. History append-only, effective dates unique per lease; organization/parent RLS and prepare permissions apply, financial lock before lease lock. Existing bookings/final snapshots remain unchanged.
- **Operation (Phase 2, T24):** Every lease history in the unit shows original rates and changes with their source; "Miete ändern ab …" ("Change rent from …") records the full valid base rent/operating cost rate. The date must lie within the contract limits displayed inclusively, cent-exact strings up to `Numeric(14,2)`, no floating-point arithmetic. Write roles can add entries, READ_ONLY can only read; load/server errors are visible, inputs are kept and the dialog is locked against double actions/closing during the POST. The browser acceptance run demonstrates unchanged original data after reload and 24 tenant ledger items with an annual expected amount of 10,650 EUR for +50 EUR from April 1, 2026.
- **Separation by component:** The expected amount is maintained separately for `KALTMIETE` (= `base_rent`) and `NK_VORAUSZAHLUNG` (= `advance_payment`), because only the operating cost advance payment flows into the operating cost statement (§6.2), the base rent does not. Mixing them would distort the settlement integration.
- **Mid-year start/end (day-accurate partial months):** If the lease begins/ends in the middle of a month, the expected amount of the partial month is **pro rata**. Basis: monthly, the partial month computed day-accurately via the same `app/engine/periods.py` arithmetic as M3 (day-inclusive, ADR-0004/0006). **[§18/domain F6-4]** whether a partial month is computed day-accurately (`days_occupied_in_month / days_in_month`) or as a flat full/half month — handled differently under leases/in practice. **Provisional assumption:** day-accurate partial month (consistent with M3); clearly marked, requires domain sign-off.
- **Read boundary:** `lease_agreement.valid_to` is stored half-open; the expected-amount function reads via `db_exclusive_to_engine_inclusive` (ADR-0006) — the **same** read boundary as M3, **one** definition. This way the last rental month counts correctly (no off-by-one at lease end).
- **Due date:** an expected-amount month is due from its due date on (for "overdue", M7). **[§18/domain]** Default due date (e.g. 3rd working day of the month, § 556b BGB) — provisionally the start of the month; requires domain sign-off.

### 2.2 Owner expected amount (per owner, from the finalized economic plan)

```
monthly expected(owner, month) = economic_plan_line(OWNER_ADVANCE, owner).monthly_amount
                                 (from the FINALIZED economic_plan of the year)
```

- The source is the `OWNER_ADVANCE.monthly_amount` per owner already computed in M4 and rounded on a **monthly basis** (ADR-0007 E4-3b). M6 invents **nothing** here — it reads the finalized plan.
- **Only finalized plans** yield a binding monthly Hausgeld expected amount (a `draft` plan is not a resolved expected amount). Without a finalized plan, no Hausgeld advance is invented. Since Phase 2 (T32), explicitly created, frozen special levies (Sonderumlagen) can be added independently of this as a one-time owner expected amount.
- **Multiple units per owner:** The plan has one `OWNER_ADVANCE` row per (owner, unit); the owner's expected amount per month is the **sum** of the `monthly_amount` over their units (exact, no further rounding — cent + cent = cent, m3 §4.3).

#### Special levies since Phase 2 (T32)

`GET/POST /api/special-levies` extends the existing account with explicitly created
special levies. Required are the property, the complete WEG scope, a positive
cent-exact total amount and a due date; a resolution may optionally be referenced and
must belong to the same property and the same organization. Partial scopes are not
treated as a complete property-wide levy. All units need a positive MEA (co-ownership
share, Miteigentumsanteil) and owners valid on the due date; multiple owners need
positive shares, which are normalized within their unit. The existing remainder
distribution preserves the total cent-exactly.

The creation freezes owner, unit, history row, effective MEA and amount per share.
Header and shares are append-only; later MEA/history changes do not alter this
obligation. The owner account takes it over exactly once as `SONDERZAHLUNG` in the due
month, only if the due date falls within the selected reporting period. An economic
plan is not required for this. Existing booked `SONDERZAHLUNG` inflows are credited
via the same expected/actual path; payments, reversals and old settlement snapshots
are not changed in the process. READ, prepare, source and organization boundaries also
apply to the new source. Since T33, `/sonderumlagen` shows the frozen shares with
names and separate READ/prepare scope selections. The existing account and dunning
forms support special levies without an invented economic plan; for such a dunning
notice the WEG property is selected explicitly. The dunning calculation receives the
actual due date: a levy due June 15 is not yet overdue on June 14/15. With several due
dates in the same month, the combined monthly payment is credited exactly once, oldest
due date first; Hausgeld and other payment types remain separate. Previous undated
monthly items keep their previous rules.

The immutable dunning context records the levy IDs used as well as the scope, owner
and reporting period. For this, migration 0128 extends the existing provenance check;
it refuses a downgrade as soon as levy provenance that is already in use would
otherwise be lost. A downgrade-and-upgrade run without such contexts and the refusal
when provenance is already recorded are tested. Issued letters, snapshots and shares
are not changed retrospectively.

Since Phase 2 (T35), M7 can calculate default interest (Verzugszinsen) for these open
items from an explicit base interest rate input with an optional configured default:
`open × (base + 5) / 100 × calendar days since due date / 365`. Per item, rounding to
the cent (`ROUND_HALF_UP`) happens only once; afterwards the stored interest lines are
summed. Fees and interest are not themselves charged interest. Example: 700 EUR at a
base rate of 2.50% over 60 days → 8.63 EUR interest. The interest snapshot records due
date, reference date, days, base/annual interest rate and amounts; an issued dunning
notice, including its PDF, uses exclusively these stored values. The expected/actual
balance of the account is not supplemented by further account postings as a result.
Without a configured default, the new interface requires an explicit input; old API
calls without an interest base and existing letters keep their interest-free
semantics. No official interest rate is invented and no delivery is claimed. Migration
0129 protects the new amounts and provenance even against a direct SQL update and
refuses a downgrade that would lose existing interest bases.

Since T53, individual and selected tenant dunning notices use the same
history-sensitive tenant ledger path. The non-writing dunning-run preview filters
principal claims strictly greater than the explicitly chosen threshold. Confirmation
creates the chosen drafts atomically; changes to sources/escalation make a new preview
necessary. Repeating a request whose outcome was unclear returns the same original
drafts after renewed prepare/source admission. No M6 payments are added, old issued
letters/PDFs are not changed and nothing is sent.

### 2.3 Expected-amount engine as a pure function

`app/engine/mietkonto.py`:
- `tenant_expected_charges(lease, period) -> list[ExpectedCharge]` — monthly expected-amount items (KALTMIETE/NK_VORAUSZAHLUNG separately), day-accurate partial months.
- `owner_expected_charges(owner_advance_lines, period) -> list[ExpectedCharge]` — monthly Hausgeld expected-amount items per owner.
- Both take **frozen inputs** (dataclasses) and return a deterministic expected-amount list; no DB. The rounding of the monthly installments already happens in M4 (`monthly_amount`); for the tenant expected amount, `base_rent+advance_payment` is already cent-exact, × a day-accurate partial month if applicable via `distribute_amount` (ADR-0005, if a partial month yields several partial expected amounts).

---

## 3. Actual (Ist) / assignment — assigning payments to expected-amount items

### 3.1 Balance & open items per person (pure arithmetic, Decimal)

`app/engine/mietkonto.py`:
```
account_statement(expected_charges, payments) -> MietkontoResult
```
- Groups the **expected amount** (from §2) and the **actual** (`booked`, non-reversed `payment`) per (period, type).
- Returns, per expected-amount period, the **open item** (expected − assigned actual) and the **running balance** over the period.
- **Pure Decimal arithmetic**, no float, deterministic — golden-tested (§7). Summation without rounding (all amounts already cent-exact).

### 3.2 Partial payment → open item

If the tenant pays less than the expected amount (e.g. expected 850.00 €, payment 600.00 €), an **open item** of 250.00 € remains (open > 0). Several partial payments add up (Σ actual). No automatic "offsetting order" across periods in the MVP — the assignment follows the **explicit** `period_year`/`period_month`/`lease_agreement_id` of the payment (assigned manually), not a guessed order of application.

> **[§18/domain F6-6] Order of application / offsetting.** With several open periods and a payment without an exact month reference, the offsetting order (oldest debt first? § 366 BGB?) is a domain question. **Provisional assumption:** Payments carry their period reference **explicitly** (assigned manually when recorded); no automatic order of application in the MVP. Whether a § 366 BGB offsetting is needed requires domain sign-off.

### 3.3 Overpayment → credit balance

If the person pays more than the expected amount (e.g. expected 850.00 €, payment 1000.00 €), the open item is **negative** (−150.00 €) = **credit balance**. The credit balance appears as a negative open item/balance; a **refund** to the person is its own `payment` row with `direction='OUT'` (no negative `amount`).

### 3.4 Security deposit / deposit account [§18/domain]

`bank_account.account_type='KAUTION'` exists. Security deposit payments (`payment_type='KAUTION'`) are **settlement-neutral** and stay outside the rent balance. **Phase 2, T25:** `lease.deposit` supplies the separate agreed expected amount; the pure `deposit_statement` sums booked active inflows and genuine outflows separately, holdings = inflow − repayment. Drafts, reversed originals and reversal rows do not count. `/kautionen` or `GET /api/payments/deposits` show the complete authorized lease view with labels, also before the first payment and for past leases; within an explicitly chosen MIET scope without year/account-type filter. Without a scope only the complete legacy context is permitted; missing permissions/sources, unclassified inputs or incoherent legacy deposits are rejected instead of yielding a partial/zero report. New/changed/to-be-booked deposits check the account's property, lease and tenant; the existing reversal path for legacy data is preserved. Example: expected amount 1,800, two IN installments of 900, genuine OUT repayment of 300 → paid 1,800, returned 300, holdings 1,500 EUR; base rent/operating cost tenant ledger unchanged. Recording/booking/repayment happen via the existing payment interface, READ_ONLY only reads. No new migration or change to finalized snapshots. Interest, form of investment and legal repayment conditions remain **[§18/domain]** — no automatic interest/repayment rule, no invented holdings cap; negative holdings with incomplete history remain transparent.

---

## 4. Segregation of funds — DB hardening (constraint) now [DECISION]

> **[DECISION E6-4]: DB constraint hardening of the segregation-of-funds coherence now (composite FK), NO second RLS axis.** *(Recommendation; full rationale + rejected options in ADR-0010.)*

M0 explicitly **deferred this decision to M6** (architecture.md A-4, ADR-0001 §Abgrenzung (delimitation): "DB hardening of the segregation of funds at the earliest with M6"). M6 decides it:

- **The risk is concrete and trust-relevant:** Money of WEG A must never end up on the account/in the sphere of WEG B — **even within the same organization**. RLS does **not** catch this (both WEGs are the same org). Pure app-layer discipline is too weak here (the same weakness that ADR-0001 explicitly did not accept for organization isolation).
- **Mechanism (strict, without a second RLS axis):** Every `payment` runs against exactly one `bank_account` (`bank_account_id NOT NULL`). Property/mandate of the payment **follow from the account** and are bound to the account by **composite FK** `payment(organization_id, bank_account_id, property_id) → bank_account(organization_id, id, property_id)`. "Payment on account X claims property Y although account X belongs to property Z" is thereby **impossible at DB level** — not merely a convention. Prerequisite: UNIQUE index `(organization_id, id, property_id)` on `bank_account` (§1.1, §8).
- **Why not the full second RLS axis (option C):** It would be the strongest isolation, but is overshooting for an MVP and would stand in the way of the **cross-mandate management views** (deadline monitor across all WEGs, total open items) that ADR-0001 names as an intended advantage of the shared DB. The composite-FK coherence secures the concretely endangered scenario (cross-posting of money) **strictly** and leaves the later upgrade to C open.
- **Account type ↔ payment type consistency (plausibility):** Security deposit to the deposit account, reserve share to the reserve account — in the MVP as a **warning/plausibility check**, not as a hard CHECK (real-world data is messy). **[§18/domain F6-2]**, requires domain sign-off. The property↔account coherence remains **strict**.

---

## 5. GoBD — payments immutable (reversal instead of edit) [DECISION]

> **[DECISION E6-2]: A booked payment is an immutable booking voucher; correction = reversal + rebooking.** *(Recommendation; full rationale in ADR-0009.)*

Payments are **booking vouchers** (GoBD, SDD §6.6/§17, 8-year retention). M6 applies the **existing ADR-0003 pattern** — **nothing is reinvented**:

- **`draft` → `booked` two-step** (analogous to `cost_entry.draft→confirmed`): In status `draft` the payment is freely editable/deletable (pure recording correction, not a GoBD event). With the deliberate **booking** (`booked`) it is frozen. **[DECISION E6-3]** whether the two-step is used (recommended) or a payment is `booked` immediately — recommendation: two-step, because it mirrors the established pattern and does not force typo corrections into reversals.
- **Status-aware `BEFORE UPDATE/DELETE` trigger** (freeze condition `status='booked'`, pattern `0011`/`trg_settlement_run_immutability`): Content-bearing columns (`amount`, `direction`, `value_date`, `bank_account_id`, `property_id`, `management_mandate_id`, `tenant_profile_id`, `owner_profile_id`, `payment_type`, `lease_agreement_id`, `period_year`, `period_month`, `settlement_run_id`, `status`) are locked after `booked` via `IS DISTINCT FROM`; the **only permitted** mutation of a `booked` row: setting `reversed_by_id` (reversal chain) + `updated_at`. DELETE of a `booked` row → always an exception. `draft` rows are free (the trigger lets them through). Full CRUD grant + trigger as the sole status-dependent lock (E3-5 line like `cost_entry`/`settlement_*`, because an editable `draft` state exists).
- **Correction = reversal + rebooking:** Reversal = new `payment` row with opposite `direction`, `reverses_id` → original; the original is pointed to via `reversed_by_id` and stays unchanged (GoBD, SDD §14). The correct booking is another new row. In the service as a guided `reverse_payment(...)` operation (analogous to `create_correction_run`, settlement_service).
- **Interlocking with finalized settlements (the actual reason):** A finalized settlement freezes its `advance_payments_total`/actual as a **copy** in the snapshot (M3 §1.2, M4 §1.5). A later reversal does **not** change the **finalized** settlement (snapshot stays immutable); it only affects a new (reversal/correction) settlement. This keeps the chain consistent: immutable payment → immutable settlement snapshot → correction always as a new version.
- **Audit trail:** `payment.created`, `payment.booked`, `payment.reversed` via the existing `AuditRepository` (append-only `audit_event`, M0).
- **Retention/GDPR:** `payment.retention_until` is the retention anchor (8 years, architecture.md §3.3); a payment as a booking voucher falls under statutory retention — a GDPR erasure request leads (as with vouchers) to **blocking/restriction of processing**, not hard delete, as long as `retention_until` is running (M9 mechanics, only the anchor here).

---

## 6. Settlement integration (core) — which payment → which settlement quantity

This is the most correctness-critical place in M6 and the reason why it is designed as a correctness-critical design step. The bridge `advance_payments_source: MANUAL → DERIVED` is prepared in M3/M4; M6 defines **precisely** which payments flow into which settlement quantity — **without double counting, clearly delimited, manual as a fallback**.

### 6.1 Basic principle: a pure derivation function, no matching

```
derive_advance_total(payments, filter) -> Decimal
   = Σ amount  over all payments with:
       status = 'booked'  AND  reversed_by_id IS NULL  (not reversed)
       AND payment_type ∈ filter.types
       AND period_year  = filter.year
       AND (filter.lease_agreement_id IS NULL OR lease_agreement_id = filter.lease_agreement_id)
       AND (filter.owner_profile_id  IS NULL OR owner_profile_id  = filter.owner_profile_id)
```

- **Pure function** (`app/engine/mietkonto.py`), `Decimal` in/out, no DB — testable, deterministic. The service loads the matching `payment` rows (repository query with exactly this filter) and passes them to the function; the function sums. **No bank matching, no parsing** (SDD §3).
- **No double counting — guaranteed by three properties:** (1) every payment has **exactly one** `payment_type` and **exactly one** period reference (`period_year`) and addressee; (2) **reversed** payments (`reversed_by_id IS NOT NULL`) and reversal rows are treated consistently (either both out, or both in — net zero; see §6.6); (3) the settlement filters are **disjoint** per quantity (operating cost advance payment ≠ Hausgeld advance ≠ base rent). A payment can thus flow into **at most one** settlement quantity.

### 6.2 Operating cost statement (M3, `advance_payments_total`) ← `NK_VORAUSZAHLUNG`

**Precise assignment:**

| Settlement quantity | M3 field | payments fed (type + period + addressee) |
| --- | --- | --- |
| Tenant's advance payment total | `settlement_run.advance_payments_total` (`source='DERIVED'`) | `payment` with `payment_type='NK_VORAUSZAHLUNG'` **AND** `period_year` = settlement year of the run **AND** `lease_agreement_id` = `settlement_run.lease_agreement_id` **AND** `status='booked'` **AND** not reversed. |

- **Only the operating cost advance payment** flows into the operating cost statement — **not** the base rent (`KALTMIETE` is tenant ledger expected/actual, not operating costs). This is the sharpest delimitation: a tenant often pays base rent + operating costs in one amount, but in the system they are **two separate `payment` rows with two types** (or a split when recording). The operating cost statement pulls **exclusively** the `NK_VORAUSZAHLUNG` part.
- **Addressee-exact:** On a mid-year tenant change, each tenant has their **own** `lease_agreement_id`; the DERIVED sum per run pulls only the payments of **this** lease (consistent with M3 §3.5: one `settlement_run` per lease).
- **Period-exact:** The payment's `period_year` must match the settlement year. Advance payments for 2025 flow into the 2025 operating cost statement, not 2024 — analogous to the `service_period` logic of the costs (M3 §3.4). **[§18/domain F6-7]** Treatment of payments at the year boundary (December rent paid in January / advance payment for the following year): provisionally the **recorded `period_year`** (assigned manually) is authoritative, not the `value_date`. Requires domain sign-off.

### 6.3 WEG settlement (M4, payment status) ← `HAUSGELD_VORSCHUSS` (informational, NOT into the balance)

**Precise assignment — and the most important delimitation of M6:**

| Settlement quantity | M4 carrier | payments fed | flows into the settlement balance? |
| --- | --- | --- | --- |
| **Expected advance (Soll-Vorschuss)** of the owner | `OwnerAdvanceInput.plan_advance_total` (from `economic_plan`, §2.2) | **no payments** — comes from the **finalized plan** | **YES** — the subtrahend of the balance (ADR-0007). |
| **Actual payment** (payment status) of the owner | `WegComputeExtras.paid_advances` (MANUAL today) → **DERIVED** | `payment` with `payment_type='HAUSGELD_VORSCHUSS'` **AND** `period_year` = settlement year **AND** `owner_profile_id` = owner **AND** `status='booked'` **AND** not reversed | **NO** — **informational only** (payment status/arrears). |

- **The settlement balance remains `actual costs − expected advance(plan)` (ADR-0007 E4-3a) — payments do NOT flow into the balance.** This is the WEMoG logic: the subject of the resolution (Beschluss) is the adjustment against the **expected amount**, not against what was actually paid. A defaulting owner has the same balance as a paying one (same expected amount, same actual costs); their **arrears** are a separate, informational quantity (a dunning matter, M7). M6 changes **nothing** about this — it only replaces the **MANUALLY** recorded `paid_advances` (today's informational payment status) with the **DERIVED** sum from payments.
- **Consequence:** M6 does **not touch** the correctness-critical balance at all. It fills only the **informational** payment status disclosure (`settlement_line(line_type='ADVANCE_PAYMENT')` optional, M4 §2.2/§2.4). The separation established in M4 (balance relevant to the resolution ↔ payment status informational) is thereby **strengthened**, not diluted.

### 6.4 Reserve share ← `RUECKLAGENANTEIL` (informational, not into the balance)

`RUECKLAGENANTEIL` payments feed the **actual state of the reserve contribution** per owner (informational). They do **not** flow into the settlement balance (the contribution is asset formation, not actual costs — M4 §3). The **expected** contribution share comes from the plan/the `ERHALTUNGSRUECKLAGE` cost items (M4 §3.3); the DERIVED payment sum is the **actual** comparison. Optional contribution to the asset report (§6.5).

### 6.5 Asset report (M4, §28 IV) ← optionally DERIVED account balance

M4 currently uses manually recorded reference-date balances (ADR-0008). From M6 on, the asset report **can** derive the account balance per `bank_account` **from `payment`** (`Σ IN − Σ OUT` up to the reference date, + optionally `opening_balance`, §1.1) instead of manually. **[DECISION E6-7]** whether M6 offers the DERIVED account balance for the asset report (recommended: yes, as an option alongside the manual reference-date balance) or leaves M4's manual path unchanged. Recommendation: **offer** DERIVED, MANUAL as a fallback — exactly the `source` pattern. Receivables/liabilities (M4 §6.4) remain manual (M6 models no vendor payment statuses beyond what is necessary).

### 6.6 Reversal handling in the derivation (no double/zero counting)

The DERIVED sum must be correct **net** when reversals exist:
- **Variant A (recommended):** The derivation counts only payments with `reversed_by_id IS NULL` (non-reversed originals) **and** excludes reversal rows (`reverses_id IS NOT NULL`). A reversed payment + its reversal thus both drop out → net zero. Clear and simple.
- **Variant B:** count all `booked`, reversals with the opposite sign — also net zero, but more error-prone (sign logic). **Recommendation: variant A.** Golden-tested (§7): "reversed payment does not flow into the DERIVED sum".

### 6.7 Manual remains as a fallback (the `source` discriminator pattern)

- `advance_payments_source='MANUAL'` (M3/M4 default) remains fully functional: the case handler sets the advance payment total by hand (e.g. when payments are not recorded in the system). **M6 does not enforce DERIVED.**
- `advance_payments_source='DERIVED'` is chosen **optionally** when creating/computing a run: the service then pulls the sum via the §6.1 function and sets `advance_payments_total` from the payments. On **finalizing**, the value determined this way (like every value) is frozen as a **copy** in the snapshot — from then on it is immutable, even if payments are reversed later (§5, ADR-0009 #3).
- **Transition without schema break:** The columns already exist and are listed in the immutability trigger (`0011`). M6 changes **no** settlement schema; it only fills the `DERIVED` path in the service.

> **[DECISION E6-8] Time of derivation.** Pull the DERIVED value freshly **on every `compute`** (always current, until `finalize` freezes it) — recommended — vs. fixing it once on creation. Recommendation: fresh on every `compute` (the `draft` mirrors the current payment status); `finalize` freezes the then-current value. Please confirm.

---

### 6.8 Bank transactions and file import (Phase 2, T15–T19)

Imported `bank_transaction` rows are initially **NEU** (new), not booked
`payment` rows, and therefore not yet actual (Ist) in the tenant or owner account. They carry
signed EUR decimal amounts; RLS and composite FKs secure organization and account.
A later payment link must belong to the same account and is immutable
once booked. Existing payments and finalized settlements are not changed
by the import.

`POST /api/bank-accounts/{id}/imports` accepts a file (`file`, CSV/XML/ZIP).
`dry_run=true` is the safe default: count, total sum, new sum,
booking period, duplicates and at most 100 preview rows, **no persistence**.
With `dry_run=false` the takeover happens atomically under an account lock and additional
unique-conflict protection; the same statement again produces zero rows, even with
concurrent imports. Foreign accounts return 404; READ_ONLY may not import.
Known source IBANs must match a stored target IBAN.
Parser/format errors return 422, oversized uploads 413, in each case without partial takeover.
Formats, limits and boundaries for missing bank-side transaction IDs: README.
The UI `/bankkonten/{id}/umsaetze` offers status filter, pagination and,
for write roles, file → preview → apply. Changing the file discards the
old release; feedback also reports zero new transactions. The real local
T19 browser run checks CSV, reload, repeated import and the same bookings
as CAMT XML/ZIP without duplicates. Account-related list via
`GET /api/bank-accounts/{id}/transactions`, paginated (50, at most 100 rows).
The import itself does not book; the separate API assignment is described in §6.10,
its user interface follows in T22.

### 6.9 Assignment suggestions (Phase 2, T20)

`suggest_matches` works purely on one bank transaction and already aggregated
`OpenItem` values from `account_statement` (expected amount from lease/economic plan,
minus booked actual). It loads no data and writes no payments.
The assignment service restricts the targets to organization, account and property.
A known deviating IBAN prevents a match; names are compared only on the basis of
normalized whole words, not as a fuzzy substring.
An explicit month in the payment reference is authoritative. The value date month is only a
fallback requiring review; several contradictory months are not guessed.

A gross rent (Warmmiete) of 850 EUR is split exactly with 700 EUR open base rent and 150 EUR open
operating costs. Components already paid are not suggested again.
Hausgeld uses the existing owner expected amount. A partial payment on a single unambiguous
component is cent-exact; with an unclear gross rent split, the split amounts remain
`None`, `unallocated_amount` contains the full receipt and `requires_review` is
true. **No invented order of application and no automatically bookable
split** for this case. Several matching targets, a deviating name or
an unknown month lower the confidence or require review. Security deposit, reversal,
chargeback, mixed rent/Hausgeld reference, unknown identity and overpayment
are not reinterpreted as a safe regular expected-amount assignment; they remain
manually editable. The API validates again (§6.10); the user interface follows in T22.

### 6.10 Atomic transaction assignment (Phase 2, T21)

`GET /api/bank-transactions` returns paginated transactions with real suggestions
(default status `NEU`, explicitly `ALL` for all states, account filter,
limit 50/maximum 100). Even without suggestions,
the account RLS and current scope grants remain authoritative. Each list entry
uses its own calculation context; Hausgeld only from the matching
finalized, non-superseded economic plan. The owner actual filter
additionally restricts to the bank property, even if the person owns several properties.

`POST /{id}/assign` validates all rows before the first payment. Positive,
cent-exact decimal amounts must together cover `abs(bank_transaction.amount)`;
target, payment type and period are checked. Account, property, direction and
value date cannot be set by the client. The existing payment service creates
drafts with the bank link **before** booking and sets audit/retention data.
The bank transaction stays locked until the end of the transaction. Only after all
bookings does it become `ZUGEORDNET`; any error rolls back payments, audit and status
completely. `POST /{id}/ignore` sets `IGNORIERT` without a payment.
Only `NEU` is editable; concurrent or repeated processing returns 409.
Foreign/invisible IDs return 404, READ_ONLY 403, missing login 401,
invalid content 422. With account scope, the existing `prepare` and
`confirm` permissions apply, not the economic plan `approve` permission. No change
to booked histories, finalized plans or existing scope rules.

### 6.11 Transaction assignment in the user interface (Phase 2, T22)

Write roles can explicitly accept complete unambiguous suggestions,
split manually in the dialog by lease/owner, payment type and month,
or ignore a receipt without a payment. Suggestions requiring review or incomplete
suggestions are not booked automatically. The positive split amounts must
cover the absolute bank amount cent-exactly; German decimal input is validated without
floating-point arithmetic. Errors keep the inputs, 409 becomes visible, reload
follows only on success. READ_ONLY gets no mutation actions. "Alle" ("All") also keeps
processed rows; account filter and pagination remain server-side.
The browser scenarios in `features/bank_assignment.feature` set up master data and
import receipts through the API, then accept suggestions, split and ignore receipts
through the UI. They assert January's 850=700+150 is fully paid and its receipt appears
under "Zugeordnet"; February's 600=500+100 leaves 250 EUR open. After processing all
receipts, the reloaded filters separate assigned and ignored receipts, retain all
three under "Alle", and show none under "Neu"; ignoring the 25 EUR receipt creates
no rent payment.

### 6.12 Property annual overview (Phase 2, T38)

`GET /api/reports/property-year` sums active booked payments by `value_date` and type
(`IN` positive, `OUT` negative); reversed originals, reversal rows, cancelled drafts
and raw bank transactions remain excluded. Confirmed, non-superseded costs count with
their gross amount by `invoice_date`, grouped by category and the unchanged
allocability flag. Allocability that is partly still to be reviewed is not
automatically treated as fully allocable. Balance = income total − cost total; all
amounts remain Decimal-exact, even with large sums and a low outer precision context.

The date basis used deliberately differs from the expected-amount period/service
period. Security deposits remain reported separately and are not reinterpreted for tax
purposes. The overview does not replace a complete Anlage V (rental income tax form)
or EÜR (cash-basis profit statement). Complete source read permissions and, where
applicable, an explicit whole-property/whole-year scope are required; partial access
or unresolved classification does not yield seemingly complete totals. Existing
bookings and snapshots remain unchanged.

The `/berichte` interface (T39) queries property/year/scope explicitly; changing the
selection removes old results. The CSV download at `/api/reports/property-year.csv`
computes the same basis afresh and additionally requires scope/source export
permissions, checked again before output. UTF-8 BOM, semicolon, CRLF and decimal comma
are fixed for spreadsheet programs. Texts prone to formula injection receive an
apostrophe prefix, genuine negative amounts remain unchanged numerically. CSV and
display do not change bookings, costs or histories; they do not replace a tax review.

#### Anlage V mapping aid (#34)

`GET /api/reports/property-year/anlage-v` applies the immutable default table in
`engine/anlage_v.py` to that same authorized report. Its query is `property_id`,
`year`, and the existing optional `accounting_scope_id`; it reuses the report
dependency, complete-source checks, date basis and scope limitations. The response
contains property/year/scope context, `lines` (key, German label, section, amount,
judgement note), `unmapped` (source code, original label, section, amount, note),
and `reconciliation` for income and expenses (mapped, unassigned, original total,
difference). Money is always a decimal string with two places. The `/berichte`
panel shows the server amounts as `1.234,56 €`, including zero lines, exclusions
and unknown categories. A changed selection removes the old panel and download;
loading, retry and export failures are shown inline without discarding the overview.

The separate `GET /api/reports/property-year/anlage-v.csv` keeps the existing year
CSV contract unchanged. It requires the same additional scope and source export
permissions, rechecked after rendering. Its seven German columns are `Objekt`,
`Jahr`, `Bereich`, `Zuordnung`, `Bezeichnung`, `Hinweis`, `Betrag (EUR)`. It includes
mapped and unassigned rows, four reconciliation rows per section and the caveat.
It uses the existing UTF-8 BOM, semicolon, CRLF, decimal comma and spreadsheet
formula protection. Neither endpoint writes financial data.

This is a **mapping aid, not tax advice**: the software says
"Zuordnungshilfe, keine Steuerberatung" and "Zeilennummern je Formularjahr prüfen".
The [official ELSTER 2025 instructions](https://www.elster.de/eportal/helpGlobal?themaGlobal=help_est_ufa_10_2025)
group operating expenses under "Umgelegte Kosten" and "Nicht umgelegte Kosten",
with a separate "Sonstige Kosten" group. The detailed water, lighting, caretaker,
administration and similar rows below are mapping-aid detail labels, **not separate
official numbered fields**. In particular, administration and bank fees must be
reviewed within the official non-recovered operating-cost group rather than
automatically copied into "Sonstige Kosten". The source allocability flag is not
evidence of what was actually recovered from tenants. No automatic split between
those official groups is made. `docs/fachreferenz.md` is absent from this checkout;
no form-year line numbers are asserted.

Rent is provisionally placed under residential rent; the report does not distinguish
commercial/garage use, relatives, earlier-year receipts or advance rent. Those
require review of the underlying records. Operating-cost advances remain income;
recoverable expenses remain gross expenses. They are not netted or treated as
non-taxable pass-through amounts. VAT is not extracted from gross costs or rent.
AfA, interest, financing costs, separate VAT, other rental uses and earlier-year
rent have no unambiguous source in this report: their zero rows mean **no mapped
report amount**, not evidence of zero tax amounts. Invoice-date expenses do not
establish tax-year cash outflow; maintenance may require capitalization or spreading.
An aggregate WEG property overview is not an individual owner's return: ownership
shares, private use and deductibility are not inferred. Existing SEV/scope guards
remain in force; management types do not change.

Booked deposits, Hausgeld advances, reserve receipts, owner special payments,
reserve contributions recorded as costs and special levies are never assigned as
rent or deductible expenses merely because they appear in the overview. They stay
visible under "Nicht zugeordnet" with an exclusion/review note. Metering services,
legal advice and unknown/historical payment types also stay unassigned when their
category does not establish a sensible line. A later deposit appropriation or
actual spending from reserves requires separate evidence, not reinterpretation of
the deposit/contribution itself.

The overview's existing exclusions remain unchanged: unbooked/draft payments,
reversed originals and reversal rows, cancelled payments, raw bank transactions,
unconfirmed and superseded costs, and records outside the selected property,
calendar year and complete authorized scope do not enter the mapping. Every amount
that does enter is counted once: **mapped + unassigned = original report total**
for each section, including explicit exclusions in unassigned. Refunds/credits keep
their signs. No redistribution or sub-cent rounding is needed; the source is already
cent-exact. An inconsistent input total is refused. Tables are validated at import
(duplicate categories cannot silently overwrite each other); organization-specific
overrides and ELSTER submission remain follow-ups, with no migration in this change.

##### Anlage V line catalog

<!-- anlage-v-lines:start -->
| Key | German detail label | Section |
| --- | --- | --- |
| `rent` | Mieteinnahmen für Wohnungen (ohne Umlagen) | Einnahmen |
| `levies` | Einnahmen aus umgelegten Neben-/Betriebskosten (Umlagen) | Einnahmen |
| `other_rent` | Einnahmen aus Vermietung von Garagen, Werbeflächen und anderen Räumen | Einnahmen |
| `prior_rent` | Vereinnahmte Mieten für frühere Jahre / Vorauszahlungen | Einnahmen |
| `vat` | Vereinnahmte Umsatzsteuer | Einnahmen |
| `depreciation` | Absetzung für Abnutzung (AfA) | Werbungskosten |
| `interest` | Schuldzinsen | Werbungskosten |
| `financing` | Geldbeschaffungskosten | Werbungskosten |
| `maintenance` | Erhaltungsaufwendungen | Werbungskosten |
| `property_tax` | Grundsteuer | Werbungskosten |
| `waste` | Straßenreinigung / Müllabfuhr | Werbungskosten |
| `water` | Wasserversorgung / Entwässerung | Werbungskosten |
| `lighting` | Hausbeleuchtung | Werbungskosten |
| `heating` | Heizung / Warmwasser | Werbungskosten |
| `chimney` | Schornsteinreinigung | Werbungskosten |
| `insurance` | Hausversicherungen | Werbungskosten |
| `caretaker` | Hauswart | Werbungskosten |
| `cleaning` | Treppenreinigung | Werbungskosten |
| `lift` | Fahrstuhl | Werbungskosten |
| `administration` | Verwaltungskosten | Werbungskosten |
| `other` | Sonstige Kosten | Werbungskosten |
<!-- anlage-v-lines:end -->

##### Default Anlage V category mapping

The judgement notes below quote the German software text. A test renders both
tables from the code constants and compares this documentation exactly.

<!-- anlage-v-mapping:start -->
| Category / income code | German detail label | Judgement note |
| --- | --- | --- |
| `KALTMIETE` | Mieteinnahmen für Wohnungen (ohne Umlagen) | [§/fachlich] Wohnraummiete als Vorschlag; andere Nutzungen, Angehörige und Vorjahresmieten gesondert prüfen. |
| `NK_VORAUSZAHLUNG` | Einnahmen aus umgelegten Neben-/Betriebskosten (Umlagen) | [§/fachlich] Laufende Umlagen; Abrechnungserstattungen und Nachzahlungen gesondert prüfen. |
| `KAUTION` | Nicht zugeordnet | [§/fachlich] Ausgeschlossen: verwahrte Kaution, keine Miete; eine spätere Verrechnung ist gesondert zu prüfen. |
| `HAUSGELD_VORSCHUSS` | Nicht zugeordnet | [§/fachlich] Ausgeschlossen: WEG-Eigentümervorschuss, keine Vermietungseinnahme. |
| `RUECKLAGENANTEIL` | Nicht zugeordnet | [§/fachlich] Ausgeschlossen: Rücklagenzuführung, keine Vermietungseinnahme. |
| `SONDERZAHLUNG` | Nicht zugeordnet | [§/fachlich] Nicht als Miete angesetzt: Eigentümer-Sonderzahlung; Verwendungszweck gesondert prüfen. |
| `GRUNDSTEUER` | Grundsteuer | [§/fachlich] Nur vermietungsbezogene Grundsteuer; andere öffentliche Lasten prüfen. |
| `KALTWASSER` | Wasserversorgung / Entwässerung | [§/fachlich] Vermietungsbezogene Wasserkosten; Abfluss und Umlage prüfen. |
| `ENTWAESSERUNG` | Wasserversorgung / Entwässerung | [§/fachlich] Vermietungsbezogene Abwasserkosten; Abfluss und Umlage prüfen. |
| `HEIZUNG_ZENTRAL` | Heizung / Warmwasser | [§/fachlich] Laufender Heizungsbetrieb; Investitionen und doppelte Erfassung ausschließen. |
| `WARMWASSER_ZENTRAL` | Heizung / Warmwasser | [§/fachlich] Laufende Warmwasserkosten; doppelte Erfassung mit Heizung ausschließen. |
| `MUELLBESEITIGUNG` | Straßenreinigung / Müllabfuhr | [§/fachlich] Laufende Müllabfuhr; Abfluss und Umlage prüfen. |
| `STRASSENREINIGUNG` | Straßenreinigung / Müllabfuhr | [§/fachlich] Laufende Straßenreinigung; Abfluss und Umlage prüfen. |
| `WINTERDIENST` | Straßenreinigung / Müllabfuhr | [§/fachlich] Winterdienst der Straßenreinigung zugeordnet; Leistungsumfang prüfen. |
| `GEBAEUDEREINIGUNG` | Treppenreinigung | [§/fachlich] Treppenreinigung als Vorschlag; sonstige Gebäudereinigung gesondert prüfen. |
| `GARTENPFLEGE` | Sonstige Kosten | [§/fachlich] Laufende Gartenpflege als sonstige Kosten; Neuanlage nicht automatisch abziehbar. |
| `ALLGEMEINSTROM` | Hausbeleuchtung | [§/fachlich] Hausbeleuchtung als Vorschlag; andere Stromnutzung gesondert prüfen. |
| `SCHORNSTEINREINIGUNG` | Schornsteinreinigung | [§/fachlich] Laufende Schornsteinreinigung; Abfluss und Umlage prüfen. |
| `GEBAEUDEVERSICHERUNG` | Hausversicherungen | [§/fachlich] Gebäudeversicherung mit Vermietungsbezug; private Anteile prüfen. |
| `HAFTPFLICHTVERSICHERUNG` | Hausversicherungen | [§/fachlich] Nur Haus- und Grundbesitzerhaftpflicht mit Vermietungsbezug. |
| `AUFZUG` | Fahrstuhl | [§/fachlich] Betrieb und Wartung des Fahrstuhls; Ersatzinvestitionen gesondert prüfen. |
| `WASCHRAUM` | Sonstige Kosten | [§/fachlich] Laufender Betrieb als sonstige Kosten; Anschaffungen gesondert prüfen. |
| `HAUSMEISTER` | Hauswart | [§/fachlich] Hauswart als Vorschlag; Verwaltungs- und Reparaturanteile gesondert prüfen. |
| `SONSTIGE_BK` | Sonstige Kosten | [§/fachlich] Sonstige laufende Kosten; Beleg, Vermietungsbezug und Umlage prüfen. |
| `KABEL_BREITBAND` | Sonstige Kosten | [§/fachlich] Vermietungsbezogene Kosten; Vertragsgrundlage und Umlage gesondert prüfen. |
| `MESSDIENST` | Nicht zugeordnet | [§/fachlich] Keine eindeutige Zuordnung: Wasser, Heizung oder Abrechnung; Doppelansatz vermeiden. |
| `CO2_KOSTEN` | Heizung / Warmwasser | [§/fachlich] Heizungsbezogene CO₂-Kosten; Aufteilung und Doppelansatz mit Brennstoffkosten prüfen. |
| `WEG_VERWALTERKOSTEN` | Verwaltungskosten | [§/fachlich] Verwaltung statt sonstige Kosten als Detailvorschlag; nur vermietungsbezogener Eigentümeranteil. |
| `BUCHHALTUNG` | Verwaltungskosten | [§/fachlich] Vermietungsbezogene Buchhaltung als Verwaltung; private Steuerberatung nicht eingeschlossen. |
| `EIGENTUEMERVERSAMMLUNG` | Verwaltungskosten | [§/fachlich] Verwaltungsbezug als Vorschlag; Vermietungsbezug und Eigentümeranteil prüfen. |
| `BANKGEBUEHREN` | Verwaltungskosten | [§/fachlich] Laufende Kontoführung als Verwaltung; keine Geldbeschaffungskosten ohne Finanzierungsnachweis. |
| `RECHTSBERATUNG` | Nicht zugeordnet | [§/fachlich] Keine eindeutige Zuordnung: Mietstreit, Erwerb oder privater Anlass; Beleg prüfen. |
| `INSTANDHALTUNG` | Erhaltungsaufwendungen | [§/fachlich] Erhaltungsaufwand als Vorschlag; Herstellungskosten, 15-%-Grenze und Verteilung prüfen. |
| `INSTANDSETZUNG` | Erhaltungsaufwendungen | [§/fachlich] Reparatur als Vorschlag; Herstellungskosten, 15-%-Grenze und Verteilung prüfen. |
| `ERHALTUNGSRUECKLAGE` | Nicht zugeordnet | [§/fachlich] Ausgeschlossen: Rücklagenzuführung ist kein abziehbarer Erhaltungsaufwand; tatsächliche Verwendung zählt. |
| `SONDERUMLAGE` | Nicht zugeordnet | [§/fachlich] Keine eindeutige Zuordnung: Mittelzuführung ohne Nachweis der tatsächlichen Verwendung. |
<!-- anlage-v-mapping:end -->

### 6.13 List export (Phase 2, T40)

The payment and cost lists export their complete filtered READ row set via
`/api/payments/export.csv` and `/api/cost-entries/export.csv` respectively. All seven
payment filters apply unchanged; status and property of the cost list are combined.
Each selected record additionally needs its source and scope export permissions.
Before the fully buffered output, a renewed check is made; a binding that has
meanwhile expired and become invisible is not a legacy fallback.

The CSV contains IDs, statuses and original codes, UTF-8 BOM, semicolon, CRLF and
exact decimal-comma values with text formula protection. The separate balance amount
is positive for IN, negative for OUT. List balance and cost gross total cover exactly
the statuses shown, including drafts or reversal/correction rows per filter; they are
neither an automatic actual (Ist) nor an account balance. Selecting an identical
filter is a no-op; load/error/context changes block stale downloads. The export
changes no financial, voucher, audit or history data.

### 6.14 DATEV current-state output (Phase 3, T49)

`GET /api/reports/datev/sources`, `POST /preview` under the same DATEV prefix and
`POST /api/reports/datev.csv` use complete property/financial-scope and source export
permissions, including contacts. The financial lock and live check remain effective
until the final export check. Time basis: value date of active booked payments and
invoice date of current confirmed costs. No additional raw bank transactions, reversal
pairs, superseded revisions or zero postings.

SKR03/SKR04 and all real accounts are configured explicitly per file; no default
accounts or global change to the category plan. Account/debit for bank IN,
account/credit for bank OUT; costs positive in debit, credit notes in credit. Bank
offset accounts must be set explicitly per payment type, cost accounts and clearing
accounts per category. Security deposit/reserve are not reinterpreted as revenue; an
invoice payment and the cost source must not appear twice as expense through wrong
account assignment. Deliberately reviewed gross accounts **without automatic tax
handling** are supported, no VAT split or BU keys (DATEV posting keys).

Primary format:
[DATEV format](https://developer.datev.de/de/file-format/details/datev-format/getting-started),
the publicly provided official format description, with header 700, posting batch
(Buchungsstapel) 13, 125 fields, DDMM, positive turnover with debit/credit indicator
and UTF-8 BOM. The preview binds source rows and configuration by fingerprint; a
deviation before download leads to 409. No sending to DATEV and no proven import. **No
delta/journal history:** The output is a complete current state; reconcile
repetition/overlap and later corrections in the target system, do not import multiple
times. Existing bookings/snapshots remain unchanged.

### 6.15 SEPA CORE monthly file (Phase 3, T50)

An explicit creditor/bank agreement and an externally signed tenant mandate are
prerequisites, not a consent derived from a contact IBAN. Persistent reference,
signature, FRST/RCUR, the last actual bank submission and the original reference are
recorded manually; references are unique per creditor. Name/IBAN changes, revocation,
a data-protection block or outdated consent each block the collection. The
conservative 36-month check counts from the last submission, for unused mandates from
the signature; when in doubt, obtain new consent. Rejected, returned and refunded
submissions must likewise be counted as actual use.

Monthly amount = the existing `tenant_expected_charges` over all overlapping leases:
base rent plus operating costs, rent history and separate cent rounding per
partial-month component; several leases of one tenant are combined. No deduction of
payments already booked and no automatic collection of arrears. Complete
property/financial-scope/contact export permissions and financial locks also apply to
read-only exports. A missing mandate is an error, not a filtered subset. The preview
and the digest-bound download book nothing and change no history.

Profile: CORE, EUR, pain.008.001.08, FRST/RCUR, SLEV; original EPC XSD in the runtime
package, additional independent ISO XSD validation in tests and browser download. Bank
acceptance, the mandate original and the actual pre-notification (14 days, unless a
shorter period is agreed) remain to be checked externally. No transmission, no
OOFF/FNAL automation, no mandate amendment message or bank/clearing-calendar
commitment. Do not submit the same file to the bank several times without checking; a
preview is not an execution receipt.

Primary sources:
[EPC CORE C2PSP Guidelines](https://www.europeanpaymentscouncil.eu/document-library/implementation-guidelines/sepa-direct-debit-core-customer-psp-implementation-1),
[EPC CORE Rulebook 2025 v1.2](https://www.europeanpaymentscouncil.eu/document-library/rulebooks/2025-sepa-direct-debit-core-rulebook-version-1-2),
[Bundesbank creditor ID](https://www.bundesbank.de/de/aufgaben/unbarer-zahlungsverkehr/serviceangebot/sepa/glaeubiger-identifikationsnummer).
The EPC notice of September 30, 2026 postpones the previously announced fixed
unstructured-address shutdown to a date not yet determined; do not treat the old
November deadline as a continuing assurance. Allocation of a creditor ID alone does
not confer a collection authorization. Confirm the bank profile explicitly.

## 7. Synthetic golden tests (objective M6 yardstick)

There are **no** real sample data. Test cases are **synthetic** from the rules + **hand-calculated** expected/actual/balance values (every cent), exactly like M3/M4 §9. Both kinds of tests are binding for M6 acceptance; they run against the **pure engine** (highest test density) and against service/DB.

### 7.1 Golden tests (hand-calculated)

1. **Expected/actual per lease, fully paid:** Lease, base rent 700.00 € + operating cost advance payment 150.00 € = 850.00 €/month, 12 months, 12 payments of 850.00 € each. Expected result: annual expected amount 10,200.00 €, actual 10,200.00 €, **balance 0**; open items empty. Tests expected-amount generation (§2) + separate components.
2. **Partial payment → open item:** one month with expected amount 850.00 €, payment 600.00 €. Expected result: open item 250.00 € (open > 0). Tests §3.2.
3. **Overpayment → credit balance:** one month with expected amount 850.00 €, payment 1000.00 €. Expected result: open item −150.00 € (credit balance). Tests §3.3.
4. **Mid-year lease start (day-accurate partial month):** Lease from March 15, operating cost advance payment 150.00 €/month; March pro rata (17/31 days). Expected result: March operating cost expected amount = `distribute`-exact share; full months from April in full. Tests §2.1 + read boundary (ADR-0006).
5. **DERIVED operating cost advance payment in an operating cost statement == MANUAL (exit core):** Tenant pays monthly 700.00 € `KALTMIETE` + 150.00 € `NK_VORAUSZAHLUNG` (12×), `period_year=2025`, one lease. (a) Operating cost statement with `advance_payments_source='MANUAL'`, `advance_payments_total=1800,00` (12×150). (b) Operating cost statement with `advance_payments_source='DERIVED'`. Expected result: **both yield exactly `advance_payments_total=1800,00`** and an **identical** `balance`; the `KALTMIETE` payments (8400.00 €) flow into **neither**. **This is the direct image of the M6 exit criterion** ("a settlement pulls recorded payments automatically") and the sharpest delimitation test (base rent ≠ operating costs).
6. **Hausgeld expected/actual + settlement balance untouched:** WEG, owner A: plan expected advance (Soll-Vorschuss) 5500.00 € (from finalized plan, §2.2), actual-cost share 6000.00 €, paid `HAUSGELD_VORSCHUSS` = 5000.00 € (arrears 500.00 € vs. expected amount). Expected result: **settlement balance = 6000 − 5500 = +500.00 € (unchanged vs. M4 golden test 2)**; the payment status (actual 5000.00 €, arrears against expected amount 500.00 €) appears **informationally**, does **NOT** flow into the balance. Tests §6.3 — the core delimitation "payments not into the balance".
7. **Reversal does not flow into DERIVED:** Tenant pays operating cost advance 150.00 €, then reversal (opposite direction), then the correct 160.00 €. Expected result: DERIVED operating cost sum = 160.00 € (original + reversal net zero). Tests §6.6 + GoBD reversal (§5).
8. **Segregation of funds — cross-posting fails (DB negative test):** Attempt to write a `payment` with the `bank_account` of WEG A but the `property_id` of WEG B (same org) → **rejected** by the composite FK. Tests §4 / ADR-0010.
9. **GoBD — `booked` payment immutable (DB negative test):** UPDATE of `amount`/assignment of a `booked` payment → trigger exception; DELETE → exception; only `reversed_by_id` can be set. Tests §5 / ADR-0003 pattern.

Each golden test checks **not only the final total**, but the **individual open items, balances and (for §6) the exact DERIVED sum** against the hand calculation (otherwise a compensating double error slips through — M3/M4 discipline).

### 7.2 Property-based invariants (Hypothesis, over random inputs)

- **I6-A "balance == Σ expected − Σ actual":** for random expected-amount/payment lists, the computed balance holds exactly (Decimal, no float drift).
- **I6-B "DERIVED sum == Σ matching, non-reversed payments":** for random payment sets, the derivation (§6.1) is exactly the sum of the `booked` non-reversed payments with the same type/period/addressee; reversals net zero.
- **I6-C "no double counting / disjointness":** no single payment flows into more than one settlement quantity (operating costs ⊥ Hausgeld ⊥ base rent ⊥ reserve).
- **I6-D "balance independent of payments":** for random actual costs + plan expected amount + arbitrary `HAUSGELD_VORSCHUSS` payments, the WEG settlement balance is **invariant** with respect to the payments (== `actual − expected(plan)`, ADR-0007). **The central M6 safety invariant.**
- **I6-E "DERIVED == MANUAL at equal value":** if `advance_payments_total` (MANUAL) is set = the DERIVED sum, the settlement result is bit-identical (same snapshot hash). Tests the bridge.
- **I6-F "segregation-of-funds coherence":** no `booked` payment has a `property_id` that deviates from the `property_id` of its `bank_account` (composite-FK invariant).

### 7.3 The exit test (DERIVED == MANUAL)

Golden test 5 + invariant I6-E together are the objective proof of the M6 exit: an operating cost statement that **derives** its advance payment from recorded `NK_VORAUSZAHLUNG` payments yields **exactly the same** (to the cent, to the snapshot hash) as the manual variant with the same sum. This provides **reproducible** proof that "a settlement pulls recorded payments automatically" — without touching the verified M3 engine.

---

## 8. Implementation checklist

> Recording the flow of money in M6 is predominantly bulk engineering. **But:** the **settlement integration (§6) is correctness-critical** and is to be built as a **pure, tested derivation function**; in case of ambiguity in the DERIVED logic, the segregation-of-funds coherence or the GoBD mechanics, **escalate for a correctness review**. Order as in M3/M4: **engine first (pure functions + tests), then persistence/trigger, then service, then API, finally PDF/display.** **Prerequisite:** the human has confirmed the [DECISION] items (§10) and ADR-0009/0010. No commit before that. **All 291 existing tests stay green** (additive).

### 8.1 Engine as pure functions (first, highest test density)
- [ ] `app/engine/mietkonto.py` (new, **deliberately separated** from `services`/`api`/`models` — SDD §16): no DB/HTTP/ORM imports; `Decimal`/`date`/dataclasses in/out.
  - [ ] `tenant_expected_charges(...)`, `owner_expected_charges(...)` — expected-amount generation, monthly basis, day-accurate partial months (§2). **Reuse:** `app/engine/periods.py` (day-accurate), `app/engine/rounding.py::distribute_amount` (partial-month/installment rounding, ADR-0005). **Do not duplicate.**
  - [ ] `account_statement(...)` — expected/actual/balance/open items (§3), pure Decimal arithmetic.
  - [ ] `derive_advance_total(payments, filter) -> Decimal` — the DERIVED derivation (§6.1), incl. reversal handling variant A (§6.6).
- [ ] Golden tests (§7.1) `tests/engine/test_golden_mietkonto.py` (hand-calculated); property-based (§7.2) `tests/engine/test_invariants_mietkonto.py` (Hypothesis), esp. I6-D (balance independent of payments) and I6-E (DERIVED==MANUAL).

### 8.2 Persistence & trigger (afterwards)
- [ ] Model `app/models/payment.py` (style of `cost_entry.py`/`settlement_run.py`: mixins, `Numeric`, `Mapped`, string+CHECK, FK `RESTRICT` to `organization`). Money `Decimal`. `reverses_id`/`reversed_by_id` (reversal chain).
- [ ] **Migration `0016_payment_table`:** `payment` with all CHECK constraints (§1.2/§1.4), FKs, **composite FK** `(organization_id, bank_account_id, property_id) → bank_account(organization_id, id, property_id)` (ADR-0010) — **requires beforehand** the UNIQUE index `(organization_id, id, property_id)` on `bank_account` (additive, in this migration). Indexes (§1.2). Optional: `bank_account.opening_balance`/`opening_balance_date` (E6-6, if confirmed). `downgrade()` reverts everything.
- [ ] **Migration `0017_payment_rls_policies`:** four policies for `payment` exactly in the pattern of `0010`/`0014` (`NULLIF(current_setting('app.current_org_id', true),'')::uuid`, fail-closed). Full CRUD grant (editable `draft` → trigger-only freeze, E3-5 line).
- [ ] **Migration `0018_payment_immutability_trigger`** (ADR-0003 pattern of `0011`): `BEFORE UPDATE/DELETE` trigger for `payment` (freeze condition `status='booked'`; content-bearing columns locked via `IS DISTINCT FROM`; only permitted mutation `reversed_by_id` + `updated_at`; DELETE of a `booked` row → exception; `draft` free). Trigger source extensively commented; `downgrade()` drops trigger + function.
- [ ] Extend `TENANT_TABLES` in `tests/test_rls_coverage.py` by `payment` (one line).
- [ ] Repository `app/repositories/payment_repo.py` (style of `cost_entry_repo.py`): active/booked query (`status='booked' AND reversed_by_id IS NULL`); the **DERIVED filter queries** (§6.1) per quantity (operating costs per lease/year; Hausgeld per owner/year; account balance per account up to the reference date).

### 8.3 Service (recording, booking, reversal — atomic)
- [ ] `app/services/payment_service.py` (style of `cost_entry_service.py`): `create_draft`, `update_draft`, `book` (`draft→booked`, atomic, audit), `reverse_payment` (reversal = new `booked` row + set `reversed_by_id` — the **only** permitted mutation of the `booked` original row, analogous to `create_correction_run`), `account_statement_for(person/lease)` (loads expected-amount inputs + payments, calls the pure engine), `open_items_for(...)`.
- [ ] **Settlement integration (pure, tested derivation):**
  - [ ] `settlement_service.py` (M3): add a `DERIVED` path — on `compute`/`finalize` of a run with `advance_payments_source='DERIVED'`, pull the operating cost sum via `payment_repo` + `derive_advance_total` (§6.2), set `advance_payments_total`. **MANUAL path unchanged.** The M3 engine itself (`compute_bk_settlement`) stays **untouched** — it only receives the same `advance_payments_total` as before, now from payments.
  - [ ] `weg_settlement_service.py` (M4): optionally derive `WegComputeExtras.paid_advances` from payments (`HAUSGELD_VORSCHUSS` per owner/year, §6.3) instead of manually. **The balance (`actual−expected(plan)`) is NOT touched** — only the informational `paid_advances` disclosure. Engine (`compute_weg_jahresabrechnung`) unchanged.
  - [ ] optionally `asset_report` (M4): offer DERIVED account balance per `bank_account` (§6.5, E6-7), MANUAL as a fallback.
- [ ] `audit_event` actions: `payment.created`, `payment.booked`, `payment.reversed`.

### 8.4 API (API-first, RBAC as in M0–M4)
- [ ] `app/api/payments.py`: `POST` (create payment, `require_write`), `PATCH /{id}` (`draft` only), `POST /{id}/book`, `POST /{id}/reverse`, `GET` (list/detail, all roles read, filter by account/person/period/type), `DELETE /{id}` (`draft` only). READ_ONLY: GET only. Foreign ID → **404** (as in M0–M4).
- [ ] `app/api/mietkonto.py` (or in `payments.py`): `GET /accounts/{lease|owner}/statement` (computed tenant ledger/expected-actual), `GET /open-items` (computed open items per person/property). Read-only, all roles.
- [ ] Register router in `app/main.py`.

### 8.5 Display/PDF (last, strictly separated)
- [ ] No **new** settlement PDFs in M6 (the operating cost/WEG PDFs from M3/M4 already show the advance payment deduction; they render the now possibly DERIVED value unchanged — **the PDF recalculates nothing**, test: PDF values == `settlement_line` values stays green). Optional: a simple **tenant ledger / open items view** (list, read-only) as preparation for M7 dunning letters.

### 8.6 Definition of Done for M6 (handover to M7)
- [ ] Engine package pure (no DB/HTTP imports); `distribute_amount`/`periods` **reused**, not duplicated; `Decimal` throughout.
- [ ] **Golden tests §7.1 green** (hand-calculated), esp. test 5 (DERIVED operating costs == MANUAL, base rent does not flow in), test 6 (balance untouched), test 7 (reversal net zero).
- [ ] **Property-based invariants §7.2 green** (I6-A to I6-F), esp. **I6-D (balance independent of payments)** and **I6-E (DERIVED==MANUAL)**.
- [ ] `payment` with RLS (four policies, fail-closed) + ADR-0003 trigger (`booked` immutable) + composite-FK segregation-of-funds coherence (ADR-0010).
- [ ] `book`/`reverse` atomic; `audit_event` for all operations; correction = reversal + rebooking (never mutation).
- [ ] DERIVED path in the M3 **and** M4 service as a **pure derivation**; **M3/M4 engine unchanged**; MANUAL remains a fallback. No double counting (disjointness of the filters demonstrated, I6-C).
- [ ] DB negative tests green: cross-posting fails (§7.1 #8), `booked` payment immutable (§7.1 #9).
- [ ] RLS coverage test + `test_tenant_isolation` extended by `payment`; **all 291 existing tests stay green** (M3/M4 settlement unchanged; in particular the snapshot hash of existing golden tests must not change).
- [ ] M6 exit objectively met: expected/actual per person is correct (golden 1–4); an operating cost statement pulls recorded operating cost advance payments automatically and yields **the same** as manual (golden 5 / I6-E).
- [ ] No **[DECISION]**/**[§18/domain]** is silently "guessed away" without marking; provisional assumptions clearly marked; domain sign-off (tax advisor/specialist lawyer) recommended.

---

## 9. Assumptions made (M6)

| # | Assumption | Impact if wrong | Clarify with |
| --- | --- | --- | --- |
| **M6-A1** | Expected amount (Soll) **derived** (not persisted), monthly basis (ADR-0009/0007). | A persisted expected amount would need a materialization job + a second truth; model/service rework. | Human (ADR-0009) |
| **M6-A2** | Payment **immutable from `booked`**; correction = reversal + rebooking (ADR-0003 pattern). | An editable payment violates GoBD + can make finalized settlements contradictory. | Human/tax advisor (ADR-0009) |
| **M6-A3** | Segregation of funds via **composite-FK coherence**, no second RLS axis (ADR-0010). | Full mandate-bound DB isolation would need a second GUC/policy axis. | Human (ADR-0010) |
| **M6-A4** | **Operating cost advance payment → operating cost statement** (DERIVED), **base rent NOT**; **Hausgeld advance → WEG informational, NOT into the balance** (balance stays `actual−expected(plan)`, ADR-0007). | A wrong integration would distort settlements / the balance. | Human/tax advisor/specialist lawyer (§6) |
| **M6-A5** | Payment carries an **explicit** period reference (`period_year`); no automatic order of application (§ 366 BGB). | Automatic offsetting would be a separate domain building block. | Tax advisor/specialist lawyer (§3.2/F6-6) |
| **M6-A6** | `bank_account` **without** balance column; account balance **derived** (optionally + opening balance anchor). | A stored balance drifts from the bookings. | Human (§1.1) |
| **M6-A7** | Dunning persistence (open-item status) is **M7**, not M6. | Anticipating M7 avoided; open items in M6 only computed. | (Roadmap M7) |
| **M6-A8** | Authoritative for period assignment is the **recorded `period_year`**, not the `value_date` (year boundary). | Different assignment at year end. | Tax advisor (§6.2/F6-7) |
| **M6-A9** | Security deposit only **holdings tracking**, no interest in the MVP. | Interest/form of investment would be an additional building block. | Tax advisor/specialist lawyer (§3.4/F6-3) |

---

## 10. Summary of open decisions (for the human)

| # | Item | Type | Recommendation | When |
| --- | --- | --- | --- | --- |
| **E6-1** | `open_item` derived vs. persisted | [DECISION] | **derived** (computed), no materialization (ADR-0009) | before M6 impl. |
| **E6-2** | Payment immutable (reversal) vs. editable | [DECISION] | **immutable from `booked`**, reversal + rebooking (ADR-0003 pattern, ADR-0009) | before M6 impl. |
| **E6-3** | `draft`→`booked` two-step vs. immediately `booked` | [DECISION] | **two-step** (mirrors `cost_entry`; typo correction ≠ reversal) | during impl. |
| **E6-4** | Segregation of funds: DB constraint now vs. app layer | [DECISION] | **DB composite-FK coherence now**, no second RLS axis (ADR-0010; resolves M0-A4) | before M6 impl. |
| **E6-5** | Expand `bank_account` vs. unchanged | [DECISION] | **unchanged** (no balance column; balance derived) + UNIQUE index for composite FK | before M6 impl. |
| **E6-6** | Opening balance anchor on `bank_account` | [DECISION] | **yes** (small, additive: `opening_balance`/`_date`) | during impl. |
| **E6-7** | Offer asset report account balance as DERIVED | [DECISION] | **yes**, DERIVED as an option, MANUAL fallback (ADR-0008 line) | during impl. |
| **E6-8** | Time of the DERIVED derivation | [DECISION] | **fresh on every `compute`**; `finalize` freezes | during impl. |
| **F6-1** | Dunning persistence (open-item status) | [§18/domain] | **not M6** — M7's concern | M7 |
| **F6-2** | Account type ↔ payment type: hard CHECK vs. warning | [§18/domain] | **warning** in the MVP; property↔account strict | before production |
| **F6-3** | Scope of payment types (security deposit? further types?) | [§18/domain] | provisional vocabulary; `KAUTION` optional/settlement-neutral | before production |
| **F6-4** | Partial month day-accurate vs. flat | [§18/domain] | provisionally **day-accurate** (consistent with M3) | before production |
| **F6-5** | Owner expected amount without a finalized plan | [§18/domain] | provisional: no expected amount without a finalized plan (analogous to M4 F4-1) | before production |
| **F6-6** | Order of application / § 366 BGB offsetting | [§18/domain] | provisional: explicit period reference, no automatic offsetting | before production |
| **F6-7** | Period assignment at the year boundary | [§18/domain] | provisional: recorded `period_year` authoritative | before production |

> **No self-certification (stance):** This design makes expected amounts (Soll), actual amounts (Ist), open items, the segregation of funds and the settlement integration **transparent, deterministic and reproducible** (golden tests + property-based invariants as the objective yardstick, esp. "balance independent of payments" and "DERIVED == MANUAL"). It **cannot** guarantee that the tenant ledger / Hausgeld account is accounting-sound or audit-proof in an individual case (M6 is not financial accounting). Before productive use, the domain correctness (ledger depth, security deposit, GoBD voucher quality of manual payments, trust/third-party funds segregation, offsetting rules) must be signed off by a **tax advisor / specialist lawyer**.

---

### Erroneous entries and financial history (T56)

An unreferenced property, unit, person or lease can be deleted after explicit
confirmation and a current source prepare. Even a single profile, rent history,
payment, voucher, source release, import receipt, audit identity or stored snapshot
reference prevents this with 409 and a reason. Drafts and RLS-hidden references also
count; no link is removed for the purpose of deletion. Issued letters and financial
originals remain byte-identical. This is not GDPR anonymization and not a
reversal/correction function.

## 11. What this document deliberately does NOT decide

- **Dunning / dunning levels / `DunningNotice` / dunning persistence:** M7 (§0, ADR-0009 §1/F6-1) — M6 only supplies the *computed* overdue items.
- **Bank import / payment reconciliation:** was a non-goal of the original M6. Phase 2 of the former MVP task list (`docs/mvp.md`, now in git history) extended this with CSV/CAMT import (T15–T19) and verified assignment (T20–T22); for the finished parser/API state see §6.8. MT940 remains unsupported.
- **Full security deposit accounting** (interest, form of investment, refund on move-out): [§18/domain] (§3.4, F6-3).
- **Order of application / § 366 BGB offsetting:** [§18/domain] (§3.2, F6-6).
- **Exact due-date rules** (§ 556b BGB, Hausgeld due date): [§18/domain] (§2.1).
- **Second RLS axis (full mandate-bound DB isolation):** deliberately not in the MVP (ADR-0010 option C) — door left open, not blocked.
- **Final legal sign-off:** tax advisor/specialist lawyer — cannot be replaced by this document.
