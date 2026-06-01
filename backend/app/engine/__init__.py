"""M3 distribution & settlement engine -- a layer of PURE functions.

Architecture: SDD section 16, m3-abrechnungs-engine.md section 0/section 10.1.

HARD RULE (the reason this package exists):
  This package contains ONLY pure computation.  It MUST NOT import from
  app.models, app.repositories, app.services, app.api, sqlalchemy, fastapi,
  or perform any DB / HTTP / file I/O.  Everything in and out is Decimal,
  date, and frozen dataclasses.

  This separation is what makes the billing math testable in isolation
  (Golden tests + property-based invariants, section 9) and reusable for M4
  (WEG-Jahresabrechnung) without dragging persistence along.

Money and quotas are ALWAYS Decimal, never float (architecture.md I4,
ADR-0005 "not negotiable" #1).

Modules:
  rounding.py     -- distribute_amount (Largest-Remainder, ADR-0005). THE
                     single place money is rounded to cent.
  periods.py      -- day-exact time arithmetic (ADR-0004): tenant/vacancy
                     period splitting, time factors.
  distribution.py -- per-key weight vectors (section 2) + the time x key
                     chaining (section 3.2).
  settlement.py   -- orchestrates one BK_MIETER computation -> EngineResult.
  canonical.py    -- canonical JSON serialization + sha256 for the snapshot
                     (section 1.8); deterministic, money-as-string.
  types.py        -- frozen dataclasses mirroring the snapshot inputs.
"""

from __future__ import annotations
