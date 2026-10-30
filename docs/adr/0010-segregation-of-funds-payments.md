# ADR-0010: Segregation of funds (Vermögenstrennung) for payments — DB constraint hardening now, no second RLS axis

Status: **Proposed** (confirmation by the human pending — no code/commit yet)
Date: 2026-06-02 · Context: M6 (resolves the item explicitly deferred to M6 in M0) · Reference: [0001-organization-isolation-rls.md](0001-organization-isolation-rls.md) (§Delimitation: "DB hardening of the segregation of funds at the earliest with M6") · [../architecture.md](../architecture.md) §2.6, assumption A-4, §9 item 4 · [../m6-zahlungen-mietkonto.md](../m6-zahlungen-mietkonto.md) §4 · [../sdd-property-management-mvp.md](../sdd-property-management-mvp.md) §4 (segregation of funds), §6.3
Author: `engine-correctness` (Opus 4.8)

> M0 (architecture.md A-4, ADR-0001 §Delimitation) explicitly recorded: segregation of funds is, in M0, **only prepared in the model**; the question of **DB hardening** is "to be decided at the earliest with M6, when accounts/payments are real." M6 makes accounts/payments real — this ADR decides it. **[DECISION]** for the human.

## Context

**Two isolation axes** (architecture.md §2.6, SDD §4):

- **Organization isolation (Mandantentrennung) (I1):** between *organizations*. Strictly enforced via RLS (ADR-0001) — unchanged.
- **Segregation of funds:** *within* an organization between management mandates/WEGs. Funds and vouchers of the WEG "Hauptstr. 5" are never commingled with those of the WEG "Bahnhofstr. 12" (fiduciary/third-party funds segregation, SDD §4: "Funds and vouchers of different mandates/WEGs are never commingled. Each WEG or client has its own accounts and its own settlement sphere.").

With M6, real **money movements** (`payment`) against **accounts** (`bank_account`) arise for the first time. This turns the segregation of funds from a model preparation into a property that must be enforced: a payment must be guaranteed to be assigned to the **correct** account/mandate/property, and an account belongs to exactly **one** property/mandate. A "cross-booking" (a payment of WEG A lands on the account of WEG B *within the same organization*) is a serious error relevant to fiduciary duty — RLS does **not** catch it, because both WEGs belong to the **same** organization (RLS isolates only between organizations).

## Options

| Option | Mechanics | Isolation strength | Effort | Assessment |
| --- | --- | --- | --- | --- |
| **A — App layer only** | The service checks on booking that payment and account belong to the same mandate/property; no DB guarantee. | Weak: a service bug or ad-hoc INSERT can cross-book. | Low | Insufficient for trust funds — the same weakness that ADR-0001 explicitly did *not* accept for I1. |
| **B — DB constraint hardening (CHECK/FK consistency), no second RLS axis** | The DB enforces the **coherence** of the asset references: (a) `bank_account` belongs to exactly one `property` (+ optionally `management_mandate`); (b) `payment.bank_account_id` is mandatory and the payment inherits property/mandate **from the account** (not freely selectable); (c) composite FK/trigger ensure that `payment.property_id` == `bank_account.property_id`. | Strong for *coherence* (no cross-booking); without the operational overhead of a second RLS policy axis. | Medium | **Recommended.** |
| **C — Second RLS axis (mandate-scoped RLS)** | In addition to the org RLS, a mandate/property-bound RLS policy with a second session GUC (`app.current_mandate_id`). | Very strong (the DB filters even within the org). | High: second GUC in the tenant context, every cross-mandate view (deadline monitor, all open items — which the property management *needs*, ADR-0001 §Consequences) would have to deliberately widen the second axis; coverage tests double. | Premature/excessive for the MVP; takes away the desired cross-mandate views from the property management or requires constant deliberate bypassing. |

## Decision

**Option B — DB constraint hardening of the asset coherence; no second RLS axis.** *(Recommendation, [DECISION E6-4])*

