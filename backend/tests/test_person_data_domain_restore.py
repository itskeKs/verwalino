"""The import restores a contact data domain and a grant that another administrator made (issue #817, ADR 0016).

`protect_person_data_setup` binds `created_by` of a new domain and `granted_by` of a new grant to the signed-in user.
After an import the archived creator or grantor (a second administrator, or any administrator whose address the importing
administrator does not carry) is an inactive placeholder account, so the restore was refused at `person_data_domain`
(sqlstate 42501, "Domain actor mismatch"). The creator and the grantor stay the archived accounts: the import names the very
row it restores and its archived actor in the transaction-local settings `app.organization_import_person_data_domain` and
`app.organization_import_person_data_grant`, and the trigger has one more way in for each insert. That only the import
sets them is checked beside the other markers, in `test_dunning_context_restore.py`.
"""
import importlib
import json
import re
import uuid
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import APP_RW_URL, MIGRATOR_SYNC_URL, MIGRATOR_URL
from tests.test_organization_export import export_store as export_store
from tests.test_organization_import import import_target as import_target, in_target_ids, source_organization_deleted
from tests.test_rent_changes import api_client, rent_db as rent_db, rent_world as rent_world  # noqa: F401
from tests.test_scope_binding_restore import second_administrator

DOMAIN_MARKER = 'app.organization_import_person_data_domain'
GRANT_MARKER = 'app.organization_import_person_data_grant'


def key():
    return {'Idempotency-Key': str(uuid.uuid4())}


async def contact_setup(world, *, maker='second_admin', grantee='admin'):
    """The contact pages: `maker` adds the contact Anna Berg, prepares its data domain and grants it to `grantee`.

    Every request is the one the page sends (an Idempotency-Key on the domain and on the grant). With no `grantee`
    the domain stays without a grant.
    """
    async with api_client(world, maker) as api:
        person = await api.post('/api/persons', json={'first_name': 'Anna', 'last_name': 'Berg'})
        assert person.status_code == 201, person.text
        domain = await api.post('/api/person-data-domains', headers=key(), json={
            'person_id': person.json()['id'], 'reason_code': 'TEAM_ACCESS'})
        assert domain.status_code == 201, domain.text
        grant = None
        if grantee:
            grant = await api.post(f"/api/person-data-domains/{domain.json()['id']}/grants", headers=key(), json={
                'user_id': str(world[grantee]), 'capabilities': ['read', 'export'], 'reason_code': 'TEAM_ACCESS'})
            assert grant.status_code == 201, grant.text
    return person.json()['id'], domain.json(), grant.json() if grant else None


async def listed(world, person):
    """The domain of the contact and the grants of that domain, as the administrator's contact page lists them."""
    async with api_client(world) as api:
        domains = await api.get('/api/person-data-domains/page', params={'person_id': person})
        assert domains.status_code == 200, domains.text
        grants = []
        for domain in domains.json()['items']:
            page = await api.get(f"/api/person-data-domains/{domain['id']}/grants/page")
            assert page.status_code == 200, page.text
            grants += page.json()['items']
        return domains.json()['items'], grants


async def actors(db, target):
    """`created_by` of the domain and `granted_by` of the grant, with the account behind each."""
    await db.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:admin,true)"),
                     {'org': str(target['org']), 'admin': str(target['admin'])})
    rows = (await db.execute(text('''SELECT 'domain' AS kind,d.created_by AS actor,u.is_active,u.password_hash
          FROM person_data_domain d JOIN "user" u ON u.organization_id=d.organization_id AND u.id=d.created_by
          WHERE d.organization_id=:org
        UNION ALL SELECT 'grant',g.granted_by,u.is_active,u.password_hash
          FROM person_data_grant g JOIN "user" u ON u.organization_id=g.organization_id AND u.id=g.granted_by
          WHERE g.organization_id=:org'''), {'org': target['org']})).all()
    await db.rollback()
    return {row.kind: row for row in rows}


async def domain_prepared_by(db, target):
    """Who the restored audit trail names as the actor of `person_data.domain_prepared`."""
    await db.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:admin,true)"),
                     {'org': str(target['org']), 'admin': str(target['admin'])})
    rows = (await db.execute(text("SELECT actor_user_id FROM audit_event WHERE organization_id=:org "
                                  "AND entity_type='person_data_domain' AND action='person_data.domain_prepared'"), target)).all()
    await db.rollback()
    return sorted(str(row.actor_user_id) for row in rows)


