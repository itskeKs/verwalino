"""Empty WEG drafts may disappear; financial evidence must survive."""
import asyncio
import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.test_m4_weg import _seed_weg, _create_and_finalize_plan
from tests.test_reserve_ledger import payment, reserve_world, finalize, paid


async def asset_draft(api, world, year=2020):
    response = await api.post('/api/weg-settlements/asset-reports', json={
        'property_id': world['property'], 'period_start': f'{year}-01-01', 'period_end': f'{year}-12-31'})
    assert response.status_code == 201, response.text
    return response.json()['id']


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['WEG_JAHRESABRECHNUNG', 'VERMOEGENSBERICHT'])
async def test_empty_draft_deletion_removes_the_run_from_its_property(client_a_admin, seed_data, kind):
    api = client_a_admin
    w = _seed_weg(seed_data)
    if kind == 'WEG_JAHRESABRECHNUNG':
        plan = await _create_and_finalize_plan(api, w)
        created = await api.post('/api/weg-settlements', json={'property_id': w['property'],
            'economic_plan_id': plan, 'period_start': '2025-01-01', 'period_end': '2025-12-31'})
        assert created.status_code == 201, created.text
        run = created.json()['id']
    else:
        run = await asset_draft(api, w)
    path = f'/api/weg-settlements/{run}'
    assert (await api.get(path)).json()['delete_restriction'] is None
    response = await api.delete(path)
    assert response.status_code == 204, response.text
    assert response.content == b''
    assert (await api.get(path)).status_code == 404
    listed = await api.get('/api/weg-settlements', params={'property_id': w['property'], 'run_type': kind})
    assert run not in [row['id'] for row in listed.json()]


@pytest.mark.asyncio
async def test_deleting_stray_asset_draft_unblocks_the_first_real_opening_import(client_a_admin, seed_data):
    api = client_a_admin
    w = _seed_weg(seed_data)
    stray = await asset_draft(api, w)
    real = await asset_draft(api, w, 2025)
    path = f'/api/weg-settlements/{real}/reserve-ledger/overrides'
    values = {'component': 'OPENING', 'amount': '12500.00', 'reason': 'Bankbestätigter Anfangsbestand 2025'}
    assert (await api.post(path, json=values)).status_code == 422
    assert (await api.delete(f'/api/weg-settlements/asset-reports/{stray}')).status_code == 204
    imported = await api.post(path, json=values)
    assert imported.status_code == 201, imported.text
    assert (await api.get(f'/api/weg-settlements/{real}/reserve-ledger')).json()['opening_balance'] == '12500.00'


@pytest.mark.asyncio
@pytest.mark.parametrize('source,code,message', [
    ('payment', 'SETTLEMENT_DELETE_HAS_BOOKINGS', 'Entwürfe mit Buchungen können nicht gelöscht werden.'),
    ('ledger', 'SETTLEMENT_DELETE_HAS_LEDGER', 'Entwürfe mit Rücklagenbuch-Einträgen können nicht gelöscht werden.'),
    ('finalized', 'SETTLEMENT_DELETE_NOT_DRAFT', 'Nur nicht ersetzte Entwürfe können gelöscht werden.'),
    ('superseded', 'SETTLEMENT_DELETE_NOT_DRAFT', 'Nur nicht ersetzte Entwürfe können gelöscht werden.'),
    ('coverage', 'SETTLEMENT_DELETE_HAS_EVIDENCE', 'Dieser Entwurf wird von aufbewahrungspflichtigen Nachweisen verwendet und kann nicht gelöscht werden.'),
])
async def test_financial_evidence_and_frozen_runs_refuse_deletion(client_a_admin, seed_data, source, code, message):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    path = f"/api/weg-settlements/{w['run']}"
    if source == 'payment':
        await payment(api, w, '100.00', kind='HAUSGELD_VORSCHUSS')
    elif source == 'ledger':
        assert (await api.post(path + '/reserve-ledger/overrides', json={
            'component': 'OPENING', 'amount': '1.00', 'reason': 'Geprüfter Anfangsbestand'})).status_code == 201
    elif source in ('finalized', 'superseded'):
        await finalize(api, w)
        if source == 'superseded':
            response = await api.post(f"/api/settlements/{w['run']}/correction")
            assert response.status_code == 201, response.text
    else:
        from tests.financial_coverage_helpers import attest_fixture
        await attest_fixture(api, 'settlement', w['run'], paid(w))
    restriction = {'code': code, 'message': message}
    assert (await api.get(path)).json()['delete_restriction'] == restriction
    response = await api.delete(path)
    assert response.status_code == 409, response.text
    assert response.json()['detail'] == restriction
    assert (await api.get(path)).status_code == 200


