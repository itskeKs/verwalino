"""Active runs of one lease cannot overlap across accounting scopes, whatever a user may read."""
import uuid
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.test_financial_scope_boundary import actor, scope_seed as scope_seed, worker_tenant as worker_tenant  # noqa: F401
from tests.test_scope_grants import scope_fixture
from tests.test_scoped_account_statements import client

pytestmark = pytest.mark.asyncio

CONFLICT = {
    'code': 'settlement_period_conflict',
    'message': 'Für diesen Mietvertrag existiert bereits eine Abrechnung mit überschneidendem Zeitraum.',
}
HELD = ('2026-06-01', '2027-05-31')  # Held by the first accounting scope.


async def overlap_fixture(session, seed):
    """One property, two tenant scopes and two leases; the clerk prepares only the second scope."""
    ids = await scope_fixture(session, seed)
    ids.update({key: uuid.uuid4() for key in
                ('second_scope', 'weg_other', 'person', 'profile', 'unit', 'lease', 'other_unit', 'other_lease')})
    ids['first_scope'], ids['weg_scope'] = ids['other_scope'], ids['scope']
    await session.execute(text("INSERT INTO accounting_scope(id,organization_id,property_id,scope_type,label,valid_from,whole_property,origin,created_by) VALUES (:second_scope,:org,:property,'MIET','Second tenant sphere','2025-01-01',true,'REVIEWED',:actor)"), ids)
    await session.execute(text("INSERT INTO accounting_scope(id,organization_id,property_id,scope_type,label,valid_from,whole_property,origin,created_by) VALUES (:weg_other,:org,:property,'WEG','Second owner sphere','2025-01-01',true,'REVIEWED',:actor)"), ids)
    await session.execute(text("INSERT INTO person(id,organization_id,last_name,street,zip_code,city) VALUES (:person,:org,'Overlap','','','')"), ids)
    await session.execute(text("INSERT INTO tenant_profile(id,organization_id,person_id,notes) VALUES (:profile,:org,:person,'')"), ids)
    for unit, lease in (('unit', 'lease'), ('other_unit', 'other_lease')):
        await session.execute(text("INSERT INTO unit(id,organization_id,property_id,number,usage_type) VALUES (:unit,:org,:property,:number,'WOHNUNG')"),
                              {**ids, 'unit': ids[unit], 'number': str(ids[unit])[:8]})
        await session.execute(text("INSERT INTO lease_agreement(id,organization_id,unit_id,tenant_profile_id,valid_from,base_rent,advance_payment,notes) VALUES (:lease,:org,:unit,:profile,'2024-01-01',500,100,'')"),
                              {**ids, 'unit': ids[unit], 'lease': ids[lease]})
    for scope, user, capabilities in (('weg_scope', 'actor', ['read', 'prepare']), ('weg_other', 'actor', ['read', 'prepare']),
                                      ('first_scope', 'actor', ['read', 'prepare', 'approve']),
                                      ('second_scope', 'actor', ['read', 'prepare', 'approve']),
                                      ('second_scope', 'clerk', ['read', 'prepare'])):
        await session.execute(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES (:org,:scope,:user,:capabilities,:actor,'Reviewed team assignment')"),
                              {**ids, 'scope': ids[scope], 'user': ids[user], 'capabilities': capabilities})
    return ids


async def add_run(session, ids, start, end, *, scope='first_scope', lease='lease', status='draft', successor=None):
    """Insert a run into one scope the way the application does: with the scope selected."""
    run = uuid.uuid4()
    params = {**ids, 'run': run, 'lease_id': ids[lease] if lease else None,
              'start': date.fromisoformat(start), 'end': date.fromisoformat(end), 'status': status, 'successor': successor, 'digest': 'a' * 64,
              'unit_id': ids['unit' if lease == 'lease' else 'other_unit'] if lease else None,
              'type': 'BK_MIETER' if lease else 'WEG_JAHRESABRECHNUNG', 'profile': ids['profile'] if lease else None}
    await session.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:actor,true),set_config('app.current_scope_id',:scope,true)"),
                          {'org': str(ids['org']), 'actor': str(ids['actor']), 'scope': str(ids[scope]) if scope else ''})
    columns = 'id,organization_id,property_id,run_type,period_start,period_end,unit_id,lease_agreement_id,tenant_profile_id,status,engine_version'
    values = ":run,:org,:property,:type,:start,:end,:unit_id,:lease_id,:profile,:status,'test'"
    if status == 'finalized':
        columns += ',snapshot_payload,snapshot_sha256,finalized_at,finalized_by'
        values += ",'{}'::jsonb,:digest,now(),:actor"
    await session.execute(text(f'INSERT INTO settlement_run({columns}) VALUES ({values})'), params)
    if successor is not None:
        await session.execute(text('UPDATE settlement_run SET superseded_by_id=:successor WHERE id=:run'), params)
    await session.execute(text("SELECT set_config('app.current_scope_id','',true)"))
    return run


