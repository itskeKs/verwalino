"""T41 operational tasks: API contract and source authorization."""
import pytest

pytestmark = pytest.mark.anyio


async def create(client, **changes):
    response = await client.post('/api/tasks', json={'title': 'Prüfung', **changes})
    assert response.status_code == 201, response.text
    return response.json()


async def test_tasks_require_authentication(client_unauth):
    assert (await client_unauth.get('/api/tasks')).status_code == 401


async def test_global_task_crud(client_a_admin):
    row = await create(client_a_admin)
    assert row['title'] == 'Prüfung'
    assert row['note'] == '' and row['due_date'] is None and row['completed'] is False
    assert row['property_id'] is None and row['unit_id'] is None
    assert row['created_at'] and row['updated_at'] and row['assigned_user_id']
    path = f"/api/tasks/{row['id']}"
    assert (await client_a_admin.get(path)).json() == row
    response = await client_a_admin.patch(path, json={'completed': True, 'due_date': '2026-10-22'})
    assert response.status_code == 200, response.text
    assert response.json()['completed'] is True
    response = await client_a_admin.patch(path, json={'completed': False, 'due_date': None})
    assert response.status_code == 200 and response.json()['due_date'] is None
    assert response.json()['completed'] is False
    assert (await client_a_admin.delete(path)).status_code == 204
    assert (await client_a_admin.get(path)).status_code == 404


import asyncio
import uuid
from contextlib import closing
from datetime import date, timedelta

import httpx
import psycopg2
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.db import get_tenant_session
from app.core.tenant import AuthContext, get_current_user
from app.main import app
from tests.conftest import MIGRATOR_SYNC_URL
from tests.test_meters_api import meter_world as task_world
from tests.test_financial_scope_boundary import scope_seed, worker_tenant  # noqa: F401
from tests.test_property_master_access import existing, principals
from tests.test_property_source_access import grant
from tests.test_property_source_enforcement import synthetic_enforcement, change_mode
from tests.test_scoped_account_statements import client, mutate


@pytest.mark.parametrize('body', [
    {}, {'title': ''}, {'title': ' \n\t '}, {'title': 'x' * 256},
    {'title': 'bad\x00title'}, {'title': 'ok', 'note': 'bad\x00note'},
    {'title': 'ok', 'note': 'x' * 10001}, {'title': None},
    {'title': 'ok', 'note': None}, {'title': 'ok', 'completed': None},
    {'title': 'ok', 'due_date': '2026-02-30'}, {'title': 'ok', 'organization_id': str(uuid.uuid4())},
    {'title': 'ok', 'created_by': str(uuid.uuid4())}, {'title': 'ok', 'unknown': 'field'},
])
async def test_create_validation(client_a_admin, body):
    assert (await client_a_admin.post('/api/tasks', json=body)).status_code == 422


@pytest.mark.parametrize('body', [
    {'title': ''}, {'title': '  '}, {'title': 'x' * 256}, {'title': 'bad\x00text'},
    {'note': 'bad\x00text'}, {'note': 'x' * 10001}, {'title': None},
    {'note': None}, {'completed': None}, {'assigned_user_id': None},
    {'created_by': str(uuid.uuid4())}, {'organization_id': str(uuid.uuid4())},
    {'due_date': '2025-02-29'}, {'extra': True},
])
async def test_patch_validation_is_atomic(client_a_admin, body):
    row = await create(client_a_admin)
    path = f"/api/tasks/{row['id']}"
    assert (await client_a_admin.patch(path, json=body)).status_code == 422
    assert (await client_a_admin.get(path)).json() == row


async def test_text_boundaries_and_noop_patch(client_a_admin):
    row = await create(client_a_admin, title='x' * 255, note='n' * 10000)
    response = await client_a_admin.patch(f"/api/tasks/{row['id']}", json={})
    assert response.status_code == 200 and response.json()['title'] == 'x' * 255
    changed = await client_a_admin.patch(f"/api/tasks/{row['id']}", json={'title': '  Normalized  ', 'note': ''})
    assert changed.status_code == 200 and changed.json()['title'] == 'Normalized'
    assert changed.json()['note'] == ''


