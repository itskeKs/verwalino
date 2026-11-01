# ADR-0005: Rounding of the distribution — commercial rounding to the cent + largest remainder against cent drift

Status: **Proposed** (confirmation by the human pending — no code/commit yet)
Date: 2026-06-01 · Context: M3 · Reference: [../m3-abrechnungs-engine.md](../m3-abrechnungs-engine.md) §4 · [../architecture.md](../architecture.md) §5.1 (I4) · SDD §6.9, §10, §18

> This ADR decides the point explicitly left open in SDD §18, "Which rounding rules apply per settlement", for the **distribution engine** (M3/M4). It is a **[DECISION]** for the human. The ADR fixes the *technical* rounding mechanics that guarantee the core invariant "sum of the shares = total costs, no cent is lost". The *domain* question whether a different rule is prescribed for a particular type of settlement remains to be signed off on the domain side.

## Context

Money is held exactly as `NUMERIC(14,2)` / `Decimal` (I4, architecture.md §5.1). A cost item (total amount) is distributed across several recipients via a distribution key (MEA, area, …) and, where applicable, a time share (ADR-0004). The exact, unrounded individual shares are in general **not** round cent amounts (e.g. 1000,00 € ÷ 3 = 333,3333… €). Rounding each share to the cent produces a **rounding remainder** (3 × 333,33 € = 999,99 € ≠ 1000,00 €). This "cent drift" is a classic, audit-relevant settlement error: The sum of the stated tenant/owner shares must **exactly** equal the stated total costs of the cost category — otherwise the settlement is formally open to challenge and arithmetically wrong.

