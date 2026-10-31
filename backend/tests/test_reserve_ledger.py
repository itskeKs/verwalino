"""Reserve API and frozen annual statements, on synthetic WEG records."""
import pytest
from decimal import Decimal
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.test_m4_weg import _seed_weg, _create_and_finalize_plan
from tests.financial_coverage_helpers import attest_fixture


async def reserve_world(api, seed):
    world = _seed_weg(seed)
    plan = await _create_and_finalize_plan(api, world)
    response = await api.post('/api/weg-settlements', json={
        'property_id': world['property'], 'economic_plan_id': plan,
        'period_start': '2025-01-01', 'period_end': '2025-12-31'})
    assert response.status_code == 201, response.text
    world['run'] = response.json()['id']
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
    assert (await api.post(override, json={'component': 'OPENING', 'amount': '20000.00', 'reason': ' '})).status_code == 422
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
        entries=AsyncMock(return_value=[]), payments=AsyncMock(return_value=[]))
    ledger = await service.read(run)
    assert ledger['closing_balance'] == '0.00' and not ledger['frozen']


async def payment(api, w, amount, *, kind='RUECKLAGENANTEIL', direction='IN', when='2025-02-01'):
    body = {'bank_account_id': w['bank_giro'], 'direction': direction, 'amount': amount,
            'value_date': when, 'payment_type': kind, 'period_year': int(when[:4]), 'period_month': int(when[5:7])}
    if direction == 'IN':
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
    # A late receipt must neither rewrite the issued statement nor its carry-over.
    await payment(api, w, '999.00', when='2025-12-31')
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
