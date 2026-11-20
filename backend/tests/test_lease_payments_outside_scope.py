"""The payments of a lease that the selected scope cannot read reach a statement only as amounts (#803, 0194)."""
import importlib
import uuid
from datetime import date
from decimal import Decimal

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import APP_RW_URL, MIGRATOR_SYNC_URL
from tests.test_financial_scope_boundary import worker_tenant as worker_tenant, scope_seed as scope_seed
from tests.test_scoped_account_statements import add_payments, fetch, mutate, statement_fixture

pytestmark = pytest.mark.asyncio

COLUMNS = ('payment_type', 'direction', 'amount', 'period_year', 'period_month', 'value_date', 'booked_at')
SIGNATURE = 'lease_payments_outside_scope(uuid,uuid,uuid[])'


async def outside(ids, *, lease=None, scope=None, user='clerk', org=None, session_org=None, listed=()):
    """The function as the runtime role for a member of the organization, with `scope` selected; (columns, rows)."""
    engine = create_async_engine(APP_RW_URL)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:user,true),"
                                          "set_config('app.current_scope_id',:scope,true)"),
                                     {'org': str(session_org or ids['org']), 'user': str(ids[user]), 'scope': str(scope) if scope else ''})
            answer = await connection.execute(text(f'SELECT * FROM {SIGNATURE.split("(")[0]}(:org,:lease,CAST(:listed AS uuid[]))'),
                                              {'org': org or ids['org'], 'lease': lease or ids['lease'], 'listed': list(listed)})
            return tuple(answer.keys()), [tuple(row) for row in answer]
    finally:
        await engine.dispose()


async def test_it_returns_only_the_calculation_columns_of_the_payments_the_caller_did_not_list(migrator_session, scope_seed):
    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    listed = await fetch('SELECT id FROM payment WHERE bank_account_id=:bank', ids)
    columns, rows = await outside(ids, scope=ids['scope'], listed=[listed])
    # No id, no account, no counterparty, no text: the amount, its direction and what the calculation allocates by.
    assert columns == COLUMNS
    assert rows == [('KALTMIETE', 'IN', Decimal('900.00'), 2025, 6, date(2025, 6, 1), None)]
    # Listing nothing asks for every payment of the lease that a statement counts.
    _, everything = await outside(ids, scope=ids['scope'])
    assert sorted(row[2] for row in everything) == [Decimal('100.00'), Decimal('900.00')]


async def test_it_leaves_out_what_a_statement_does_not_count(migrator_session, scope_seed):
    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    original, storno = uuid.uuid4(), uuid.uuid4()
    await add_payments(
        ids,
        {'amount': '777.00', 'status': 'draft'},
        {'id': original, 'amount': '333.00', 'reversed_by': storno},
        {'id': storno, 'amount': '333.00', 'direction': 'OUT', 'reverses': original},
    )
    _, rows = await outside(ids, scope=ids['scope'])
    assert sorted(row[2] for row in rows) == [Decimal('100.00'), Decimal('900.00')]


async def foreign_lease(ids, org):
    """A lease of another organization with one booked payment, and the ids to read it with."""
    foreign = {key: uuid.uuid4() for key in ('property', 'unit', 'person', 'tenant', 'lease', 'bank')}
    for statement in (
        "INSERT INTO property(id,organization_id,name,street,zip_code,city,management_type) VALUES (:property,:org,'Foreign','','','','MIET')",
        "INSERT INTO unit(id,organization_id,property_id,number,usage_type) VALUES (:unit,:org,:property,'F1','WOHNUNG')",
        "INSERT INTO person(id,organization_id,first_name,last_name,street,zip_code,city) VALUES (:person,:org,'Foreign','Tenant','','','')",
        "INSERT INTO tenant_profile(id,organization_id,person_id,notes) VALUES (:tenant,:org,:person,'')",
        "INSERT INTO lease_agreement(id,organization_id,unit_id,tenant_profile_id,valid_from,base_rent,advance_payment,notes) VALUES (:lease,:org,:unit,:tenant,'2024-01-01',500,100,'')",
        "INSERT INTO bank_account(id,organization_id,property_id,account_type,label) VALUES (:bank,:org,:property,'LAUFEND','Foreign account')",
        """INSERT INTO payment(organization_id,property_id,bank_account_id,amount,direction,value_date,payment_type,tenant_profile_id,lease_agreement_id,period_year,period_month,status)
           VALUES (:org,:property,:bank,50,'IN','2025-06-01','KALTMIETE',:tenant,:lease,2025,6,'booked')""",
    ):
        await mutate(text(statement), {**foreign, 'org': org})
    return foreign


