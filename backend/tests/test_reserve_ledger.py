"""Reserve API and frozen annual statements, on synthetic WEG records."""
import pytest
from decimal import Decimal
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.test_m4_weg import _seed_weg, _create_and_finalize_plan
from tests.financial_coverage_helpers import attest_fixture
from tests.test_ownership_change import (
    ownership_world as ownership_world, ownership_client, create_owner, end_owner,
)


async def reserve_world(api, seed):
    world = _seed_weg(seed)
    plan = await _create_and_finalize_plan(api, world)
    response = await api.post('/api/weg-settlements', json={
        'property_id': world['property'], 'economic_plan_id': plan,
        'period_start': '2025-01-01', 'period_end': '2025-12-31'})
    assert response.status_code == 201, response.text
    world['run'] = response.json()['id']
    world['plan'] = plan
    return world


@pytest.mark.asyncio
async def test_ledger_separates_planned_from_cash_and_requires_override_reason(client_a_admin, seed_data):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    path = f"/api/weg-settlements/{w['run']}/reserve-ledger"
    response = await api.get(path)
    assert response.status_code == 200, response.text
    assert response.json()['planned_contributions_total'] == '6000.00'
    assert response.json()['contributions_total'] == '0.00'
    override = path + '/overrides'
    for reason in ('', ' '):
        refused = await api.post(override, json={'component': 'OPENING', 'amount': '20000.00', 'reason': reason})
        assert refused.status_code == 422
        assert refused.json()['detail']['code'] == 'RESERVE_CORRECTION_REASON_REQUIRED'
    response = await api.post(override, json={'component': 'OPENING', 'amount': '20000.00', 'reason': 'Imported bank-confirmed balance'})
    assert response.status_code == 201, response.text
    assert (await api.get(path)).json()['closing_balance'] == '20000.00'


@pytest.mark.asyncio
async def test_ledger_foreign_ids_and_readonly_writes_are_refused(client_a_admin, client_b_admin, client_a_readonly, seed_data):
    w = await reserve_world(client_a_admin, seed_data)
    path = f"/api/weg-settlements/{w['run']}/reserve-ledger"
    assert (await client_b_admin.get(path)).status_code == 404
    body = {'component': 'ADJUSTMENT', 'amount': '1.00', 'reason': 'Reviewed interest'}
    assert (await client_b_admin.post(path + '/overrides', json=body)).status_code == 404
    assert (await client_a_readonly.post(path + '/overrides', json=body)).status_code == 403


@pytest.mark.asyncio
async def test_hidden_reserve_history_has_the_same_answer_as_a_missing_run(client_a_admin, seed_data, monkeypatch):
    import uuid
    from unittest.mock import AsyncMock
    from app.repositories.reserve_ledger_repo import ReserveLedgerRepository
    w = await reserve_world(client_a_admin, seed_data)
    missing = await client_a_admin.get(f'/api/weg-settlements/{uuid.uuid4()}/reserve-ledger')
    class HiddenHistory(Exception):
        sqlstate = '42501'
    monkeypatch.setattr(ReserveLedgerRepository, 'finalized', AsyncMock(side_effect=DBAPIError(None, None, HiddenHistory())))
    hidden = await client_a_admin.get(f"/api/weg-settlements/{w['run']}/reserve-ledger")
    assert hidden.status_code == missing.status_code == 404
    assert hidden.json() == missing.json()


@pytest.mark.asyncio
async def test_reserve_history_helper_refuses_missing_properties(client_a_admin, seed_data, app_rw_session):
    import uuid
    w = await reserve_world(client_a_admin, seed_data)
    await app_rw_session.execute(text("SELECT set_config('app.current_org_id',:org,true)"),
        {'org': str(seed_data['org_a_id'])})
    await app_rw_session.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),
        {'actor': str(seed_data['user_a_admin_id'])})
    statement = text("SELECT reserve_history_access(:org,:property,'2026-12-31')")
    assert await app_rw_session.scalar(statement, {'org': seed_data['org_a_id'], 'property': w['property']}) is None
    with pytest.raises(DBAPIError) as hidden:
        async with app_rw_session.begin_nested():
            await app_rw_session.execute(statement, {'org': seed_data['org_a_id'], 'property': uuid.uuid4()})
    assert hidden.value.orig.sqlstate == '42501'


@pytest.mark.asyncio
@pytest.mark.parametrize('storno', [False, True])
async def test_late_hausgeld_uses_frozen_plan_and_storno_amount_after_source_changes(storno):
    from types import SimpleNamespace as S
    from unittest.mock import AsyncMock
    from datetime import date
    from app.services.reserve_ledger_service import ReserveLedgerService
    start, end = date(2025, 1, 1), date(2025, 12, 31)
    payload = {'inputs': {'units': [{'id': 'a', 'number': '1', 'mea': '2'}, {'id': 'b', 'number': '2', 'mea': '1'}],
        'owner_periods': [{'unit_id': unit, 'owner_profile_id': owner, 'from': '2020-01-01', 'to': None, 'mea_share': None}
                         for unit, owner in [('a', 'payer'), ('b', 'other')]]},
        'result': {'owners': [{'unit_id': unit, 'owner_profile_id': owner, 'annual_advance': '100.00'}
                             for unit, owner in [('a', 'payer'), ('b', 'other')]]}}
    original = S(id='original-plan', snapshot_payload=payload, reserve_contribution_total=Decimal('75'), period_start=start, period_end=end)
    replacement = S(id='replacement-plan', snapshot_payload=payload, reserve_contribution_total=Decimal('150'), period_start=start, period_end=end)
    frozen = S(id='closed', status='finalized', period_start=start, period_end=end, management_mandate_id=None,
        snapshot_payload={'reserve_ledger': {'closing_balance': '50.00', 'economic_plan_id': original.id,
            'entries': [{'kind': 'CONTRIBUTION', 'payment_id': 'original-cash', 'amount': '50.00'}]}})
    run = S(id='next', status='draft', period_start=date(2026, 1, 1), period_end=date(2026, 12, 31),
        management_mandate_id=None, economic_plan_id=None)
    cash = S(id='late-cash', payment_type='HAUSGELD_VORSCHUSS', amount=Decimal('100'), direction='OUT' if storno else 'IN',
        value_date=date(2025, 8, 5), owner_profile_id='payer', bank_account_id='bank',
        reverses_id='original-cash' if storno else None, reversed_by_id=None)
    service = ReserveLedgerService(None, 'org', 'actor')
    service.repo = S(finalized=AsyncMock(return_value=[(frozen, None)]), current_scope=AsyncMock(return_value=None),
        plans=AsyncMock(return_value=[]), entries=AsyncMock(return_value=[]), payments=AsyncMock(return_value=[cash]),
        owner_units=AsyncMock(return_value={cash.id: {'a', 'b'} if storno else {'a'}}),
        cash_plans=AsyncMock(return_value=[replacement, original]))
    ledger = await service.read(run)
    assert ledger['late_cash_total'] == ('-50.00' if storno else '50.00')
    assert ledger['closing_balance'] == ('0.00' if storno else '100.00')