def without_stamp(rows):
    """The restore stamps `created_at` again, and the grant list names the importing administrator by their own name."""
    return [{k: v for k, v in row.items() if k not in {'created_at', 'user_display_name'}} for row in rows]


async def test_import_restores_a_domain_and_a_grant_the_second_administrator_made(
    rent_world, import_target, rent_db, export_store, confirmed_second_factor,
):
    """The user check: the second administrator prepares the data domain of "Anna Berg" and grants it to the first one;
    the first exports the organization, has the source erased and imports the archive into a new organization."""
    world = await second_administrator(rent_db, rent_world, confirmed_second_factor)
    person, domain, grant = await contact_setup(world)
    source_domains, source_grants = await listed(world, person)
    assert [row['id'] for row in source_domains] == [domain['id']] and [row['id'] for row in source_grants] == [grant['id']]
    async with api_client(world) as api:
        archive = await api.post('/api/organizations/export')
        assert archive.status_code == 200, archive.text
    await source_organization_deleted(rent_db, world['org'], world['admin'], import_target)
    async with api_client(import_target) as api:
        answer = await api.post('/api/organizations/import', files={'file': ('second-administrator.tar.gz', archive.content)})
        assert answer.status_code == 200, answer.text
    ids = answer.json()['id_map']
    restored_domains, restored_grants = await listed(import_target, ids['person'][person])
    # Domain and grant equal the source: reason, rights, expiry, revocation and the grantee, the importing administrator.
    assert without_stamp(restored_domains) == without_stamp(in_target_ids(source_domains, ids))
    assert without_stamp(restored_grants) == without_stamp(in_target_ids(source_grants, ids))
    assert (restored_domains[0]['reason_code'], restored_domains[0]['label']) == ('TEAM_ACCESS', f"Contact {ids['person'][person]}")
    assert (restored_grants[0]['capabilities'], restored_grants[0]['reason_code']) == (['export', 'read'], 'TEAM_ACCESS')
    assert restored_grants[0]['user_id'] == str(import_target['admin']) == ids['user'][str(world['admin'])]
    # The creator and the grantor stay the second administrator's account: a placeholder that cannot act, never the importer.
    placeholder = ids['user'][str(world['second_admin'])]
    assert placeholder != str(import_target['admin'])
    found = await actors(rent_db, import_target)
    assert found.keys() == {'domain', 'grant'}
    for kind, account in found.items():
        assert (str(account.actor), account.is_active, account.password_hash) == (placeholder, False, '!'), kind
    # The archived audit event keeps the second administrator. The trigger writes its own event for the restore insert, as
    # it does for every restored domain, and names the signed-in user, the importing administrator, as the actor of that act.
    assert await domain_prepared_by(rent_db, import_target) == sorted([placeholder, str(import_target['admin'])])


async def test_import_restores_a_domain_beside_the_living_source_with_the_archived_creator(
    rent_world, import_target, rent_db, export_store, confirmed_second_factor,
):
    """The line of the issue: the source organization lives on, so every archived administrator, the first one too,
    is a placeholder in the target; the domain keeps its archived creator."""
    world = await second_administrator(rent_db, rent_world, confirmed_second_factor)
    person, domain, _ = await contact_setup(world, grantee=None)
    source_domains, _ = await listed(world, person)
    async with api_client(world) as api:
        archive = await api.post('/api/organizations/export')
        assert archive.status_code == 200, archive.text
    async with api_client(import_target) as api:
        answer = await api.post('/api/organizations/import', files={'file': ('beside-the-source.tar.gz', archive.content)})
        assert answer.status_code == 200, answer.text
    ids = answer.json()['id_map']
    restored, _ = await listed(import_target, ids['person'][person])
    assert without_stamp(restored) == without_stamp(in_target_ids(source_domains, ids))
    creator = (await actors(rent_db, import_target))['domain']
    assert (str(creator.actor), creator.is_active, creator.password_hash) == (ids['user'][str(world['second_admin'])], False, '!')
    # The living source is untouched.
    assert [row['id'] for row in (await listed(world, person))[0]] == [domain['id']]


