"""The level of a tenant notice counts the demands of a lease across its accounting scopes (#818, 0203).

Amounts stay with the scope; only the number of earlier demands is read across scopes, and only as a number.
"""
import importlib
import json
import uuid

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import APP_RW_URL, MIGRATOR_SYNC_URL
from tests.test_financial_scope_boundary import worker_tenant as worker_tenant, scope_seed as scope_seed
from tests.test_lease_payments_outside_scope import foreign_lease
from tests.test_m7_dunning import _key, _pdf_text, _stored
from tests.test_scoped_account_statements import client, mutate, statement_fixture

pytestmark = pytest.mark.asyncio

SIGNATURE = 'lease_item_demand_count(uuid,uuid,jsonb)'
PREPARE_AND_ISSUE = ['read', 'prepare', 'approve', 'export']
REMINDER, DUNNING = (1, '0.00'), (2, '2.50')  # level and fee of the default fee table
FULL_YEAR = ('2025-01-01', '2025-12-31')


async def give(ids, user, scope, capabilities):
    """The user's one live grant for the scope; none when `capabilities` is empty."""
    params = {**ids, 'holder': ids[user], 'sphere': ids[scope], 'caps': capabilities}
    await mutate(text('UPDATE scope_grant SET revoked_at=now(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:sphere AND user_id=:holder AND revoked_at IS NULL'), params)
    if capabilities:
        await mutate(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES (:org,:sphere,:holder,:caps,:actor,'Dunning level test grant')"), params)


async def give_second_scope_an_account(ids):
    """A notice is issued to the one active current account with an IBAN that its scope lets the caller read."""
    ids['payee'] = uuid.uuid4()
    await mutate(text("INSERT INTO bank_account(id,organization_id,property_id,account_type,label,iban) VALUES (:payee,:org,:property,'LAUFEND','Payee account','DE89370400440532013000')"), ids)
    await mutate(text("INSERT INTO accounting_scope_binding(organization_id,property_id,scope_id,bank_account_id,origin,reason,reviewed_by) VALUES (:org,:property,:second_scope,:payee,'REVIEWED','Level test account',:actor)"), ids)


async def prepare(api, ids, scope='scope', *, lease='lease', period=FULL_YEAR, reference='2026-01-15'):
    response = await api.post('/api/dunning/tenant', json={
        'tenant_profile_id': str(ids['profile']), 'lease_agreement_id': str(ids[lease]),
        'accounting_scope_id': str(ids[scope]), 'period_start': period[0], 'period_end': period[1],
        'reference_date': reference})
    assert response.status_code == 201, response.text
    return response.json()


async def issue(api, notice):
    response = await api.post(f"/api/dunning/{notice['id']}/issue")
    assert response.status_code == 200, response.text
    return response.json()


def level(notice):
    return notice['level'], notice['fee_amount']


@pytest.fixture
async def two_scopes(migrator_session, scope_seed):
    """One lease (rent 500 and advance 100 a month) that two rental scopes of 2025 cover; the clerk prepares and issues in both."""
    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    for scope in ('scope', 'second_scope'):
        await give(ids, 'clerk', scope, PREPARE_AND_ISSUE)
    await give_second_scope_an_account(ids)
    return ids


# --- the level of a notice -----------------------------------------------------------------------------------------

async def test_the_second_scopes_notice_for_months_the_first_scope_demanded_is_level_two_with_its_fee(two_scopes):
    ids = two_scopes
    async with client(ids) as api:
        first = await issue(api, await prepare(api, ids, 'scope'))
        assert level(first) == REMINDER
        second = await prepare(api, ids, 'second_scope', reference='2026-02-10')
        # The debt belongs to the lease, so both scopes owe the same amount; only the level and its fee differ.
        assert level(second) == DUNNING
        # 12 x 600.00 owed - 1000.00 paid for June's rent (100.00 + 900.00 into the two scopes) = 6200.00.
        assert (first['principal_amount'], first['total_amount']) == ('6200.00', '6200.00')
        assert (second['principal_amount'], second['total_amount']) == ('6200.00', '6202.50')
        assert second['accounting_scope_id'] == str(ids['second_scope'])
        # The other way round: the first scope escalates over the notice of the second one once that is issued.
        assert level(await prepare(api, ids, 'scope', reference='2026-02-11')) == DUNNING
        await issue(api, second)
        assert level(await prepare(api, ids, 'scope', reference='2026-02-12')) == (3, '2.50')


async def test_a_notice_that_is_not_issued_or_was_discarded_does_not_raise_the_level_of_the_other_scope(two_scopes):
    ids = two_scopes
    async with client(ids) as api:
        draft = await prepare(api, ids, 'scope')
        assert level(await prepare(api, ids, 'second_scope')) == REMINDER
        assert (await api.delete(f"/api/dunning/{draft['id']}")).status_code == 204
        assert (await api.get(f"/api/dunning/{draft['id']}")).json()['status'] == 'cancelled'
        assert level(await prepare(api, ids, 'second_scope')) == REMINDER
        await issue(api, await prepare(api, ids, 'scope'))
        assert level(await prepare(api, ids, 'second_scope')) == DUNNING


async def test_the_count_is_per_open_item_across_scopes(two_scopes):
    ids = two_scopes
    async with client(ids) as api:
        # May and June 2025: June's rent was paid twice over (1000.00 for 500.00 owed); the 500.00 settle the oldest
        # position, May's rent (#806). The notice demands the advance of May and the advance of June.
        await issue(api, await prepare(api, ids, 'scope', period=('2025-05-01', '2025-06-30')))
        for period, expected in {
            ('2025-07-01', '2025-07-31'): REMINDER,  # a month the first scope never demanded
            ('2025-07-01', '2025-08-31'): REMINDER,
            ('2025-05-01', '2025-05-31'): DUNNING,  # the advance of May was demanded once; its rent never
            ('2025-04-01', '2025-05-31'): DUNNING,  # the whole notice takes the highest count of its items
            ('2025-03-01', '2025-04-30'): REMINDER,
            # June's rent settles June's advance (100.00) and 400.00 of July's rent: only July's positions are
            # open, and no earlier notice demanded them.
            ('2025-06-01', '2025-07-31'): REMINDER,
        }.items():
            assert level(await prepare(api, ids, 'second_scope', period=period)) == expected, period
        # June alone owes nothing: the surplus of its rent settles its advance, so there is no notice to make.
        refused = await api.post('/api/dunning/tenant', json={
            'tenant_profile_id': str(ids['profile']), 'lease_agreement_id': str(ids['lease']),
            'accounting_scope_id': str(ids['second_scope']), 'period_start': '2025-06-01',
            'period_end': '2025-06-30', 'reference_date': '2026-01-15'})
        assert (refused.status_code, refused.json()['detail']) == (
            422, 'No overdue items found for this tenant in the given period')


async def test_a_demand_for_another_lease_of_the_same_tenant_raises_nothing(two_scopes):
    ids = two_scopes
    async with client(ids) as api:
        # The same tenant, the same months, another lease that only the whole-property scope covers.
        other = await issue(api, await prepare(api, ids, 'second_scope', lease='excluded_lease'))
        assert (other['principal_amount'], level(other)) == ('9600.00', REMINDER)
        assert level(await prepare(api, ids, 'scope')) == REMINDER
        assert level(await prepare(api, ids, 'second_scope')) == REMINDER
        assert level(await prepare(api, ids, 'second_scope', lease='excluded_lease')) == DUNNING


async def test_a_notice_of_the_selected_scope_is_counted_once(two_scopes):
    ids = two_scopes
    async with client(ids) as api:
        await issue(api, await prepare(api, ids, 'scope'))
        # One earlier demand makes the second notice level 2, not 3, although this scope can read that notice itself.
        assert level(await prepare(api, ids, 'scope', reference='2026-02-10')) == DUNNING
        # And the other scope's later notice joins the same count: two demands, level 3.
        await issue(api, await prepare(api, ids, 'second_scope', reference='2026-02-11'))
        assert level(await prepare(api, ids, 'scope', reference='2026-02-12')) == (3, '2.50')


# --- source protection ---------------------------------------------------------------------------------------------

async def test_a_member_of_one_scope_sees_nothing_of_the_other_scopes_notice_although_its_level_counts_it(two_scopes):
    ids = two_scopes
    await give(ids, 'actor', 'scope', PREPARE_AND_ISSUE)
    await give(ids, 'clerk', 'scope', [])
    await give(ids, 'clerk', 'other_scope', [])
    async with client(ids, 'actor') as owner_of_first:
        first = await issue(owner_of_first, await prepare(owner_of_first, ids, 'scope', reference='2026-01-15'))
    async with client(ids) as api:
        second = await issue(api, await prepare(api, ids, 'second_scope', reference='2026-02-10'))
        assert level(second) == DUNNING
        # Nothing of the first notice reaches the second scope's member: no id, date, scope or text.
        traces = [first['id'], str(ids['scope']), 'Bounded report scope', '2026-01-15', '2026-01-29', '15.01.2026', '29.01.2026']
        detail = await api.get(f"/api/dunning/{second['id']}")
        listing = await api.get('/api/dunning')
        statement = await api.get(f"/api/payments/mietkonto/lease/{ids['lease']}", params={
            'period_start': FULL_YEAR[0], 'period_end': FULL_YEAR[1], 'accounting_scope_id': str(ids['second_scope'])})
        pdf = await api.get(f"/api/dunning/{second['id']}/pdf")
        assert [answer.status_code for answer in (detail, listing, statement, pdf)] == [200, 200, 200, 200]
        for name, text_of in {'detail': detail.text, 'list': listing.text, 'statement': statement.text, 'pdf': _pdf_text(pdf.content)}.items():
            assert [trace for trace in traces if trace in text_of] == [], name
        assert [row['id'] for row in listing.json()] == [second['id']]
        assert [row['level'] for row in statement.json()['dunning_notices']] == [2]
        for suffix in ('', '/pdf'):
            assert (await api.get(f"/api/dunning/{first['id']}{suffix}")).status_code == 404
    # And the member of the first scope reads nothing of the second one's notice.
    async with client(ids, 'actor') as owner_of_first:
        assert [row['id'] for row in (await owner_of_first.get('/api/dunning')).json()] == [first['id']]
        for suffix in ('', '/pdf'):
            assert (await owner_of_first.get(f"/api/dunning/{second['id']}{suffix}")).status_code == 404


# --- the database function -----------------------------------------------------------------------------------------

async def demand(ids, scope, items, *, lease='lease', status='issued', cancelled=False):
    """A notice of the scope for the lease with the frozen items, as the preparer made it; written as the owner of the tables."""
    params = {**ids, 'notice': uuid.uuid4(), 'sphere': ids[scope], 'lease_id': ids[lease], 'items': json.dumps(items),
              'status': status, 'cancelled': cancelled}
    await mutate(text("""WITH n AS (INSERT INTO dunning_notice(id,organization_id,tenant_profile_id,level,issue_date,due_date,
          principal_amount,fee_amount,total_amount,included_items,status,issued_at,issued_by,cancelled_at)
        VALUES (CAST(:notice AS uuid),CAST(:org AS uuid),CAST(:profile AS uuid),1,'2026-01-15','2026-01-29',1,0,1,
          CAST(:items AS jsonb),CAST(:status AS text),
          CASE WHEN CAST(:status AS text)='issued' THEN now() END,
          CASE WHEN CAST(:status AS text)='issued' THEN CAST(:actor AS uuid) END,
          CASE WHEN CAST(:cancelled AS boolean) THEN now() END) RETURNING id)
      INSERT INTO dunning_scope_context(organization_id,dunning_notice_id,scope_id,lease_agreement_id,period_start,period_end,
          created_by,reason)
      SELECT CAST(:org AS uuid),id,CAST(:sphere AS uuid),CAST(:lease_id AS uuid),DATE '2025-01-01',DATE '2025-12-31',
          CAST(:actor AS uuid),'Level test notice' FROM n"""), params)


def june(payment_type='KALTMIETE', due=None):
    return [_stored(payment_type, 6, due, year=2025)]


async def counted(ids, *keys, lease=None, scope=None, user='clerk', org=None, session_org=None):
    """What the function answers the runtime role for a member of the organization with `scope` selected."""
    from app.repositories.dunning_notice_repo import _item_vars

    engine = create_async_engine(APP_RW_URL)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:user,true),"
                                          "set_config('app.current_scope_id',:scope,true)"),
                                     {'org': str(session_org or ids['org']), 'user': str(ids[user]), 'scope': str(scope) if scope else ''})
            return await connection.scalar(text('SELECT lease_item_demand_count(:org,:lease,CAST(:keys AS jsonb))'), {
                'org': org or ids['org'], 'lease': lease or ids['lease'], 'keys': json.dumps([_item_vars(key) for key in keys])})
    finally:
        await engine.dispose()


