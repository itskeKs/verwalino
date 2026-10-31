# ADR-0007: WEG settlement balance (Abrechnungsspitze, WEMoG) as actual − expected (Ist − Soll) · economic plan (Wirtschaftsplan) advances on a monthly basis

Status: **Proposed** (confirmation by the human pending — no code/commit yet)
Date: 2026-06-02 · Context: M4 · Reference: [../m4-weg-abrechnung.md](../m4-weg-abrechnung.md) §2, §5 · [0004-pro-rata-daily-allocation.md](0004-pro-rata-daily-allocation.md) (resolves the plan basis left open there) · [0005-rounding-largest-remainder.md](0005-rounding-largest-remainder.md) · [../sdd-property-management-mvp.md](../sdd-property-management-mvp.md) §6.9, §10, §17 · SDD §18 (indirectly)

> This ADR decides two correctness-critical M4 questions: (1) **how the settlement balance is defined under WEMoG (§ 28 II WEG)** (subject of the resolution), and (2) **which basis applies to the economic plan advances** — which ADR-0004 explicitly left open for M4. Both are **[DECISION]** items for the human; open domain points are marked **[§18/domain]**.

## Context

**(1) Settlement balance.** Before the 2020 WEG reform, the **entire** annual settlement was resolved. Since the **WEMoG (§ 28 para. 2 WEG, in force since 2020-12-01)**, **the only subject of the resolution is the adjustment of the advances — the "settlement balance" (Abrechnungsspitze)** (additional payment (Nachschuss) or adjustment/reduction). SDD §6.9 requires this verbatim: "The WEG annual settlement shows the difference between the **expected advances** resolved in the economic plan and the actual costs as the settlement balance. The subject of the resolution is the adjustment (additional payment/credit), not the entire settlement." The correctness-critical question is the **precise definition** of the balance and the distinction between two easily confused quantities:
- **Expected advance** (per the resolved economic plan) vs.
- **payment actually made** (what the owner really transferred).

**(2) Economic plan basis.** ADR-0004 (day-accurate actual cost distribution) explicitly states: *"For planned monthly Hausgeld (monthly owner contribution) advances (not actual cost distribution), a month-based basis (12 equal monthly installments) may be desired from a domain perspective. That is a different quantity than the actual cost distribution and will be decided separately in M4."* This ADR resolves that.

## Options

**(1) Definition of the settlement balance:**

| Option | Mechanics | Assessment |
| --- | --- | --- |
| **A — actual − expected advance** | Balance = the owner's pro rata actual costs − resolved expected advances per the economic plan. | Matches the WEMoG system (subject of the resolution = **adjustment of the expected amount**); prevailing view in the WEG literature. **Recommended.** |
| **B — actual − advances actually paid** | Balance = actual costs − payments actually made. | Mixes the **resolution-relevant** adjustment with the **payment status** (arrears/credits). A defaulting owner would have a different "balance" than a paying one with the same expected amount — that is wrong from a domain perspective for the subject of the resolution (arrears are an enforcement/dunning matter, not a settlement adjustment). Rejected as the definition of the balance. |

**(2) Economic plan basis:**

| Option | Mechanics | Assessment |
| --- | --- | --- |
| **M — month-based (12 equal installments)** | Annual advance / 12, to the cent (largest remainder over 12 installments). | Hausgeld is a prepayment that stays constant each month; the practical and domain-expected basis. **Recommended.** |
| **T — day-accurate** | Pro rata daily advances. | Unusual/impractical for a forward-looking plan; day-accurate is the **actual** basis (ADR-0004), not the plan basis. Rejected for the plan. |

## Decision

**(1) Settlement balance = pro rata actual costs − resolved expected advances (option A).** *(Recommendation, [DECISION E4-3a])*

```
settlement balance (per owner)
   = OWNER_COST_TOTAL           (proportional actual costs: apportionable + non-apportionable,
                                 WITHOUT reserve contribution — that is asset formation, m4 §3)
   − PLAN_ADVANCE               (expected advances of the owner per the resolved economic plan)
   > 0  → additional payment
   < 0  → adjustment credit/reduction
```

