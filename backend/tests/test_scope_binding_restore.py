"""The import restores a scope binding that another user reviewed (issue #716, ADR 0016).

`scope_insert` on `accounting_scope_binding` binds `reviewed_by` of a new binding to the signed-in user. After an import
the archived reviewer of a binding (the clerk whose payment the trigger bound, a second administrator who classified an
account) is an inactive placeholder account, so the restore was refused at `accounting_scope_binding` (sqlstate 42501)
although the importing administrator holds every grant on the scope. The reviewer stays the archived reviewer: the import
names the very binding it restores and its archived reviewer in the transaction-local setting
`app.organization_import_scope_binding`, and the policy has one more way in. That only the import sets the setting is
checked beside the dunning marker, in `test_dunning_context_restore.py`.
"""
import importlib
import json
import uuid

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import settings
from tests.conftest import APP_RW_URL, MIGRATOR_SYNC_URL, MIGRATOR_URL
from tests.test_dunning_context_restore import notice_source
from tests.test_organization_export import export_store as export_store
from tests.test_organization_import import (
    binding_rows, classify_scope, import_target as import_target, mapped_binding, payment_rows,
    rent_statement, source_organization_deleted,
)
from tests.test_rent_changes import api_client, rent_db as rent_db, rent_world as rent_world  # noqa: F401
from tests.test_special_levy import levy_db as levy_db, scope_seed as scope_seed, worker_tenant as worker_tenant  # noqa: F401

MARKER = 'app.organization_import_scope_binding'
PAYMENT_COLUMNS = ('direction', 'amount', 'value_date', 'status', 'payment_type', 'period_year', 'period_month')


async def second_administrator(db, world, confirmed_second_factor):
    """A second administrator of the source organization, who may classify what the first one granted him."""
    ids = {'user': uuid.uuid4(), 'org': world['org']}
    await db.execute(text('INSERT INTO "user"(id,organization_id,email,password_hash,display_name)'
                          " VALUES (:user,:org,:email,'fixture','Second administrator')"),
                     {**ids, 'email': f"{ids['user']}@second-admin.invalid"})
    await db.execute(text("INSERT INTO membership(organization_id,user_id,role) VALUES (:org,:user,'OWNER_ADMIN')"), ids)
    await db.commit()
    if settings.deployment_mode == 'hosted':
        confirmed_second_factor(world['org'], ids['user'])
    return {**world, 'second_admin': ids['user']}


async def grant(api, scope, user, capabilities):
    response = await api.post(f'/api/accounting-scopes/{scope}/grants', headers={'Idempotency-Key': str(uuid.uuid4())},
                              json={'user_id': str(user), 'capabilities': capabilities, 'reason': 'Reviewer of the scope'})
    assert response.status_code == 201, response.text


async def rent_on_a_scoped_account(world, *, booked_by, classified_by):
    """The UI path with two people: one classifies the bank account, another books the rent from one bank movement.

    The administrator creates the scope, the bank account and the statement import. `classified_by` classifies the account
    with the button of the bank account page; `booked_by` assigns the movement as rent plus advance in the matching dialog.
    The database trigger records whoever books as the reviewer of the payment's binding.
    """
    async with api_client(world) as admin:
        scope = await classify_scope(admin, world, world['property'], 'MIET')
        for actor in {booked_by, classified_by} - {'admin'}:
            await grant(admin, scope, world[actor], ['read', 'prepare', 'confirm'])
        bank = await admin.post('/api/bank-accounts', json={
            'property_id': str(world['property']), 'account_type': 'LAUFEND', 'label': 'Scoped rent account'})
        assert bank.status_code == 201, bank.text
        bank = bank.json()['id']
        statement = ('Buchungstag;Wertstellung;Betrag;Name;Verwendungszweck\n'
                     '03.03.2026;03.03.2026;850,00;Synthetic payer;Warmmiete Maerz 2026\n').encode()
        imported = await admin.post(f'/api/bank-accounts/{bank}/imports', params={'dry_run': 'false'},
                                    files={'file': ('rent.csv', statement, 'text/csv')})
        assert imported.status_code == 200, imported.text
        page = await admin.get('/api/bank-transactions', params={'bank_account_id': bank})
        movement, = [row['id'] for row in page.json()['items']]
    async with api_client(world, classified_by) as classifier:
        classified = await classifier.post(f'/api/accounting-scopes/{scope}/bindings', json={
            'resource_type': 'bank_account', 'resource_id': bank, 'reason': 'Konto der Mieten'})
        assert classified.status_code == 201, classified.text
    async with api_client(world, booked_by) as booker:
        assigned = await booker.post(f'/api/bank-transactions/{movement}/assign', json={'lines': [
            {'payment_type': payment_type, 'amount': amount, 'period_year': 2026, 'period_month': 3,
             'lease_agreement_id': str(world['lease']), 'tenant_profile_id': str(world['tenant'])}
            for payment_type, amount in (('KALTMIETE', '700.00'), ('NK_VORAUSZAHLUNG', '150.00'))]})
        assert assigned.status_code == 200, assigned.text
        rent, advance = assigned.json()['payments']
        assert (rent['status'], advance['status']) == ('booked', 'booked')
    return scope, bank, [rent['id'], advance['id']]


