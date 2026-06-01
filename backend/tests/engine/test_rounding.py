"""Unit tests for distribute_amount edge cases (ADR-0005 section 4.4)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.engine.rounding import CENT, DistributionError, distribute_amount


class TestDistributeAmountEdgeCases:
    def test_single_addressee_gets_all(self):
        assert distribute_amount(Decimal("123.45"), [Decimal("7")]) == [Decimal("123.45")]

    def test_exact_division_no_extra_cents(self):
        # 1200 over 75/50/25 -> 600/400/200 (Golden test 1).
        result = distribute_amount(
            Decimal("1200.00"), [Decimal("75"), Decimal("50"), Decimal("25")]
        )
        assert result == [Decimal("600.00"), Decimal("400.00"), Decimal("200.00")]

    def test_largest_remainder_thirds(self):
        # 1000 / 3 -> 333.34 / 333.33 / 333.33 (Golden test 2). Tie-break by
        # stable input order gives the first addressee the extra cent.
        result = distribute_amount(Decimal("1000.00"), [Decimal("1")] * 3)
        assert result == [Decimal("333.34"), Decimal("333.33"), Decimal("333.33")]
        assert sum(result) == Decimal("1000.00")

    def test_remainder_goes_to_larger_share_first(self):
        # 100.00 over weights 2 and 1 -> exact 66.6667 / 33.3333.
        # floors 66.66 / 33.33, remainder 1 cent -> larger fractional? both
        # .6667 and .3333 -> larger frac is 66.66's .6667 -> gets the cent.
        result = distribute_amount(Decimal("100.00"), [Decimal("2"), Decimal("1")])
        assert result == [Decimal("66.67"), Decimal("33.33")]
        assert sum(result) == Decimal("100.00")

    def test_negative_total_credit(self):
        result = distribute_amount(Decimal("-1000.00"), [Decimal("1")] * 3)
        assert sum(result) == Decimal("-1000.00")
        for s in result:
            assert s in (Decimal("-333.33"), Decimal("-333.34"))

    def test_zero_total(self):
        result = distribute_amount(Decimal("0.00"), [Decimal("3"), Decimal("7")])
        assert result == [Decimal("0.00"), Decimal("0.00")]

    def test_zero_weight_sum_raises(self):
        with pytest.raises(DistributionError, match="sum of weights is zero"):
            distribute_amount(Decimal("100.00"), [Decimal("0"), Decimal("0")])

    def test_empty_weights_raises(self):
        with pytest.raises(DistributionError, match="empty weight vector"):
            distribute_amount(Decimal("100.00"), [])

    def test_negative_weight_raises(self):
        with pytest.raises(DistributionError, match="negative weight"):
            distribute_amount(Decimal("100.00"), [Decimal("1"), Decimal("-1")])

    def test_zero_weight_addressee_gets_zero(self):
        # A zero-weight addressee (e.g. Stellplatz at a Wohnflaeche key, if it
        # were included) gets 0; the rest split the total.
        result = distribute_amount(
            Decimal("100.00"), [Decimal("1"), Decimal("0"), Decimal("1")]
        )
        assert result[1] == Decimal("0.00")
        assert sum(result) == Decimal("100.00")

    def test_many_addressees_sum_exact(self):
        # 50 unequal weights, awkward total -> sum still exact.
        weights = [Decimal(i + 1) for i in range(50)]
        result = distribute_amount(Decimal("9999.97"), weights)
        assert sum(result) == Decimal("9999.97")
        assert len(result) == 50

    def test_deterministic_repeat(self):
        weights = [Decimal("7"), Decimal("11"), Decimal("13"), Decimal("17")]
        a = distribute_amount(Decimal("1234.56"), weights)
        b = distribute_amount(Decimal("1234.56"), weights)
        assert a == b