async def test_it_answers_nothing_for_a_lease_the_caller_may_not_open(migrator_session, scope_seed, seed_data):
    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    # A payment of the lease's tenant for the lease whose unit the first scope excludes, booked in the second scope.
    await add_payments(ids, {'amount': '555.00', 'lease': ids['excluded_lease']})
    foreign = await foreign_lease(ids, seed_data['org_b_id'])
    assert (await outside(ids, scope=ids['scope']))[1]
    # The second scope covers the whole property: the same lease question has an answer there.
    assert (await outside(ids, scope=ids['second_scope'], lease=ids['excluded_lease']))[1]
    for name, arguments in {
        'a lease outside the selected scope': {'scope': ids['scope'], 'lease': ids['excluded_lease']},
        'an unknown id': {'scope': ids['scope'], 'lease': uuid.uuid4()},
        'a lease of another organization': {'scope': ids['scope'], 'lease': foreign['lease']},
        'another organization named by the caller': {'scope': ids['scope'], 'org': seed_data['org_b_id']},
        'a member without a grant for the scope': {'scope': ids['scope'], 'user': 'actor'},
        'no scope while the property is classified': {},
    }.items():
        assert (await outside(ids, **arguments))[1] == [], name
    # The grant is checked live: revoked, the scope answers nothing.
    await mutate(text('UPDATE scope_grant SET revoked_at=now(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:scope AND user_id=:clerk AND revoked_at IS NULL'), ids)
    assert (await outside(ids, scope=ids['scope']))[1] == []


async def test_a_member_of_another_organization_reads_nothing_of_this_one(migrator_session, scope_seed, seed_data):
    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    ids['user_b'] = seed_data['user_b_admin_id']
    _, rows = await outside(ids, scope=ids['scope'], user='user_b', session_org=seed_data['org_b_id'], org=seed_data['org_b_id'])
    assert rows == []
    _, rows = await outside(ids, scope=ids['scope'], user='user_b', session_org=seed_data['org_b_id'])
    assert rows == []


async def test_it_has_definer_rights_a_fixed_search_path_and_executes_for_the_runtime_role_only(migrator_session):
    row = (await migrator_session.execute(text("""SELECT p.prosecdef, p.provolatile::text, p.proconfig, l.lanname, p.proretset,
        pg_get_userbyid(p.proowner), (SELECT array_agg(CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END ORDER BY a.grantee)
          FROM aclexplode(p.proacl) a WHERE a.privilege_type='EXECUTE')
        FROM pg_proc p JOIN pg_language l ON l.oid=p.prolang WHERE p.oid=CAST(:signature AS regprocedure)"""), {'signature': SIGNATURE})).one()
    assert tuple(row[:5]) == (True, 'v', ['search_path=pg_catalog, pg_temp', 'row_security=off'], 'plpgsql', True)
    assert sorted(row[6]) == sorted([row[5], 'app_rw']), row
    assert await migrator_session.scalar(text("SELECT has_function_privilege('app_rw',CAST(:signature AS regprocedure),'EXECUTE')"), {'signature': SIGNATURE})


def test_0194_adds_and_drops_only_the_function():
    migration = importlib.import_module('migrations.versions.0194_lease_payments_outside_scope')
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