@pytest.mark.parametrize('booked_by,classified_by', [
    pytest.param('clerk', 'admin', id='clerk-books-the-payment'),
    pytest.param('admin', 'second_admin', id='second-administrator-classifies-the-account'),
    pytest.param('clerk', 'second_admin', id='clerk-books-on-an-account-the-second-administrator-classified')])
async def test_import_restores_bindings_another_user_reviewed_with_the_archived_reviewer(
    rent_world, import_target, rent_db, export_store, confirmed_second_factor, booked_by, classified_by,
):
    world = await second_administrator(rent_db, rent_world, confirmed_second_factor)
    scope, bank, payments = await rent_on_a_scoped_account(world, booked_by=booked_by, classified_by=classified_by)
    async with api_client(world) as api:
        before = await rent_statement(api, world['lease'], scope)
        assert before['ist_total'] == '850.00'
        archive = await api.post('/api/organizations/export')
        assert archive.status_code == 200, archive.text
    source = await binding_rows(rent_db, world['org'])
    source_payments = await payment_rows(rent_db, payments)
    reviewers = {row['reviewed_by'] for row in source.values()}
    assert str(world[booked_by]) in reviewers and str(world[classified_by]) in reviewers
    assert (reviewers - {str(world['admin'])}) == {str(world[actor]) for actor in {booked_by, classified_by} - {'admin'}}
    await rent_db.rollback()
    await source_organization_deleted(rent_db, world['org'], world['admin'], import_target)
    async with api_client(import_target) as api:
        answer = await api.post('/api/organizations/import', files={'file': ('reviewed-by-others.tar.gz', archive.content)})
        assert answer.status_code == 200, answer.text
        ids = answer.json()['id_map']
        # Every binding is restored as archived, the reviewer being the account the archived reviewer maps to.
        restored = await binding_rows(rent_db, import_target['org'])
        assert restored == {ids['accounting_scope_binding'][identifier]: {**mapped_binding(row, ids),
            'id': ids['accounting_scope_binding'][identifier]} for identifier, row in source.items()}
        # Payments and the account of the lease equal the source to the cent.
        paid = await payment_rows(rent_db, [ids['payment'][identifier] for identifier in payments])
        for identifier in payments:
            assert {k: paid[ids['payment'][identifier]][k] for k in PAYMENT_COLUMNS} == {
                k: source_payments[identifier][k] for k in PAYMENT_COLUMNS}
        assert await rent_statement(api, ids['lease_agreement'][str(world['lease'])], ids['accounting_scope'][scope]) == before
        listed = await api.get('/api/payments', params={'bank_account_id': ids['bank_account'][bank]})
        assert listed.status_code == 200, listed.text
        assert sorted((row['amount'], row['value_date']) for row in listed.json()) == [
            ('150.00', '2026-03-03'), ('700.00', '2026-03-03')]
    # The reviewer stays the archived reviewer, never the importer, and is a placeholder that cannot act.
    for archived in sorted({row['reviewed_by'] for row in source.values()} - {str(world['admin'])}):
        assert ids['user'][archived] != str(import_target['admin'])
        account = (await rent_db.execute(text('SELECT is_active,password_hash FROM "user" WHERE id=:id'),
                                         {'id': ids['user'][archived]})).one()
        assert (account.is_active, account.password_hash) == (False, '!')
    reviewed_by_importer = await rent_db.scalar(text(
        'SELECT count(*) FROM accounting_scope_binding WHERE organization_id=:org AND reviewed_by=:admin'),
        {'org': import_target['org'], 'admin': import_target['admin']})
    assert reviewed_by_importer == sum(1 for row in source.values() if row['reviewed_by'] == str(world['admin']))
    await rent_db.rollback()


# ---- the policy itself: direct writes under the runtime role --------------------------------------

