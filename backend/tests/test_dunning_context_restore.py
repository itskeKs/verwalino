"""The import restores a dunning notice that another user prepared (issue #694, ADR 0016).

`protect_dunning_context` binds the preparer of a new context to the signed-in user. After an import the archived
preparer is an inactive placeholder account, so the restore of a notice a clerk (or a second administrator) prepared
was refused at `dunning_scope_context` (sqlstate 42501). The guard now has one more way in: the import names the very
row it restores and its archived preparer in the transaction-local setting `app.organization_import_dunning_context`.
"""
import json
import importlib
import re
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import settings
from app.engine.canonical import canonical_sha256
from tests.conftest import APP_RW_URL, MIGRATOR_SYNC_URL, MIGRATOR_URL
from tests.test_organization_import import import_target as import_target, in_target_ids, source_organization_deleted
from tests.test_organization_export import export_store as export_store
from tests.test_rent_changes import api_client, rent_db as rent_db, rent_world as rent_world  # noqa: F401
from tests.test_special_levy import levy_db as levy_db, scope_seed as scope_seed, worker_tenant as worker_tenant, client as levy_client  # noqa: F401

MARKER = 'app.organization_import_dunning_context'
ALL_CAPABILITIES = ['read', 'prepare', 'approve', 'confirm', 'export']
WINDOW = {'period_start': '2026-03-01', 'period_end': '2026-03-31'}
NOTICE_KINDS = ('owner_plan', 'lease')


async def notice_source(db, seed, kind, preparer='clerk', confirmed_second_factor=None):
    """One scope with everything a scoped notice needs, nothing prepared yet.

    The administrator of the organization holds every capability of the scope; the preparer holds `read` and
    `prepare`: the preparer is the organization's clerk or a second administrator.
    """
    ids = {key: uuid.uuid4() for key in ('property', 'scope', 'unit', 'person', 'profile', 'lease', 'plan', 'line', 'bank')}
    ids.update(org=seed['org_a_id'], actor=seed['user_a_admin_id'], kind=kind)
    ids['preparer'] = seed['user_a_clerk_id'] if preparer == 'clerk' else uuid.uuid4()
    if preparer == 'second_admin':
        await db.execute(text('INSERT INTO "user"(id,organization_id,email,password_hash,display_name)'
                              " VALUES (:preparer,:org,:email,'fixture','Second administrator')"),
                         {**ids, 'email': f"{ids['preparer']}@preparer.invalid"})
        await db.execute(text("INSERT INTO membership(organization_id,user_id,role) VALUES (:org,:preparer,'OWNER_ADMIN')"), ids)
        if settings.deployment_mode == 'hosted':
            confirmed_second_factor(ids['org'], ids['preparer'])
    weg = kind == 'owner_plan'
    ids['scope_type'] = 'WEG' if weg else 'MIET'
    statements = [
        "INSERT INTO property(id,organization_id,name,management_type) VALUES (:property,:org,'Preparer property','MISCH')",
        "INSERT INTO accounting_scope(id,organization_id,property_id,scope_type,label,valid_from,whole_property,origin,created_by)"
        " VALUES (:scope,:org,:property,:scope_type,'Preparer scope','2025-01-01',true,'REVIEWED',:actor)",
        "INSERT INTO unit(id,organization_id,property_id,number,usage_type,mea) VALUES (:unit,:org,:property,'1','WOHNUNG',1000)",
        "INSERT INTO person(id,organization_id,first_name,last_name,street,zip_code,city) VALUES (:person,:org,'Prepared','Person','','','')",
    ]
    if weg:
        statements += [
            "INSERT INTO owner_profile(id,organization_id,person_id,notes) VALUES (:profile,:org,:person,'')",
            "INSERT INTO ownership_history(organization_id,unit_id,owner_profile_id,valid_from) VALUES (:org,:unit,:profile,'2024-01-01')",
            "INSERT INTO economic_plan(id,organization_id,property_id,plan_year,period_start,period_end,status,engine_version)"
            " VALUES (:plan,:org,:property,2026,'2026-01-01','2026-12-31','draft','fixture')",
            "INSERT INTO economic_plan_line(id,organization_id,economic_plan_id,line_type,unit_id,owner_profile_id,label,planned_amount,monthly_amount,sort_order)"
            " VALUES (:line,:org,:plan,'OWNER_ADVANCE',:unit,:profile,'Hausgeld',1200,100,1)",
            "INSERT INTO accounting_scope_binding(organization_id,property_id,scope_id,economic_plan_id,origin,reason,reviewed_by)"
            " VALUES (:org,:property,:scope,:plan,'REVIEWED','Plan source',:actor)",
        ]
    else:
        statements += [
            "INSERT INTO tenant_profile(id,organization_id,person_id,notes) VALUES (:profile,:org,:person,'')",
            "INSERT INTO lease_agreement(id,organization_id,unit_id,tenant_profile_id,valid_from,base_rent,advance_payment,notes)"
            " VALUES (:lease,:org,:unit,:profile,'2025-01-01',500,100,'')",
            # A rental notice names the property's account where the rent is to be paid.
            "INSERT INTO bank_account(id,organization_id,property_id,account_type,label,iban)"
            " VALUES (:bank,:org,:property,'LAUFEND','Rent account','DE02200505501015871393')",
            "INSERT INTO accounting_scope_binding(organization_id,property_id,scope_id,bank_account_id,origin,reason,reviewed_by)"
            " VALUES (:org,:property,:scope,:bank,'REVIEWED','Rent account',:actor)",
        ]
    for statement in statements:
        await db.execute(text(statement), ids)
    if weg:
        # A frozen plan carries the hash of its snapshot, which the import checks.
        await db.execute(text("UPDATE economic_plan SET status='finalized',finalized_at=now(),finalized_by=:actor,"
                              "snapshot_payload='{}',snapshot_sha256=:sha WHERE id=:plan"), {**ids, 'sha': canonical_sha256({})})
    for user, capabilities in ((ids['actor'], ALL_CAPABILITIES), (ids['preparer'], ['read', 'prepare'])):
        await db.execute(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason)"
                              " VALUES (:org,:scope,:user,:capabilities,:actor,'Prepare and issue dunning notices')"),
                         {**ids, 'user': user, 'capabilities': capabilities})
    await db.commit()
    return ids