@pytest.mark.asyncio
async def test_same_period_frozen_ledger_cannot_replace_another_accessible_scope():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from datetime import date
    from app.services.reserve_ledger_service import ReserveLedgerService
    scope_a, scope_b = SimpleNamespace(id='a', whole_property=False), SimpleNamespace(id='b', whole_property=False)
    frozen = SimpleNamespace(status='finalized', period_start=date(2025, 1, 1), period_end=date(2025, 12, 31),
        snapshot_payload={'reserve_ledger': {'closing_balance': '9999.00'}}, management_mandate_id=None)
    run = SimpleNamespace(id='draft-b', status='draft', period_start=frozen.period_start, period_end=frozen.period_end,
        economic_plan_id=None, management_mandate_id=None)
    service = ReserveLedgerService(None, 'org', 'actor')
    service.repo = SimpleNamespace(finalized=AsyncMock(return_value=[(frozen, scope_a)]),
        current_scope=AsyncMock(return_value=scope_b), plans=AsyncMock(return_value=[]),
        owner_units=AsyncMock(return_value={}),
        entries=AsyncMock(return_value=[]), payments=AsyncMock(return_value=[]))
    ledger = await service.read(run)
    assert ledger['closing_balance'] == '0.00' and not ledger['frozen']


async def payment(api, w, amount, *, kind='RUECKLAGENANTEIL', direction='IN', when='2025-02-01'):
    body = {'bank_account_id': w['bank_giro'], 'direction': direction, 'amount': amount,
            'value_date': when, 'payment_type': kind, 'period_year': int(when[:4]), 'period_month': int(when[5:7])}
    if direction == 'IN' or kind == 'HAUSGELD_VORSCHUSS':
        body['owner_profile_id'] = w['owner_a']
    response = await api.post('/api/payments', json=body)
    assert response.status_code == 201, response.text
    identifier = response.json()['id']
    response = await api.post(f'/api/payments/{identifier}/book')
    assert response.status_code == 200, response.text
    return identifier


def paid(w):
    return {'paid_advances': [{'owner_profile_id': w[key], 'paid_total': '0.00'} for key in ('owner_a', 'owner_b')]}


async def finalize(api, w):
    await attest_fixture(api, 'settlement', w['run'], paid(w))
    response = await api.post(f"/api/weg-settlements/{w['run']}/finalize", json=paid(w))
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_hausgeld_uses_only_its_frozen_reserve_share_and_reversal_net_cash(client_a_admin, seed_data):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    identifier = await payment(api, w, '1300.00', kind='HAUSGELD_VORSCHUSS')
    path = f"/api/weg-settlements/{w['run']}/reserve-ledger"
    first = (await api.get(path)).json()
    assert first['contributions_total'] == '600.00'
    # An unrelated cash movement and the synthetic reserve cost do not fund reserves.
    await payment(api, w, '500.00', kind='SONDERZAHLUNG')
    assert (await api.get(path)).json()['contributions_total'] == '600.00'
    reversed_payment = await api.post(f'/api/payments/{identifier}/reverse', json={'note': 'Incorrect receipt'})
    assert reversed_payment.status_code == 201, reversed_payment.text
    assert (await api.get(path)).json()['contributions_total'] == '0.00'


