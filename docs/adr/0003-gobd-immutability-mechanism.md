# ADR-0003: Enforcing GoBD immutability (I2) — status-aware trigger + revoked grants

Status: **Proposed** (confirmation by the human pending — no code/commit yet)
Date: 2026-06-01 · Context: M2 · Reference: [../architecture.md](../architecture.md) §3.2(e), §5.4 · [m2-gobd-belegfluss.md](../m2-gobd-belegfluss.md) · SDD §6.6, §9, §14, §16

> This ADR decides the point deliberately left open in `architecture.md` §3.2(e) and §10: *"Final choice of the immutability enforcement mechanism (trigger vs. grants vs. frozen table) — detail in M2."*

## Context

Invariant **I2** (GoBD immutability, SDD §6.6/§14): A **released** voucher (Beleg) and a **finalized** settlement must never have their content overwritten or deleted; a correction creates a **new version** with a reference to the original. This affects two new M2 tables:

- **`cost_entry`** — cost item. In the status `draft_extracted`/`needs_review` it is **deliberately editable** (the user corrects OCR suggestions), but it is **frozen** upon `confirmed` (settlement-relevant, SDD §9). So it is *not* append-only throughout — it has a clear freeze point.
- **`invoice_document`** — the actual voucher file (metadata + SHA-256 + version chain). An `active` version that has been released (`released_at` set) is append-only; correction = new row with `supersedes_id`.

The existing mechanism for `audit_event` (M0) is **revoked UPDATE/DELETE grants** for `app_rw` plus an omitted DELETE policy — secured by `test_rls_coverage.py::test_audit_event_no_update_delete_for_app_rw`. This works for a table that is append-only **throughout**. It does **not** work unchanged for `cost_entry`, because there UPDATE must be allowed on *draft* rows and is only forbidden from `confirmed` on. A blanket grant revocation cannot express "mutable until release, frozen afterward".

## Options

| Option | Mechanism | Strength | Weakness |
| --- | --- | --- | --- |
| **A — Revoked grants (like `audit_event`)** | `REVOKE UPDATE, DELETE` for `app_rw`. | Hardest, simplest guarantee; no trigger code; already proven. | Does **not** know the row status → cannot express "editable until `confirmed`". Unusable for `cost_entry` alone. |
| **B — BEFORE UPDATE/DELETE trigger, status-aware** | PL/pgSQL trigger: allows defined transitions, forbids any mutation of content-bearing columns once the row is frozen; forbids DELETE of frozen rows. | Expresses the state machine **in the DB**; also applies to the migrator role unless deliberately bypassed; one place for the rule. | Trigger logic must be tested; can theoretically be disabled by a role with `session_replication_role = replica` (superuser/owner only). |
| **C — Separate "frozen" table** | On release, copy the row into a second table without UPDATE/DELETE grant. | Physical separation of mutable/frozen. | Double schema, data duplication, FK/query complexity (vouchers live in two tables); disproportionate for an MVP; keeping two tables consistent is its own source of errors. |

## Decision

**Combination of B (leading) + A (backstop), differentiated by table:**

1. **`cost_entry` and `invoice_document`: status-aware `BEFORE UPDATE` / `BEFORE DELETE` trigger (option B)** as the primary enforcement. The trigger is the only place where "what may still change in which status" is defined:
   - **`cost_entry`** (freeze condition: `status = 'confirmed'`):
     - UPDATE of a `confirmed` row that changes a **content-bearing column** (amount, VAT, category, vendor, date, service period, distribution key, allocability, property/mandate) → `RAISE EXCEPTION`.
     - The **only** permitted UPDATE of a `confirmed` row is the transition `confirmed → superseded` by a correction new version (see voucher-flow (Belegfluss) doc §3) or merely setting a technical "superseded" marker — the trigger permits exactly this column change and no other.
     - DELETE of a `confirmed` row → always `RAISE EXCEPTION`.
     - UPDATE/DELETE in the status `draft_extracted`/`needs_review`/`rejected` (draft) → allowed.
   - **`invoice_document`** (freeze condition: `released_at IS NOT NULL`):
     - UPDATE of a released row that changes `content_sha256`, `storage_uri`, `storage_version`, `version`, `released_at`, `released_by` or `content_byte_size` → `RAISE EXCEPTION`.
     - **Only** the `status` transition `active` → `superseded` (when a successor version is attached) and the setting of `superseded_at` remain permitted; every other column change of a released row is rejected.
     - DELETE of a released row → always `RAISE EXCEPTION`.
   - The exact column list ("content-bearing" vs. "technically permitted") is fixed in the migration as trigger source text and is part of the code review.

2. **Grant backstop (option A) where it fits:** For `invoice_document`, it is additionally being considered to grant `app_rw` **no DELETE** (`GRANT SELECT, INSERT, UPDATE` without `DELETE`) — voucher files are never deleted, only superseded. This is a second line against voucher deletion, independent of the trigger. **[DECISION]** whether DELETE on `invoice_document` is revoked entirely for `app_rw` (recommended: yes) or only blocked via trigger. For `cost_entry`, DELETE is **not** revoked across the board (drafts/`rejected` may be deleted), but is controlled solely by the trigger depending on status.

3. **`settlement_run`/`settlement_line` (from M3/M4):** the same trigger mechanism with freeze condition `status = 'finalized'`. Noted in this ADR so that M3/M4 reuse the same pattern rather than reinventing it.

4. **`audit_event` stays unchanged with option A** (append-only throughout, no status) — do not touch.

**Rationale:** GoBD immutability is **status-dependent** here ("frozen from release"), not absolute. Only a status-aware trigger can express these semantics in the DB itself; revoked grants alone cannot. Where immutability is *absolute* (never delete a voucher file, never change `audit_event`), the harder grant revocation complements the trigger as a second line (defense in depth, consistent with the RLS philosophy of ADR-0001). Option C (frozen table) brings only duplication and consistency costs for an MVP, with no benefit over trigger + grant.

## Consequences

**Positive:**
- The state machine (voucher-flow doc §3) is not merely an application convention but **DB-enforced**: a bug in the service or an ad-hoc `UPDATE` cannot change the content of a `confirmed` cost item or a released voucher.
- Correction-as-new-version becomes the only possible form of correction — exactly as SDD §14 requires.
- Consistent pattern for all finalized entities (vouchers now, settlements from M3/M4).
- Provable via tests (see voucher-flow doc §8): "UPDATE/DELETE on `confirmed` fails", "correction creates new version", "hash verification".

**Negative / to note:**
- Trigger logic is code in the DB and must be versioned (Alembic), reviewed and **tested** (positive *and* negative tests: allowed draft edit vs. rejected `confirmed` edit).
- A trigger can theoretically be bypassed via `SET session_replication_role = replica` — but this requires superuser/owner rights that `app_rw` does not have (ADR-0001). Migration/maintenance jobs (`app_migrator`) must deliberately respect immutability; a later migration that touches finalized data is a red flag in review.
- The trigger must precisely distinguish the "technically permitted" column change (`active → superseded`) from a "content mutation". This distinction is the trickiest part and is explicitly commented in the migration.

**Non-negotiable (across all finalized entities):**
1. Content-bearing columns of a frozen row are immutable — DB-enforced, not merely by convention.
2. Correction exclusively via a new version with `supersedes_id`/version chain; the original is retained and marked `superseded`.
3. Voucher files are never deleted (only superseded); `retention_until` is the only anchor at which, after expiry, removal is decided at all (GDPR (DSGVO) linkage, voucher-flow doc §7).
4. Every DB-enforced immutability is evidenced by a negative test that would fail without the trigger/grant.
