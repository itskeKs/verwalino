"""M9 Fristen engine -- PURE functions for deadline/obligation monitoring.

HARD RULE (same as the rest of app/engine, SDD section 16):
  This module contains ONLY pure computation. It MUST NOT import from
  app.models, app.repositories, app.services, app.api, sqlalchemy, fastapi,
  or perform any DB / HTTP / file I/O. Everything in and out is date, int and
  frozen dataclasses. This is what makes the deadline arithmetic testable in
  isolation and keeps the legal date rules in one reviewable place.

Scope (SDD section 12, roadmap M9):
  1. BK-Abrechnung (section 556 III BGB): deadline = period_end + 12 months
     (HARD, statutory). An obligation exists for a MIET/MISCH property + fiscal
     year that has confirmed costs and at least one tenant lease but NO
     finalized BK_MIETER settlement covering it.
  2. WEG-Jahresabrechnung + Vermoegensbericht: deadline per WEG year
     PROVISIONAL [section 18/fachlich] -- WEMoG/section 28 does not fix a hard
     calendar date the way 556 III does; "promptly after year end" is the norm.
     We use a provisional offset (default: end of the following year).
  3. Wirtschaftsplan for the COMING year: due per WEG; PROVISIONAL due date.
  4. Overdue open items (count, from M6) -- nachrichtlich.
  5. Beleg-Aufbewahrung (retention_until) -- nachrichtlich (informational).

Output: a flat list of DeadlineItem (kind, reference, due_date, status). The
service layer assembles the inputs from ORM rows and maps the result to the
read-only API. Read-only, RBAC, tenant-scoped -- enforced by the service/API,
NOT here (the engine has no notion of tenant or role).

NO money in this module; everything is dates/counts. Date discipline: dates
only (datetime.date), calendar arithmetic, no timezone.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date

# ---------------------------------------------------------------------------
# Deadline kinds (controlled vocabulary, stable string keys for the API/UI).
# ---------------------------------------------------------------------------
KIND_BK_ABRECHNUNG = "BK_ABRECHNUNG"          # section 556 III BGB (HARD)
KIND_WEG_JAHRESABRECHNUNG = "WEG_JAHRESABRECHNUNG"  # [section 18/fachlich]
KIND_WEG_VERMOEGENSBERICHT = "WEG_VERMOEGENSBERICHT"  # [section 18/fachlich]
KIND_WIRTSCHAFTSPLAN = "WIRTSCHAFTSPLAN"      # coming year, [section 18/fachlich]
KIND_OFFENE_POSTEN = "OFFENE_POSTEN"          # overdue items (nachrichtlich)
KIND_BELEG_AUFBEWAHRUNG = "BELEG_AUFBEWAHRUNG"  # retention (nachrichtlich)

# ---------------------------------------------------------------------------
# Status vocabulary.
# ---------------------------------------------------------------------------
STATUS_OK = "OK"            # done / no action (e.g. a finalized settlement exists)
STATUS_UPCOMING = "UPCOMING"  # due in the future, within the look-ahead window
STATUS_DUE = "DUE"          # due now / very soon (within DUE_SOON_DAYS of deadline)
STATUS_OVERDUE = "OVERDUE"  # deadline passed without fulfilment
STATUS_INFO = "INFO"        # purely informational (retention, open-item count)

# ---------------------------------------------------------------------------
# [section 18/fachlich] Provisional rule parameters -- must be validated by a
# legal expert before productive use. Centralised here, overridable per call.
# ---------------------------------------------------------------------------

# How many days before the deadline an item flips from UPCOMING to DUE.
DUE_SOON_DAYS: int = 90

# How far into the future we surface UPCOMING items (a Fristenmonitor should not
# list a deadline that is years away). Items further out are omitted.
LOOK_AHEAD_DAYS: int = 365

# [section 18/fachlich] PROVISIONAL: months after the WEG fiscal-year end by
# which the Jahresabrechnung / Vermoegensbericht is expected. The WEMoG sets no
# hard date; a common practice is "within the following year". 12 months is a
# provisional, clearly-marked default and is NOT a statutory deadline.
WEG_ABRECHNUNG_MONTHS_AFTER_YEAR_END: int = 12

# [section 18/fachlich] PROVISIONAL: the Wirtschaftsplan for the coming year is
# typically resolved before / at the start of that year. We mark a plan for
# (reference_year + 1) as due; the provisional deadline is the start of that
# coming year.
# (No extra parameter needed; see upcoming_wirtschaftsplaene.)


def _add_months(d: date, months: int) -> date:
    """Add `months` calendar months to a date, clamping the day if needed.

    Pure calendar arithmetic (no dateutil). E.g. period_end 2024-12-31 + 12
    months -> 2025-12-31; 2024-01-31 + 1 month -> 2024-02-29 (clamped).
    """
    total = (d.year * 12 + (d.month - 1)) + months
    year, month = divmod(total, 12)
    month += 1
    # clamp day to the last valid day of the target month
    if month == 12:
        next_month_first = date(year + 1, 1, 1)
    else:
        next_month_first = date(year, month + 1, 1)
    last_day = date.fromordinal(next_month_first.toordinal() - 1).day
    return date(year, month, min(d.day, last_day))


def _status_for(deadline: date, reference_date: date) -> str:
    """Classify a deadline relative to the reference date (PROVISIONAL thresholds).

    OVERDUE  : deadline < reference_date (passed).
    DUE      : 0 <= (deadline - reference_date) <= DUE_SOON_DAYS.
    UPCOMING : DUE_SOON_DAYS < (deadline - reference_date) <= LOOK_AHEAD_DAYS.
    (further out -> caller omits the item.)
    """
    delta_days = (deadline - reference_date).days
    if delta_days < 0:
        return STATUS_OVERDUE
    if delta_days <= DUE_SOON_DAYS:
        return STATUS_DUE
    return STATUS_UPCOMING


# ===========================================================================
# Inputs (frozen copies; the service maps ORM rows to these at the read-edge).
# ===========================================================================
@dataclass(frozen=True)
class FiscalYearObligationInput:
    """One (property, fiscal year) for which an abrechnungs obligation MAY exist.

    The service derives the candidate fiscal years from the service periods of
    the confirmed cost entries of a property (grouped by the property's
    fiscal_year_start_month) and supplies the period bounds it computed. The
    engine does NOT re-derive periods from costs -- it takes the bounds and the
    booleans and classifies. period_start/period_end are tag-inclusive
    (last day of the fiscal year).

    [section 18/fachlich] The exact period boundaries of a settlement year
    (calendar vs. fiscal, how a stray cost outside the year is handled) follow
    the property convention; the precise legal mapping is a M3/M4 + fachlich
    question. The engine only needs the bounds the service computed.
    """

    property_id: uuid.UUID
    property_name: str
    management_type: str  # MIET | WEG | SEV | MISCH
    fiscal_year_label: str  # e.g. "2024" or "2024/2025" (display)
    period_start: date
    period_end: date  # tag-inclusive last day of the fiscal year
    has_confirmed_costs: bool
    has_tenant_lease: bool  # at least one lease overlapping the period (BK relevance)
    has_finalized_bk: bool  # a finalized BK_MIETER run covers this period
    has_finalized_weg_abrechnung: bool  # finalized WEG_JAHRESABRECHNUNG
    has_finalized_vermoegensbericht: bool  # finalized VERMOEGENSBERICHT


@dataclass(frozen=True)
class WirtschaftsplanObligationInput:
    """A WEG property + the plan_year it should have a Wirtschaftsplan for.

    The service supplies (property, coming_year, has_finalized_plan). The coming
    year is reference_date.year + 1 (the plan is forward-looking).
    """

    property_id: uuid.UUID
    property_name: str
    plan_year: int
    has_finalized_plan: bool


@dataclass(frozen=True)
class OverdueItemsInput:
    """Nachrichtlich: the count of overdue open items (from the M6 derivation).

    The service runs the M6 account_statement + find_overdue_items per
    lease/owner and supplies the aggregate count + total. The engine only
    surfaces it as an INFO/DUE item (it does not recompute Mahnwesen).
    """

    overdue_item_count: int


@dataclass(frozen=True)
class RetentionAnchorInput:
    """Nachrichtlich: the earliest upcoming retention expiry of a document group.

    Aggregated by the service (e.g. the minimum retention_until over all
    invoice_documents / settlement_runs of the org that is still in the future,
    plus the count). Surfaced as INFO. NEVER an action item -- retention
    expiry only ENABLES a later DSGVO purge (ADR-0012), it does not demand one.
    """

    label: str
    earliest_retention_until: date | None
    document_count: int


# ===========================================================================
# Output.
# ===========================================================================
@dataclass(frozen=True)
class DeadlineItem:
    """One row of the Fristenmonitor (read-only).

    `kind`       -- one of the KIND_* constants.
    `reference`  -- human-readable subject (property name + period / year).
    `due_date`   -- the (possibly provisional) deadline; None for pure INFO.
    `status`     -- one of the STATUS_* constants.
    `is_statutory` -- True only for the HARD section 556 III BK deadline.
    `is_provisional` -- True where the deadline rule is [section 18/fachlich].
    `detail`     -- optional extra context (counts, notes).
    """

    kind: str
    reference: str
    due_date: date | None
    status: str
    is_statutory: bool = False
    is_provisional: bool = False
    property_id: uuid.UUID | None = None
    detail: str = ""


# ===========================================================================
# BK-Abrechnung (section 556 III BGB) -- HARD deadline = period_end + 12 months.
# ===========================================================================
def bk_deadline(period_end: date) -> date:
    """The statutory BK abrechnungs deadline: end of period + 12 months.

    section 556 Abs. 3 BGB: the landlord must deliver the operating-cost
    statement within 12 months of the end of the settlement period. This is the
    ONE hard, non-provisional date rule in this module.
    """
    return _add_months(period_end, 12)


def evaluate_bk_obligations(
    obligations: list[FiscalYearObligationInput],
    reference_date: date,
) -> list[DeadlineItem]:
    """BK obligations for MIET/MISCH properties (section 556 III, HARD).

    An obligation is RELEVANT iff the (property, fiscal year) has confirmed
    costs AND at least one tenant lease overlapping the period (a pure-WEG year
    with no tenant has no BK statement). If a finalized BK_MIETER run already
    covers it -> STATUS_OK (done). Otherwise the deadline = period_end + 12
    months and the status follows _status_for. UPCOMING items beyond the
    look-ahead window are omitted.
    """
    items: list[DeadlineItem] = []
    for ob in obligations:
        if ob.management_type not in ("MIET", "MISCH"):
            continue
        if not ob.has_confirmed_costs or not ob.has_tenant_lease:
            continue
        deadline = bk_deadline(ob.period_end)
        if ob.has_finalized_bk:
            items.append(
                DeadlineItem(
                    kind=KIND_BK_ABRECHNUNG,
                    reference=f"{ob.property_name} -- BK {ob.fiscal_year_label}",
                    due_date=deadline,
                    status=STATUS_OK,
                    is_statutory=True,
                    property_id=ob.property_id,
                    detail="Finalisierte BK-Abrechnung vorhanden.",
                )
            )
            continue
        status = _status_for(deadline, reference_date)
        if status == STATUS_UPCOMING and (deadline - reference_date).days > LOOK_AHEAD_DAYS:
            continue
        items.append(
            DeadlineItem(
                kind=KIND_BK_ABRECHNUNG,
                reference=f"{ob.property_name} -- BK {ob.fiscal_year_label}",
                due_date=deadline,
                status=status,
                is_statutory=True,
                property_id=ob.property_id,
                detail="Frist = Abrechnungszeitraum-Ende + 12 Monate (section 556 III BGB).",
            )
        )
    return items


# ===========================================================================
# WEG-Jahresabrechnung + Vermoegensbericht -- PROVISIONAL [section 18/fachlich].
# ===========================================================================
def weg_abrechnung_deadline(period_end: date) -> date:
    """[section 18/fachlich] PROVISIONAL WEG abrechnungs deadline.

    period_end + WEG_ABRECHNUNG_MONTHS_AFTER_YEAR_END months. NOT statutory:
    the WEMoG sets no hard calendar date for the Jahresabrechnung. Provisional,
    clearly marked, must be confirmed by a legal expert.
    """
    return _add_months(period_end, WEG_ABRECHNUNG_MONTHS_AFTER_YEAR_END)


def evaluate_weg_obligations(
    obligations: list[FiscalYearObligationInput],
    reference_date: date,
) -> list[DeadlineItem]:
    """WEG Jahresabrechnung + Vermoegensbericht per WEG fiscal year (PROVISIONAL).

    For WEG/MISCH properties with confirmed costs: a Jahresabrechnung and a
    Vermoegensbericht (section 28 IV WEG) are each expected per year. Both are
    marked is_provisional=True. A finalized run of the respective type ->
    STATUS_OK; otherwise the provisional deadline + _status_for. Two separate
    DeadlineItems (Abrechnung, Vermoegensbericht) so each can be tracked.
    """
    items: list[DeadlineItem] = []
    for ob in obligations:
        if ob.management_type not in ("WEG", "MISCH"):
            continue
        if not ob.has_confirmed_costs:
            continue
        deadline = weg_abrechnung_deadline(ob.period_end)

        # Jahresabrechnung
        items.extend(
            _weg_item(
                ob,
                deadline,
                reference_date,
                kind=KIND_WEG_JAHRESABRECHNUNG,
                done=ob.has_finalized_weg_abrechnung,
                subject="Jahresabrechnung",
                done_detail="Finalisierte WEG-Jahresabrechnung vorhanden.",
                open_detail=(
                    "[section 18/fachlich] PROVISORISCHE Frist (WEMoG setzt keine "
                    "harte Kalenderfrist); fachlich zu bestaetigen."
                ),
            )
        )
        # Vermoegensbericht (section 28 IV WEG)
        items.extend(
            _weg_item(
                ob,
                deadline,
                reference_date,
                kind=KIND_WEG_VERMOEGENSBERICHT,
                done=ob.has_finalized_vermoegensbericht,
                subject="Vermoegensbericht (section 28 IV WEG)",
                done_detail="Finalisierter Vermoegensbericht vorhanden.",
                open_detail=(
                    "[section 18/fachlich] PROVISORISCHE Frist; fachlich zu bestaetigen."
                ),
            )
        )
    return items


def _weg_item(
    ob: FiscalYearObligationInput,
    deadline: date,
    reference_date: date,
    *,
    kind: str,
    done: bool,
    subject: str,
    done_detail: str,
    open_detail: str,
) -> list[DeadlineItem]:
    if done:
        return [
            DeadlineItem(
                kind=kind,
                reference=f"{ob.property_name} -- {subject} {ob.fiscal_year_label}",
                due_date=deadline,
                status=STATUS_OK,
                is_provisional=True,
                property_id=ob.property_id,
                detail=done_detail,
            )
        ]
    status = _status_for(deadline, reference_date)
    if status == STATUS_UPCOMING and (deadline - reference_date).days > LOOK_AHEAD_DAYS:
        return []
    return [
        DeadlineItem(
            kind=kind,
            reference=f"{ob.property_name} -- {subject} {ob.fiscal_year_label}",
            due_date=deadline,
            status=status,
            is_provisional=True,
            property_id=ob.property_id,
            detail=open_detail,
        )
    ]


# ===========================================================================
# Wirtschaftsplan for the coming year -- PROVISIONAL [section 18/fachlich].
# ===========================================================================
def evaluate_wirtschaftsplan_obligations(
    obligations: list[WirtschaftsplanObligationInput],
    reference_date: date,
) -> list[DeadlineItem]:
    """A Wirtschaftsplan is due for the COMING year for each WEG (PROVISIONAL).

    Provisional deadline = the start of the plan year (Jan 1 of plan_year): the
    plan should ideally be resolved before the year it covers begins. If a
    finalized plan for that year exists -> STATUS_OK; otherwise _status_for.
    is_provisional=True (no statutory calendar date).
    """
    items: list[DeadlineItem] = []
    for ob in obligations:
        deadline = date(ob.plan_year, 1, 1)
        if ob.has_finalized_plan:
            status = STATUS_OK
            detail = "Finalisierter Wirtschaftsplan vorhanden."
        else:
            status = _status_for(deadline, reference_date)
            if status == STATUS_UPCOMING and (deadline - reference_date).days > LOOK_AHEAD_DAYS:
                continue
            detail = (
                "[section 18/fachlich] PROVISORISCHE Frist (Plan fuer das kommende "
                "Jahr); fachlich zu bestaetigen."
            )
        items.append(
            DeadlineItem(
                kind=KIND_WIRTSCHAFTSPLAN,
                reference=f"{ob.property_name} -- Wirtschaftsplan {ob.plan_year}",
                due_date=deadline,
                status=status,
                is_provisional=True,
                property_id=ob.property_id,
                detail=detail,
            )
        )
    return items


# ===========================================================================
# Overdue open items + retention -- nachrichtlich.
# ===========================================================================
def evaluate_overdue_items(overdue: OverdueItemsInput) -> list[DeadlineItem]:
    """Surface the overdue-open-item count as a single DUE/INFO item (M6/M7).

    > 0 overdue items -> STATUS_DUE (action: consider Mahnwesen). 0 -> STATUS_OK.
    No deadline date (it is a running aggregate, not a single Frist).
    """
    if overdue.overdue_item_count <= 0:
        return [
            DeadlineItem(
                kind=KIND_OFFENE_POSTEN,
                reference="Ueberfaellige offene Posten",
                due_date=None,
                status=STATUS_OK,
                detail="Keine ueberfaelligen offenen Posten.",
            )
        ]
    return [
        DeadlineItem(
            kind=KIND_OFFENE_POSTEN,
            reference="Ueberfaellige offene Posten",
            due_date=None,
            status=STATUS_DUE,
            detail=f"{overdue.overdue_item_count} ueberfaellige(r) Posten (Mahnwesen pruefen).",
        )
    ]


def evaluate_retention_anchors(
    anchors: list[RetentionAnchorInput],
    reference_date: date,
) -> list[DeadlineItem]:
    """Surface upcoming Beleg-Aufbewahrungs-Abläufe as INFO items (nachrichtlich).

    Retention expiry is INFORMATIONAL only: it ENABLES a later DSGVO purge
    (ADR-0012), never demands an action. Always STATUS_INFO. An anchor with no
    upcoming expiry (all retention in the past or no documents) is still listed
    as INFO with a neutral note.
    """
    items: list[DeadlineItem] = []
    for a in anchors:
        if a.earliest_retention_until is None or a.document_count == 0:
            detail = f"{a.document_count} Beleg(e); keine anstehende Aufbewahrungsfrist."
        else:
            detail = (
                f"{a.document_count} Beleg(e); fruehester Aufbewahrungsablauf "
                f"{a.earliest_retention_until.isoformat()}."
            )
        items.append(
            DeadlineItem(
                kind=KIND_BELEG_AUFBEWAHRUNG,
                reference=a.label,
                due_date=a.earliest_retention_until,
                status=STATUS_INFO,
                detail=detail,
            )
        )
    return items


# ===========================================================================
# Top-level assembly + deterministic ordering.
# ===========================================================================
# Sort priority of a status in the monitor (most urgent first).
_STATUS_RANK = {
    STATUS_OVERDUE: 0,
    STATUS_DUE: 1,
    STATUS_UPCOMING: 2,
    STATUS_INFO: 3,
    STATUS_OK: 4,
}


def build_fristenmonitor(
    fiscal_obligations: list[FiscalYearObligationInput],
    wirtschaftsplan_obligations: list[WirtschaftsplanObligationInput],
    overdue: OverdueItemsInput,
    retention_anchors: list[RetentionAnchorInput],
    reference_date: date,
) -> list[DeadlineItem]:
    """Assemble the full Fristenmonitor from all input groups (deterministic).

    Order: by status urgency (OVERDUE -> DUE -> UPCOMING -> INFO -> OK), then by
    due_date (None last), then by kind, then by reference -- so the same inputs
    always produce the same monitor (prueffest, testable).
    """
    items: list[DeadlineItem] = []
    items += evaluate_bk_obligations(fiscal_obligations, reference_date)
    items += evaluate_weg_obligations(fiscal_obligations, reference_date)
    items += evaluate_wirtschaftsplan_obligations(
        wirtschaftsplan_obligations, reference_date
    )
    items += evaluate_overdue_items(overdue)
    items += evaluate_retention_anchors(retention_anchors, reference_date)

    def _key(it: DeadlineItem):
        return (
            _STATUS_RANK.get(it.status, 99),
            it.due_date or date.max,
            it.kind,
            it.reference,
        )

    return sorted(items, key=_key)
