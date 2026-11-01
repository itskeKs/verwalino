"""Reserve API and frozen annual statements, on synthetic WEG records."""
import pytest
from datetime import date
from decimal import Decimal
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.test_m4_weg import _seed_weg, _create_and_finalize_plan
from tests.financial_coverage_helpers import attest_fixture, reserve_evidence
from tests.test_financial_scope_boundary import scope_seed as scope_seed
from tests.test_invoice_extraction_worker import worker_tenant as worker_tenant
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
    assert {row['owner_name'] for row in response.json()['entries']} == {'Anton Eigentuemer', 'Berta Eigentuemer'}
    override = path + '/overrides'
    for reason in ('', ' '):
        refused = await api.post(override, json={'component': 'OPENING', 'amount': '20000.00', 'reason': reason})
        assert refused.status_code == 422
        assert refused.json()['detail']['code'] == 'RESERVE_CORRECTION_REASON_REQUIRED'
    response = await api.post(override, json={'component': 'OPENING', 'amount': '20000.00', 'reason': 'Imported bank-confirmed balance'})
    assert response.status_code == 201, response.text
    assert (await api.get(path)).json()['closing_balance'] == '20000.00'


@pytest.mark.asyncio
async def test_ledger_keeps_readable_payment_references_on_draft_and_frozen_reads(client_a_admin, seed_data):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    response = await api.post('/api/payments', json={
        'bank_account_id': w['bank_giro'], 'direction': 'IN', 'amount': '1000.00',
        'value_date': '2025-02-01', 'payment_type': 'RUECKLAGENANTEIL',
        'owner_profile_id': w['owner_a'], 'note': 'Zuführung für Änne Groß'})
    assert response.status_code == 201, response.text
    cash = response.json()['id']
    assert (await api.post(f'/api/payments/{cash}/book')).status_code == 200
    path = f"/api/weg-settlements/{w['run']}/reserve-ledger"
    for frozen in (False, True):
        if frozen:
            await finalize(api, w)
        ledger = (await api.get(path)).json()
        row = next(row for row in ledger['entries'] if row.get('payment_id') == cash)
        assert row['payment_note'] == 'Zuführung für Änne Groß'
        assert row['owner_name'] == 'Anton Eigentuemer'
        assert ledger['frozen'] is frozen


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


async def payment(api, w, amount, *, kind='RUECKLAGENANTEIL', direction='IN', when='2025-02-01', evidence=None):
    body = {'bank_account_id': w['bank_giro'], 'direction': direction, 'amount': amount,
            'value_date': when, 'payment_type': kind, 'period_year': int(when[:4]), 'period_month': int(when[5:7])}
    if direction == 'IN' or kind == 'HAUSGELD_VORSCHUSS':
        body['owner_profile_id'] = w['owner_a']
    if kind == 'RUECKLAGENENTNAHME':
        body.update(evidence or await reserve_evidence(api, w))
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
        'owner_name': 'Anton Eigentuemer', 'payment_note': None,
        'cost_document_number': None, 'register_sequence_number': None,
    }
    next_world = {**w, 'run': next_run}
    next_result = await finalize(api, next_world)
    lines = next_result['lines']
    assert next(row['sort_order'] for row in lines if row['line_type'] == 'RESERVE_OPENING') < next(
        row['sort_order'] for row in lines if row['label'] == 'Nachträgliche Buchungen zum abgeschlossenen Jahr')
    closing_note = next(row['note'] for row in lines if row['line_type'] == 'RESERVE_CLOSING')
    assert 'nachträgliche Buchungen' in closing_note
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
    assert 'nachträgliche Buchungen' in content
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
@pytest.mark.parametrize('half_year_first', [False, True])
@pytest.mark.parametrize('database_backstop', [False, True])
async def test_imported_opening_blocks_overlapping_half_and_full_year_creation(
        client_a_admin, seed_data, monkeypatch, half_year_first, database_backstop):
    from unittest.mock import AsyncMock
    from app.repositories.reserve_ledger_repo import ReserveLedgerRepository
    api = client_a_admin
    w = _seed_weg(seed_data)
    imported_end, refused_end = ('2025-06-30', '2025-12-31') if half_year_first else ('2025-12-31', '2025-06-30')
    response = await api.post('/api/weg-settlements/asset-reports', json={
        'property_id': w['property'], 'period_start': '2025-01-01', 'period_end': imported_end})
    assert response.status_code == 201, response.text
    report = response.json()['id']
    response = await api.post(f'/api/weg-settlements/{report}/reserve-ledger/overrides', json={
        'component': 'OPENING', 'amount': '1000.00', 'reason': 'Synthetic first-period opening'})
    assert response.status_code == 201, response.text
    assert (await api.get(f'/api/weg-settlements/{report}/reserve-ledger')).json()['opening_balance'] == '1000.00'
    if database_backstop:
        monkeypatch.setattr(ReserveLedgerRepository, 'has_later_opening_import', AsyncMock(return_value=False))
    refused = await api.post('/api/weg-settlements/asset-reports', json={
        'property_id': w['property'], 'period_start': '2025-01-01', 'period_end': refused_end})
    assert refused.status_code == 422, refused.text
    assert refused.json()['detail']['code'] == 'RESERVE_OPENING_IMPORT_EXISTS'
    # An identical period is still valid for sibling and correction runs.
    sibling = await api.post('/api/weg-settlements/asset-reports', json={
        'property_id': w['property'], 'period_start': '2025-01-01', 'period_end': imported_end})
    assert sibling.status_code == 201, sibling.text
    assert (await api.get(f'/api/weg-settlements/{report}/reserve-ledger')).json()['opening_balance'] == '1000.00'


