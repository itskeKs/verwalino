# ADR-0012: GDPR (DSGVO) erasure vs. GoBD retention — restriction instead of erasure, anonymization only without a retention binding

Status: **Proposed** — technical mechanics implemented in M9 (`engine/dsgvo.py`, `services/dsgvo_service.py`, `api/dsgvo.py`, tests). Domain/legal sign-off by the data protection officer (DSB) / tax advisor / specialist attorney pending (all **[§18/domain]** points). No self-certification.
Date: 2026-06-02 · Context: M9 · Reference: [../architecture.md](../architecture.md) §3.3 · [../m2-gobd-belegfluss.md](../m2-gobd-belegfluss.md) §7 · [0003](0003-gobd-immutability-mechanism.md) · SDD §15, §17, §18

> This ADR decides, on the **technical** level (which mechanism, which reconciliation invariant), the question explicitly left open in `architecture.md` §3.3 and SDD §18: *"level of detail of the erasure/anonymization concept in the area of tension with audit-proof retention"*. The **legal** sufficiency (which field is a voucher component, which anonymization depth suffices, which start date/duration the retention period has) remains subject to domain sign-off and is marked throughout here with **[§18/domain]**. **No self-certification.**

## Context

SDD §15/§17 demand two things that are in tension:

1. **GoBD/commercial-law retention** (SDD §17, BEG IV from 2025): booking vouchers, released vouchers and finalized settlements must be retained **for at least 8 years, audit-proof and with unchanged content** (invariant I2, ADR-0003). A finalized settlement naturally contains the name/address of the addressee.
2. **GDPR right to erasure / access** (Art. 15, 17, 18 GDPR; SDD §15): A data subject can demand erasure and access, "without violating the audit-proof voucher retention".

The key to the resolution is already in `architecture.md` §3.3 and is condensed here into an implementable rule: **separation of voucher substance and pure person master data** plus **restriction of processing (Art. 18 GDPR) instead of erasure**, as long as a statutory retention obligation exists as the legal basis against the erasure claim (Art. 17 para. 3 lit. b/e GDPR).

The fields in the model exist since M0/M1 (`person.deletion_requested_at`, `person.processing_restricted`, `person.anonymized_at`; `invoice_document.retention_until`, `payment.retention_until`). M9 delivers the **mechanics** and the **reconciliation invariant** that decides *what* happens to *which* person.

### Important characteristic of render-time name resolution

Finalized settlement/dunning PDFs originally resolved the addressee name **exclusively at render time** from `person`/profile (name from the linked `person` record, not frozen into the PDF bytes). If one anonymized a person who is the addressee of a finalized settlement, **the retention-bound document would render a neutral placeholder instead of the correct name** — a violation of the unchanged state/completeness of the retention.

**Fixed by freezing the display names (see §9, implemented 2026-08-10):** When finalizing/issuing, the plain-text names needed for the document are written into the frozen snapshot; render-time resolution remains only a **fallback** for drafts and legacy data. The snapshot is thus self-contained, and re-rendering a finalized document is immune to later master data changes/anonymization.

**This does NOT change the reconciliation decision (§3):** bound persons continue to be **only restricted, not anonymized** (until retention expires). Whether freezing legally sufficed to anonymize a bound person *earlier* (because the voucher presentation would remain correct even without the live person) is a **[§18/domain]** question and is **not** decided unilaterally here — freezing is self-containment/defense-in-depth, not a free pass for anonymization.

## Options

| Option | Mechanics | Strength | Weakness |
| --- | --- | --- | --- |
| **A — Hard delete of the person** | `DELETE FROM person` on an erasure request. | Maximum data minimization. | Breaks FKs (`RESTRICT` on all profiles/vouchers), makes finalized settlements/vouchers unresolvable → **violates I2/retention**. Inadmissible. |
| **B — Anonymize across the board** | Always replace PII with placeholders, keep FKs. | Simple; one code path. | Also anonymizes bound persons → finalized PDFs render incorrectly (see above). Violates retention. |
| **C — Reconciliation: restriction with a binding, anonymization without** | On an erasure request, check whether the person is bound to retention-bound vouchers. **With** a binding → `processing_restricted=true`, keep data, capture the relevant `retention_until`, anonymization only after expiry (purge). **Without** a binding → anonymize immediately. | Fulfills both obligations; congruent with `architecture.md` §3.3; bound PDFs stay correct. | Needs a correct, tested binding determination across several entities. |