async def test_a_grant_beside_the_living_source_is_refused_for_its_grantee_not_for_its_grantor(
    rent_world, import_target, rent_db, export_store, confirmed_second_factor,
):
    """The grantee is a placeholder there too (no archived address is the importing administrator's), and the trigger
    still takes only an active member as grantee, so the grant, and with it the import, is refused; nothing is written."""
    world = await second_administrator(rent_db, rent_world, confirmed_second_factor)
    await contact_setup(world)
    async with api_client(world) as api:
        archive = await api.post('/api/organizations/export')
    async with api_client(import_target) as api:
        answer = await api.post('/api/organizations/import', files={'file': ('beside-the-source.tar.gz', archive.content)})
    assert answer.status_code == 422, answer.text
    assert (answer.json()['detail']['code'], answer.json()['detail']['entity']) == ('IMPORT_INTEGRITY_REFUSAL', 'person_data_grant')
    assert await rent_db.scalar(text('SELECT count(*) FROM person WHERE organization_id=:org'), import_target) == 0
    await rent_db.rollback()


async def test_a_grant_to_another_archived_member_is_still_refused_after_the_source_was_erased(
    rent_world, import_target, rent_db, export_store, confirmed_second_factor,
):
    """Only the importing administrator is an active member after the import; a grant to the clerk meets the unchanged
    rule that a grantee is an active member, whoever made the grant."""
    world = await second_administrator(rent_db, rent_world, confirmed_second_factor)
    await contact_setup(world, grantee='clerk')
    async with api_client(world) as api:
        archive = await api.post('/api/organizations/export')
    await source_organization_deleted(rent_db, world['org'], world['admin'], import_target)
    async with api_client(import_target) as api:
        answer = await api.post('/api/organizations/import', files={'file': ('grant-to-the-clerk.tar.gz', archive.content)})
    assert answer.status_code == 422, answer.text
    assert (answer.json()['detail']['code'], answer.json()['detail']['entity']) == ('IMPORT_INTEGRITY_REFUSAL', 'person_data_grant')
    await rent_db.rollback()


# ---- the trigger itself: direct writes under the runtime role --------------------------------------

REFUSALS = {'domain': 'Domain actor mismatch', 'grant': 'Grant requires matching actor and active member'}
SETTINGS = {'domain': DOMAIN_MARKER, 'grant': GRANT_MARKER}
KEYS = {'domain': ('domain_id', 'created_by'), 'grant': ('grant_id', 'granted_by')}
INSERT = {
    'domain': text("""INSERT INTO person_data_domain(id,organization_id,person_id,reason_code,created_by,request_key)
        VALUES (:row,:org,:other_person,'TEAM_ACCESS',:by,:request)"""),
    'grant': text("""INSERT INTO person_data_grant(id,organization_id,domain_id,user_id,capabilities,reason_code,granted_by,
        request_key,revoked_at,revoked_by) VALUES (:row,:org,:domain,:grantee,ARRAY['read'],'TEAM_ACCESS',:by,:request,
        CASE WHEN CAST(:revoker AS uuid) IS NULL THEN NULL ELSE now() END,:revoker)"""),
}


async def add_user(db, org, *, active, password_hash, role='CLERK'):
    user = uuid.uuid4()
    await db.execute(text('INSERT INTO "user"(id,organization_id,email,password_hash,display_name,is_active)'
                          " VALUES (:user,:org,:email,:hash,'Probe',:active)"),
                     {'user': user, 'org': org, 'email': f'{user}@probe.invalid', 'hash': password_hash, 'active': active})
    await db.execute(text('INSERT INTO membership(organization_id,user_id,role) VALUES (:org,:user,:role)'),
                     {'org': org, 'user': user, 'role': role})
    await db.commit()
    return user