async def clerk_insert(session, ids, start, end, *, lease='lease'):
    """The second scope's clerk cannot read the first scope's runs; the database must still see them."""
    await actor(session, ids)
    await session.execute(text("SELECT set_config('app.current_scope_id',:scope,true)"), {'scope': str(ids['second_scope'])})
    try:
        async with session.begin_nested():
            await session.execute(text("""INSERT INTO settlement_run(organization_id,property_id,run_type,period_start,period_end,unit_id,
                lease_agreement_id,tenant_profile_id,status,engine_version)
                VALUES (:org,:property,'BK_MIETER',:start,:end,:unit,:lease,:profile,'draft','test')"""),
                {**ids, 'start': date.fromisoformat(start), 'end': date.fromisoformat(end), 'lease': ids[lease], 'unit': ids['unit' if lease == 'lease' else 'other_unit']})
    finally:
        await session.execute(text('RESET ROLE'))


@pytest.mark.parametrize('first_status', ['draft', 'finalized'])
@pytest.mark.parametrize('start,end', [
    ('2026-01-01', '2026-12-31'),  # Straddles the held start.
    ('2026-09-01', '2026-12-31'),  # Contained.
    ('2026-01-01', '2027-12-31'),  # Containing.
    ('2026-06-01', '2026-12-31'),  # Shared start.
    ('2026-12-01', '2027-05-31'),  # Shared end.
    ('2026-01-01', '2026-06-01'),  # Inclusive boundary day.
])
async def test_database_rejects_overlapping_runs_hidden_in_another_scope(migrator_session, scope_seed, first_status, start, end):
    ids = await overlap_fixture(migrator_session, scope_seed)
    held = await add_run(migrator_session, ids, *HELD, status=first_status)
    assert await migrator_session.scalar(text('SELECT count(*) FROM settlement_run WHERE id=:held'), {'held': held}) == 1
    with pytest.raises(DBAPIError) as refused:
        await clerk_insert(migrator_session, ids, start, end)
    assert refused.value.orig.sqlstate == '23P01'
    assert refused.value.orig.__cause__.constraint_name == 'settlement_run_active_period'
    assert str(held) not in str(refused.value.orig)
    assert await migrator_session.scalar(text('SELECT count(*) FROM settlement_run WHERE lease_agreement_id=:lease'), ids) == 1


@pytest.mark.parametrize('first_status', ['draft', 'finalized'])
@pytest.mark.parametrize('start,end', [
    HELD,                         # Identical periods stay valid.
    ('2025-06-01', '2026-05-31'),  # Adjacent before.
    ('2027-06-01', '2028-05-31'),  # Adjacent after.
])
async def test_database_allows_identical_and_disjoint_runs_in_another_scope(migrator_session, scope_seed, first_status, start, end):
    ids = await overlap_fixture(migrator_session, scope_seed)
    await add_run(migrator_session, ids, *HELD, status=first_status)
    await clerk_insert(migrator_session, ids, start, end)
    assert await migrator_session.scalar(text('SELECT count(*) FROM settlement_run WHERE lease_agreement_id=:lease'), ids) == 2


async def test_database_ignores_superseded_runs_other_leases_and_runs_without_a_lease(migrator_session, scope_seed):
    ids = await overlap_fixture(migrator_session, scope_seed)
    # A superseded run holds the contested period; its successor lies elsewhere.
    successor = await add_run(migrator_session, ids, '2028-01-01', '2028-12-31')
    await add_run(migrator_session, ids, *HELD, status='finalized', successor=successor)
    await add_run(migrator_session, ids, *HELD, lease='other_lease')
    await clerk_insert(migrator_session, ids, '2026-01-01', '2026-12-31')
    # Annual statements carry no lease: their scoped overlap rules live elsewhere.
    await add_run(migrator_session, ids, '2026-01-01', '2026-12-31', lease=None, scope='weg_scope')
    await add_run(migrator_session, ids, '2026-06-01', '2027-05-31', lease=None, scope='weg_other')
    assert await migrator_session.scalar(text('SELECT count(*) FROM settlement_run WHERE organization_id=:org'), ids) == 6


async def test_drafts_of_one_scope_still_coexist(migrator_session, scope_seed):
    ids = await overlap_fixture(migrator_session, scope_seed)
    await add_run(migrator_session, ids, '2026-07-01', '2026-09-30', scope='second_scope')
    await add_run(migrator_session, ids, '2026-04-01', '2026-08-31', scope='second_scope')
    assert await migrator_session.scalar(text('SELECT count(*) FROM settlement_run WHERE lease_agreement_id=:lease'), ids) == 2