@pytest.mark.asyncio
async def test_finalized_year_freezes_cash_and_carries_closing_into_next_period(client_a_admin, seed_data, migrator_session):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    path = f"/api/weg-settlements/{w['run']}/reserve-ledger"
    await api.post(path + '/overrides', json={'component': 'OPENING', 'amount': '20000.00', 'reason': 'Imported balance'})
    await payment(api, w, '6000.00')
    result = await finalize(api, w)
    frozen = (await api.get(path)).json()
    assert frozen['closing_balance'] == '26000.00'
    assert frozen['frozen'] is True
    # Preserve the issued opening; expose late cash separately in the next period.
    late = await payment(api, w, '999.00', when='2025-12-31')
    assert (await api.get(path)).json() == frozen
    assert (await api.get(f"/api/weg-settlements/{w['run']}")).json()['snapshot_sha256'] == result['snapshot_sha256']
    response = await api.post('/api/economic-plans', json={'property_id': w['property'], 'plan_year': 2026,
        'period_start': '2026-01-01', 'period_end': '2026-12-31'})
    assert response.status_code == 201, response.text
    plan = response.json()['id']
    values = {'planned_costs': [], 'reserve_contribution_total': '1200.00'}
    await attest_fixture(api, 'economic_plan', plan, values)
    assert (await api.post(f'/api/economic-plans/{plan}/finalize', json=values)).status_code == 200
    response = await api.post('/api/weg-settlements', json={'property_id': w['property'], 'economic_plan_id': plan,
        'period_start': '2026-01-01', 'period_end': '2026-12-31'})
    assert response.status_code == 201, response.text
    next_run = response.json()['id']
    ledger = (await api.get(f'/api/weg-settlements/{next_run}/reserve-ledger')).json()
    assert ledger['opening_balance'] == '26000.00'
    assert ledger['opening_source_run_id'] == w['run']
    assert ledger['late_cash_total'] == '999.00'
    assert ledger['closing_balance'] == '26999.00'
    assert next(row for row in ledger['entries'] if row.get('payment_id') == late) == {
        'kind': 'LATE_CASH', 'component': 'CONTRIBUTION', 'date': '2025-12-31',
        'amount': '999.00', 'payment_id': late, 'bank_account_id': w['bank_giro'],
        'owner_profile_id': w['owner_a'],
    }
    next_world = {**w, 'run': next_run}
    next_result = await finalize(api, next_world)
    lines = next_result['lines']
    assert next(row['sort_order'] for row in lines if row['line_type'] == 'RESERVE_OPENING') < next(
        row['sort_order'] for row in lines if row['label'] == 'Nachträgliche Buchungen zum abgeschlossenen Jahr')
    assert (await api.get(f'/api/weg-settlements/{next_run}/reserve-ledger')).json()['closing_balance'] == '26999.00'
    # A report for the issued year consumes the exact same frozen ledger.
    response = await api.post('/api/weg-settlements/asset-reports', json={
        'property_id': w['property'], 'period_start': '2025-01-01', 'period_end': '2025-12-31'})
    assert response.status_code == 201, response.text
    report = response.json()['id']
    report_values = {'bank_balances': [{'bank_account_id': w[key], 'label': key, 'account_type': kind, 'balance': '0.00'}
                      for key, kind in [('bank_giro', 'LAUFEND'), ('bank_ruecklage', 'RUECKLAGE')]],
                     'receivables': [], 'payables': []}
    await attest_fixture(api, 'settlement', report, report_values)
    response = await api.post(f'/api/weg-settlements/asset-reports/{report}/finalize', json=report_values)
    assert response.status_code == 200, response.text
    assert next(row['amount'] for row in response.json()['lines'] if row['line_type'] == 'RESERVE_CLOSING') == '26000.00'
    stored = await migrator_session.scalar(text('SELECT snapshot_payload FROM settlement_run WHERE id=CAST(:id AS uuid)'), {'id': report})
    assert stored['reserve_ledger']['closing_balance'] == '26000.00'
    response = await api.post('/api/weg-settlements/asset-reports', json={
        'property_id': w['property'], 'period_start': '2026-01-01', 'period_end': '2026-12-31'})
    assert response.status_code == 201, response.text
    report = response.json()['id']
    await attest_fixture(api, 'settlement', report, report_values)
    response = await api.post(f'/api/weg-settlements/asset-reports/{report}/finalize', json=report_values)
    assert response.status_code == 200, response.text
    document = await api.get(f'/api/weg-settlements/asset-reports/{report}/pdf')
    assert document.status_code == 200, document.text
    content = pdf_text(document.content)
    assert 'Nachträgliche Buchungen zum abgeschlossenen Jahr' in content
    assert '999,00 EUR' in content
    assert '26.000,00 EUR' in content and '26.999,00 EUR' in content
    amounts = {line['line_type']: Decimal(line['amount']) for line in response.json()['lines']}
    assert amounts['RESERVE_OPENING'] + amounts['RESERVE_CONTRIBUTION'] - amounts['RESERVE_WITHDRAWAL'] + amounts['RESERVE_LATE_CASH'] == amounts['RESERVE_CLOSING'] == Decimal('26999.00')


async def ownership_plan(api, w, year, reserve='365.00'):
    response = await api.post('/api/economic-plans', json={
        'property_id': w['property'], 'plan_year': year,
        'period_start': f'{year}-01-01', 'period_end': f'{year}-12-31'})
    assert response.status_code == 201, response.text
    plan = response.json()['id']
    values = {'planned_costs': [{'category_code': 'WEG_VERWALTERKOSTEN', 'label': 'Annual cost',
        'planned_amount': '365.00', 'cost_group': 'NICHT_UMLAGEFAEHIG'}], 'reserve_contribution_total': reserve}
    await attest_fixture(api, 'economic_plan', plan, values)
    assert (await api.post(f'/api/economic-plans/{plan}/finalize', json=values)).status_code == 200
    return plan


@pytest.mark.asyncio
async def test_buyer_hausgeld_uses_unit_ratio_and_allows_attestation_compute_and_finalize(ownership_world):
    w = ownership_world
    async with ownership_client(w) as api:
        seller = await create_owner(api, w)
        plan = await ownership_plan(api, w, 2026)
        await end_owner(api, seller)
        await create_owner(api, w, 'buyer', valid_from='2026-07-01')
        response = await api.post('/api/bank-accounts', json={
            'property_id': w['property'], 'account_type': 'LAUFEND', 'label': 'Synthetic change-month cash'})
        assert response.status_code == 201, response.text
        cash_world = {'bank_giro': response.json()['id'], 'owner_a': w['buyer']}
        buyer_payment = await payment(api, cash_world, '100.00', kind='HAUSGELD_VORSCHUSS', when='2026-08-05')
        response = await api.post('/api/weg-settlements', json={
            'property_id': w['property'], 'economic_plan_id': plan,
            'period_start': '2026-01-01', 'period_end': '2026-12-31'})
        assert response.status_code == 201, response.text
        run = response.json()['id']
        response = await api.get(f'/api/weg-settlements/{run}/reserve-ledger')
        assert response.status_code == 200, response.text
        assert response.json()['contributions_total'] == '50.00'
        assert next(row['amount'] for row in response.json()['entries'] if row.get('payment_id') == buyer_payment) == '50.00'
        # Attribute each party's cash using ownership on its value date.
        for payer, when in [('seller', '2026-06-30'), ('buyer', '2026-07-15')]:
            await payment(api, {**cash_world, 'owner_a': w[payer]}, '10.00', kind='HAUSGELD_VORSCHUSS', when=when)
        values = {'paid_advances': [{'owner_profile_id': w[payer], 'paid_total': '0.00'} for payer in ('seller', 'buyer')]}
        await attest_fixture(api, 'settlement', run, values)
        for action in ('compute', 'finalize'):
            response = await api.post(f'/api/weg-settlements/{run}/{action}', json=values)
            assert response.status_code == 200, response.text
        assert (await api.get(f'/api/weg-settlements/{run}/reserve-ledger')).json()['contributions_total'] == '60.00'
        response = await api.post('/api/weg-settlements/asset-reports', json={
            'property_id': w['property'], 'period_start': '2026-01-01', 'period_end': '2026-12-31'})
        assert response.status_code == 201, response.text
        report = response.json()['id']
        report_values = {'bank_balances': [{'bank_account_id': cash_world['bank_giro'], 'label': 'Cash',
            'account_type': 'LAUFEND', 'balance': '120.00'}], 'receivables': [], 'payables': []}
        await attest_fixture(api, 'settlement', report, report_values)
        for action in ('compute', 'finalize'):
            response = await api.post(f'/api/weg-settlements/asset-reports/{report}/{action}', json=report_values)
            assert response.status_code == 200, response.text
        # The buyer's next-year cash uses the unit's current cash-year ratio.
        next_plan = await ownership_plan(api, w, 2027, reserve='1095.00')
        await payment(api, cash_world, '100.00', kind='HAUSGELD_VORSCHUSS', when='2027-02-01')
        response = await api.post('/api/weg-settlements', json={
            'property_id': w['property'], 'economic_plan_id': next_plan,
            'period_start': '2027-01-01', 'period_end': '2027-12-31'})
        assert response.status_code == 201, response.text
        response = await api.get(f"/api/weg-settlements/{response.json()['id']}/reserve-ledger")
        assert response.status_code == 200, response.text
        assert response.json()['contributions_total'] == '75.00'
        assert response.json()['late_cash_total'] == '0.00'


