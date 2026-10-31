"""Unit tests for day-exact period arithmetic + cover building (ADR-0004 / 3.4)."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.engine.periods import (
    KIND_TENANT,
    KIND_VACANCY,
    build_unit_cover,
    day_count,
    overlap_days,
    time_factor,
)
from app.engine.types import LeasePeriodInput, OwnerPeriodInput, PeriodOverlapError


class TestDayCount:
    def test_inclusive_single_day(self):
        assert day_count(date(2025, 1, 1), date(2025, 1, 1)) == 1

    def test_full_year_2025(self):
        assert day_count(date(2025, 1, 1), date(2025, 12, 31)) == 365

    def test_leap_year_2024(self):
        assert day_count(date(2024, 1, 1), date(2024, 12, 31)) == 366

    def test_to_before_from_is_zero(self):
        assert day_count(date(2025, 6, 1), date(2025, 5, 1)) == 0


class TestOverlapDays:
    def test_full_overlap(self):
        assert overlap_days(
            date(2025, 1, 1), date(2025, 12, 31), date(2025, 1, 1), date(2025, 12, 31)
        ) == 365

    def test_partial_overlap(self):
        # Jan-Jun vs full year -> 181 days (2025 non-leap).
        assert overlap_days(
            date(2025, 1, 1), date(2025, 12, 31), date(2025, 1, 1), date(2025, 6, 30)
        ) == 181

    def test_no_overlap(self):
        assert overlap_days(
            date(2025, 1, 1), date(2025, 3, 31), date(2025, 4, 1), date(2025, 6, 30)
        ) == 0


class TestTimeFactor:
    def test_full_year_factor_one(self):
        assert time_factor(
            date(2025, 1, 1), date(2025, 12, 31), date(2025, 1, 1), date(2025, 12, 31)
        ) == Decimal(1)

    def test_half_year_exact_fraction(self):
        f = time_factor(
            date(2025, 1, 1), date(2025, 12, 31), date(2025, 1, 1), date(2025, 6, 30)
        )
        assert f == Decimal(181) / Decimal(365)


class TestTenantChangeBoundary:
    """section 3.4: Auszug 30.06. / Einzug 01.07. -> no gap, no overlap."""

    def test_day_exact_change_no_vacancy(self):
        unit = uuid.uuid4()
        owner = uuid.uuid4()
        leases = [
            LeasePeriodInput(
                lease_agreement_id=uuid.uuid4(),
                unit_id=unit,
                tenant_profile_id=uuid.uuid4(),
                valid_from=date(2025, 1, 1),
                valid_to=date(2025, 6, 30),
            ),
            LeasePeriodInput(
                lease_agreement_id=uuid.uuid4(),
                unit_id=unit,
                tenant_profile_id=uuid.uuid4(),
                valid_from=date(2025, 7, 1),
                valid_to=date(2025, 12, 31),
            ),
        ]
        owners = [
            OwnerPeriodInput(
                unit_id=unit, owner_profile_id=owner, valid_from=date(2000, 1, 1), valid_to=None
            )
        ]
        cover = build_unit_cover(unit, date(2025, 1, 1), date(2025, 12, 31), leases, owners)
        # Exactly two tenant slices, no vacancy.
        kinds = [c.kind for c in cover]
        assert kinds == [KIND_TENANT, KIND_TENANT]
        assert cover[0].days == 181
        assert cover[1].days == 184
        assert cover[0].days + cover[1].days == 365


class TestVacancyCover:
    """section 3.3: gap between leases is vacancy attributed to the owner."""

    def test_vacancy_between_tenants(self):
        unit = uuid.uuid4()
        owner = uuid.uuid4()
        # A: Jan-Mar (90 days), vacancy Apr-May (61 days), B: Jun-Dec (214 days).
        leases = [
            LeasePeriodInput(
                lease_agreement_id=uuid.uuid4(),
                unit_id=unit,
                tenant_profile_id=uuid.uuid4(),
                valid_from=date(2025, 1, 1),
                valid_to=date(2025, 3, 31),
            ),
            LeasePeriodInput(
                lease_agreement_id=uuid.uuid4(),
                unit_id=unit,
                tenant_profile_id=uuid.uuid4(),
                valid_from=date(2025, 6, 1),
                valid_to=date(2025, 12, 31),
            ),
        ]
        owners = [
            OwnerPeriodInput(
                unit_id=unit, owner_profile_id=owner, valid_from=date(2000, 1, 1), valid_to=None
            )
        ]
        cover = build_unit_cover(unit, date(2025, 1, 1), date(2025, 12, 31), leases, owners)
        assert [c.kind for c in cover] == [KIND_TENANT, KIND_VACANCY, KIND_TENANT]
        assert cover[0].days == 90
        assert cover[1].days == 61
        assert cover[1].owner_profile_id == owner
        assert cover[2].days == 214
        assert sum(c.days for c in cover) == 365

    def test_leading_and_trailing_vacancy(self):
        unit = uuid.uuid4()
        owner = uuid.uuid4()
        # Only a mid-year lease; before and after is vacancy.
        leases = [
            LeasePeriodInput(
                lease_agreement_id=uuid.uuid4(),
                unit_id=unit,
                tenant_profile_id=uuid.uuid4(),
                valid_from=date(2025, 4, 1),
                valid_to=date(2025, 9, 30),
            ),
        ]
        owners = [
            OwnerPeriodInput(
                unit_id=unit, owner_profile_id=owner, valid_from=date(2000, 1, 1), valid_to=None
            )
        ]
        cover = build_unit_cover(unit, date(2025, 1, 1), date(2025, 12, 31), leases, owners)
        assert [c.kind for c in cover] == [KIND_VACANCY, KIND_TENANT, KIND_VACANCY]
        assert sum(c.days for c in cover) == 365

    def test_open_ended_lease_clamped_to_period_end(self):
        unit = uuid.uuid4()
        owner = uuid.uuid4()
        leases = [
            LeasePeriodInput(
                lease_agreement_id=uuid.uuid4(),
                unit_id=unit,
                tenant_profile_id=uuid.uuid4(),
                valid_from=date(2024, 1, 1),
                valid_to=None,  # open-ended
            ),
        ]
        owners = [
            OwnerPeriodInput(
                unit_id=unit, owner_profile_id=owner, valid_from=date(2000, 1, 1), valid_to=None
            )
        ]
        cover = build_unit_cover(unit, date(2025, 1, 1), date(2025, 12, 31), leases, owners)
        assert [c.kind for c in cover] == [KIND_TENANT]
        assert cover[0].days == 365
        assert cover[0].start == date(2025, 1, 1)
        assert cover[0].end == date(2025, 12, 31)


class TestOverlapDetection:
    def test_overlapping_leases_raise(self):
        unit = uuid.uuid4()
        owner = uuid.uuid4()
        leases = [
            LeasePeriodInput(
                lease_agreement_id=uuid.uuid4(),
                unit_id=unit,
                tenant_profile_id=uuid.uuid4(),
                valid_from=date(2025, 1, 1),
                valid_to=date(2025, 7, 31),
            ),
            LeasePeriodInput(
                lease_agreement_id=uuid.uuid4(),
                unit_id=unit,
                tenant_profile_id=uuid.uuid4(),
                valid_from=date(2025, 7, 1),  # overlaps previous (31.07 vs 01.07)
                valid_to=date(2025, 12, 31),
            ),
        ]
        owners = [
            OwnerPeriodInput(
                unit_id=unit, owner_profile_id=owner, valid_from=date(2000, 1, 1), valid_to=None
            )
        ]
        with pytest.raises(ValueError, match="Overlapping lease periods"):
            build_unit_cover(unit, date(2025, 1, 1), date(2025, 12, 31), leases, owners)

    def test_overlap_raises_period_overlap_error_subclass(self):
        """Secondary hardening: a genuine overlap raises PeriodOverlapError.

        PeriodOverlapError is a ValueError subclass and is in the service's
        _compute_engine catch tuple, so a real (EXCLUDE-bypassing) overlap surfaces
        as a clean 422, not a 500. Adjacent back-to-back periods (Auszug 30.06. /
        Einzug 01.07. in engine convention) do NOT raise.
        """
        unit = uuid.uuid4()
        owner = uuid.uuid4()
        owners = [
            OwnerPeriodInput(
                unit_id=unit, owner_profile_id=owner, valid_from=date(2000, 1, 1), valid_to=None
            )
        ]
        overlapping = [
            LeasePeriodInput(
                lease_agreement_id=uuid.uuid4(), unit_id=unit, tenant_profile_id=uuid.uuid4(),
                valid_from=date(2025, 1, 1), valid_to=date(2025, 7, 31),
            ),
            LeasePeriodInput(
                lease_agreement_id=uuid.uuid4(), unit_id=unit, tenant_profile_id=uuid.uuid4(),
                valid_from=date(2025, 7, 1), valid_to=date(2025, 12, 31),
            ),
        ]
        with pytest.raises(PeriodOverlapError):
            build_unit_cover(unit, date(2025, 1, 1), date(2025, 12, 31), overlapping, owners)
        assert issubclass(PeriodOverlapError, ValueError)

        # Engine-convention back-to-back (last day 30.06., next from 01.07.) is fine.
        adjacent = [
            LeasePeriodInput(
                lease_agreement_id=uuid.uuid4(), unit_id=unit, tenant_profile_id=uuid.uuid4(),
                valid_from=date(2025, 1, 1), valid_to=date(2025, 6, 30),
            ),
            LeasePeriodInput(
                lease_agreement_id=uuid.uuid4(), unit_id=unit, tenant_profile_id=uuid.uuid4(),
                valid_from=date(2025, 7, 1), valid_to=date(2025, 12, 31),
            ),
        ]
        cover = build_unit_cover(unit, date(2025, 1, 1), date(2025, 12, 31), adjacent, owners)
        assert [c.kind for c in cover] == [KIND_TENANT, KIND_TENANT]


class TestPropertyPeriods:
    @pytest.mark.parametrize("end", [date(2025, 2, 27), date(2025, 2, 28)])
    def test_leap_day_start_accepts_february_end_next_year(self, end):
        from app.engine.periods import validate_settlement_period

        validate_settlement_period(date(2024, 2, 29), end)

    def test_leap_day_start_rejects_march_next_year(self):
        from app.engine.periods import validate_settlement_period

        with pytest.raises(ValueError, match="zwölf Monate"):
            validate_settlement_period(date(2024, 2, 29), date(2025, 3, 1))

    @pytest.mark.parametrize("month", range(1, 13))
    def test_every_start_month_has_inclusive_twelve_month_bounds(self, month):
        from app.engine.periods import period_for_date, period_starting_in, period_label

        start, end = period_starting_in(month, 2025)
        assert start == date(2025, month, 1)
        assert end == date.fromordinal(date(2026, month, 1).toordinal() - 1)
        assert period_for_date(month, start) == (start, end)
        assert period_for_date(month, end) == (start, end)
        assert period_label(start, end) == ("2025" if month == 1 else "2025/2026")

    def test_february_contains_leap_day_and_wraps_in_january(self):
        from app.engine.periods import period_for_date

        expected = (date(2024, 2, 1), date(2025, 1, 31))
        assert period_for_date(2, date(2024, 2, 29)) == expected
        assert period_for_date(2, date(2025, 1, 1)) == expected

    def test_march_period_ends_on_leap_day(self):
        from app.engine.periods import period_starting_in

        assert period_starting_in(3, 2023) == (date(2023, 3, 1), date(2024, 2, 29))

    def test_december_and_january_belong_to_the_same_period(self):
        from app.engine.periods import period_for_date

        expected = (date(2025, 12, 1), date(2026, 11, 30))
        assert period_for_date(12, date(2025, 12, 31)) == expected
        assert period_for_date(12, date(2026, 1, 1)) == expected
