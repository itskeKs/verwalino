"""T33: frozen special levies use the existing owner dunning lifecycle."""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from tests.test_special_levy import (
    levy_db as levy_db, levy_fixture as levy_fixture, scope_seed as scope_seed, worker_tenant as worker_tenant, client, create, query,
)

from tests.test_scoped_fristen import add_monitor_plan

from app.engine.dunning import find_overdue_items
from app.engine.mietkonto import account_statement, special_levy_expected_charges


def test_special_levy_dunning_respects_exact_due_date():
    owner_id = uuid.uuid4()
    charges = special_levy_expected_charges(
        [(date(2026, 6, 15), Decimal('3000.00'))], owner_id,
        date(2026, 6, 1), date(2026, 6, 30))
    statement = account_statement(charges, [])
    assert not find_overdue_items(list(statement.open_items), date(2026, 6, 14), current_year=2026)
    assert not find_overdue_items(list(statement.open_items), date(2026, 6, 15), current_year=2026)
    overdue = find_overdue_items(list(statement.open_items), date(2026, 6, 16), current_year=2026)
    assert sum(item.open_amount for item in overdue) == Decimal('3000.00')


@pytest.mark.asyncio
async def test_0128_migration_roundtrip_before_levy_notices(migrator_session):
    import importlib
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine
    from tests.conftest import MIGRATOR_SYNC_URL
    await migrator_session.commit()
    migration = importlib.import_module('migrations.versions.0128_special_levy_dunning')
    successor_migration = importlib.import_module('migrations.versions.0163_dated_successor_dunning')
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
            before = connection.scalar(text("SELECT pg_get_functiondef('validate_dunning_context()'::regprocedure)"))
            contexts_sql = text('SELECT to_jsonb(c) FROM dunning_scope_context c ORDER BY organization_id,dunning_notice_id')
            before_contexts = connection.execute(contexts_sql).scalars().all()
            point = connection.begin_nested()
            try:
                successors = connection.execute(text("""SELECT c.dunning_notice_id
                    FROM dunning_scope_context c JOIN dunning_notice n
                      ON n.id=c.dunning_notice_id AND n.organization_id=c.organization_id
                    WHERE c.economic_plan_id IS NOT NULL AND n.owner_profile_id IS NOT NULL
                      AND NOT EXISTS(SELECT 1 FROM economic_plan_line ln
                        WHERE ln.organization_id=c.organization_id AND ln.economic_plan_id=c.economic_plan_id
                          AND ln.owner_profile_id=n.owner_profile_id AND ln.line_type='OWNER_ADVANCE')""")).scalars().all()
                if successors:
                    with pytest.raises(RuntimeError, match='Cannot remove dated successor dunning provenance'):
                        successor_migration.downgrade()
                    # Earlier scenarios retain real successor notices. Only
                    # this rollback-only historical projection excludes them.
                    connection.execute(text('ALTER TABLE dunning_scope_context DISABLE TRIGGER USER'))
                    connection.execute(text('DELETE FROM dunning_scope_context WHERE dunning_notice_id=ANY(:ids)'),
                                       {'ids': successors})
                    connection.execute(text('ALTER TABLE dunning_scope_context ENABLE TRIGGER USER'))
                successor_migration.downgrade()
                historical = connection.scalar(text("SELECT pg_get_functiondef('validate_dunning_context()'::regprocedure)"))
                migration.downgrade()
                assert not connection.scalar(text("SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_name='dunning_scope_context' AND column_name='special_levy_ids')"))
                migration.upgrade()
                assert connection.scalar(text("SELECT pg_get_functiondef('validate_dunning_context()'::regprocedure)")) == historical
                successor_migration.upgrade()
                assert connection.scalar(text("SELECT pg_get_functiondef('validate_dunning_context()'::regprocedure)")) == before
            finally:
                point.rollback()
            assert connection.execute(contexts_sql).scalars().all() == before_contexts
            assert connection.scalar(text("SELECT pg_get_functiondef('validate_dunning_context()'::regprocedure)")) == before
    finally:
        engine.dispose()


def dunning_body(ids, **extra):
    return {**query(ids), 'owner_profile_id': str(ids['owners'][0]),
            'period_start': '2026-06-01', 'period_end': '2026-06-30',
            'reference_date': '2026-06-16', **extra}


async def monitor_open_items(api, ids, reference_date):
    response = await api.get('/api/fristen', params={**query(ids), 'reference_date': reference_date})
    assert response.status_code == 200, response.text
    return next(row for row in response.json() if row['kind'] == 'OFFENE_POSTEN')