@pytest.mark.asyncio
async def test_unknown_hausgeld_payer_is_accepted_and_listed_without_plan_assignment(client_a_admin, seed_data):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    person = await api.post('/api/persons', json={'first_name': 'Unknown', 'last_name': 'Synthetic payer'})
    assert person.status_code == 201, person.text
    owner = await api.post('/api/owner-profiles', json={'person_id': person.json()['id']})
    assert owner.status_code == 201, owner.text
    response = await api.post('/api/payments', json={'bank_account_id': w['bank_giro'], 'direction': 'IN',
        'amount': '100.00', 'value_date': '2025-08-05', 'payment_type': 'HAUSGELD_VORSCHUSS',
        'owner_profile_id': owner.json()['id'], 'period_year': 2025, 'period_month': 8})
    assert response.status_code == 201, response.text
    cash = response.json()['id']
    response = await api.post(f'/api/payments/{cash}/book')
    assert response.status_code == 200, response.text
    ledger = (await api.get(f"/api/weg-settlements/{w['run']}/reserve-ledger")).json()
    assert ledger['unlinked_payment_ids'] == [cash]
    assert next(row for row in ledger['entries'] if row.get('payment_id') == cash)['kind'] == 'UNASSIGNED'


@pytest.mark.asyncio
@pytest.mark.parametrize('known_owner', [False, True])
async def test_hausgeld_booked_before_plan_stays_unassigned_until_resolved(client_a_admin, seed_data, migrator_session, known_owner):
    api = client_a_admin
    w = _seed_weg(seed_data)
    if not known_owner:
        person = await api.post('/api/persons', json={'first_name': 'Unknown', 'last_name': 'Synthetic payer'})
        owner = await api.post('/api/owner-profiles', json={'person_id': person.json()['id']})
        w['owner_a'] = owner.json()['id']
    cash = await payment(api, w, '1300.00', kind='HAUSGELD_VORSCHUSS', when='2025-08-05')
    response = await api.post('/api/weg-settlements/asset-reports', json={
        'property_id': w['property'], 'period_start': '2025-01-01', 'period_end': '2025-12-31'})
    assert response.status_code == 201, response.text
    report = response.json()['id']
    path = f'/api/weg-settlements/{report}/reserve-ledger'
    ledger = (await api.get(path)).json()
    assert ledger['unlinked_payment_ids'] == [cash]
    assert next(row for row in ledger['entries'] if row.get('payment_id') == cash)['amount'] == '1300.00'
    assert next(row for row in ledger['entries'] if row.get('payment_id') == cash)['kind'] == 'UNASSIGNED'
    audit = await migrator_session.scalar(text("SELECT payload_diff FROM audit_event WHERE entity_id=CAST(:id AS uuid) AND action='payment.booked'"), {'id': cash})
    assert audit['reserve_plan_unresolved'] is True
    values = {'bank_balances': [
        {'bank_account_id': w['bank_giro'], 'label': 'Girokonto', 'account_type': 'LAUFEND', 'balance': '1300.00'},
        {'bank_account_id': w['bank_ruecklage'], 'label': 'Rücklagenkonto', 'account_type': 'RUECKLAGE', 'balance': '0.00'},
    ], 'receivables': [], 'payables': []}
    await attest_fixture(api, 'settlement', report, values)
    blocked = await api.post(f'/api/weg-settlements/asset-reports/{report}/compute', json=values)
    assert blocked.status_code == 422
    assert blocked.json()['detail']['code'] == 'RESERVE_ASSIGNMENT_REQUIRED'
    await _create_and_finalize_plan(api, w)
    ledger = (await api.get(path)).json()
    if not known_owner:
        assert ledger['unlinked_payment_ids'] == [cash]
        assigned = await api.post(path + '/overrides', json={
            'component': 'CONTRIBUTION', 'payment_id': cash, 'amount': '600.00',
            'reason': 'Bank statement and unit assignment reviewed'})
        assert assigned.status_code == 201, assigned.text
        ledger = (await api.get(path)).json()
        row = next(row for row in ledger['entries'] if row.get('payment_id') == cash)
        assert row['reason'] == 'Bank statement and unit assignment reviewed'
        duplicate = await api.post(path + '/overrides', json={
            'component': 'CONTRIBUTION', 'payment_id': cash, 'amount': '600.00', 'reason': 'Duplicate'})
        assert duplicate.status_code == 422
        overlapping = await api.post('/api/weg-settlements/asset-reports', json={
            'property_id': w['property'], 'period_start': '2025-07-01', 'period_end': '2025-12-31'})
        assert overlapping.status_code == 201, overlapping.text
        other = (await api.get(f"/api/weg-settlements/{overlapping.json()['id']}/reserve-ledger")).json()
        assert other['unlinked_payment_ids'] == []
        assert other['contributions_total'] == '600.00'
    assert ledger['unlinked_payment_ids'] == []
    assert ledger['contributions_total'] == '600.00'
    await attest_fixture(api, 'settlement', report, values)
    response = await api.post(f'/api/weg-settlements/asset-reports/{report}/compute', json=values)
    assert response.status_code == 200, response.text
    if not known_owner:
        response = await api.post(f'/api/payments/{cash}/reverse', json={'note': 'Cancel reviewed assignment'})
        assert response.status_code == 201, response.text
        ledger = (await api.get(path)).json()
        assert ledger['contributions_total'] == '0.00' and ledger['unlinked_payment_ids'] == []


