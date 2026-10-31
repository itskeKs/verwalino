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
# Engine-level errors (subclass ValueError; live here so the low-level period/
# distribution modules can raise them without importing settlement.py).
# ---------------------------------------------------------------------------
class PeriodOverlapError(ValueError):
    """Two lease periods for the same unit overlap inside the cover (section 2.6).

    This is a fail-loud data error: the M1 EXCLUDE constraint should prevent it,
    so it normally signals either a bypassed constraint or a read-edge convention
    mismatch (half-open [) DB bound not converted to the engine's tag-inclusive
    []). The engine escalates rather than mis-attribute days; the service maps it
    to a clean 4xx instead of a 500.
    """


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
    # Full-period occupant quantities; empty means day-based split within unit.
    consumption_periods: tuple[tuple[date, date, Decimal], ...] = ()


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
    heating: HeatingPositionInput | None = None


@dataclass(frozen=True)
class PropertyInput:
    id: uuid.UUID
    name: str
    management_type: str
    fiscal_year_start_month: int = 1


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
    allocation_inputs: tuple[SettlementInput, ...] = ()


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
    heating_costs: tuple[HeatingAllocationResult, ...] = ()
    period_results: tuple[EngineResult, ...] = ()
    period_inputs: tuple[SettlementInput, ...] = ()


@dataclass(frozen=True)
class VacancyShare:
    """An amount attributed to an owner for vacancy days of a unit/position."""

    cost_entry_id: uuid.UUID
    unit_id: uuid.UUID
    owner_profile_id: uuid.UUID | None
    days: int
    amount: Decimal


# ===========================================================================
# M4 (WEG) -- owner-side input/output dataclasses (m4-weg-abrechnung.md).
#
# DESIGN NOTE (m4 section 4.4): M4 is the *mirrored addressee focus* of M3, NOT
# a new distribution algorithm. It REUSES the central rounding (distribute_amount,
# ADR-0005), the day-exact time_factor (ADR-0004), the UnitInput/OwnerPeriodInput
# dataclasses and the canonical snapshot machinery. The only genuinely new pieces
# are (a) a second cover function over OWNER periods (build_owner_cover, no
# tenant/vacancy split, co-ownership via mea_share) and (b) a second orchestrator
# that sums per OWNER instead of picking one tenant cell, computes the
# Abrechnungsspitze (Ist - Soll, WEMoG, ADR-0007) and the reserve development.
# ===========================================================================

# A cost_group value (denormalised from cost_category) flagging the three
# WEG owner-side cost spheres (m4 section 2.3 / 3).
COST_GROUP_UMLAGEFAEHIG = "UMLAGEFAEHIG"
COST_GROUP_GEMISCHT = "GEMISCHT_PRUEFEN"
COST_GROUP_NICHT_UMLAGEFAEHIG = "NICHT_UMLAGEFAEHIG"
COST_GROUPS = frozenset(
    {COST_GROUP_UMLAGEFAEHIG, COST_GROUP_GEMISCHT, COST_GROUP_NICHT_UMLAGEFAEHIG}
)


@dataclass(frozen=True)
class WegCostPositionInput:
    """A WEG cost position to distribute over owners (m4 section 4).

    The owner sphere reads `owner_distribution_key` (NOT tenant_distribution_key)
    and distributes the `gross_amount` (the owner bears the full cost share by
    MEA; what a letting owner may pass on to a tenant is the separate M3 sphere
    -- m4 section 4.5, [F4-3]). A position whose category is `reserve_relevant`
    is treated as a RESERVE CONTRIBUTION (Vermoegensbildung), NOT as cost, and
    is kept out of OWNER_COST_TOTAL / the Abrechnungsspitze (m4 section 3).
    """

    cost_entry_id: uuid.UUID
    category_id: uuid.UUID | None
    category_code: str
    label: str
    gross_amount: Decimal
    service_period_from: date
    service_period_to: date
    owner_distribution_key: str | None
    # cost_group ∈ COST_GROUPS -- which owner cost sphere the position belongs to.
    cost_group: str
    # True only for ERHALTUNGSRUECKLAGE (reserve_relevant on cost_category):
    # then this position is a reserve contribution, not a consumed cost.
    reserve_relevant: bool = False
    # Per-unit direct amounts for EXTERNE_ABRECHNUNG / MANUELLER_BETRAG (owner
    # side mirrors the M3 mechanism; e.g. an externally metered position the WEG
    # still distributes to owners by the delivered per-unit amounts).
    external_amounts: tuple[ExternalAmountInput, ...] = ()
    manual_amounts: tuple[ManualAmountInput, ...] = ()