def notice_form(ids):
    """What the notice form sends: the scope, the counterparty, the period and the reference date."""
    if ids['kind'] == 'lease':
        return '/api/dunning/tenant', {'accounting_scope_id': str(ids['scope']), 'tenant_profile_id': str(ids['profile']),
                                       'lease_agreement_id': str(ids['lease']), **WINDOW, 'reference_date': '2026-03-16'}
    return '/api/dunning/owner', {'accounting_scope_id': str(ids['scope']), 'owner_profile_id': str(ids['profile']),
                                  'economic_plan_id': str(ids['plan']), **WINDOW, 'reference_date': '2026-03-16'}


def statement_url(ids):
    return f"/api/payments/mietkonto/{'lease' if ids['kind'] == 'lease' else 'owner'}/{ids['lease'] if ids['kind'] == 'lease' else ids['profile']}"


async def prepared_and_issued(ids):
    """The preparer prepares the notice and the administrator issues it, as the notice page does."""
    async with levy_client(ids, 'actor') as admin:
        fees = await admin.put('/api/dunning/settings', json={
            'fee_level_1': '5.00', 'fee_level_2': '10.00', 'fee_level_3': '15.00', 'default_interest_enabled': True})
        assert fees.status_code == 200, fees.text
    path, form = notice_form(ids)
    async with levy_client(ids, 'preparer') as preparer:
        drafted = await preparer.post(path, json=form)
        assert drafted.status_code == 201, drafted.text
        assert drafted.json()['status'] == 'draft'
    async with levy_client(ids, 'actor') as admin:
        issued = await admin.post(f"/api/dunning/{drafted.json()['id']}/issue")
        assert issued.status_code == 200, issued.text
        statement = await admin.get(statement_url(ids), params={'accounting_scope_id': str(ids['scope']), **WINDOW})
        assert statement.status_code == 200, statement.text
        archive = await admin.post('/api/organizations/export')
        assert archive.status_code == 200, archive.text
    return issued.json(), statement.json(), archive.content


