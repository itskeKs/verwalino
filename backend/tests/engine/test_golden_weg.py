"""Golden tests for the M4 WEG engine -- hand-computed Soll results, exact cents.

m4-weg-abrechnung.md section 9.1. Each test asserts NOT only end totals but the
individual per-owner line amounts, key bases, subtotals, the Abrechnungsspitze
and the reserve development against the hand computation, so a compensating
double error cannot slip through (same discipline as the M3 Golden tests).

2025 is a non-leap year (365 days): Jan-Jun = 181 days, Jul-Dec = 184.

Pure-function tests -- no DB, no server.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import date
from decimal import Decimal
from fractions import Fraction

import pytest

from app.engine.canonical import canonical_sha256
from app.engine.snapshot import build_weg_snapshot_payload
from app.engine.types import (
    OwnerAdvanceInput,
    OwnerPeriodInput,
    PropertyInput,
    ReserveInput,
    UnitInput,
    WegSettlementInput,
    WegCostPositionInput,
)
from app.engine import weg as weg_engine
from app.engine.weg import WegValidationError, compute_weg_jahresabrechnung

pytestmark = pytest.mark.no_db

YEAR_START = date(2025, 1, 1)
YEAR_END = date(2025, 12, 31)


def _U(n: int) -> uuid.UUID:
    return uuid.UUID(int=n)


def _owner_result(result, owner_id):
    return next(o for o in result.owners if o.owner_profile_id == owner_id)


def _cost_line(owner_result, category_code):
    return next(
        ln for ln in owner_result.cost_lines if ln.category_code == category_code
    )


@pytest.mark.parametrize('cost,plan,paid,spitze,balance,payment', [
    ('6400.00', '7200.00', '6600.00', '-800.00', '600.00', '-200.00'),
    ('8000.00', '7200.00', '7500.00', '800.00', '-300.00', '500.00'),
    ('6400.00', '7200.00', '7200.00', '-800.00', '0.00', '-800.00'),
    ('6400.01', '7200.02', '6600.03', '-800.01', '599.99', '-200.02'),
])
def test_owner_payment_amount_keeps_the_resolved_spitze(cost, plan, paid, spitze, balance, payment):
    inp = TestGolden2Abrechnungsspitze()._scenario()
    inp = replace(inp, units=(inp.units[0],), owner_periods=(inp.owner_periods[0],),
        cost_positions=(replace(inp.cost_positions[0], gross_amount=Decimal(cost)),),
        plan_advances=(OwnerAdvanceInput(_U(21), Decimal(plan)),),
        paid_advances=(OwnerAdvanceInput(_U(21), Decimal(paid)),))
    result = compute_weg_jahresabrechnung(inp)
    owner = result.owners[0]
    assert owner.abrechnungsspitze == Decimal(spitze)
    assert owner.account_balance == Decimal(balance)
    assert owner.payment_amount == Decimal(payment)
    saved = build_weg_snapshot_payload(inp, result)['result']['owners'][0]
    assert {key: saved[key] for key in ('plan_soll', 'paid', 'account_balance', 'payment_amount')} == {
        'plan_soll': plan, 'paid': paid, 'account_balance': balance, 'payment_amount': payment}


@pytest.mark.parametrize('reserve,shares,advances', [
    ('6000.00', ('3600.00', '2400.00'), ('7800.00', '5200.00')),
    ('6000.01', ('3600.01', '2400.00'), ('7800.01', '5200.00')),
])
def test_planned_reserve_is_owner_soll_without_becoming_cost(reserve, shares, advances):
    from app.engine.rounding import distribute_amount

    inp = TestGolden2Abrechnungsspitze()._scenario()
    expected = distribute_amount(Decimal(reserve), [Decimal('600'), Decimal('400')])
    assert expected == [Decimal(amount) for amount in shares]
    inp = replace(inp,
        cost_positions=(replace(inp.cost_positions[0], gross_amount=Decimal('7000.00')),),
        plan_advances=tuple(OwnerAdvanceInput(_U(21 + i), Decimal(amount)) for i, amount in enumerate(advances)),
        plan_reserve=tuple(OwnerAdvanceInput(_U(21 + i), amount) for i, amount in enumerate(expected)))
    result = compute_weg_jahresabrechnung(inp)
    assert result.cost_total == Decimal('7000.00')
    assert sum((o.plan_reserve_soll for o in result.owners), Decimal('0.00')) == Decimal(reserve)
    for owner, cost, share in zip(result.owners, ('4200.00', '2800.00'), shares):
        assert owner.owner_cost_total == Decimal(cost)
        assert owner.plan_reserve_soll == Decimal(share)
        assert owner.abrechnungsspitze == Decimal('0.00')
    assert result.abrechnungsspitze_total == Decimal('0.00')
    saved = build_weg_snapshot_payload(inp, result)['result']['owners']
    assert [o['plan_reserve_soll'] for o in saved] == list(shares)


def test_owner_with_only_planned_reserve_has_a_billed_result():
    inp = TestGolden2Abrechnungsspitze()._scenario()
    inp = replace(inp, plan_reserve=(OwnerAdvanceInput(_U(23), Decimal('123.45')),))
    owner = _owner_result(compute_weg_jahresabrechnung(inp), _U(23))
    assert owner.owner_cost_total == Decimal('0.00')
    assert owner.plan_reserve_soll == owner.abrechnungsspitze == Decimal('123.45')
    assert owner.reserve_notice is None


def test_equal_mea_keeps_canonical_reserve_cents_and_exact_aggregate_spitze():
    # The frozen plan has no per-owner planned cost share: #401 decision 2(a).
    inp = TestGolden2Abrechnungsspitze()._scenario()
    advances = ('66.67', '66.67', '66.66')
    reserves = ('33.34', '33.33', '33.33')
    inp = replace(inp,
        units=tuple(replace(inp.units[0], id=_U(11 + i), number=str(i + 1), mea=Decimal('1')) for i in range(3)),
        owner_periods=tuple(replace(inp.owner_periods[0], unit_id=_U(11 + i), owner_profile_id=_U(21 + i)) for i in range(3)),
        cost_positions=(replace(inp.cost_positions[0], gross_amount=Decimal('100.00')),),
        plan_advances=tuple(OwnerAdvanceInput(_U(21 + i), Decimal(amount)) for i, amount in enumerate(advances)),
        plan_reserve=tuple(OwnerAdvanceInput(_U(21 + i), Decimal(amount)) for i, amount in enumerate(reserves)))
    result = compute_weg_jahresabrechnung(inp)
    assert [o.owner_cost_total for o in result.owners] == list(map(Decimal, reserves))
    assert [o.plan_advance for o in result.owners] == list(map(Decimal, advances))
    assert [o.plan_reserve_soll for o in result.owners] == list(map(Decimal, reserves))
    assert [o.abrechnungsspitze for o in result.owners] == list(map(Decimal, ('0.01', '-0.01', '0.00')))
    assert all(abs(o.abrechnungsspitze) <= Decimal('0.01') for o in result.owners)
    assert result.abrechnungsspitze_total == Decimal('0.00')


def test_owner_change_never_transfers_payments_to_the_successor():
    inp = TestGolden2Abrechnungsspitze()._scenario()
    inp = replace(inp, units=(inp.units[0],), owner_periods=(
        replace(inp.owner_periods[0], valid_to=date(2025, 6, 30)),
        replace(inp.owner_periods[0], owner_profile_id=_U(22), valid_from=date(2025, 7, 1)),),
        cost_positions=(replace(inp.cost_positions[0], gross_amount=Decimal('3650.00')),),
        plan_advances=(OwnerAdvanceInput(_U(21), Decimal('1800.00')), OwnerAdvanceInput(_U(22), Decimal('1800.00'))),
        paid_advances=(OwnerAdvanceInput(_U(21), Decimal('1600.00')), OwnerAdvanceInput(_U(22), Decimal('1900.00'))))
    old, new = compute_weg_jahresabrechnung(inp).owners
    assert (old.abrechnungsspitze, old.account_balance, old.payment_amount) == (Decimal('10'), Decimal('200'), Decimal('210'))
    assert (new.abrechnungsspitze, new.account_balance, new.payment_amount) == (Decimal('40'), Decimal('-100'), Decimal('-60'))


def test_payment_only_successor_keeps_credit_without_changing_original_owners():
    inp = TestGolden2Abrechnungsspitze()._scenario()
    inp = replace(inp,
        owner_periods=(inp.owner_periods[0], replace(inp.owner_periods[1], valid_to=date(2025, 6, 30)),
            replace(inp.owner_periods[1], owner_profile_id=_U(23), valid_from=date(2025, 7, 1))),
        cost_positions=tuple(replace(p, service_period_to=date(2025, 6, 30)) for p in inp.cost_positions),
        paid_advances=(OwnerAdvanceInput(_U(21), Decimal('5000.00')), OwnerAdvanceInput(_U(22), Decimal('4000.00'))))
    original = compute_weg_jahresabrechnung(inp)
    inp = replace(inp, paid_advances=(*inp.paid_advances, OwnerAdvanceInput(_U(23), Decimal('1900.00'))))
    result = compute_weg_jahresabrechnung(inp)
    successor = next((o for o in result.owners if o.owner_profile_id == _U(23)), None)
    assert successor is not None, 'An owner with only booked payments must have a result'
    assert (successor.owner_cost_total, successor.plan_advance, successor.abrechnungsspitze) == (Decimal('0.00'),) * 3
    assert (successor.paid_advances, successor.account_balance, successor.payment_amount) == (Decimal('1900.00'), Decimal('-1900.00'), Decimal('-1900.00'))
    assert successor.reserve_notice is None
    assert tuple(o for o in result.owners if o.owner_profile_id != _U(23)) == original.owners
    assert result.abrechnungsspitze_total == original.abrechnungsspitze_total
    saved = build_weg_snapshot_payload(inp, result)['result']['owners']
    assert next(o['payment_amount'] for o in saved if o['owner_profile_id'] == str(_U(23))) == '-1900.00'


@pytest.mark.parametrize('funded, expected', [
    ('0.00', ('4200.00', '2800.00')),
    ('400.00', ('3960.00', '2640.00')),
    ('1000.00', ('3600.00', '2400.00')),
])
def test_reserve_financing_is_deducted_before_owner_distribution(funded, expected):
    inp = TestGolden2Abrechnungsspitze()._scenario()
    ordinary = replace(inp.cost_positions[0], gross_amount=Decimal('6000.00'))
    repair = replace(ordinary, cost_entry_id=_U(51), category_code='REPARATUR',
                     gross_amount=Decimal('1000.00'), reserve_funded_amount=Decimal(funded))
    inp = replace(inp, cost_positions=(ordinary, repair),
                  reserve=ReserveInput(opening_balance=Decimal('5000.00'),
                                       withdrawals_total=Decimal(funded)))
    result = compute_weg_jahresabrechnung(inp)
    assert result.cost_total == Decimal('7000.00') - Decimal(funded)
    for owner_id, total in zip((_U(21), _U(22)), expected):
        owner = _owner_result(result, owner_id)
        assert owner.owner_cost_total == Decimal(total)
        assert owner.subtotal_umlagefaehig == Decimal(total)
        assert owner.abrechnungsspitze == Decimal(total) - next(
            a.plan_advance_total for a in inp.plan_advances if a.owner_profile_id == owner_id)
        assert _cost_line(owner, 'REPARATUR').total_amount == Decimal('1000.00') - Decimal(funded)
    assert result.reserve.closing_balance == Decimal('5000.00') - Decimal(funded)
    payload = build_weg_snapshot_payload(inp, result)
    position = next(p for p in payload['inputs']['cost_positions'] if p['category_code'] == 'REPARATUR')
    assert position['gross_amount'] == '1000.00'
    assert position.get('reserve_funded_amount', '0.00') == funded
    assert ('reserve_funded_amount' in position) is (Decimal(funded) != 0)


@pytest.mark.parametrize('key', ['MANUELLER_BETRAG', 'EXTERNE_ABRECHNUNG'])
def test_financing_preserves_direct_owner_weights(key):
    from app.engine.types import ExternalAmountInput, ManualAmountInput
    inp = TestGolden2Abrechnungsspitze()._scenario()
    pos = replace(inp.cost_positions[0], gross_amount=Decimal('1000.00'),
        owner_distribution_key=key, reserve_funded_amount=Decimal('400.00'),
        manual_amounts=tuple(ManualAmountInput(unit_id=unit.id, amount=Decimal(amount))
                            for unit, amount in zip(inp.units, ('700.00', '300.00'))),
        external_amounts=tuple(ExternalAmountInput(unit_id=unit.id, tenant_amount=Decimal(amount))
                              for unit, amount in zip(inp.units, ('700.00', '300.00'))))
    result = compute_weg_jahresabrechnung(replace(inp, cost_positions=(pos,)))
    assert _owner_result(result, _U(21)).owner_cost_total == Decimal('420.00')
    assert _owner_result(result, _U(22)).owner_cost_total == Decimal('180.00')
    assert result.cost_total == Decimal('600.00')


@pytest.mark.parametrize('funded', ['-0.01', '10000.01'])
def test_invalid_financing_cannot_create_a_negative_owner_cost(funded):
    inp = TestGolden2Abrechnungsspitze()._scenario()
    pos = replace(inp.cost_positions[0], reserve_funded_amount=Decimal(funded))
    with pytest.raises(WegValidationError, match='invalid reserve financing'):
        compute_weg_jahresabrechnung(replace(inp, cost_positions=(pos,)))


# ===========================================================================
# Golden 1: trivial MEA distribution (m4 section 9.1 #1)
#   3 units MEA 500/300/200 (of 1000); Verwalterkosten 3000.00 NICHT_UMLAGEFAEHIG
#   MEA, full year, no change. Soll: 1500/900/600.
# ===========================================================================
class TestGolden1MeaTrivial:
    def _scenario(self):
        u1, u2, u3 = _U(11), _U(12), _U(13)
        o1, o2, o3 = _U(21), _U(22), _U(23)
        units = (
            UnitInput(id=u1, number="1", usage_type="WOHNUNG", mea=Decimal("500")),
            UnitInput(id=u2, number="2", usage_type="WOHNUNG", mea=Decimal("300")),
            UnitInput(id=u3, number="3", usage_type="WOHNUNG", mea=Decimal("200")),
        )
        owners = (
            OwnerPeriodInput(unit_id=u1, owner_profile_id=o1, valid_from=date(2000, 1, 1), valid_to=None),
            OwnerPeriodInput(unit_id=u2, owner_profile_id=o2, valid_from=date(2000, 1, 1), valid_to=None),
            OwnerPeriodInput(unit_id=u3, owner_profile_id=o3, valid_from=date(2000, 1, 1), valid_to=None),
        )
        pos = (
            WegCostPositionInput(
                cost_entry_id=_U(50),
                category_id=_U(99),
                category_code="WEG_VERWALTERKOSTEN",
                label="Verwalterkosten",
                gross_amount=Decimal("3000.00"),
                service_period_from=YEAR_START,
                service_period_to=YEAR_END,
                owner_distribution_key="MEA",
                cost_group="NICHT_UMLAGEFAEHIG",
            ),
        )
        # No plan advances -> Spitze = owner_cost_total - 0.
        return WegSettlementInput(
            period_start=YEAR_START,
            period_end=YEAR_END,
            property=PropertyInput(id=_U(1), name="WEG Haus", management_type="WEG"),
            units=units,
            owner_periods=owners,
            cost_positions=pos,
            plan_advances=(),
        )

    def test_mea_shares(self):
        r = compute_weg_jahresabrechnung(self._scenario())
        o1 = _owner_result(r, _U(21))
        o2 = _owner_result(r, _U(22))
        o3 = _owner_result(r, _U(23))
        # 3000 * 500/1000 = 1500 ; * 300/1000 = 900 ; * 200/1000 = 600
        assert _cost_line(o1, "WEG_VERWALTERKOSTEN").amount == Decimal("1500.00")
        assert _cost_line(o2, "WEG_VERWALTERKOSTEN").amount == Decimal("900.00")
        assert _cost_line(o3, "WEG_VERWALTERKOSTEN").amount == Decimal("600.00")
        # Key base recorded (numerator/denominator).
        ln = _cost_line(o1, "WEG_VERWALTERKOSTEN")
        assert ln.distribution_base_share == Decimal("500")
        assert ln.distribution_base_total == Decimal("1000")
        assert ln.total_amount == Decimal("3000.00")
        # NICHT_UMLAGEFAEHIG subtotal carries it; umlagefaehig + reserve = 0.
        assert o1.subtotal_nicht_umlagefaehig == Decimal("1500.00")
        assert o1.subtotal_umlagefaehig == Decimal("0.00")
        assert o1.subtotal_reserve == Decimal("0.00")
        # OWNER_COST_TOTAL = umlagefaehig + nicht (no reserve).
        assert o1.owner_cost_total == Decimal("1500.00")

    def test_sum_equals_total(self):
        r = compute_weg_jahresabrechnung(self._scenario())
        s = sum((o.owner_cost_total for o in r.owners), Decimal("0.00"))
        assert s == Decimal("3000.00")
        assert r.cost_total == Decimal("3000.00")


# ===========================================================================
# Golden 2: Abrechnungsspitze (m4 section 9.1 #2) -- THE core
#   2 units MEA 600/400; Ist gesamt 10.000,00 (MEA -> 6000/4000);
#   plan Soll-Vorschuesse 5500/4200.
#   Spitze A = 6000 - 5500 = +500.00 (Nachschuss); B = 4000 - 4200 = -200.00.
# ===========================================================================
class TestGolden2Abrechnungsspitze:
    def _scenario(self):
        u1, u2 = _U(11), _U(12)
        oa, ob = _U(21), _U(22)
        units = (
            UnitInput(id=u1, number="1", usage_type="WOHNUNG", mea=Decimal("600")),
            UnitInput(id=u2, number="2", usage_type="WOHNUNG", mea=Decimal("400")),
        )
        owners = (
            OwnerPeriodInput(unit_id=u1, owner_profile_id=oa, valid_from=date(2000, 1, 1), valid_to=None),
            OwnerPeriodInput(unit_id=u2, owner_profile_id=ob, valid_from=date(2000, 1, 1), valid_to=None),
        )
        pos = (
            WegCostPositionInput(
                cost_entry_id=_U(50),
                category_id=_U(99),
                category_code="HEIZUNG",
                label="Bewirtschaftung",
                gross_amount=Decimal("10000.00"),
                service_period_from=YEAR_START,
                service_period_to=YEAR_END,
                owner_distribution_key="MEA",
                cost_group="UMLAGEFAEHIG",
            ),
        )
        plan = (
            OwnerAdvanceInput(owner_profile_id=oa, plan_advance_total=Decimal("5500.00")),
            OwnerAdvanceInput(owner_profile_id=ob, plan_advance_total=Decimal("4200.00")),
        )
        return WegSettlementInput(
            period_start=YEAR_START,
            period_end=YEAR_END,
            property=PropertyInput(id=_U(1), name="WEG Haus", management_type="WEG"),
            units=units,
            owner_periods=owners,
            cost_positions=pos,
            plan_advances=plan,
        )

    def test_spitze_exact(self):
        r = compute_weg_jahresabrechnung(self._scenario())
        oa = _owner_result(r, _U(21))
        ob = _owner_result(r, _U(22))
        assert oa.owner_cost_total == Decimal("6000.00")
        assert ob.owner_cost_total == Decimal("4000.00")
        assert oa.plan_advance == Decimal("5500.00")
        assert ob.plan_advance == Decimal("4200.00")
        # The beschlussrelevante Spitze = Ist - Soll (NOT Ist - gezahlt).
        assert oa.abrechnungsspitze == Decimal("500.00")  # Nachschuss
        assert ob.abrechnungsspitze == Decimal("-200.00")  # Guthaben

    def test_spitze_total_no_drift(self):
        r = compute_weg_jahresabrechnung(self._scenario())
        # Σ Spitzen == Σ Ist - Σ Soll = 10000 - 9700 = 300.
        assert r.cost_total == Decimal("10000.00")
        assert r.plan_advances_total == Decimal("9700.00")
        assert r.abrechnungsspitze_total == Decimal("300.00")
        s = sum((o.abrechnungsspitze for o in r.owners), Decimal("0.00"))
        assert s == Decimal("300.00")

    def test_paid_advances_kept_separate_from_spitze(self):
        # A säumiger owner (paid less than Soll) must NOT change the Spitze.
        inp = self._scenario()
        inp = WegSettlementInput(
            period_start=inp.period_start,
            period_end=inp.period_end,
            property=inp.property,
            units=inp.units,
            owner_periods=inp.owner_periods,
            cost_positions=inp.cost_positions,
            plan_advances=inp.plan_advances,
            paid_advances=(
                OwnerAdvanceInput(owner_profile_id=_U(21), plan_advance_total=Decimal("0.00")),
                OwnerAdvanceInput(owner_profile_id=_U(22), plan_advance_total=Decimal("4200.00")),
            ),
        )
        r = compute_weg_jahresabrechnung(inp)
        oa = _owner_result(r, _U(21))
        # Spitze unchanged (Ist - Soll), paid only nachrichtlich.
        assert oa.abrechnungsspitze == Decimal("500.00")
        assert oa.paid_advances == Decimal("0.00")


# ===========================================================================
# Golden 3: Trennung umlagefaehig / nicht / Ruecklage (m4 section 9.1 #3)
#   1 unit (MEA 1000, sole owner) so the split is unambiguous.
#   Gartenpflege 1000 UMLAGEFAEHIG; Verwalter 500 NICHT_UMLAGEFAEHIG;
#   Erhaltungsruecklage-Zufuehrung 2000 reserve_relevant.
#   Soll: subtotal_uml=1000, subtotal_nicht=500, subtotal_reserve=2000,
#         OWNER_COST_TOTAL = 1500 (reserve NOT included).
# ===========================================================================
class TestGolden3ThreeSpheres:
    def _scenario(self):
        u1 = _U(11)
        o1 = _U(21)
        units = (UnitInput(id=u1, number="1", usage_type="WOHNUNG", mea=Decimal("1000")),)
        owners = (
            OwnerPeriodInput(unit_id=u1, owner_profile_id=o1, valid_from=date(2000, 1, 1), valid_to=None),
        )
        pos = (
            WegCostPositionInput(
                cost_entry_id=_U(50), category_id=_U(91), category_code="GARTENPFLEGE",
                label="Gartenpflege", gross_amount=Decimal("1000.00"),
                service_period_from=YEAR_START, service_period_to=YEAR_END,
                owner_distribution_key="MEA", cost_group="UMLAGEFAEHIG",
            ),
            WegCostPositionInput(
                cost_entry_id=_U(51), category_id=_U(92), category_code="WEG_VERWALTERKOSTEN",
                label="Verwalter", gross_amount=Decimal("500.00"),
                service_period_from=YEAR_START, service_period_to=YEAR_END,
                owner_distribution_key="MEA", cost_group="NICHT_UMLAGEFAEHIG",
            ),
            WegCostPositionInput(
                cost_entry_id=_U(52), category_id=_U(93), category_code="ERHALTUNGSRUECKLAGE",
                label="Zufuehrung Ruecklage", gross_amount=Decimal("2000.00"),
                service_period_from=YEAR_START, service_period_to=YEAR_END,
                owner_distribution_key="MEA", cost_group="NICHT_UMLAGEFAEHIG",
                reserve_relevant=True,
            ),
        )
        return WegSettlementInput(
            period_start=YEAR_START, period_end=YEAR_END,
            property=PropertyInput(id=_U(1), name="WEG", management_type="WEG"),
            units=units, owner_periods=owners, cost_positions=pos,
            plan_advances=(OwnerAdvanceInput(owner_profile_id=o1, plan_advance_total=Decimal("1400.00")),),
        )

    def test_three_subtotals(self):
        r = compute_weg_jahresabrechnung(self._scenario())
        o1 = _owner_result(r, _U(21))
        assert o1.subtotal_umlagefaehig == Decimal("1000.00")
        assert o1.subtotal_nicht_umlagefaehig == Decimal("500.00")
        assert o1.subtotal_reserve == Decimal("2000.00")

    def test_reserve_not_in_owner_cost_total_or_spitze(self):
        r = compute_weg_jahresabrechnung(self._scenario())
        o1 = _owner_result(r, _U(21))
        # OWNER_COST_TOTAL = 1000 + 500 = 1500 (reserve 2000 NOT counted).
        assert o1.owner_cost_total == Decimal("1500.00")
        # Spitze = 1500 - 1400 = 100 (reserve must not push this to 2100).
        assert o1.abrechnungsspitze == Decimal("100.00")
        # The reserve contribution line is RESERVE_CONTRIBUTION, not COST.
        res_line = _cost_line(o1, "ERHALTUNGSRUECKLAGE")
        assert res_line.line_type == "RESERVE_CONTRIBUTION"

    def test_cost_total_excludes_reserve(self):
        r = compute_weg_jahresabrechnung(self._scenario())
        assert r.cost_total == Decimal("1500.00")
        assert r.reserve_contributions_total == Decimal("2000.00")


# ===========================================================================
# Golden 4: Reserve development (m4 section 9.1 #4)
#   opening 20000, contribution 6000 (a reserve cost position), withdrawal 8000
#   -> closing 18000. Invariant + escalation on inconsistency (the engine
#   DERIVES closing, so an inconsistent closing cannot be supplied; we instead
#   verify the development math and that a wrong opening/withdrawal is reflected).
# ===========================================================================
class TestGolden4ReserveDevelopment:
    def _scenario(self, opening, withdrawal, contribution):
        u1 = _U(11)
        o1 = _U(21)
        units = (UnitInput(id=u1, number="1", usage_type="WOHNUNG", mea=Decimal("1000")),)
        owners = (
            OwnerPeriodInput(unit_id=u1, owner_profile_id=o1, valid_from=date(2000, 1, 1), valid_to=None),
        )
        pos = (
            WegCostPositionInput(
                cost_entry_id=_U(52), category_id=_U(93), category_code="ERHALTUNGSRUECKLAGE",
                label="Zufuehrung", gross_amount=contribution,
                service_period_from=YEAR_START, service_period_to=YEAR_END,
                owner_distribution_key="MEA", cost_group="NICHT_UMLAGEFAEHIG",
                reserve_relevant=True,
            ),
        )
        return WegSettlementInput(
            period_start=YEAR_START, period_end=YEAR_END,
            property=PropertyInput(id=_U(1), name="WEG", management_type="WEG"),
            units=units, owner_periods=owners, cost_positions=pos, plan_advances=(),
            reserve=ReserveInput(opening_balance=opening, withdrawals_total=withdrawal),
        )

    def test_development(self):
        r = compute_weg_jahresabrechnung(
            self._scenario(Decimal("20000.00"), Decimal("8000.00"), Decimal("6000.00"))
        )
        dev = r.reserve
        assert dev.opening_balance == Decimal("20000.00")
        assert dev.contributions_total == Decimal("6000.00")
        assert dev.withdrawals_total == Decimal("8000.00")
        # closing = 20000 + 6000 - 8000 = 18000.
        assert dev.closing_balance == Decimal("18000.00")

    def test_invariant_holds_for_random_like_values(self):
        r = compute_weg_jahresabrechnung(
            self._scenario(Decimal("1234.56"), Decimal("789.10"), Decimal("321.00"))
        )
        dev = r.reserve
        assert dev.closing_balance == (
            dev.opening_balance + dev.contributions_total - dev.withdrawals_total
        )


# ===========================================================================
# Golden 5: Eigentuemerwechsel unterjaehrig tagegenau (m4 section 9.1 #5)
#   Unit verkauft 30.06.->01.07. (2025); 1 MEA cost position full year.
#   Verkaeufer 181/365, Kaeufer 184/365 of the unit's share; Σ = unit share.
# ===========================================================================
class TestGolden5OwnerChangeDayExact:
    def _scenario(self):
        # Two units MEA 500/500 of 1000. A deliberately "krummer" cost so the
        # unit-1 share is NOT a clean multiple of 365 -> the day-exact split
        # genuinely exercises Largest-Remainder at the owner change.
        # Cost 1000.00 -> each unit 500.00. Unit 1 changes owner mid-year.
        u1, u2 = _U(11), _U(12)
        seller, buyer, o2 = _U(21), _U(22), _U(23)
        units = (
            UnitInput(id=u1, number="1", usage_type="WOHNUNG", mea=Decimal("500")),
            UnitInput(id=u2, number="2", usage_type="WOHNUNG", mea=Decimal("500")),
        )
        owners = (
            # Seller owns unit 1 until 30.06 (inclusive, engine tag-inclusive).
            OwnerPeriodInput(unit_id=u1, owner_profile_id=seller, valid_from=date(2000, 1, 1), valid_to=date(2025, 6, 30)),
            # Buyer owns unit 1 from 01.07 onward.
            OwnerPeriodInput(unit_id=u1, owner_profile_id=buyer, valid_from=date(2025, 7, 1), valid_to=None),
            OwnerPeriodInput(unit_id=u2, owner_profile_id=o2, valid_from=date(2000, 1, 1), valid_to=None),
        )
        pos = (
            WegCostPositionInput(
                cost_entry_id=_U(50), category_id=_U(99), category_code="GRUNDSTEUER",
                label="Grundsteuer", gross_amount=Decimal("1000.00"),
                service_period_from=YEAR_START, service_period_to=YEAR_END,
                owner_distribution_key="MEA", cost_group="UMLAGEFAEHIG",
            ),
        )
        return WegSettlementInput(
            period_start=YEAR_START, period_end=YEAR_END,
            property=PropertyInput(id=_U(1), name="WEG", management_type="WEG"),
            units=units, owner_periods=owners, cost_positions=pos, plan_advances=(),
        )

    def test_seller_buyer_day_exact(self):
        r = compute_weg_jahresabrechnung(self._scenario())
        seller = _owner_result(r, _U(21))
        buyer = _owner_result(r, _U(22))
        o2 = _owner_result(r, _U(23))
        # Unit 1 share = 1000 * 500/1000 = 500.00.
        # Seller 500 * 181/365 = 247.945205... ; Buyer 500 * 184/365 = 252.0547...
        # The single Largest-Remainder pass runs over ALL cells (seller, buyer,
        # o2) so Σ = 1000.00 exactly. Cell exact shares:
        #   seller = 1000 * (0.5 * 181/365) = 247.945205...
        #   buyer  = 1000 * (0.5 * 184/365) = 252.054794...
        #   o2     = 1000 * 0.5             = 500.000000
        # floors: 247.94 / 252.05 / 500.00 = 999.99 ; 1 cent remnant ->
        # largest fractional remnant is buyer (.054... > seller .945? no:
        # frac(seller)=.945205, frac(buyer)=.054794, frac(o2)=.0) -> seller wins.
        # seller 247.95 ; buyer 252.05 ; o2 500.00 ; Σ = 1000.00.
        assert seller.owner_cost_total == Decimal("247.95")
        assert buyer.owner_cost_total == Decimal("252.05")
        assert o2.owner_cost_total == Decimal("500.00")
        # No phantom day: seller + buyer == unit 1 share (500.00).
        assert seller.owner_cost_total + buyer.owner_cost_total == Decimal("500.00")

    def test_property_total_consistent(self):
        r = compute_weg_jahresabrechnung(self._scenario())
        s = sum((o.owner_cost_total for o in r.owners), Decimal("0.00"))
        assert s == Decimal("1000.00")


# ===========================================================================
# Golden 6: Miteigentum / Bruchteilseigentum (m4 section 9.1 #6)
#   Unit with two co-owners mea_share 0.6 / 0.4, MEA cost position.
#   Unit share split exactly 0.6/0.4, Largest-Remainder summengenau.
# ===========================================================================
class TestGolden6CoOwnership:
    def _scenario(self):
        # One unit MEA 1000 (sole in WEG) cost 1000.00 -> co-owners 600/400.
        u1 = _U(11)
        co_a, co_b = _U(21), _U(22)
        units = (UnitInput(id=u1, number="1", usage_type="WOHNUNG", mea=Decimal("1000")),)
        owners = (
            OwnerPeriodInput(unit_id=u1, owner_profile_id=co_a, valid_from=date(2000, 1, 1), valid_to=None, mea_share=Decimal("0.6")),
            OwnerPeriodInput(unit_id=u1, owner_profile_id=co_b, valid_from=date(2000, 1, 1), valid_to=None, mea_share=Decimal("0.4")),
        )
        pos = (
            WegCostPositionInput(
                cost_entry_id=_U(50), category_id=_U(99), category_code="GRUNDSTEUER",
                label="Grundsteuer", gross_amount=Decimal("1000.00"),
                service_period_from=YEAR_START, service_period_to=YEAR_END,
                owner_distribution_key="MEA", cost_group="UMLAGEFAEHIG",
            ),
        )
        return WegSettlementInput(
            period_start=YEAR_START, period_end=YEAR_END,
            property=PropertyInput(id=_U(1), name="WEG", management_type="WEG"),
            units=units, owner_periods=owners, cost_positions=pos, plan_advances=(),
        )

    def test_co_owner_split(self):
        r = compute_weg_jahresabrechnung(self._scenario())
        a = _owner_result(r, _U(21))
        b = _owner_result(r, _U(22))
        # 1000 * 0.6 = 600.00 ; * 0.4 = 400.00.
        assert a.owner_cost_total == Decimal("600.00")
        assert b.owner_cost_total == Decimal("400.00")
        assert a.owner_cost_total + b.owner_cost_total == Decimal("1000.00")

    def test_co_owner_split_summengenau_odd(self):
        # An odd amount that does not divide cleanly: 100.01 * 0.6 = 60.006 -> 60.01
        # 100.01 * 0.4 = 40.004 -> 40.00 ; Σ = 100.01 (Largest-Remainder).
        inp = self._scenario()
        pos = (
            WegCostPositionInput(
                cost_entry_id=_U(50), category_id=_U(99), category_code="GRUNDSTEUER",
                label="Grundsteuer", gross_amount=Decimal("100.01"),
                service_period_from=YEAR_START, service_period_to=YEAR_END,
                owner_distribution_key="MEA", cost_group="UMLAGEFAEHIG",
            ),
        )
        inp = WegSettlementInput(
            period_start=inp.period_start, period_end=inp.period_end,
            property=inp.property, units=inp.units, owner_periods=inp.owner_periods,
            cost_positions=pos, plan_advances=(),
        )
        r = compute_weg_jahresabrechnung(inp)
        a = _owner_result(r, _U(21))
        b = _owner_result(r, _U(22))
        assert a.owner_cost_total + b.owner_cost_total == Decimal("100.01")
        # 60.006 has the larger fractional remnant (.006 vs .004) -> gets the cent.
        assert a.owner_cost_total == Decimal("60.01")
        assert b.owner_cost_total == Decimal("40.00")


# ===========================================================================
# Golden 10: service period crossing the year boundary (issue #403)
#   A weighted key's effective total is windowed to the settlement period
#   day-accurately (ADR-0004), like the M3 tenant side since #122: 184/365 of
#   3,650.00 = 1,840.00 in 2024 and 181/365 = 1,810.00 in 2025, never the
#   full amount in both years. Cumulative cents make adjacent years telescope
#   exactly back to the gross amount.
# ===========================================================================
class TestGolden10CrossYearServicePeriod:
    def _scenario(self, *, period_start, period_end, gross, sp_from, sp_to,
                  units=None, owner_periods=None):
        if units is None:
            units = (UnitInput(id=_U(11), number="1", usage_type="WOHNUNG", mea=Decimal("1000")),)
        if owner_periods is None:
            owner_periods = (
                OwnerPeriodInput(unit_id=_U(11), owner_profile_id=_U(21), valid_from=date(2000, 1, 1), valid_to=None),
            )
        pos = (
            WegCostPositionInput(
                cost_entry_id=_U(50), category_id=_U(99), category_code="GEBAEUDEVERSICHERUNG",
                label="Gebaeudeversicherung", gross_amount=gross,
                service_period_from=sp_from, service_period_to=sp_to,
                owner_distribution_key="MEA", cost_group="UMLAGEFAEHIG",
            ),
        )
        return WegSettlementInput(
            period_start=period_start, period_end=period_end,
            property=PropertyInput(id=_U(1), name="WEG", management_type="WEG"),
            units=units, owner_periods=owner_periods, cost_positions=pos, plan_advances=(),
        )

    def test_3650_splits_1840_1810(self):
        gross = Decimal("3650.00")
        pos_period = (date(2024, 7, 1), date(2025, 6, 30))  # 184 + 181 = 365 days
        r2024 = compute_weg_jahresabrechnung(self._scenario(
            period_start=date(2024, 1, 1), period_end=date(2024, 12, 31),
            gross=gross, sp_from=pos_period[0], sp_to=pos_period[1]))
        r2025 = compute_weg_jahresabrechnung(self._scenario(
            period_start=date(2025, 1, 1), period_end=date(2025, 12, 31),
            gross=gross, sp_from=pos_period[0], sp_to=pos_period[1]))
        owner_2024 = _owner_result(r2024, _U(21))
        owner_2025 = _owner_result(r2025, _U(21))
        # 3650 * 184/365 = 1840.00 ; 3650 * 181/365 = 1810.00.
        assert r2024.cost_total == Decimal("1840.00")
        assert owner_2024.owner_cost_total == Decimal("1840.00")
        assert r2025.cost_total == Decimal("1810.00")
        assert owner_2025.owner_cost_total == Decimal("1810.00")
        # The line carries the windowed total and the real day counts.
        ln2024 = _cost_line(owner_2024, "GEBAEUDEVERSICHERUNG")
        assert ln2024.total_amount == Decimal("1840.00")
        assert (ln2024.time_share_days, ln2024.time_share_total_days) == (184, 365)
        ln2025 = _cost_line(owner_2025, "GEBAEUDEVERSICHERUNG")
        assert ln2025.total_amount == Decimal("1810.00")
        assert (ln2025.time_share_days, ln2025.time_share_total_days) == (181, 365)
        # Adjacent years telescope exactly to the gross amount.
        assert r2024.cost_total + r2025.cost_total == gross

    def test_1200_splits_604_93_595_07(self):
        gross = Decimal("1200.00")
        pos_period = (date(2025, 7, 1), date(2026, 6, 30))  # 184 + 181 = 365 days
        r2025 = compute_weg_jahresabrechnung(self._scenario(
            period_start=date(2025, 1, 1), period_end=date(2025, 12, 31),
            gross=gross, sp_from=pos_period[0], sp_to=pos_period[1]))
        r2026 = compute_weg_jahresabrechnung(self._scenario(
            period_start=date(2026, 1, 1), period_end=date(2026, 12, 31),
            gross=gross, sp_from=pos_period[0], sp_to=pos_period[1]))
        # floor(1200 * 184/365) = 604.93 ; the remainder 1200 - 604.93 = 595.07.
        assert r2025.cost_total == Decimal("604.93")
        assert r2026.cost_total == Decimal("595.07")
        assert r2025.cost_total + r2026.cost_total == gross

    def test_two_units_owner_change_inside_the_window_sums_exactly(self):
        # Units MEA 600/400; unit 1 changes owner at the year boundary
        # (seller until 31.12.2025, buyer from 01.01.2026). One position
        # 1,200.00 for 01.07.2025-30.06.2026, key MEA.
        units = (
            UnitInput(id=_U(11), number="1", usage_type="WOHNUNG", mea=Decimal("600")),
            UnitInput(id=_U(12), number="2", usage_type="WOHNUNG", mea=Decimal("400")),
        )
        owners = (
            OwnerPeriodInput(unit_id=_U(11), owner_profile_id=_U(21), valid_from=date(2000, 1, 1), valid_to=date(2025, 12, 31)),
            OwnerPeriodInput(unit_id=_U(11), owner_profile_id=_U(22), valid_from=date(2026, 1, 1), valid_to=None),
            OwnerPeriodInput(unit_id=_U(12), owner_profile_id=_U(23), valid_from=date(2000, 1, 1), valid_to=None),
        )
        sp_from, sp_to = date(2025, 7, 1), date(2026, 6, 30)
        r2025 = compute_weg_jahresabrechnung(self._scenario(
            period_start=date(2025, 1, 1), period_end=date(2025, 12, 31),
            gross=Decimal("1200.00"), sp_from=sp_from, sp_to=sp_to,
            units=units, owner_periods=owners))
        r2026 = compute_weg_jahresabrechnung(self._scenario(
            period_start=date(2026, 1, 1), period_end=date(2026, 12, 31),
            gross=Decimal("1200.00"), sp_from=sp_from, sp_to=sp_to,
            units=units, owner_periods=owners))
        seller = _owner_result(r2025, _U(21))
        buyer = _owner_result(r2026, _U(22))
        steady = (  # the owner of unit 2 appears in both settlements
            _owner_result(r2025, _U(23)), _owner_result(r2026, _U(23)))
        # Windowed totals: 604.93 (2025) / 595.07 (2026).
        assert r2025.cost_total == Decimal("604.93")
        assert r2026.cost_total == Decimal("595.07")
        # 2025: 604.93 x 0.6 = 362.958 -> 362.96 (larger remnant gets the cent);
        #       604.93 x 0.4 = 241.972 -> 241.97.
        # 2026: 595.07 x 0.6 = 357.042 -> 357.04 ; 595.07 x 0.4 = 238.028 -> 238.03.
        assert seller.owner_cost_total == Decimal("362.96")
        assert steady[0].owner_cost_total == Decimal("241.97")
        assert buyer.owner_cost_total == Decimal("357.04")
        assert steady[1].owner_cost_total == Decimal("238.03")
        # Unit 1 across the boundary: exactly its 60% share (720.00).
        assert seller.owner_cost_total + buyer.owner_cost_total == Decimal("720.00")
        # Unit 2 across the boundary: exactly its 40% share (480.00).
        assert steady[0].owner_cost_total + steady[1].owner_cost_total == Decimal("480.00")
        assert r2025.cost_total + r2026.cost_total == Decimal("1200.00")

    def test_service_period_inside_the_year_is_unchanged_in_full(self):
        # Jan-Jun service period fully inside the 2025 settlement: no window.
        r = compute_weg_jahresabrechnung(self._scenario(
            period_start=date(2025, 1, 1), period_end=date(2025, 12, 31),
            gross=Decimal("1000.00"),
            sp_from=date(2025, 1, 1), sp_to=date(2025, 6, 30)))
        owner = _owner_result(r, _U(21))
        assert r.cost_total == Decimal("1000.00")
        assert owner.owner_cost_total == Decimal("1000.00")
        ln = _cost_line(owner, "GEBAEUDEVERSICHERUNG")
        assert ln.total_amount == Decimal("1000.00")
        assert (ln.time_share_days, ln.time_share_total_days) == (181, 181)


# ===========================================================================
# Golden 9 (engine part): snapshot/hash determinism (m4 section 9.1 #9)
#   Scenario 2 finalized twice (identical inputs) -> identical sha256.
# ===========================================================================
class TestGolden9SnapshotDeterminism:
    def test_hash_stable(self):
        s2 = TestGolden2Abrechnungsspitze()
        inp = s2._scenario()
        r1 = compute_weg_jahresabrechnung(inp)
        r2 = compute_weg_jahresabrechnung(inp)
        p1 = build_weg_snapshot_payload(inp, r1)
        p2 = build_weg_snapshot_payload(inp, r2)
        assert canonical_sha256(p1) == canonical_sha256(p2)


# ===========================================================================
# Fail-loud validation (m4 section 10.1)
# ===========================================================================
class TestWegFailLoud:
    def test_ownership_gap_escalates(self):
        u1 = _U(11)
        o1 = _U(21)
        units = (UnitInput(id=u1, number="1", usage_type="WOHNUNG", mea=Decimal("1000")),)
        # Owner only covers half the year -> a gap in [01.07..31.12].
        owners = (
            OwnerPeriodInput(unit_id=u1, owner_profile_id=o1, valid_from=YEAR_START, valid_to=date(2025, 6, 30)),
        )
        pos = (
            WegCostPositionInput(
                cost_entry_id=_U(50), category_id=_U(99), category_code="GRUNDSTEUER",
                label="x", gross_amount=Decimal("100.00"),
                service_period_from=YEAR_START, service_period_to=YEAR_END,
                owner_distribution_key="MEA", cost_group="UMLAGEFAEHIG",
            ),
        )
        inp = WegSettlementInput(
            period_start=YEAR_START, period_end=YEAR_END,
            property=PropertyInput(id=_U(1), name="WEG", management_type="WEG"),
            units=units, owner_periods=owners, cost_positions=pos, plan_advances=(),
        )
        with pytest.raises(WegValidationError, match="gap|owner"):
            compute_weg_jahresabrechnung(inp)

    def test_missing_mea_escalates(self):
        from app.engine.rounding import DistributionError

        u1 = _U(11)
        o1 = _U(21)
        # Unit has no MEA -> no participating unit for the MEA key.
        units = (UnitInput(id=u1, number="1", usage_type="WOHNUNG", mea=None),)
        owners = (
            OwnerPeriodInput(unit_id=u1, owner_profile_id=o1, valid_from=date(2000, 1, 1), valid_to=None),
        )
        pos = (
            WegCostPositionInput(
                cost_entry_id=_U(50), category_id=_U(99), category_code="GRUNDSTEUER",
                label="x", gross_amount=Decimal("100.00"),
                service_period_from=YEAR_START, service_period_to=YEAR_END,
                owner_distribution_key="MEA", cost_group="UMLAGEFAEHIG",
            ),
        )
        inp = WegSettlementInput(
            period_start=YEAR_START, period_end=YEAR_END,
            property=PropertyInput(id=_U(1), name="WEG", management_type="WEG"),
            units=units, owner_periods=owners, cost_positions=pos, plan_advances=(),
        )
        with pytest.raises(DistributionError):
            compute_weg_jahresabrechnung(inp)

    def test_invalid_owner_key_escalates(self):
        u1 = _U(11)
        o1 = _U(21)
        units = (UnitInput(id=u1, number="1", usage_type="WOHNUNG", mea=Decimal("1000")),)
        owners = (
            OwnerPeriodInput(unit_id=u1, owner_profile_id=o1, valid_from=date(2000, 1, 1), valid_to=None),
        )
        pos = (
            WegCostPositionInput(
                cost_entry_id=_U(50), category_id=_U(99), category_code="X",
                label="x", gross_amount=Decimal("100.00"),
                service_period_from=YEAR_START, service_period_to=YEAR_END,
                owner_distribution_key="FANTASIE", cost_group="UMLAGEFAEHIG",
            ),
        )
        inp = WegSettlementInput(
            period_start=YEAR_START, period_end=YEAR_END,
            property=PropertyInput(id=_U(1), name="WEG", management_type="WEG"),
            units=units, owner_periods=owners, cost_positions=pos, plan_advances=(),
        )
        with pytest.raises(WegValidationError, match="owner_distribution_key"):
            compute_weg_jahresabrechnung(inp)


def test_refund_only_hausgeld_is_a_negative_payment_not_an_unpaid_zero():
    inp = TestGolden2Abrechnungsspitze()._scenario()
    inp = replace(inp, paid_advances=(OwnerAdvanceInput(_U(21), Decimal('-50.01')),))
    owner = _owner_result(compute_weg_jahresabrechnung(inp), _U(21))
    assert owner.account_balance == Decimal('5550.01')
    assert owner.payment_amount == Decimal('6050.01')


# ===========================================================================
# Golden 12: co-owner shares are ratios inside their unit (issue #404)
#   A co-owner is weighted by mea_share / (sum of the mea_share of the owners
#   active on the same days); a sole owner weighs 1 whatever mea_share says.
#   The unit's ownership timeline is split at every ownership boundary, so a
#   part-year overlap weights each span by its own days. A calculation is
#   refused (WegShareSumError) when the active shares of a unit sum to neither
#   1 nor 100 in some span of the settlement period.
#
#   All values by hand, 2025 (365 days: Jan-Jun 181, Jul-Dec 184), cost 1,000.00
#   by MEA, no cents lost: floors, then the remaining cents by largest remainder.
# ===========================================================================
def _co_owner_scenario(unit2_owners, *, unit1_share=None, cost="1000.00", sp=(YEAR_START, YEAR_END)):
    """Two units of 500 MEA: unit 1 owned by Anna alone, unit 2 by `unit2_owners`.

    `unit2_owners` is a list of (owner id, share, valid_from, valid_to); shares are
    strings or None, as they are stored in ownership_history.mea_share.
    """
    u1, u2 = _U(11), _U(12)
    units = (
        UnitInput(id=u1, number="1", usage_type="WOHNUNG", mea=Decimal("500")),
        UnitInput(id=u2, number="2", usage_type="WOHNUNG", mea=Decimal("500")),
    )
    owners = [OwnerPeriodInput(unit_id=u1, owner_profile_id=_U(21), valid_from=date(2000, 1, 1),
                               valid_to=None, mea_share=None if unit1_share is None else Decimal(unit1_share))]
    for owner, share, start, end in unit2_owners:
        owners.append(OwnerPeriodInput(unit_id=u2, owner_profile_id=owner, valid_from=start, valid_to=end,
                                       mea_share=None if share is None else Decimal(share)))
    pos = (
        WegCostPositionInput(
            cost_entry_id=_U(50), category_id=_U(99), category_code="GRUNDSTEUER",
            label="Grundsteuer", gross_amount=Decimal(cost),
            service_period_from=sp[0], service_period_to=sp[1],
            owner_distribution_key="MEA", cost_group="UMLAGEFAEHIG",
        ),
    )
    return WegSettlementInput(
        period_start=YEAR_START, period_end=YEAR_END,
        property=PropertyInput(id=_U(1), name="WEG", management_type="WEG"),
        units=units, owner_periods=tuple(owners), cost_positions=pos, plan_advances=(),
    )


def _whole_year(owner, share):
    return (owner, share, date(2000, 1, 1), None)


ANNA, BERND, CARLA, DORA = _U(21), _U(22), _U(23), _U(24)


def _owner_costs(result, *owners):
    return tuple(_owner_result(result, o).owner_cost_total for o in owners)


class TestGolden12CoOwnerShares:
    def test_table_50_50_splits_the_unit_share_in_half(self):
        # Unit 1 share 500.00 -> Anna. Unit 2 share 500.00 -> Bernd 50 / Carla 50 = 1/2 each.
        # Cell weights 1/2, 1/4, 1/4 -> 500.00 / 250.00 / 250.00 exactly.
        # (Before #404 the raw 50 outweighed Anna's 1: 9.90 / 495.05 / 495.05.)
        r = compute_weg_jahresabrechnung(_co_owner_scenario([_whole_year(BERND, "50"), _whole_year(CARLA, "50")]))
        assert _owner_costs(r, ANNA, BERND, CARLA) == (Decimal("500.00"), Decimal("250.00"), Decimal("250.00"))
        assert r.cost_total == Decimal("1000.00")

    @pytest.mark.parametrize("fraction,percent,expected", [
        # 0.6 / 0.4 of 500.00 = 300.00 / 200.00
        (("0.6", "0.4"), ("60", "40"), ("300.00", "200.00")),
        # 0.25 / 0.75 of 500.00 = 125.00 / 375.00
        (("0.25", "0.75"), ("25", "75"), ("125.00", "375.00")),
        # 0.5 / 0.5 was right before and stays right
        (("0.5", "0.5"), ("50", "50"), ("250.00", "250.00")),
    ])
    def test_share_scale_one_or_hundred_gives_the_same_split(self, fraction, percent, expected):
        for shares in (fraction, percent):
            r = compute_weg_jahresabrechnung(
                _co_owner_scenario([_whole_year(BERND, shares[0]), _whole_year(CARLA, shares[1])]))
            assert _owner_costs(r, ANNA, BERND, CARLA) == (Decimal("500.00"), *map(Decimal, expected))

    def test_three_co_owners_in_percent(self):
        # 50 / 30 / 20 of the unit's 500.00 = 250.00 / 150.00 / 100.00.
        r = compute_weg_jahresabrechnung(_co_owner_scenario(
            [_whole_year(BERND, "50"), _whole_year(CARLA, "30"), _whole_year(DORA, "20")]))
        assert _owner_costs(r, ANNA, BERND, CARLA, DORA) == tuple(
            Decimal(x) for x in ("500.00", "250.00", "150.00", "100.00"))

    def test_july_joiner_is_weighted_by_the_days_of_each_span(self):
        # One unit MEA 1000. Anna 0.5 all year, Bernd 0.5 from 01.07.
        # 01.01.-30.06. (181 days): Anna alone -> weight 1.
        # 01.07.-31.12. (184 days): Anna 1/2, Bernd 1/2.
        # Anna = 181/365 + 184/365 * 1/2 = 273/365, Bernd = 92/365.
        # 1000 * 273/365 = 747.945205...; 1000 * 92/365 = 252.054794...
        # floors 747.94 + 252.05 = 999.99, one cent left: remainders .5205 vs .4794 -> Anna.
        # (Before #404 the whole slice was weighted: 664.85 / 335.15.)
        u1 = _U(11)
        inp = WegSettlementInput(
            period_start=YEAR_START, period_end=YEAR_END,
            property=PropertyInput(id=_U(1), name="WEG", management_type="WEG"),
            units=(UnitInput(id=u1, number="1", usage_type="WOHNUNG", mea=Decimal("1000")),),
            owner_periods=(
                OwnerPeriodInput(unit_id=u1, owner_profile_id=ANNA, valid_from=date(2020, 1, 1),
                                 valid_to=None, mea_share=Decimal("0.5")),
                OwnerPeriodInput(unit_id=u1, owner_profile_id=BERND, valid_from=date(2025, 7, 1),
                                 valid_to=None, mea_share=Decimal("0.5")),
            ),
            cost_positions=_co_owner_scenario([]).cost_positions, plan_advances=(),
        )
        r = compute_weg_jahresabrechnung(inp)
        assert _owner_costs(r, ANNA, BERND) == (Decimal("747.95"), Decimal("252.05"))
        assert _cost_line(_owner_result(r, ANNA), "GRUNDSTEUER").time_share_days == 365
        assert _cost_line(_owner_result(r, BERND), "GRUNDSTEUER").time_share_days == 184

    @pytest.mark.parametrize("anna,bernd", [("0.7", "0.3"), ("70", "30")])
    def test_july_joiner_with_unequal_shares(self, anna, bernd):
        # Anna = 181/365 + 184/365 * 0.7 = 309.8/365, Bernd = 184/365 * 0.3 = 55.2/365.
        # 1000 * 309.8/365 = 848.767123...; 1000 * 55.2/365 = 151.232876...
        # floors 848.76 + 151.23 = 999.99, remainders .7123 vs .2876 -> the cent goes to Anna.
        u1 = _U(11)
        inp = WegSettlementInput(
            period_start=YEAR_START, period_end=YEAR_END,
            property=PropertyInput(id=_U(1), name="WEG", management_type="WEG"),
            units=(UnitInput(id=u1, number="1", usage_type="WOHNUNG", mea=Decimal("1000")),),
            owner_periods=(
                OwnerPeriodInput(unit_id=u1, owner_profile_id=ANNA, valid_from=date(2020, 1, 1),
                                 valid_to=None, mea_share=Decimal(anna)),
                OwnerPeriodInput(unit_id=u1, owner_profile_id=BERND, valid_from=date(2025, 7, 1),
                                 valid_to=None, mea_share=Decimal(bernd)),
            ),
            cost_positions=_co_owner_scenario([]).cost_positions, plan_advances=(),
        )
        r = compute_weg_jahresabrechnung(inp)
        assert _owner_costs(r, ANNA, BERND) == (Decimal("848.77"), Decimal("151.23"))

    def test_a_co_owner_swap_keeps_every_dated_share(self):
        # Dated ownership of #601/#663 is unchanged: shares add up to 1 in both spans.
        # Unit 2 share 500.00: Bernd 0.5 = 250.00; Carla 0.3 until 30.06. and Dora 0.3 from 01.07.
        # Carla 150 * 181/365 = 74.38; Dora 150 * 184/365 = 75.62; Carla+Dora must give 150.00.
        r = compute_weg_jahresabrechnung(_co_owner_scenario([
            _whole_year(BERND, "0.7"),
            (CARLA, "0.3", date(2000, 1, 1), date(2025, 6, 30)),
            (DORA, "0.3", date(2025, 7, 1), None),
        ]))
        # Bernd 350.00. Carla 500*0.3*181/365 = 74.383561..., Dora 500*0.3*184/365 = 75.616438...
        # floors 74.38 + 75.61 = 149.99, one cent: remainders .3561 vs .6438 -> Dora 75.62.
        assert _owner_costs(r, ANNA, BERND, CARLA, DORA) == tuple(
            Decimal(x) for x in ("500.00", "350.00", "74.38", "75.62"))

    @pytest.mark.parametrize("share", [None, "0.5", "7", "100", "0", "-1"])
    def test_a_sole_owner_weighs_one_whatever_the_share_says(self, share):
        r = compute_weg_jahresabrechnung(_co_owner_scenario(
            [_whole_year(BERND, "50"), _whole_year(CARLA, "50")], unit1_share=share))
        assert _owner_costs(r, ANNA, BERND, CARLA) == (Decimal("500.00"), Decimal("250.00"), Decimal("250.00"))

    @pytest.mark.parametrize("shares,total", [
        (("0.6", "0.3"), "0.9"),
        (("60", "30"), "90"),
        (("0.5", "0.4"), "0.9"),
        (("1", "1"), "2"),
        (("50", "60"), "110"),
        (("0.5", "0.5", "0.5"), "1.5"),
        (("0.3", "0.3", "0.3"), "0.9"),
        # Thirds rounded by the user to two places are not stored rounding noise.
        (("33.33", "33.33", "33.33"), "99.99"),
    ])
    def test_shares_adding_up_to_neither_one_nor_hundred_are_refused(self, shares, total):
        owners = [_whole_year(o, s) for o, s in zip((BERND, CARLA, DORA), shares)]
        with pytest.raises(weg_engine.WegShareSumError) as caught:
            compute_weg_jahresabrechnung(_co_owner_scenario(owners))
        exc = caught.value
        assert isinstance(exc, WegValidationError)
        assert (exc.unit_id, exc.unit_number) == (_U(12), "2")
        assert (exc.span_start, exc.span_end) == (YEAR_START, YEAR_END)
        assert exc.share_sum == Decimal(total)

    @pytest.mark.parametrize("shares", [
        ("0.5", "0.5"), ("50", "50"),
        # 1/3 + 2/3 as stored (NUMERIC(12,6)): the last digit is rounded, never refused.
        ("0.333333", "0.666667"), ("0.333333", "0.666666"), ("0.333334", "0.666666"),
        ("33.333333", "66.666667"), ("33.333333", "66.666666"), ("33.33", "66.67"),
        ("0.333333", "0.333333", "0.333333"), ("33.333333", "33.333333", "33.333333"),
        ("0.333334", "0.333333", "0.333333"),
        ("0.3333333333333333333333333333", "0.6666666666666666666666666667"),
        ("0.2", "0.3", "0.5"), ("20", "30", "50"),
    ])
    def test_shares_adding_up_to_one_or_hundred_within_stored_rounding_are_accepted(self, shares):
        owners = [_whole_year(o, s) for o, s in zip((BERND, CARLA, DORA), shares)]
        r = compute_weg_jahresabrechnung(_co_owner_scenario(owners))
        costs = _owner_costs(r, ANNA, *(o for o, _ in zip((BERND, CARLA, DORA), shares)))
        assert sum(costs, Decimal("0.00")) == Decimal("1000.00")
        assert costs[0] == Decimal("500.00")
        total_share = sum((Decimal(s) for s in shares), Decimal(0))
        for amount, share in zip(costs[1:], shares):
            exact = Fraction(500) * Fraction(Decimal(share)) / Fraction(total_share)
            assert abs(Fraction(amount) - exact) < Fraction(1, 100)

    def test_the_span_with_wrong_shares_is_named_when_the_other_span_is_fine(self):
        # Bernd 0.5 all year; Carla 0.5 until 30.06. (sum 1 in H1); Dora 0.3 from 01.07. (sum 0.8 in H2).
        with pytest.raises(weg_engine.WegShareSumError) as caught:
            compute_weg_jahresabrechnung(_co_owner_scenario([
                _whole_year(BERND, "0.5"),
                (CARLA, "0.5", date(2000, 1, 1), date(2025, 6, 30)),
                (DORA, "0.3", date(2025, 7, 1), None),
            ]))
        exc = caught.value
        assert (exc.unit_number, exc.span_start, exc.span_end, exc.share_sum) == (
            "2", date(2025, 7, 1), YEAR_END, Decimal("0.8"))

    def test_the_first_span_is_named_when_only_it_is_wrong(self):
        # Carla 0.3 until 30.06. (sum 0.8 in H1); Dora 0.5 from 01.07. (sum 1 in H2).
        with pytest.raises(weg_engine.WegShareSumError) as caught:
            compute_weg_jahresabrechnung(_co_owner_scenario([
                _whole_year(BERND, "0.5"),
                (CARLA, "0.3", date(2000, 1, 1), date(2025, 6, 30)),
                (DORA, "0.5", date(2025, 7, 1), None),
            ]))
        exc = caught.value
        assert (exc.span_start, exc.span_end, exc.share_sum) == (YEAR_START, date(2025, 6, 30), Decimal("0.8"))

    def test_wrong_shares_in_a_span_without_costs_still_refuse_the_calculation(self):
        # The cost only falls in H1 where the shares add up; the unit's H2 shares do not.
        with pytest.raises(weg_engine.WegShareSumError) as caught:
            compute_weg_jahresabrechnung(_co_owner_scenario([
                _whole_year(BERND, "0.5"),
                (CARLA, "0.5", date(2000, 1, 1), date(2025, 6, 30)),
                (DORA, "0.3", date(2025, 7, 1), None),
            ], sp=(YEAR_START, date(2025, 6, 30))))
        assert caught.value.span_start == date(2025, 7, 1)

    def test_wrong_shares_before_the_settlement_period_do_not_matter(self):
        # 2024: Bernd 0.5 + Carla 0.3 = 0.8, but the settlement covers 2025 only,
        # where Bernd 0.5 + Dora 0.5 = 1.
        r = compute_weg_jahresabrechnung(_co_owner_scenario([
            _whole_year(BERND, "0.5"),
            (CARLA, "0.3", date(2000, 1, 1), date(2024, 12, 31)),
            (DORA, "0.5", date(2025, 1, 1), None),
        ]))
        assert _owner_costs(r, ANNA, BERND, DORA) == (
            Decimal("500.00"), Decimal("250.00"), Decimal("250.00"))
        assert all(o.owner_profile_id != CARLA for o in r.owners)

    @pytest.mark.parametrize("shares,message", [
        ((None, "0.5"), "has no mea_share"),
        (("0.5", None), "has no mea_share"),
        (("0.5", "0"), "non-positive mea_share"),
        (("1.5", "-0.5"), "non-positive mea_share"),
    ])
    def test_missing_or_non_positive_shares_keep_their_own_error(self, shares, message):
        with pytest.raises(WegValidationError, match=message) as caught:
            compute_weg_jahresabrechnung(_co_owner_scenario(
                [_whole_year(BERND, shares[0]), _whole_year(CARLA, shares[1])]))
        assert not isinstance(caught.value, weg_engine.WegShareSumError)

    def test_a_refused_sum_would_still_normalise_to_the_issue_ratio(self):
        # 0.6 / 0.3 -> 2/3 and 1/3 of the unit's 500.00 = 333.33 / 166.67 (second row of the
        # issue). The sum check refuses it, so no result carries these amounts; the ratio is
        # what the weights of any accepted scale are made of.
        owners = [OwnerPeriodInput(unit_id=_U(12), owner_profile_id=o, valid_from=date(2000, 1, 1),
                                   valid_to=None, mea_share=Decimal(s))
                  for o, s in ((BERND, "0.6"), (CARLA, "0.3"))]
        from app.engine.periods import build_owner_cover

        unit = UnitInput(id=_U(12), number="2", usage_type="WOHNUNG", mea=Decimal("500"))
        spans = weg_engine._unit_owner_spans(unit, build_owner_cover(_U(12), YEAR_START, YEAR_END, owners))
        assert len(spans) == 1 and spans[0].share_sum == Decimal("0.9")
        assert sorted(spans[0].fractions.values()) == [Fraction(1, 3), Fraction(2, 3)]