REFUSAL = 'accounting_scope_binding'
INSERT_BINDING = text("""INSERT INTO accounting_scope_binding(id,organization_id,property_id,scope_id,bank_account_id,origin,reason,reviewed_by)
    VALUES (:binding,:org,:property,:scope,:account,:origin,'Restored',:reviewer)""")


async def migrate(statement, **params):
    engine = create_async_engine(MIGRATOR_URL)
    try:
        async with engine.begin() as connection:
            await connection.execute(text(statement), params)
    finally:
        await engine.dispose()


async def add_user(org, *, active, password_hash, role=None):
    user = uuid.uuid4()
    await migrate("INSERT INTO organization(id,name) VALUES (:org,'Probe organization') ON CONFLICT DO NOTHING", org=org)
    await migrate('INSERT INTO "user"(id,organization_id,email,password_hash,display_name,is_active)'
                  " VALUES (:user,:org,:email,:hash,'Probe',:active)",
                  user=user, org=org, email=f'{user}@probe.invalid', hash=password_hash, active=active)
    if role:
        await migrate('INSERT INTO membership(organization_id,user_id,role) VALUES (:org,:user,:role)', org=org, user=user, role=role)
    return user


@pytest.fixture
async def probe(levy_db, scope_seed):
    """A rental scope: the administrator holds every capability, the clerk `read` and `prepare`, and one bank account of
    the property is still unclassified. Beside them the accounts a restore may name as reviewer."""
    ids = await notice_source(levy_db, scope_seed, 'lease')
    ids.update(
        account=uuid.uuid4(), foreign_org=uuid.uuid4(),
        placeholder=await add_user(ids['org'], active=False, password_hash='!'),
        colleague=await add_user(ids['org'], active=True, password_hash='$argon2id$real'),
        deactivated_colleague=await add_user(ids['org'], active=False, password_hash='$argon2id$real'),
        ungranted_admin=await add_user(ids['org'], active=True, password_hash='$argon2id$real', role='OWNER_ADMIN'))
    ids.update(
        foreign_placeholder=await add_user(ids['foreign_org'], active=False, password_hash='!'),
        foreign_admin=await add_user(ids['foreign_org'], active=True, password_hash='$argon2id$real', role='OWNER_ADMIN'))
    await migrate("INSERT INTO bank_account(id,organization_id,property_id,account_type,label) VALUES (:account,:org,:property,'LAUFEND','Unclassified')", **ids)
    return ids


async def insert_binding(ids, *, marker, reviewer, binding=None, user='actor', org=None, origin='REVIEWED'):
    """One INSERT of a binding as the runtime role would run it, in a transaction that is rolled back.

    The transaction-local settings are those of a request (organization, signed-in user) plus the import's marker where
    the case has one. Returns the row count; a refusal is raised by the database.
    """
    binding = binding or uuid.uuid4()
    session_user = ids[user] if isinstance(user, str) else user
    engine = create_async_engine(APP_RW_URL)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                await connection.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:user,true)"),
                                         {'org': str(org or ids['org']), 'user': str(session_user)})
                if marker is not None:
                    value = marker(binding) if callable(marker) else marker
                    await connection.execute(text('SELECT set_config(:name,:value,true)'),
                                             {'name': MARKER, 'value': value if isinstance(value, str) else json.dumps(value)})
                return (await connection.execute(INSERT_BINDING, {**ids, 'binding': binding, 'reviewer': reviewer, 'origin': origin})).rowcount
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


def naming(reviewer, *, binding=None):
    """The marker the import sets: the binding it restores and its archived reviewer."""
    return lambda row: {'binding_id': str(binding or row), 'reviewed_by': str(reviewer)}


async def refused(ids, **kwargs):
    with pytest.raises(DBAPIError) as denied:
        await insert_binding(ids, **kwargs)
    assert denied.value.orig.sqlstate == '42501' and REFUSAL in str(denied.value), denied.value
    return str(denied.value)


async def test_the_policy_accepts_the_restore_of_this_row_for_its_archived_placeholder_reviewer(probe):
    assert await insert_binding(probe, marker=naming(probe['placeholder']), reviewer=probe['placeholder']) == 1


async def test_the_policy_still_wants_the_signed_in_user_as_reviewer_without_the_marker(probe):
    await refused(probe, marker=None, reviewer=probe['placeholder'])
    await refused(probe, marker='', reviewer=probe['placeholder'])
    # The ordinary path is untouched: the signed-in user reviews their own binding, with or without a marker.
    assert await insert_binding(probe, marker=None, reviewer=probe['actor']) == 1
    assert await insert_binding(probe, marker=naming(probe['placeholder']), reviewer=probe['actor']) == 1
    # Neither does a marker make a colleague the reviewer of a binding the signed-in user records.
    await refused(probe, marker=None, reviewer=probe['colleague'])
    await refused(probe, marker=None, reviewer=probe['preparer'])