@pytest.fixture
async def probe(rent_world, rent_db):
    """The organization of `rent_world` with the administrator `actor`, a clerk, a contact with a data domain and one
    without, and the accounts a restore may name as creator or grantor, beside an organization that is not its own."""
    ids = {'org': rent_world['org'], 'actor': rent_world['admin'], 'clerk': rent_world['clerk'],
           'other_person': rent_world['hidden_person'], 'foreign_org': rent_world['foreign_org'],
           'foreign_admin': rent_world['foreign_admin']}
    ids.update(
        placeholder=await add_user(rent_db, ids['org'], active=False, password_hash='!'),
        active_unusable=await add_user(rent_db, ids['org'], active=True, password_hash='!'),
        colleague=await add_user(rent_db, ids['org'], active=True, password_hash='$argon2id$real'),
        deactivated_colleague=await add_user(rent_db, ids['org'], active=False, password_hash='$argon2id$real'),
        foreign_placeholder=await add_user(rent_db, ids['foreign_org'], active=False, password_hash='!'))
    async with api_client(rent_world) as api:
        domain = await api.post('/api/person-data-domains', headers=key(), json={
            'person_id': str(rent_world['person']), 'reason_code': 'INITIAL_SETUP'})
        assert domain.status_code == 201, domain.text
    ids['domain'] = uuid.UUID(domain.json()['id'])
    return ids


def naming(kind, actor, *, row=None):
    """The setting the import makes: the row it restores and its archived actor."""
    row_key, actor_key = KEYS[kind]
    return lambda this_row: {row_key: str(row or this_row), actor_key: str(actor)}


async def insert(ids, kind, *, marker, by, row=None, user='actor', org=None, grantee='clerk', revoked_by=None):
    """One INSERT of a domain or a grant as the runtime role would run it, in a transaction that is rolled back.

    The transaction-local settings are those of a request (organization, signed-in user) plus the import's setting where
    the case has one. Returns the row count; a refusal is raised by the database.
    """
    row = row or uuid.uuid4()
    session_user = ids[user] if isinstance(user, str) else user
    engine = create_async_engine(APP_RW_URL)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                await connection.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:user,true)"),
                                         {'org': str(org or ids['org']), 'user': str(session_user)})
                if marker is not None:
                    value = marker(row) if callable(marker) else marker
                    await connection.execute(text('SELECT set_config(:name,:value,true)'),
                                             {'name': SETTINGS[kind], 'value': value if isinstance(value, str) else json.dumps(value)})
                return (await connection.execute(INSERT[kind], {
                    'row': row, 'org': ids['org'], 'other_person': ids['other_person'], 'domain': ids['domain'],
                    'grantee': ids[grantee], 'by': ids[by] if isinstance(by, str) else by, 'request': uuid.uuid4(),
                    'revoker': ids[revoked_by] if revoked_by else None})).rowcount
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


async def refused(ids, kind, *, message=None, **kwargs):
    with pytest.raises(DBAPIError) as denied:
        await insert(ids, kind, **kwargs)
    assert denied.value.orig.sqlstate == '42501' and (message or REFUSALS[kind]) in str(denied.value), denied.value


kinds = pytest.mark.parametrize('kind', ['domain', 'grant'])


@kinds
async def test_the_trigger_accepts_the_restore_of_this_row_for_its_archived_placeholder(probe, kind):
    assert await insert(probe, kind, marker=naming(kind, probe['placeholder']), by='placeholder') == 1


@kinds
async def test_the_trigger_still_wants_the_signed_in_user_without_the_setting(probe, kind):
    await refused(probe, kind, marker=None, by='placeholder')
    await refused(probe, kind, marker='', by='placeholder')
    # The ordinary path is untouched: the signed-in user makes the row, with or without a setting.
    assert await insert(probe, kind, marker=None, by='actor') == 1
    assert await insert(probe, kind, marker=naming(kind, probe['placeholder']), by='actor') == 1
    # Neither does a setting make a colleague the actor of a row the signed-in user records.
    await refused(probe, kind, marker=None, by='colleague')


@kinds
async def test_the_trigger_refuses_a_setting_that_names_another_row(probe, kind):
    await refused(probe, kind, marker=naming(kind, probe['placeholder'], row=uuid.uuid4()), by='placeholder')


@kinds
async def test_the_trigger_refuses_a_setting_that_names_another_actor(probe, rent_db, kind):
    other = await add_user(rent_db, probe['org'], active=False, password_hash='!')
    await refused(probe, kind, marker=naming(kind, other), by='placeholder')