@pytest.mark.parametrize('preparer', ['clerk', 'second_admin'])
@pytest.mark.parametrize('kind', NOTICE_KINDS)
async def test_import_restores_a_notice_another_user_prepared_with_the_archived_preparer(
    levy_db, scope_seed, import_target, rent_db, export_store, kind, preparer, confirmed_second_factor,
):
    """A WEG owner notice (finalized plan) and a rental notice (lease), prepared by a clerk or by a second administrator."""
    ids = await notice_source(levy_db, scope_seed, kind, preparer, confirmed_second_factor)
    notice, statement, archive = await prepared_and_issued(ids)
    assert notice['status'] == 'issued' and notice['level'] == 1 and notice['fee_amount'] == '5.00'
    assert Decimal(notice['principal_amount']) > 0 and Decimal(notice['interest_amount']) > 0
    assert Decimal(notice['total_amount']) == sum(Decimal(notice[key]) for key in ('principal_amount', 'fee_amount', 'interest_amount'))
    await source_organization_deleted(rent_db, ids['org'], ids['actor'], import_target)
    async with api_client(import_target) as api:
        restored = await api.post('/api/organizations/import', files={'file': ('prepared-by-another-user.tar.gz', archive)})
        assert restored.status_code == 200, restored.text
        mapping = restored.json()['id_map']
        shown = await api.get(f"/api/dunning/{mapping['dunning_notice'][notice['id']]}")
        assert shown.status_code == 200, shown.text
        assert in_target_ids(notice, mapping) == shown.json()
        subject = mapping['lease_agreement' if kind == 'lease' else 'owner_profile'][str(ids['lease'] if kind == 'lease' else ids['profile'])]
        account = await api.get(statement_url({**ids, 'lease': subject, 'profile': subject}),
                                params={'accounting_scope_id': mapping['accounting_scope'][str(ids['scope'])], **WINDOW})
        assert account.status_code == 200, account.text
        assert in_target_ids(statement, mapping) == account.json()
    # The preparer stays the archived preparer: the inactive placeholder of the account, never the importer.
    context = (await rent_db.execute(text('''SELECT c.created_by,c.scope_id,u.is_active,u.password_hash FROM dunning_scope_context c
        JOIN "user" u ON u.organization_id=c.organization_id AND u.id=c.created_by WHERE c.organization_id=:org'''), import_target)).one()
    assert str(context.created_by) == mapping['user'][str(ids['preparer'])] != str(import_target['admin'])
    assert (context.is_active, context.password_hash) == (False, '!')
    assert str(context.scope_id) == mapping['accounting_scope'][str(ids['scope'])]
    assert await rent_db.scalar(text('SELECT count(*) FROM dunning_notice WHERE organization_id=:org'), import_target) == 1
    await rent_db.rollback()


# ---- the guard itself: direct writes under the runtime role --------------------------------------

REFUSAL = 'Explicit preparation context required for a new notice'
INSERT_CONTEXT = text("""INSERT INTO dunning_scope_context(organization_id,dunning_notice_id,scope_id,lease_agreement_id,
    period_start,period_end,created_by,reason) VALUES (:org,:notice,:scope,:lease,'2026-03-01','2026-03-31',:created_by,'Restored')""")


async def add_user(org, *, active, password_hash):
    user = uuid.uuid4()
    engine = create_async_engine(MIGRATOR_URL)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("INSERT INTO organization(id,name) VALUES (:org,'Probe organization') ON CONFLICT DO NOTHING"), {'org': org})
            await connection.execute(text('INSERT INTO "user"(id,organization_id,email,password_hash,display_name,is_active)'
                                          " VALUES (:user,:org,:email,:hash,'Probe',:active)"),
                                     {'user': user, 'org': org, 'email': f'{user}@probe.invalid', 'hash': password_hash, 'active': active})
    finally:
        await engine.dispose()
    return user


@pytest.fixture
async def probe(levy_db, scope_seed):
    """A rental scope with the clerk holding `read` and `prepare`, and the accounts a restore may name as preparer."""
    ids = await notice_source(levy_db, scope_seed, 'lease')
    ids.update(
        placeholder=await add_user(ids['org'], active=False, password_hash='!'),
        colleague=await add_user(ids['org'], active=True, password_hash='$argon2id$real'),
        deactivated_colleague=await add_user(ids['org'], active=False, password_hash='$argon2id$real'),
        foreign_placeholder=await add_user(uuid.uuid4(), active=False, password_hash='!'))
    return ids


