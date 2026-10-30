# ADR-0011: Resolution register (Beschluss-Sammlung) -- immutability and gapless numbering

Status: **Accepted**
Date: 2026-06-02 -- Context: M8 -- Reference: SDD section 6.10, WEG section 24 para. 7, ADR-0003

## Context

The resolution register (WEG section 24 para. 7) is a legally required,
consecutively numbered register of all resolutions of a homeowners'
association. Each entry contains the date, place, wording and, where applicable,
a challenge/annulment annotation. The register is open to inspection
(for owners and their legal successors).

Two invariants must be DB-enforced:
1. **Gapless, consecutive numbering** per WEG (property).
2. **Immutability** of the wording, number, date, place and resolution
   reference once entered. The only permitted addition is the
   challenge/annulment annotation.

## Decision

### Numbering

- `sequence_number` is assigned in the creation transaction as `max(seq WHERE property_id) + 1`.
- `UNIQUE (organization_id, property_id, sequence_number)` prevents duplicates.
- Gaplessness follows from the fact that only the service layer creates entries
  and always uses max+1. DELETE is forbidden by trigger, so
  no gaps can arise from deletion.
- With concurrency (two simultaneous INSERTs), the UNIQUE constraint
  fails for one of the two -> IntegrityError, retry.

### Immutability

Mechanics: **BEFORE UPDATE / BEFORE DELETE trigger** (ADR-0003 pattern), but
in a stricter variant: the freeze applies **from INSERT**, not from
a status transition. There is no "draft" status for register entries.

Frozen columns (any change -> RAISE EXCEPTION):
- `organization_id`, `property_id`, `sequence_number`, `entry_date`,
  `location`, `wording`, `resolution_id`, `created_at`

Permitted mutations:
- `challenge_annotation` (challenge/annulment annotation) -- can be
  set, updated or deleted at any time.
- `updated_at` (technical timestamp)

DELETE is ALWAYS forbidden (trigger + no DELETE grant for app_rw as
defense-in-depth, consistent with ADR-0003).

## Consequences

**Positive:**
- The statutory requirement for the resolution register is DB-enforced,
  not just a convention.
- A bug in the service or an ad-hoc UPDATE cannot corrupt the wording or
  the numbering.
- Provable by negative test (UPDATE on wording/number fails,
  DELETE fails, challenge annotation update succeeds).

**Negative / to note:**
- An erroneous entry (e.g., a typo in the wording) can only be documented by
  a correction note in the challenge_annotation field,
  not by overwriting. This corresponds to the paper-based precedent.
- Migrations that touch register data are a red flag in review
  (as with all ADR-0003-protected tables).
