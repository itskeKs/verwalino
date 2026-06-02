"""M9 DSGVO engine -- PURE reconciliation: restrict-vs-anonymize + retention.

HARD RULE (same as the rest of app/engine, SDD section 16):
  This module contains ONLY pure computation. It MUST NOT import from
  app.models, app.repositories, app.services, app.api, sqlalchemy, fastapi,
  or perform any DB / HTTP / file I/O. Everything in and out is date, str and
  frozen dataclasses. This is what makes the legal restrict/anonymize decision
  testable in isolation (Golden / reconciliation tests).

What this module decides (ADR-0012):
  Given a person's retention BINDINGS (references that are themselves under
  statutory retention) and a reference date, decide:
    * RESTRICT      -- the person is retention-bound: keep the data under
                       processing restriction (Art. 18 DSGVO), do NOT anonymize;
                       record the governing retention_until (= max over bindings).
                       Anonymization is only permissible AFTER that date (purge).
    * ANONYMIZE_NOW -- the person has no binding: anonymize immediately
                       (neutralize PII, keep id + structural FKs).

  And it provides the anonymized PII field values (neutral placeholders).

The reconciliation INVARIANT (ADR-0012 section 6): retention-bound documents
are NEVER altered/deleted -- the retention is the legal basis against the
erasure claim. This module never touches such documents; the SERVICE only ever
mutates `person` fields. Anonymization happens ONLY where there is no binding,
so finalized settlement/dunning PDFs (which resolve names at render time)
stay correct for bound persons until retention expiry.

[section 18/fachlich] Provisional, legally-unconfirmed parts are marked.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date

# ---------------------------------------------------------------------------
# Decision vocabulary.
# ---------------------------------------------------------------------------
DECISION_RESTRICT = "RESTRICT"
DECISION_ANONYMIZE_NOW = "ANONYMIZE_NOW"

# ---------------------------------------------------------------------------
# Binding kinds (which retention source bound the person). Stable keys for
# audit/export; mirror ADR-0012 section 1.
# ---------------------------------------------------------------------------
BINDING_COST_ENTRY_VENDOR = "COST_ENTRY_VENDOR"          # vendor of a confirmed cost_entry
BINDING_SETTLEMENT_ADDRESSEE = "SETTLEMENT_ADDRESSEE"    # addressee of a finalized run
BINDING_FINALIZED_LEASE = "FINALIZED_LEASE"              # lease in a finalized BK run
BINDING_FINALIZED_OWNERSHIP = "FINALIZED_OWNERSHIP"      # ownership in a finalized WEG run
BINDING_DUNNING = "DUNNING"                              # addressee of an issued dunning

ALL_BINDING_KINDS = frozenset({
    BINDING_COST_ENTRY_VENDOR,
    BINDING_SETTLEMENT_ADDRESSEE,
    BINDING_FINALIZED_LEASE,
    BINDING_FINALIZED_OWNERSHIP,
    BINDING_DUNNING,
})

# ---------------------------------------------------------------------------
# Anonymization placeholders (ADR-0012 section 4). Deterministic + idempotent.
# [section 18/fachlich] Exact anonymization depth (whether free-text/audit
# payloads must also be neutralised) is a legal question; this module defines
# the `person`-field placeholders only.
# ---------------------------------------------------------------------------
ANON_LAST_NAME = "Anonymisiert"
ANON_TEXT = ""  # neutral empty for first_name / company_name / address parts


def retention_from_year_end(reference_day: date, years: int = 8) -> date:
    """[section 18/fachlich] PROVISIONAL retention end: end of the reference

    day's calendar year + `years`. This is the SAME provisional rule as
    m2-gobd-belegfluss.md section 7.3 (the statutory frist begins with the end
    of the calendar year). Used to derive retention for bound records that lack
    an explicit retention_until column (settlement_run, dunning_notice,
    cost_entry without an attached document). NOT a legal fact -- must be
    confirmed by StB/Fachanwalt.
    """
    return date(reference_day.year + years, 12, 31)


# ===========================================================================
# Inputs (frozen copies; the service builds these from the DB).
# ===========================================================================
@dataclass(frozen=True)
class RetentionBinding:
    """One reference that ties a person to a record under statutory retention.

    `kind`           -- one of the BINDING_* constants.
    `entity_type`    -- the bound entity's table (for audit/export, e.g.
                        "cost_entry", "settlement_run", "dunning_notice").
    `entity_id`      -- the bound record's id.
    `retention_until`-- the governing retention date for THIS binding. The
                        service supplies it (from the record's retention_until
                        column where present, else via retention_from_year_end).
                        None means "retention applies but no date computed" ->
                        treated as not-yet-expired (conservative: bind).
    """

    kind: str
    entity_type: str
    entity_id: uuid.UUID
    retention_until: date | None


@dataclass(frozen=True)
class PersonPii:
    """The current PII field values of a person (frozen copy for anonymization).

    Mirrors the mutable PII columns of `person`. The engine returns the
    anonymized counterpart (AnonymizedPii); the service applies it to the ORM
    row. id / organization_id / structural FKs are NOT here -- they are
    preserved untouched (ADR-0012 section 4).
    """

    first_name: str
    last_name: str
    company_name: str | None
    email: str | None
    phone: str | None
    street: str
    zip_code: str
    city: str
    iban: str | None
    bic: str | None
    bank_name: str | None
    already_anonymized: bool = False  # anonymized_at IS NOT NULL


# ===========================================================================
# Outputs.
# ===========================================================================
@dataclass(frozen=True)
class DeletionDecision:
    """The reconciliation result for one deletion request (ADR-0012 section 3).

    `decision`        -- DECISION_RESTRICT or DECISION_ANONYMIZE_NOW.
    `is_bound`        -- True iff at least one retention binding exists.
    `governing_retention_until` -- max over the bindings' retention_until
                         (None if unbound, or if bound but no date computable).
    `bindings`        -- the bindings considered (for audit/transparency).
    `purgeable_now`   -- True iff bound BUT all binding retention dates have
                         expired as of reference_date (then anonymization is
                         permissible after all -> the service may purge).
    """

    decision: str
    is_bound: bool
    governing_retention_until: date | None
    bindings: tuple[RetentionBinding, ...]
    purgeable_now: bool


@dataclass(frozen=True)
class AnonymizedPii:
    """The neutralized PII values to write back (ADR-0012 section 4)."""

    first_name: str
    last_name: str
    company_name: str | None
    email: str | None
    phone: str | None
    street: str
    zip_code: str
    city: str
    iban: str | None
    bic: str | None
    bank_name: str | None


# ===========================================================================
# Core reconciliation.
# ===========================================================================
def governing_retention_until(
    bindings: list[RetentionBinding],
) -> date | None:
    """The MAX retention_until over all bindings (None if none computable).

    The person may be anonymized only once EVERY binding's retention has
    expired; the binding that expires LAST therefore governs -> the max. A
    binding with retention_until=None contributes None (conservative: the
    overall max-with-None handling treats an unknown date as "not yet expired",
    see decide_deletion).
    """
    dates = [b.retention_until for b in bindings if b.retention_until is not None]
    if not dates:
        return None
    return max(dates)


def _all_expired(bindings: list[RetentionBinding], reference_date: date) -> bool:
    """True iff EVERY binding has a retention_until strictly in the past.

    A binding with retention_until=None is treated as NOT expired (conservative:
    we do not purge while a retention date is unknown). An empty binding list is
    vacuously "all expired" but decide_deletion handles unbound separately.
    """
    if not bindings:
        return True
    for b in bindings:
        if b.retention_until is None:
            return False
        if not (b.retention_until < reference_date):
            return False
    return True


def decide_deletion(
    bindings: list[RetentionBinding],
    reference_date: date,
) -> DeletionDecision:
    """Decide RESTRICT vs ANONYMIZE_NOW for a deletion request (ADR-0012 sec. 3).

    - No bindings -> ANONYMIZE_NOW (the person is not retention-bound).
    - Bindings, at least one not expired -> RESTRICT (keep under restriction,
      record governing retention_until; do NOT anonymize).
    - Bindings, ALL expired -> ANONYMIZE_NOW with purgeable_now=True (the
      retention basis has lapsed, so the erasure claim prevails again).

    The bindings tuple is sorted deterministically (kind, entity_type,
    entity_id) so the decision is reproducible.
    """
    ordered = tuple(
        sorted(bindings, key=lambda b: (b.kind, b.entity_type, str(b.entity_id)))
    )
    if not ordered:
        return DeletionDecision(
            decision=DECISION_ANONYMIZE_NOW,
            is_bound=False,
            governing_retention_until=None,
            bindings=(),
            purgeable_now=False,
        )

    gov = governing_retention_until(list(ordered))
    if _all_expired(list(ordered), reference_date):
        return DeletionDecision(
            decision=DECISION_ANONYMIZE_NOW,
            is_bound=True,
            governing_retention_until=gov,
            bindings=ordered,
            purgeable_now=True,
        )
    return DeletionDecision(
        decision=DECISION_RESTRICT,
        is_bound=True,
        governing_retention_until=gov,
        bindings=ordered,
        purgeable_now=False,
    )


def anonymized_values(pii: PersonPii) -> AnonymizedPii:
    """Neutralize PII fields (ADR-0012 section 4). Idempotent.

    Replaces names/contact/bank fields with neutral placeholders; the structural
    id/FKs are preserved by the caller (not present here). Calling it on an
    already-anonymized person returns the same neutral values (idempotent).
    """
    return AnonymizedPii(
        first_name=ANON_TEXT,
        last_name=ANON_LAST_NAME,
        company_name=None,
        email=None,
        phone=None,
        street=ANON_TEXT,
        zip_code=ANON_TEXT,
        city=ANON_TEXT,
        iban=None,
        bic=None,
        bank_name=None,
    )


def is_anonymized(pii: AnonymizedPii | PersonPii) -> bool:
    """True if the given PII values are already neutralized to placeholders.

    Used to assert idempotency in tests and to short-circuit a re-anonymization.
    """
    return (
        pii.first_name == ANON_TEXT
        and pii.last_name == ANON_LAST_NAME
        and pii.company_name is None
        and pii.email is None
        and pii.phone is None
        and pii.street == ANON_TEXT
        and pii.zip_code == ANON_TEXT
        and pii.city == ANON_TEXT
        and pii.iban is None
        and pii.bic is None
        and pii.bank_name is None
    )