@pytest.mark.asyncio
@pytest.mark.parametrize('evidence', ['ledger', 'payout', 'statement'])
async def test_populated_reserve_downgrade_refuses_without_losing_state(client_a_admin, seed_data, evidence):
    import importlib.util
    from pathlib import Path
    from sqlalchemy import create_engine
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext
    from tests.conftest import MIGRATOR_SYNC_URL, clear_disposable_source_context
    from tests.test_session_review_access import _chain_snapshot

    w = await reserve_world(client_a_admin, seed_data)
    if evidence == 'ledger':
        response = await client_a_admin.post(f"/api/weg-settlements/{w['run']}/reserve-ledger/overrides", json={
            'component': 'OPENING', 'amount': '1.00', 'reason': 'Retained synthetic opening'})
        assert response.status_code == 201, response.text
    elif evidence == 'payout':
        await payment(client_a_admin, w, '1.00', kind='RUECKLAGENENTNAHME', direction='OUT')
    else:
        sources = await reserve_evidence(client_a_admin, w)
        response = await client_a_admin.post(f"/api/weg-settlements/{w['run']}/reserve-ledger/overrides", json={
            'component': 'ADJUSTMENT', 'amount': '-1.00', 'reason': 'Retained statement evidence',
            **{key.removeprefix('reserve_'): value for key, value in sources.items()}})
        assert response.status_code == 201, response.text
        result = await finalize(client_a_admin, w)
        assert any(line['line_type'] == 'RESERVE_ADJUSTMENT' for line in result['lines'])
    path = Path(__file__).parents[1] / 'migrations/versions/0149_maintenance_reserve_ledger.py'
    spec = importlib.util.spec_from_file_location('reserve_migration', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.connect() as connection, connection.begin() as outer:
            # Isolate each refusal from evidence retained by earlier tests.
            if evidence in ('payout', 'statement'):
                connection.execute(text('TRUNCATE public.reserve_ledger_entry'))
            if evidence == 'statement':
                connection.execute(text('TRUNCATE public.payment CASCADE'))
                assert connection.scalar(text("""SELECT EXISTS(SELECT 1 FROM settlement_line l
                    JOIN settlement_run r ON r.id=l.settlement_run_id
                    WHERE r.id=CAST(:run AS uuid) AND r.status='finalized'
                      AND l.line_type='RESERVE_ADJUSTMENT')"""), {'run': w['run']})
            before = _chain_snapshot(connection)
            message = {'ledger': 'Retain reserve ledger evidence', 'payout': 'Retain reserve payout bookings',
                       'statement': 'Retain reserve statement evidence'}[evidence]
            with pytest.raises(DBAPIError, match=message):
                with connection.begin_nested(), Operations.context(MigrationContext.configure(connection)):
                    migration.downgrade()
            assert _chain_snapshot(connection) == before
            with connection.connection.cursor() as cursor:
                clear_disposable_source_context(cursor)
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
            assert connection.scalar(text("SELECT to_regclass('public.reserve_ledger_entry')")) is None
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
    cash = await payment(api, w, '10.00', kind='RUECKLAGENENTNAHME', direction='OUT', when='2025-12-31',
        evidence={'reserve_cost_entry_id': w['ce_garten'], 'reserve_register_entry_id': register})
    values = {'bank_balances': [{'bank_account_id': w[key], 'label': key, 'account_type': kind, 'balance': '0.00'}
        for key, kind in [('bank_giro', 'LAUFEND'), ('bank_ruecklage', 'RUECKLAGE')]], 'receivables': [], 'payables': []}
    for year, late, closing in [(2026, '-10.00', '-10.00'), (2027, '10.00', '0.00')]:
        response = await api.post('/api/weg-settlements/asset-reports', json={
            'property_id': w['property'], 'period_start': f'{year}-01-01', 'period_end': f'{year}-12-31'})
        assert response.status_code == 201, response.text
        run = response.json()['id']
        path = f'/api/weg-settlements/{run}/reserve-ledger'
        if year == 2026:
            assert (await api.get(path)).json()['unlinked_payment_ids'] == []
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
    unchanged = await api.post('/api/financial-input-coverage', json={'subject_kind': 'settlement',
        'subject_id': w['run'], 'values': values, 'confirmed_complete': True, 'reason': 'Reviewed records'})
    assert unchanged.status_code == 201, unchanged.text
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
    evidence = {k.removeprefix('reserve_'): v for k, v in (await reserve_evidence(api, w)).items()}
    response = await api.post(f"/api/weg-settlements/{w['run']}/reserve-ledger/overrides", json={
        **evidence, 'component': 'ADJUSTMENT', 'amount': '-1.00', 'reason': 'Bank fee correction'})
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
    cost = (await api.get(f"/api/cost-entries/{w['ce_garten']}")).json()
    response = await api.post('/api/cost-entries', json={
        **{key: cost[key] for key in ('property_id', 'cost_category_id', 'invoice_date',
            'service_period_from', 'service_period_to', 'gross_amount', 'owner_distribution_key',
            'tenant_distribution_key', 'tenant_apportionable')}, 'document_number': 'RE-2025-Ä-42'})
    assert response.status_code == 201, response.text
    w['ce_garten'] = response.json()['id']
    response = await api.post(f"/api/cost-entries/{w['ce_garten']}/confirm")
    assert response.status_code == 200, response.text
    evidence = {'reserve_cost_entry_id': w['ce_garten'], 'reserve_register_entry_id': register}
    cash = await payment(api, w, '1000.00', kind='RUECKLAGENENTNAHME', direction='OUT', evidence=evidence)
    path = f"/api/weg-settlements/{w['run']}/reserve-ledger"
    assert (await api.get(path)).json()['withdrawals_total'] == '1000.00'
    assert (await api.get(path)).json()['unlinked_payment_ids'] == []
    body = {'component': 'FUNDING', 'amount': '0.00', 'reason': 'Executed garden repair resolution',
            'payment_id': cash, 'cost_entry_id': w['ce_garten'], 'register_entry_id': register}
    response = await api.post(path + '/overrides', json=body)
    assert response.status_code == 201, response.text
    assert (await api.post(path + '/overrides', json=body)).status_code == 409
    ledger = (await api.get(path)).json()
    assert ledger['withdrawals_total'] == '1000.00'
    assert any(row.get('register_entry_id') == register for row in ledger['entries'])
    row = next(row for row in ledger['entries'] if row.get('register_entry_id') == register)
    assert row['cost_document_number'] == 'RE-2025-Ä-42'
    assert row['register_sequence_number'] == 1

    # Storno frees the funding limit for the corrected booking.
    response = await api.post(f'/api/payments/{cash}/reverse', json={'note': 'Corrected payout'})
    assert response.status_code == 201, response.text
    replacement = await payment(api, w, '1000.00', kind='RUECKLAGENENTNAHME', direction='OUT', evidence=evidence)
    body['payment_id'] = replacement
    response = await api.post(path + '/overrides', json=body)
    assert response.status_code == 201, response.text
    assert (await api.get(path)).json()['withdrawals_total'] == '1000.00'


@pytest.mark.asyncio
async def test_reversed_payout_retains_cash_and_evidence_without_blocking_finalization(client_a_admin, seed_data):
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
@pytest.mark.parametrize('signed,amount', [('100.00', '5000.00'), ('100.00', '-250.00'), ('-100.00', '-5000.00'), ('-100.00', '250.00')])
async def test_service_rejects_unassigned_amount_before_writing(signed, amount):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from fastapi import HTTPException
    from app.services.reserve_ledger_service import ReserveLedgerService

    service = ReserveLedgerService(None, 'org', 'actor')
    service.read = AsyncMock(return_value={'frozen': False, 'entries': [
        {'kind': 'UNASSIGNED', 'payment_id': 'cash', 'amount': signed},
    ]})
    service.repo.create = AsyncMock(side_effect=AssertionError('Invalid assignment reached persistence'))
    with pytest.raises(HTTPException) as refused:
        await service.add(SimpleNamespace(id='run', property_id='property', period_start=None, period_end=None), 'CONTRIBUTION',
                          Decimal(amount), 'Reviewed receipt', payment_id='cash')
    assert refused.value.status_code == 422
    assert refused.value.detail['code'] == 'RESERVE_ASSIGNMENT_EXCEEDS_PAYMENT'
    service.repo.create.assert_not_awaited()


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
    response = await api.post(path + '/overrides', json={'component': 'OPENING', 'amount': '-1000.00', 'reason': 'Imported opening overdraft'})
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
    if family == 'asset':
        assert 'Zuführungen' in content
        assert 'Erhaltungsrücklage' in content


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
                          'validate_accounting_binding_baseline(public.accounting_scope_binding)',
                          'organization_retention_catalog()']
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




@pytest.mark.asyncio
@pytest.mark.parametrize('missing', ['both', 'cost', 'resolution'])
async def test_reserve_payout_requires_both_references_at_booking(client_a_admin, seed_data, migrator_session, missing):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    evidence = await reserve_evidence(api, w)
    draft = await api.post('/api/payments', json={'bank_account_id': w['bank_giro'], 'direction': 'OUT',
        'amount': '100.00', 'value_date': '2025-02-01', 'payment_type': 'RUECKLAGENENTNAHME',
        **({} if missing == 'both' else {k: v for k, v in evidence.items() if ('cost' in k) != (missing == 'cost')})})
    assert draft.status_code == 201, draft.text
    path = f"/api/payments/{draft.json()['id']}"
    refused = await api.post(path + '/book')
    assert refused.status_code == 422, refused.text
    assert refused.json()['detail']['code'] == 'RESERVE_PAYOUT_EVIDENCE_REQUIRED'
    assert (await api.get(path)).json()['status'] == 'draft'
    with pytest.raises(DBAPIError) as database_refusal:
        async with migrator_session.begin_nested():
            await migrator_session.execute(text("UPDATE payment SET status='booked',booked_at=now(),booked_by=:actor,retention_until='2034-12-31' WHERE id=CAST(:id AS uuid)"),
                {'actor': seed_data['user_a_admin_id'], 'id': draft.json()['id']})
    assert database_refusal.value.orig.sqlstate == '23514'
    linked = await api.patch(path, json=evidence)
    assert linked.status_code == 200, linked.text
    assert (await api.post(path + '/book')).status_code == 200
    with pytest.raises(DBAPIError, match='GoBD'):
        async with migrator_session.begin_nested():
            await migrator_session.execute(text("UPDATE payment SET reserve_cost_entry_id=NULL WHERE id=CAST(:id AS uuid)"), {'id': draft.json()['id']})
    ledger = (await api.get(f"/api/weg-settlements/{w['run']}/reserve-ledger")).json()
    assert ledger['unlinked_payment_ids'] == []
    assert ledger['withdrawals_total'] == '100.00'
    assert next(row for row in ledger['entries'] if row.get('payment_id') == draft.json()['id'])['register_entry_id'] == evidence['reserve_register_entry_id']


@pytest.mark.asyncio
@pytest.mark.parametrize('missing', ['both', 'cost', 'resolution'])
@pytest.mark.parametrize('component', ['WITHDRAWAL', 'ADJUSTMENT', 'CONTRIBUTION'])
async def test_typed_withdrawal_correction_requires_both_references(client_a_admin, seed_data, missing, component):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    evidence = {k.removeprefix('reserve_'): v for k, v in (await reserve_evidence(api, w)).items()}
    path = f"/api/weg-settlements/{w['run']}/reserve-ledger/overrides"
    body = {'component': component, 'amount': '-1200.00' if component == 'ADJUSTMENT' else '1200.00',
            'reason': 'Correct documented reserve movement'}
    partial = {} if missing == 'both' else {k: v for k, v in evidence.items() if ('cost' in k) != (missing == 'cost')}
    refused = await api.post(path, json={**body, **partial})
    assert refused.status_code == 422, refused.text
    assert refused.json()['detail']['code'] == 'RESERVE_PAYOUT_EVIDENCE_REQUIRED'
    accepted = await api.post(path, json={**body, **evidence})
    assert accepted.status_code == 201, accepted.text


