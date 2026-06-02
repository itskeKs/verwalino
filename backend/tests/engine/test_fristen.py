"""Pure engine tests for the M9 Fristen module (app.engine.fristen).

No DB, no I/O -- tests the pure deadline arithmetic and classification against
hand-computed expected values. Covers:
  - bk_deadline = period_end + 12 months (HARD, section 556 III BGB).
  - status classification (OVERDUE / DUE / UPCOMING) around thresholds.
  - BK obligation relevance (MIET/MISCH + costs + tenant; finalized -> OK).
  - WEG Jahresabrechnung + Vermoegensbericht (provisional, two items).
  - Wirtschaftsplan for the coming year.
  - overdue items + retention anchors (nachrichtlich).
  - deterministic ordering of the assembled monitor.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest

from app.engine import fristen
from app.engine.fristen import (
    DUE_SOON_DAYS,
    KIND_BELEG_AUFBEWAHRUNG,
    KIND_BK_ABRECHNUNG,
    KIND_OFFENE_POSTEN,
    KIND_WEG_JAHRESABRECHNUNG,
    KIND_WEG_VERMOEGENSBERICHT,
    KIND_WIRTSCHAFTSPLAN,
    STATUS_DUE,
    STATUS_INFO,
    STATUS_OK,
    STATUS_OVERDUE,
    STATUS_UPCOMING,
    FiscalYearObligationInput,
    OverdueItemsInput,
    RetentionAnchorInput,
    WirtschaftsplanObligationInput,
    bk_deadline,
    build_fristenmonitor,
    evaluate_bk_obligations,
    evaluate_overdue_items,
    evaluate_retention_anchors,
    evaluate_weg_obligations,
    evaluate_wirtschaftsplan_obligations,
)


# ---------------------------------------------------------------------------
# Date arithmetic
# ---------------------------------------------------------------------------
class TestDeadlineArithmetic:
    def test_bk_deadline_is_period_end_plus_12_months(self):
        # Calendar year 2024 -> deadline end of 2025 (section 556 III BGB).
        assert bk_deadline(date(2024, 12, 31)) == date(2025, 12, 31)

    def test_bk_deadline_mid_year_period(self):
        # Fiscal year ending 30.06.2024 -> 30.06.2025.
        assert bk_deadline(date(2024, 6, 30)) == date(2025, 6, 30)

    def test_add_months_clamps_leap_day(self):
        # 31.01 + 1 month -> 29.02 in a leap year (clamped, no overflow).
        assert fristen._add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)
        # 31.01 + 1 month -> 28.02 in a non-leap year.
        assert fristen._add_months(date(2023, 1, 31), 1) == date(2023, 2, 28)

    def test_add_months_year_rollover(self):
        assert fristen._add_months(date(2024, 11, 30), 3) == date(2025, 2, 28)


# ---------------------------------------------------------------------------
# Status classification
# ---------------------------------------------------------------------------
class TestStatusClassification:
    def test_overdue_when_deadline_passed(self):
        assert fristen._status_for(date(2025, 1, 1), date(2025, 6, 1)) == STATUS_OVERDUE

    def test_due_within_threshold(self):
        # deadline exactly DUE_SOON_DAYS away -> DUE (inclusive).
        ref = date(2025, 1, 1)
        deadline = date.fromordinal(ref.toordinal() + DUE_SOON_DAYS)
        assert fristen._status_for(deadline, ref) == STATUS_DUE

    def test_due_today(self):
        assert fristen._status_for(date(2025, 6, 1), date(2025, 6, 1)) == STATUS_DUE

    def test_upcoming_beyond_threshold(self):
        ref = date(2025, 1, 1)
        deadline = date.fromordinal(ref.toordinal() + DUE_SOON_DAYS + 1)
        assert fristen._status_for(deadline, ref) == STATUS_UPCOMING


# ---------------------------------------------------------------------------
# BK obligations (section 556 III)
# ---------------------------------------------------------------------------
def _bk_ob(**kw) -> FiscalYearObligationInput:
    base = dict(
        property_id=uuid.uuid4(),
        property_name="Bahnhofstr 12",
        management_type="MIET",
        fiscal_year_label="2024",
        period_start=date(2024, 1, 1),
        period_end=date(2024, 12, 31),
        has_confirmed_costs=True,
        has_tenant_lease=True,
        has_finalized_bk=False,
        has_finalized_weg_abrechnung=False,
        has_finalized_vermoegensbericht=False,
    )
    base.update(kw)
    return FiscalYearObligationInput(**base)


class TestBkObligations:
    def test_overdue_bk_obligation(self):
        # Period 2024 -> deadline 2025-12-31; reference 2026-06-02 -> OVERDUE.
        items = evaluate_bk_obligations([_bk_ob()], date(2026, 6, 2))
        assert len(items) == 1
        it = items[0]
        assert it.kind == KIND_BK_ABRECHNUNG
        assert it.due_date == date(2025, 12, 31)
        assert it.status == STATUS_OVERDUE
        assert it.is_statutory is True
        assert it.is_provisional is False

    def test_finalized_bk_is_ok(self):
        items = evaluate_bk_obligations(
            [_bk_ob(has_finalized_bk=True)], date(2026, 6, 2)
        )
        assert len(items) == 1
        assert items[0].status == STATUS_OK

    def test_no_obligation_without_costs(self):
        assert evaluate_bk_obligations(
            [_bk_ob(has_confirmed_costs=False)], date(2026, 6, 2)
        ) == []

    def test_no_obligation_without_tenant(self):
        # A pure-WEG year with no tenant lease has no BK statement.
        assert evaluate_bk_obligations(
            [_bk_ob(has_tenant_lease=False)], date(2026, 6, 2)
        ) == []

    def test_pure_weg_property_not_bk(self):
        assert evaluate_bk_obligations(
            [_bk_ob(management_type="WEG")], date(2026, 6, 2)
        ) == []

    def test_misch_property_is_bk_relevant(self):
        items = evaluate_bk_obligations(
            [_bk_ob(management_type="MISCH")], date(2026, 6, 2)
        )
        assert len(items) == 1
        assert items[0].kind == KIND_BK_ABRECHNUNG

    def test_far_future_upcoming_omitted(self):
        # period 2030 -> deadline 2031; reference 2026 -> > LOOK_AHEAD -> omitted.
        ob = _bk_ob(
            fiscal_year_label="2030",
            period_start=date(2030, 1, 1),
            period_end=date(2030, 12, 31),
        )
        assert evaluate_bk_obligations([ob], date(2026, 6, 2)) == []


# ---------------------------------------------------------------------------
# WEG obligations (provisional)
# ---------------------------------------------------------------------------
class TestWegObligations:
    def test_two_items_per_weg_year(self):
        ob = _bk_ob(management_type="WEG")
        items = evaluate_weg_obligations([ob], date(2026, 6, 2))
        kinds = {it.kind for it in items}
        assert kinds == {KIND_WEG_JAHRESABRECHNUNG, KIND_WEG_VERMOEGENSBERICHT}
        # Both provisional, neither statutory.
        assert all(it.is_provisional and not it.is_statutory for it in items)

    def test_finalized_both_ok(self):
        ob = _bk_ob(
            management_type="WEG",
            has_finalized_weg_abrechnung=True,
            has_finalized_vermoegensbericht=True,
        )
        items = evaluate_weg_obligations([ob], date(2026, 6, 2))
        assert all(it.status == STATUS_OK for it in items)

    def test_partial_finalized(self):
        # Abrechnung done, Vermoegensbericht missing -> one OK, one open.
        ob = _bk_ob(
            management_type="WEG",
            has_finalized_weg_abrechnung=True,
            has_finalized_vermoegensbericht=False,
        )
        items = evaluate_weg_obligations([ob], date(2026, 6, 2))
        by_kind = {it.kind: it for it in items}
        assert by_kind[KIND_WEG_JAHRESABRECHNUNG].status == STATUS_OK
        assert by_kind[KIND_WEG_VERMOEGENSBERICHT].status == STATUS_OVERDUE

    def test_miet_property_not_weg(self):
        assert evaluate_weg_obligations([_bk_ob(management_type="MIET")], date(2026, 6, 2)) == []


# ---------------------------------------------------------------------------
# Wirtschaftsplan (coming year)
# ---------------------------------------------------------------------------
class TestWirtschaftsplan:
    def _wp(self, year: int, finalized: bool) -> WirtschaftsplanObligationInput:
        return WirtschaftsplanObligationInput(
            property_id=uuid.uuid4(),
            property_name="Hauptstr 5",
            plan_year=year,
            has_finalized_plan=finalized,
        )

    def test_coming_year_plan_due(self):
        # plan year 2027, reference mid-2026 -> deadline 2027-01-01 -> within
        # look-ahead, > DUE_SOON_DAYS -> UPCOMING (or DUE near year end).
        items = evaluate_wirtschaftsplan_obligations(
            [self._wp(2027, False)], date(2026, 6, 2)
        )
        assert len(items) == 1
        assert items[0].kind == KIND_WIRTSCHAFTSPLAN
        assert items[0].is_provisional is True
        assert items[0].status in (STATUS_UPCOMING, STATUS_DUE)

    def test_finalized_plan_ok(self):
        items = evaluate_wirtschaftsplan_obligations(
            [self._wp(2027, True)], date(2026, 6, 2)
        )
        assert items[0].status == STATUS_OK

    def test_missing_past_plan_overdue(self):
        # plan year 2025, reference 2026 -> deadline 2025-01-01 passed -> OVERDUE.
        items = evaluate_wirtschaftsplan_obligations(
            [self._wp(2025, False)], date(2026, 6, 2)
        )
        assert items[0].status == STATUS_OVERDUE


# ---------------------------------------------------------------------------
# Overdue items + retention (nachrichtlich)
# ---------------------------------------------------------------------------
class TestNachrichtlich:
    def test_overdue_items_due(self):
        items = evaluate_overdue_items(OverdueItemsInput(overdue_item_count=3))
        assert items[0].kind == KIND_OFFENE_POSTEN
        assert items[0].status == STATUS_DUE
        assert "3" in items[0].detail

    def test_no_overdue_items_ok(self):
        items = evaluate_overdue_items(OverdueItemsInput(overdue_item_count=0))
        assert items[0].status == STATUS_OK

    def test_retention_anchor_is_info(self):
        anchors = [
            RetentionAnchorInput(
                label="Belege (invoice_document)",
                earliest_retention_until=date(2033, 12, 31),
                document_count=5,
            )
        ]
        items = evaluate_retention_anchors(anchors, date(2026, 6, 2))
        assert items[0].kind == KIND_BELEG_AUFBEWAHRUNG
        assert items[0].status == STATUS_INFO
        assert "2033-12-31" in items[0].detail

    def test_retention_anchor_no_documents(self):
        anchors = [
            RetentionAnchorInput(
                label="Belege", earliest_retention_until=None, document_count=0
            )
        ]
        items = evaluate_retention_anchors(anchors, date(2026, 6, 2))
        assert items[0].status == STATUS_INFO


# ---------------------------------------------------------------------------
# Full monitor assembly + ordering
# ---------------------------------------------------------------------------
class TestBuildMonitor:
    def test_ordering_overdue_first(self):
        overdue_ob = _bk_ob()  # 2024 -> overdue at 2026
        upcoming_wp = WirtschaftsplanObligationInput(
            property_id=uuid.uuid4(),
            property_name="Hauptstr 5",
            plan_year=2027,
            has_finalized_plan=False,
        )
        monitor = build_fristenmonitor(
            fiscal_obligations=[overdue_ob],
            wirtschaftsplan_obligations=[upcoming_wp],
            overdue=OverdueItemsInput(overdue_item_count=2),
            retention_anchors=[
                RetentionAnchorInput("Belege", date(2033, 12, 31), 5)
            ],
            reference_date=date(2026, 6, 2),
        )
        # First item must be the most urgent (OVERDUE).
        assert monitor[0].status == STATUS_OVERDUE
        # INFO/OK items rank after action items.
        statuses = [it.status for it in monitor]
        assert statuses == sorted(
            statuses, key=lambda s: fristen._STATUS_RANK[s]
        )

    def test_deterministic(self):
        ob = _bk_ob()
        args = dict(
            fiscal_obligations=[ob],
            wirtschaftsplan_obligations=[],
            overdue=OverdueItemsInput(overdue_item_count=0),
            retention_anchors=[],
            reference_date=date(2026, 6, 2),
        )
        m1 = build_fristenmonitor(**args)
        m2 = build_fristenmonitor(**args)
        assert [(i.kind, i.reference, i.status) for i in m1] == [
            (i.kind, i.reference, i.status) for i in m2
        ]