@pytest.mark.asyncio
async def test_cash_ratio_excludes_units_owned_only_before_or_after_value_date(client_a_admin, seed_data, migrator_session):
    from uuid import UUID
    from types import SimpleNamespace
    from app.engine.reserve_ledger import hausgeld_reserve_share
    from app.repositories.reserve_ledger_repo import ReserveLedgerRepository

    api = client_a_admin
    w = _seed_weg(seed_data)
    cash = UUID(await payment(api, w, '1300.00', kind='HAUSGELD_VORSCHUSS', when='2025-08-05'))
    await migrator_session.execute(text("""INSERT INTO ownership_history
        (organization_id,unit_id,owner_profile_id,valid_from,valid_to)
        VALUES (:org,:unit,:owner,'2000-01-01','2025-07-01'),
               (:org,:unit,:owner,'2025-09-01',NULL)"""),
        {'org': seed_data['org_a_id'], 'unit': w['u2'], 'owner': w['owner_a']})
    units = await ReserveLedgerRepository(migrator_session, seed_data['org_a_id']).owner_units(
        [SimpleNamespace(id=cash)])
    assert units == {cash: {w['u1']}}
    # Unequal unit ratios expose an accidental historical/future unit union.
    planned = [{'unit_id': w['u1'], 'amount': '3600.00'}, {'unit_id': w['u2'], 'amount': '2400.00'}]
    owners = [{'unit_id': w['u1'], 'annual_advance': '7100.00'}, {'unit_id': w['u2'], 'annual_advance': '5900.00'}]
    assert hausgeld_reserve_share(Decimal('1300'), units[cash], planned, owners) == Decimal('659.15')


@pytest.mark.asyncio
async def test_half_year_opening_adjustment_does_not_enter_full_year(client_a_admin, seed_data):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    response = await api.post('/api/weg-settlements/asset-reports', json={
        'property_id': w['property'], 'period_start': '2025-07-01', 'period_end': '2025-12-31'})
    assert response.status_code == 201, response.text
    report = response.json()['id']
    response = await api.post(f'/api/weg-settlements/{report}/reserve-ledger/overrides', json={
        'component': 'OPENING', 'amount': '1000.00', 'reason': 'Synthetic half-year opening'})
    assert response.status_code == 201, response.text
    assert (await api.get(f'/api/weg-settlements/{report}/reserve-ledger')).json()['opening_balance'] == '1000.00'
    assert (await api.get(f"/api/weg-settlements/{w['run']}/reserve-ledger")).json()['opening_balance'] == '0.00'


