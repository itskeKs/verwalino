"""Full-property allocation remains independent of the statement window."""

import uuid
from dataclasses import replace
from datetime import date
from decimal import Decimal as D

import pytest

from app.engine.settlement import compute_bk_settlement
from app.engine.types import (
    CostPositionInput,
    HeatingPositionInput,
    HeatingSliceInput,
    LeasePeriodInput,
    OwnerPeriodInput,
    PropertyInput,
    SettlementInput,
    UnitInput,
)


def uid(n):
    return uuid.UUID(int=n)


def example(heating=False):
    start, end = date(2025, 1, 1), date(2025, 12, 31)
    units = tuple(
        UnitInput(uid(i), str(i), "WOHNUNG", area=D(60), consumption=D(1000))
        for i in (1, 2)
    )
    leases = (
        LeasePeriodInput(uid(11), uid(1), uid(91), date(2020, 1, 1), date(2025, 9, 30)),
        LeasePeriodInput(uid(12), uid(1), uid(92), date(2025, 10, 1), None),
        LeasePeriodInput(uid(13), uid(2), uid(93), date(2020, 1, 1), None),
    )
    owners = tuple(
        OwnerPeriodInput(uid(i), uid(80), date(2019, 1, 1), None) for i in (1, 2)
    )
    slices = tuple(
        HeatingSliceInput(uid(i), uid(u), a, b, uid(l), uid(80), D(60), D(c))
        for i, u, a, b, l, c in (
            (1, 1, start, date(2025, 9, 30), 11, 600),
            (2, 1, date(2025, 10, 1), end, 12, 400),
            (3, 2, start, end, 13, 1000),
        )
    )
    pos = CostPositionInput(
        uid(5),
        None,
        "HEIZUNG_ZENTRAL" if heating else "WASSER",
        "Kosten",
        D("10000.00") if heating else D("1000.00"),
        start,
        end,
        "WOHNFLAECHE" if heating else "VERBRAUCH",
        "UMLAGEFAEHIG",
        heating=HeatingPositionInput("HEATING", D("0.70"), "AREA", True, True, slices)
        if heating
        else None,
    )
    return SettlementInput(
        start,
        end,
        PropertyInput(uid(9), "Haus", "MIET"),
        uid(11),
        units,
        leases,
        owners,
        (pos,),
        D(0),
    )


@pytest.mark.parametrize(
    "heating,expected",
    [
        (False, [D("373.97"), D("126.03"), D("500.00")]),
        (True, [D("3221.92"), D("1778.08"), D("5000.00")]),
    ],
)
def test_tenant_change_keeps_full_property_consumption_and_cost_pool(heating, expected):
    inp = example(heating)
    amounts = []
    for lease in inp.lease_periods:
        full = replace(inp, target_lease_agreement_id=lease.lease_agreement_id)
        clipped = replace(
            full,
            period_start=max(inp.period_start, lease.valid_from),
            period_end=min(inp.period_end, lease.valid_to or inp.period_end),
        )
        assert (
            compute_bk_settlement(clipped).total_cost
            == compute_bk_settlement(full).total_cost
        )
        amounts.append(compute_bk_settlement(clipped).total_cost)
    assert amounts == expected
    assert sum(amounts) == inp.cost_positions[0].gross_amount


def test_cross_boundary_source_is_charged_once_across_two_years():
    inp = example()
    inp = replace(
        inp,
        lease_periods=(replace(inp.lease_periods[0], valid_to=None),),
        units=inp.units[:1],
    )
    pos = replace(
        inp.cost_positions[0],
        tenant_distribution_key="WOHNFLAECHE",
        gross_amount=D("3650.00"),
        service_period_from=date(2024, 7, 1),
        service_period_to=date(2025, 6, 30),
    )
    amounts = [
        compute_bk_settlement(
            replace(
                inp,
                cost_positions=(pos,),
                period_start=date(y, 1, 1),
                period_end=date(y, 12, 31),
            )
        ).total_cost
        for y in (2024, 2025)
    ]
    assert amounts == [D("1840.00"), D("1810.00")]
    assert sum(amounts) == pos.gross_amount