Two sub-questions:
1. **Rounding mode** per individual value (commercial / banker's / truncate).
2. **Remainder handling**, so that Σ shares == total amount holds **exactly**.

## Options

**Part 1 — rounding mode:**

| Option | Mechanism | Assessment |
| --- | --- | --- |
| **Commercial (HALF_UP)** | 0,5 rounds up. | The expected, common commercial rounding in Germany; the obvious choice for settlements. **Recommended.** |
| Banker's (HALF_EVEN) | 0,5 rounds to the even digit. | Statistically bias-free, but unexpected for laypeople/auditors in monetary settlements; no advantage worth the explanation effort. |
| Truncate (DOWN) | always round down. | Systematic bias, unusual in the domain. Rejected. |

**Part 2 — remainder handling (critical):**

| Option | Mechanism | Strength | Weakness |
| --- | --- | --- | --- |
| **A — Largest remainder (Hare/Hamilton)** | **Round down** each share (floor to the cent); the difference `total − Σ round-downs` is an integer number `k` of cents; distribute `k` extra cents to the `k` shares with the **largest fractional remainder** (deterministic tie-break). | Σ == total **exactly**, guaranteed. Distributes the remainder *fairly* (to those that were closest to rounding up). Mathematically clean, easily testable, common in distribution arithmetic. | Somewhat more logic than "one gets everything". |
| **B — Difference to the largest/last share** | Round everything commercially; add the remaining difference entirely to the largest (or last) share. | Very simple. | The remainder can be > 1 cent and concentrates arbitrarily on **one** recipient; with many units unfair and in need of explanation; the choice "largest vs. last" is arbitrary. |
| **C — No balancing** | Round each share independently, commercially. | Trivial. | **Σ ≠ total** possible (cent drift). **Unacceptable** — violates the core invariant. Rejected. |

## Decision

**Commercial mode for the display rounding + largest remainder (option A) for the remainder handling — as the binding, project-wide rounding mechanics of the distribution engine.** *(Recommendation, [DECISION])*

Precise algorithm (for **every** split of a total amount `G` (cent-exact) across `n` recipients with exact weights `wᵢ`):

```
1. Exact share:           eᵢ = G * wᵢ / Σw            (Decimal, unrounded, high precision)
2. Round down to cent:    fᵢ = floor(eᵢ, 2)            (always round down)
3. Remainder in cents:    k  = round((G − Σfᵢ) * 100)  (integer, 0 ≤ k < n)
4. Rank by fraction:      rᵢ = eᵢ − fᵢ  (the "lost" sub-cent remainder per addressee)
5. Award +0,01 € to the k addressees with the largest rᵢ.
   Tie-break on equal rᵢ: deterministic by (largest eᵢ, then stable
   sorting by unit/addressee ID) — reproducible, not random.
6. Result cᵢ = fᵢ (+0,01 if awarded).   IT HOLDS: Σcᵢ == G  (exact).
```

This mechanic is implemented **once** as a pure function (`distribute_amount(total, weights) -> list[Decimal]`, m3 doc §4/§10) and is used by **every** distribution key and by the time-share split. The separate rule for year-only advance payments is documented in the amendment below.

**Order for combined distribution (time × key):** First multiply the **exact** (unrounded) weights across all stages, **then round+balance once** over the complete source cover. A statement that cuts an already balanced cell selects cumulative day shares using the separate rule in the amendment below; it never rebalances the source.

**Rationale:** Largest remainder is the only one of the three remainder options that *guarantees* the core invariant `Σ == G` **and** distributes the remainder *fairly and deterministically*. "Determinism" is not cosmetic here: A finalized settlement is frozen as a snapshot and hashed (ADR-0003 §3); computing the same inputs twice must yield bit-identically the same snapshot, otherwise the hash integrity is worthless. Commercial display rounding matches the German expectation. Option C is ruled out because it produces exactly the error that settlement reliability forbids.

## Consequences

**Positive:**
- **Correctness by construction:** `Σ shares == total costs` is not "hopefully" but a property of the function — provable as a property-based test over random amounts/weights (m3 doc §9, "no cent is lost").
- **Determinism → stable snapshot hash** (ADR-0003 linkage).
- One largest-remainder pass per source, followed only by stable selection of balanced cell cents for shorter statements.
- Fair: The remainder goes to those that were arithmetically closest to rounding up.

**Negative / to note:**
- With very small amounts and many recipients, a recipient may receive 1 cent more/less than their "naively commercially rounded" value — this is **correct** and intended (it is the price for the sum being exact), but must be explained in the procedural documentation (Verfahrensdokumentation).
- The algorithm must handle **negative amounts** (credits) and `Σw = 0` (no valid weight → defined error, not a silent 0 distribution) cleanly (m3 doc §4.4).
- **Order discipline** (exact combined weights, one balancing pass, then cumulative cell selection) is an implementation rule that code review and targeted tests must safeguard.

**Non-negotiable (consequence of this ADR):**
1. A `float` is **never** used for money or weights (I4); the distribution calculates in `Decimal`.
2. There is **one** central distribution/rounding function; every cost distribution runs through it.
3. `Σ rounded shares == total amount` holds exactly after every distribution — evidenced by a test.
4. The remainder allocation is **deterministic** (reproducible tie-break), so that snapshots are stable/hashable.

> **[OPEN/§18]:** Whether a particular type of settlement or a lease prescribes a **deviating** rounding rule (e.g. commercial per item without remainder balancing, with a difference knowingly accepted) must be examined from the domain side. The default of this ADR is the arithmetically exact variant (largest remainder); deviations would be a deliberate domain determination. **No self-certification.**

## Amendment — 2026-10-31: cost-cell selection and advance months (#122, #136)

Costs first balance the complete source cover using largest remainder. A shorter
statement then selects an already rounded cell of amount `C` and `D` days using
`floor_cent(C × through / D) − floor_cent(C × before / D)`. This is a second
rounding step, not another distribution. Adjacent selections telescope: every
internal cumulative term cancels, leaving `C − 0`. Summing the complete cells
therefore still recovers the source total exactly, including negative credits
and owner vacancy shares, independently of statement order. Individual property
period totals across several cells can differ by cents from a single day share
of the source total; the complete source still reconciles.

This differs from ranking recipients by their fractional remainders because the
monthly allocation must remain fixed across independently calculated periods.
Adjacent periods therefore reconcile exactly, even when they split a month,
regardless of draft or finalization order. Both rules live in
`backend/app/engine/rounding.py`: `distribute_amount` for costs and
`annual_advance_share_for_period` for year-only advances.

Month-assigned NK advances use the same cumulative rule, with the denominator
restricted to the lease's occupied days in the assigned month. One statement
covering all those days credits the full booked payment, even if the lease begins
or ends mid-month. Day proration applies only between statements of the same
lease that split those days (#136). It does not prorate an already prorated
payment again: ten 100-euro payments for a lease starting 15 March credit
1,000 euros; two statements of a full-year lease split at 14/15 March credit
245.16 and 954.84 euros from twelve 100-euro payments.

Year-only advances divide equally over calendar months the lease actually covers
in that assigned year, including its partial first/last month. Floor each month
share to cents and give the remainder to the last covered month; split a month
between statements using its occupied lease days as above. A July-start lease's
only July–December statement therefore credits its full 600-euro annual payment.
Without a lease span (Hausgeld), all twelve calendar months apply, and December
retains the remainder: 1,000 euros becomes eleven shares of 83.33 and one of 83.37.
The shared helpers live in `backend/app/engine/rounding.py`; lifecycle, payment
type and addressee filters remain in `derive_advance_total_for_period`.

The independence from other statements holds while lease dates and booked payments stay fixed. If a
lease end is entered later, each assigned year/month bucket substitutes the
credits in already finalized snapshots for those windows' currently calculated
shares. The current statement receives its occupied-month/day share plus that
difference, floored at zero. Several open drafts may each include the same
outstanding difference; finalizing one makes the others stale until re-derived.
The last open statement receives the remainder. Late bookings for a finalized
month also enter the next statement through this difference. A statement in a later calendar window
that was finalized first also counts, so corrections and reordered finalization
cannot apply the same difference again. Already credited amounts come
from frozen snapshot buckets, never a recalculation of the finalized result.
Thus a 1,200-euro year-only advance with 300 euros finalized through March still
credits 900 euros after an end on 30 September is entered. With January–14 March
already crediting 245.16 euros of three 100-euro payments, an end on 20 March leaves
54.84 euros, exactly 300 euros together. Identical finalized periods count once;
superseded statements do not count again. A changed lease end is refused with a
structured German HTTP 409 only when it falls before an active finalized
DERIVED statement's last day and changes one of its credit buckets, including an
end on or before that statement's start. MANUAL statements do
not block date edits, and an end on the statement's last day is allowed.
Legacy derived snapshots without per-year/month credits remain usable with fixed
lease dates. After a date edit, a changed credit bucket within the legacy span
requires correction or a confirmed manual total (`advance_credit_history_missing`,
HTTP 409 on derivation); the application
does not invent a historical breakdown or mutate a finalized snapshot.

Active booked advances assigned entirely outside the lease remain credited at
zero (including an assigned year before the lease begins). Their total appears
as a non-financial notice line in draft and frozen statement lines, the API and
the PDF: “Nicht angerechnet: Zahlungen außerhalb des Mietverhältnisses: X €”.
Before-start months (and wholly before-start assigned years) appear on the statement
containing the lease start; after-end months (and wholly after-end assigned years)
appear on the statement containing the lease end. Each side appears once, including
when the statements are clipped to the lease or split within a month. A statement
containing both boundaries combines both totals. Other statements do not repeat
the notice. It does not alter statement costs, credited advances or the balance.

## Amendment — 2026-10-31: reserve cash within combined Hausgeld

A deliberate exception applies to the reserve component of each booked Hausgeld
payment: multiply the signed cash amount by the unit's frozen annual reserve
share divided by its total annual Hausgeld, then round that single component
commercially to cents (`ROUND_HALF_UP`). This is not a distribution of a fixed
reserve total across receipts: cash arrives independently and a reversal must
cancel its original component exactly. Do not redistribute fractions across
payments with `distribute_amount`. Planning still uses largest remainder for
owner/unit shares and monthly rates. See [M4](../m4-weg-abrechnung.md#maintenance-reserve-ledger-36).

For `n` payments, the property sum can differ from the sum of their exact ratio
components by up to `n × 0.005 €` (strictly less when no component is exactly a
half-cent; HALF_UP can attain the bound at ties). At a reserve/Hausgeld ratio
of `6000/13000`, twelve payments of 433.33 € produce 2,400.00 € of rounded reserve
components, versus 2,399.98 € when their exact components are summed and rounded
once. One hundred payments of 0.01 € produce 0.00 €, versus 0.46 € when summed
before rounding. These differences are retained, not silently redistributed.