@kinds
@pytest.mark.parametrize('account', ['colleague', 'deactivated_colleague', 'active_unusable', 'foreign_placeholder'])
async def test_the_trigger_accepts_only_a_placeholder_of_this_organization(probe, kind, account):
    """An active member, a colleague deactivated in the user list (their password is intact), an active account whose
    password is unusable and an account of another organization are no placeholder, whatever the setting says."""
    await refused(probe, kind, marker=naming(kind, probe[account]), by=account)


@kinds
@pytest.mark.parametrize('marker', [
    pytest.param('{not json', id='malformed'), pytest.param('{"domain_id": ', id='cut-off'), pytest.param('[]', id='array'),
    pytest.param('"text"', id='string'), pytest.param('7', id='number'), pytest.param('null', id='json-null'),
    pytest.param('{}', id='empty-object'), pytest.param('{"row": 7, "actor": 7}', id='numbers'),
    pytest.param('{"row": "not-a-uuid", "actor": "not-a-uuid"}', id='not-uuids'),
    pytest.param('{"row": null, "actor": null}', id='nulls'),
    pytest.param('{"row": "THIS_ROW"}', id='no-actor'), pytest.param('{"actor": "PLACEHOLDER"}', id='no-row')])
async def test_a_missing_or_malformed_setting_is_refused_like_any_other_actor_and_never_raises_another_error(probe, kind, marker):
    """The check answers false for a setting of any shape; the refusal is the trigger's own 42501, never a cast error."""
    row, placeholder = uuid.uuid4(), probe['placeholder']
    row_key, actor_key = KEYS[kind]
    marker = marker.replace('"row"', f'"{row_key}"').replace('"actor"', f'"{actor_key}"').replace(
        'THIS_ROW', str(row)).replace('PLACEHOLDER', str(placeholder))
    await refused(probe, kind, marker=marker, by='placeholder', row=row)


@kinds
async def test_the_setting_of_the_other_table_is_no_alternative(probe, kind):
    """A domain's setting names a domain and a grant's names a grant: neither opens the insert of the other."""
    other = 'grant' if kind == 'domain' else 'domain'
    row, engine = uuid.uuid4(), create_async_engine(APP_RW_URL)
    try:
        with pytest.raises(DBAPIError) as denied:
            async with engine.begin() as connection:
                await connection.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:user,true),"
                                              "set_config(:name,:value,true)"),
                                         {'org': str(probe['org']), 'user': str(probe['actor']), 'name': SETTINGS[other],
                                          'value': json.dumps(naming(other, probe['placeholder'])(row))})
                await connection.execute(INSERT[kind], {
                    'row': row, 'org': probe['org'], 'other_person': probe['other_person'], 'domain': probe['domain'],
                    'grantee': probe['clerk'], 'by': probe['placeholder'], 'request': uuid.uuid4(), 'revoker': None})
        assert denied.value.orig.sqlstate == '42501' and REFUSALS[kind] in str(denied.value), denied.value
    finally:
        await engine.dispose()


@kinds
async def test_the_trigger_still_wants_the_importing_administrators_rights(probe, kind):
    """The setting changes the actor comparison only: the signed-in user is an active administrator of the organization."""
    marker = naming(kind, probe['placeholder'])
    # A clerk, a stranger and an administrator of another organization are no administrator of this one.
    await refused(probe, kind, marker=marker, by='placeholder', user='clerk', message='Active administrator required')
    await refused(probe, kind, marker=marker, by='placeholder', user=uuid.uuid4(), message='Active administrator required')
    await refused(probe, kind, marker=marker, by='placeholder', user='foreign_admin', message='Active administrator required')
    # A session of another organization writes nothing into this one.
    await refused(probe, kind, marker=marker, by='placeholder', user='foreign_admin', org=probe['foreign_org'],
                  message='Person data organization mismatch')
    # Nor does a deactivated administrator.
    await migrate('UPDATE "user" SET is_active=false WHERE id=:actor', actor=probe['actor'])
    await refused(probe, kind, marker=marker, by='placeholder', message='Active administrator required')


async def migrate(statement, **params):
    engine = create_async_engine(MIGRATOR_URL)
    try:
        async with engine.begin() as connection:
            await connection.execute(text(statement), params)
    finally:
        await engine.dispose()