def test_heating_free_subwindows_reconcile_to_full_lease_share():
    inp = example(True)
    first = replace(inp, period_end=date(2025, 3, 31))
    second = replace(inp, period_start=date(2025, 4, 1), period_end=date(2025, 9, 30))
    assert compute_bk_settlement(first).total_cost + compute_bk_settlement(
        second
    ).total_cost == D("3221.92")


@pytest.mark.parametrize("month", [7, 12])
def test_delimited_heating_pool_uses_annual_unit_weights_but_only_service_period_occupants(month):
    inp = example(True)
    pos = replace(inp.cost_positions[0], service_period_from=date(2025, month, 1))
    amounts = []
    for lease in inp.lease_periods:
        result = compute_bk_settlement(replace(inp, cost_positions=(pos,),
            target_lease_agreement_id=lease.lease_agreement_id,
            period_start=max(inp.period_start, lease.valid_from),
            period_end=min(inp.period_end, lease.valid_to or inp.period_end)))
        amounts.append(result.total_cost)
    assert amounts[0] + amounts[1] == D("5000.00")
    assert amounts[2] == D("5000.00")
    if month == 12:
        assert amounts[0] == D("0.00")


def build_scenario(s):
    """Use the reconciliation generator for all time/consumption/heating keys."""
    from app.engine.periods import build_unit_cover
    from app.engine.types import ManualAmountInput

    units = tuple(
        UnitInput(
            u["id"],
            str(i),
            "WOHNUNG",
            area=u["area"],
            heated_area=u["area"],
            mea=u["mea"],
            person_count=i + 1,
            consumption=D(i + 1) * 100,
        )
        for i, u in enumerate(s["units"])
    )
    leases = tuple(LeasePeriodInput(**lease) for lease in s["leases"])
    owners = tuple(OwnerPeriodInput(**owner) for owner in s["owners"])
    measured_units = []
    for i, unit in enumerate(units):
        covers = build_unit_cover(unit.id, s["ys"], s["ye"], list(leases), list(owners))
        if i % 2 == 0 and len(covers) > 1:
            from app.engine.rounding import distribute_amount

            amounts = distribute_amount(
                unit.consumption, [D(j + 1) for j in range(len(covers))]
            )
            unit = replace(
                unit,
                consumption_periods=tuple(
                    (c.start, c.end, q) for c, q in zip(covers, amounts)
                ),
            )
        measured_units.append(unit)
    units = tuple(measured_units)
    positions = []
    keys = [
        "WOHNFLAECHE",
        "BEHEIZTE_FLAECHE",
        "EINHEIT",
        "MEA",
        "PERSONENZAHL",
        "MANUELLER_BETRAG",
        "VERBRAUCH",
        "HEATING",
    ]
    for i, key in enumerate(keys):
        p = s["positions"][i % len(s["positions"])]
        heating = None
        manual = ()
        total = abs(p["total"])
        if key == "MANUELLER_BETRAG":
            from app.engine.rounding import distribute_amount

            amounts = distribute_amount(total, [D(1)] * len(units))
            manual = tuple(
                ManualAmountInput(u.id, amount) for u, amount in zip(units, amounts)
            )
        if key == "HEATING":
            start, end = s["ys"], s["ye"]
            if start > end:
                continue
            slices = []
            for unit in units:
                covers = build_unit_cover(
                    unit.id, start, end, list(leases), list(owners)
                )
                for cover in covers:
                    slices.append(
                        HeatingSliceInput(
                            uid(10000 + len(slices)),
                            unit.id,
                            cover.start,
                            cover.end,
                            cover.lease_agreement_id,
                            cover.owner_profile_id,
                            unit.area,
                            D(cover.days) * (unit.id.int + 1),
                        )
                    )
            heating = HeatingPositionInput(
                "HEATING", D("0.70"), "AREA", True, False, tuple(slices)
            )
            key = "WOHNFLAECHE"
        positions.append(
            CostPositionInput(
                uid(5000 + i),
                None,
                str(i),
                "Kosten",
                total,
                p["sf"],
                p["st"],
                key,
                "UMLAGEFAEHIG",
                manual_amounts=manual,
                heating=heating,
            )
        )
    return units, leases, owners, positions