- The **maintenance reserve contribution** (Rücklagenzuführung) does **not** go into `OWNER_COST_TOTAL` (it is not consumed costs but savings — m4 §3); it is shown separately.
- The **payments actually made** (payment status) are shown **for information** and **separately** in M4 (manual, `advance_payments_source='MANUAL'`; M6 `DERIVED`), but do **not** flow into the resolution-relevant balance.
- The balance and its two components are stored **in structured form** in `settlement_line` (`OWNER_COST_TOTAL`, `PLAN_ADVANCE`, `ABRECHNUNGSSPITZE`) and in the hashed `snapshot_payload` — machine-checkable and immutable.

**(2) Economic plan advances on a monthly basis: 12 equal monthly installments (option M).** *(Recommendation, [DECISION E4-3b])*

```
annual_advance_owner    = (Σ planned costs + planned reserve contribution) * MEA share
monthly Hausgeld amount = distribute_amount(annual_advance, [1]*12) -> 12 cent installments
                          (largest remainder, ADR-0005 -> Σ installments == annual_advance exactly)
```

- **Plan = month-based; actual settlement = day-accurate (ADR-0004).** This separation is the core statement: the retrospective actual cost distribution of the annual settlement stays day-accurate (even with a mid-year owner change); the forward-looking plan calculates in 12 equal monthly installments.
- The **12-installment rounding** runs through the same central `distribute_amount` function (ADR-0005), so that `Σ monthly installments == annual advance` holds exactly (no cent drift over the year).

**Rationale.** (1) The WEMoG system makes the **adjustment of the expected amount** the subject of the resolution; only `actual − expected` represents this correctly and cleanly decouples the settlement from payment behavior (which is tracked separately). (2) Hausgeld is in practice a constant monthly installment; the month-based plan is the expected, easily communicated basis, without touching the day-accurate actual precision (ADR-0004). Both decisions adhere to the project-wide rounding discipline (ADR-0005).

## Consequences

**Positive:**
- The resolution-relevant settlement balance is **one unambiguous, recomputable quantity** (`actual − expected`), machine-checkable (property invariant I4-C: `Σ settlement balances == Σ actual − Σ expected`) and hand-calculable (golden test §9.1 scenario 2).
- **Clean separation** of adjustment (balance) ↔ payment status (dunning M6/M7) ↔ asset building (reserve).
- **Method consistency with ADR-0004:** day-accurate for actual, month-based for plan — explicitly separated, not silently mixed (exactly the trap ADR-0004 wanted to avoid).
- Reuse of the central `distribute_amount` (ADR-0005) for the 12-installment split as well → no second rounding location.

**Negative / to note:**
- The balance presupposes a **stored economic plan** (source of the expected amount). **[§18/domain F4-1]:** What applies if **no** plan was resolved for the settlement year? Provisional assumption: `economic_plan_id` is **required** for `WEG_JAHRESABRECHNUNG`; the expected amount comes from the plan (captured at least as `draft`). It must be domain-reviewed (WEG specialist attorney) whether this suffices or whether a substitute expected amount is needed (e.g., a carried-forward prior-year plan, or expected = payments made).
- The month-based plan abstracts from a mid-year owner change (who pays in which month is a payment/transition/notary matter for M6). This is intended, but must be domain-reviewed.

**Non-negotiable (consequences of this ADR):**
1. The resolution-relevant settlement balance is `pro rata actual costs − expected advance`, **never** `− actually paid`; the payment status is a separate quantity shown for information.
2. The reserve contribution does **not** count as actual costs of the balance (it is asset building).
3. Plan advances are month-based (12 equal installments), actual cost distribution stays day-accurate (ADR-0004); the two bases are kept separate.
4. Every cent split (MEA distribution (co-ownership share, Miteigentumsanteil) **and** 12 monthly installments) runs through the central `distribute_amount` (ADR-0005); `Σ == total` exactly.

> **[§18/domain] No self-certification:** The `actual − expected` definition of the balance and the month-based plan basis are the transparent and recomputable choice customary under the prevailing view on the WEMoG. Whether a different treatment is mandatory in a specific case (deviating declaration of division/resolutions, missing plan, special cost bearing under § 16 II WEG) must be domain-reviewed by a **tax advisor/WEG specialist attorney**. This ADR fixes the *technical* definition and its recomputable implementation, not the final legal assessment.