async def test_runs_of_one_scope_are_left_to_the_application_checks(migrator_session, scope_seed):
    # The application refuses a new draft beside a finalized run it can see; the database does not
    # duplicate that, so an archive holding such a state stays restorable in any row order.
    ids = await overlap_fixture(migrator_session, scope_seed)
    await add_run(migrator_session, ids, '2026-07-01', '2026-09-30', scope='second_scope', status='finalized')
    await add_run(migrator_session, ids, '2026-04-01', '2026-08-31', scope='second_scope')
    assert await migrator_session.scalar(text('SELECT count(*) FROM settlement_run WHERE lease_agreement_id=:lease'), ids) == 2


async def test_a_legacy_run_without_a_scope_counts_as_its_own_context(migrator_session, scope_seed):
    ids = await overlap_fixture(migrator_session, scope_seed)
    await add_run(migrator_session, ids, *HELD, scope=None)
    with pytest.raises(DBAPIError) as refused:
        await clerk_insert(migrator_session, ids, '2026-01-01', '2026-12-31')
    assert refused.value.orig.sqlstate == '23P01'
    await clerk_insert(migrator_session, ids, *HELD)


@pytest.mark.parametrize('column,start,end,value', [
    ('period_end', '2026-01-01', '2026-06-30', date(2026, 8, 31)),    # Grows into the held period.
    ('period_start', '2026-10-01', '2026-12-31', date(2026, 8, 1)),   # Grows back into it.
])
async def test_moving_a_run_into_an_overlap_with_another_scope_is_refused(migrator_session, scope_seed, column, start, end, value):
    ids = await overlap_fixture(migrator_session, scope_seed)
    await add_run(migrator_session, ids, '2026-07-01', '2026-09-30')
    moved = await add_run(migrator_session, ids, start, end, scope='second_scope')
    with pytest.raises(DBAPIError) as refused:
        async with migrator_session.begin_nested():
            await migrator_session.execute(text(f'UPDATE settlement_run SET {column}=:value WHERE id=:moved'), {'moved': moved, 'value': value})
    assert refused.value.orig.sqlstate == '23P01'


async def settlement_count(ids):
    from tests.test_scope_payment_reconciliation import read_value
    return await read_value('SELECT count(*) FROM settlement_run WHERE lease_agreement_id=:lease', ids)


async def committed_fixture(session, seed):
    ids = await overlap_fixture(session, seed)
    await add_run(session, ids, *HELD)
    await session.commit()
    return ids


def body(ids, start, end, lease='lease'):
    return {'property_id': str(ids['property']), 'lease_agreement_id': str(ids[lease]),
            'period_start': start, 'period_end': end, 'accounting_scope_id': str(ids['second_scope'])}


@pytest.mark.parametrize('start,end', [('2026-01-01', '2026-12-31'), ('2026-09-01', '2026-12-31')])
async def test_scoped_user_is_refused_an_overlap_hidden_in_another_scope(migrator_session, scope_seed, start, end):
    ids = await committed_fixture(migrator_session, scope_seed)
    async with client(ids) as api:
        answer = await api.post('/api/settlements', json=body(ids, start, end))
        assert answer.status_code == 409, answer.text
        assert answer.json()['detail'] == CONFLICT
        assert (await api.get('/api/settlements', params={'property_id': str(ids['property'])})).json() == []
    assert await settlement_count(ids) == 1


async def test_scoped_user_may_repeat_the_identical_period_of_another_scope(migrator_session, scope_seed):
    ids = await committed_fixture(migrator_session, scope_seed)
    async with client(ids) as api:
        answer = await api.post('/api/settlements', json=body(ids, *HELD))
        assert answer.status_code == 201, answer.text
    assert await settlement_count(ids) == 2


async def test_batch_reports_the_hidden_overlap_per_lease_and_creates_the_others(migrator_session, scope_seed):
    ids = await committed_fixture(migrator_session, scope_seed)
    async with client(ids) as api:
        batch = {key: value for key, value in body(ids, '2026-01-01', '2026-12-31').items() if key != 'lease_agreement_id'}
        answer = await api.post('/api/settlements/batch', json=batch)
        assert answer.status_code == 200, answer.text
        created, errors = answer.json()['created'], answer.json()['errors']
        assert [row['lease_agreement_id'] for row in created] == [str(ids['other_lease'])]
        assert [(row['lease_agreement_id'], row['code'], row['error']) for row in errors] == [
            (str(ids['lease']), CONFLICT['code'], CONFLICT['message'])]
    assert await settlement_count(ids) == 1
