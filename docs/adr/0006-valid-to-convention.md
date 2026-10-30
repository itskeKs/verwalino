# ADR-0006: valid_to convention — API inclusive, storage exclusive (conversion at the API boundary)

Status: **Accepted** (decision made by the human; implementation done — additive fix, no migration)
Date: 2026-06-02 · Context: M1 ↔ M3 (read/write boundary) · Reference: [0004-pro-rata-daily-allocation.md](0004-pro-rata-daily-allocation.md) #6 · [../m3-abrechnungs-engine.md](../m3-abrechnungs-engine.md) §3.4 · [../sdd-property-management-mvp.md](../sdd-property-management-mvp.md) §6.2, §14
Author: `engine-correctness` (Opus 4.8)

> This ADR records a **decision already made by the human** and documents the implemented mechanics. It is the follow-up consolidation to the M3 read-edge fix (settlement_service `−1`): after the *engine* read boundary was fixed, this step makes the *API* write/display boundary consistent, so that the `valid_to` semantics follow a single, verifiable rule throughout the system.

## Context

The M1 temporal tables `lease_agreement` and `ownership_history` store periods **half-open** `[valid_from, valid_to)`:

- The EXCLUDE constraint (M1, migration 0003) uses `daterange(valid_from, valid_to, '[)')` — the upper bound is **exclusive**.
- The point-in-time query (`*_repo`) uses `valid_from <= D AND (valid_to IS NULL OR valid_to > D)` — likewise an exclusive upper bound.

Thus the stored `valid_to` is the **first NOT occupied day**. Two adjoining records `… valid_to = D` and `valid_from = D` do **not** overlap in the DB: Day D belongs exclusively to the second record. This is the clean, btree_gist-enforceable representation for "gap-free, non-overlapping" and remains the foundation of the organization/chronology invariants (SDD §14).

Two problems arose from this storage convention at the edges:

1. **Engine (already fixed):** The engine calculates **day-inclusive** `[from, to]` (ADR-0004). Without conversion, it read the stored exclusive `valid_to` as the last occupied day, counted the boundary day D twice (predecessor *and* successor) and raised `PeriodOverlapError` → live 500 in the DB-legal case "move-out 30.06. / move-in 01.07.". Fixed by the read-boundary conversion in `settlement_service` (`valid_to − 1 day`).

2. **API (this ADR):** What does the **user** enter, and what do they see back? If they enter the storage convention (first unoccupied day), they must enter the value `2025-07-01` for "lease ends 30.06." — counterintuitive and error-prone. If the API reads the raw stored value back, they see `2025-07-01`, although in business terms they mean "until 30.06.".

## Decision (by the human)

**The API is day-inclusive; storage stays half-open/exclusive. The conversion happens exclusively at the API boundary.**

- **User input (inclusive):** "Lease ends 30.06." → input `2025-06-30` (the **last occupied day**).
- **Storage (unchanged, exclusive):** The DB continues to store `[valid_from, valid_to)` with `valid_to` = first unoccupied day. **No migration, no EXCLUDE rebuild, no query rebuild.**
- **Conversion only at the API boundary:**
  - **Write side** (`POST /api/leases`, `POST /api/ownership`): incoming inclusive `valid_to` → `+1 day` → store as exclusive. `valid_from` stays unchanged (first occupied day). `NULL` stays `NULL` (open).
  - **Display side** (`LeaseOut`/`OwnershipOut` for list, detail and the `tenant_at_date`/`owners_at_date` path): stored exclusive `valid_to` → `−1 day` → return as inclusive. The user sees exactly what they entered.
- **Engine read boundary (unchanged):** `settlement_service` continues to convert storage-exclusive → engine-inclusive (`−1`). **Do not convert twice:** The engine reads directly from storage, not via the API.

### Precise rule (a single source of truth)

There are three representations of an upper period bound and exactly **one** place that converts between them — `app/core/temporal.py`:

| Representation | Semantics of `valid_to` | Where |
| --- | --- | --- |
| **Storage (DB)** | first NOT occupied day (exclusive, `[)`) | `lease_agreement` / `ownership_history` tables |
| **API (in/out)** | last occupied day (inclusive) | `leases.py` / `ownership.py` request/response |
| **Engine** | last occupied day (inclusive, `[from, to]`) | `periods.py` (ADR-0004) |

The relationship is purely ±1 calendar day (`DATE`, no `TIMESTAMP`; no time zone/DST):

```
storage_exclusive     = inclusive_last_day + 1 day    (write boundary)
inclusive_last_day    = storage_exclusive  − 1 day    (display and engine boundary)
NULL ↔ NULL                                           (open stays open)
```