@dataclass(frozen=True)
class OwnerAdvanceInput:
    """A SOLL advance (Wirtschaftsplan-Vorschuss) of one owner for the period.

    The beschlussrelevante Soll side of the Abrechnungsspitze (m4 section 2.1,
    ADR-0007). Copied from the verknuepfter economic_plan into the immutable
    settlement snapshot (m4 section 1.5). Money exact to cent.
    """

    owner_profile_id: uuid.UUID
    plan_advance_total: Decimal


@dataclass(frozen=True)
class ReserveInput:
    """Inputs for the Erhaltungsruecklage development (m4 section 3.3).

    `opening_balance` and `withdrawals` are MANUALLY captured in M4 (no booking
    logic yet; [F4-4/F4-5]). The contributions come from the reserve_relevant
    cost positions (computed, not supplied here). The closing balance is derived
    and the development invariant (opening + contributions - withdrawals ==
    closing) is checked fail-loud by the engine.
    """

    opening_balance: Decimal = Decimal("0.00")
    # Total withdrawals from the reserve in the period (manual, m4 section 3.3).
    withdrawals_total: Decimal = Decimal("0.00")


@dataclass(frozen=True)
class WegSettlementInput:
    """The complete, frozen input for ONE objektweite WEG-Jahresabrechnung.

    One run per WEG (E4-4): the property-wide document from which the per-owner
    Einzelabrechnung is a view. Per-owner lines carry addressee_owner_profile_id.
    """

    period_start: date
    period_end: date
    property: PropertyInput
    units: tuple[UnitInput, ...]
    # Owner periods over the whole property (ownership_history). Tag-inclusive
    # (the read-edge already converted the [) DB bound, ADR-0006).
    owner_periods: tuple[OwnerPeriodInput, ...]
    cost_positions: tuple[WegCostPositionInput, ...]
    # SOLL advances per owner (from the economic_plan; m4 section 1.5).
    plan_advances: tuple[OwnerAdvanceInput, ...]
    reserve: ReserveInput = ReserveInput()
    # (nachrichtlich) actually-paid advances per owner -- MANUAL in M4, kept
    # SEPARATE from the beschlussrelevante Spitze (m4 section 2.1 / 2.4). Not
    # required; absent -> no payment-status line.
    paid_advances: tuple[OwnerAdvanceInput, ...] = ()
    economic_plan_id: uuid.UUID | None = None
    engine_version: str = "m4.1.0"


@dataclass(frozen=True)
class PlannedCostInput:
    """A planned cost position of a Wirtschaftsplan (m4 section 5.1)."""

    category_id: uuid.UUID | None
    category_code: str
    label: str
    planned_amount: Decimal
    distribution_key: str
    cost_group: str


@dataclass(frozen=True)
class WirtschaftsplanInput:
    """The complete, frozen input for ONE Wirtschaftsplan computation (m4 §5).

    Forward-looking; no Ist costs, no Soll-Ist. Planned costs per category +
    the planned reserve contribution -> per-owner annual advance (MEA) -> 12
    equal monthly Hausgeld rates (E4-3b, monatsbasiert; Largest-Remainder so
    sum of 12 rates == annual advance exactly).
    """

    plan_year: int
    period_start: date
    period_end: date
    property: PropertyInput
    units: tuple[UnitInput, ...]
    owner_periods: tuple[OwnerPeriodInput, ...]
    planned_costs: tuple[PlannedCostInput, ...]
    # Planned total reserve contribution of the year (Vermoegensbildung, NOT a
    # cost umlage; m4 section 3). Distributed by MEA like a planned cost but kept
    # in its own RESERVE_CONTRIBUTION subtotal.
    reserve_contribution_total: Decimal = Decimal("0.00")
    engine_version: str = "m4.1.0"


@dataclass(frozen=True)
class BankBalanceInput:
    """A manual Stichtagssaldo of one bank account (Vermoegensbericht, ADR-0008).

    A Verwalter-Angabe (manual), NOT an automatic booking saldo. Marked as such
    in the PDF (m4 section 6.3 / 8.3).
    """

    bank_account_id: uuid.UUID
    label: str
    account_type: str
    balance: Decimal


@dataclass(frozen=True)
class AssetItemInput:
    """A manual receivable/payable line of the Vermoegensbericht (ADR-0008).

    Forderung (RECEIVABLE) / Verbindlichkeit (PAYABLE). Manual Verwalter-Angabe.
    """

    label: str
    amount: Decimal