## Decision

**Option C.** The reconciliation logic is a **pure function** (`app/engine/dsgvo.py`, testable without a DB); the service (`app/services/dsgvo_service.py`) collects the bindings from the DB and persists the result.

### 1. Retention binding — definition

A `person` is **retention-bound** if at least one of the following references exists (via their profiles or as vendor):

| Binding | Source | Retention legal basis |
| --- | --- | --- |
| **Vendor of a confirmed `cost_entry`** | `cost_entry.vendor_id → vendor.person_id`, only `status='confirmed'` | Booking voucher (GoBD/§147 AO, BEG IV) |
| **Addressee of a finalized `settlement_run`** | `settlement_run.tenant_profile_id`/`.addressee_owner_profile_id` (via `settlement_line`), only `status='finalized'` | Finalized settlement (I2) |
| **Tenant/owner in `lease`/`ownership` of a finalized settlement** | finalized `settlement_run` → `lease_agreement_id` → `tenant_profile_id`; or WEG run → `ownership_history` of the settled units → `owner_profile_id` | Component of the finalized settlement basis |
| **Addressee of an issued `dunning_notice`** | `dunning_notice.tenant_profile_id`/`.owner_profile_id`, only `status='issued'` | Dunning case/booking document |

> **[§18/domain]** The **exact scope** of this list (e.g., whether a *booked* `payment` binds on its own as a booking voucher, whether `cost_entry` already binds without an attached voucher) must be confirmed by domain experts (tax advisor/DSB). The list chosen here is conservatively broad: when in doubt, **rather bind (restrict) than anonymize**, because an accidental anonymization of a retention-bound reference is the more serious error (it violates the retention irreversibly).

### 2. Relevant `retention_until`

The `retention_until` relevant for the person is the **maximum** of the `retention_until` values of all bound vouchers. Where the bound record itself carries no `retention_until` field (`settlement_run`, `dunning_notice`), it is derived **provisionally**:

- `retention_until := (end of the calendar year of the relevant date) + 8 years`,

with relevant date = `period_end` (settlement) or `issue_date` (dunning notice), `invoice_date` (cost_entry, if no voucher is linked).

> **[§18/domain]** This is **the same provisional assumption** as in `m2-gobd-belegfluss.md` §7.3 (period starts at the end of the calendar year, + 8 years). **Which start date applies per voucher type** (invoice date vs. calendar-year end vs. posting-lock date vs. fiscal-year end) is open in SDD §18 and is **not decided unilaterally**. The function is testable and transparent; its legal bindingness must be signed off.

### 3. Reaction to an erasure request (`request_deletion`)

Always first: `deletion_requested_at := now()`, `processing_restricted := true` (from the request on, the person is blocked against new active use — Art. 18). Then reconciliation-driven:

- **With a binding →** **keep the data under restriction**, do **not** anonymize. `retention_until` (maximum, §2) is shown in the audit/export. The person remains complete for the correct render-time resolution of the bound documents. Anonymization only becomes permissible **after expiry** of the relevant `retention_until` (purge, §5).
- **Without a binding →** **anonymize immediately** (`anonymize`, §4).

### 4. Anonymization (`anonymize`)

PII fields are replaced by **neutral, deterministic placeholders**; `id` and all structural FKs (`organization_id`, profiles/references) are preserved (referential integrity, no orphaned vouchers). `anonymized_at := now()`. Affected fields of `person`:

- `first_name`, `last_name`, `company_name`, `email`, `phone`, `street`, `zip_code`, `city`, `iban`, `bic`, `bank_name`.