async def test_roles_and_org_isolation(client_a_admin, client_a_clerk, client_a_readonly,
                                       client_b_admin, client_unauth, seed_data):
    own = await create(client_a_clerk)
    other = await create(client_b_admin)
    assert own['created_by'] == str(seed_data['user_a_clerk_id'])
    assert own['assigned_user_id'] == own['created_by']
    assert own['assigned_user_name'] == 'Clerk Alpha'
    assert 'email' not in own and 'password_hash' not in own
    path = f"/api/tasks/{own['id']}"
    assert (await client_a_readonly.get(path)).status_code == 200
    for method, data in [('post', {'title': 'denied'}), ('patch', {'completed': True}), ('delete', None)]:
        url = '/api/tasks' if method == 'post' else path
        kwargs = {'json': data} if data is not None else {}
        assert (await getattr(client_a_readonly, method)(url, **kwargs)).status_code == 403
        assert (await getattr(client_unauth, method)(url, **kwargs)).status_code == 401
    for api, hidden in [(client_a_admin, other), (client_b_admin, own)]:
        url = f"/api/tasks/{hidden['id']}"
        assert (await api.get(url)).status_code == 404
        assert (await api.patch(url, json={'completed': True})).status_code == 404
        assert (await api.delete(url)).status_code == 404
        assert hidden['id'] not in {r['id'] for r in (await api.get('/api/tasks')).json()['items']}
    assert (await client_a_admin.patch(path, json={'completed': True})).status_code == 200
    assert (await client_a_clerk.delete(path)).status_code == 204


@pytest.mark.parametrize('query', [{'offset': -1}, {'limit': 0}, {'limit': 101},
                                  {'due_until': '2026-13-01'}, {'completed': 'nonsense'}])
async def test_filter_validation(client_a_admin, query):
    assert (await client_a_admin.get('/api/tasks', params=query)).status_code == 422


async def test_dates_filters_pagination_and_combination(client_a_admin, task_world, seed_data):
    today = date.today()
    dates = [today - timedelta(days=1), today, today + timedelta(days=1),
             today + timedelta(days=2), None]
    rows = []
    for index, day in enumerate(dates):
        rows.append(await create(client_a_admin, property_id=task_world['property'],
            unit_id=task_world['unit'] if index != 3 else None,
            due_date=day.isoformat() if day else None, completed=index == 1))
    for day in dates[:-1]:
        query = {'property_id': task_world['property'], 'due_until': day.isoformat()}
        response = await client_a_admin.get('/api/tasks', params=query)
        assert response.status_code == 200
        assert {r['id'] for r in response.json()['items']} == {
            row['id'] for row in rows if row['due_date'] and row['due_date'] <= day.isoformat()}
    first = (await client_a_admin.get('/api/tasks', params={
        'property_id': task_world['property'], 'limit': 2})).json()
    second = (await client_a_admin.get('/api/tasks', params={
        'property_id': task_world['property'], 'limit': 2, 'offset': 2})).json()
    third = (await client_a_admin.get('/api/tasks', params={
        'property_id': task_world['property'], 'limit': 2, 'offset': 4})).json()
    assert [first['next_offset'], second['next_offset'], third['next_offset']] == [2, 4, None]
    assert [r['id'] for p in [first, second, third] for r in p['items']] == [r['id'] for r in reversed(rows)]
    query = {'property_id': task_world['property'], 'unit_id': task_world['unit'],
        'assigned_user_id': str(seed_data['user_a_admin_id']), 'completed': 'false',
        'due_until': (today + timedelta(days=1)).isoformat()}
    response = await client_a_admin.get('/api/tasks', params=query)
    assert {r['id'] for r in response.json()['items']} == {rows[0]['id'], rows[2]['id']}
    query['completed'] = 'true'
    assert [r['id'] for r in (await client_a_admin.get('/api/tasks', params=query)).json()['items']] == [rows[1]['id']]
    query['assigned_user_id'] = str(seed_data['user_a_clerk_id'])
    assert (await client_a_admin.get('/api/tasks', params=query)).json() == {'items': [], 'next_offset': None}


