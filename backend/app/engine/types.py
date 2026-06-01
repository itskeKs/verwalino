"""Frozen input/output dataclasses for the engine (pure, no ORM).

These mirror the `snapshot_payload.inputs` structure (m3-abrechnungs-engine.md
section 1.7).  The repository/service layer is responsible for loading ORM rows
and converting them into these frozen dataclasses; the engine never touches the
DB.  All money/quotas are Decimal.

Dates are tag-inclusive (ADR-0004): a period [period_from, period_to] includes
BOTH endpoints.  `period_to = None` means open-ended (e.g. an ongoing lease);
the engine clamps open-ended periods to the settlement period end.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal


# ---------------------------------------------------------------------------
# Controlled vocabularies (mirror the M2 cost_entry CHECK constraints).
# Kept here as plain constants so the engine has no DB dependency.
# ---------------------------------------------------------------------------
WEIGHTED_KEYS = frozenset(
    {"MEA", "WOHNFLAECHE", "BEHEIZTE_FLAECHE", "EINHEIT", "PERSONENZAHL", "VERBRAUCH"}
)
DIRECT_KEYS = frozenset({"EXTERNE_ABRECHNUNG", "MANUELLER_BETRAG"})
ALL_KEYS = WEIGHTED_KEYS | DIRECT_KEYS

# tenant_apportionable values whose effective umzulegender Betrag is the
# explicit apportionable_tenant_amount (NOT gross_amount). section 6.2.
APPORTIONABLE_AMOUNT_REQUIRED = frozenset(
    {"GEMISCHT_PRUEFEN", "TEILWEISE_PRUEFEN"}
)
# tenant_apportionable values handled via direct per-unit external amounts.
EXTERNAL_APPORTIONABLE = frozenset({"UMLAGEFAEHIG_EXTERN_PRUEFEN"})


@dataclass(frozen=True)
class UnitInput:
    """A unit participating in the property (frozen copy of `unit` row)."""

    id: uuid.UUID
    number: str
    usage_type: str  # WOHNUNG | GEWERBE | STELLPLATZ | SONSTIGE
    area: Decimal | None = None
    heated_area: Decimal | None = None
    mea: Decimal | None = None
    # Person count for the PERSONENZAHL key. PROVISIONAL (M3-A7, section 2.3):
    # taken per-unit (constant over the settlement period); the engine takes
    # it as input so the data-source decision does not touch the engine.
    person_count: int | None = None
    # Self-measured consumption for the VERBRAUCH key (already a difference of
    # meter readings; section 2.4). Engine takes the resulting quantity.
    consumption: Decimal | None = None


@dataclass(frozen=True)
class LeasePeriodInput:
    """A lease period (frozen copy of a `lease_agreement` row) for a unit.

    `valid_to = None` means open-ended (clamped to settlement period end).
    Tag-inclusive: valid_to is the last occupied day (section 3.4).
    """

    lease_agreement_id: uuid.UUID
    unit_id: uuid.UUID
    tenant_profile_id: uuid.UUID
    valid_from: date
    valid_to: date | None
    advance_payment: Decimal | None = None
    tenant_name: str = ""


@dataclass(frozen=True)
class OwnerPeriodInput:
    """An ownership period (frozen copy of `ownership_history`) for a unit.

    Used to attribute vacancy to the correct owner (section 3.3). Co-ownership
    (mea_share) is carried but irrelevant for the Mieter-BK addressee.
    """

    unit_id: uuid.UUID
    owner_profile_id: uuid.UUID
    valid_from: date
    valid_to: date | None
    mea_share: Decimal | None = None


@dataclass(frozen=True)
class ExternalAmountInput:
    """A direct per-(unit, occupancy period) tenant amount for EXTERNE_ABRECHNUNG.

    PROVISIONAL (O3-1, section 5.2): the external statement (Messdienst) already
    delivers a per-unit tenant share, split per occupancy period on tenant
    change. `period_from/_to` (inclusive) bind the value to one occupancy slice;
    if None, the value applies to the whole settlement period for that unit.
    """

    unit_id: uuid.UUID
    tenant_amount: Decimal
    period_from: date | None = None
    period_to: date | None = None


@dataclass(frozen=True)
class ManualAmountInput:
    """A direct per-unit amount for the MANUELLER_BETRAG key."""

    unit_id: uuid.UUID
    amount: Decimal


@dataclass(frozen=True)
class CostPositionInput:
    """A cost position to be distributed (frozen copy of an active cost_entry).

    Only ACTIVE + CONFIRMED entries whose service period overlaps the
    settlement period reach the engine (the repository filters; section 2.6
    re-validates defensively).
    """

    cost_entry_id: uuid.UUID
    category_id: uuid.UUID | None
    category_code: str
    label: str
    gross_amount: Decimal
    service_period_from: date
    service_period_to: date
    tenant_distribution_key: str | None
    tenant_apportionable: str
    apportionable_tenant_amount: Decimal | None = None
    # Per-unit direct amounts for EXTERNE_ABRECHNUNG / MANUELLER_BETRAG.
    external_amounts: tuple[ExternalAmountInput, ...] = ()
    manual_amounts: tuple[ManualAmountInput, ...] = ()


@dataclass(frozen=True)
class PropertyInput:
    id: uuid.UUID
    name: str
    management_type: str


@dataclass(frozen=True)
class SettlementInput:
    """The complete, frozen input for one BK_MIETER computation.

    This is everything the engine needs to compute the bill for ONE lease
    (one addressee) over one settlement period -- with the full property
    context so the per-position distribution over all participating units is
    consistent (sum over all units/tenants/vacancy == G).
    """

    period_start: date
    period_end: date
    property: PropertyInput
    # The lease (addressee) this run bills. Must be one of `lease_periods`.
    target_lease_agreement_id: uuid.UUID
    units: tuple[UnitInput, ...]
    lease_periods: tuple[LeasePeriodInput, ...]
    owner_periods: tuple[OwnerPeriodInput, ...]
    cost_positions: tuple[CostPositionInput, ...]
    advance_payments_total: Decimal
    advance_payments_source: str = "MANUAL"
    engine_version: str = "m3.1.0"


# ---------------------------------------------------------------------------
# Output (pure result -- mirrors settlement_line + computation trace).
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ComputationStep:
    """The full, auditable rechenweg of one cost position for the addressee."""

    cost_entry_id: uuid.UUID
    category_code: str
    distribution_key: str
    total_amount: Decimal  # gross / effective total of the position
    key_base_total: Decimal | None  # denominator (sum of weights)
    key_base_share: Decimal | None  # numerator (addressee's unit weight)
    time_share_days: int | None
    time_share_total_days: int | None
    exact_share: Decimal  # ungerounded addressee share (time x key)
    rounded_amount: Decimal  # final cent amount for the addressee
    note: str = ""


@dataclass(frozen=True)
class ResultLine:
    """A single output line (mirror of settlement_line). Money is Decimal."""

    line_type: str  # COST | ADVANCE_PAYMENT | BALANCE
    label: str
    amount: Decimal
    sort_order: int
    cost_entry_id: uuid.UUID | None = None
    category_id: uuid.UUID | None = None
    category_code: str | None = None
    distribution_key: str | None = None
    total_amount: Decimal | None = None
    distribution_base_total: Decimal | None = None
    distribution_base_share: Decimal | None = None
    time_share_days: int | None = None
    time_share_total_days: int | None = None
    note: str | None = None


@dataclass(frozen=True)
class EngineResult:
    """The pure computation result for one BK_MIETER run."""

    lines: tuple[ResultLine, ...]
    computation: tuple[ComputationStep, ...]
    total_cost: Decimal
    advance_payments_total: Decimal
    balance: Decimal  # total_cost - advance_payments_total
    # Vacancy attribution per position (so "no cent lost via vacancy" is
    # demonstrable; section 3.3). Maps cost_entry_id -> list of (owner, amount).
    vacancy_attribution: tuple["VacancyShare", ...] = ()


@dataclass(frozen=True)
class VacancyShare:
    """An amount attributed to an owner for vacancy days of a unit/position."""

    cost_entry_id: uuid.UUID
    unit_id: uuid.UUID
    owner_profile_id: uuid.UUID | None
    days: int
    amount: Decimal