@dataclass(frozen=True)
class VermoegensberichtInput:
    """The complete, frozen input for ONE Vermoegensbericht (§ 28 IV WEG, m4 §6).

    Reserve development is automatic (opening + contributions - withdrawals ==
    closing, fail-loud); bank balances and receivables/payables are MANUAL
    Verwalter-Angaben (ADR-0008). Stichtag = period_end.
    """

    period_start: date
    period_end: date
    property: PropertyInput
    reserve: ReserveInput
    # Reserve contributions of the year (computed elsewhere or supplied as the
    # sum of reserve_relevant cost positions); kept explicit so the report is
    # reproducible without re-running the Jahresabrechnung.
    reserve_contributions_total: Decimal = Decimal("0.00")
    bank_balances: tuple[BankBalanceInput, ...] = ()
    receivables: tuple[AssetItemInput, ...] = ()
    payables: tuple[AssetItemInput, ...] = ()
    engine_version: str = "m4.1.0"


# ---------------------------------------------------------------------------
# M4 output dataclasses (mirror settlement_line / economic_plan_line).
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class OwnerCostStep:
    """The auditable rechenweg of one cost position for ONE owner (m4 §2.2).

    Mirrors M3's ComputationStep but is owner-addressed and tracks the cost
    sphere (cost_group) and whether the position is a reserve contribution.
    """

    cost_entry_id: uuid.UUID
    owner_profile_id: uuid.UUID
    category_code: str
    distribution_key: str
    cost_group: str
    is_reserve: bool
    total_amount: Decimal  # gross / effective total of the position
    key_base_total: Decimal | None  # denominator (sum of weights)
    key_base_share: Decimal | None  # numerator (owner's effective unit weight)
    time_share_days: int | None
    time_share_total_days: int | None
    exact_share: Decimal
    rounded_amount: Decimal
    note: str = ""


@dataclass(frozen=True)
class OwnerResult:
    """The full per-owner result block of a WEG-Jahresabrechnung (m4 §2.2).

    All money is Decimal, cent-exact. The three subtotals are the WEG-required
    getrennte Ausweisung umlagefaehig / nicht umlagefaehig / Ruecklage (§2.3).
    """

    owner_profile_id: uuid.UUID
    # Per (cost position) rounded share for this owner, in deterministic order.
    cost_lines: tuple[ResultLine, ...]
    subtotal_umlagefaehig: Decimal
    subtotal_nicht_umlagefaehig: Decimal
    subtotal_reserve: Decimal
    # OWNER_COST_TOTAL = umlagefaehig + nicht umlagefaehig (NOT reserve).
    owner_cost_total: Decimal
    # PLAN_ADVANCE (Soll) of this owner from the Wirtschaftsplan.
    plan_advance: Decimal
    # ABRECHNUNGSSPITZE = owner_cost_total - plan_advance (WEMoG, ADR-0007).
    abrechnungsspitze: Decimal
    # (nachrichtlich) actually-paid advances of this owner -- separate from the
    # Spitze (m4 section 2.1). None if not supplied.
    paid_advances: Decimal | None = None


@dataclass(frozen=True)
class ReserveDevelopment:
    """The Erhaltungsruecklage development block (m4 section 3.3).

    Invariant (checked fail-loud): closing == opening + contributions - withdrawals.
    """

    opening_balance: Decimal
    contributions_total: Decimal
    withdrawals_total: Decimal
    closing_balance: Decimal


@dataclass(frozen=True)
class WegResult:
    """The pure computation result for one objektweite WEG-Jahresabrechnung."""

    owners: tuple[OwnerResult, ...]
    computation: tuple[OwnerCostStep, ...]
    reserve: ReserveDevelopment
    # Property-wide total of the per-cost-type sums (umlagefaehig + nicht
    # umlagefaehig, WITHOUT reserve) -- the Σ-Ist that Σ owner_cost_total equals.
    cost_total: Decimal
    reserve_contributions_total: Decimal
    plan_advances_total: Decimal
    # Σ abrechnungsspitze over all owners (== cost_total - plan_advances_total).
    abrechnungsspitze_total: Decimal


@dataclass(frozen=True)
class PlanOwnerResult:
    """A per-owner result block of a Wirtschaftsplan (m4 section 5.2)."""

    owner_profile_id: uuid.UUID
    unit_id: uuid.UUID
    # annual advance share of this owner/unit (cost + reserve, by MEA).
    annual_advance: Decimal
    # 12 monthly rates (Largest-Remainder; sum == annual_advance exactly).
    monthly_rates: tuple[Decimal, ...]
    # the representative monthly amount shown (== monthly_rates[0]); the exact
    # 12 rates are in monthly_rates (some cents differ by +/-0.01 over the year).
    monthly_amount: Decimal
    distribution_base_share: Decimal | None
    distribution_base_total: Decimal | None