@pytest.mark.asyncio
async def test_opening_import_is_only_allowed_in_first_ledger_year(client_a_admin, seed_data):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    body = {'component': 'OPENING', 'amount': '1000.00', 'reason': 'Imported opening balance'}
    path = f"/api/weg-settlements/{w['run']}/reserve-ledger/overrides"
    assert (await api.post(path, json=body)).status_code == 201
    await finalize(api, w)
    run = await api.post('/api/weg-settlements/asset-reports', json={'property_id': w['property'],
        'period_start': '2026-01-01', 'period_end': '2026-12-31'})
    assert run.status_code == 201, run.text
    refused = await api.post(f"/api/weg-settlements/{run.json()['id']}/reserve-ledger/overrides", json=body)
    assert refused.status_code == 422, refused.text
    assert refused.json()['detail']['code'] == 'RESERVE_OPENING_IMPORT_ONLY'


@pytest.mark.asyncio
async def test_earlier_draft_blocks_opening_import_and_carry_equals_frozen_closing(client_a_admin, seed_data):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    run = await api.post('/api/weg-settlements/asset-reports', json={'property_id': w['property'],
        'period_start': '2026-01-01', 'period_end': '2026-12-31'})
    assert run.status_code == 201, run.text
    path = f"/api/weg-settlements/{run.json()['id']}/reserve-ledger"
    for amount in ('5000.00', '7.00'):
        refused = await api.post(path + '/overrides', json={'component': 'OPENING', 'amount': amount,
            'reason': 'Must not import above an earlier draft'})
        assert refused.status_code == 422, refused.text
        assert refused.json()['detail']['code'] == 'RESERVE_OPENING_IMPORT_ONLY'
    await payment(api, w, '123.45')
    await finalize(api, w)
    frozen = (await api.get(f"/api/weg-settlements/{w['run']}/reserve-ledger")).json()
    carried = (await api.get(path)).json()
    assert carried['opening_balance'] == frozen['closing_balance'] == '123.45'
    assert carried['opening_source_run_id'] == w['run']


@pytest.mark.asyncio
async def test_first_draft_opening_accepts_audited_positive_and_negative_deltas(client_a_admin, seed_data):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    path = f"/api/weg-settlements/{w['run']}/reserve-ledger"
    for amount in ('5000.00', '500.00', '-300.00'):
        response = await api.post(path + '/overrides', json={'component': 'OPENING', 'amount': amount,
            'reason': 'Audited opening import'})
        assert response.status_code == 201, response.text
    ledger = (await api.get(path)).json()
    assert ledger['opening_balance'] == '5200.00'
    assert sorted(Decimal(row['amount']) for row in ledger['entries'] if row.get('component') == 'OPENING') == [Decimal('-300'), Decimal('500'), Decimal('5000')]
    response = await api.post(path + '/overrides', json={'component': 'OPENING', 'amount': '0.00', 'reason': 'No change'})
    assert response.status_code == 422, response.text
    assert response.json()['detail']['code'] == 'RESERVE_AMOUNT_INVALID'


async def imported_reserve_draft(api, w, *, split=False):
    statement = ('Buchungstag;Wertstellung;Betrag;Name;IBAN;Verwendungszweck;Währung\n'
        '02.02.2025;03.02.2025;-1200,00;Gartenservice;;Reparatur;EUR\n').encode()
    response = await api.post(f"/api/bank-accounts/{w['bank_giro']}/imports", params={'dry_run': 'false'},
        files={'file': ('reserve.csv', statement, 'text/csv')})
    assert response.status_code == 200, response.text
    page = await api.get('/api/bank-transactions', params={'bank_account_id': w['bank_giro']})
    transaction = next(row for row in page.json()['items'] if row['amount'] == '-1200.00')
    line = {'payment_type': 'RUECKLAGENENTNAHME', 'amount': '600.00' if split else '1200.00', 'period_year': 2025, 'period_month': 2}
    assigned = await api.post(f"/api/bank-transactions/{transaction['id']}/assign", json={'lines': [line, line] if split else [line]})
    assert assigned.status_code == 200, assigned.text
    return transaction['id'], assigned.json()['payments'][0]['id']


@pytest.mark.asyncio
@pytest.mark.parametrize('evidence_part', ['none', 'cost', 'register'])
async def test_legacy_import_release_deletes_draft_and_allows_retyping(client_a_admin, client_b_admin, seed_data, migrator_session, evidence_part):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    transaction, draft = await imported_reserve_draft(api, w)
    path = f'/api/payments/{draft}'
    if evidence_part != 'none':
        evidence = await reserve_evidence(api, w)
        response = await api.patch(path, json={key: value for key, value in evidence.items() if evidence_part in key})
        assert response.status_code == 200, response.text
    assert (await client_b_admin.delete(path)).status_code == 404
    response = await api.delete(path)
    assert response.status_code == 204, response.text
    audit = (await migrator_session.execute(text("SELECT payload_diff FROM audit_event WHERE entity_id=CAST(:id AS uuid) AND action='bank_transaction.released'"), {'id': transaction})).scalar_one()
    assert audit == {'payment_id': draft, 'previous_status': 'ZUGEORDNET'}
    await migrator_session.rollback()
    retained = await api.get(path)
    assert retained.status_code == 404, retained.text
    assert (await api.delete(path)).status_code == 404
    page = await api.get('/api/bank-transactions', params={'bank_account_id': w['bank_giro'], 'status': 'NEU'})
    assert next(row for row in page.json()['items'] if row['id'] == transaction)['status'] == 'NEU'
    response = await api.post(f'/api/bank-transactions/{transaction}/assign', json={'lines': [{
        'payment_type': 'RUECKLAGENANTEIL', 'owner_profile_id': w['owner_a'], 'amount': '1200.00', 'period_year': 2025, 'period_month': 2}]})
    assert response.status_code == 200, response.text
    assert response.json()['payments'][0]['status'] == 'booked'


@pytest.mark.asyncio
@pytest.mark.parametrize('state', ['booked', 'evidenced', 'ledger', 'booking_stamp', 'split'])
async def test_imported_payout_release_refuses_bound_states(client_a_admin, seed_data, monkeypatch, state):
    from unittest.mock import AsyncMock
    from app.repositories.payment_repo import PaymentRepository
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    transaction, draft = await imported_reserve_draft(api, w, split=state == 'split')
    if state in ('booked', 'evidenced'):
        evidence = await reserve_evidence(api, w)
        response = await api.patch(f'/api/payments/{draft}', json=evidence)
        assert response.status_code == 200, response.text
        if state == 'booked':
            response = await api.post(f'/api/payments/{draft}/book')
            assert response.status_code == 200, response.text
    elif state == 'ledger':
        monkeypatch.setattr(PaymentRepository, 'has_reserve_entry', AsyncMock(return_value=True), raising=False)
    elif state == 'booking_stamp':
        original = PaymentRepository.get_by_id
        async def legacy_stamp(repo, payment_id):
            payment = await original(repo, payment_id)
            if payment is not None:
                from datetime import datetime, timezone
                payment.booked_at = datetime.now(timezone.utc)
            return payment
        monkeypatch.setattr(PaymentRepository, 'get_by_id', legacy_stamp)
    response = await api.delete(f'/api/payments/{draft}')
    assert response.status_code == 409, response.text
    assert response.json()['detail']['code'] == 'RESERVE_IMPORTED_DRAFT_BOUND'
    page = await api.get('/api/bank-transactions', params={'bank_account_id': w['bank_giro'], 'status': 'ZUGEORDNET'})
    assert next(row for row in page.json()['items'] if row['id'] == transaction)['status'] == 'ZUGEORDNET'


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['release', 'without_reset', 'movement', 'ledger', 'booking_stamp', 'evidenced', 'booked', 'split'])
async def test_database_only_releases_unbooked_unbacked_import_without_ledger(client_a_admin, seed_data, migrator_session, case):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    transaction, draft = await imported_reserve_draft(api, w, split=case == 'split')
    if case in ('evidenced', 'booked'):
        evidence = await reserve_evidence(api, w)
        assert (await api.patch(f'/api/payments/{draft}', json=evidence)).status_code == 200
        if case == 'booked':
            assert (await api.post(f'/api/payments/{draft}/book')).status_code == 200
    await migrator_session.execute(text("SELECT set_config('app.current_user_id',:actor,true),set_config('app.current_org_id',:org,true)"),
        {'actor': str(seed_data['user_a_admin_id']), 'org': str(seed_data['org_a_id'])})
    if case == 'ledger':
        # Reproduce a historical draft-linked row; normal inserts already forbid it.
        await migrator_session.execute(text('ALTER TABLE reserve_ledger_entry DISABLE TRIGGER guard_reserve_ledger'))
        await migrator_session.execute(text("""INSERT INTO reserve_ledger_entry
            (organization_id,property_id,settlement_run_id,entry_date,component,amount,reason,payment_id,created_by)
            VALUES (:org,:property,:run,'2025-02-03','CONTRIBUTION',0,'Historical retained reference',:payment,:actor)"""),
            {'org': seed_data['org_a_id'], 'property': w['property'], 'run': w['run'], 'payment': draft, 'actor': seed_data['user_a_admin_id']})
        await migrator_session.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
        await migrator_session.execute(text('ALTER TABLE reserve_ledger_entry ENABLE TRIGGER guard_reserve_ledger'))
    if case == 'booking_stamp':
        await migrator_session.execute(text("UPDATE payment SET booked_at=now() WHERE id=CAST(:id AS uuid)"), {'id': draft})
    if case != 'without_reset':
        await migrator_session.execute(text("UPDATE bank_transaction SET status='NEU' WHERE id=CAST(:id AS uuid)"), {'id': transaction})
    statement = text("UPDATE payment SET cancelled_at=now()" + (",amount=1" if case == 'movement' else '') + " WHERE id=CAST(:id AS uuid)")
    if case == 'release':
        await migrator_session.execute(statement, {'id': draft})
        assert await migrator_session.scalar(text("SELECT cancelled_at IS NOT NULL FROM payment WHERE id=CAST(:id AS uuid)"), {'id': draft})
    else:
        with pytest.raises(DBAPIError):
            async with migrator_session.begin_nested():
                await migrator_session.execute(statement, {'id': draft})


