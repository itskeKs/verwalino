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

This mechanic is implemented **once** as a pure function (`distribute_amount(total, weights) -> list[Decimal]`, m3 doc §4/§10) and is used by **every** distribution key and by the time-share split. There is no other place where money is rounded to the cent.

**Order for combined distribution (time × key):** First multiply the **exact** (unrounded) value across all stages, **then round+balance once at the end** — never round in between (otherwise rounding errors accumulate). That means: the time share (ADR-0004) and the distribution weight are chained exactly; the largest-remainder rounding applies to the **final** set of `settlement_line` amounts per cost item.

**Rationale:** Largest remainder is the only one of the three remainder options that *guarantees* the core invariant `Σ == G` **and** distributes the remainder *fairly and deterministically*. "Determinism" is not cosmetic here: A finalized settlement is frozen as a snapshot and hashed (ADR-0003 §3); computing the same inputs twice must yield bit-identically the same snapshot, otherwise the hash integrity is worthless. Commercial display rounding matches the German expectation. Option C is ruled out because it produces exactly the error that settlement reliability forbids.

## Consequences

**Positive:**
- **Correctness by construction:** `Σ shares == total costs` is not "hopefully" but a property of the function — provable as a property-based test over random amounts/weights (m3 doc §9, "no cent is lost").
- **Determinism → stable snapshot hash** (ADR-0003 linkage).
- A single rounding point → no scattered, inconsistent roundings.
- Fair: The remainder goes to those that were arithmetically closest to rounding up.

**Negative / to note:**
- With very small amounts and many recipients, a recipient may receive 1 cent more/less than their "naively commercially rounded" value — this is **correct** and intended (it is the price for the sum being exact), but must be explained in the procedural documentation (Verfahrensdokumentation).
- The algorithm must handle **negative amounts** (credits) and `Σw = 0` (no valid weight → defined error, not a silent 0 distribution) cleanly (m3 doc §4.4).
- **Order discipline** (calculate exactly, round once at the end) is an implementation rule that code review and a targeted test must safeguard.

**Non-negotiable (consequence of this ADR):**
1. A `float` is **never** used for money or weights (I4); the distribution calculates in `Decimal`.
2. There is **one** central distribution/rounding function; every cost distribution runs through it.
3. `Σ rounded shares == total amount` holds exactly after every distribution — evidenced by a test.
4. The remainder allocation is **deterministic** (reproducible tie-break), so that snapshots are stable/hashable.

> **[OPEN/§18]:** Whether a particular type of settlement or a lease prescribes a **deviating** rounding rule (e.g. commercial per item without remainder balancing, with a difference knowingly accepted) must be examined from the domain side. The default of this ADR is the arithmetically exact variant (largest remainder); deviations would be a deliberate domain determination. **No self-certification.**