async def insert_context(ids, *, marker, created_by, notice=None, user=None, selected=True, session_user_key='preparer'):
    """One INSERT of a context as the runtime role would run it, in a transaction that is rolled back.

    The transaction-local settings are those of a request (organization, signed-in user, selected scope) plus the
    import's marker where the case has one. Returns the row count; a refusal is raised by the database.
    """
    notice = notice or uuid.uuid4()
    engine = create_async_engine(APP_RW_URL)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                await connection.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:user,true),"
                                              "set_config('app.current_scope_id',:scope,true)"),
                                         {'org': str(ids['org']), 'user': str(user or ids[session_user_key]),
                                          'scope': str(ids['scope']) if selected else ''})
                if marker is not None:
                    await connection.execute(text('SELECT set_config(:name,:value,true)'),
                                             {'name': MARKER, 'value': json.dumps(marker(notice) if callable(marker) else marker)})
                return (await connection.execute(INSERT_CONTEXT, {**ids, 'notice': notice, 'created_by': created_by})).rowcount
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


def naming(created_by, *, notice=None):
    """The marker the import sets: the notice whose context is restored and its archived preparer."""
    return lambda row: {'notice_id': str(notice or row), 'created_by': str(created_by)}


async def refused(ids, **kwargs):
    with pytest.raises(DBAPIError) as denied:
        await insert_context(ids, **kwargs)
    assert denied.value.orig.sqlstate == '42501', denied.value
    return str(denied.value)


async def test_the_guard_accepts_the_restore_of_this_row_for_its_archived_placeholder_preparer(probe):
    assert await insert_context(probe, marker=naming(probe['placeholder']), created_by=probe['placeholder']) == 1


async def test_the_guard_still_wants_the_signed_in_user_as_preparer_without_the_marker(probe):
    assert REFUSAL in await refused(probe, marker=None, created_by=probe['placeholder'])
    # The ordinary path is untouched: the signed-in user prepares their own context.
    assert await insert_context(probe, marker=None, created_by=probe['preparer']) == 1


async def test_the_guard_refuses_a_marker_that_names_another_row(probe):
    assert REFUSAL in await refused(probe, marker=naming(probe['placeholder'], notice=uuid.uuid4()), created_by=probe['placeholder'])


async def test_the_guard_refuses_a_marker_that_names_another_actor(probe):
    other = await add_user(probe['org'], active=False, password_hash='!')
    assert REFUSAL in await refused(probe, marker=naming(other), created_by=probe['placeholder'])


@pytest.mark.parametrize('account', ['colleague', 'deactivated_colleague', 'foreign_placeholder'])
async def test_the_guard_accepts_only_a_placeholder_of_this_organization(probe, account):
    """An active member, a colleague deactivated in the user list (their password is intact) and an account of
    another organization are no placeholder, whatever the marker says."""
    assert REFUSAL in await refused(probe, marker=naming(probe[account]), created_by=probe[account])


async def test_the_guard_refuses_the_marker_for_a_notice_that_already_exists(probe):
    """The context goes first: a context for a notice that exists is refused, marker or not."""
    path, form = notice_form(probe)
    async with levy_client(probe, 'preparer') as preparer:
        drafted = await preparer.post(path, json=form)
        assert drafted.status_code == 201, drafted.text
    notice = uuid.UUID(drafted.json()['id'])
    assert REFUSAL in await refused(probe, marker=naming(probe['placeholder'], notice=notice), created_by=probe['placeholder'], notice=notice)


async def test_the_guard_still_wants_the_prepare_capability_and_the_selected_scope(probe):
    """The marker changes the actor comparison only: the signed-in user still needs a live `prepare` grant on the
    selected scope of the context."""
    assert REFUSAL in await refused(probe, marker=naming(probe['placeholder']), created_by=probe['placeholder'], selected=False)
    reader = await add_user(probe['org'], active=True, password_hash='$argon2id$real')
    engine = create_async_engine(MIGRATOR_URL)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("INSERT INTO membership(organization_id,user_id,role) VALUES (:org,:user,'CLERK')"), {**probe, 'user': reader})
            await connection.execute(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason)"
                                          " VALUES (:org,:scope,:user,ARRAY['read'],:actor,'Read only')"), {**probe, 'user': reader})
    finally:
        await engine.dispose()
    assert REFUSAL in await refused(probe, marker=naming(probe['placeholder']), created_by=probe['placeholder'], user=reader)
    assert REFUSAL in await refused(probe, marker=naming(probe['placeholder']), created_by=probe['placeholder'], user=uuid.uuid4())