Placeholders: `last_name := "Anonymisiert"` (German for "anonymized"), remaining plain-text fields `""`/neutral, bank/contact fields `NULL`. The anonymization is **idempotent** (calling it again changes nothing).

> **[§18/domain]** The **exact anonymization depth** (is neutralizing the `person` fields enough, or must, e.g., plain-text traces in `audit_event.payload_diff` / note fields / `snapshot_payload` be handled as well?) must be clarified from a domain perspective. M9 anonymizes the `person` master data; **frozen snapshots are deliberately not touched** (they are voucher components under retention — precisely why a bound person is not anonymized). Note/free-text fields of other entities are recorded as possible PII remainder, but are **not** part of this MVP mechanic.

### 5. Purge after retention expiry

A person with `deletion_requested_at` and a binding becomes **purgeable** as soon as `retention_until < today` holds for **all** bindings: then §4 (anonymization) applies subsequently. The purge is a separate, deliberate operation (no automatism in the MVP).

The purge is realized via the **guarded `anonymize` operation**: the pure function `decide_deletion` returns `purgeable_now=True` (→ `ANONYMIZE_NOW`) when the binding has expired; the service then anonymizes, but otherwise (binding not yet expired) rejects with **409**. Thus the retention period is the hard gate before anonymization — the same code path for "unbound" and "purged after expiry".

> **[§18/domain]** The **trigger** of the purge (manual run, periodic job, four-eyes approval) is open; M9 makes **no** operational decision about it, but only provides the deadline-guarded operation. As long as the binding exists, nothing happens except the restriction.

### 6. Reconciliation invariant (guaranteed DB-side by I2)

**Retention-bound vouchers/snapshots are NEVER changed or deleted by the GDPR path.** This is not a new commitment but coincides with ADR-0003: `cost_entry` (confirmed), `invoice_document` (released), `settlement_run`/`settlement_line` (finalized), `payment` (booked), `dunning_notice` (issued) are immutable via trigger + revoked grants. The GDPR service does **not** touch these tables in a mutating way at all — it changes only `person` fields. Retention is thus the legal basis against the erasure claim, and the render-time resolution of bound documents remains correct until retention expires.

### 7. Access request / export (`export_person_data`)

A pure read aggregation (Art. 15): master data + all references concerning the person (profiles, leases, ownership, payments, settlements as addressee, dunning notices, cost items as vendor) as a structured export structure. Read-only, tenant-scoped, RBAC.

### 8. Blocking new use

Principle: A person with `processing_restricted=true` (or `anonymized_at` set) should **not** be used as counterparty of a **new** active use (new lease, new payment, new cost item as vendor), **never** retroactively on existing, already locked vouchers.

What is implemented in M9 is the **opposite direction**, which is most important here: A deletion request for a person with an **ongoing** (current/future) tenancy or ownership relationship is rejected with **409** (`has_active_relationship` in the service) — the ongoing contractual relationship (Art. 6 I b) must be ended first. The `processing_restricted` status is set and shown via `status`/`export`.

> **[§18/domain] — deliberately deferred:** The **fail-loud 409 blocking on creation** of new leases/payments/cost items that reference an already restricted/anonymized person is **not yet wired into the create paths (M1/M2/M6)** in M9. Where exactly "new use" is to be blocked (only released contracts? drafts too? which entities?) is an open product/legal question; the wiring follows after the scope has been determined from a domain perspective. The status flag and the pure decision are ready for it.

### 9. Self-contained snapshots: freezing the display names (2026-08-10)

Implementation of the polish item deferred in `roadmap.md` as "snapshot name freezing". Closes the gap described in "Important characteristic of render-time name resolution".