@pytest.mark.asyncio
async def test_foreign_and_readonly_users_cannot_delete_empty_drafts(client_a_admin, client_b_admin, client_a_readonly, seed_data):
    run = await asset_draft(client_a_admin, _seed_weg(seed_data))
    for path in (f'/api/weg-settlements/{run}', f'/api/weg-settlements/asset-reports/{run}'):
        assert (await client_b_admin.delete(path)).status_code == 404
        assert (await client_a_readonly.delete(path)).status_code == 403
    assert (await client_a_admin.get(f'/api/weg-settlements/{run}')).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize('source', ['ledger', 'payment'])
async def test_direct_database_delete_cannot_discard_a_draft_with_financial_entries(
        client_a_admin, seed_data, app_rw_session, source):
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    if source == 'ledger':
        response = await api.post(f"/api/weg-settlements/{w['run']}/reserve-ledger/overrides", json={
            'component': 'OPENING', 'amount': '12500.00', 'reason': 'Geprüfter Anfangsbestand'})
        assert response.status_code == 201, response.text
    else:
        await payment(api, w, '100.00', kind='HAUSGELD_VORSCHUSS')
    await app_rw_session.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:actor,true)"),
        {'org': str(seed_data['org_a_id']), 'actor': str(seed_data['user_a_admin_id'])})
    with pytest.raises(DBAPIError) as failure:
        async with app_rw_session.begin_nested():
            await app_rw_session.execute(text('DELETE FROM settlement_run WHERE id=:run'), {'run': w['run']})
    assert failure.value.orig.__cause__.constraint_name == 'settlement_delete_has_' + ('ledger' if source == 'ledger' else 'bookings')


@pytest.mark.asyncio
async def test_delete_waits_for_a_booking_and_rechecks_the_committed_evidence(client_a_admin, seed_data):
    """Hold the actual financial lock in another transaction, then commit a booking."""
    import asyncpg
    api = client_a_admin
    w = await reserve_world(api, seed_data)
    draft = await api.post('/api/payments', json={'bank_account_id': w['bank_giro'], 'direction': 'IN',
        'amount': '100.00', 'value_date': '2025-02-01', 'payment_type': 'HAUSGELD_VORSCHUSS',
        'owner_profile_id': w['owner_a']})
    assert draft.status_code == 201, draft.text
    connection = await asyncpg.connect(os.environ['HV_DATABASE_URL_SYNC'].replace('postgresql://', 'postgres://'))
    deletion = None
    try:
        async with connection.transaction():
            await connection.execute("SELECT pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||$1,0)),pg_advisory_xact_lock(hashtextextended('financial-inputs:'||$1,0))", str(seed_data['org_a_id']))
            await connection.execute("UPDATE payment SET status='booked',booked_at=now(),booked_by=$1 WHERE id=$2", seed_data['user_a_admin_id'], uuid.UUID(draft.json()['id']))
            deletion = asyncio.create_task(api.delete(f"/api/weg-settlements/{w['run']}"))
            # Observe PostgreSQL's lock wait, instead of relying on elapsed sleeps.
            for _ in range(300):
                waiting = await connection.fetchval("SELECT EXISTS(SELECT 1 FROM pg_locks WHERE locktype='advisory' AND NOT granted AND pid<>pg_backend_pid())")
                if waiting:
                    break
                await asyncio.sleep(0.01)
            assert waiting and not deletion.done()
        response = await asyncio.wait_for(deletion, timeout=5)
        assert response.status_code == 409, response.text
        assert response.json()['detail']['code'] == 'SETTLEMENT_DELETE_HAS_BOOKINGS'
    finally:
        await connection.close()
        if deletion is not None and not deletion.done():
            deletion.cancel()
            await asyncio.gather(deletion, return_exceptions=True)