async def lease_for(api, unit_id):
    person = await api.post('/api/persons', json={'first_name': 'T41', 'last_name': 'Tenant'})
    assert person.status_code == 201, person.text
    tenant = await api.post('/api/tenant-profiles', json={'person_id': person.json()['id']})
    assert tenant.status_code == 201, tenant.text
    lease = await api.post('/api/leases', json={'unit_id': unit_id,
        'tenant_profile_id': tenant.json()['id'], 'valid_from': '2026-01-01',
        'base_rent': '700.00', 'advance_payment': '150.00'})
    assert lease.status_code == 201, lease.text
    return lease.json()['id']


async def test_canonical_parents_labels_filters_and_relink(client_a_admin, task_world):
    w = task_world
    lease = await lease_for(client_a_admin, w['unit'])
    row = await create(client_a_admin, lease_agreement_id=lease)
    assert (row['property_id'], row['unit_id']) == (w['property'], w['unit'])
    assert row['property_name'].startswith('T29 ') and row['unit_number'] == '1'
    assert row['lease_label'] == 'Vertrag ab 2026-01-01 · Einheit 1'
    assert [r['id'] for r in (await client_a_admin.get('/api/tasks', params={
        'lease_agreement_id': lease, 'property_id': w['property'], 'unit_id': w['unit']})).json()['items']] == [row['id']]
    path = f"/api/tasks/{row['id']}"
    for changes in [dict(property_id=w['other_property']), dict(unit_id=w['other_unit']),
                    dict(property_id=w['other_property'], lease_agreement_id=lease)]:
        assert (await client_a_admin.post('/api/tasks', json={'title': 'Denied', 'lease_agreement_id': lease, **changes})).status_code == 422
        assert (await client_a_admin.patch(path, json=changes)).status_code == 422
    for changes in [dict(property_id=w['foreign_property']), dict(unit_id=w['foreign_unit']),
                    dict(lease_agreement_id=str(uuid.uuid4()))]:
        assert (await client_a_admin.post('/api/tasks', json={'title': 'Denied', **changes})).status_code == 404
        assert (await client_a_admin.patch(path, json=changes)).status_code == 404
    result = await client_a_admin.patch(path, json={'lease_agreement_id': None, 'unit_id': w['other_unit']})
    assert result.status_code == 200, result.text
    assert result.json()['property_id'] == w['other_property']
    result = await client_a_admin.patch(path, json={'lease_agreement_id': lease})
    assert result.status_code == 200 and result.json()['unit_id'] == w['unit']
    result = await client_a_admin.patch(path, json={
        'property_id': None, 'unit_id': None, 'lease_agreement_id': None})
    assert result.status_code == 200
    assert all(result.json()[key] is None for key in ['property_id', 'unit_id', 'lease_agreement_id',
        'property_name', 'unit_number', 'lease_label'])
    unit_only = await create(client_a_admin, unit_id=w['unit'])
    assert unit_only['property_id'] == w['property']


async def test_assignment_active_same_org_and_inactive_existing_retained(client_a_admin, seed_data):
    clerk = str(seed_data['user_a_clerk_id'])
    row = await create(client_a_admin, assigned_user_id=clerk)
    path = f"/api/tasks/{row['id']}"
    for target in [str(seed_data['user_b_admin_id']), str(uuid.uuid4())]:
        assert (await client_a_admin.post('/api/tasks', json={'title': 'Denied', 'assigned_user_id': target})).status_code == 404
        assert (await client_a_admin.patch(path, json={'assigned_user_id': target})).status_code == 404
    await mutate(text('UPDATE "user" SET is_active=false WHERE id=:id'), {'id': uuid.UUID(clerk)})
    try:
        assert (await client_a_admin.get(path)).status_code == 200
        changed = await client_a_admin.patch(path, json={'completed': True})
        assert changed.status_code == 200, changed.text
        assert changed.json()['assigned_user_id'] == clerk
        assert (await client_a_admin.post('/api/tasks', json={'title': 'Denied', 'assigned_user_id': clerk})).status_code == 404
        # Explicitly retaining an inactive assignee does not orphan old work.
        assert (await client_a_admin.patch(path, json={'assigned_user_id': clerk})).status_code == 200
        assert (await client_a_admin.patch(path, json={'assigned_user_id': str(seed_data['user_a_admin_id'])})).status_code == 200
        assert (await client_a_admin.patch(path, json={'assigned_user_id': clerk})).status_code == 404
    finally:
        await mutate(text('UPDATE "user" SET is_active=true WHERE id=:id'), {'id': uuid.UUID(clerk)})
    assert (await client_a_admin.patch(path, json={'assigned_user_id': str(seed_data['user_a_readonly_id'])})).status_code == 200