@pytest.mark.asyncio
async def test_opening_reattestation_records_only_the_difference_and_stays_first_year_only(client_a_admin, seed_data, migrator_session):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    path = f"/api/weg-settlements/{w['run']}/reserve-ledger"
    async def attest(run, balance, reason='Opening reconciled against bank records'):
        return await api.post('/api/financial-input-coverage', json={'subject_kind': 'settlement',
            'subject_id': run, 'values': {**paid(w), 'reserve_opening_balance': balance, 'reserve_override_reason': reason},
            'confirmed_complete': True, 'reason': 'Complete synthetic records'})
    for balance, expected in [('5000.00', ['5000.00']), ('5500.00', ['5000.00', '500.00']),
                              ('5200.00', ['5000.00', '500.00', '-300.00'])]:
        response = await attest(w['run'], balance)
        assert response.status_code == 201, response.text
        ledger = (await api.get(path)).json()
        assert ledger['opening_balance'] == balance
        rows = [row for row in ledger['entries'] if row.get('component') == 'OPENING']
        assert sorted(Decimal(row['amount']) for row in rows) == sorted(map(Decimal, expected))
        assert all(row['reason'] == 'Opening reconciled against bank records' for row in rows)
    rows = (await migrator_session.execute(text("SELECT amount,created_by FROM reserve_ledger_entry WHERE settlement_run_id=CAST(:id AS uuid) ORDER BY created_at,id"), {'id': w['run']})).all()
    assert all(row.created_by == seed_data['user_a_admin_id'] for row in rows)
    assert await migrator_session.scalar(text("SELECT count(*) FROM audit_event WHERE entity_id=CAST(:id AS uuid) AND action='reserve.override_added'"), {'id': w['run']}) == 3
    refused = await attest(w['run'], '5300.00', '')
    assert refused.status_code == 422, refused.text
    assert refused.json()['detail']['code'] == 'RESERVE_CORRECTION_REASON_REQUIRED'
    unchanged = await attest(w['run'], '5200.00')
    assert unchanged.status_code == 201, unchanged.text
    later_plan = await ownership_plan(api, w, 2026)
    later = await api.post('/api/weg-settlements', json={'property_id': w['property'], 'economic_plan_id': later_plan,
        'period_start': '2026-01-01', 'period_end': '2026-12-31'})
    assert later.status_code == 201, later.text
    refused = await attest(later.json()['id'], '6000.00')
    assert refused.status_code == 422, refused.text
    assert refused.json()['detail']['code'] == 'RESERVE_OPENING_IMPORT_ONLY'
    await finalize(api, w)
    refused = await attest(w['run'], '6000.00')
    assert refused.status_code == 409, refused.text
    assert refused.json()['detail']['code'] == 'RESERVE_PERIOD_FINALIZED'
    refused = await api.post(path + '/overrides', json={'component': 'OPENING', 'amount': '1.00', 'reason': 'Frozen'})
    assert refused.status_code == 409, refused.text
    assert refused.json()['detail']['code'] == 'RESERVE_PERIOD_FINALIZED'


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['weg', 'asset'])
@pytest.mark.parametrize('database_backstop', [False, True])
async def test_imported_opening_blocks_creation_of_an_earlier_period(
        client_a_admin, seed_data, monkeypatch, kind, database_backstop):
    from unittest.mock import AsyncMock
    from app.repositories.reserve_ledger_repo import ReserveLedgerRepository

    api = client_a_admin
    w = _seed_weg(seed_data)
    plan = await _create_and_finalize_plan(api, w)
    later = await api.post('/api/weg-settlements/asset-reports', json={
        'property_id': w['property'], 'period_start': '2026-01-01', 'period_end': '2026-12-31'})
    assert later.status_code == 201, later.text
    response = await api.post(f"/api/weg-settlements/{later.json()['id']}/reserve-ledger/overrides", json={
        'component': 'OPENING', 'amount': '5000.00', 'reason': 'Audited first opening'})
    assert response.status_code == 201, response.text
    if database_backstop:
        monkeypatch.setattr(ReserveLedgerRepository, 'has_later_opening_import', AsyncMock(return_value=False), raising=False)
    body = {'property_id': w['property'], 'period_start': '2025-01-01', 'period_end': '2025-12-31'}
    if kind == 'weg':
        body['economic_plan_id'] = plan
    response = await api.post('/api/weg-settlements' + ('/asset-reports' if kind == 'asset' else ''), json=body)
    assert response.status_code == 422, response.text
    assert response.json()['detail']['code'] == 'RESERVE_OPENING_IMPORT_EXISTS'
    assert (await api.get(f"/api/weg-settlements/{later.json()['id']}/reserve-ledger")).json()['opening_balance'] == '5000.00'


@pytest.mark.asyncio
async def test_earliest_import_carries_exact_frozen_closing_through_later_years(client_a_admin, seed_data):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    response = await api.post(f"/api/weg-settlements/{w['run']}/reserve-ledger/overrides", json={
        'component': 'OPENING', 'amount': '700.00', 'reason': 'Audited earliest opening'})
    assert response.status_code == 201, response.text
    await payment(api, w, '123.45')
    await finalize(api, w)
    previous = w['run']
    values = {'bank_balances': [{'bank_account_id': w[key], 'label': key, 'account_type': kind, 'balance': '0.00'}
        for key, kind in [('bank_giro', 'LAUFEND'), ('bank_ruecklage', 'RUECKLAGE')]], 'receivables': [], 'payables': []}
    for year in (2026, 2027):
        response = await api.post('/api/weg-settlements/asset-reports', json={
            'property_id': w['property'], 'period_start': f'{year}-01-01', 'period_end': f'{year}-12-31'})
        assert response.status_code == 201, response.text
        run = response.json()['id']
        ledger = (await api.get(f'/api/weg-settlements/{run}/reserve-ledger')).json()
        frozen = (await api.get(f'/api/weg-settlements/{previous}/reserve-ledger')).json()
        assert ledger['opening_balance'] == frozen['closing_balance'] == '823.45'
        assert ledger['opening_source_run_id'] == previous
        await attest_fixture(api, 'settlement', run, values)
        response = await api.post(f'/api/weg-settlements/asset-reports/{run}/finalize', json=values)
        assert response.status_code == 200, response.text
        previous = run


@pytest.mark.asyncio
@pytest.mark.parametrize('constraint,sqlstate,status,code', [
    ('reserve_opening_import_only', '23514', 422, 'RESERVE_OPENING_IMPORT_ONLY'),
    ('reserve_period_finalized', '23514', 409, 'RESERVE_PERIOD_FINALIZED'),
    ('reserve_ledger_entry_organization_id_payment_id_key', '23505', 409, 'RESERVE_PAYMENT_ALREADY_ASSIGNED'),
])
async def test_override_database_errors_keep_the_specific_reserve_code(monkeypatch, constraint, sqlstate, status, code):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import uuid4
    from fastapi import HTTPException
    from sqlalchemy.exc import IntegrityError
    from app.api import weg_settlements as routes

    class DriverError(Exception):
        pass
    driver = DriverError()
    driver.sqlstate = sqlstate
    driver.__cause__ = DriverError()
    driver.__cause__.constraint_name = constraint
    service = SimpleNamespace(subject=AsyncMock(), add=AsyncMock(side_effect=IntegrityError(None, None, driver)))
    monkeypatch.setattr(routes, 'ReserveLedgerService', lambda *args: service)
    with pytest.raises(HTTPException) as refusal:
        await routes.reserve_override(uuid4(), routes.ReserveOverrideRequest(
            component='OPENING', amount='1.00', reason='Audited import'),
            SimpleNamespace(organization_id=uuid4(), user_id=uuid4()), None)
    assert refusal.value.status_code == status
    assert refusal.value.detail['code'] == code