async def test_a_context_stays_immutable_with_the_marker(probe):
    """No grant of the runtime role, and the trigger refuses the owner of the table too, marker or not."""
    path, form = notice_form(probe)
    async with levy_client(probe, 'preparer') as preparer:
        drafted = await preparer.post(path, json=form)
        assert drafted.status_code == 201, drafted.text
    engine = create_async_engine(APP_RW_URL)
    try:
        for statement in ("UPDATE dunning_scope_context SET reason='Rewritten' WHERE dunning_notice_id=:notice",
                          'DELETE FROM dunning_scope_context WHERE dunning_notice_id=:notice'):
            with pytest.raises(DBAPIError) as denied:
                async with engine.begin() as connection:
                    await connection.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:user,true)"),
                                             {'org': str(probe['org']), 'user': str(probe['preparer'])})
                    await connection.execute(text('SELECT set_config(:name,:value,true)'),
                                             {'name': MARKER, 'value': json.dumps({'notice_id': drafted.json()['id'], 'created_by': str(probe['placeholder'])})})
                    await connection.execute(text(statement), {'notice': uuid.UUID(drafted.json()['id'])})
            assert denied.value.orig.sqlstate == '42501', denied.value
    finally:
        await engine.dispose()
    owner = create_async_engine(MIGRATOR_URL)
    try:
        for statement in ("UPDATE dunning_scope_context SET reason='Rewritten' WHERE dunning_notice_id=:notice",
                          'DELETE FROM dunning_scope_context WHERE dunning_notice_id=:notice'):
            with pytest.raises(DBAPIError) as denied:
                async with owner.begin() as connection:
                    await connection.execute(text('SELECT set_config(:name,:value,true)'),
                                             {'name': MARKER, 'value': json.dumps({'notice_id': drafted.json()['id'], 'created_by': str(probe['placeholder'])})})
                    await connection.execute(text(statement), {'notice': uuid.UUID(drafted.json()['id'])})
            assert denied.value.orig.sqlstate == '23514' and 'immutable' in str(denied.value), denied.value
    finally:
        await owner.dispose()


def test_0195_replaces_only_the_guard_and_its_downgrade_restores_the_text_of_0039():
    migration = importlib.import_module('migrations.versions.0195_import_restores_dunning_preparer')
    original = Path(migration.__file__).with_name('0039_dunning_scope_context.py').read_text()
    # Whitespace aside, the body 0039 wrote between its dollar quotes.
    wrote = ' '.join((re.search(r'CREATE FUNCTION protect_dunning_context\(\).*?AS \$\$(.*?END) \$\$;', original, re.S).group(1)).split())
    functions = text("""SELECT p.oid::regprocedure::text,pg_get_functiondef(p.oid),p.proacl::text,p.proconfig,p.proowner,p.prosecdef
        FROM pg_proc p WHERE p.pronamespace='public'::regnamespace ORDER BY 1""")
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
            savepoint = db.begin_nested()
            try:
                head = {row[0]: tuple(row[1:]) for row in db.execute(functions)}
                migration.downgrade()
                older = {row[0]: tuple(row[1:]) for row in db.execute(functions)}
                assert [name for name in head if head[name] != older[name]] == ['protect_dunning_context()']
                guard = 'protect_dunning_context()'
                # Definer rights, search path, owner and ACL are those of the function it replaces.
                assert head[guard][1:] == older[guard][1:] and head[guard][4] is True
                assert MARKER in head[guard][0] and MARKER not in older[guard][0]
                assert ' '.join(older[guard][0].split('$function$')[1].split()) == wrote
                migration.upgrade()
                assert {row[0]: tuple(row[1:]) for row in db.execute(functions)} == head
            finally:
                savepoint.rollback()
    finally:
        engine.dispose()


# ---- the marker has one writer ---------------------------------------------------------------------

APP_ROOT = Path(__file__).resolve().parents[1] / 'app'


@pytest.mark.no_db
def test_only_the_organization_import_sets_the_restore_marker():
    """The guard trusts the marker, so no other code may set it (ADR 0016)."""
    writers = sorted(str(path.relative_to(APP_ROOT)) for path in APP_ROOT.rglob('*.py') if MARKER in path.read_text())
    assert writers == ['services/organization_import_service.py']