async def test_live_actor_snapshot_cannot_keep_write_authority(client_a_admin, seed_data):
    row = await create(client_a_admin)
    path = f"/api/tasks/{row['id']}"
    stale = AuthContext(seed_data['user_a_readonly_id'], seed_data['org_a_id'], 'OWNER_ADMIN')
    app.dependency_overrides[get_current_user] = lambda: stale
    try:
        assert (await client_a_admin.get(path)).status_code == 200
        assert (await client_a_admin.post('/api/tasks', json={'title': 'Denied'})).status_code == 403
        assert (await client_a_admin.patch(path, json={'completed': True})).status_code == 403
        assert (await client_a_admin.delete(path)).status_code == 403
        stale.user_id = seed_data['user_b_admin_id']
        for verb in ['get', 'delete']:
            assert (await getattr(client_a_admin, verb)(path)).status_code == 401
        stale.user_id = uuid.uuid4()
        assert (await client_a_admin.get('/api/tasks')).status_code == 401
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert (await client_a_admin.get(path)).json() == row


async def test_source_read_not_prepare_revoke_and_no_detach(scope_seed, migrator_session, monkeypatch):
    await migrator_session.commit()
    async with client(principals(scope_seed), 'actor') as api:
        prop, unit = await existing(api)
        row = await create(api, unit_id=unit)
        domain, receipt = await grant(api, scope_seed, prop, caps=['read'])
        path = f"/api/tasks/{row['id']}"
        async with synthetic_enforcement(scope_seed):
            assert (await api.get(path)).status_code == 200
            assert [r['id'] for r in (await api.get('/api/tasks')).json()['items']] == [row['id']]
            assert (await api.patch(path, json={'completed': True})).status_code == 404
            assert (await api.patch(path, json={'property_id': None, 'unit_id': None})).status_code == 404
            assert (await api.delete(path)).status_code == 404
            assert (await api.post('/api/tasks', json={'title': 'Denied', 'unit_id': unit})).status_code == 404
            assert (await api.post(f'/api/property-data-domains/{domain}/grants/{receipt}/revoke')).status_code == 204
            from app.repositories.user_repo import UserRepository
            async def no_labels(*args):
                pytest.fail('Revoked source reached assignee label lookup')
            monkeypatch.setattr(UserRepository, 'get_by_id', no_labels)
            assert (await api.get(path)).status_code == 404
            assert (await api.get('/api/tasks')).json() == {'items': [], 'next_offset': None}
            assert (await api.get('/api/tasks', params={'unit_id': unit})).status_code == 404


async def test_prepare_paused_and_old_new_parent_rights(scope_seed, migrator_session):
    await migrator_session.commit()
    async with client(principals(scope_seed), 'actor') as api:
        prop, unit = await existing(api)
        other, other_unit = await existing(api)
        await grant(api, scope_seed, prop, caps=['read', 'prepare'])
        await grant(api, scope_seed, other, caps=['read'])
        async with synthetic_enforcement(scope_seed):
            row = await create(api, unit_id=unit)
            path = f"/api/tasks/{row['id']}"
            assert (await api.patch(path, json={'unit_id': other_unit})).status_code == 404
            assert (await api.patch(path, json={'completed': True})).status_code == 200
            await change_mode(scope_seed, 'PAUSED')
            assert (await api.get(path)).status_code == 200
            assert (await api.patch(path, json={'completed': False})).status_code == 404
            assert (await api.delete(path)).status_code == 404
            assert (await api.post('/api/tasks', json={'title': 'Denied', 'unit_id': unit})).status_code == 404
            # A global task has no financial or property source requirement.
            global_task = await create(api)
            assert (await api.delete(f"/api/tasks/{global_task['id']}")).status_code == 204