async def test_the_policy_refuses_a_marker_that_names_another_row(probe):
    await refused(probe, marker=naming(probe['placeholder'], binding=uuid.uuid4()), reviewer=probe['placeholder'])


async def test_the_policy_refuses_a_marker_that_names_another_reviewer(probe):
    other = await add_user(probe['org'], active=False, password_hash='!')
    await refused(probe, marker=naming(other), reviewer=probe['placeholder'])


@pytest.mark.parametrize('account', ['colleague', 'deactivated_colleague', 'foreign_placeholder'])
async def test_the_policy_accepts_only_a_placeholder_of_this_organization(probe, account):
    """An active member, a colleague deactivated in the user list (their password is intact) and an account of another
    organization are no placeholder, whatever the marker says."""
    await refused(probe, marker=naming(probe[account]), reviewer=probe[account])


@pytest.mark.parametrize('marker', [
    pytest.param('{not json', id='malformed'), pytest.param('{"binding_id": ', id='cut-off'), pytest.param('[]', id='array'),
    pytest.param('"text"', id='string'), pytest.param('7', id='number'), pytest.param('null', id='json-null'),
    pytest.param('{}', id='empty-object'), pytest.param('{"binding_id": 7, "reviewed_by": 7}', id='numbers'),
    pytest.param('{"binding_id": "not-a-uuid", "reviewed_by": "not-a-uuid"}', id='not-uuids'),
    pytest.param('{"binding_id": null, "reviewed_by": null}', id='nulls'),
    pytest.param(lambda row: {'binding_id': str(row)}, id='no-reviewer'),
    pytest.param(lambda row: {'reviewed_by': 'PLACEHOLDER'}, id='no-binding')])
async def test_a_missing_or_malformed_marker_is_refused_like_any_other_reviewer_and_never_raises_another_error(probe, marker):
    """The policy answers false for a setting of any shape; the refusal is the policy's 42501, never a cast error."""
    if callable(marker):
        build = marker
        marker = lambda row: json.loads(json.dumps(build(row)).replace('PLACEHOLDER', str(probe['placeholder'])))  # noqa: E731
    await refused(probe, marker=marker, reviewer=probe['placeholder'])


async def test_the_policy_still_wants_the_prepare_capability_and_the_importing_administrators_rights(probe):
    """The marker changes the actor comparison only: the signed-in user is an administrator of the organization with a
    live `prepare` grant on the scope of the binding."""
    marker = naming(probe['placeholder'])
    await refused(probe, marker=marker, reviewer=probe['placeholder'], user='ungranted_admin')
    # The clerk holds `prepare` on the scope, but is no administrator: a marker is not enough for a clerk's session.
    await refused(probe, marker=marker, reviewer=probe['placeholder'], user='preparer')
    await refused(probe, marker=marker, reviewer=probe['placeholder'], user=uuid.uuid4())
    # A session of another organization inserts nothing into this one, nor does a foreign administrator who names it.
    await refused(probe, marker=marker, reviewer=probe['placeholder'], user='foreign_admin', org=probe['foreign_org'])
    await refused(probe, marker=marker, reviewer=probe['placeholder'], user='foreign_admin', org=probe['org'])
    # Nor does a deactivated administrator who held the grant.
    await migrate('UPDATE "user" SET is_active=false WHERE id=:actor', **probe)
    await refused(probe, marker=marker, reviewer=probe['placeholder'])


async def test_the_policy_still_wants_the_reviewed_origin_with_the_marker(probe):
    await refused(probe, marker=naming(probe['placeholder']), reviewer=probe['placeholder'], origin='PROVEN_LEGACY')


