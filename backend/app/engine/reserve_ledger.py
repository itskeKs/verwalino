"""Deterministic cash reserve ledger; planned shares never increase cash holdings."""
from calendar import monthrange
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from app.engine.rounding import CENT, distribute_amount


def money(value: Decimal) -> Decimal:
    if not value.is_finite():
        raise ValueError('Reserve amounts must be finite')
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def reserve_totals(opening, contributions, withdrawals, overrides):
    opening, contributions, withdrawals, overrides = map(money, (opening, contributions, withdrawals, overrides))
    closing = money(opening + contributions - withdrawals + overrides)
    return {'opening_balance': format(opening, '.2f'), 'contributions_total': format(contributions, '.2f'),
            'withdrawals_total': format(withdrawals, '.2f'), 'overrides_total': format(overrides, '.2f'),
            'closing_balance': format(closing, '.2f'), 'overdrawn': closing < 0}


def planned_reserve_entries(total, cells, period_start):
    """Frozen MEA/co-owner cells, stable ID ties, then twelve cent-exact rates."""
    cells = sorted(cells)
    if not cells:
        if total:
            raise ValueError('Reserve plan has no owner/unit shares')
        return []
    annual = distribute_amount(money(total), [cell[2] for cell in cells])
    rows = []
    for (unit, owner, _weight), share in zip(cells, annual):
        rates = distribute_amount(share, [Decimal(1)] * 12)
        for offset, amount in enumerate(rates):
            month_number = period_start.year * 12 + period_start.month - 1 + offset
            year, month = divmod(month_number, 12)
            when = date(year, month + 1, min(period_start.day, monthrange(year, month + 1)[1]))
            rows.append({'kind': 'PLANNED', 'date': str(when), 'unit_id': unit,
                         'owner_profile_id': owner, 'amount': format(amount, '.2f')})
    return sorted(rows, key=lambda row: (row['date'], row['unit_id'], row['owner_profile_id']))