async def test_commit_visible_when_asgi_success_starts(client_a_admin):
    title = f'Commit {uuid.uuid4()}'
    observed = []
    async def observe(scope, receive, send):
        async def observed_send(message):
            if message['type'] == 'http.response.start':
                with closing(psycopg2.connect(MIGRATOR_SYNC_URL)) as connection, connection.cursor() as cursor:
                    cursor.execute('SELECT count(*) FROM task WHERE title=%s', (title,))
                    observed.append(cursor.fetchone()[0])
            await send(message)
        await app(scope, receive, observed_send)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=observe), base_url='http://test',
                                 cookies=client_a_admin.cookies) as api:
        await create(api, title=title)
    assert observed == [1], 'Task must be committed before HTTP 201 is emitted'


async def test_parallel_patches_do_not_lose_unrelated_fields(client_a_admin):
    row = await create(client_a_admin)
    path = f"/api/tasks/{row['id']}"
    responses = await asyncio.wait_for(asyncio.gather(
        client_a_admin.patch(path, json={'title': 'Parallel title'}),
        client_a_admin.patch(path, json={'note': 'Parallel note', 'completed': True})), timeout=10)
    assert [r.status_code for r in responses] == [200, 200]
    result = (await client_a_admin.get(path)).json()
    assert (result['title'], result['note'], result['completed']) == ('Parallel title', 'Parallel note', True)
    assert result['updated_at'] > result['created_at']


async def test_failure_after_flush_rolls_back(client_a_admin, monkeypatch):
    from app.services.task_service import TaskService
    title = f'Rollback {uuid.uuid4()}'
    async def fail(*args):
        raise RuntimeError('Task serialization failed after insert')
    monkeypatch.setattr(TaskService, 'output', fail)
    with pytest.raises(RuntimeError, match='Task serialization failed'):
        await client_a_admin.post('/api/tasks', json={'title': title})
    with closing(psycopg2.connect(MIGRATOR_SYNC_URL)) as connection, connection.cursor() as cursor:
        cursor.execute('SELECT count(*) FROM task WHERE title=%s', (title,))
        assert cursor.fetchone()[0] == 0


async def test_sql_force_rls_both_orgs_role_and_parent_coherence(
        client_a_admin, client_b_admin, task_world, seed_data, app_rw_session, migrator_session):
    own = await create(client_a_admin, unit_id=task_world['unit'])
    other = await create(client_b_admin)
    db = app_rw_session
    for org, actor, visible, hidden in [
        (seed_data['org_a_id'], seed_data['user_a_admin_id'], own, other),
        (seed_data['org_b_id'], seed_data['user_b_admin_id'], other, own),
    ]:
        await db.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:actor,true)"),
            {'org': str(org), 'actor': str(actor)})
        assert await db.scalar(text('SELECT count(*) FROM task WHERE id=:id'), {'id': uuid.UUID(visible['id'])}) == 1
        assert await db.scalar(text('SELECT count(*) FROM task WHERE id=:id'), {'id': uuid.UUID(hidden['id'])}) == 0
        assert (await db.execute(text('UPDATE task SET completed=true WHERE id=:id'), {'id': uuid.UUID(hidden['id'])})).rowcount == 0
        assert (await db.execute(text('DELETE FROM task WHERE id=:id'), {'id': uuid.UUID(hidden['id'])})).rowcount == 0
        with pytest.raises(DBAPIError):
            async with db.begin_nested():
                await db.execute(text("INSERT INTO task(organization_id,created_by,assigned_user_id,title) VALUES(:org,:actor,:actor,'Foreign')"),
                    {'org': uuid.UUID(hidden['organization_id']), 'actor': actor})
    await db.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:actor,true)"),
        {'org': str(seed_data['org_a_id']), 'actor': str(seed_data['user_a_admin_id'])})
    for column, target in [('property_id', task_world['other_property']),
                           ('property_id', task_world['foreign_property']),
                           ('unit_id', task_world['other_unit']),
                           ('assigned_user_id', str(seed_data['user_b_admin_id'])),
                           ('created_by', str(seed_data['user_a_clerk_id']))]:
        with pytest.raises(DBAPIError):
            async with db.begin_nested():
                await db.execute(text(f'UPDATE task SET {column}=:target WHERE id=:id'),
                    {'id': uuid.UUID(own['id']), 'target': uuid.UUID(target)})
    for actor in [seed_data['user_a_readonly_id'], uuid.uuid4()]:
        await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {'actor': str(actor)})
        assert (await db.execute(text('UPDATE task SET completed=true WHERE id=:id'), {'id': uuid.UUID(own['id'])})).rowcount == 0
        assert (await db.execute(text('DELETE FROM task WHERE id=:id'), {'id': uuid.UUID(own['id'])})).rowcount == 0
        with pytest.raises(DBAPIError):
            async with db.begin_nested():
                await db.execute(text("INSERT INTO task(organization_id,created_by,assigned_user_id,title) VALUES(:org,:actor,:assignee,'Denied')"),
                    {'org': seed_data['org_a_id'], 'actor': actor, 'assignee': seed_data['user_a_admin_id']})
    flags = (await migrator_session.execute(text("SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE relname='task'"))).one()
    assert flags == (True, True)
    assert {r[0] for r in (await migrator_session.execute(text("SELECT cmd FROM pg_policies WHERE tablename='task'"))).all()} == {'SELECT', 'INSERT', 'UPDATE', 'DELETE'}