async def test_the_function_counts_issued_notices_of_every_scope_of_the_lease_by_item(migrator_session, scope_seed):
    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    kalt, nebenkosten = _key('KALTMIETE', 6, year=2025), _key('NK_VORAUSZAHLUNG', 6, year=2025)
    await demand(ids, 'scope', june() + june('NK_VORAUSZAHLUNG'))
    await demand(ids, 'second_scope', june())
    await demand(ids, 'second_scope', june(), status='draft')
    await demand(ids, 'second_scope', june(), status='draft', cancelled=True)
    # An issued notice of the same tenant for another lease of the same scope counts for that lease only.
    await demand(ids, 'second_scope', june(), lease='excluded_lease')
    for scope in (ids['scope'], ids['second_scope']):
        assert await counted(ids, kalt, scope=scope) == 2
        assert await counted(ids, nebenkosten, scope=scope) == 1
        assert await counted(ids, nebenkosten, kalt, scope=scope) == 2
        assert await counted(ids, _key('KALTMIETE', 7, year=2025), scope=scope) == 0
        assert await counted(ids, scope=scope) == 0
        assert await counted(ids, kalt, lease=ids['excluded_lease'], scope=ids['second_scope']) == 1


async def test_the_function_matches_an_item_like_the_repository_count_of_the_unscoped_path(migrator_session, scope_seed):
    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    # The cases of test_m7_dunning.py (a levy due date, an old item without one, the joint advance), one notice each.
    for items in (
        [_stored('SONDERZAHLUNG', 6, '2026-06-05')],
        [_stored('SONDERZAHLUNG', 6, '2026-06-05')],
        [_stored('SONDERZAHLUNG', 6, '2026-06-25'), _stored('HAUSGELD_VORSCHUSS', 6, '2026-06-03')],
        [_stored('SONDERZAHLUNG', 7)],
        [_stored('SONDERZAHLUNG', None, '2026-01-01', year=0)],
        [_stored('KALTMIETE', 1, '2026-01-06')],
        [_stored('NK_VORAUSZAHLUNG', 4, '2026-04-07', combined_advance=True, soll='240.00')],
        [_stored('NK_VORAUSZAHLUNG', 5, '2026-05-07')],
    ):
        await demand(ids, 'scope', items)
    scope = ids['second_scope']

    async def count(*keys):
        return await counted(ids, *keys, scope=scope)

    assert await count(_key('SONDERZAHLUNG', 6, '2026-06-05')) == 2
    assert await count(_key('SONDERZAHLUNG', 6, '2026-06-25')) == 1
    assert await count(_key('SONDERZAHLUNG', 6, '2026-06-15')) == 0
    assert await count(_key('SONDERZAHLUNG', 6, '2026-06-05'), _key('SONDERZAHLUNG', 6, '2026-06-25')) == 2
    assert await count(_key('SONDERZAHLUNG', 6)) == 3
    assert await count(_key('SONDERZAHLUNG', 7, '2026-07-10')) == 1
    assert await count(_key('SONDERZAHLUNG', 7, '2026-07-20')) == 1
    assert await count(_key('SONDERZAHLUNG', None, year=0)) == 1
    assert await count(_key('SONDERZAHLUNG', None, year=0), _key('SONDERZAHLUNG', 7, '2026-07-10')) == 1
    assert await count(_key('HAUSGELD_VORSCHUSS', 6, '2026-06-03')) == 1
    assert await count(_key('HAUSGELD_VORSCHUSS', 6, '2026-06-04')) == 1
    assert await count(_key('KALTMIETE', 1, '2026-01-07')) == 1
    assert await count(_key('KALTMIETE', 6, '2026-06-03')) == 0
    assert await count(_key('HK_VORAUSZAHLUNG', 4, '2026-04-07')) == 1
    assert await count(_key('NK_VORAUSZAHLUNG', 4, '2026-04-07')) == 1
    assert await count(_key('HK_VORAUSZAHLUNG', 5, '2026-05-07')) == 0
    assert await count(_key('NK_VORAUSZAHLUNG', 5, '2026-05-07')) == 1