**Principle:** The display names that a PDF shows (property, unit, tenant, owner, dunning addressee, lease info) are written into the frozen snapshot **when finalizing/issuing** — still as a draft, before the immutability triggers (ADR-0003) take effect (`OLD.status='draft'`). The six renderers remain **unchanged** (they continue to receive a `*DisplayNames` object); only the **source** of the names changes: frozen (finalized/issued and present), otherwise live. Resolution and reconstruction are centralized in `app/services/render_names.py` (one truth for the freeze and render paths), supplemented for the dunning notice in `dunning_service.py`.

**Storage (versioned: `{"schema_version": 1, "values": {...}}`, so that later format changes remain backward compatible):**
- `settlement_run` (operating cost statement (BK), WEG annual settlement, asset report (Vermögensbericht)) and `economic_plan` (economic plan (Wirtschaftsplan)): under the key `display_names` **in the existing `snapshot_payload`**. This is already protected by the ADR-0003 trigger (denylist contains `snapshot_payload`) and is included in the `snapshot_sha256` — the names are thus **hash- AND trigger-secured**, without a migration and without changing the pure engine snapshot builders (the golden/hash determinism tests remain unchanged and green).
- `dunning_notice`: its own nullable column `frozen_display_names` (migration 0022), because the dunning notice has no `snapshot_payload` (only the list `included_items`, no `snapshot_sha256`). The trigger from 0019 was extended by this column via the **denylist** (0022) — otherwise the new column would be changeable on an issued dunning notice (the triggers check named columns, not the entire row across the board). Freezing happens in the same `issue` UPDATE (`OLD.status='draft'` → allowed).

**Legacy data:** Runs already finalized/issued without frozen names are no longer writable (trigger, ADR-0003) — for them the **live resolution path remains permanently as a fallback**. An unknown (future) `schema_version` value likewise deliberately leads to the live fallback rather than to a wrong reconstruction. The new column is nullable; the migration does not break existing data.

**Drafts** continue to resolve live (no frozen block or `status != finalized/issued`).

**Asset report & meeting PDFs** carry **no person names** (only property data) — the GDPR anonymization conflict does not arise there. For the sake of self-containment, the asset report (`settlement_run`) nevertheless also freezes property name/address (same mechanics). The meeting PDFs (`owners_meeting`: no snapshot/hash, lifecycle `PLANNED`/`HELD`, minutes remain editable until the resolution register (Beschluss-Sammlung)) continue to resolve the property data live. **[§18/domain]** Whether a later renaming of the property may alter a historical set of minutes is an open question that is **not** GDPR-driven.

**Tested** (`tests/test_m9_snapshot_names.py`, 13 tests): (1) freezing on finalizing/issuing; (2) immunity of the finalized/issued render against subsequent anonymization; (3) legacy-data live fallback; (4) live resolution for drafts; plus DB-free tests of the version/fallback/reconstruction logic. WEG annual settlement and economic plan use the same `snapshot_payload` mechanism (identical freeze code in the service).

## Consequences

**Positive:**
- Both obligations (retention *and* GDPR) are fulfilled without violating the other.
- Finalized documents are **self-contained**: their render is immune to later master data changes/anonymization (§9) — render correctness no longer depends solely on the restriction of processing of the bound person, but additionally on the frozen name (defense-in-depth).
- The decision "restrict vs. anonymize" is a **pure, tested function** — traceable and verifiable (golden/reconciliation tests).
- Finalized documents of bound persons stay render-correct until retention expires.
- No new DB mutation path on retention-bound tables → I2 remains untouched.

**Negative / to note:**
- The binding determination must be complete; a **forgotten** binding source would anonymize wrongly (hence conservatively broad, §1, and secured by tests).
- The relevant `retention_until` is **provisional** (calendar-year end + 8 years) — a legal assumption, not a fact.
- Remainders of PII outside `person` (free text, audit payload) are **not** part of this mechanic (§4 [§18/domain]).

**No self-certification:** This concept makes the erasure/retention conflict *transparent, traceable and auditable* and discloses all assumptions. Whether it is privacy-/audit-proof in a specific case must be signed off by the data protection officer and tax advisor/specialist attorney — in particular all points marked **[§18/domain]**.