async def test_foreign_parent_hidden_before_own_child_mismatch(client_a_admin, task_world):
    lease = await lease_for(client_a_admin, task_world['unit'])
    body = {'title': 'Denied', 'property_id': task_world['foreign_property'],
            'unit_id': task_world['other_unit'], 'lease_agreement_id': lease}
    assert (await client_a_admin.post('/api/tasks', json=body)).status_code == 404
    row = await create(client_a_admin)
    body.pop('title')
    assert (await client_a_admin.patch(f"/api/tasks/{row['id']}", json=body)).status_code == 404


async def test_sql_old_source_prepare_and_revocation(scope_seed, migrator_session):
    await migrator_session.commit()
    ids = principals(scope_seed)
    async with client(ids, 'actor') as api:
        prop, unit = await existing(api)
        row = await create(api, unit_id=unit)
        domain, receipt = await grant(api, scope_seed, prop, caps=['read'])
        async with synthetic_enforcement(scope_seed):
            async with get_tenant_session(ids['org']) as db:
                await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {'actor': str(ids['actor'])})
                values = {'id': uuid.UUID(row['id']), 'org': ids['org'], 'actor': ids['actor'],
                          'prop': uuid.UUID(prop), 'unit': uuid.UUID(unit)}
                assert await db.scalar(text('SELECT count(*) FROM task WHERE id=:id'), values) == 1
                assert (await db.execute(text('UPDATE task SET property_id=NULL,unit_id=NULL WHERE id=:id'), values)).rowcount == 0
                assert (await db.execute(text('DELETE FROM task WHERE id=:id'), values)).rowcount == 0
                with pytest.raises(DBAPIError):
                    async with db.begin_nested():
                        await db.execute(text("INSERT INTO task(organization_id,created_by,assigned_user_id,property_id,unit_id,title) VALUES(:org,:actor,:actor,:prop,:unit,'Denied')"), values)
            assert (await api.post(f'/api/property-data-domains/{domain}/grants/{receipt}/revoke')).status_code == 204
            async with get_tenant_session(ids['org']) as db:
                await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {'actor': str(ids['actor'])})
                assert await db.scalar(text('SELECT count(*) FROM task WHERE id=:id'), values) == 0


async def test_lease_source_read_prepare_and_pause(scope_seed, migrator_session):
    await migrator_session.commit()
    async with client(principals(scope_seed), 'actor') as api:
        prop, unit = await existing(api)
        lease = await lease_for(api, unit)
        row = await create(api, lease_agreement_id=lease)
        domain, receipt = await grant(api, scope_seed, prop, caps=['read'])
        path = f"/api/tasks/{row['id']}"
        async with synthetic_enforcement(scope_seed):
            assert (await api.get(path)).status_code == 200
            assert (await api.patch(path, json={'completed': True})).status_code == 404
            assert (await api.post('/api/tasks', json={'title': 'Denied', 'lease_agreement_id': lease})).status_code == 404
            await change_mode(scope_seed, 'PAUSED')
            assert (await api.get(path)).status_code == 200
            assert (await api.delete(path)).status_code == 404
            await change_mode(scope_seed, 'ENFORCED')
            assert (await api.post(f'/api/property-data-domains/{domain}/grants/{receipt}/revoke')).status_code == 204
            assert (await api.get(path)).status_code == 404
            assert (await api.get('/api/tasks', params={'lease_agreement_id': lease})).status_code == 404