@pytest.mark.asyncio
@pytest.mark.parametrize('constraint,status,code', [
    ('reserve_opening_import_only', 422, 'RESERVE_OPENING_IMPORT_ONLY'),
    ('reserve_period_finalized', 409, 'RESERVE_PERIOD_FINALIZED'),
])
async def test_attestation_maps_opening_database_refusals(monkeypatch, constraint, status, code):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import uuid4
    from fastapi import HTTPException
    from app.api import financial_input_coverage as routes
    from app.services.financial_scope_service import FinancialScopeService
    class DriverError(Exception):
        sqlstate = '23514'
    driver = DriverError()
    driver.__cause__ = DriverError()
    driver.__cause__.constraint_name = constraint
    failure = DBAPIError(None, None, driver)
    subject = SimpleNamespace(run_type='WEG_JAHRESABRECHNUNG')
    monkeypatch.setattr(FinancialScopeService, 'lock_inputs', AsyncMock())
    monkeypatch.setattr(routes, 'SettlementRunRepository', lambda *args: SimpleNamespace(get_by_id_for_update=AsyncMock(return_value=subject)))
    monkeypatch.setattr(routes, 'FinancialInputPolicy', lambda *args: SimpleNamespace(attest=AsyncMock(side_effect=failure)))
    with pytest.raises(HTTPException) as refused:
        await routes.attest(routes.Attestation(subject_kind='settlement', subject_id=uuid4(),
            values={'paid_advances': []}, confirmed_complete=True, reason='Reviewed inputs'),
            SimpleNamespace(organization_id=uuid4(), user_id=uuid4()), None)
    assert refused.value.status_code == status
    assert refused.value.detail['code'] == code


@pytest.mark.asyncio
async def test_unchanged_opening_reattestation_refreshes_stale_cash_coverage(client_a_admin, seed_data):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    values = {**paid(w), 'reserve_opening_balance': '5000.00', 'reserve_override_reason': 'Reviewed opening'}
    first = await attest_fixture(api, 'settlement', w['run'], values)
    repeated = await attest_fixture(api, 'settlement', w['run'], values)
    assert repeated['id'] == first['id']
    await payment(api, w, '100.00')
    stale = await api.post(f"/api/weg-settlements/{w['run']}/compute", json=values)
    assert stale.status_code == 422, stale.text
    assert stale.json()['detail']['blockers'][0]['code'] == 'coverage_stale'
    refreshed = await attest_fixture(api, 'settlement', w['run'], values)
    assert refreshed['revision'] == first['revision'] + 1
    computed = await api.post(f"/api/weg-settlements/{w['run']}/compute", json=values)
    assert computed.status_code == 200, computed.text
    ledger = (await api.get(f"/api/weg-settlements/{w['run']}/reserve-ledger")).json()
    assert [row['amount'] for row in ledger['entries'] if row.get('component') == 'OPENING'] == ['5000.00']


@pytest.mark.asyncio
@pytest.mark.parametrize('database_backstop', [False, True])
async def test_correction_period_keeps_finalized_opening_but_accepts_evidenced_corrections(
        client_a_admin, seed_data, migrator_session, database_backstop):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    report = await api.post('/api/weg-settlements/asset-reports', json={'property_id': w['property'],
        'period_start': '2025-01-01', 'period_end': '2025-12-31'})
    assert report.status_code == 201, report.text
    for run, amount in [(w['run'], '5000.00'), (report.json()['id'], '7000.00')]:
        response = await api.post(f'/api/weg-settlements/{run}/reserve-ledger/overrides',
            json={'component': 'OPENING', 'amount': amount, 'reason': 'Reviewed import'})
        assert response.status_code == 201, response.text
    await finalize(api, w)
    correction = await api.post(f"/api/settlements/{w['run']}/correction")
    assert correction.status_code == 201, correction.text
    run = correction.json()['id']
    path = f'/api/weg-settlements/{run}/reserve-ledger'
    assert (await api.get(path)).json()['opening_balance'] == '12000.00'
    if database_backstop:
        await migrator_session.execute(text("SELECT set_config('app.current_user_id',:actor,true),set_config('app.current_org_id',:org,true)"),
            {'actor': str(seed_data['user_a_admin_id']), 'org': str(seed_data['org_a_id'])})
        with pytest.raises(DBAPIError) as refused:
            async with migrator_session.begin_nested():
                await migrator_session.execute(text("""INSERT INTO reserve_ledger_entry
                    (organization_id,property_id,settlement_run_id,entry_date,component,amount,reason,created_by)
                    VALUES (:org,:property,:run,'2025-01-01','OPENING',100,'Frozen import',:actor)"""),
                    {'org': seed_data['org_a_id'], 'property': w['property'], 'run': run, 'actor': seed_data['user_a_admin_id']})
        assert refused.value.orig.__cause__.constraint_name == 'reserve_period_finalized'
        await migrator_session.rollback()
    else:
        refused = await api.post(path + '/overrides', json={'component': 'OPENING', 'amount': '100.00', 'reason': 'Frozen import'})
        assert refused.status_code == 409, refused.text
        assert refused.json()['detail']['code'] == 'RESERVE_PERIOD_FINALIZED'
    evidence = await reserve_evidence(api, w)
    for component in ('WITHDRAWAL', 'ADJUSTMENT'):
        response = await api.post(path + '/overrides', json={'component': component, 'amount': '1.00',
            'reason': 'Evidence-backed correction', **{key.removeprefix('reserve_'): value for key, value in evidence.items()}})
        assert response.status_code == 201, response.text


@pytest.mark.asyncio
@pytest.mark.parametrize('overlap', [False, True])
@pytest.mark.parametrize('database_backstop', [False, True])
async def test_opening_refuses_earlier_or_overlapping_period_on_both_input_paths(
        client_a_admin, seed_data, monkeypatch, overlap, database_backstop):
    from unittest.mock import AsyncMock
    from app.repositories.reserve_ledger_repo import ReserveLedgerRepository
    from tests.test_financial_input_policy import attest
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    report = await api.post('/api/weg-settlements/asset-reports', json={'property_id': w['property'],
        'period_start': '2025-01-01' if overlap else '2026-01-01',
        'period_end': '2025-06-30' if overlap else '2026-12-31'})
    assert report.status_code == 201, report.text
    if database_backstop:
        monkeypatch.setattr(ReserveLedgerRepository, 'has_earlier_ledger', AsyncMock(return_value=False))
        monkeypatch.setattr(ReserveLedgerRepository, 'has_overlapping_ledger', AsyncMock(return_value=False), raising=False)
    run = report.json()['id']
    body = {'component': 'OPENING', 'amount': '5000.00', 'reason': 'Conflicting import'}
    refused = await api.post(f'/api/weg-settlements/{run}/reserve-ledger/overrides', json=body)
    assert refused.status_code == 422, refused.text
    assert refused.json()['detail']['code'] == 'RESERVE_OPENING_IMPORT_ONLY'
    values = {'bank_balances': [{'bank_account_id': w[key], 'account_type': kind, 'label': kind, 'balance': '0.00'}
        for key, kind in [('bank_giro', 'LAUFEND'), ('bank_ruecklage', 'RUECKLAGE')]],
        'receivables': [], 'payables': [], 'reserve_opening_balance': '5000.00', 'reserve_override_reason': 'Conflicting import'}
    refused = await attest(api, 'settlement', run, values)
    assert refused.status_code == 422, refused.text
    assert refused.json()['detail']['code'] == 'RESERVE_OPENING_IMPORT_ONLY'


@pytest.mark.asyncio
@pytest.mark.parametrize('reserve_fields', [False, True])
async def test_finalized_attestation_uses_reserve_code_only_for_reserve_fields(client_a_admin, seed_data, reserve_fields):
    from tests.test_financial_input_policy import attest
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    await finalize(api, w)
    values = {**paid(w), **({'reserve_opening_balance': '1.00'} if reserve_fields else {})}
    refused = await attest(api, 'settlement', w['run'], values)
    assert refused.status_code == 409, refused.text
    assert refused.json()['detail'] == ({'code': 'RESERVE_PERIOD_FINALIZED',
        'message': 'Die Rücklage dieses Zeitraums ist finalisiert.'} if reserve_fields else 'Only draft inputs can be attested')


