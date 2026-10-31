"""Deterministic cash reserve ledger; planned shares never increase cash holdings."""
from calendar import monthrange
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from app.engine.rounding import CENT, distribute_amount


def money(value: Decimal) -> Decimal:
    if not value.is_finite():
        raise ValueError('Reserve amounts must be finite')
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def reserve_totals(opening, contributions, withdrawals, overrides, late_cash=Decimal('0.00')):
    opening, contributions, withdrawals, overrides = map(money, (opening, contributions, withdrawals, overrides))
    late_cash = money(late_cash)
    closing = money(opening + contributions - withdrawals + overrides + late_cash)
    return {'opening_balance': format(opening, '.2f'), 'contributions_total': format(contributions, '.2f'),
            'withdrawals_total': format(withdrawals, '.2f'), 'overrides_total': format(overrides, '.2f'),
            'late_cash_total': format(late_cash, '.2f'),
            'closing_balance': format(closing, '.2f'), 'overdrawn': closing < 0}


def plan_reserve_entries(payload, total, period_start):
    """Keep the frozen plan's owner cells for Soll, independent of later sales."""
    units = {u['id']: u for u in payload['inputs']['units']}
    cells = []
    for unit, values in sorted(units.items()):
        mea = Decimal(values['mea'] or '0')
        owners = [o for o in payload['inputs']['owner_periods'] if o['unit_id'] == unit
                  and o['from'] <= str(period_start) and (o['to'] is None or o['to'] >= str(period_start))]
        if not owners or mea <= 0:
            continue
        denominator = sum((Decimal(o['mea_share'] or '0') for o in owners), Decimal(0))
        for owner in owners:
            weight = mea if len(owners) == 1 else mea * Decimal(owner['mea_share']) / denominator
            cells.append((unit, owner['owner_profile_id'], weight))
    rows = planned_reserve_entries(total, cells, period_start)
    for row in rows:
        row['unit_number'] = units[row['unit_id']]['number']
    return rows


def hausgeld_reserve_share(signed, unit_ids, planned, plan_owners):
    """Use whole unit plan lines, including all co-owners; never the payer's line.

    A payer with several units uses their combined plan volumes. Round each cash
    booking HALF_UP independently so a Storno cancels the original exactly.
    Missing unit/plan coverage or a nonpositive denominator remains unassigned.
    """
    reserve = sum((Decimal(row['amount']) for row in planned if row['unit_id'] in unit_ids), Decimal(0))
    advance = sum((Decimal(row['annual_advance']) for row in plan_owners if row['unit_id'] in unit_ids), Decimal(0))
    return money(signed * reserve / advance) if advance > 0 else None


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