The API-inclusive day and the engine-inclusive day are **the same calendar day** (both "last occupied day"); they differ only in *where* the `−1` conversion takes place (API display or engine read edge). That is why `db_exclusive_to_api_inclusive` and `db_exclusive_to_engine_inclusive` are the same calculation — just under two names, so that each call site documents its own intent.

### Not affected

- **`cost_entry.service_period_from/_to` (M2):** **inclusive** in business terms, **no** `[)` EXCLUDE. Is **never** converted (ADR-0004 #6, m3 §3.4).
- **`at_date` query parameter** (`tenant_at_date`/`owners_at_date`): a **point in time**, not an upper bound of a period. The half-open point query `valid_from <= D AND valid_to > D` correctly returns, for every calendar day D, the record occupying D — independent of the inclusive/exclusive convention. `at_date` is therefore **not** converted; only the **output** `valid_to` (via `_to_out`) is inclusive.
- **Settlement period boundaries** (`period_start`/`period_end` from the UI): already inclusive, no `[)`. Unchanged.

## Consequences

**Positive:**
- **Intuitive API:** "ends 30.06." ↔ `2025-06-30`. Roundtrip-stable: What goes in comes back.
- **Storage untouched:** The btree_gist EXCLUDE and the chronology invariant (SDD §14) remain exactly as they are — no migration risk, no touching of the enforced organization/immutability mechanics.
- **Adjoining records work without a special case:** "A until 30.06." + "B from 01.07." → the write boundary maps A.valid_to to `2025-07-01` = B.valid_from → `[.,2025-07-01)` and `[2025-07-01,.)` do not overlap under `[)` (no false 409). The engine read boundary (`−1`) turns this back into A=…30.06., B=01.07.… → Σ time shares = 1, no phantom day (ADR-0004 #4). Write `+1` and engine `−1` cancel out exactly.
- **A single source of truth:** All ±1 conversions live in `app/core/temporal.py`; the engine service and both APIs import from there. No "sometimes +1, sometimes −1" scattered around.

**Negative / to note:**
- **Double conversion would be a bug:** The engine reads from **storage** (not via the API), so it applies its own `−1`. If the engine were ever to read via the (already inclusive) API, an off-by-one would result. By construction it reads raw from the tables; the off-by-one guard test pins both ends.
- **Update path:** The phase 1 APIs for lease/ownership only have `create` (no `update` endpoint). If an `update` were added, it **must** apply the same write conversion — the central helper function exists for this.
- **Existing/import data:** Data written directly to the DB, bypassing the API (seeds, migrations, future bulk imports), must itself comply with the storage convention (exclusive `valid_to`) — the API conversion does not apply there. The existing seeds already do this explicitly (`test_m3_settlement.py`).

**Non-negotiable (consequence of this ADR):**
1. Storage stays half-open `[valid_from, valid_to)`; `valid_to` = first unoccupied day. No migration.
2. The ±1 conversion exists in exactly **one** code location (`app/core/temporal.py`) and is applied **exactly once** per data flow (write boundary: `+1` once; display boundary: `−1` once; engine read edge: `−1` once).
3. `service_period_*` and `at_date` are **never** converted.

## Test evidence

Via the real endpoints (`tests/test_valid_to_convention.py`):

- **Roundtrip:** Create a lease "ends 2025-06-30" via the API → POST echo and GET return `valid_to=2025-06-30`; a direct DB check shows `2025-07-01`. Likewise for ownership.
- **Adjoining (end-to-end):** "A until 2025-06-30" + "B from 2025-07-01" can be created via the API (no 409); then `compute` → A=01.01.–30.06. (181 days), B=01.07.–31.12. (184 days), 181+184=365, A+B costs = the full 1200,00 € (no phantom day, no cent lost). Proves that write `+1` and engine `−1` compose correctly.
- **Off-by-one guard:** One test pins both ends against a POST: without the write `+1`, the DB assert (`== 2025-07-01`) turns red; without the display `−1`, the GET assert (`== 2025-06-30`) turns red. Both directions were verified as red by temporarily removing the respective conversion.
- **Helper unit tests:** `+1`/`−1`, NULL preservation, leap year/year boundary, roundtrip identity, engine alias == display alias.

The existing engine read-edge regression (`TestM3AdjacentLeaseReadEdge`) and the existing helper test (`db_exclusive_to_engine_inclusive`) remain unchanged and green; the helper is now re-exported from `app/core/temporal.py` (import path `app.services.settlement_service` preserved).

> **No self-certification:** The inclusive input matches the understanding that is customary in business terms ("lease ends on 30.06."). Whether a deviating cut-off date agreement applies in a specific lease remains to be signed off on the domain side (tax advisor/specialist lawyer). This ADR fixes the *technical* convention and its verifiable implementation.
