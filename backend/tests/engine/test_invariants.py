"""Property-based invariants for the M3 engine (section 9.2, I-A..I-G).

These are the objective, model-independent measuring stick: they hold for ANY
valid input, not just the hand-picked Golden scenarios.  Hypothesis generates
adversarial inputs.

Pure-function tests -- no DB, no server.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from hypothesis import given, settings, strategies as st

from app.engine.canonical import canonical_json, canonical_sha256
from app.engine.distribution import distribute_position
from app.engine.periods import (
    KIND_TENANT,
    KIND_VACANCY,
    build_unit_cover,
    day_count,
    overlap_days,
    time_factor,
)
from app.engine.rounding import CENT, DistributionError, distribute_amount
from app.engine.settlement import compute_bk_settlement
from app.engine.snapshot import build_snapshot_payload
from app.engine.types import (
    CostPositionInput,
    LeasePeriodInput,
    OwnerPeriodInput,
    PropertyInput,
    SettlementInput,
    UnitInput,
)


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------
def money_amounts(min_eur=-10000, max_eur=10000):
    """Cent-exact Decimal amounts in [min_eur, max_eur]."""
    return st.integers(min_value=min_eur * 100, max_value=max_eur * 100).map(
        lambda c: (Decimal(c) / 100).quantize(CENT)
    )


def positive_weights(min_n=1, max_n=50):
    """A non-empty list of non-negative weights with sum > 0."""
    weight = st.integers(min_value=0, max_value=10_000_000).map(
        lambda x: Decimal(x) / Decimal(1000)
    )
    return st.lists(weight, min_size=min_n, max_size=max_n).filter(
        lambda ws: sum(ws) > 0
    )


# ===========================================================================
# I-A: sum of shares == total cost  (the most fundamental invariant, ADR-0005)
# ===========================================================================
class TestIA_SumEqualsTotal:
    @settings(max_examples=400)
    @given(total=money_amounts(), weights=positive_weights())
    def test_sum_of_shares_equals_total_exactly(self, total, weights):
        shares = distribute_amount(total, weights)
        assert sum(shares) == total
        assert len(shares) == len(weights)

    @settings(max_examples=200)
    @given(total=money_amounts(), weights=positive_weights())
    def test_each_share_is_cent_quantized(self, total, weights):
        for s in distribute_amount(total, weights):
            assert s == s.quantize(CENT)


# ===========================================================================
# I-B: no cent lost / sub-cent bounded -- each share within 1 cent of floor;
#      number of extra cents handed out == k.
# ===========================================================================
class TestIB_SubCentBounded:
    @settings(max_examples=400)
    @given(total=money_amounts(0, 10000), weights=positive_weights())
    def test_each_share_within_one_cent_of_exact_floor(self, total, weights):
        from decimal import ROUND_FLOOR

        weight_sum = sum(weights)
        shares = distribute_amount(total, weights)
        extra = 0
        for w, s in zip(weights, shares):
            exact = (total * w) / weight_sum
            floor = exact.quantize(CENT, rounding=ROUND_FLOOR)
            diff = s - floor
            # Each share is its floor or floor + 1 cent.
            assert diff in (Decimal("0.00"), CENT), (w, exact, floor, s)
            if diff == CENT:
                extra += 1
        # The handed-out extra cents equal the whole-cent remainder.
        floor_sum = sum(
            ((total * w) / weight_sum).quantize(CENT, rounding=ROUND_FLOOR)
            for w in weights
        )
        k = int(((total - floor_sum) / CENT).quantize(Decimal(1)))
        assert extra == k


# ===========================================================================
# I-C: time factors sum to 1 over a gap-free, overlap-free cover (ADR-0004)
# ===========================================================================
class TestIC_TimeFactorsSumToOne:
    """ADR-0004 #4: time factors of a gap-free, overlap-free cover sum to 1.

    The EXACT statement is the integer day partition: the sum of per-slice
    overlap days equals the service-period total days (so the factors, each
    = days/total, sum to total/total = 1 in rational arithmetic).  We assert
    that exact integer identity, and that the factor formed from the SUMMED
    days equals 1 -- this is the faithful, precision-independent form (the
    engine never sums raw Decimal factors to make money; it rounds the final
    cent amounts via Largest-Remainder, which is exact).
    """

    @settings(max_examples=300)
    @given(
        start_ord=st.integers(
            min_value=date(2020, 1, 1).toordinal(),
            max_value=date(2030, 1, 1).toordinal(),
        ),
        length=st.integers(min_value=1, max_value=800),
        cut_fracs=st.lists(
            st.floats(min_value=0.0, max_value=1.0), min_size=0, max_size=8
        ),
    )
    def test_partition_days_sum_to_total(self, start_ord, length, cut_fracs):
        sp_from = date.fromordinal(start_ord)
        sp_to = date.fromordinal(start_ord + length - 1)
        total_days = day_count(sp_from, sp_to)

        # Build an ordered set of cut days strictly inside the period.
        cuts = sorted(
            {
                start_ord + max(1, min(length - 1, int(f * length)))
                for f in cut_fracs
            }
        )
        boundaries = [start_ord] + cuts + [start_ord + length]
        day_sum = 0
        prev = boundaries[0]
        for nxt in boundaries[1:]:
            if nxt <= prev:
                continue
            p_from = date.fromordinal(prev)
            p_to = date.fromordinal(nxt - 1)  # tag-inclusive slice end
            ov = overlap_days(sp_from, sp_to, p_from, p_to)
            # Each non-empty slice contributes a positive time factor in (0, 1].
            f = time_factor(sp_from, sp_to, p_from, p_to)
            assert f == Decimal(ov) / Decimal(total_days)
            day_sum += ov
            prev = nxt
        # EXACT integer partition: slice days sum to the service-period total.
        assert day_sum == total_days
        # Hence the factor of the summed days is exactly 1.
        assert (Decimal(day_sum) / Decimal(total_days)) == Decimal(1)


# ===========================================================================
# I-D: vacancy -> owner / completeness; sum over tenant+vacancy == G per pos
# ===========================================================================
class TestID_VacancyToOwnerCompleteness:
    @settings(max_examples=120, deadline=None)
    @given(
        n_units=st.integers(min_value=1, max_value=4),
        total_eur=st.integers(min_value=1, max_value=50000),
        n_cuts=st.integers(min_value=0, max_value=3),
        leave_vacancy=st.booleans(),
        key=st.sampled_from(["WOHNFLAECHE", "MEA", "EINHEIT"]),
    )
    def test_full_distribution_equals_position_total(
        self, n_units, total_eur, n_cuts, leave_vacancy, key
    ):
        period_start = date(2025, 1, 1)
        period_end = date(2025, 12, 31)
        prop_id = uuid.uuid4()

        units = []
        leases = []
        owners = []
        for i in range(n_units):
            uid = uuid.uuid4()
            units.append(
                UnitInput(
                    id=uid,
                    number=str(i + 1),
                    usage_type="WOHNUNG",
                    area=Decimal(50 + 10 * i),
                    mea=Decimal(100 + 5 * i),
                )
            )
            owners.append(
                OwnerPeriodInput(
                    unit_id=uid,
                    owner_profile_id=uuid.uuid4(),
                    valid_from=date(2000, 1, 1),
                    valid_to=None,
                )
            )
            # Build 1..n tenant slices for this unit, optionally leaving a gap.
            cut_days = sorted(
                {
                    period_start.toordinal() + (j + 1) * 80
                    for j in range(n_cuts)
                    if (j + 1) * 80 < 360
                }
            )
            seg_start = period_start
            boundaries = cut_days + [period_end.toordinal() + 1]
            first = True
            for b in boundaries:
                seg_end = date.fromordinal(b - 1)
                if seg_end < seg_start:
                    seg_start = date.fromordinal(b)
                    continue
                # Optionally leave the second segment vacant.
                if leave_vacancy and not first and b != boundaries[-1]:
                    seg_start = date.fromordinal(b)
                    first = False
                    continue
                leases.append(
                    LeasePeriodInput(
                        lease_agreement_id=uuid.uuid4(),
                        unit_id=uid,
                        tenant_profile_id=uuid.uuid4(),
                        valid_from=seg_start,
                        valid_to=seg_end,
                    )
                )
                seg_start = date.fromordinal(b)
                first = False

        pos = CostPositionInput(
            cost_entry_id=uuid.uuid4(),
            category_id=uuid.uuid4(),
            category_code="GRUNDSTEUER",
            label="Grundsteuer",
            gross_amount=Decimal(total_eur).quantize(CENT),
            service_period_from=period_start,
            service_period_to=period_end,
            tenant_distribution_key=key,
            tenant_apportionable="UMLAGEFAEHIG",
        )

        covers = {
            u.id: build_unit_cover(u.id, period_start, period_end, leases, owners)
            for u in units
        }
        cells = distribute_position(pos, units, covers)
        # Sum over ALL cells (tenant + vacancy, all units) == position total.
        assert sum((c.amount for c in cells), Decimal("0.00")) == pos.gross_amount
        # Every vacancy cell is attributed to an owner (here always present).
        for c in cells:
            if c.cover.kind == KIND_VACANCY:
                assert c.cover.owner_profile_id is not None


# ===========================================================================
# I-E: determinism -- identical inputs -> identical canonical payload + hash
# ===========================================================================
class TestIE_Determinism:
    @settings(max_examples=60, deadline=None)
    @given(
        total_eur=st.integers(min_value=1, max_value=20000),
        advance_eur=st.integers(min_value=0, max_value=20000),
    )
    def test_same_input_same_hash(self, total_eur, advance_eur):
        inp = _two_tenant_input(total_eur, advance_eur)
        r1 = compute_bk_settlement(inp)
        r2 = compute_bk_settlement(inp)
        p1 = build_snapshot_payload(inp, r1)
        p2 = build_snapshot_payload(inp, r2)
        assert canonical_json(p1) == canonical_json(p2)
        assert canonical_sha256(p1) == canonical_sha256(p2)


# ===========================================================================
# I-F: NICHT_UMLAGEFAEHIG never appears in a Mieter-BK
# ===========================================================================
class TestIF_NonApportionableExcluded:
    @settings(max_examples=60, deadline=None)
    @given(total_eur=st.integers(min_value=1, max_value=20000))
    def test_non_apportionable_position_never_in_lines(self, total_eur):
        inp = _two_tenant_input(total_eur, 0, extra_non_apportionable=True)
        result = compute_bk_settlement(inp)
        for ln in result.lines:
            if ln.line_type == "COST":
                assert ln.category_code != "VERWALTERKOSTEN"
        for step in result.computation:
            assert step.category_code != "VERWALTERKOSTEN"


# ===========================================================================
# I-G: payload.result totals == settlement line sums (consistency)
# ===========================================================================
class TestIG_PayloadConsistency:
    @settings(max_examples=60, deadline=None)
    @given(
        total_eur=st.integers(min_value=1, max_value=20000),
        advance_eur=st.integers(min_value=0, max_value=20000),
    )
    def test_payload_result_matches_lines(self, total_eur, advance_eur):
        inp = _two_tenant_input(total_eur, advance_eur)
        result = compute_bk_settlement(inp)
        payload = build_snapshot_payload(inp, result)

        cost_sum = sum(
            (ln.amount for ln in result.lines if ln.line_type == "COST"),
            Decimal("0.00"),
        )
        assert cost_sum == result.total_cost
        assert Decimal(payload["result"]["total_cost"]) == result.total_cost
        assert Decimal(payload["result"]["balance"]) == result.balance
        # balance == total_cost - advance
        assert result.balance == (result.total_cost - result.advance_payments_total)


# ---------------------------------------------------------------------------
# Shared scenario builder
# ---------------------------------------------------------------------------
def _two_tenant_input(
    total_eur: int,
    advance_eur: int,
    extra_non_apportionable: bool = False,
) -> SettlementInput:
    """One unit, tenant A (Jan-Jun) and B (Jul-Dec) 2025; bill tenant A."""
    prop_id = uuid.uuid4()
    unit_id = uuid.uuid4()
    la = uuid.uuid4()
    lb = uuid.uuid4()
    owner = uuid.uuid4()
    units = (
        UnitInput(
            id=unit_id, number="1", usage_type="WOHNUNG", area=Decimal("100"), mea=Decimal("100")
        ),
    )
    leases = (
        LeasePeriodInput(
            lease_agreement_id=la,
            unit_id=unit_id,
            tenant_profile_id=uuid.uuid4(),
            valid_from=date(2025, 1, 1),
            valid_to=date(2025, 6, 30),
            advance_payment=Decimal("100.00"),
        ),
        LeasePeriodInput(
            lease_agreement_id=lb,
            unit_id=unit_id,
            tenant_profile_id=uuid.uuid4(),
            valid_from=date(2025, 7, 1),
            valid_to=date(2025, 12, 31),
        ),
    )
    owners = (
        OwnerPeriodInput(
            unit_id=unit_id, owner_profile_id=owner, valid_from=date(2000, 1, 1), valid_to=None
        ),
    )
    positions = [
        CostPositionInput(
            cost_entry_id=uuid.uuid4(),
            category_id=uuid.uuid4(),
            category_code="GRUNDSTEUER",
            label="Grundsteuer",
            gross_amount=Decimal(total_eur).quantize(CENT),
            service_period_from=date(2025, 1, 1),
            service_period_to=date(2025, 12, 31),
            tenant_distribution_key="WOHNFLAECHE",
            tenant_apportionable="UMLAGEFAEHIG",
        )
    ]
    if extra_non_apportionable:
        positions.append(
            CostPositionInput(
                cost_entry_id=uuid.uuid4(),
                category_id=uuid.uuid4(),
                category_code="VERWALTERKOSTEN",
                label="WEG-Verwalterkosten",
                gross_amount=Decimal("999.00"),
                service_period_from=date(2025, 1, 1),
                service_period_to=date(2025, 12, 31),
                tenant_distribution_key="MEA",
                tenant_apportionable="NICHT_UMLAGEFAEHIG",
            )
        )
    return SettlementInput(
        period_start=date(2025, 1, 1),
        period_end=date(2025, 12, 31),
        property=PropertyInput(id=prop_id, name="Haus", management_type="MISCH"),
        target_lease_agreement_id=la,
        units=units,
        lease_periods=leases,
        owner_periods=owners,
        cost_positions=tuple(positions),
        advance_payments_total=Decimal(advance_eur).quantize(CENT),
    )