async def test_sql_parent_foreign_keys_and_lease_coherence_bypass_role(
        client_a_admin, task_world, seed_data, migrator_session):
    lease = await lease_for(client_a_admin, task_world['unit'])
    row = await create(client_a_admin, lease_agreement_id=lease)
    for changes in [
        "property_id=:foreign_property", "unit_id=:other_unit", "unit_id=NULL",
        "property_id=NULL", "lease_agreement_id=:missing", "assigned_user_id=:foreign_actor",
        "created_by=:foreign_actor", "organization_id=:foreign_org",
    ]:
        with pytest.raises(DBAPIError):
            async with migrator_session.begin_nested():
                await migrator_session.execute(text(f'UPDATE task SET {changes} WHERE id=:id'), {
                    'id': uuid.UUID(row['id']), 'foreign_property': uuid.UUID(task_world['foreign_property']),
                    'other_unit': uuid.UUID(task_world['other_unit']), 'missing': uuid.uuid4(),
                    'foreign_actor': seed_data['user_b_admin_id'], 'foreign_org': seed_data['org_b_id']})
    # Full SQL insertion also rejects a lease detached from its canonical unit.
    with pytest.raises(DBAPIError):
        async with migrator_session.begin_nested():
            await migrator_session.execute(text("INSERT INTO task(organization_id,created_by,assigned_user_id,property_id,lease_agreement_id,title) VALUES(:org,:actor,:actor,:prop,:lease,'Incoherent')"),
                {'org': seed_data['org_a_id'], 'actor': seed_data['user_a_admin_id'],
                 'prop': uuid.UUID(task_world['property']), 'lease': uuid.UUID(lease)})


async def test_inactive_or_removed_live_actor_cannot_read_or_mutate(client_a_admin, seed_data):
    # Isolated principal: never deactivate a shared fixture actor.
    created_user = await client_a_admin.post('/api/users', json={
        'email': f'{uuid.uuid4()}@tasks.invalid', 'password': 'testpass1234',
        'display_name': 'Temporary task actor', 'role': 'CLERK'})
    assert created_user.status_code == 201, created_user.text
    actor = uuid.UUID(created_user.json()['id'])
    stale = AuthContext(actor, seed_data['org_a_id'], 'OWNER_ADMIN')
    row = await create(client_a_admin, assigned_user_id=str(actor))
    path = f"/api/tasks/{row['id']}"
    for statement in ['UPDATE "user" SET is_active=false WHERE id=:id',
                      'DELETE FROM membership WHERE user_id=:id']:
        await mutate(text(statement), {'id': actor})
        app.dependency_overrides[get_current_user] = lambda: stale
        try:
            assert (await client_a_admin.get('/api/tasks')).status_code == 401
            assert (await client_a_admin.get(path)).status_code == 401
            assert (await client_a_admin.post('/api/tasks', json={'title': 'Denied'})).status_code == 401
            assert (await client_a_admin.patch(path, json={'completed': True})).status_code == 401
            assert (await client_a_admin.delete(path)).status_code == 401
        finally:
            app.dependency_overrides.pop(get_current_user, None)
        await mutate(text('UPDATE "user" SET is_active=true WHERE id=:id'), {'id': actor})
    assert (await client_a_admin.patch(path, json={'completed': True})).status_code == 200
    assert (await client_a_admin.post('/api/tasks', json={'title': 'Denied', 'assigned_user_id': str(actor)})).status_code == 404