def test_random_windows_reconcile_per_unit_and_property():
    """Shuffled adjacent windows and tenant/vacancy cells conserve every cent."""
    from collections import defaultdict
    from datetime import timedelta
    import random
    from app.engine.distribution import distribute_position
    from app.engine.heating_settlement import distribute_heating_position
    from app.engine.periods import build_unit_cover
    from tests.engine.settlement_scenarios import rand_scenario

    checked = 0
    for seed in range(150):
        s = rand_scenario(seed)
        units, leases, owners, positions = build_scenario(s)
        bounds = (
            [s["ys"]]
            + [s["ys"] + timedelta(days=c) for c in s["cuts"]]
            + [s["ye"] + timedelta(days=1)]
        )
        windows = [(a, b - timedelta(days=1)) for a, b in zip(bounds, bounds[1:])]
        random.Random(seed).shuffle(windows)
        for pos in positions:
            if pos.service_period_from > s["ye"] or pos.service_period_to < s["ys"]:
                continue
            allocation_period = (
                (s["ys"], s["ye"])
                if pos.tenant_distribution_key == "VERBRAUCH"
                else None
            )
            start, end = allocation_period or (
                pos.service_period_from,
                pos.service_period_to,
            )
            covers = {
                u.id: build_unit_cover(u.id, start, end, list(leases), list(owners))
                for u in units
            }

            def cells(a, b):
                if pos.heating:
                    return distribute_heating_position(
                        pos, period_start=a, period_end=b
                    )[0]
                return distribute_position(
                    pos,
                    list(units),
                    covers,
                    period_start=a,
                    period_end=b,
                    allocation_period=allocation_period,
                )

            annual = cells(s["ys"], s["ye"])
            expected, actual = defaultdict(D), defaultdict(D)
            for cell in annual:
                expected[cell.unit_id] += cell.amount
            for a, b in windows:
                for cell in cells(a, b):
                    actual[cell.unit_id] += cell.amount
            assert actual == expected, (
                seed,
                pos.tenant_distribution_key,
                actual,
                expected,
            )
            if s["ys"] <= pos.service_period_from <= pos.service_period_to <= s["ye"]:
                assert sum(actual.values()) == pos.gross_amount, (seed, pos)
            for lease in leases:
                a, b = (
                    max(s["ys"], lease.valid_from),
                    min(s["ye"], lease.valid_to or s["ye"]),
                )
                if a > b or (
                    not (allocation_period or pos.heating)
                    and max(a, pos.service_period_from) > min(b, pos.service_period_to)
                ):
                    continue
                inp = SettlementInput(
                    s["ys"],
                    s["ye"],
                    PropertyInput(uid(9), "Haus", "MIET"),
                    lease.lease_agreement_id,
                    units,
                    leases,
                    owners,
                    (pos,),
                    D(0),
                )
                full = compute_bk_settlement(inp).total_cost
                assert (
                    compute_bk_settlement(
                        replace(inp, period_start=a, period_end=b)
                    ).total_cost
                    == full
                )
                pieces = D(0)
                for ws, we in windows:
                    cs, ce = max(a, ws), min(b, we)
                    if cs <= ce and (
                        (allocation_period or pos.heating)
                        or max(cs, pos.service_period_from)
                        <= min(ce, pos.service_period_to)
                    ):
                        pieces += compute_bk_settlement(
                            replace(inp, period_start=cs, period_end=ce)
                        ).total_cost
                assert pieces == full, (seed, lease, pieces, full)
            checked += 1
    assert checked == 1200


