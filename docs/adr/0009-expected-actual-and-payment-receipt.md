# ADR-0009: Expected position (Soll-Position) derived (not persisted) · payment as an immutable voucher (Beleg) (reversal instead of edit)

Status: **Proposed** (confirmation by the human pending — no code/commit yet)
Date: 2026-06-02 · Context: M6 · Reference: [../m6-zahlungen-mietkonto.md](../m6-zahlungen-mietkonto.md) §1, §2, §3, §5 · [0003-gobd-immutability-mechanism.md](0003-gobd-immutability-mechanism.md) (reused pattern) · [0005-rounding-largest-remainder.md](0005-rounding-largest-remainder.md) · [0007-weg-settlement-balance-and-economic-plan.md](0007-weg-settlement-balance-and-economic-plan.md) · [../architecture.md](../architecture.md) §2.6, §3, §5.1 · [../sdd-property-management-mvp.md](../sdd-property-management-mvp.md) §6.3, §6.7, §14, §16, §17, §18
Author: `engine-correctness` (Opus 4.8)

> This ADR decides two related core M6 questions: (1) whether the **expected position** (`open_item`) is **derived** (computed at runtime) or **persisted** (materialized), and (2) whether a captured **payment** carries the GoBD voucher character and is therefore **immutable** (correction = reversal + rebooking) or editable. Both are **[DECISION]** items for the human. Open domain/§18 points are marked **[§18/domain]**.

## Context

M6 models the flow of money (SDD §6.3, §6.7): manually captured payments, a simple tenant ledger (Mietkonto) (expected charge per period, captured payments, balance) and an open items list per person. **Bank import (CAMT/MT940) and automatic reconciliation are explicitly a non-goal (SDD §3).**

Two quantities face each other:

- **Expected (Soll)** — what a person owes for a period: for the tenant, base rent + operating cost advance (NK-Vorauszahlung) per month (from `lease_agreement`); for the owner, the monthly Hausgeld (monthly owner contribution) advance (from the finalized `economic_plan`, `OWNER_ADVANCE.monthly_amount`). Monthly basis (ADR-0007 E4-3b for the plan; consistently continued for the rent expected charge).
- **Actual (Ist)** — what the person actually paid: the captured `payment` rows.

The **open item** (`open_item`) is the link: expected position, the assigned actual amount and the remaining balance. SDD §13 lists `OpenItem` as an entity; what is open is whether it is a **computed** or a **stored** object. SDD §18 also explicitly lists as open: "Level of detail of the tenant ledger (only expected/actual (Soll/Ist) vs. individual expected charges per month)".

### Question 1 — Expected amount derived vs. persisted

| Option | Mechanics | Assessment |
| --- | --- | --- |
| **A — expected amount derived (computed)** | The expected positions of a period are **computed** on demand from `lease_agreement`/`economic_plan` (pure function, monthly basis); `open_item` is a computed view (expected − assigned actual per period). **Nothing** is materialized per month. | One source of truth (lease/plan); no drift between a stored expected amount and changed master data; no scheduler/cron that generates rows monthly; simpler GoBD footprint (no additional table to be kept immutable). Drawback: a per-month dunning status (M7) still needs an anchor (see below). **Recommended.** |
| **B — expected amount persisted (materialized)** | A job generates one `open_item` row (expected) per month/lease or owner, which is then reconciled against payments. | Stable anchor for the dunning status per month; but: a second truth that needs maintenance (quickly becomes inconsistent on master data changes — retroactive rent change, corrected plan); needs a reliable generation job; the additional GoBD question "is a materialized expected charge a voucher to be frozen?" arises. More moving parts for an MVP. |

### Question 2 — Payment immutable (reversal) vs. editable

| Option | Mechanics | Assessment |
| --- | --- | --- |
| **U — payment immutable; correction = reversal + rebooking** | A captured, "booked" payment is append-only. Typo/wrong assignment → **reversal payment** (opposite entry referencing the original) + if needed the correct rebooking. Pattern as in ADR-0003 (trigger, freeze condition). | Payments are **booking vouchers** (GoBD, SDD §6.6/§17, 8-year retention). A booking voucher is not overwritten; the correction is traceable (who/when/why). Consistent with the project-wide I2 invariant and the existing `cost_entry`/`settlement_run` pattern. **Recommended.** |
| **E — payment freely editable** | UPDATE of the amount/date/assignment fields allowed. | Violates the GoBD immutability of a booking voucher; an assigned payment that is changed afterwards can retroactively make an already **finalized** settlement (which used this payment as `DERIVED` actual) contradictory. Rejected. |

## Decision

**(1) Expected position derived (option A).** *(Recommendation, [DECISION E6-1])*