@pytest.mark.asyncio
async def test_levy_without_plan_real_dunning_payment_issue_pdf(levy_db, levy_fixture):
    ids = levy_fixture
    # APPROVE/EXPORT are separate from payment confirmation, even for a levy.
    await levy_db.execute(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES (:org,:scope,:actor,ARRAY['read','prepare','approve','confirm','export'],:actor,'Dunning lifecycle test')"), ids)
    await levy_db.commit()
    async with client(ids, 'actor') as api:
        await create(api, ids, due_date='2026-06-15')
        statement = await api.get(f"/api/payments/mietkonto/owner/{ids['owners'][0]}", params={**query(ids), 'period_start': '2026-06-01', 'period_end': '2026-06-30'})
        assert statement.status_code == 200, statement.text
        assert statement.json()['balance'] == '3000.00'
        async with client(ids, 'readonly') as reader:
            due_today = await monitor_open_items(reader, ids, '2026-06-15')
            assert due_today['status'] == 'OK'
            overdue = await monitor_open_items(reader, ids, '2026-06-16')
            assert overdue['status'] == 'DUE'
            assert overdue['detail'].startswith('3 '), overdue
        for day in ('2026-06-14', '2026-06-15'):
            before = await api.post('/api/dunning/owner', json=dunning_body(ids, reference_date=day))
            assert before.status_code == 422, before.text
            assert before.json()['detail'] == (
                'Für den ausgewählten Eigentümer gibt es im gewählten Zeitraum '
                'zum Stichtag keine überfälligen Beträge.'
            ), before.text
        result = await api.post('/api/dunning/owner', json=dunning_body(ids))
        assert result.status_code == 201, result.text
        draft = result.json()
        assert draft['principal_amount'] == '3000.00'
        assert draft['included_items'][0]['payment_type'] == 'SONDERZAHLUNG'
        assert (await api.get(f"/api/dunning/{draft['id']}/pdf")).status_code == 409
        payment = await api.post('/api/payments', json={'bank_account_id': str(ids['bank']), 'amount': '1000.00', 'direction': 'IN', 'value_date': '2026-06-15', 'payment_type': 'SONDERZAHLUNG', 'owner_profile_id': str(ids['owners'][0]), 'period_year': 2026, 'period_month': 6})
        assert payment.status_code == 201, payment.text
        assert (await api.post(f"/api/payments/{payment.json()['id']}/book")).status_code == 200
        reduced = await api.post('/api/dunning/owner', json=dunning_body(ids))
        assert reduced.status_code == 201, reduced.text
        assert reduced.json()['principal_amount'] == '2000.00'
        issued = await api.post(f"/api/dunning/{draft['id']}/issue")
        assert issued.status_code == 200, issued.text
        assert issued.json()['principal_amount'] == '3000.00'
        pdf = await api.get(f"/api/dunning/{draft['id']}/pdf")
        assert pdf.status_code == 200, pdf.text
        assert pdf.content.startswith(b'%PDF-')
        assert (await api.post(f"/api/dunning/{draft['id']}/issue")).status_code == 409
        assert (await api.delete(f"/api/dunning/{draft['id']}")).status_code == 409
    assert await levy_db.scalar(text("SELECT count(*) FROM audit_event WHERE entity_id=:id AND action IN ('dunning_notice.created','dunning_notice.issued')"), {'id': uuid.UUID(draft['id'])}) == 2


def test_multiple_levy_dates_apply_monthly_payment_once_and_keep_types_separate():
    from app.engine.mietkonto import ExpectedCharge, PaymentInput
    owner = uuid.uuid4()
    charges = special_levy_expected_charges([
        (date(2026, 6, 15), Decimal('200.00')),
        (date(2026, 6, 15), Decimal('100.00')),
        (date(2026, 6, 25), Decimal('700.00'))], owner,
        date(2026, 6, 1), date(2026, 6, 30))
    charges.append(ExpectedCharge(2026, 6, 'HAUSGELD_VORSCHUSS', Decimal('90.00'), owner_profile_id=owner))
    payment = PaymentInput(payment_id=uuid.uuid4(), payment_type='SONDERZAHLUNG', direction='IN',
        amount=Decimal('100.00'), period_year=2026, period_month=6, owner_profile_id=owner,
        is_booked=True)
    statement = account_statement(charges, [payment])
    assert statement.soll_total == Decimal('1090.00')
    assert statement.ist_total == Decimal('100.00')
    assert statement.balance == Decimal('990.00')
    overdue = find_overdue_items(list(statement.open_items), date(2026, 6, 16), current_year=2026)
    assert {(item.payment_type, item.open_amount) for item in overdue} == {
        ('SONDERZAHLUNG', Decimal('200.00')), ('HAUSGELD_VORSCHUSS', Decimal('90.00'))}
    assert not find_overdue_items([item for item in statement.open_items if item.payment_type == 'SONDERZAHLUNG'], date(2026, 6, 17), grace_days=2, current_year=2026)
    assert sum(item.open_amount for item in find_overdue_items(list(statement.open_items), date(2026, 6, 26), current_year=2026)) == Decimal('990.00')


async def book_payment(api, ids, amount, *, payment_type='SONDERZAHLUNG', owner=None, **extra):
    response = await api.post('/api/payments', json={'bank_account_id': str(ids['bank']),
        'amount': amount, 'direction': 'IN', 'value_date': '2026-06-15', 'payment_type': payment_type,
        'owner_profile_id': str(owner or ids['owners'][0]), 'period_year': 2026, 'period_month': 6, **extra})
    assert response.status_code == 201, response.text
    payment = response.json()
    booked = await api.post(f"/api/payments/{payment['id']}/book")
    assert booked.status_code == 200, booked.text
    return payment


@pytest.mark.asyncio
@pytest.mark.parametrize('payment_type', ['SONDERZAHLUNG', 'HAUSGELD_VORSCHUSS'])
async def test_monitor_refuses_adjacent_plans_using_the_same_monthly_payment(levy_fixture, payment_type):
    ids = {**levy_fixture, 'profile': levy_fixture['owners'][0], 'unit': levy_fixture['units'][0]}
    amount = '600.00' if payment_type == 'HAUSGELD_VORSCHUSS' else '0.00'
    windows = [('2025-07-01', '2025-07-15'), ('2025-07-16', '2025-07-31')]
    plans = [await add_monitor_plan(ids, 'scope', day=day, amount=amount, start=start, end=end)
             for (start, end), day in zip(windows, (3, 17), strict=True)]
    async with client(ids) as api:
        if payment_type == 'SONDERZAHLUNG':
            for due in ('2025-07-03', '2025-07-17'):
                await create(api, ids, due_date=due, amount='2000.00')
            # Pay both levies for the other owners so their balances cannot mask the error.
            for owner, total in zip(ids['owners'][1:], ('1200.00', '1600.00'), strict=True):
                await book_payment(api, ids, total, owner=owner,
                    value_date='2025-07-03', period_year=2025, period_month=7)
        await book_payment(api, ids, '600.00', payment_type=payment_type,
            value_date='2025-07-03', period_year=2025, period_month=7)
        url = f"/api/payments/mietkonto/owner/{ids['profile']}"
        for (start, end), plan in zip(windows, plans, strict=True):
            statement = await api.get(url, params={**query(ids), 'economic_plan_id': str(plan),
                'period_start': start, 'period_end': end, 'as_of': '2025-07-30'})
            assert statement.status_code == 200, statement.text
            assert statement.json()['balance'] == '0.00'
        if payment_type == 'SONDERZAHLUNG':
            whole_month = await api.get(url, params={**query(ids),
                'period_start': '2025-07-01', 'period_end': '2025-07-31', 'as_of': '2025-07-30'})
            assert whole_month.status_code == 200, whole_month.text
            assert whole_month.json()['due_balance'] == '600.00'
        monitor = await api.get('/api/fristen', params={**query(ids), 'reference_date': '2025-07-30'})
        assert monitor.status_code == 409, monitor.text
        assert monitor.json()['detail']['code'] == 'hausgeld_plan_ambiguous'


@pytest.mark.asyncio
async def test_monitor_counts_frozen_levies_across_a_plan_boundary_once(levy_fixture):
    ids = {**levy_fixture, 'profile': levy_fixture['owners'][0], 'unit': levy_fixture['units'][0]}
    await add_monitor_plan(ids, 'scope', amount='0.00', start='2025-07-16')
    await add_monitor_plan(ids, 'scope', amount='0.00', start='2025-08-01', end='2025-08-31')
    async with client(ids) as api:
        for due in ('2025-07-03', '2025-07-17'):
            await create(api, ids, due_date=due, amount='2000.00')
        for owner, amount in zip(ids['owners'], ('600.00', '1200.00', '1600.00'), strict=True):
            await book_payment(api, ids, amount, owner=owner,
                value_date='2025-07-03', period_year=2025, period_month=7)
    async with client(ids, 'readonly') as reader:
        due_today = await monitor_open_items(reader, ids, '2025-07-17')
        assert due_today['status'] == 'OK'
        overdue = await monitor_open_items(reader, ids, '2025-08-15')
        assert overdue['status'] == 'DUE'
        assert overdue['detail'].startswith('1 '), overdue


@pytest.mark.asyncio
async def test_automatic_owner_interest_starts_after_the_resolved_hausgeld_due_date(levy_db, levy_fixture):
    ids = {**levy_fixture, 'profile': levy_fixture['owners'][0], 'unit': levy_fixture['units'][0]}
    plan = await add_monitor_plan(ids, 'scope', start='2026-07-01', end='2026-07-31')
    await levy_db.execute(text("""INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason)
        VALUES (:org,:scope,:actor,ARRAY['read','prepare'],:actor,'Owner interest fixture')"""), ids)
    await levy_db.commit()
    async with client(ids, 'actor') as api:
        settings_path = '/api/dunning/settings'
        previous = await api.get(settings_path)
        assert previous.status_code == 200, previous.text
        enabled = await api.put(settings_path, json={**previous.json(), 'default_interest_enabled': True})
        assert enabled.status_code == 200, enabled.text
        try:
            body = dunning_body(ids, economic_plan_id=str(plan), period_start='2026-07-01', period_end='2026-07-31')
            early = await api.post('/api/dunning/owner', json={**body, 'reference_date': '2026-07-02'})
            assert early.status_code == 422, early.text
            assert early.json()['detail'] == (
                'Für den ausgewählten Eigentümer gibt es im gewählten Zeitraum '
                'zum Stichtag keine überfälligen Beträge.'
            ), early.text
            late = await api.post('/api/dunning/owner', json={**body, 'reference_date': '2026-07-04'})
            assert late.status_code == 201, late.text
            notice = late.json()
            assert notice['principal_amount'] == '600.00'
            assert notice['interest_amount'] == '0.11'
            assert notice['included_items'][0]['due_date'] == '2026-07-03'
            item = notice['interest_snapshot']['items'][0]
            assert (item['due_date'], item['interest_start'], item['days']) == ('2026-07-03', '2026-07-04', 1)
        finally:
            restored = await api.put(settings_path, json=previous.json())
            assert restored.status_code == 200, restored.text


@pytest.mark.asyncio
async def test_multiple_levies_and_actual_payment_matching(levy_db, levy_fixture):
    ids = levy_fixture
    async with client(ids) as api:
        await create(api, ids, due_date='2026-06-15')
        await create(api, ids, due_date='2026-06-25', amount='2000.00')
        await book_payment(api, ids, '1000.00')
        await book_payment(api, ids, '8000.00', owner=ids['owners'][1])
        await book_payment(api, ids, '9000.00', payment_type='HAUSGELD_VORSCHUSS')
        await book_payment(api, ids, '7000.00', payment_type='RUECKLAGENANTEIL')
        await book_payment(api, ids, '6000.00', period_month=5)
        draft_payment = await api.post('/api/payments', json={'bank_account_id': str(ids['bank']), 'amount': '5000.00', 'direction': 'IN', 'value_date': '2026-06-15', 'payment_type': 'SONDERZAHLUNG', 'owner_profile_id': str(ids['owners'][0]), 'period_year': 2026, 'period_month': 6})
        assert draft_payment.status_code == 201, draft_payment.text
        early = await api.post('/api/dunning/owner', json=dunning_body(ids))
        assert early.status_code == 201, early.text
        assert early.json()['principal_amount'] == '2000.00'
        late = await api.post('/api/dunning/owner', json=dunning_body(ids, reference_date='2026-06-26'))
        assert late.status_code == 201, late.text
        assert late.json()['principal_amount'] == '2600.00'
        assert {item['due_date'] for item in late.json()['included_items']} == {'2026-06-15', '2026-06-25'}


@pytest.mark.asyncio
async def test_plan_plus_levy_keeps_hausgeld_semantics(levy_db, levy_fixture):
    ids = {**levy_fixture, 'plan': uuid.uuid4(), 'profile': levy_fixture['owners'][0],
           'unit': levy_fixture['units'][0]}
    await levy_db.execute(text("INSERT INTO economic_plan(id,organization_id,property_id,plan_year,period_start,period_end,status,engine_version) VALUES (:plan,:org,:property,2026,'2026-01-01','2026-12-31','draft','fixture')"), ids)
    await levy_db.execute(text("INSERT INTO economic_plan_line(organization_id,economic_plan_id,line_type,unit_id,owner_profile_id,label,planned_amount,monthly_amount,sort_order) VALUES (:org,:plan,'OWNER_ADVANCE',:unit,:profile,'Plan source',1200,100,1)"), ids)
    await levy_db.execute(text("INSERT INTO accounting_scope_binding(organization_id,property_id,scope_id,economic_plan_id,origin,reason,reviewed_by) VALUES (:org,:property,:scope,:plan,'REVIEWED','Plan fixture',:actor)"), ids)
    await levy_db.commit()
    async with client(ids) as api:
        await create(api, ids, due_date='2026-06-15')
        invalid = await api.post('/api/dunning/owner', json=dunning_body(ids, economic_plan_id=str(ids['plan'])))
        assert invalid.status_code == 422, invalid.text
    await levy_db.execute(text("UPDATE economic_plan SET status='finalized',finalized_at=now(),finalized_by=:actor,snapshot_payload='{}',snapshot_sha256=:sha WHERE id=:plan"), {**ids, 'sha': 'd'*64})
    await levy_db.commit()
    async with client(ids) as api:
        await book_payment(api, ids, '40.00', payment_type='HAUSGELD_VORSCHUSS')
        await book_payment(api, ids, '1000.00')
        mixed = await api.post('/api/dunning/owner', json=dunning_body(ids, economic_plan_id=str(ids['plan'])))
        assert mixed.status_code == 201, mixed.text
        assert mixed.json()['principal_amount'] == '2060.00'
        assert {item['payment_type']: item['open_amount'] for item in mixed.json()['included_items']} == {'HAUSGELD_VORSCHUSS': '60.00', 'SONDERZAHLUNG': '2000.00'}
        no_plan = await api.post('/api/dunning/owner', json=dunning_body(ids))
        assert no_plan.status_code == 201, no_plan.text
        assert no_plan.json()['principal_amount'] == '2000.00'


@pytest.mark.asyncio
async def test_frozen_coowner_allocations_not_current_ownership(levy_db, levy_fixture):
    ids = levy_fixture
    await levy_db.execute(text("UPDATE ownership_history SET valid_to='2026-06-15' WHERE id=:history"), {'history': ids['histories'][0]})
    for owner, share in [(ids['owners'][1], 1), (ids['owners'][2], 3)]:
        await levy_db.execute(text("INSERT INTO ownership_history(organization_id,unit_id,owner_profile_id,valid_from,mea_share) VALUES (:org,:unit,:owner,'2026-06-15',:share)"), {**ids, 'unit': ids['units'][0], 'owner': owner, 'share': share})
    await levy_db.commit()
    async with client(ids) as api:
        await create(api, ids, due_date='2026-06-15')
    await levy_db.execute(text("UPDATE ownership_history SET valid_to='2026-06-16' WHERE organization_id=:org AND unit_id=:unit AND valid_to IS NULL"), {**ids, 'unit': ids['units'][0]})
    await levy_db.execute(text('UPDATE unit SET mea=999 WHERE id=:unit'), {'unit': ids['units'][0]})
    await levy_db.commit()
    async with client(ids) as api:
        no_share = await api.post('/api/dunning/owner', json=dunning_body(ids))
        assert no_share.status_code == 422, no_share.text
        for owner, expected in [(ids['owners'][1], '3750.00'), (ids['owners'][2], '6250.00')]:
            result = await api.post('/api/dunning/owner', json=dunning_body(ids, owner_profile_id=str(owner)))
            assert result.status_code == 201, result.text
            assert result.json()['principal_amount'] == expected


@pytest.mark.asyncio
async def test_live_scope_roles_org_and_revoke(levy_db, levy_fixture, client_b_admin):
    ids = levy_fixture
    async with client(ids) as api:
        await create(api, ids, due_date='2026-06-15')
        draft = await api.post('/api/dunning/owner', json=dunning_body(ids))
        assert draft.status_code == 201, draft.text
        notice = draft.json()['id']
        assert (await api.post(f'/api/dunning/{notice}/issue')).status_code == 404
        wrong_scope = await api.post('/api/dunning/owner', json=dunning_body(ids, accounting_scope_id=str(ids['other_scope'])))
        assert wrong_scope.status_code in (403, 404)
        unknown = await api.post('/api/dunning/owner', json=dunning_body(ids, owner_profile_id=str(uuid.uuid4())))
        assert unknown.status_code == 404
    async with client(ids, 'readonly') as api:
        assert (await api.get(f'/api/dunning/{notice}')).status_code == 200
        assert (await api.post('/api/dunning/owner', json=dunning_body(ids))).status_code == 403
    assert (await client_b_admin.get(f'/api/dunning/{notice}')).status_code == 404
    assert (await client_b_admin.post('/api/dunning/owner', json=dunning_body(ids))).status_code == 404
    await levy_db.execute(text('UPDATE scope_grant SET revoked_at=now(),revoked_by=:actor WHERE id=:grant'), ids)
    await levy_db.commit()
    async with client(ids) as api:
        assert (await api.get(f'/api/dunning/{notice}')).status_code == 404
        assert (await api.post(f'/api/dunning/{notice}/issue')).status_code == 404
        assert (await api.post('/api/dunning/owner', json=dunning_body(ids))).status_code == 404


@pytest.mark.asyncio
async def test_levy_dunning_source_revocation_fails_closed(levy_db, levy_fixture):
    from tests.test_property_source_access import grant
    from tests.contact_source_fixtures import grant_property_contacts
    from tests.test_property_source_enforcement import synthetic_enforcement
    from tests.test_financial_scope_boundary import actor
    ids = levy_fixture
    seed = {'org_a_id': ids['org'], 'user_a_admin_id': ids['actor']}
    await levy_db.execute(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES (:org,:scope,:actor,ARRAY['read','prepare','approve','export'],:actor,'Levy source test')"), ids)
    await levy_db.commit()
    async with client(ids, 'actor') as api:
        await create(api, ids, due_date='2026-06-15')
        await grant_property_contacts(api, seed, ids['property'])
        domain, receipt = await grant(api, seed, ids['property'], caps=['read', 'prepare', 'export'])
        async with synthetic_enforcement(seed):
            response = await api.post('/api/dunning/owner', json=dunning_body(ids))
            assert response.status_code == 201, response.text
            notice = response.json()['id']
            assert (await api.post(f'/api/dunning/{notice}/issue')).status_code == 200
            assert (await api.get(f'/api/dunning/{notice}/pdf')).status_code == 200
            assert (await api.post(f'/api/property-data-domains/{domain}/grants/{receipt}/revoke')).status_code == 204
            assert (await api.get(f'/api/dunning/{notice}')).status_code == 404
            assert (await api.get(f'/api/dunning/{notice}/pdf')).status_code == 404
            assert (await api.post('/api/dunning/owner', json=dunning_body(ids))).status_code == 404
            await actor(levy_db, ids, 'actor')
            for table, column in [('dunning_notice', 'id'), ('audit_event', 'entity_id')]:
                assert await levy_db.scalar(text(f'SELECT count(*) FROM {table} WHERE {column}=:id'), {'id': uuid.UUID(notice)}) == 0
            await levy_db.execute(text('RESET ROLE'))
            await levy_db.rollback()


@pytest.mark.asyncio
async def test_dunning_failure_rolls_back_notice_context_and_audit(levy_db, levy_fixture, monkeypatch):
    from app.repositories.audit_repo import AuditRepository
    ids = levy_fixture
    async with client(ids) as api:
        await create(api, ids, due_date='2026-06-15')
    async def fail(self, event):
        raise RuntimeError('injected dunning audit failure')
    monkeypatch.setattr(AuditRepository, 'create', fail)
    async with client(ids, raise_errors=False) as api:
        result = await api.post('/api/dunning/owner', json=dunning_body(ids))
        assert result.status_code == 500, result.text
    for table in ('dunning_notice', 'dunning_scope_context'):
        assert await levy_db.scalar(text(f'SELECT count(*) FROM {table} WHERE organization_id=:org'), ids) == 0
    assert await levy_db.scalar(text("SELECT count(*) FROM audit_event WHERE organization_id=:org AND entity_type='dunning_notice'"), ids) == 0


@pytest.mark.asyncio
async def test_levy_context_and_issued_snapshot_immutable_and_downgrade_guard(levy_db, levy_fixture):
    import importlib
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    from tests.conftest import MIGRATOR_SYNC_URL
    ids = levy_fixture
    await levy_db.execute(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES (:org,:scope,:actor,ARRAY['read','prepare','approve'],:actor,'Immutable notice test')"), ids)
    await levy_db.commit()
    async with client(ids, 'actor') as api:
        levy = await create(api, ids, due_date='2026-06-15')
        response = await api.post('/api/dunning/owner', json=dunning_body(ids))
        assert response.status_code == 201, response.text
        notice = response.json()['id']
        assert (await api.post(f'/api/dunning/{notice}/issue')).status_code == 200
    params = {**ids, 'notice': uuid.UUID(notice)}
    sources = await levy_db.scalar(text('SELECT special_levy_ids FROM dunning_scope_context WHERE dunning_notice_id=:notice'), params)
    assert sources == [uuid.UUID(levy['id'])]
    for sql in ["UPDATE dunning_scope_context SET special_levy_ids='{}' WHERE dunning_notice_id=:notice",
                'DELETE FROM dunning_scope_context WHERE dunning_notice_id=:notice',
                'UPDATE dunning_notice SET principal_amount=1 WHERE id=:notice',
                "UPDATE dunning_notice SET included_items='[]' WHERE id=:notice"]:
        with pytest.raises(DBAPIError):
            async with levy_db.begin_nested():
                await levy_db.execute(text(sql), params)
    await levy_db.rollback()
    migration = importlib.import_module('migrations.versions.0128_special_levy_dunning')
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(DBAPIError, match='Cannot downgrade while levy-backed'):
                with connection.begin_nested():
                    migration.downgrade()
    finally:
        engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('bad_source', ['owner', 'scope', 'period', 'levy'])
async def test_database_rejects_unmatched_frozen_levy_context(levy_db, levy_fixture, bad_source):
    from sqlalchemy.exc import DBAPIError
    ids = levy_fixture
    async with client(ids) as api:
        levy = await create(api, ids, due_date='2026-06-15')
    params = {**ids, 'notice': uuid.uuid4(), 'levy': uuid.UUID(levy['id']),
              'owner': ids['owners'][0], 'start': date(2026, 6, 1), 'end': date(2026, 6, 30)}
    if bad_source == 'owner':
        params['owner'] = uuid.uuid4()
        # Existing owner, but not an allocation recipient.
        person = uuid.uuid4()
        await levy_db.execute(text("INSERT INTO person(id,organization_id,first_name,last_name) VALUES (:person,:org,'Other','Owner')"), {**ids, 'person': person})
        await levy_db.execute(text('INSERT INTO owner_profile(id,organization_id,person_id) VALUES (:owner,:org,:person)'), {**params, 'person': person})
        await levy_db.commit()
    elif bad_source == 'scope':
        params['scope'] = ids['other_scope']
    elif bad_source == 'period':
        params['end'] = date(2026, 6, 14)
    else:
        params['levy'] = uuid.uuid4()
    with pytest.raises(DBAPIError, match='Dunning.*(WEG|levy)'):
        async with levy_db.begin_nested():
            await levy_db.execute(text("INSERT INTO dunning_scope_context(organization_id,dunning_notice_id,scope_id,special_levy_ids,period_start,period_end,created_by,reason) VALUES (:org,:notice,:scope,ARRAY[CAST(:levy AS uuid)],:start,:end,:actor,'Deliberately mismatched source')"), params)
            await levy_db.execute(text("INSERT INTO dunning_notice(id,organization_id,owner_profile_id,level,issue_date,due_date,principal_amount,fee_amount,total_amount,included_items) VALUES (:notice,:org,:owner,1,'2026-06-16','2026-06-30',3000,0,3000,'[]')"), params)
            await levy_db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))


@pytest.mark.asyncio
async def test_same_owner_other_property_payment_and_levy_do_not_cross_scope(levy_db, levy_fixture):
    from tests.test_scope_grants import scope_fixture, bind
    ids = levy_fixture
    other = await scope_fixture(levy_db, {'org_a_id': ids['org'], 'user_a_admin_id': ids['actor'], 'user_a_clerk_id': ids['clerk']})
    await bind(levy_db, other)
    await levy_db.execute(text('UPDATE scope_grant SET revoked_at=now(),revoked_by=:actor WHERE id=:grant'), other)
    await levy_db.execute(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES (:org,:scope,:clerk,ARRAY['read','prepare','confirm'],:actor,'Second property payment test')"), other)
    other.update(owners=ids['owners'], unit=uuid.uuid4(), meeting=uuid.uuid4(), resolution=uuid.uuid4())
    await levy_db.execute(text("INSERT INTO owners_meeting(id,organization_id,property_id,meeting_date,location,meeting_type,status) VALUES (:meeting,:org,:property,'2026-05-15','','ORDENTLICH','HELD')"), other)
    await levy_db.execute(text("INSERT INTO resolution(id,organization_id,meeting_id,wording,votes_yes,votes_no,votes_abstain,result) VALUES (:resolution,:org,:meeting,'Second property levy',1,0,0,'ANGENOMMEN')"), other)
    await levy_db.execute(text("INSERT INTO unit(id,organization_id,property_id,number,usage_type,mea) VALUES (:unit,:org,:property,'Elsewhere','WOHNUNG',1000)"), other)
    await levy_db.execute(text("INSERT INTO ownership_history(organization_id,unit_id,owner_profile_id,valid_from) VALUES (:org,:unit,:owner,'2024-01-01')"), {**other, 'owner': ids['owners'][0]})
    await levy_db.commit()
    async with client(ids) as api:
        await create(api, ids, due_date='2026-06-15')
        await create(api, other, due_date='2026-06-15', amount='7000.00')
        await book_payment(api, other, '5000.00')
        first = await api.post('/api/dunning/owner', json=dunning_body(ids))
        assert first.status_code == 201, first.text
        assert first.json()['principal_amount'] == '3000.00'
        second = await api.post('/api/dunning/owner', json=dunning_body(other))
        assert second.status_code == 201, second.text
        assert second.json()['principal_amount'] == '2000.00'


@pytest.mark.asyncio
async def test_t35_levy_interest_actual_dates_payment_and_configuration_frozen(levy_db, levy_fixture, client_b_admin):
    import io
    import pdfplumber
    ids = levy_fixture
    await levy_db.execute(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES (:org,:scope,:actor,ARRAY['read','prepare','approve','confirm','export'],:actor,'Interest lifecycle test')"), ids)
    await levy_db.commit()
    async with client(ids, 'actor') as api:
        settings_path = "/api/dunning/settings"
        previous = (await api.get(settings_path)).json()
        assert (await api.put(settings_path, json={**previous, "default_interest_enabled": True})).status_code == 200
        await create(api, ids, due_date='2026-06-15')
        await create(api, ids, due_date='2026-06-25', amount='2000.00')
        response = await api.post('/api/dunning/owner', json=dunning_body(ids, reference_date='2026-06-26', override_fee='0.00'))
        assert response.status_code == 201, response.text
        draft = response.json()
        assert draft['principal_amount'] == '3600.00'
        assert draft['interest_amount'] == '5.77'  # 3000 * 6.27% * 11/365 + 600 * 6.27% * 1/365, per-item cents
        assert draft['total_amount'] == '3605.77'
        assert [(i['due_date'], i['days']) for i in draft['interest_snapshot']['items']] == [('2026-06-15', 11), ('2026-06-25', 1)]
        notice = draft['id']
        assert (await api.post(f'/api/dunning/{notice}/issue')).status_code == 200
        pdf_before = await api.get(f'/api/dunning/{notice}/pdf')
        assert pdf_before.status_code == 200
        with pdfplumber.open(io.BytesIO(pdf_before.content)) as pdf:
            before_text = '\n'.join(page.extract_text() for page in pdf.pages)
        assert '5,77' in before_text and '3.605,77' in before_text
        await book_payment(api, ids, '1000.00', value_date='2026-06-20')
        recalculated = await api.post('/api/dunning/owner', json=dunning_body(ids, reference_date='2026-06-26', override_fee='0.00'))
        assert recalculated.status_code == 201, recalculated.text
        assert recalculated.json()['principal_amount'] == '2600.00'
        assert recalculated.json()['interest_amount'] == '4.57'
        assert [(row['days'], row['principal']) for row in
                recalculated.json()['interest_snapshot']['items'][0]['segments']] == [(4, '3000.00'), (7, '2000.00')]
        assert (await api.put(settings_path, json=previous)).status_code == 200
        retained = (await api.get(f'/api/dunning/{notice}')).json()
        for key in ('principal_amount', 'interest_amount', 'interest_snapshot', 'total_amount', 'included_items'):
            assert retained[key] == draft[key]
        pdf_after = await api.get(f'/api/dunning/{notice}/pdf')
        with pdfplumber.open(io.BytesIO(pdf_after.content)) as pdf:
            assert '\n'.join(page.extract_text() for page in pdf.pages) == before_text
        fresh = await api.post('/api/dunning/owner', json=dunning_body(ids, reference_date='2026-06-26', override_fee='0.00'))
        assert fresh.status_code == 201, fresh.text
        assert fresh.json()['principal_amount'] == '2600.00'
        assert fresh.json()['interest_snapshot'] is None
    assert (await client_b_admin.get(f'/api/dunning/{notice}')).status_code == 404
    async with client(ids, 'readonly') as api:
        assert (await api.post('/api/dunning/owner', json=dunning_body(ids))).status_code == 403
    await levy_db.execute(text("UPDATE scope_grant SET revoked_at=now(), revoked_by=:actor WHERE organization_id=:org AND scope_id=:scope AND user_id=:actor AND revoked_at IS NULL"), ids)
    await levy_db.commit()
    async with client(ids, 'actor') as api:
        assert (await api.get(f'/api/dunning/{notice}')).status_code == 404
        assert (await api.get(f'/api/dunning/{notice}/pdf')).status_code == 404