def test_random_lease_statements_credit_every_booked_advance_once():
    from datetime import timedelta
    import random
    from app.engine.mietkonto import AdvanceFilter, PaymentInput, derive_advance_total_for_period
    from tests.engine.settlement_scenarios import rand_scenario

    checked = 0
    for seed in range(150):
        s = rand_scenario(seed)
        r = random.Random(seed)
        for lease in s["leases"]:
            start, end = max(s["ys"], lease["valid_from"]), min(s["ye"], lease["valid_to"] or s["ye"])
            if start > end:
                continue
            months = list(range(start.month, end.month + 1)) + [None]
            payments = [PaymentInput(uid(i), "NK_VORAUSZAHLUNG", "IN", D(r.randint(1, 100000)) / 100,
                period_year=s["year"], period_month=month, lease_agreement_id=lease["lease_agreement_id"],
                is_booked=True) for i, month in enumerate(months)]
            boundaries = [start] + [s["ys"] + timedelta(days=c) for c in s["cuts"]
                                    if start < s["ys"] + timedelta(days=c) <= end] + [end + timedelta(days=1)]
            windows = [(a, b - timedelta(days=1)) for a, b in zip(boundaries, boundaries[1:])]
            r.shuffle(windows)
            filt = AdvanceFilter(frozenset({"NK_VORAUSZAHLUNG"}), s["year"], lease["lease_agreement_id"])
            credited = sum((derive_advance_total_for_period(payments, filt, a, b,
                lease_start=lease["valid_from"], lease_end=lease["valid_to"]) for a, b in windows), D(0))
            assert credited == sum((p.amount for p in payments), D(0)), (seed, lease, credited)
            checked += 1
    assert checked == 358


def test_random_late_lease_end_after_a_finalized_prefix_preserves_booked_advances():
    from datetime import timedelta
    import random
    from app.engine.mietkonto import (
        AdvanceFilter, PaymentInput, derive_advance_credits_for_period, uncredited_advance_total,
    )

    for seed in range(600):
        r = random.Random(seed)
        start = date(2024, 1, 1) + timedelta(days=r.randrange(400))
        end = start + timedelta(days=r.randrange(2, 600))
        cuts = sorted(r.sample(range(1, (end - start).days + 1), min(4, (end - start).days)))
        bounds = [start] + [start + timedelta(days=n) for n in cuts] + [end + timedelta(days=1)]
        prefix = r.randint(1, len(bounds) - 2)
        late_end = bounds[prefix] + timedelta(days=r.randrange((end - bounds[prefix]).days + 1))
        payments = [PaymentInput(uid(y * 100 + (m or 0)), "NK_VORAUSZAHLUNG", "IN",
            D(r.randint(1, 100000)) / 100, y, m, lease_agreement_id=uid(7))
            for y in range(start.year, end.year + 1) for m in [*range(1, 13), None]]
        windows = list(zip(bounds, bounds[1:]))
        suffix = windows[prefix:]
        r.shuffle(suffix)
        windows = windows[:prefix] + suffix
        frozen = []
        for index, (a, b) in enumerate(windows):
            current_end = None if index < prefix else late_end
            b = min(b - timedelta(days=1), current_end or end)
            if a > b:
                continue
            for year in range(a.year, b.year + 1):
                filt = AdvanceFilter(frozenset({"NK_VORAUSZAHLUNG"}), year, uid(7))
                frozen.extend(derive_advance_credits_for_period(payments, filt, a, b,
                    lease_start=start, lease_end=current_end, finalized_credits=tuple(frozen)))
        expected = sum((p.amount for p in payments), D(0)) - sum((uncredited_advance_total(
            payments, AdvanceFilter(frozenset({"NK_VORAUSZAHLUNG"}), year, uid(7)), start, late_end)
            for year in range(start.year, end.year + 1)), D(0))
        assert sum((c.amount for c in frozen), D(0)) == expected, seed
        assert all(c.amount >= 0 for c in frozen), seed