async def test_the_repository_reads_every_scope_of_a_lease_in_one_call_whatever_the_number_of_items(migrator_session, scope_seed):
    from app.core.db import get_tenant_session
    from app.repositories.dunning_notice_repo import DunningNoticeRepository

    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    await demand(ids, 'scope', june())
    await demand(ids, 'scope', june())
    keys = [_key('KALTMIETE', month, year=year) for year in range(1990, 2100) for month in range(1, 13)]
    assert len(keys) > 1000
    async with get_tenant_session(ids['org']) as db:
        await db.execute(text("SELECT set_config('app.current_user_id',:user,true),set_config('app.current_scope_id',:scope,true)"),
                         {'user': str(ids['clerk']), 'scope': str(ids['second_scope'])})
        repository = DunningNoticeRepository(db, ids['org'])
        arguments = {'tenant_profile_id': ids['profile'], 'lease_id': ids['lease'], 'scope_id': ids['second_scope']}
        assert await repository.highest_item_demand_count(item_keys=keys, **arguments) == 2
        assert await repository.highest_item_demand_count(item_keys=keys[:12], **arguments) == 0
        assert await repository.highest_item_demand_count(item_keys=[], **arguments) == 0


async def test_the_function_answers_nothing_where_the_statement_itself_is_not_admitted(migrator_session, scope_seed, seed_data):
    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    await demand(ids, 'scope', june())
    await demand(ids, 'second_scope', june(), lease='excluded_lease')
    foreign = await foreign_lease(ids, seed_data['org_b_id'])
    key = _key('KALTMIETE', 6, year=2025)
    assert await counted(ids, key, scope=ids['scope']) == 1
    # The whole-property scope covers the other lease; the unit-bound one does not.
    assert await counted(ids, key, scope=ids['second_scope'], lease=ids['excluded_lease']) == 1
    for name, arguments in {
        'a lease outside the selected scope': {'scope': ids['scope'], 'lease': ids['excluded_lease']},
        'an unknown id': {'scope': ids['scope'], 'lease': uuid.uuid4()},
        'a lease of another organization': {'scope': ids['scope'], 'lease': foreign['lease']},
        'another organization named by the caller': {'scope': ids['scope'], 'org': seed_data['org_b_id']},
        'a member without a grant for the scope': {'scope': ids['scope'], 'user': 'actor'},
        'no scope while the property is classified': {},
    }.items():
        assert await counted(ids, key, **arguments) == 0, name
    await mutate(text('UPDATE scope_grant SET revoked_at=now(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:scope AND user_id=:clerk AND revoked_at IS NULL'), ids)
    assert await counted(ids, key, scope=ids['scope']) == 0