@pytest.mark.asyncio
@pytest.mark.parametrize('evidence', ['ledger', 'payout'])
async def test_populated_reserve_downgrade_refuses_without_losing_state(client_a_admin, seed_data, evidence):
    import importlib.util
    from pathlib import Path
    from sqlalchemy import create_engine
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext
    from tests.conftest import MIGRATOR_SYNC_URL
    from tests.test_session_review_access import _chain_snapshot

    w = await reserve_world(client_a_admin, seed_data)
    if evidence == 'ledger':
        response = await client_a_admin.post(f"/api/weg-settlements/{w['run']}/reserve-ledger/overrides", json={
            'component': 'OPENING', 'amount': '1.00', 'reason': 'Retained synthetic opening'})
        assert response.status_code == 201, response.text
    else:
        await payment(client_a_admin, w, '1.00', kind='RUECKLAGENENTNAHME', direction='OUT')
    path = Path(__file__).parents[1] / 'migrations/versions/0149_maintenance_reserve_ledger.py'
    spec = importlib.util.spec_from_file_location('reserve_migration', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.connect() as connection, connection.begin() as outer:
            # Isolate each refusal from evidence retained by earlier tests.
            if evidence == 'payout':
                connection.execute(text('TRUNCATE public.reserve_ledger_entry'))
            before = _chain_snapshot(connection)
            with pytest.raises(DBAPIError, match='Retain reserve'):
                with connection.begin_nested(), Operations.context(MigrationContext.configure(connection)):
                    migration.downgrade()
            assert _chain_snapshot(connection) == before
            outer.rollback()
    finally:
        engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("correction_finalized", [False, True])
async def test_late_storno_and_hausgeld_keep_original_ratio_and_are_carried_once(client_a_admin, seed_data, correction_finalized):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    original = await payment(api, w, '100.00')
    await finalize(api, w)
    response = await api.post(f'/api/payments/{original}/reverse', json={'note': 'Late synthetic cancellation'})
    assert response.status_code == 201, response.text
    storno = response.json()['id']
    response = await api.post(f"/api/economic-plans/{w['plan']}/correction")
    assert response.status_code == 201, response.text
    if correction_finalized:
        correction = response.json()['id']
        values = {'planned_costs': [], 'reserve_contribution_total': '1200.00'}
        await attest_fixture(api, 'economic_plan', correction, values)
        assert (await api.post(f'/api/economic-plans/{correction}/finalize', json=values)).status_code == 200
    late = await payment(api, w, '1300.00', kind='HAUSGELD_VORSCHUSS', when='2025-12-31')
    values = {'bank_balances': [{'bank_account_id': w[key], 'label': key, 'account_type': kind, 'balance': '0.00'}
        for key, kind in [('bank_giro', 'LAUFEND'), ('bank_ruecklage', 'RUECKLAGE')]], 'receivables': [], 'payables': []}
    for year, opening, late_total in [(2026, '100.00', '500.00'), (2027, '600.00', '0.00')]:
        response = await api.post('/api/weg-settlements/asset-reports', json={
            'property_id': w['property'], 'period_start': f'{year}-01-01', 'period_end': f'{year}-12-31'})
        assert response.status_code == 201, response.text
        run = response.json()['id']
        ledger = (await api.get(f'/api/weg-settlements/{run}/reserve-ledger')).json()
        assert ledger['opening_balance'] == opening
        assert ledger['late_cash_total'] == late_total
        assert ledger['closing_balance'] == '600.00'
        if year == 2026:
            cash = {row['payment_id']: row for row in ledger['entries'] if row.get('payment_id')}
            assert cash[storno]['amount'] == '-100.00' and cash[storno]['kind'] == 'LATE_CASH'
            assert cash[late]['amount'] == '600.00' and cash[late]['date'] == '2025-12-31'
        else:
            assert not [row for row in ledger['entries'] if row['kind'] == 'LATE_CASH']
        await attest_fixture(api, 'settlement', run, values)
        for action in ('compute', 'finalize'):
            response = await api.post(f'/api/weg-settlements/asset-reports/{run}/{action}', json=values)
            assert response.status_code == 200, response.text
            assert next(row['amount'] for row in response.json()['lines'] if row['line_type'] == 'RESERVE_CLOSING') == '600.00'
            if year == 2026:
                lines = response.json()['lines']
                assert next(row['sort_order'] for row in lines if row['line_type'] == 'RESERVE_OPENING') < next(
                    row['sort_order'] for row in lines if row['label'] == 'Nachträgliche Buchungen zum abgeschlossenen Jahr')
    assert (await api.get(f"/api/weg-settlements/{w['run']}/reserve-ledger")).json()['closing_balance'] == '100.00'


@pytest.mark.asyncio
async def test_late_payout_can_be_funded_and_its_later_storno_keeps_the_evidence(client_a_admin, seed_data):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    await finalize(api, w)
    cash = await payment(api, w, '10.00', kind='RUECKLAGENENTNAHME', direction='OUT', when='2025-12-31')
    response = await api.post('/api/meetings', json={'property_id': w['property'], 'meeting_date': '2025-01-15', 'meeting_type': 'ORDENTLICH'})
    assert response.status_code == 201, response.text
    meeting = response.json()['id']
    assert (await api.post(f'/api/meetings/{meeting}/hold')).status_code == 200
    response = await api.post('/api/meetings/resolutions', json={'meeting_id': meeting, 'wording': 'Fund synthetic late repair',
        'votes_yes': 2, 'votes_no': 0, 'votes_abstain': 0, 'result': 'ANGENOMMEN'})
    assert response.status_code == 201, response.text
    response = await api.post(f"/api/meetings/resolutions/{response.json()['id']}/register")
    assert response.status_code == 201, response.text
    register = response.json()['id']
    values = {'bank_balances': [{'bank_account_id': w[key], 'label': key, 'account_type': kind, 'balance': '0.00'}
        for key, kind in [('bank_giro', 'LAUFEND'), ('bank_ruecklage', 'RUECKLAGE')]], 'receivables': [], 'payables': []}
    for year, late, closing in [(2026, '-10.00', '-10.00'), (2027, '10.00', '0.00')]:
        response = await api.post('/api/weg-settlements/asset-reports', json={
            'property_id': w['property'], 'period_start': f'{year}-01-01', 'period_end': f'{year}-12-31'})
        assert response.status_code == 201, response.text
        run = response.json()['id']
        path = f'/api/weg-settlements/{run}/reserve-ledger'
        if year == 2026:
            assert (await api.get(path)).json()['unlinked_payment_ids'] == [cash]
            response = await api.post(path + '/overrides', json={'component': 'FUNDING', 'amount': '0.00',
                'reason': 'Synthetic late payout evidence', 'payment_id': cash,
                'cost_entry_id': w['ce_garten'], 'register_entry_id': register})
            assert response.status_code == 201, response.text
        ledger = (await api.get(path)).json()
        assert ledger['late_cash_total'] == late
        assert ledger['closing_balance'] == closing
        assert ledger['unlinked_payment_ids'] == []
        assert next(row['register_entry_id'] for row in ledger['entries'] if row['kind'] == 'LATE_CASH') == register
        await attest_fixture(api, 'settlement', run, values)
        response = await api.post(f'/api/weg-settlements/asset-reports/{run}/finalize', json=values)
        assert response.status_code == 200, response.text
        if year == 2026:
            response = await api.post(f'/api/payments/{cash}/reverse', json={'note': 'Late payout cancelled'})
            assert response.status_code == 201, response.text


@pytest.mark.asyncio
async def test_typed_reserve_totals_require_reason_and_persist_an_audited_delta(client_a_admin, seed_data, migrator_session):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    values = {**paid(w), 'reserve_opening_balance': '1000.00'}
    response = await api.post('/api/financial-input-coverage', json={'subject_kind': 'settlement', 'subject_id': w['run'],
        'values': values, 'confirmed_complete': True, 'reason': 'Reviewed records'})
    assert response.status_code == 422, response.text
    values['reserve_override_reason'] = 'Imported bank-confirmed opening'
    await attest_fixture(api, 'settlement', w['run'], values)
    await attest_fixture(api, 'settlement', w['run'], values)
    response = await api.post(f"/api/weg-settlements/{w['run']}/compute", json=values)
    assert response.status_code == 200, response.text
    count = await migrator_session.scalar(text('SELECT count(*) FROM reserve_ledger_entry WHERE settlement_run_id=CAST(:id AS uuid)'), {'id': w['run']})
    assert count == 1
    audits = await migrator_session.scalar(text("SELECT count(*) FROM audit_event WHERE entity_id=CAST(:id AS uuid) AND action='reserve.override_added'"), {'id': w['run']})
    assert audits == 1


@pytest.mark.asyncio
async def test_new_override_invalidates_coverage_and_cannot_be_edited(client_a_admin, seed_data, migrator_session):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    await attest_fixture(api, 'settlement', w['run'], paid(w))
    response = await api.post(f"/api/weg-settlements/{w['run']}/reserve-ledger/overrides", json={
        'component': 'ADJUSTMENT', 'amount': '-1.00', 'reason': 'Bank fee correction'})
    assert response.status_code == 201, response.text
    assert (await api.post(f"/api/weg-settlements/{w['run']}/finalize", json=paid(w))).status_code == 422
    with pytest.raises(DBAPIError, match='append-only'):
        async with migrator_session.begin_nested():
            await migrator_session.execute(text("UPDATE reserve_ledger_entry SET reason='changed' WHERE id=CAST(:id AS uuid)"), response.json())
    ledger = (await api.get(f"/api/weg-settlements/{w['run']}/reserve-ledger")).json()
    assert Decimal(ledger['closing_balance']) == Decimal('-1.00') and ledger['overdrawn']


@pytest.mark.asyncio
async def test_withdrawal_requires_cash_cost_and_registered_resolution(client_a_admin, seed_data):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    response = await api.post('/api/meetings', json={'property_id': w['property'], 'meeting_date': '2025-01-15', 'meeting_type': 'ORDENTLICH'})
    assert response.status_code == 201, response.text
    meeting = response.json()['id']
    assert (await api.post(f'/api/meetings/{meeting}/hold')).status_code == 200
    response = await api.post('/api/meetings/resolutions', json={'meeting_id': meeting, 'wording': 'Fund garden repairs from reserves',
        'votes_yes': 2, 'votes_no': 0, 'votes_abstain': 0, 'result': 'ANGENOMMEN'})
    assert response.status_code == 201, response.text
    response = await api.post(f"/api/meetings/resolutions/{response.json()['id']}/register")
    assert response.status_code == 201, response.text
    register = response.json()['id']
    cash = await payment(api, w, '1000.00', kind='RUECKLAGENENTNAHME', direction='OUT')
    path = f"/api/weg-settlements/{w['run']}/reserve-ledger"
    assert (await api.get(path)).json()['withdrawals_total'] == '1000.00'
    assert (await api.get(path)).json()['unlinked_payment_ids'] == [cash]
    await attest_fixture(api, 'settlement', w['run'], paid(w))
    refused = await api.post(f"/api/weg-settlements/{w['run']}/compute", json=paid(w))
    assert refused.status_code == 422
    assert refused.json()['detail']['code'] == 'RESERVE_PAYOUT_EVIDENCE_REQUIRED'
    body = {'component': 'FUNDING', 'amount': '0.00', 'reason': 'Executed garden repair resolution',
            'payment_id': cash, 'cost_entry_id': w['ce_garten'], 'register_entry_id': register}
    response = await api.post(path + '/overrides', json=body)
    assert response.status_code == 201, response.text
    assert (await api.post(path + '/overrides', json=body)).status_code == 409
    ledger = (await api.get(path)).json()
    assert ledger['withdrawals_total'] == '1000.00'
    assert any(row.get('register_entry_id') == register for row in ledger['entries'])

    # Storno frees the funding limit for the corrected booking.
    response = await api.post(f'/api/payments/{cash}/reverse', json={'note': 'Corrected payout'})
    assert response.status_code == 201, response.text
    replacement = await payment(api, w, '1000.00', kind='RUECKLAGENENTNAHME', direction='OUT')
    body['payment_id'] = replacement
    response = await api.post(path + '/overrides', json=body)
    assert response.status_code == 201, response.text
    assert (await api.get(path)).json()['withdrawals_total'] == '1000.00'


@pytest.mark.asyncio
async def test_reversed_unlinked_payout_retains_cash_rows_without_blocking_finalization(client_a_admin, seed_data):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    cash = await payment(api, w, '1000.00', kind='RUECKLAGENENTNAHME', direction='OUT')
    response = await api.post(f'/api/payments/{cash}/reverse', json={'note': 'Payout cancelled before allocation'})
    assert response.status_code == 201, response.text
    ledger = (await api.get(f"/api/weg-settlements/{w['run']}/reserve-ledger")).json()
    assert ledger['withdrawals_total'] == '0.00'
    assert ledger['unlinked_payment_ids'] == []
    assert len([row for row in ledger['entries'] if row['kind'] == 'WITHDRAWAL']) == 2
    await finalize(api, w)


@pytest.mark.asyncio
async def test_reserve_payout_refuses_rental_accounts_and_personal_counterparties(client_a_admin, seed_data):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    body = {'bank_account_id': w['bank_giro'], 'payment_type': 'RUECKLAGENENTNAHME',
            'amount': '10.00', 'value_date': '2025-02-01', 'direction': 'OUT'}
    assert (await api.post('/api/payments', json={**body, 'owner_profile_id': w['owner_a']})).status_code == 422
    assert (await api.post('/api/payments', json={**body, 'direction': 'IN'})).status_code == 422
    prop = await api.post('/api/properties', json={'name': 'Rental payout rejection', 'management_type': 'MIET'})
    assert prop.status_code == 201, prop.text
    bank = await api.post('/api/bank-accounts', json={'property_id': prop.json()['id'], 'account_type': 'LAUFEND', 'label': 'Rental account'})
    assert bank.status_code == 201, bank.text
    assert (await api.post('/api/payments', json={**body, 'bank_account_id': bank.json()['id']})).status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize('direction,amount', [('IN', '5000.00'), ('IN', '-250.00'), ('OUT', '-5000.00'), ('OUT', '250.00')])
async def test_unassigned_reserve_amount_cannot_exceed_or_reverse_payment(client_a_admin, seed_data, migrator_session, direction, amount):
    api = client_a_admin
    w = _seed_weg(seed_data)
    cash = await payment(api, w, '100.00', kind='HAUSGELD_VORSCHUSS', direction=direction, when='2025-08-05')
    # Remove the plan ratio: this is a cash assignment, not a general adjustment.
    response = await api.post('/api/weg-settlements/asset-reports', json={
        'property_id': w['property'], 'period_start': '2025-08-01', 'period_end': '2025-08-31'})
    assert response.status_code == 201, response.text
    path = f"/api/weg-settlements/{response.json()['id']}/reserve-ledger"
    before = (await api.get(path)).json()
    assert cash in before['unlinked_payment_ids']
    refused = await api.post(path + '/overrides', json={
        'component': 'CONTRIBUTION', 'payment_id': cash, 'amount': amount, 'reason': 'Reviewed bank receipt'})
    assert refused.status_code == 422, refused.text
    assert refused.json()['detail']['code'] == 'RESERVE_ASSIGNMENT_EXCEEDS_PAYMENT'
    assert (await api.get(path)).json() == before
    await migrator_session.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {'actor': str(seed_data['user_a_admin_id'])})
    with pytest.raises(DBAPIError) as refused_write:
        async with migrator_session.begin_nested():
            await migrator_session.execute(text("""INSERT INTO reserve_ledger_entry
                (organization_id,property_id,settlement_run_id,entry_date,component,amount,reason,payment_id,created_by)
                VALUES (:org,:property,:run,'2025-08-31','CONTRIBUTION',:amount,'Reviewed receipt',:payment,:actor)"""),
                {'org': seed_data['org_a_id'], 'property': w['property'], 'run': path.split('/')[3],
                 'amount': Decimal(amount), 'payment': cash, 'actor': seed_data['user_a_admin_id']})
    assert refused_write.value.orig.sqlstate == '23514'
    assert refused_write.value.orig.__cause__.constraint_name == 'reserve_assignment_amount'