@pytest.mark.asyncio
async def test_immutable_scope_binding_refuses_api_and_direct_deletion(client_a_admin, seed_data, migrator_session, app_rw_session):
    api = client_a_admin
    w = _seed_weg(seed_data)
    run = await asset_draft(api, w, 2025)
    ids = {'org': seed_data['org_a_id'], 'actor': seed_data['user_a_admin_id'],
           'property': w['property'], 'run': run, 'scope': uuid.uuid4()}
    await migrator_session.execute(text("""INSERT INTO accounting_scope
        (id,organization_id,property_id,scope_type,label,valid_from,whole_property,origin,created_by)
        VALUES (:scope,:org,:property,'WEG','Reviewed WEG','2025-01-01',true,'REVIEWED',:actor)"""), ids)
    await migrator_session.execute(text("""INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason)
        VALUES (:org,:scope,:actor,ARRAY['read','prepare'],:actor,'Reviewed WEG access')"""), ids)
    await migrator_session.execute(text("""INSERT INTO accounting_scope_binding(organization_id,property_id,scope_id,settlement_run_id,origin,reason,reviewed_by)
        VALUES (:org,:property,:scope,:run,'REVIEWED','Reviewed draft context',:actor)"""), ids)
    await migrator_session.commit()
    response = await api.delete(f'/api/weg-settlements/{run}')
    assert response.status_code == 409, response.text
    assert response.json()['detail']['code'] == 'SETTLEMENT_DELETE_SCOPE_DEPENDENCY'
    await app_rw_session.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:actor,true)"),
        {'org': str(ids['org']), 'actor': str(ids['actor'])})
    with pytest.raises(DBAPIError) as refused:
        async with app_rw_session.begin_nested():
            await app_rw_session.execute(text('DELETE FROM settlement_run WHERE id=:run'), ids)
    assert refused.value.orig.__cause__.constraint_name == 'settlement_delete_scope_dependency'


@pytest.mark.asyncio
async def test_dependency_helper_does_not_disclose_foreign_runs(client_a_admin, seed_data, app_rw_session):
    run = await asset_draft(client_a_admin, _seed_weg(seed_data))
    await app_rw_session.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:actor,true)"),
        {'org': str(seed_data['org_b_id']), 'actor': str(seed_data['user_b_admin_id'])})
    assert await app_rw_session.scalar(text('SELECT weg_draft_delete_reason(:org,:run)'),
        {'org': seed_data['org_a_id'], 'run': run}) == 'NOT_FOUND'
    assert await app_rw_session.scalar(text('SELECT weg_draft_delete_reason(:org,:run)'),
        {'org': seed_data['org_b_id'], 'run': run}) == 'NOT_FOUND'
    assert not await app_rw_session.scalar(text("SELECT has_function_privilege(current_user,'weg_draft_delete_reason(settlement_run)','EXECUTE')"))