@pytest.mark.asyncio
async def test_direct_ledger_write_locks_its_cost_evidence(client_a_admin, seed_data, migrator_session, app_rw_session):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    evidence = await reserve_evidence(api, w)
    # NO KEY UPDATE permits the FK's KEY SHARE lock, but blocks the guard's UPDATE lock.
    await migrator_session.execute(text('SELECT id FROM cost_entry WHERE id=:cost FOR NO KEY UPDATE'),
        {'cost': evidence['reserve_cost_entry_id']})
    await app_rw_session.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:actor,true)"),
        {'org': str(seed_data['org_a_id']), 'actor': str(seed_data['user_a_admin_id'])})
    await app_rw_session.execute(text("SET LOCAL lock_timeout='200ms'"))
    with pytest.raises(DBAPIError) as blocked:
        async with app_rw_session.begin_nested():
            await app_rw_session.execute(text("""INSERT INTO reserve_ledger_entry
                (organization_id,property_id,settlement_run_id,entry_date,component,amount,reason,cost_entry_id,register_entry_id,created_by)
                VALUES (:org,:property,:run,'2025-01-01','WITHDRAWAL',1,'Concurrent evidence',:cost,:register,:actor)"""),
                {'org': seed_data['org_a_id'], 'property': w['property'], 'run': w['run'],
                 'cost': evidence['reserve_cost_entry_id'], 'register': evidence['reserve_register_entry_id'],
                 'actor': seed_data['user_a_admin_id']})
    assert blocked.value.orig.sqlstate == '55P03'


@pytest.mark.asyncio
@pytest.mark.parametrize('database_backstop', [False, True])
async def test_compatible_weg_scope_cannot_reimport_a_finalized_opening(
        migrator_session, app_rw_session, scope_seed, worker_tenant, monkeypatch, database_backstop):
    from uuid import uuid4
    from unittest.mock import AsyncMock
    from app.repositories.reserve_ledger_repo import ReserveLedgerRepository
    from app.services.reserve_ledger_service import ReserveLedgerService
    from tests.test_scope_grants import scope_fixture, bind

    session = migrator_session
    api, _ = worker_tenant
    ids = await scope_fixture(session, scope_seed)
    ids['next_scope'] = uuid4()
    await session.execute(text("""INSERT INTO accounting_scope
        (id,organization_id,property_id,scope_type,label,valid_from,whole_property,origin,created_by)
        VALUES (:next_scope,:org,:property,'WEG','Second whole-property scope','2025-01-01',true,'REVIEWED',:actor)"""), ids)
    for scope in (ids['scope'], ids['next_scope']):
        await session.execute(text("""INSERT INTO scope_grant
            (organization_id,scope_id,user_id,capabilities,granted_by,reason)
            VALUES (:org,:scope,:actor,ARRAY['read','prepare','confirm','approve','export'],:actor,'Reviewed report access')"""),
            {**ids, 'scope': scope})
    await bind(session, ids)
    await session.commit()
    body = {'property_id': str(ids['property']), 'period_start': '2025-01-01', 'period_end': '2025-12-31'}
    original = await api.post('/api/weg-settlements/asset-reports', json={**body, 'accounting_scope_id': str(ids['scope'])})
    assert original.status_code == 201, original.text
    run = original.json()['id']
    opening = await api.post(f'/api/weg-settlements/{run}/reserve-ledger/overrides', json={
        'component': 'OPENING', 'amount': '5000.00', 'reason': 'Reviewed first import'})
    assert opening.status_code == 201, opening.text
    values = {'bank_balances': [{'bank_account_id': str(ids['bank']), 'account_type': 'LAUFEND', 'label': 'Test', 'balance': '0.00'}],
        'receivables': [], 'payables': []}
    await attest_fixture(api, 'settlement', run, values)
    finalized = await api.post(f'/api/weg-settlements/asset-reports/{run}/finalize', json=values)
    assert finalized.status_code == 200, finalized.text
    sibling = await api.post('/api/weg-settlements/asset-reports', json={**body, 'accounting_scope_id': str(ids['next_scope'])})
    assert sibling.status_code == 201, sibling.text
    if database_backstop:
        monkeypatch.setattr(ReserveLedgerRepository, 'has_finalized_opening_period', AsyncMock(return_value=False))
    else:
        await app_rw_session.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:actor,true)"),
            {'org': str(ids['org']), 'actor': str(ids['actor'])})
        ledger = ReserveLedgerService(app_rw_session, ids['org'], ids['actor'])
        subject = await ledger.subject(sibling.json()['id'], write=True)
        assert await ledger.repo.has_finalized_opening_period(subject) is True
        await app_rw_session.rollback()
    refused = await api.post(f"/api/weg-settlements/{sibling.json()['id']}/reserve-ledger/overrides", json={
        'component': 'OPENING', 'amount': '100.00', 'reason': 'Incompatible reimport'})
    assert refused.status_code == 409, refused.text
    assert refused.json()['detail']['code'] == 'RESERVE_PERIOD_FINALIZED'
    assert (await api.get(f'/api/weg-settlements/{run}/reserve-ledger')).json()['opening_balance'] == '5000.00'


@pytest.mark.asyncio
@pytest.mark.parametrize('import_first', [False, True])
@pytest.mark.parametrize('overlap', [False, True])
async def test_whole_property_weg_scopes_share_the_first_period_rule(migrator_session, scope_seed, monkeypatch, import_first, overlap):
    from uuid import uuid4
    from unittest.mock import AsyncMock
    from fastapi import HTTPException
    from app.services.reserve_ledger_service import ReserveLedgerService
    from app.services.weg_settlement_service import WegSettlementService
    from tests.test_financial_scope_boundary import actor
    from tests.test_scope_grants import scope_fixture

    session = migrator_session
    ids = await scope_fixture(session, scope_seed)
    ids['next_scope'] = uuid4()
    await session.execute(text("""INSERT INTO accounting_scope
        (id,organization_id,property_id,scope_type,label,valid_from,whole_property,origin,created_by)
        VALUES (:next_scope,:org,:property,'WEG','Next reserve year','2025-01-01',true,'REVIEWED',:actor)"""), ids)
    await session.execute(text("""INSERT INTO scope_grant
        (organization_id,scope_id,user_id,capabilities,granted_by,reason)
        VALUES (:org,:next_scope,:clerk,ARRAY['read','prepare'],:actor,'Prepare next reserve year')"""), ids)
    await actor(session, ids)
    service = WegSettlementService(session, ids['org'], ids['clerk'])
    if not import_first:
        await service.create_asset_report_draft(ids['property'], date(2025, 1, 1), date(2025, 12, 31), accounting_scope_id=ids['scope'])
    await session.execute(text("SELECT set_config('app.current_scope_id','',true)"))
    later = await service.create_asset_report_draft(ids['property'], date(2025, 1, 1) if overlap else date(2026, 1, 1),
        date(2025, 6, 30) if overlap else date(2026, 12, 31), accounting_scope_id=ids['next_scope'])
    ledger = ReserveLedgerService(session, ids['org'], ids['clerk'])
    monkeypatch.setattr(ledger, 'read', AsyncMock(return_value={'frozen': False, 'entries': []}))
    if import_first:
        await ledger.add(later, 'OPENING', Decimal('5000'), 'Audited first import')
        await session.execute(text("SELECT set_config('app.current_scope_id','',true)"))
    with pytest.raises(HTTPException) as refused:
        if import_first:
            await service.create_asset_report_draft(ids['property'], date(2025, 1, 1), date(2025, 12, 31), accounting_scope_id=ids['scope'])
        else:
            await ledger.add(later, 'OPENING', Decimal('5000'), 'Not the earliest WEG period')
    assert refused.value.status_code == 422
    assert refused.value.detail['code'] == ('RESERVE_OPENING_IMPORT_EXISTS' if import_first else 'RESERVE_OPENING_IMPORT_ONLY')
    await session.execute(text('RESET ROLE'))


@pytest.mark.asyncio
@pytest.mark.parametrize('component,amount', [('WITHDRAWAL', '1000000.00'), ('ADJUSTMENT', '50000.00'),
                                            ('ADJUSTMENT', '-50000.00')])
async def test_typed_correction_cannot_exceed_confirmed_cost(client_a_admin, seed_data, component, amount):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    evidence = {k.removeprefix('reserve_'): v for k, v in (await reserve_evidence(api, w)).items()}
    response = await api.post(f"/api/weg-settlements/{w['run']}/reserve-ledger/overrides", json={
        'component': component, 'amount': amount, 'reason': 'Over cost amount', **evidence})
    assert response.status_code == 422, response.text
    assert response.json()['detail']['code'] == 'RESERVE_PAYOUT_EVIDENCE_REQUIRED'


