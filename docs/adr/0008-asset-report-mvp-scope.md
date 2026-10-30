# ADR-0008: Asset report (Vermögensbericht, § 28 IV WEG) — MVP scope given the current data state

Status: **Proposed** (confirmation by the human pending — domain sign-off required; no code/commit yet)
Date: 2026-06-02 · Context: M4 · Reference: [../m4-weg-abrechnung.md](../m4-weg-abrechnung.md) §6 · [../sdd-property-management-mvp.md](../sdd-property-management-mvp.md) §6.3, §11, §17, §18 · [../architecture.md](../architecture.md) §5.3 (`bank_account` anchor) · [archived roadmap](../archive/roadmap.md) M4/M6/M7
Author: `engine-correctness` (Opus 4.8)

> This ADR delimits the **MVP scope of the asset report** (§ 28 IV WEG) against the **current data state** (M0–M3): what can be done cleanly and automatically now, what is captured manually, and what is deliberately deferred to M6/M7. It makes **no** unilateral domain determination — the MVP scope is **[§18/domain]** and must be signed off by a tax advisor/WEG specialist attorney before productive use. It makes the delimitation **transparent** instead of feigning a completeness that the data cannot support.

## Context

§ 28 para. 4 WEG obliges the property manager to prepare, **after the end of the calendar year**, an **asset report** (Vermögensbericht) that contains:
1. the **balance of the maintenance reserve** (Erhaltungsrücklage), and
2. a **statement of the essential community assets** (essentially bank account balances, receivables and liabilities of the association).

SDD §11: "The asset report contains **at least** the balance of the maintenance reserve and a statement of the essential community assets (receivables/liabilities of the association)." SDD §18 explicitly lists the exact PDF structure of the asset report as an **open point**.

The current data state limits what can be determined **automatically**:
- **Maintenance reserve:** Contributions can be captured as `cost_entry` of category `ERHALTUNGSRUECKLAGE` (`reserve_relevant=True`, seeded); the development (opening + contribution − withdrawal = closing) can be derived in M4 (m4 §3.3).
- **`bank_account`:** exists as an **M1 model anchor** (model comment: "model anchor: schema + FK only; full implementation in M6") — **without** balance/booking columns. **No bank statement import** (SDD §3 non-goal: "No bank import (CAMT/MT940) and no automatic payment reconciliation").
- **Receivables/liabilities:** **not modeled** — open items (`OpenItem`) and payment status are M6/M7; a `cost_entry` is a cost item, not a payment status.

## Options

| Component | (A) automatic now | (B) manual in the report snapshot | (C) deliberately deferred to M6/M7, placeholder |
| --- | --- | --- | --- |
| **Maintenance reserve balance** | **possible** (reserve development, m4 §3.3) | (opening balance/withdrawals manual for lack of bookings) | — |
| **Bank account balances** | not possible (no balance column, no import) | manual reporting-date balance per account | conceivable |
| **Receivables** | not possible (`OpenItem` = M6/M7) | manual item | conceivable |
| **Liabilities** | not possible (payment status = M6) | manual item | conceivable |

A **balance column/movement table on `bank_account`** (instead of a manual reporting-date balance) would anticipate the M6 booking logic (payments/tenant ledger (Mietkonto)) and is **premature** — it would prejudge the model that has to be built in M6 anyway.

## Decision

**MVP scope (for domain confirmation, not unilaterally final):**

1. **Maintenance reserve balance: (A) automatic** from the reserve development (m4 §3.3): `closing balance = opening balance + Σ contributions − Σ withdrawals`. Opening balance and withdrawals are captured **manually** in M4 (there is no booking logic yet; **[§18/domain F4-4/F4-5]**), the contributions come from the `ERHALTUNGSRUECKLAGE` `cost_entry`s. The engine checks the development invariant (fail-loud).

2. **Bank account balances: (B) manual reporting-date balance in the report snapshot** (recommendation). The processing clerk captures a reporting-date balance per `bank_account` (as of `period_end`), which is frozen into the `snapshot_payload` and shown as a `BANK_BALANCE` line. **No** schema extension to `bank_account` (that belongs to M6). **[§18/domain F4-8]**

3. **Receivables/liabilities: (B) manual capture with an origin note** (recommendation for the MVP) as `RECEIVABLE`/`PAYABLE` lines in the report snapshot — **with a clear PDF note** that these are **property manager statements** (Verwalter-Angaben) and **not** an automatic balance from bookings. Alternative (C) — deliberately deferring to M6/M7 with a clearly marked placeholder — is equally defensible from a domain perspective. **This choice is [§18/domain] and expressly reserved for the domain decision.**

4. **Data model:** Asset report as `settlement_run` `run_type='VERMOEGENSBERICHT'` (property-wide), balance `settlement_line`s (`RESERVE_OPENING`/`RESERVE_CONTRIBUTION`/`RESERVE_WITHDRAWAL`/`RESERVE_CLOSING`/`BANK_BALANCE`/`RECEIVABLE`/`PAYABLE` — additive `line_type` CHECK extension, m4 §3.2/§6.5); complete frozen statement in the `snapshot_payload`. No expected/actual (Soll/Ist) logic.

**Rationale.** The MVP should make § 28 IV **formally operable** **without feigning correctness that the data cannot support**. The reserve balance is genuinely derivable and is delivered automatically. Bank account balances and receivables/liabilities are, at the current state, **property manager statements** — keeping them as manual, clearly labeled reporting-date values in the frozen report is more transparent and honest than either (i) leaving a mandatory field empty or (ii) faking an automatism from incomplete data. The manual reporting-date balance does **not** anticipate the M6 booking logic (no `bank_account` schema rework).

## Consequences

**Positive:**
- § 28 IV is **operable** in the MVP; the reserve balance is recomputable (development invariant).
- **Honest delimitation:** Manual values are labeled as such; no feigned derivation from bookings.
- **No M6 anticipation:** `bank_account` remains the pure M1 anchor; real balances/movements come cleanly with M6.
- The report is a **frozen reporting-date record** (snapshot + hash), GoBD-compliant like the settlements.

**Negative / to note:**
- Manually captured bank account balances and receivables/liabilities are **not** automatically plausibility-checked against bookings — an input/transcription error by the property manager is possible. From M6 on (payments/open items), these values should be **replaced** by derived balances; the report snapshot `source` note makes the transition traceable.
- Whether a manual reporting-date balance and manual receivable/liability statements **suffice** for § 28 IV in a specific case must be **domain-reviewed** (tax advisor/WEG specialist attorney). This ADR does **not** decide that conclusively.

**Non-negotiable (consequences of this ADR):**
1. The reserve balance is derived from the recomputable development (opening + contribution − withdrawal = closing); the invariant is checked (fail-loud).
2. Manually captured values (bank account balances, receivables/liabilities) are **labeled as property manager statements** in the PDF — no feigned automatic derivation.
3. No schema extension to `bank_account` in M4 (no M6 anticipation); the manual reporting-date balance lives exclusively in the frozen report snapshot.
4. The asset report is a hashed snapshot to be frozen (ADR-0003 mechanics, same envelope as the settlements).

> **[§18/domain] No self-certification:** The MVP scope makes the asset report transparent and auditable and discloses which values are automatic and which are manual property manager statements. Whether this scope satisfies § 28 IV WEG in a specific case — in particular the treatment of receivables/liabilities and the acceptance of manual reporting-date balances without a bank reconciliation — must be signed off by a **tax advisor/WEG specialist attorney** before productive use. The SDD §18 question "exact PDF structure … asset report" remains a product/domain decision.