@dataclass(frozen=True)
class PlanResult:
    """The pure computation result for one Wirtschaftsplan (m4 section 5)."""

    # Planned cost lines per category (+ the reserve contribution line) with
    # their MEA distribution; deterministic order.
    planned_lines: tuple[ResultLine, ...]
    subtotal_umlagefaehig: Decimal
    subtotal_nicht_umlagefaehig: Decimal
    reserve_contribution_total: Decimal
    # total planned advance volume (Σ planned costs + reserve).
    total_advance_volume: Decimal
    owners: tuple[PlanOwnerResult, ...]


@dataclass(frozen=True)
class AssetReportLine:
    """One Bestandszeile of the Vermoegensbericht (m4 section 6.5)."""

    line_type: str  # RESERVE_OPENING | RESERVE_CONTRIBUTION | RESERVE_WITHDRAWAL
    #                 | RESERVE_CLOSING | BANK_BALANCE | RECEIVABLE | PAYABLE
    label: str
    amount: Decimal
    sort_order: int
    # True for manual Verwalter-Angaben (bank balances, receivables, payables) --
    # the PDF marks these as such (ADR-0008, m4 section 6.4 / 8.3).
    is_manual: bool = False
    note: str | None = None


@dataclass(frozen=True)
class AssetReportResult:
    """The pure computation result for one Vermoegensbericht (m4 section 6)."""

    reserve: ReserveDevelopment
    lines: tuple[AssetReportLine, ...]
    bank_balance_total: Decimal
    receivables_total: Decimal
    payables_total: Decimal


# T45: already separated heating/hot-water pools; no meter estimation or ORM.
@dataclass(frozen=True)
class HeatingUnitInput:
    unit_id: uuid.UUID
    base_quantity: Decimal
    consumption: Decimal | None


@dataclass(frozen=True)
class HeatingCostPoolInput:
    """A §7 heating or §8 hot-water pool, delimited upstream under §9.

    base_key describes the supplied quantities (AREA, HEATED_AREA, VOLUME).
    consumption_recorded=False explicitly selects a non-consumption bill, not
    an assumed estimate when just one reading is missing. Building conditions
    for §7's mandatory 70% rule must be assessed upstream, not inferred here.
    """
    kind: str  # HEATING | HOT_WATER
    total_amount: Decimal
    consumption_fraction: Decimal
    base_key: str
    consumption_recorded: bool
    mandatory_seventy_percent: bool
    units: tuple[HeatingUnitInput, ...]


@dataclass(frozen=True)
class HeatingUnitResult:
    unit_id: uuid.UUID
    base_quantity: Decimal
    consumption: Decimal | None
    base_amount: Decimal
    consumption_amount: Decimal
    gross_amount: Decimal
    reduction_amount: Decimal
    amount_due: Decimal
    reduction_reason: str | None


@dataclass(frozen=True)
class HeatingPoolResult:
    kind: str
    base_key: str
    consumption_fraction: Decimal
    consumption_recorded: bool
    method: str  # CONSUMPTION | NON_CONSUMPTION
    total_amount: Decimal
    base_cost_total: Decimal
    consumption_cost_total: Decimal
    reduction_total: Decimal
    amount_due: Decimal
    rows: tuple[HeatingUnitResult, ...]


@dataclass(frozen=True)
class HeatingCostResult:
    pools: tuple[HeatingPoolResult, ...]
    gross_total: Decimal
    reduction_total: Decimal
    amount_due: Decimal


@dataclass(frozen=True)
class HeatingSliceInput:
    id: uuid.UUID
    unit_id: uuid.UUID
    period_from: date
    period_to: date
    lease_agreement_id: uuid.UUID | None
    owner_profile_id: uuid.UUID | None
    base_quantity: Decimal
    consumption: Decimal | None


@dataclass(frozen=True)
class HeatingPositionInput:
    kind: str
    consumption_fraction: Decimal
    base_key: str
    consumption_recorded: bool
    mandatory_seventy_percent: bool
    slices: tuple[HeatingSliceInput, ...]


@dataclass(frozen=True)
class HeatingAllocationResult:
    cost_entry_id: uuid.UUID
    pool: HeatingPoolResult
    slices: tuple[HeatingSliceInput, ...]
    statement_amounts: tuple[tuple[uuid.UUID, date, date, Decimal], ...] = ()