async def test_a_member_of_another_organization_counts_nothing_of_this_one(migrator_session, scope_seed, seed_data):
    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    await demand(ids, 'scope', june())
    ids['user_b'] = seed_data['user_b_admin_id']
    key = _key('KALTMIETE', 6, year=2025)
    assert await counted(ids, key, scope=ids['scope'], user='user_b', session_org=seed_data['org_b_id'], org=seed_data['org_b_id']) == 0
    assert await counted(ids, key, scope=ids['scope'], user='user_b', session_org=seed_data['org_b_id']) == 0


async def test_it_has_definer_rights_a_fixed_search_path_and_executes_for_the_runtime_role_only(migrator_session):
    row = (await migrator_session.execute(text("""SELECT p.prosecdef, p.provolatile::text, p.proconfig, l.lanname, p.proretset,
        pg_get_userbyid(p.proowner), (SELECT array_agg(CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END ORDER BY a.grantee)
          FROM aclexplode(p.proacl) a WHERE a.privilege_type='EXECUTE'), pg_get_function_result(p.oid)
        FROM pg_proc p JOIN pg_language l ON l.oid=p.prolang WHERE p.oid=CAST(:signature AS regprocedure)"""), {'signature': SIGNATURE})).one()
    # One number and nothing else: no row, so no notice id, scope, date or text can come back.
    assert tuple(row[:5]) == (True, 'v', ['search_path=pg_catalog, pg_temp', 'row_security=off'], 'plpgsql', False)
    assert row[7] == 'integer'
    assert sorted(row[6]) == sorted([row[5], 'app_rw']), row
    assert await migrator_session.scalar(text("SELECT has_function_privilege('app_rw',CAST(:signature AS regprocedure),'EXECUTE')"), {'signature': SIGNATURE})


def test_0203_adds_and_drops_only_the_function():
    migration = importlib.import_module('migrations.versions.0203_dunning_level_across_scopes')
    functions = text("""SELECT p.oid::regprocedure::text,pg_get_functiondef(p.oid),p.proacl::text,p.proconfig,p.proowner
        FROM pg_proc p WHERE p.pronamespace='public'::regnamespace ORDER BY 1""")
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
            savepoint = db.begin_nested()
            try:
                head = {row[0]: tuple(row[1:]) for row in db.execute(functions)}
                assert SIGNATURE in head
                migration.downgrade()
                assert {row[0]: tuple(row[1:]) for row in db.execute(functions)} == {key: value for key, value in head.items() if key != SIGNATURE}
                migration.upgrade()
                assert {row[0]: tuple(row[1:]) for row in db.execute(functions)} == head
            finally:
                savepoint.rollback()
    finally:
        engine.dispose()