def test_consumption_interim_readings_split_only_the_changed_units_share():
    inp = example()
    first = replace(
        inp.units[0],
        consumption_periods=(
            (date(2025, 1, 1), date(2025, 9, 30), D(600)),
            (date(2025, 10, 1), date(2025, 12, 31), D(400)),
        ),
    )
    inp = replace(inp, units=(first, inp.units[1]))
    amounts = [
        compute_bk_settlement(replace(inp, target_lease_agreement_id=uid(n))).total_cost
        for n in (11, 12, 13)
    ]
    assert amounts == [D("300.00"), D("200.00"), D("500.00")]
    assert sum(amounts) == D("1000.00")


def test_missing_interim_reading_splits_known_unit_consumption_by_days():
    from app.engine.meter_consumption import MeterReadingInput, split_consumption
    from app.engine.periods import build_unit_cover

    inp = example()
    readings = [
        MeterReadingInput(uid(100), inp.period_start, D(0)),
        MeterReadingInput(uid(101), inp.period_end, D(1000)),
    ]
    covers = build_unit_cover(
        uid(1),
        inp.period_start,
        inp.period_end,
        list(inp.lease_periods),
        list(inp.owner_periods),
    )
    quantities = split_consumption(readings, inp.period_start, inp.period_end, covers).periods
    assert sum(q for _, _, q in quantities) == D(1000)
    assert quantities[0][2] / D(1000) == D(273) / D(365)
    readings.append(MeterReadingInput(uid(102), date(2025, 9, 30), D(600)))
    assert [
        q
        for _, _, q in split_consumption(
            readings, inp.period_start, inp.period_end, covers
        ).periods
    ] == [D(600), D(400)]


@pytest.mark.parametrize("interim", [False, True])
def test_december_consumption_source_does_not_charge_the_departed_tenant(interim):
    inp = example()
    if interim:
        inp = replace(inp, units=(replace(inp.units[0], consumption_periods=(
            (inp.period_start, date(2025, 9, 30), D(600)),
            (date(2025, 10, 1), inp.period_end, D(400)),
        )), inp.units[1]))
    pos = replace(inp.cost_positions[0], gross_amount=D(100), service_period_from=date(2025, 12, 1))
    amounts = []
    for lease in inp.lease_periods:
        full = replace(inp, cost_positions=(pos,), target_lease_agreement_id=lease.lease_agreement_id)
        clipped = replace(full, period_start=max(inp.period_start, lease.valid_from),
                          period_end=min(inp.period_end, lease.valid_to or inp.period_end))
        assert compute_bk_settlement(full).total_cost == compute_bk_settlement(clipped).total_cost
        amounts.append(compute_bk_settlement(full).total_cost)
    assert amounts == [D("0.00"), D("50.00"), D("50.00")]


def test_half_year_consumption_invoices_keep_their_own_cost_periods():
    inp = example()
    positions = (
        replace(inp.cost_positions[0], gross_amount=D(600), service_period_to=date(2025, 6, 30)),
        replace(inp.cost_positions[0], cost_entry_id=uid(6), gross_amount=D(400), service_period_from=date(2025, 7, 1)),
    )
    amounts = [compute_bk_settlement(replace(inp, cost_positions=positions,
        target_lease_agreement_id=uid(n))).total_cost for n in (11, 12, 13)]
    assert amounts == [D("400.00"), D("100.00"), D("500.00")]


def test_in_period_snapshot_bytes_match_f3869e1_for_963_runs():
    import hashlib
    import json
    from tests.engine.settlement_scenarios import in_period_fingerprints

    records = in_period_fingerprints()
    assert len(records) == 963
    assert (
        hashlib.sha256(json.dumps(records).encode()).hexdigest()
        == "b0a354c6c3df5eb71372980ebd0ff20ff968701a9069e6d9eb3e5586a568a8cf"
    )