@pytest.mark.asyncio
@pytest.mark.parametrize('component', ['WITHDRAWAL', 'CONTRIBUTION'])
@pytest.mark.parametrize('amount', ['-999999.00', '0.00'])
async def test_typed_cash_correction_requires_positive_amount(client_a_admin, seed_data, component, amount):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    evidence = {k.removeprefix('reserve_'): v for k, v in (await reserve_evidence(api, w)).items()}
    response = await api.post(f"/api/weg-settlements/{w['run']}/reserve-ledger/overrides", json={
        'component': component, 'amount': amount, 'reason': 'Invalid sign', **evidence})
    assert response.status_code == 422, response.text
    assert response.json()['detail']['code'] == 'RESERVE_AMOUNT_INVALID'


@pytest.mark.asyncio
@pytest.mark.parametrize('first', ['WITHDRAWAL', 'ADJUSTMENT', 'PAYOUT'])
async def test_corrections_share_the_remaining_cost_with_booked_payouts(client_a_admin, seed_data, first):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    payment_evidence = await reserve_evidence(api, w)
    evidence = {k.removeprefix('reserve_'): v for k, v in payment_evidence.items()}
    path = f"/api/weg-settlements/{w['run']}/reserve-ledger/overrides"
    if first == 'PAYOUT':
        await payment(api, w, '3000.00', kind='RUECKLAGENENTNAHME', direction='OUT', evidence=payment_evidence)
    else:
        response = await api.post(path, json={'component': first, 'amount': '-3000.00' if first == 'ADJUSTMENT' else '3000.00',
            'reason': 'Documented correction', **evidence})
        assert response.status_code == 201, response.text
    for amount, status in [('1200.00', 422), ('1000.00', 201), ('0.01', 422)]:
        response = await api.post(path, json={'component': 'WITHDRAWAL', 'amount': amount,
            'reason': 'Use remaining confirmed cost', **evidence})
        assert response.status_code == status, response.text
        if status == 422:
            assert response.json()['detail']['code'] == 'RESERVE_PAYOUT_EVIDENCE_REQUIRED'


@pytest.mark.asyncio
async def test_booking_includes_typed_corrections_in_cost_cap(client_a_admin, seed_data, migrator_session):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    evidence = await reserve_evidence(api, w)
    response = await api.post(f"/api/weg-settlements/{w['run']}/reserve-ledger/overrides", json={
        'component': 'ADJUSTMENT', 'amount': '3000.00', 'reason': 'Documented correction',
        **{k.removeprefix('reserve_'): v for k, v in evidence.items()}})
    assert response.status_code == 201, response.text
    draft = await api.post('/api/payments', json={'bank_account_id': w['bank_giro'], 'direction': 'OUT',
        'amount': '1200.00', 'value_date': '2025-02-01', 'payment_type': 'RUECKLAGENENTNAHME', **evidence})
    assert draft.status_code == 201, draft.text
    response = await api.post(f"/api/payments/{draft.json()['id']}/book")
    assert response.status_code == 422, response.text
    assert response.json()['detail']['code'] == 'RESERVE_PAYOUT_EVIDENCE_REQUIRED'
    with pytest.raises(DBAPIError) as refusal:
        async with migrator_session.begin_nested():
            await migrator_session.execute(text("UPDATE payment SET status='booked',booked_at=now(),booked_by=:actor,retention_until='2034-12-31' WHERE id=CAST(:id AS uuid)"),
                {'actor': seed_data['user_a_admin_id'], 'id': draft.json()['id']})
    assert refusal.value.orig.__cause__.constraint_name == 'reserve_payout_evidence_required'


@pytest.mark.asyncio
@pytest.mark.parametrize('case,component,amount,constraint', [
    ('earlier_draft', 'OPENING', '5000.00', 'reserve_opening_import_only'),
    ('duplicate', 'OPENING', '-7.00', None),
    ('sign', 'OPENING', '0.00', 'reserve_amount_invalid'),
    ('sign', 'WITHDRAWAL', '-1.00', 'reserve_amount_invalid'),
    ('sign', 'CONTRIBUTION', '0.00', 'reserve_amount_invalid'),
    ('cap', 'ADJUSTMENT', '-50000.00', 'reserve_payout_evidence_required'),
    ('payout', 'WITHDRAWAL', '1200.00', 'reserve_payout_evidence_required'),
    ('correction', 'ADJUSTMENT', '1200.00', 'reserve_payout_evidence_required'),
    ('valid', 'WITHDRAWAL', '1000.00', None),
])
async def test_database_mirrors_opening_and_correction_limits(client_a_admin, seed_data, migrator_session,
                                                           case, component, amount, constraint):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    evidence = {} if component == 'OPENING' else await reserve_evidence(api, w)
    run, entry_date = w['run'], '2025-01-01'
    if case == 'earlier_draft':
        response = await api.post('/api/weg-settlements/asset-reports', json={'property_id': w['property'],
            'period_start': '2026-01-01', 'period_end': '2026-12-31'})
        assert response.status_code == 201, response.text
        run, entry_date = response.json()['id'], '2026-01-01'
    elif case in ('duplicate', 'correction'):
        response = await api.post(f"/api/weg-settlements/{run}/reserve-ledger/overrides", json={
            'component': 'OPENING' if case == 'duplicate' else 'WITHDRAWAL', 'amount': '3000.00',
            'reason': 'Retained first entry', **{k.removeprefix('reserve_'): v for k, v in evidence.items()}})
        assert response.status_code == 201, response.text
    elif case == 'payout':
        await payment(api, w, '3000.00', kind='RUECKLAGENENTNAHME', direction='OUT', evidence=evidence)
    await migrator_session.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),
        {'actor': str(seed_data['user_a_admin_id'])})
    await migrator_session.execute(text("SELECT set_config('app.current_org_id',:org,true)"),
        {'org': str(seed_data['org_a_id'])})
    async def insert():
        await migrator_session.execute(text("""INSERT INTO reserve_ledger_entry
                (organization_id,property_id,settlement_run_id,entry_date,component,amount,reason,cost_entry_id,register_entry_id,created_by)
                VALUES (:org,:property,:run,CAST(:date AS date),:component,:amount,'Database boundary',:cost,:register,:actor)"""),
                {'org': seed_data['org_a_id'], 'property': w['property'], 'run': run, 'date': date.fromisoformat(entry_date),
                 'component': component, 'amount': Decimal(amount), 'actor': seed_data['user_a_admin_id'],
                 'cost': evidence.get('reserve_cost_entry_id'), 'register': evidence.get('reserve_register_entry_id')})
    if constraint is None:
        await insert()
        return
    message = 'Active reserve withdrawals exceed confirmed cost amount' if case in ('cap', 'payout', 'correction') else None
    with pytest.raises(DBAPIError, match=message) as refusal:
        async with migrator_session.begin_nested():
            await insert()
    assert refusal.value.orig.__cause__.constraint_name == constraint


@pytest.mark.asyncio
async def test_evidence_picker_register_exposes_resolution_results(client_a_admin, seed_data):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    evidence = await reserve_evidence(api, w)
    response = await api.get(f"/api/meetings/register/{w['property']}")
    assert response.status_code == 200, response.text
    row = next(row for row in response.json() if row['id'] == evidence['reserve_register_entry_id'])
    assert row['result'] == 'ANGENOMMEN'


@pytest.mark.asyncio
async def test_typed_withdrawal_target_without_evidence_is_refused(client_a_admin, seed_data):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    response = await api.post('/api/financial-input-coverage', json={'subject_kind': 'settlement', 'subject_id': w['run'],
        'values': {**paid(w), 'reserve_withdrawals_total': '1200.00', 'reserve_override_reason': 'Unsupported manual target'},
        'confirmed_complete': True, 'reason': 'Reviewed records'})
    assert response.status_code == 422, response.text
    assert response.json()['detail']['code'] == 'RESERVE_PAYOUT_EVIDENCE_REQUIRED'


@pytest.mark.asyncio
async def test_compute_keeps_its_guard_for_historical_unbacked_payouts(client_a_admin, seed_data, monkeypatch):
    from app.repositories.reserve_ledger_repo import ReserveLedgerRepository
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    await payment(api, w, '100.00', kind='RUECKLAGENENTNAHME', direction='OUT')
    original = ReserveLedgerRepository.payments
    async def historical_payments(self, run, start=None):
        rows = await original(self, run, start)
        for row in rows:
            if row.payment_type == 'RUECKLAGENENTNAHME':
                # Model a retained pre-gate booking without mutating its stored original.
                row.__dict__['reserve_cost_entry_id'] = None
                row.__dict__['reserve_register_entry_id'] = None
        return rows
    monkeypatch.setattr(ReserveLedgerRepository, 'payments', historical_payments)
    await attest_fixture(api, 'settlement', w['run'], paid(w))
    refused = await api.post(f"/api/weg-settlements/{w['run']}/compute", json=paid(w))
    assert refused.status_code == 422, refused.text
    assert refused.json()['detail']['code'] == 'RESERVE_PAYOUT_EVIDENCE_REQUIRED'