async def test_commit_failure_never_emits_success(client_a_admin):
    from sqlalchemy import event
    from sqlalchemy.orm import Session
    from app.models.task import Task
    title = f'Commit failure {uuid.uuid4()}'
    def mark_flush(session, flush_context):
        if any(isinstance(row, Task) and row.title == title for row in session.new):
            session.info['fail_task_commit'] = True
    def fail_commit(session):
        if session.info.get('fail_task_commit'):
            raise RuntimeError('Synthetic task commit failure')
    statuses = []
    async def observe(scope, receive, send):
        async def observed_send(message):
            if message['type'] == 'http.response.start':
                statuses.append(message['status'])
            await send(message)
        await app(scope, receive, observed_send)
    event.listen(Session, 'after_flush', mark_flush)
    event.listen(Session, 'before_commit', fail_commit)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=observe, raise_app_exceptions=False),
            base_url='http://test', cookies=client_a_admin.cookies) as api:
            result = await api.post('/api/tasks', json={'title': title})
            assert result.status_code == 500
    finally:
        event.remove(Session, 'before_commit', fail_commit)
        event.remove(Session, 'after_flush', mark_flush)
    assert statuses == [500]
    with closing(psycopg2.connect(MIGRATOR_SYNC_URL)) as connection, connection.cursor() as cursor:
        cursor.execute('SELECT count(*) FROM task WHERE title=%s', (title,))
        assert cursor.fetchone()[0] == 0


@pytest.mark.parametrize('day', ['0001-01-01', '2024-02-29', '9999-12-31'])
async def test_date_storage_boundaries(client_a_admin, day):
    row = await create(client_a_admin, due_date=day)
    assert row['due_date'] == day
    assert (await client_a_admin.get(f"/api/tasks/{row['id']}")).json()['due_date'] == day
    response = await client_a_admin.patch(f"/api/tasks/{row['id']}", json={'due_date': None})
    assert response.status_code == 200 and response.json()['due_date'] is None


async def test_unknown_live_role_is_denied_not_successful_empty_list(client_a_admin, seed_data):
    user = await client_a_admin.post('/api/users', json={
        'email': f'{uuid.uuid4()}@tasks.invalid', 'password': 'testpass1234',
        'display_name': 'Unknown task role', 'role': 'CLERK'})
    assert user.status_code == 201, user.text
    actor = uuid.UUID(user.json()['id'])
    await mutate(text("UPDATE membership SET role='UNKNOWN' WHERE user_id=:id"), {'id': actor})
    row = await create(client_a_admin)
    path = f"/api/tasks/{row['id']}"
    async with client({'org': seed_data['org_a_id'], 'actor': actor}, 'actor') as api:
        for url in ['/api/tasks', path]:
            assert (await api.get(url)).status_code == 403
        assert (await api.post('/api/tasks', json={'title': 'Denied'})).status_code == 403
        assert (await api.patch(path, json={'completed': True})).status_code == 403
        assert (await api.delete(path)).status_code == 403
    assert (await client_a_admin.post('/api/tasks', json={
        'title': 'Denied', 'assigned_user_id': str(actor)})).status_code == 404


async def test_sql_temp_parent_shadow_cannot_forge_org_assignment(app_rw_session, seed_data):
    db = app_rw_session
    values = {'org': seed_data['org_a_id'], 'actor': seed_data['user_a_admin_id'],
              'foreign_actor': seed_data['user_b_admin_id']}
    await db.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:actor,true)"),
        {key: str(values[key]) for key in ('org', 'actor')})
    await db.execute(text('CREATE TEMP TABLE "user" (LIKE public."user" INCLUDING DEFAULTS) ON COMMIT DROP'))
    await db.execute(text('INSERT INTO pg_temp."user" SELECT * FROM public."user"'))
    await db.execute(text("INSERT INTO pg_temp.\"user\"(id,organization_id,email,password_hash,display_name,is_active) VALUES(:foreign_actor,:org,'forged@tasks.invalid','test','Forged',true)"), values)
    await db.execute(text('CREATE TEMP TABLE membership (LIKE public.membership INCLUDING DEFAULTS) ON COMMIT DROP'))
    await db.execute(text("INSERT INTO pg_temp.membership(organization_id,user_id,role) VALUES(:org,:foreign_actor,'CLERK')"), values)
    with pytest.raises(DBAPIError, match='Task parent coherence violation'):
        async with db.begin_nested():
            await db.execute(text("INSERT INTO public.task(organization_id,created_by,assigned_user_id,title) VALUES(:org,:actor,:foreign_actor,'Forged assignment')"), values)
    assert await db.scalar(text("INSERT INTO public.task(organization_id,created_by,assigned_user_id,title) VALUES(:org,:actor,:actor,'Valid assignment') RETURNING id"), values)