@pytest.mark.asyncio
@pytest.mark.parametrize('direction,amount', [('IN', '0.00'), ('IN', '25.00'), ('IN', '100.00'), ('OUT', '-25.00'), ('OUT', '-100.00')])
async def test_unassigned_reserve_accepts_zero_partial_and_full_signed_payment(client_a_admin, seed_data, direction, amount):
    api = client_a_admin
    w = _seed_weg(seed_data)
    cash = await payment(api, w, '100.00', kind='HAUSGELD_VORSCHUSS', direction=direction, when='2025-08-05')
    report = await api.post('/api/weg-settlements/asset-reports', json={
        'property_id': w['property'], 'period_start': '2025-01-01', 'period_end': '2025-12-31'})
    assert report.status_code == 201, report.text
    path = f"/api/weg-settlements/{report.json()['id']}/reserve-ledger"
    body = {'component': 'CONTRIBUTION', 'payment_id': cash, 'amount': amount, 'reason': 'Reviewed bank receipt'}
    response = await api.post(path + '/overrides', json=body)
    assert response.status_code == 201, response.text
    ledger = (await api.get(path)).json()
    assert ledger['contributions_total'] == amount
    assert ledger['unlinked_payment_ids'] == []
    # One retained assignment per payment also bounds the aggregate across drafts.
    duplicate = await api.post(path + '/overrides', json=body)
    assert duplicate.status_code == 422
    assert duplicate.json()['detail']['code'] == 'RESERVE_ASSIGNMENT_REQUIRED'