1. **Account anchor remains, becomes the mandatory pivot:** `bank_account` belongs to exactly one `property` (column `property_id` already exists) and optionally to one `management_mandate`. A payment is **always** captured against an account (`payment.bank_account_id NOT NULL`).
2. **Payment inherits the asset reference from the account, not freely selectable:** `payment.property_id` (and `management_mandate_id`) are derived/mirrored **from the chosen `bank_account`**, not set independently. Secured DB-side via a **composite foreign key**: `payment(organization_id, bank_account_id, property_id)` references `bank_account(organization_id, id, property_id)` (with a matching UNIQUE index on `bank_account`). This makes "payment on account X claims property Y, but account X belongs to property Z" **impossible in the DB** — not just an app convention.
3. **No cross-booking across mandates/properties:** Since property/mandate of the payment follow from the account and accounts are separated per property, the segregation of funds is enforced as a **coherence invariant** of the table.
4. **Organization isolation (I1) unchanged:** All M6 tables carry `organization_id` + RLS (four policies, fail-closed, pattern `0010`/`0014`) like everything else. The asset hardening is an **additional** coherence layer *within* the org, **not** a replacement for RLS.
5. **Account type consistency (plausibility, no hard constraint in the MVP):** From a domain perspective, security deposit payments belong on the security deposit account, reserve portions on the reserve account. **[§18/domain F6-2]** whether this is enforced in the MVP as a **hard** CHECK (`payment.payment_type` ↔ `bank_account.account_type`) or only carried as a **warning**. Recommendation: **warning/plausibility** in the MVP (real-world data is messy; a hard constraint can block legitimate special cases), but the property↔account coherence (item 2) **hard**. Must be domain-reviewed.

**Rationale.** Fiduciary/third-party funds segregation tolerates — just like the organization isolation in ADR-0001 — no pure app-layer discipline: a cross-booking is liability-relevant. A **composite FK coherence** makes the segregation a *property of the storage* (the ADR-0001 guiding motif), without the operational overhead and reporting friction of a second RLS axis. Option C (second RLS axis) would be the strongest isolation, but is excessive for an MVP and would stand in the way of the cross-mandate management views (deadline monitor, all open items) that ADR-0001 names as a desired advantage of the shared-DB architecture. Option B hard-secures the concretely endangered scenario (cross-booking of money) and leaves the later upgrade to C open, should an audit demand it.

## Consequences

**Positive:**
- Cross-booking of money across property/mandate boundaries *within* an org is **impossible in the DB** (composite FK), not just an app convention — the core fiduciary concern is addressed hard.
- No second session GUC, no doubled RLS policies, no friction with the desired cross-mandate views of the property management.
- The asset reference of a payment is **unambiguous and audit-proof** (follows from the account) — basis for the clean `DERIVED` derivation into the settlement (m6 §6) and for mandate-segregated balances.
- Consistent with the ADR-0001 philosophy (invariant as a storage property, defense-in-depth), dosed one level weaker (coherence instead of full RLS), matching the actual risk.

**Negative / to note:**
- The composite FK requires an additional UNIQUE index `(organization_id, id, property_id)` on `bank_account` and care during migration (m6 §8).
- The account type ↔ payment type consistency remains soft in the MVP (F6-2) — a deliberately accepted remainder, to be domain-reviewed.
- A later upgrade to option C (second RLS axis) would remain possible, but would be a separate, deliberate step (tenant context extension) — recorded here, not blocked.

**Non-negotiable (consequences of this ADR):**
1. A payment is always captured against exactly one `bank_account`; property/mandate of the payment follow from the account and are bound to the account DB-side (composite FK) — cross-booking is impossible.
2. `bank_account` belongs to exactly one `property`; segregation of funds is a coherence invariant of the payment/account tables.
3. Organization isolation (I1, RLS) remains the primary, org-wide isolation axis, unchanged; the asset hardening complements it *within* the org, does not replace it.
4. The property↔account coherence is evidenced by a negative test that sees a cross-booking attempt fail DB-side (m6 §7).

> **[§18/domain] No self-certification:** This ADR makes the segregation of funds for payments *transparent and coherent as enforced by the DB* and discloses that the *complete* mandate-bound DB isolation (option C) is deliberately not implemented. Whether the coherence hardening suffices for the fiduciary/third-party funds requirements in the specific operation must be domain-reviewed (tax advisor/specialist attorney, if applicable auditor (Wirtschaftsprüfer)).
