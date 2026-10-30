# ADR-0004: Pro rata cost distribution — day-accurate (day count) as the default

Status: **Proposed** (confirmation by the human pending — no code/commit yet)
Date: 2026-06-01 · Context: M3 · Reference: [../m3-abrechnungs-engine.md](../m3-abrechnungs-engine.md) §3 · [../sdd-property-management-mvp.md](../sdd-property-management-mvp.md) §6.9, §14 · SDD §18 (indirect)
Author: `engine-correctness` (Opus 4.8)

> This ADR decides the **basis of the pro rata period calculation** in case of a mid-year owner/tenant change and vacancy, which is required by roadmap M3 and SDD §14 but not defined there. It is a **[DECISION]** for the human — the ADR documents the recommendation and the consequences, but does not replace the domain confirmation.

## Context

SDD §14 requires: "In case of a mid-year change, it must be possible to calculate pro rata." A cost item carries a **service period** (`cost_entry.service_period_from/_to`, M2). Within this period, a unit can be rented successively to different tenants (`lease_agreement` periods, M1) or be temporarily vacant (gap between leases). The costs of a service period must be divided **proportionally to time** among these periods before the distribution key applies.

The question is the **basis of the time proportion**:

- **day-accurate (day count, "actual/actual"):** share = occupied days / total days of the service period.
- **month-accurate (30/360 or "started month = full month"):** share in monthly steps; a started month counts e.g. as a full month or per the 30-day convention.

This is a **core correctness decision**: it determines which cent is assigned to which tenant/owner/vacancy. It must be explicit, uniform and tested — not fall implicitly out of a library default.

## Options

| Option | Mechanism | Strength | Weakness |
| --- | --- | --- | --- |
| **A — day-accurate (actual/actual)** | Share = `occupied_days / days_in_service_period`, both including the end day. | Most accurate, fairest representation; independent of month lengths/leap year; a single, easily explained rule; for tenancies the method that is widespread and unassailable in case law/literature. | Fractions are "odd" (e.g. 73/365) — requires exact decimal arithmetic and controlled rounding (ADR-0005). |
| **B — month-accurate (started month counts in full)** | Every touched calendar month counts in full; share in n/12 or similar. | Simple, "round" numbers; customary in some Hausgeld (monthly owner contribution)/advance payment contexts. | On a mid-month change **systematic double or under-counting** (two parties each pay the full month → sum > 100 %, or a special correction rule is needed). Leap-year/month-length distortion. Open to challenge in the tenant operating cost statement (Betriebskostenabrechnung). |
| **C — 30/360** | Every month = 30 days, year = 360 days. | Widespread in the banking/interest world; round denominators. | Uncommon in the property management context, needs explanation; distorts 31-day months and February; no domain advantage here. |

## Decision

**Option A — day-accurate (actual/actual), including both boundary days — as the binding standard basis for M3.** *(Recommendation, [DECISION])*

Precise definition (applies to the entire engine):

1. A period `[from, to]` is **day-inclusive** at both ends. Its length in days is `(to − from).days + 1`.
2. The **overlap** of two periods (service period ∩ tenant/owner period) is `[max(from₁,from₂), min(to₁,to₂)]` if `max(from) ≤ min(to)`, otherwise empty (0 days).
3. The **time share** of an occupancy period in a cost item is
   `overlap_days / service_period_days` — exactly as `Decimal`/fraction, **not** pre-rounded.
4. The sum of the time shares of all gap-free, non-overlapping periods (incl. vacancy) over the service period is **exactly 1** (days add up to the total days). This is the central invariant that rules out cent drift through time splitting (together with ADR-0005).
5. **Vacancy days** (no active lease) are a separate "period" with the owner of the unit in the respective sub-interval as the recipient (SDD §6.9). Day-accurate here as well.
6. In case of an **owner change** within the service period, the owner sphere (homeowners' association (WEG)/Hausgeld, M4; vacancy assignment in M3) is likewise split day-accurately across the `ownership_history` periods.

**Rationale:** The day-accurate method is the only one that satisfies the invariant "sum of time shares = 1" without a special rule and thus, *by construction*, loses or double-counts no cent. It is independent of month lengths and leap years, is the undisputed method for mid-year settlement in tenancy case law, and is trivially verifiable (count days). The "odd" fractions are not a problem because money is held exactly as `Decimal` anyway and rounding takes place at the end as a deliberate, tested operation (ADR-0005). Month-accurate (B) buys round numbers with a systematic inaccuracy at the month change, which affects exactly the correctness-critical case (mid-year change) — that is the wrong trade-off here.

## Consequences

**Positive:**
- A single, project-wide uniform time rule — no "sometimes one way, sometimes another" per cost category.
- The invariant "Σ time shares = 1" is provable (property-based test, m3 doc §9) and rules out the most frequent source of error (double counting on a change).
- Day-accurate is auditable: an auditor can recalculate "occupied days / total days" by hand.
- Reusable for M4 (Hausgeld: owner change) without a break in method.

**Negative / to note:**
- Requires **exact decimal/fraction arithmetic** for the time shares and controlled remainder distribution (ADR-0005); a naive `float` day ratio would be wrong. Caught by I4 (Decimal everywhere) and ADR-0005.
- The **leap-second/time-zone question** does not arise: calculation is on **calendar days** (`DATE`, no `TIMESTAMP`), consistent with `valid_from/_to` and `service_period_*` (all `DATE`).
- **Half-open vs. day-inclusive intervals:** The DB EXCLUDE constraints (M1) use `daterange` with the standard semantics `[ )` (upper bound exclusive). The engine calculates day-**inclusive** `[ ]`. This difference is deliberate and is converted cleanly at the read boundary (m3 doc §3.4): a lease `valid_from=2025-01-01, valid_to=2025-06-30` occupies the days 01-01 to 30-06 **including** 30-06. The engine must not "lose" the upper bound. This is an explicitly tested edge case (day-accurate changeover: move-out 30-06 / move-in 01-07 → no gap, no overlap).

**Non-negotiable (consequence of this ADR):**
1. All time shares are calculated day-accurately, day-inclusively, as `Decimal`/fraction (not `float`).
2. The sum of the time shares of a complete, non-overlapping set of periods over the service period is exactly 1.
3. Vacancy is a full-fledged period with the owner as recipient, not a "remainder" that vanishes.

> **[OPEN/§18] Hausgeld advances / economic plan (Wirtschaftsplan) (M4):** For **planned** monthly Hausgeld advances (not actual cost distribution), a month-based basis may be desired from a domain perspective (12 equal monthly installments). This is a **different** quantity from the actual cost distribution and will be decided separately in M4; this ADR binds only the **actual cost distribution** (operating cost statement M3, annual settlement M4) to day-accurate. Recorded here so that M4 does not silently mix them up.

> **No self-certification:** The day-accurate method is the transparent, common and verifiable choice; whether it is mandatory in a specific individual case under the lease (e.g. a deviating contractual agreement on the settlement basis) is to be signed off on the domain side (tax advisor/specialist lawyer).