async def test_a_binding_stays_immutable_with_the_marker(probe):
    """No UPDATE or DELETE grant of the runtime role, and the evidence trigger refuses the owner of the table too."""
    for statement in ("UPDATE accounting_scope_binding SET reason='Rewritten' WHERE organization_id=:org AND bank_account_id=:bank",
                      'DELETE FROM accounting_scope_binding WHERE organization_id=:org AND bank_account_id=:bank'):
        engine = create_async_engine(APP_RW_URL)
        try:
            with pytest.raises(DBAPIError) as denied:
                async with engine.begin() as connection:
                    await connection.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:user,true)"),
                                             {'org': str(probe['org']), 'user': str(probe['actor'])})
                    await connection.execute(text('SELECT set_config(:name,:value,true)'),
                                             {'name': MARKER, 'value': json.dumps({'binding_id': str(uuid.uuid4()), 'reviewed_by': str(probe['placeholder'])})})
                    await connection.execute(text(statement), {'org': probe['org'], 'bank': probe['bank']})
            assert denied.value.orig.sqlstate == '42501', denied.value
        finally:
            await engine.dispose()
        owner = create_async_engine(MIGRATOR_URL)
        try:
            with pytest.raises(DBAPIError) as denied:
                async with owner.begin() as connection:
                    await connection.execute(text(statement), {'org': probe['org'], 'bank': probe['bank']})
            assert denied.value.orig.sqlstate == '23514' and 'immutable' in str(denied.value), denied.value
        finally:
            await owner.dispose()


# ---- the migration ---------------------------------------------------------------------------------

POLICY_OF_0032 = (
    "((organization_id = (NULLIF(current_setting('app.current_org_id'::text, true), ''::text))::uuid) "
    "AND has_scope_capability(organization_id, scope_id, 'prepare'::text) AND ((origin)::text = 'REVIEWED'::text) "
    "AND (reviewed_by = (NULLIF(current_setting('app.current_user_id'::text, true), ''::text))::uuid))")


def test_0200_adds_one_alternative_to_scope_insert_and_its_downgrade_restores_the_policy_of_0032():
    migration = importlib.import_module('migrations.versions.0200_import_restores_scope_reviewer')
    policies = text("""SELECT polname,polcmd::text,pg_get_expr(polwithcheck,polrelid),pg_get_expr(polqual,polrelid),polroles::regrole[]::text
        FROM pg_policy WHERE polrelid='accounting_scope_binding'::regclass ORDER BY 1""")
    functions = text("""SELECT p.oid::regprocedure::text,pg_get_functiondef(p.oid),p.proacl::text,p.proconfig,p.proowner,p.prosecdef
        FROM pg_proc p WHERE p.pronamespace='public'::regnamespace ORDER BY 1""")
    guard = 'import_restores_scope_binding(uuid,uuid,uuid)'
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
            savepoint = db.begin_nested()
            try:
                head = {row[0]: tuple(row[1:]) for row in db.execute(functions)}
                head_policies = {row[0]: tuple(row[1:]) for row in db.execute(policies)}
                migration.downgrade()
                older = {row[0]: tuple(row[1:]) for row in db.execute(functions)}
                older_policies = {row[0]: tuple(row[1:]) for row in db.execute(policies)}
                # Only the function goes with it, and only the insert policy changes.
                assert set(head) - set(older) == {guard} and not set(older) - set(head)
                assert all(head[name] == older[name] for name in older)
                assert sorted(head_policies) == sorted(older_policies) == ['scope_insert', 'scope_select']
                assert head_policies['scope_select'] == older_policies['scope_select']
                assert older_policies['scope_insert'][1] == POLICY_OF_0032
                assert older_policies['scope_insert'][0] == head_policies['scope_insert'][0] == 'a'
                # Head: the same conditions with one more way for the reviewer; nothing else is let in.
                assert 'import_restores_scope_binding(id, organization_id, reviewed_by)' in head_policies['scope_insert'][1]
                assert head_policies['scope_insert'][1].count(' OR ') == 1
                assert head_policies['scope_insert'][1].replace(
                    ' OR import_restores_scope_binding(id, organization_id, reviewed_by)', '').replace(
                    '((reviewed_by = (NULLIF(current_setting(\'app.current_user_id\'::text, true), \'\'::text))::uuid))',
                    '(reviewed_by = (NULLIF(current_setting(\'app.current_user_id\'::text, true), \'\'::text))::uuid)') == POLICY_OF_0032
                # Definer rights with a fixed search path, executable by the runtime role only.
                definition, acl, config, owner, definer = head[guard]
                assert definer is True and config == ['search_path=pg_catalog, pg_temp']
                assert sorted(acl.strip('{}').split(',')) == ['app_migrator=X/app_migrator', 'app_rw=X/app_migrator']
                migration.upgrade()
                assert {row[0]: tuple(row[1:]) for row in db.execute(functions)} == head
                assert {row[0]: tuple(row[1:]) for row in db.execute(policies)} == head_policies
            finally:
                savepoint.rollback()
    finally:
        engine.dispose()