@pytest.mark.asyncio
@pytest.mark.parametrize('field', ['reserve_cost_entry_id', 'reserve_register_entry_id'])
async def test_payout_draft_foreign_and_unknown_evidence_are_indistinguishable(client_a_admin, client_b_admin, seed_data, field):
    import uuid
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    foreign_property = await client_b_admin.post('/api/properties', json={'name': 'Foreign evidence', 'management_type': 'WEG'})
    assert foreign_property.status_code == 201, foreign_property.text
    category = (await client_b_admin.get('/api/cost-categories')).json()[0]
    foreign_cost = await client_b_admin.post('/api/cost-entries', json={
        'property_id': foreign_property.json()['id'], 'cost_category_id': category['id'],
        'invoice_date': '2025-01-15', 'service_period_from': '2025-01-01', 'service_period_to': '2025-12-31',
        'gross_amount': '100.00', 'tenant_apportionable': 'UMLAGEFAEHIG'})
    assert foreign_cost.status_code == 201, foreign_cost.text
    evidence = await reserve_evidence(client_b_admin, {'property': foreign_property.json()['id'], 'ce_garten': foreign_cost.json()['id']})
    responses = []
    for identifier in (evidence[field], uuid.uuid4()):
        response = await api.post('/api/payments', json={'bank_account_id': w['bank_giro'], 'direction': 'OUT',
            'amount': '100.00', 'value_date': '2025-02-01', 'payment_type': 'RUECKLAGENENTNAHME', field: str(identifier)})
        assert response.status_code == 404, response.text
        responses.append(response.json())
    assert responses[0] == responses[1]


@pytest.mark.asyncio
async def test_booking_limits_total_active_payouts_to_confirmed_cost(client_a_admin, seed_data):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    evidence = await reserve_evidence(api, w)
    original = await payment(api, w, '3000.00', kind='RUECKLAGENENTNAHME', direction='OUT', evidence=evidence)
    draft = await api.post('/api/payments', json={'bank_account_id': w['bank_giro'], 'direction': 'OUT',
        'amount': '1200.00', 'value_date': '2025-02-01', 'payment_type': 'RUECKLAGENENTNAHME', **evidence})
    assert draft.status_code == 201, draft.text
    path = f"/api/payments/{draft.json()['id']}"
    refused = await api.post(path + '/book')
    assert refused.status_code == 422, refused.text
    assert refused.json()['detail']['code'] == 'RESERVE_PAYOUT_EVIDENCE_REQUIRED'
    assert (await api.post(f'/api/payments/{original}/reverse')).status_code == 201
    accepted = await api.post(path + '/book')
    assert accepted.status_code == 200, accepted.text


@pytest.mark.asyncio
@pytest.mark.parametrize('corrected_gross,amount,status', [
    (None, '4000.00', 422), ('9000.00', '4000.00', 200), ('3000.00', '0.01', 422),
])
async def test_cost_correction_chain_keeps_payout_cap_and_frozen_references(
        client_a_admin, seed_data, corrected_gross, amount, status):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    evidence = await reserve_evidence(api, w)
    original = await payment(api, w, '4000.00', kind='RUECKLAGENENTNAHME', direction='OUT', evidence=evidence)
    before = (await api.get(f'/api/payments/{original}')).json()
    changes = {'document_number': 'RE-2025-corrected'}
    if corrected_gross:
        changes['gross_amount'] = corrected_gross
    response = await api.post(f"/api/cost-entries/{w['ce_garten']}/correction", json=changes)
    assert response.status_code == 201, response.text
    current = response.json()['id']
    assert (await api.post(f'/api/cost-entries/{current}/confirm')).status_code == 200
    assert (await api.get(f"/api/cost-entries/{w['ce_garten']}")).json()['superseded_by_id'] == current
    assert (await api.get(f'/api/payments/{original}')).json() == before
    assert before['status'] == 'booked'
    assert before['reserve_cost_entry_id'] == w['ce_garten']
    draft = await api.post('/api/payments', json={'bank_account_id': w['bank_giro'], 'direction': 'OUT',
        'amount': amount, 'value_date': '2025-02-01', 'payment_type': 'RUECKLAGENENTNAHME',
        **evidence, 'reserve_cost_entry_id': current})
    assert draft.status_code == 201, draft.text
    response = await api.post(f"/api/payments/{draft.json()['id']}/book")
    assert response.status_code == status, response.text
    if status == 422:
        assert response.json()['detail']['code'] == 'RESERVE_PAYOUT_EVIDENCE_REQUIRED'


@pytest.mark.asyncio
async def test_funded_amount_follows_predecessors_and_successors_in_cost_chain(client_a_admin, seed_data, app_rw_session):
    from uuid import UUID
    from app.repositories.reserve_ledger_repo import ReserveLedgerRepository

    api = client_a_admin
    w = await reserve_world(api, seed_data)
    evidence = await reserve_evidence(api, w)
    original = await payment(api, w, '3000.00', kind='RUECKLAGENENTNAHME', direction='OUT', evidence=evidence)
    ids = [w['ce_garten']]
    for gross in ('5000.00', '9000.00'):
        response = await api.post(f'/api/cost-entries/{ids[-1]}/correction', json={'gross_amount': gross})
        assert response.status_code == 201, response.text
        ids.append(response.json()['id'])
        assert (await api.post(f'/api/cost-entries/{ids[-1]}/confirm')).status_code == 200
        if gross == '5000.00':
            response = await api.post(f"/api/weg-settlements/{w['run']}/reserve-ledger/overrides", json={
                'component': 'ADJUSTMENT', 'amount': '-1000.00', 'reason': 'Documented additional funding',
                'cost_entry_id': ids[-1], 'register_entry_id': evidence['reserve_register_entry_id']})
            assert response.status_code == 201, response.text
    await app_rw_session.execute(text("SELECT set_config('app.current_user_id',:actor,true), set_config('app.current_org_id',:org,true)"),
        {'actor': str(seed_data['user_a_admin_id']), 'org': str(seed_data['org_a_id'])})
    repo = ReserveLedgerRepository(app_rw_session, seed_data['org_a_id'])
    for cost in ids:
        assert await repo.funded_amount(UUID(cost)) == Decimal('4000.00')
        assert await repo.funded_amount(UUID(cost), UUID(original)) == Decimal('1000.00')


@pytest.mark.asyncio
@pytest.mark.parametrize('boundary', ['payout', 'ledger'])
async def test_database_guards_keep_cost_chain_cap(client_a_admin, seed_data, migrator_session, boundary):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    evidence = await reserve_evidence(api, w)
    await payment(api, w, '4000.00', kind='RUECKLAGENENTNAHME', direction='OUT', evidence=evidence)
    response = await api.post(f"/api/cost-entries/{w['ce_garten']}/correction", json={'document_number': 'RE-corrected'})
    assert response.status_code == 201, response.text
    current = response.json()['id']
    assert (await api.post(f'/api/cost-entries/{current}/confirm')).status_code == 200
    values = {'org': seed_data['org_a_id'], 'actor': seed_data['user_a_admin_id'],
              'property': w['property'], 'run': w['run'], 'cost': current,
              'register': evidence['reserve_register_entry_id']}
    if boundary == 'payout':
        draft = await api.post('/api/payments', json={'bank_account_id': w['bank_giro'], 'direction': 'OUT',
            'amount': '4000.00', 'value_date': '2025-02-01', 'payment_type': 'RUECKLAGENENTNAHME',
            **evidence, 'reserve_cost_entry_id': current})
        assert draft.status_code == 201, draft.text
        values['payment'] = draft.json()['id']
    await migrator_session.execute(text("SELECT set_config('app.current_user_id',:actor,true), set_config('app.current_org_id',:org,true)"),
        {'actor': str(values['actor']), 'org': str(values['org'])})
    with pytest.raises(DBAPIError) as refusal:
        async with migrator_session.begin_nested():
            if boundary == 'payout':
                await migrator_session.execute(text("""UPDATE payment SET status='booked',booked_at=now(),
                    booked_by=:actor,retention_until='2034-12-31' WHERE id=CAST(:payment AS uuid)"""), values)
            else:
                await migrator_session.execute(text("""INSERT INTO reserve_ledger_entry
                    (organization_id,property_id,settlement_run_id,entry_date,component,amount,reason,cost_entry_id,register_entry_id,created_by)
                    VALUES (:org,:property,:run,'2025-02-01','WITHDRAWAL',4000,'Cost correction chain',:cost,:register,:actor)"""), values)
    assert refusal.value.orig.__cause__.constraint_name == 'reserve_payout_evidence_required'
