"""Read the frozen WEG result for both the page and the PDF; never recalculate money."""
from __future__ import annotations


def owner_balance_results(payload: dict | None) -> list[dict]:
    if not payload or payload.get('run_type') != 'WEG_JAHRESABRECHNUNG':
        return []
    inputs = payload.get('inputs', {})
    units = {u['id']: u['number'] for u in inputs.get('units', [])}
    period = payload['period']
    results = []
    for owner in payload.get('result', {}).get('owners', []):
        # Legacy snapshots retain their original render path and digest.
        if owner.get('payment_amount') is None:
            continue
        result = {key: owner[key] for key in (
            'owner_profile_id', 'owner_cost_total', 'abrechnungsspitze',
            'plan_soll', 'paid', 'account_balance', 'payment_amount')}
        result['ownership_periods'] = [
            {'unit_number': units[p['unit_id']],
             'valid_from': max(p['from'], period['start']),
             'valid_to': min(p['to'] or period['end'], period['end'])}
            for p in inputs.get('owner_periods', [])
            if p['owner_profile_id'] == owner['owner_profile_id']
            and p['from'] <= period['end'] and (p['to'] is None or p['to'] >= period['start'])
        ]
        results.append(result)
    return results
