"""Single source of truth for the `valid_to` convention conversions (ADR-0006).

There are THREE representations of a temporal upper bound in this system and
exactly ONE place (this module) that converts between them:

  * STORAGE (DB)        -- HALF-OPEN ``[valid_from, valid_to)``. `valid_to` is the
                           first NON-occupied day (EXCLUSIVE upper bound). This is
                           what the M1 temporal tables (lease_agreement,
                           ownership_history) physically store: the EXCLUDE
                           constraint uses ``daterange(valid_from, valid_to, '[)')``
                           and the point-in-time query uses ``valid_to > target``
                           (see migration 0003 + the *_repo point-in-time queries).
                           This representation is UNCHANGED by ADR-0006 -- no
                           migration, no EXCLUDE rebuild, no query rewrite.

  * API (read/write)    -- TAG-INCLUSIVE. The user enters / sees the LAST occupied
                           day ("Vertrag endet 30.06." -> ``2025-06-30``). ADR-0006
                           moves the inclusive<->exclusive conversion to the API
                           boundary so users never see the +1-day storage artefact.

  * ENGINE              -- TAG-INCLUSIVE ``[from, to]`` (ADR-0004 / periods.py).
                           `valid_to` is the LAST occupied day. Same calendar day
                           as the API representation -- both are "last occupied
                           day", they differ only in WHERE the conversion happens
                           (engine: at the settlement read-edge; API: here).

The relationship is purely +/- one calendar day:

    storage_exclusive = inclusive_last_day + 1 day      (write edge)
    inclusive_last_day = storage_exclusive - 1 day       (read / display edge)

``None`` means open-ended and is preserved on every conversion.

ONLY the half-open ``[)``-EXCLUDE temporal columns (lease_agreement.valid_to,
ownership_history.valid_to) pass through here. ``cost_entry.service_period_*``
is fachlich INCLUSIVE, has NO EXCLUDE constraint, and must NEVER be converted
(see ADR-0006 / m3-abrechnungs-engine.md section 3.4).

Discipline: dates only (``datetime.date``); calendar-day arithmetic via
``toordinal`` (no timezone, no DST). The engine read-edge alias
``db_exclusive_to_engine_inclusive`` (kept for its established import path in
settlement_service) and the API display helper ``db_exclusive_to_api_inclusive``
are the SAME function -- both produce the inclusive last occupied day -- but are
named for their two distinct call sites so each read site documents its intent.
"""

from __future__ import annotations

from datetime import date


def inclusive_last_day_to_db_exclusive(inclusive_valid_to: date | None) -> date | None:
    """API/inclusive -> STORAGE/exclusive: the user's last occupied day + 1 day.

    Write edge (ADR-0006). The user enters the last occupied day (inclusive);
    the DB stores the first NON-occupied day (exclusive upper bound of the
    half-open ``[valid_from, valid_to)`` range). Two adjacent records
    "A bis 2025-06-30" and "B ab 2025-07-01" therefore become
    ``A.valid_to = 2025-07-01`` and ``B.valid_from = 2025-07-01`` in storage --
    NON-overlapping under the ``[)`` EXCLUDE constraint (no false 409).

    NULL (open-ended) stays NULL.
    """
    if inclusive_valid_to is None:
        return None
    return date.fromordinal(inclusive_valid_to.toordinal() + 1)


def db_exclusive_to_inclusive_last_day(db_valid_to: date | None) -> date | None:
    """STORAGE/exclusive -> inclusive last occupied day: stored bound - 1 day.

    The inverse of :func:`inclusive_last_day_to_db_exclusive`. The DB stores the
    first non-occupied day; the inclusive last occupied day is that minus one.
    This is the single conversion behind BOTH tag-inclusive consumers:

      * the API display edge (:func:`db_exclusive_to_api_inclusive`), so a GET
        returns exactly the date the user entered;
      * the engine settlement read-edge (:func:`db_exclusive_to_engine_inclusive`),
        so the engine's tag-inclusive ``[from, to]`` math (ADR-0004) sees the last
        occupied day and adjacent periods neither overlap nor leave a phantom day.

    NULL (open-ended) stays NULL.
    """
    if db_valid_to is None:
        return None
    return date.fromordinal(db_valid_to.toordinal() - 1)


# --- Named aliases for the two distinct tag-inclusive call sites -------------
# Same math, two names: each call site reads as what it is. Do NOT double-apply
# (e.g. the engine read-edge converts storage->engine ONCE in settlement_service;
# the API layer converts storage->api ONCE in leases.py / ownership.py).

def db_exclusive_to_api_inclusive(db_valid_to: date | None) -> date | None:
    """STORAGE/exclusive -> API/inclusive (display edge, ADR-0006)."""
    return db_exclusive_to_inclusive_last_day(db_valid_to)


def db_exclusive_to_engine_inclusive(db_valid_to: date | None) -> date | None:
    """STORAGE/exclusive -> ENGINE/inclusive (settlement read-edge, ADR-0004 #6)."""
    return db_exclusive_to_inclusive_last_day(db_valid_to)