def test_cross_boundary_consumption_pool_is_delimited_before_interim_split():
    inp = example()
    unit = replace(
        inp.units[0],
        consumption_periods=(
            (date(2025, 1, 1), date(2025, 9, 30), D(600)),
            (date(2025, 10, 1), date(2025, 12, 31), D(400)),
        ),
    )
    pos = replace(
        inp.cost_positions[0],
        gross_amount=D("3650.00"),
        service_period_from=date(2024, 7, 1),
        service_period_to=date(2025, 6, 30),
    )
    inp = replace(
        inp,
        units=(unit,),
        cost_positions=(pos,),
        lease_periods=(
            replace(inp.lease_periods[0], valid_from=date(2025, 1, 1), valid_to=None),
        ),
    )
    assert compute_bk_settlement(inp).total_cost == D("1810.00")


def test_excluded_advances_are_a_snapshot_notice_without_changing_the_balance():
    from app.engine.snapshot import build_snapshot_payload

    inp = replace(example(), uncredited_advance_payments_total=D("1234.56"))
    result = compute_bk_settlement(inp)
    notice, = [line for line in result.lines if line.line_type == "SUBTOTAL"]
    assert notice.label == "Nicht angerechnet: Zahlungen außerhalb des Mietverhältnisses: 1.234,56 €"
    assert notice.amount == 0
    assert result.balance == result.total_cost - result.advance_payments_total
    assert build_snapshot_payload(inp, result)["result"]["lines"][-1]["label"] == notice.label


def test_heating_free_window_freezes_its_selected_amount_for_the_renderer():
    from app.engine.snapshot import build_snapshot_payload

    inp = replace(example(True), period_end=date(2025, 3, 31))
    result = compute_bk_settlement(inp)
    payload = build_snapshot_payload(inp, result)
    row = next(
        r
        for r in payload["heating_costs"][0]["rows"]
        if r["lease_agreement_id"] == str(uid(11))
    )
    assert row["amount_due"] == "3221.92"
    assert row["statement_amount"] == str(result.total_cost) == "1062.17"
    assert row["statement_from"] == "2025-01-01"
    assert row["statement_to"] == "2025-03-31"


def test_interim_consumption_trace_uses_consumption_without_a_second_day_factor():
    inp = example()
    first = replace(
        inp.units[0],
        consumption_periods=(
            (date(2025, 1, 1), date(2025, 9, 30), D(600)),
            (date(2025, 10, 1), date(2025, 12, 31), D(400)),
        ),
    )
    line = compute_bk_settlement(replace(inp, units=(first, inp.units[1]))).lines[0]
    assert line.amount == D("300.00")
    assert line.distribution_base_share == D(600)
    assert line.distribution_base_total == D(2000)
    assert line.time_share_days is None
    assert line.time_share_total_days is None


def test_free_consumption_window_can_cross_the_property_year():
    inp = example()
    unit = inp.units[0]
    lease = replace(inp.lease_periods[0], valid_to=None)
    pos = replace(
        inp.cost_positions[0],
        gross_amount=D("3650.00"),
        service_period_from=date(2025, 4, 1),
        service_period_to=date(2026, 3, 31),
    )
    inp = replace(
        inp,
        property=replace(inp.property, fiscal_year_start_month=4),
        units=(unit,),
        lease_periods=(lease,),
        cost_positions=(pos,),
    )
    inp = replace(inp, advance_payments_total=D("100.00"))
    result = compute_bk_settlement(inp)
    assert result.total_cost == D("2750.00")
    assert result.balance == D("2650.00")
    assert (
        len([line for line in result.lines if line.line_type == "ADVANCE_PAYMENT"]) == 1
    )
    from app.engine.snapshot import build_snapshot_payload

    payload = build_snapshot_payload(inp, result)
    assert len(payload["inputs"]["property_periods"]) == 2
