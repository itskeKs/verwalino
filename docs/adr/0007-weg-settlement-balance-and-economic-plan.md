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

*Amendment (#602):* the number of installments is the number of calendar months the plan period touches, at most twelve (`plan_instalment_count`), so a plan shorter than twelve months charges its whole amount; `[1]*12` above is the twelve-month case. The plan engine and the Hausgeld ledger read the count from that one function.

- **Plan = month-based; actual settlement = day-accurate (ADR-0004).** This separation is the core statement: the retrospective actual cost distribution of the annual settlement stays day-accurate (even with a mid-year owner change); the forward-looking plan calculates in 12 equal monthly installments.
- The **12-installment rounding** runs through the same central `distribute_amount` function (ADR-0005), so that `Σ monthly installments == annual advance` holds exactly (no cent drift over the year).

**Rationale.** (1) The WEMoG system makes the **adjustment of the expected amount** the subject of the resolution; only `actual − expected` represents this correctly and cleanly decouples the settlement from payment behavior (which is tracked separately). (2) Hausgeld is in practice a constant monthly installment; the month-based plan is the expected, easily communicated basis, without touching the day-accurate actual precision (ADR-0004). Both decisions adhere to the project-wide rounding discipline (ADR-0005).

## Consequences

**Positive:**
- The resolution-relevant settlement balance is **one unambiguous, recomputable quantity** (`actual − expected`), machine-checkable (property invariant I4-C: `Σ settlement balances == Σ actual − Σ expected`) and hand-calculable (golden test §9.1 scenario 2).
- **Clean separation** of adjustment (balance) ↔ payment status (dunning M6/M7) ↔ asset building (reserve).
- **Method consistency with ADR-0004:** day-accurate for actual, month-based for plan — explicitly separated, not silently mixed (exactly the trap ADR-0004 wanted to avoid).
- Reuse of the central `distribute_amount` (ADR-0005) for the 12-installment split as well → no second rounding location.
- The owner account and dunning derive installment `k` from each frozen `OWNER_ADVANCE.planned_amount` with `distribute_amount(annual_amount, [Decimal(1)] * n)[k]`, where `n = plan_instalment_count(period_start, period_end)` is the number of calendar months the plan period touches, at most twelve (#602: a plan shorter than twelve months is charged in full, not by `months / 12`; the plan engine splits with the same `n`). Index zero is the plan's first calendar month, retained across later reporting windows. At most twelve installments exist: a thirteenth calendar month touched by a mid-month plan start gets no installment. Thus 1000.00 EUR over twelve months is charged as four installments of 83.34 EUR and eight of 83.33 EUR, totaling exactly 1000.00 EUR, and 3000.00 EUR over July to September as three of 1000.00 EUR. Finalized plans, their stored `monthly_amount` and finalized snapshots are preserved.
- Dated ownership transitions are applied in one walk from the plan's beginning to all installments of each line; month `k` charges installment `k`, bounded to twelve calendar months per unit. Continuing co-owners keep their own cents; departed shares pass only to unambiguous successors, with inclusive-day splitting in transfer months and positive MEA weights for concurrent successors. Constant installments retain the existing successor behavior and ambiguous transfers remain refused. *Amendment (#662):* in the month a plan starts and in the month it ends the walk covers only the days of that month inside the plan period, so an owner outside the plan period shares neither instalment; the instalment keeps its amount.

**Negative / to note:**
- The balance presupposes a **stored economic plan** (source of the expected amount). **[§18/domain F4-1]:** What applies if **no** plan was resolved for the settlement year? Provisional assumption: `economic_plan_id` is **required** for `WEG_JAHRESABRECHNUNG`; the expected amount comes from the plan (captured at least as `draft`). It must be domain-reviewed (WEG specialist attorney) whether this suffices or whether a substitute expected amount is needed (e.g., a carried-forward prior-year plan, or expected = payments made).
- The month-based plan abstracts from a mid-year owner change (who pays in which month is a payment/transition/notary matter for M6). This is intended, but must be domain-reviewed.

**Non-negotiable (consequences of this ADR):**
1. The resolution-relevant settlement balance is `pro rata actual costs − expected advance`, **never** `− actually paid`; the payment status is a separate quantity shown for information.
2. The reserve contribution does **not** count as actual costs of the balance (it is asset building).
3. Plan advances are month-based (12 equal installments), actual cost distribution stays day-accurate (ADR-0004); the two bases are kept separate.
4. Every cent split (MEA distribution (co-ownership share, Miteigentumsanteil) **and** 12 monthly installments) runs through the central `distribute_amount` (ADR-0005); `Σ == total` exactly.

> **[§18/domain] No self-certification:** The `actual − expected` definition of the balance and the month-based plan basis are the transparent and recomputable choice customary under the prevailing view on the WEMoG. Whether a different treatment is mandatory in a specific case (deviating declaration of division/resolutions, missing plan, special cost bearing under § 16 II WEG) must be domain-reviewed by a **tax advisor/WEG specialist attorney**. This ADR fixes the *technical* definition and its recomputable implementation, not the final legal assessment.

## Display clarification (#204)

New annual statements also show the owner's plan Soll, net booked Hausgeld,
`account_balance = plan_soll − paid` and `payment_amount = abrechnungsspitze + account_balance`.
The page and PDF explain their sources and ownership periods. Only the Spitze is the
resolution amount; plan arrears remain owed under the plan. The display creates no new
claim and changes neither ledger nor dunning. Versioned snapshots freeze the additional
figures without rewriting older finalized statements or their digests.


## Amendment 2026-11-08 (#401)

The owner's decision makes the linked economic plan's planned reserve contribution
an owner Soll position in the annual statement. Decision (1) and non-negotiable
point 2 continue to apply to the cost side: `OWNER_COST_TOTAL` excludes reserves.
The reserve remains asset building, not consumed costs.

The per-owner formula is now
`abrechnungsspitze = owner_cost_total + plan_reserve_soll − plan_advance`.
An owner's `plan_reserve_soll` is the sum of that owner's canonical `reserve_plan_cells`
for the settlement's linked economic plan (`ReserveLedgerRepository.plan_rows`, migration
0149: MEA per unit and owner at plan start, Hamilton cents, stable unit and owner ties).
The shares sum exactly to that plan's reserve total, with no second distribution. A
previously finalized asset report can freeze a cash ledger before this plan exists or
with an older plan; that retained ledger remains unchanged and does not supply reserve Soll. The statement stores and displays a
separate `RESERVE_SOLL` line when the share is nonzero.

For costs of 7,000.00 and planned reserve of 6,000.00, owners with 600/400 MEA
have costs of 4,200.00/2,800.00, reserve Soll of 3,600.00/2,400.00 and plan advances
of 7,800.00/5,200.00. Both Spitzen are zero when actual costs equal planned costs.
Booked payments remain separate. Under owner decision 1(b), 2026-11-09, let `H`
be net booked `HAUSGELD_VORSCHUSS`, `R` net booked `RUECKLAGENANTEIL`, and `Soll`
the owner's summed `OWNER_ADVANCE` over all units. WEG paid is
`H + min(max(R, 0), max(Soll − H, 0))`; without plan Soll, reserve adds nothing,
even when `H` is negative. Both nets use identical owner, property, period and signed
IN/OUT/Storno rules. Hausgeld overpayments retain their full credit; negative net
reserve adds nothing. Excess separate reserve stays in the uncapped reserve ledger
and cannot offset higher actual costs. Other Hausgeld and rental/BK derivations retain
their existing selection. Payments change only account balance and payment amount,
never the Spitze.

The frozen plan has per-owner/unit `OWNER_ADVANCE` and property-wide `PLANNED_COST`
lines but no per-owner planned cost share, so owner decision 2 uses fallback (a):
canonical `reserve_plan_cells`. Independent rounding of costs, reserve Soll and
Hausgeld Soll can leave an accepted ±0.01 Spitze per owner when costs match the
plan, while the aggregate remains exact. Finalized runs are never recalculated;
historical payloads, digests and PDF bytes remain unchanged.


## Amendment 2026-11-12 (#402)

The annual settlement's plan Soll follows dated ownership. The seller no longer
carries the whole annual advance: each roster owner's `plan_advance_total` is
derived from the same month rule the Hausgeld ledger and dunning already apply
(`PaymentService.owner_plan_charge_totals` over the settlement period), so a
transfer month splits by inclusive ownership days and the settlement and the
owner account agree on who owes which month. The owners' Soll of a unit always
sums to the unit's OWNER_ADVANCE `planned_amount` to the cent — the dated
charges only divide it (central largest-remainder, remainder exactly once), so
a plan period shorter than twelve months keeps its full planned amount — and a
unit whose dated charges are all zero keeps its plan-line amounts. The plan
itself stays month-based (12 largest-remainder installments, option M) and its
OWNER_ADVANCE lines remain frozen at the plan-start owner; only the
settlement's Soll derivation changed (engine input, `ENGINE_VERSION` m4.5.0).
The reserve-coverage cap of decision 1(b) and the reserve ledger's
SPLIT_RESERVE correction (current and late-cash branches) read the same dated
Soll, so booked reserve counts only against the dated unpaid Soll everywhere.
An owner whose dated Soll is 0.00 is without Soll for the cap and the reserve
ledger alike. A finalized year's late cash is decided from the Soll frozen in
that year's snapshot (`inputs.plan_advances`) — for a run stored as m4.4.0 the
undated basis it was decided with — never from ownership edits after the fact.
An asset report freezes the Soll its reserve ledger was decided with in its own
snapshot in the same shape; a year frozen before that carries no key, and its
late cash uses the finalized plan's undated line totals, which cannot change.
A history the owner ledger refuses (`hausgeld_ownership_invalid`) refuses the
calculation, the derived paid advances and the reserve ledger with the same
German 422; the plan-line fallback of the division applies only where the
ledger accepts the history. The planned reserve Soll stays on the frozen reserve-ledger
cells at plan start (amendment #401). Liability between seller and buyer
remains out of scope.