@pytest.mark.parametrize('grantee', ['placeholder', 'deactivated_colleague'])
async def test_a_grant_still_wants_an_active_member_as_grantee_with_the_setting(probe, grantee):
    marker = naming('grant', probe['placeholder'])
    await refused(probe, 'grant', marker=marker, by='placeholder', grantee=grantee)
    # The importing administrator is the one active member after an import, so a grant to them is restored.
    assert await insert(probe, 'grant', marker=marker, by='placeholder', grantee='actor') == 1


async def test_a_grant_is_still_inserted_unrevoked_with_the_setting(probe):
    await refused(probe, 'grant', marker=naming('grant', probe['placeholder']), by='placeholder', revoked_by='actor')


@kinds
async def test_a_row_stays_immutable_with_the_setting(probe, kind):
    """The runtime role has no UPDATE or DELETE on a domain and only the revocation columns of a grant; the trigger refuses
    the owner of the table too, and a setting changes none of it."""
    if kind == 'domain':
        row, table, update = probe['domain'], 'person_data_domain', "reason_code='REPLACEMENT'"
    else:
        async with api_client({'org': probe['org'], 'admin': probe['actor']}) as api:
            made = await api.post(f"/api/person-data-domains/{probe['domain']}/grants", headers=key(), json={
                'user_id': str(probe['colleague']), 'capabilities': ['read'], 'reason_code': 'TEAM_ACCESS'})
            assert made.status_code == 201, made.text
        row, table, update = uuid.UUID(made.json()['id']), 'person_data_grant', "capabilities=ARRAY['read','export']"
    statements = [f'UPDATE {table} SET {update} WHERE organization_id=:org AND id=:row',
                  f'DELETE FROM {table} WHERE organization_id=:org AND id=:row']
    engine = create_async_engine(APP_RW_URL)
    try:
        for statement in statements:
            with pytest.raises(DBAPIError) as denied:
                async with engine.begin() as connection:
                    await connection.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:user,true),"
                                                  "set_config(:name,:value,true)"),
                                             {'org': str(probe['org']), 'user': str(probe['actor']), 'name': SETTINGS[kind],
                                              'value': json.dumps(naming(kind, probe['placeholder'], row=row)(row))})
                    await connection.execute(text(statement), {'org': probe['org'], 'row': row})
            assert denied.value.orig.sqlstate == '42501', denied.value
    finally:
        await engine.dispose()
    owner = create_async_engine(MIGRATOR_URL)
    try:
        for statement in statements:
            with pytest.raises(DBAPIError) as denied:
                async with owner.begin() as connection:
                    await connection.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:user,true)"),
                                             {'org': str(probe['org']), 'user': str(probe['actor'])})
                    await connection.execute(text(statement), {'org': probe['org'], 'row': row})
            assert denied.value.orig.sqlstate == '23514', denied.value
    finally:
        await owner.dispose()


async def test_a_revocation_stays_the_signed_in_users_with_the_setting(probe):
    """The revoking update is the one write a grant allows; the setting of a restore does not let another account revoke."""
    grant, engine = uuid.uuid4(), create_async_engine(APP_RW_URL)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                await connection.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:user,true)"),
                                         {'org': str(probe['org']), 'user': str(probe['actor'])})
                await connection.execute(INSERT['grant'], {
                    'row': grant, 'org': probe['org'], 'other_person': probe['other_person'], 'domain': probe['domain'],
                    'grantee': probe['clerk'], 'by': probe['actor'], 'request': uuid.uuid4(), 'revoker': None})
                await connection.execute(text('SELECT set_config(:name,:value,true)'),
                                         {'name': GRANT_MARKER, 'value': json.dumps(naming('grant', probe['placeholder'], row=grant)(grant))})
                with pytest.raises(DBAPIError) as denied:
                    await connection.execute(text('UPDATE person_data_grant SET revoked_at=now(),revoked_by=:placeholder '
                                                  'WHERE organization_id=:org AND id=:grant'),
                                             {'org': probe['org'], 'grant': grant, 'placeholder': probe['placeholder']})
                assert denied.value.orig.sqlstate == '23514' and 'one-way' in str(denied.value), denied.value
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


# ---- the check the trigger calls --------------------------------------------------------------------