@pytest.mark.asyncio
async def test_reserve_payer_without_cost_or_plan_lines_is_listed_with_notice(client_a_admin, seed_data):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    await payment(api, w, '600.00')
    person = await api.post('/api/persons', json={'first_name': 'Synthetic', 'last_name': 'Reserve payer without unit'})
    owner = await api.post('/api/owner-profiles', json={'person_id': person.json()['id']})
    outsider = owner.json()['id']
    await payment(api, {**w, 'owner_a': outsider}, '500.00')
    result = await finalize(api, w)
    reserves = [line for line in result['lines'] if line['line_type'] == 'RESERVE_CONTRIBUTION' and line['addressee_owner_profile_id']]
    assert sum(Decimal(line['amount']) for line in reserves) == Decimal('1100.00')
    line = next(line for line in reserves if line['addressee_owner_profile_id'] == outsider)
    assert line['amount'] == '500.00'
    assert 'ohne Kosten- oder Planposition' in line['note']
    document = await api.get(f"/api/weg-settlements/{w['run']}/pdf")
    assert 'ohne Kosten- oder Planposition' in pdf_text(document.content)


def pdf_text(data):
    import io
    import pdfplumber
    with pdfplumber.open(io.BytesIO(data)) as document:
        return '\n'.join(page.extract_text() or '' for page in document.pages)


@pytest.mark.asyncio
@pytest.mark.parametrize('family', ['weg', 'asset'])
async def test_negative_reserve_is_explicitly_flagged_in_pdf(client_a_admin, seed_data, family):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    if family == 'asset':
        report = await api.post('/api/weg-settlements/asset-reports', json={
            'property_id': w['property'], 'period_start': '2025-01-01', 'period_end': '2025-12-31'})
        assert report.status_code == 201, report.text
        w['run'] = report.json()['id']
    path = f"/api/weg-settlements/{w['run']}/reserve-ledger"
    response = await api.post(path + '/overrides', json={'component': 'ADJUSTMENT', 'amount': '-1000.00', 'reason': 'Imported overdraft'})
    assert response.status_code == 201, response.text
    if family == 'weg':
        await finalize(api, w)
        url = f"/api/weg-settlements/{w['run']}/pdf"
    else:
        values = {'bank_balances': [{'bank_account_id': w[key], 'label': key, 'account_type': kind, 'balance': '0.00'}
                  for key, kind in [('bank_giro', 'LAUFEND'), ('bank_ruecklage', 'RUECKLAGE')]], 'receivables': [], 'payables': []}
        await attest_fixture(api, 'settlement', w['run'], values)
        response = await api.post(f"/api/weg-settlements/asset-reports/{w['run']}/finalize", json=values)
        assert response.status_code == 200, response.text
        url = f"/api/weg-settlements/asset-reports/{w['run']}/pdf"
    document = await api.get(url)
    assert document.status_code == 200, document.text
    content = pdf_text(document.content)
    assert 'Die Rücklage ist überzogen.' in content
    assert '-1.000,00 EUR' in content


@pytest.mark.asyncio
async def test_reserve_migration_roundtrip_below_oldest_affected_revision(migrator_session):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine
    from app.deployment import TARGET
    from tests.conftest import MIGRATOR_SYNC_URL, clear_disposable_source_context
    graph = ScriptDirectory('migrations')
    revisions = list(graph.iterate_revisions(TARGET, '0091'))
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.connect() as connection, connection.begin() as transaction:
            connection.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
            with connection.connection.cursor() as cursor:
                clear_disposable_source_context(cursor)
            signatures = ['financial_computation_source_digest(uuid,uuid,uuid,boolean)',
                          'validate_accounting_binding_baseline(public.accounting_scope_binding)']
            def definitions():
                return {signature: connection.scalar(text('SELECT pg_get_functiondef(CAST(:signature AS regprocedure))'),
                    {'signature': signature}) for signature in signatures}
            before = definitions()
            with Operations.context(MigrationContext.configure(connection)):
                for revision in revisions:
                    revision.module.downgrade()
                    if revision.revision == TARGET:
                        assert connection.scalar(text("SELECT to_regclass('reserve_ledger_entry')")) is None
                        assert connection.scalar(text("SELECT to_regprocedure('recheck_reserve_computations()')")) is None
                        assert 'RUECKLAGENENTNAHME' not in connection.scalar(text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='payment'::regclass AND conname='ck_payment_ck_payment_type'"))
                        assert 'RESERVE_LATE_CASH' not in connection.scalar(text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='settlement_line'::regclass AND conname='ck_settlement_line_ck_settlement_line_line_type'"))
                for revision in reversed(revisions):
                    revision.module.upgrade()
            assert definitions() == before
            transaction.rollback()
    finally:
        engine.dispose()