- The expected positions of a person/period are **computed** from master data, not materialized per month. A pure engine function generates, from `lease_agreement` (base rent + operating cost advance, day-accurately prorated for a mid-year start/end, monthly basis) or from the finalized `economic_plan` (`OWNER_ADVANCE.monthly_amount`), the due expected amounts per month (`expected_charge` list).
- `open_item` is the **computed balance view** per (person, period, type): `open = expected − assigned actual`. It is **not** a row persisted per month.
- **M7 anticipation deliberately avoided, but door left open:** Dunning levels/history (M7) need a per-item anchor. So that M6 does not unilaterally pre-empt this: M6 delivers the *computed* open items; whether M7 materializes from them a small **persisted dunning/status record per overdue item** (referencing the computed expected period) is **[§18/domain F6-1]** and a matter for M7. M6 does **not** decide dunning persistence.

**(2) Payment immutable; correction = reversal + rebooking (option U).** *(Recommendation, [DECISION E6-2])*

- A `payment` row is, after being booked ("Verbuchen", status `booked`), an **immutable booking voucher**. Content-bearing columns (amount, direction, date, bank account, counterparty, purpose/type, assignment) are then frozen.
- **Correction = reversal + rebooking:** A reversal is a new `payment` row with the opposite sign/marker (`reverses_id` → original) that neutralizes the effect of the original payment; the correct booking is another new row. The original remains preserved unchanged (GoBD, SDD §14).
- **Mechanics = ADR-0003 pattern, reused, not reinvented:** status-aware `BEFORE UPDATE/DELETE` trigger with freeze condition `status='booked'` (analogous to `cost_entry.status='confirmed'` / `settlement_run.status='finalized'`). In status `draft` (before booking) a payment is freely editable/deletable (capture correction), exactly like a `cost_entry` draft. The only permitted mutation of a `booked` row: setting `reversed_by_id` (reversal chain, analogous to `superseded_by_id`).
- **Rationale for the `draft`/`booked` split:** Plain capture (typing) must be correctable without immediately creating a reversal voucher — otherwise every typing correction becomes a GoBD transaction. The freeze point is the deliberate "Verbuchen" (booking), analogous to the `confirm` of a cost item (m2). **[DECISION E6-3]** whether M6 maintains this split or a payment is `booked` **immediately** (no draft). Recommendation: split (`draft`→`booked`), because it mirrors the established pattern and does not force the capture UX into GoBD reversals.

**Interaction with finalized settlements (the actual reason for the immutability):** A finalized operating cost (BK)/WEG settlement freezes its `advance_payments_total`/actual as a **copy** into the snapshot (M3 §1.2, M4 §1.5). A later reversal/correction of a payment therefore does **not** change the **finalized** settlement (the snapshot stays immutable). The correction only takes effect on the **next** (reversal/recalculation) settlement (`create_correction_run`). This keeps the chain internally consistent: immutable payment → immutable settlement snapshot → correction always as a new version.

## Consequences

**Positive:**
- **One source of truth for the expected amount** (lease/plan); no drift, no materialization job, no "orphaned" expected amount after a master data change.
- **Payment as an audit-proof booking voucher**: append-only, DB-enforced (ADR-0003 pattern), 8-year retention handled cleanly (payment carries a `retention_until` anchor analogous to `invoice_document`).
- **Consistency with the existing system**: `advance_payments_source='DERIVED'` (prepared in M3/M4) draws the actual sum from exactly these immutable, unambiguously typed payments; no double counting, because every payment is assigned to exactly one expected type/addressee/period (m6 §6).
- **Snapshot integrity is preserved**: finalized settlements are immune to later payment corrections (they carry the frozen actual copy).

**Negative / to note:**
- Derived expected amounts mean: the **state** of the tenant ledger at a point in time is a computed quantity; the calculation rule (monthly basis, day-accurate cuts at lease start/end, due date) must be **deterministic and tested** (m6 §2, golden tests m6 §7), otherwise the balance "jumps".
- The `draft`/`booked` split requires a deliberate "Verbuchen" (book) step in the UI (analogous to `confirm`); this is intended, but an additional click.
- Reversal-instead-of-edit is less familiar to users than "change the row"; the UI must offer reversal + rebooking as a guided process (otherwise frustration). From a domain perspective, however, without alternative for a booking voucher.

**Non-negotiable (consequences of this ADR):**
1. The expected amount is **derived** from lease/plan; there is no second, materialized expected-amount truth that could drift (M6).
2. A **booked** payment is an immutable booking voucher; correction exclusively via **reversal + rebooking** referencing the original (`reverses_id`/`reversed_by_id`), DB-enforced (ADR-0003 trigger, freeze condition `status='booked'`).
3. A **finalized** settlement is never changed by later payment corrections (it carries the frozen actual copy); the correction only takes effect on a new settlement version.
4. Every DB-enforced immutability is evidenced by a negative test that would turn red without the trigger (m6 §7).

> **[§18/domain] No self-certification:** This ADR makes expected amount, actual, open items and voucher immutability *transparent, deterministic and recomputable* and discloses the assumptions made. Whether the tenant ledger level of detail, the treatment of security deposit/security deposit account and the GoBD voucher quality of a manually captured payment are audit-proof in a specific case must be domain-reviewed (tax advisor/specialist attorney). This ADR fixes the *technical* definition, not the final legal assessment.