async def check(ids, kind, *, marker, actor, user='actor', org=None, row=None):
    """`import_restores_person_data` as the trigger calls it, with the owner's connection (the runtime role cannot call it)."""
    row = row or uuid.uuid4()
    engine = create_async_engine(MIGRATOR_URL)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                await connection.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:user,true)"),
                                         {'org': str(org or ids['org']), 'user': str(ids[user] if isinstance(user, str) else user)})
                if marker is not None:
                    await connection.execute(text('SELECT set_config(:name,:value,true)'),
                                             {'name': SETTINGS[kind], 'value': json.dumps(marker(row))})
                return await connection.scalar(text('SELECT import_restores_person_data(:kind,:row,:org,:actor)'),
                                               {'kind': kind, 'row': row, 'org': ids['org'], 'actor': ids[actor]})
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


@kinds
async def test_the_check_wants_an_active_administrator_of_the_organization_in_the_session(probe, kind):
    marker = naming(kind, probe['placeholder'])
    assert await check(probe, kind, marker=marker, actor='placeholder') is True
    for user, org in (('clerk', None), ('foreign_admin', None), ('foreign_admin', probe['foreign_org'])):
        assert await check(probe, kind, marker=marker, actor='placeholder', user=user, org=org) is False, (user, org)
    assert await check(probe, kind, marker=marker, actor='placeholder', user=uuid.uuid4()) is False


async def test_the_check_answers_false_for_a_kind_it_does_not_know(probe):
    """A session that would pass for a domain or a grant (an active administrator naming a placeholder) gets false for
    a kind the check does not know, so no other caller can use it for a table it was not written for."""
    assert await check(probe, 'person', marker=None, actor='placeholder') is False


# ---- the migration ---------------------------------------------------------------------------------

def test_0201_replaces_only_the_trigger_function_and_its_downgrade_restores_the_text_of_0068():
    migration = importlib.import_module('migrations.versions.0201_import_restores_person_data_actor')
    original = Path(migration.__file__).with_name('0068_person_data_domains.py').read_text()
    # Whitespace aside, the body 0068 wrote between its dollar quotes.
    wrote = ' '.join(re.search(r'CREATE FUNCTION protect_person_data_setup\(\).*?AS \$\$(.*?END) \$\$;', original, re.S).group(1).split())
    functions = text("""SELECT p.oid::regprocedure::text,pg_get_functiondef(p.oid),p.proacl::text,p.proconfig,p.proowner,p.prosecdef
        FROM pg_proc p WHERE p.pronamespace='public'::regnamespace ORDER BY 1""")
    triggers = text("""SELECT tgrelid::regclass::text,tgname,pg_get_triggerdef(oid) FROM pg_trigger
        WHERE tgfoid='protect_person_data_setup'::regproc ORDER BY 1""")
    guard, check = 'protect_person_data_setup()', 'import_restores_person_data(text,uuid,uuid,uuid)'
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
            savepoint = db.begin_nested()
            try:
                head = {row[0]: tuple(row[1:]) for row in db.execute(functions)}
                head_triggers = db.execute(triggers).all()
                migration.downgrade()
                older = {row[0]: tuple(row[1:]) for row in db.execute(functions)}
                # Only the trigger function changes, and the check goes with it; both triggers stay as they were.
                assert set(head) - set(older) == {check} and not set(older) - set(head)
                assert [name for name in older if head[name] != older[name]] == [guard]
                assert db.execute(triggers).all() == head_triggers and len(head_triggers) == 2
                # Definer rights, search path, owner and ACL are those of the function it replaces.
                assert head[guard][1:] == older[guard][1:] and head[guard][4] is True
                assert DOMAIN_MARKER not in older[guard][0] and GRANT_MARKER not in older[guard][0]
                assert ' '.join(older[guard][0].split('$function$')[1].split()) == wrote
                # The check has definer rights with a fixed search path and no other privilege than its owner's: only the
                # trigger function, which runs as that owner, calls it.
                definition, acl, config, owner, definer = head[check]
                assert definer is True and config == ['search_path=pg_catalog, pg_temp', 'row_security=off']
                assert acl.strip('{}').split(',') == ['app_migrator=X/app_migrator']
                assert DOMAIN_MARKER in definition and GRANT_MARKER in definition
                assert 'import_restores_person_data' in head[guard][0]
                migration.upgrade()
                assert {row[0]: tuple(row[1:]) for row in db.execute(functions)} == head
            finally:
                savepoint.rollback()
    finally:
        engine.dispose()