@pytest.mark.asyncio
@pytest.mark.parametrize('source', ['payment', 'ledger'])
async def test_raw_delete_waits_before_row_locks_and_keeps_concurrent_financial_evidence(client_a_admin, seed_data, source):
    import asyncpg
    from datetime import date
    from decimal import Decimal
    w = _seed_weg(seed_data)
    run = await asset_draft(client_a_admin, w, 2025)
    writer = await asyncpg.connect(os.environ['HV_DATABASE_URL_SYNC'])
    deleter = await asyncpg.connect(os.environ['HV_DATABASE_URL'].replace('postgresql+asyncpg://', 'postgresql://'))
    deletion = None
    try:
        await deleter.execute("SELECT set_config('app.current_org_id',$1,false),set_config('app.current_user_id',$2,false)",
                             str(seed_data['org_a_id']), str(seed_data['user_a_admin_id']))
        async with writer.transaction():
            await writer.execute("SELECT set_config('app.current_org_id',$1,true),set_config('app.current_user_id',$2,true)",
                                 str(seed_data['org_a_id']), str(seed_data['user_a_admin_id']))
            await writer.execute("SELECT pg_advisory_xact_lock_shared(hashtextextended('scope-acl:'||$1,0)),pg_advisory_xact_lock(hashtextextended('financial-inputs:'||$1,0))", str(seed_data['org_a_id']))
            deletion = asyncio.create_task(deleter.execute('DELETE FROM settlement_run WHERE id=$1', uuid.UUID(run)))
            for _ in range(300):
                waiting = await writer.fetchval("SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=$1 AND locktype='advisory' AND NOT granted)", deleter.get_server_pid())
                if waiting:
                    break
                await asyncio.sleep(0.01)
            assert waiting and not deletion.done()
            # This FK takes a parent key-share lock. It would deadlock if the
            # waiting DELETE had already locked that parent tuple.
            if source == 'ledger':
                await writer.execute("""INSERT INTO reserve_ledger_entry
                    (organization_id,property_id,settlement_run_id,entry_date,component,amount,reason,created_by)
                    VALUES ($1,$2,$3,$4,'OPENING',$5,'Geprüfter Anfangsbestand',$6)""",
                    seed_data['org_a_id'], uuid.UUID(w['property']), uuid.UUID(run), date(2025, 12, 31), Decimal('12500'), seed_data['user_a_admin_id'])
            else:
                await writer.execute("""INSERT INTO payment(organization_id,bank_account_id,property_id,direction,amount,
                    value_date,payment_type,owner_profile_id,status,booked_at,booked_by)
                    VALUES ($1,$2,$3,'IN',100,'2025-02-01','HAUSGELD_VORSCHUSS',$4,'booked',now(),$5)""",
                    seed_data['org_a_id'], uuid.UUID(w['bank_giro']), uuid.UUID(w['property']), uuid.UUID(w['owner_a']), seed_data['user_a_admin_id'])
        with pytest.raises(asyncpg.CheckViolationError) as refused:
            await asyncio.wait_for(deletion, timeout=5)
        assert refused.value.constraint_name == 'settlement_delete_has_' + ('ledger' if source == 'ledger' else 'bookings')
        assert await writer.fetchval('SELECT count(*) FROM settlement_run WHERE id=$1', uuid.UUID(run)) == 1
    finally:
        if deletion is not None and not deletion.done():
            deletion.cancel()
            await asyncio.gather(deletion, return_exceptions=True)
        await deleter.close()
        await writer.close()


@pytest.mark.asyncio
async def test_retention_catalog_keeps_empty_weg_drafts_conditional(client_a_admin, seed_data, migrator_session):
    row = (await migrator_session.execute(text("SELECT disposition,retain_when FROM organization_retention_catalog() WHERE entity='settlement_run'"))).one()
    assert row.disposition == 'conditional'
    assert 'weg_draft_delete_reason' in row.retain_when
    w = _seed_weg(seed_data)
    run = await asset_draft(client_a_admin, w, 2025)
    query = text(f'SELECT ({row.retain_when}) FROM settlement_run t WHERE t.id=:run')
    assert await migrator_session.scalar(query, {'run': run}) is False
    await payment(client_a_admin, w, '100.00', kind='HAUSGELD_VORSCHUSS')
    assert await migrator_session.scalar(query, {'run': run}) is True
