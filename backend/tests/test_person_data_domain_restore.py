"""The import restores a contact data domain and a grant that another administrator made (issue #817, ADR 0016).

`protect_person_data_setup` binds `created_by` of a new domain and `granted_by` of a new grant to the signed-in user.
After an import the archived creator or grantor (a second administrator, or any administrator whose address the importing
administrator does not carry) is an inactive placeholder account, so the restore was refused at `person_data_domain`
(sqlstate 42501, "Domain actor mismatch"). The creator and the grantor stay the archived accounts: the import names the very
row it restores and its archived actor in the transaction-local settings `app.organization_import_person_data_domain` and
`app.organization_import_person_data_grant`, and the trigger has one more way in for each insert. That only the import
sets them is checked beside the other markers, in `test_dunning_context_restore.py`.

The same setting restores a grant as the archive has it (issue #851): the trigger took only an unrevoked grant for an active
member and stamped `created_at` again, so a grant to a clerk (an inactive placeholder after the import), a revoked grant and a
grant whose expiry has passed (the table requires `expires_at > created_at`) were refused at `person_data_grant`. A grant the
import names keeps its grantee, its revocation and its `created_at`. What that means for access is tested here too: a restored
grant to a placeholder gives no access, a revoked or expired one none after the account is reactivated either.
"""
import importlib
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import settings
from tests.conftest import APP_RW_URL, MIGRATOR_SYNC_URL, MIGRATOR_URL
from tests.test_organization_export import export_store as export_store
from tests.test_organization_import import import_target as import_target, in_target_ids, source_organization_deleted
from tests.test_rent_changes import api_client, rent_db as rent_db, rent_world as rent_world  # noqa: F401
from tests.test_scope_binding_restore import second_administrator

DOMAIN_MARKER = 'app.organization_import_person_data_domain'
GRANT_MARKER = 'app.organization_import_person_data_grant'


def key():
    return {'Idempotency-Key': str(uuid.uuid4())}


async def contact_setup(world, *, maker='second_admin', grantee='admin', name=('Anna', 'Berg')):
    """The contact pages: `maker` adds the contact Anna Berg (or `name`), prepares its data domain and grants it to `grantee`.

    Every request is the one the page sends (an Idempotency-Key on the domain and on the grant). With no `grantee`
    the domain stays without a grant.
    """
    async with api_client(world, maker) as api:
        person = await api.post('/api/persons', json={'first_name': name[0], 'last_name': name[1]})
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
    """The restore stamps a domain's `created_at` again, and the grant list names the importing administrator by their own name."""
    return [{k: v for k, v in row.items() if k not in {'created_at', 'user_display_name'}} for row in rows]


CLERK_CONTACTS = (('live', 'Anna', 'Berg'), ('revoked', 'Bernd', 'Weber'), ('expired', 'Clara', 'Schmidt'))


async def clerk_contacts(world, db, *, maker='admin'):
    """The contact pages with three contacts, each with a data domain `maker` grants to the clerk.

    Anna Berg's grant stays live, Bernd Weber's is revoked again, and Clara Schmidt's expires: the API takes only an expiry in
    the future, so it is two seconds away and the helper waits, on the database clock the access function reads, until it has
    passed. Returns, per kind, the contact, its domain and its grant as the API answered.
    """
    made = {}
    async with api_client(world, maker) as api:
        for kind, first, last in CLERK_CONTACTS:
            person = await api.post('/api/persons', json={'first_name': first, 'last_name': last})
            assert person.status_code == 201, person.text
            domain = await api.post('/api/person-data-domains', headers=key(), json={
                'person_id': person.json()['id'], 'reason_code': 'TEAM_ACCESS'})
            assert domain.status_code == 201, domain.text
            body = {'user_id': str(world['clerk']), 'capabilities': ['read', 'export'], 'reason_code': 'TEAM_ACCESS'}
            if kind == 'expired':
                body['expires_at'] = (datetime.now(timezone.utc) + timedelta(seconds=2)).isoformat()
            grant = await api.post(f"/api/person-data-domains/{domain.json()['id']}/grants", headers=key(), json=body)
            assert grant.status_code == 201, grant.text
            if kind == 'revoked':
                revoked = await api.post(f"/api/person-data-domains/{domain.json()['id']}/grants/{grant.json()['id']}/revoke")
                assert revoked.status_code == 204, revoked.text
            made[kind] = {'person': person.json()['id'], 'domain': domain.json(), 'grant': grant.json()}
    await db.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:admin,true)"),
                     {'org': str(world['org']), 'admin': str(world[maker])})
    await db.execute(text('SELECT pg_sleep(GREATEST(0,EXTRACT(EPOCH FROM expires_at-clock_timestamp()))+0.05) '
                          'FROM person_data_grant WHERE id=:grant'), {'grant': made['expired']['grant']['id']})
    await db.rollback()
    return made


async def account(db, world, user):
    """The account row behind an id: what the grant list and the user list show about it. `world` names an organization and
    one of its administrators."""
    await db.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:admin,true)"),
                     {'org': str(world['org']), 'admin': str(world['admin'])})
    row = (await db.execute(text('SELECT email,display_name,is_active,password_hash FROM "user" WHERE organization_id=:org AND id=:user'),
                            {'org': world['org'], 'user': uuid.UUID(str(user))})).one()
    await db.rollback()
    return tuple(row)


async def answers(db, org, user, domains, capabilities=('read', 'prepare', 'export')):
    """`has_person_data_capability` per domain, which the contact's source policies call: the capabilities `user` holds.

    Asked as `user` in `org`, on the organization's database clock.
    """
    await db.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:user,true)"),
                     {'org': str(org), 'user': str(user)})
    held = {}
    for domain in domains:
        held[domain] = [name for name in capabilities if await db.scalar(
            text('SELECT has_person_data_capability(:org,:domain,:capability)'), {'org': org, 'domain': uuid.UUID(domain), 'capability': name})]
    await db.rollback()
    return held


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


async def test_a_grant_beside_the_living_source_is_restored_for_its_placeholder_grantee(
    rent_world, import_target, rent_db, export_store, confirmed_second_factor,
):
    """The grantee is a placeholder there too (no archived address is the importing administrator's): the grant keeps it,
    and nothing is refused. The importing administrator, who is not the grantee, gets no access through it."""
    world = await second_administrator(rent_db, rent_world, confirmed_second_factor)
    person, domain, grant = await contact_setup(world)
    source_domains, source_grants = await listed(world, person)
    async with api_client(world) as api:
        archive = await api.post('/api/organizations/export')
    async with api_client(import_target) as api:
        answer = await api.post('/api/organizations/import', files={'file': ('beside-the-source.tar.gz', archive.content)})
    assert answer.status_code == 200, answer.text
    ids = answer.json()['id_map']
    restored_domains, restored_grants = await listed(import_target, ids['person'][person])
    assert without_stamp(restored_domains) == without_stamp(in_target_ids(source_domains, ids))
    assert restored_grants == in_target_ids(source_grants, ids)
    placeholder = ids['user'][str(world['admin'])]
    assert placeholder != str(import_target['admin']) and restored_grants[0]['user_id'] == placeholder
    # The archived address is still the living source's, so the placeholder carries a synthetic one; its name is the archived one.
    assert (await account(rent_db, import_target, placeholder))[1:] == (await account(rent_db, world, world['admin']))[1:2] + (False, '!')
    # Nobody holds the grant's rights in the target: the placeholder is inactive and the importing administrator is not granted.
    domain_id = ids['person_data_domain'][domain['id']]
    assert await answers(rent_db, import_target['org'], placeholder, [domain_id]) == {domain_id: []}
    assert await answers(rent_db, import_target['org'], import_target['admin'], [domain_id]) == {domain_id: []}


async def test_import_restores_a_grant_to_a_clerk_a_revoked_grant_and_an_expired_grant(
    rent_world, import_target, rent_db, export_store,
):
    """The user check of issue #851: the administrator grants the contact data of three contacts to the clerk, one grant is
    revoked again and one has expired. The organization is exported, the source erased and the archive imported into a new
    organization of the same administrator. Every grant equals the source: grantee (by name and address), rights, reason,
    expiry, revocation and the time it was made; the clerk's account is the archived one, deactivated."""
    world = rent_world
    await rent_db.execute(text('UPDATE "user" SET display_name=:name WHERE id=:clerk'), {'name': 'Clara Clerk', 'clerk': world['clerk']})
    await rent_db.commit()
    made = await clerk_contacts(world, rent_db)
    source = {kind: await listed(world, one['person']) for kind, one in made.items()}
    source_clerk = await account(rent_db, world, world['clerk'])
    async with api_client(world) as api:
        archive = await api.post('/api/organizations/export')
        assert archive.status_code == 200, archive.text
    await source_organization_deleted(rent_db, world['org'], world['admin'], import_target)
    async with api_client(import_target) as api:
        answer = await api.post('/api/organizations/import', files={'file': ('grants-to-the-clerk.tar.gz', archive.content)})
        assert answer.status_code == 200, answer.text
    ids = answer.json()['id_map']
    clerk = ids['user'][str(world['clerk'])]
    assert clerk != str(import_target['admin'])
    for kind, one in made.items():
        domains, grants = await listed(import_target, ids['person'][one['person']])
        source_domains, source_grants = source[kind]
        assert [row['id'] for row in domains] == [ids['person_data_domain'][one['domain']['id']]], kind
        assert without_stamp(domains) == without_stamp(in_target_ids(source_domains, ids)), kind
        assert grants == in_target_ids(source_grants, ids), kind
        assert [(row['user_id'], row['user_display_name'], row['capabilities'], row['reason_code']) for row in grants] == [
            (clerk, 'Clara Clerk', ['export', 'read'], 'TEAM_ACCESS')], kind
    live, revoked, expired = (source[kind][1][0] for kind in ('live', 'revoked', 'expired'))
    assert (live['expires_at'], live['revoked_at']) == (None, None)
    assert revoked['revoked_at'] is not None and expired['expires_at'] is not None
    assert datetime.fromisoformat(expired['expires_at']) < datetime.now(timezone.utc)
    # The clerk's account is the archived one, name and address included, and it is shown as deactivated.
    assert await account(rent_db, import_target, clerk) == source_clerk[:2] + (False, '!')
    async with api_client(import_target) as api:
        users = await api.get('/api/users')
    assert users.status_code == 200, users.text
    assert [(row['display_name'], row['is_active'], row['role']) for row in users.json() if row['id'] == clerk] == [
        ('Clara Clerk', False, 'CLERK')]


async def test_a_grant_a_second_administrator_made_to_the_clerk_and_the_first_one_revoked_is_restored(
    rent_world, import_target, rent_db, export_store, confirmed_second_factor,
):
    """The three conditions together, with a grantor who is a placeholder (the case of issue #817): grantor, grantee and
    revoker are three different archived accounts, two of them placeholders after the import."""
    world = await second_administrator(rent_db, rent_world, confirmed_second_factor)
    person, domain, grant = await contact_setup(world, grantee='clerk')
    async with api_client(world) as api:
        revoked = await api.post(f"/api/person-data-domains/{domain['id']}/grants/{grant['id']}/revoke")
        assert revoked.status_code == 204, revoked.text
    source_domains, source_grants = await listed(world, person)
    assert source_grants[0]['revoked_at'] is not None
    async with api_client(world) as api:
        archive = await api.post('/api/organizations/export')
    await source_organization_deleted(rent_db, world['org'], world['admin'], import_target)
    async with api_client(import_target) as api:
        answer = await api.post('/api/organizations/import', files={'file': ('second-administrators-grant.tar.gz', archive.content)})
        assert answer.status_code == 200, answer.text
    ids = answer.json()['id_map']
    restored_domains, restored_grants = await listed(import_target, ids['person'][person])
    assert without_stamp(restored_domains) == without_stamp(in_target_ids(source_domains, ids))
    assert restored_grants == in_target_ids(source_grants, ids)
    found = await actors(rent_db, import_target)
    assert str(found['grant'].actor) == ids['user'][str(world['second_admin'])] and found['grant'].is_active is False
    assert restored_grants[0]['user_id'] == ids['user'][str(world['clerk'])] != str(import_target['admin'])
    # Audit trail of the grant: the archived events keep their actors (the second administrator granted, the first one
    # revoked and is the importing administrator's account now), and the trigger adds its own `grant_prepared` for the restore
    # insert naming the importing administrator, a `grant_prepared` and never a revocation, although the grant is revoked.
    await rent_db.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:admin,true)"),
                          {'org': str(import_target['org']), 'admin': str(import_target['admin'])})
    events = (await rent_db.execute(text("SELECT action,actor_user_id FROM audit_event WHERE organization_id=:org "
                                         "AND entity_type='person_data_grant' AND entity_id=:grant"),
                                    {'org': import_target['org'], 'grant': uuid.UUID(ids['person_data_grant'][grant['id']])})).all()
    await rent_db.rollback()
    importer, second = str(import_target['admin']), ids['user'][str(world['second_admin'])]
    assert sorted((action, str(actor)) for action, actor in events) == sorted([
        ('person_data.grant_prepared', second), ('person_data.grant_revoked', importer), ('person_data.grant_prepared', importer)])


async def test_a_restored_grant_to_a_placeholder_gives_no_access_until_an_administrator_reactivates_the_account(
    rent_world, import_target, rent_db, export_store, confirmed_second_factor,
):
    """What the restored grants mean, per case. Before the export the clerk reads the contact of the live grant and nobody
    else holds a right on it. After the import the clerk's account is an inactive placeholder: it holds none, and no session
    of it is accepted. The importing administrator gains none through grants of other users. An administrator can reactivate
    the placeholder (the user page does, `PATCH /api/users/{id}`); the account then holds exactly the rights of the live grant
    and none of the revoked or the expired one, and it still cannot sign in until a password is set."""
    world = rent_world
    made = await clerk_contacts(world, rent_db)
    domains = {kind: one['domain']['id'] for kind, one in made.items()}
    before = await answers(rent_db, world['org'], world['clerk'], list(domains.values()))
    assert before == {domains['live']: ['read', 'export'], domains['revoked']: [], domains['expired']: []}
    assert await answers(rent_db, world['org'], world['admin'], list(domains.values())) == {domain: [] for domain in domains.values()}
    async with api_client(world) as api:
        archive = await api.post('/api/organizations/export')
    await source_organization_deleted(rent_db, world['org'], world['admin'], import_target)
    async with api_client(import_target) as api:
        answer = await api.post('/api/organizations/import', files={'file': ('grants-to-the-clerk.tar.gz', archive.content)})
        assert answer.status_code == 200, answer.text
    ids = answer.json()['id_map']
    target = import_target['org']
    restored = {kind: ids['person_data_domain'][domain] for kind, domain in domains.items()}
    clerk = ids['user'][str(world['clerk'])]
    nobody = {domain: [] for domain in restored.values()}
    # Inactive placeholder: no right on any of the three contacts, and the session of the account is refused.
    assert await answers(rent_db, target, clerk, list(restored.values())) == nobody
    async with api_client({'org': target, 'clerk': uuid.UUID(clerk)}, 'clerk') as clerk_api:
        assert (await clerk_api.get('/api/person-data-domains/page')).status_code == 401
    # The importing administrator holds none of the clerk's rights, although the grants are listed for them.
    assert await answers(rent_db, target, import_target['admin'], list(restored.values())) == nobody
    # An administrator reactivates the placeholder: the account holds what the live grant gives and nothing else.
    async with api_client(import_target) as api:
        reactivated = await api.patch(f'/api/users/{clerk}', json={'is_active': True})
        assert reactivated.status_code == 200 and reactivated.json()['is_active'] is True, reactivated.text
    assert await answers(rent_db, target, clerk, list(restored.values())) == {
        restored['live']: ['read', 'export'], restored['revoked']: [], restored['expired']: []}
    if settings.deployment_mode == 'hosted':
        # Hosted mode lets an account without a confirmed second factor reach the enrolment routes only (issue #208).
        confirmed_second_factor(target, uuid.UUID(clerk))
    async with api_client({'org': target, 'clerk': uuid.UUID(clerk)}, 'clerk') as clerk_api:
        page = await clerk_api.get('/api/person-data-domains/page')
        assert page.status_code == 200, page.text
        assert [row['id'] for row in page.json()['items']] == [restored['live']]
        # No password was set: the unusable one of a placeholder matches nothing, so the account cannot sign in yet.
        email = await account(rent_db, import_target, clerk)
        login = await clerk_api.post('/api/auth/login', json={'email': email[0], 'password': '!'})
        assert login.status_code == 401, login.text
    # The importing administrator still holds nothing on the clerk's contacts.
    assert await answers(rent_db, target, import_target['admin'], list(restored.values())) == nobody


@pytest.mark.no_db
def test_the_import_names_every_grant_with_its_grantor_and_its_grantee():
    """A grant the importing administrator made may still be a grant to a placeholder, a revoked or an expired one, so the
    import names it whoever made it; a domain is named only where its creator is another account."""
    from app.services.organization_import_service import archived_actor_marker
    importer, other, grantee, row = (str(uuid.uuid4()) for _ in range(4))
    grant = {'id': row, 'granted_by': importer, 'user_id': grantee}
    assert archived_actor_marker('person_data_grant', grant, uuid.UUID(importer)) == (
        GRANT_MARKER, json.dumps({'grant_id': row, 'granted_by': importer, 'user_id': grantee}))
    assert json.loads(archived_actor_marker('person_data_grant', {**grant, 'granted_by': other}, uuid.UUID(importer))[1]) == {
        'grant_id': row, 'granted_by': other, 'user_id': grantee}
    domain = {'id': row, 'created_by': importer}
    assert archived_actor_marker('person_data_domain', domain, uuid.UUID(importer)) is None
    assert archived_actor_marker('person_data_domain', {**domain, 'created_by': other}, uuid.UUID(importer)) == (
        DOMAIN_MARKER, json.dumps({'domain_id': row, 'created_by': other}))


# ---- the trigger itself: direct writes under the runtime role --------------------------------------

REFUSALS = {'domain': 'Domain actor mismatch', 'grant': 'Grant requires matching actor and active member'}
# What the check is asked: the creator of a domain, the grantor of a grant, the grantee of a grant, and the grant itself (its
# revocation and the time it was made). A grantee and a grant's history are read from the same setting as its grantor.
SETTINGS = {'domain': DOMAIN_MARKER, 'grant': GRANT_MARKER, 'grantee': GRANT_MARKER, 'history': GRANT_MARKER}
KEYS = {'domain': ('domain_id', 'created_by'), 'grant': ('grant_id', 'granted_by'), 'grantee': ('grant_id', 'user_id'),
        'history': ('grant_id', None)}
INSERT = {
    'domain': text("""INSERT INTO person_data_domain(id,organization_id,person_id,reason_code,created_by,request_key)
        VALUES (:row,:org,:other_person,'TEAM_ACCESS',:by,:request)"""),
    'grant': text("""INSERT INTO person_data_grant(id,organization_id,domain_id,user_id,capabilities,reason_code,granted_by,
        request_key,created_at,expires_at,revoked_at,revoked_by) VALUES (:row,:org,:domain,:grantee,ARRAY['read'],'TEAM_ACCESS',
        :by,:request,COALESCE(CAST(:created AS timestamptz),now()),CAST(:expires AS timestamptz),
        CASE WHEN CAST(:revoker AS uuid) IS NULL THEN NULL ELSE now() END,:revoker)"""),
}
# A grant as the archive has it: made on 5 January 2026 and expired a day later.
MADE_AT, EXPIRED_AT = datetime(2026, 1, 5, 10, tzinfo=timezone.utc), datetime(2026, 1, 6, 10, tzinfo=timezone.utc)


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


def naming(kind, actor=None, *, row=None, grantee=None):
    """The setting the import makes: the row it restores, its archived actor and, for a grant, its grantee."""
    row_key, actor_key = KEYS[kind]

    def named(this_row):
        value = {row_key: str(row or this_row)}
        if actor_key:
            value[actor_key] = str(actor)
        if grantee is not None:
            value['user_id'] = str(grantee)
        return value
    return named


async def insert(ids, kind, *, marker, by, row=None, user='actor', org=None, grantee='clerk', revoked_by=None,
                 created=None, expires=None, stored=False):
    """One INSERT of a domain or a grant as the runtime role would run it, in a transaction that is rolled back.

    The transaction-local settings are those of a request (organization, signed-in user) plus the import's setting where
    the case has one. Returns the row count, or with `stored` the grant as the table keeps it (`created_at`, `expires_at`,
    `revoked_at`, `revoked_by`); a refusal is raised by the database.
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
                inserted = await connection.execute(INSERT[kind], {
                    'row': row, 'org': ids['org'], 'other_person': ids['other_person'], 'domain': ids['domain'],
                    'grantee': ids[grantee] if isinstance(grantee, str) else grantee,
                    'by': ids[by] if isinstance(by, str) else by, 'request': uuid.uuid4(), 'created': created, 'expires': expires,
                    'revoker': ids[revoked_by] if revoked_by else None})
                if not stored:
                    return inserted.rowcount
                return tuple((await connection.execute(text(
                    'SELECT created_at,expires_at,revoked_at,revoked_by FROM person_data_grant WHERE id=:row'), {'row': row})).one())
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


async def refused(ids, kind, *, message=None, sqlstate='42501', **kwargs):
    with pytest.raises(DBAPIError) as denied:
        await insert(ids, kind, **kwargs)
    assert denied.value.orig.sqlstate == sqlstate and (message or REFUSALS[kind]) in str(denied.value), denied.value


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


# Settings of every shape a bug or a hand could produce; `row` and `actor` stand for the keys of the setting under test.
MALFORMED = [
    pytest.param('{not json', id='malformed'), pytest.param('{"domain_id": ', id='cut-off'), pytest.param('[]', id='array'),
    pytest.param('"text"', id='string'), pytest.param('7', id='number'), pytest.param('null', id='json-null'),
    pytest.param('{}', id='empty-object'), pytest.param('{"row": 7, "actor": 7}', id='numbers'),
    pytest.param('{"row": "not-a-uuid", "actor": "not-a-uuid"}', id='not-uuids'),
    pytest.param('{"row": null, "actor": null}', id='nulls'),
    pytest.param('{"row": "THIS_ROW"}', id='no-actor'), pytest.param('{"actor": "PLACEHOLDER"}', id='no-row')]


def malformed(marker, kind, row, placeholder):
    row_key, actor_key = KEYS[kind]
    return marker.replace('"row"', f'"{row_key}"').replace('"actor"', f'"{actor_key or "unused"}"').replace(
        'THIS_ROW', str(row)).replace('PLACEHOLDER', str(placeholder))


@kinds
@pytest.mark.parametrize('marker', MALFORMED)
async def test_a_missing_or_malformed_setting_is_refused_like_any_other_actor_and_never_raises_another_error(probe, kind, marker):
    """The check answers false for a setting of any shape; the refusal is the trigger's own 42501, never a cast error."""
    row = uuid.uuid4()
    await refused(probe, kind, marker=malformed(marker, kind, row, probe['placeholder']), by='placeholder', row=row)


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
                    'grantee': probe['clerk'], 'by': probe['placeholder'], 'request': uuid.uuid4(), 'created': None, 'expires': None,
                    'revoker': None})
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
async def test_a_setting_that_names_no_grantee_leaves_the_active_member_rule_as_it_was(probe, grantee):
    """The setting of a grant names its grantor and its grantee. One that names only the grantor opens no grantee."""
    marker = naming('grant', probe['placeholder'])
    await refused(probe, 'grant', marker=marker, by='placeholder', grantee=grantee)
    # An active member is accepted as before, so a grant to the importing administrator is restored.
    assert await insert(probe, 'grant', marker=marker, by='placeholder', grantee='actor') == 1


# ---- a grant to a placeholder: the grantee -----------------------------------------------------------

@pytest.mark.parametrize('by', ['actor', 'placeholder'])
async def test_the_trigger_accepts_a_grant_to_a_placeholder_when_the_setting_names_it(probe, by):
    """A clerk is an inactive placeholder after the import. The grantor may be the importing administrator or a placeholder."""
    marker = naming('grant', probe[by], grantee=probe['placeholder'])
    assert await insert(probe, 'grant', marker=marker, by=by, grantee='placeholder') == 1
    # The other active members of the organization were accepted before: the clerk of the fixture is one.
    assert await insert(probe, 'grant', marker=marker, by=by, grantee='clerk') == 1


async def test_the_trigger_refuses_a_grant_to_a_placeholder_unless_the_setting_names_this_row_and_this_grantee(probe, rent_db):
    other = await add_user(rent_db, probe['org'], active=False, password_hash='!')
    row = uuid.uuid4()
    for marker in (None, '', naming('grant', probe['actor'], grantee=probe['placeholder'], row=uuid.uuid4()),
                   naming('grant', probe['actor'], grantee=other), naming('grant', probe['actor'])):
        await refused(probe, 'grant', marker=marker, by='actor', grantee='placeholder', row=row)
    # The same setting names this row and this grantee once it is right.
    assert await insert(probe, 'grant', marker=naming('grant', probe['actor'], grantee=probe['placeholder']),
                        by='actor', grantee='placeholder', row=row) == 1


@pytest.mark.parametrize('account', ['deactivated_colleague', 'foreign_placeholder'])
async def test_the_trigger_accepts_only_a_placeholder_of_this_organization_as_grantee(probe, account):
    """A colleague deactivated in the user list keeps their password and is no placeholder: reactivating them would give
    a real person the access. An account of another organization is none either. Whatever the setting says."""
    await refused(probe, 'grant', marker=naming('grant', probe['actor'], grantee=probe[account]), by='actor', grantee=account)


@pytest.mark.parametrize('marker', MALFORMED)
async def test_a_malformed_setting_is_no_grantee_and_raises_no_other_error(probe, marker):
    row = uuid.uuid4()
    await refused(probe, 'grant', marker=malformed(marker, 'grantee', row, probe['placeholder']), by='actor',
                  grantee='placeholder', row=row)


async def test_the_setting_of_a_domain_names_no_grantee(probe):
    """A domain's setting is read for a domain only: it opens no grant, whatever it names."""
    row, engine = uuid.uuid4(), create_async_engine(APP_RW_URL)
    try:
        with pytest.raises(DBAPIError) as denied:
            async with engine.begin() as connection:
                await connection.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:user,true),"
                                              "set_config(:name,:value,true)"),
                                         {'org': str(probe['org']), 'user': str(probe['actor']), 'name': DOMAIN_MARKER,
                                          'value': json.dumps({'grant_id': str(row), 'user_id': str(probe['placeholder']),
                                                               'domain_id': str(row), 'created_by': str(probe['placeholder'])})})
                await connection.execute(INSERT['grant'], {
                    'row': row, 'org': probe['org'], 'other_person': probe['other_person'], 'domain': probe['domain'],
                    'grantee': probe['placeholder'], 'by': probe['actor'], 'request': uuid.uuid4(), 'created': None, 'expires': None,
                    'revoker': None})
        assert denied.value.orig.sqlstate == '42501' and REFUSALS['grant'] in str(denied.value), denied.value
    finally:
        await engine.dispose()


# ---- a revoked grant and the time it was made: the history of the grant ---------------------------------

@pytest.mark.parametrize('revoker', ['actor', 'placeholder'])
@pytest.mark.parametrize('grantee', ['clerk', 'placeholder'])
async def test_the_trigger_accepts_a_revoked_grant_when_the_setting_names_it(probe, grantee, revoker):
    """The archive carries `revoked_at` and `revoked_by`; the restore keeps both, whoever revoked."""
    marker = naming('grant', probe['actor'], grantee=probe[grantee])
    created, expires, revoked_at, revoked_by = await insert(
        probe, 'grant', marker=marker, by='actor', grantee=grantee, revoked_by=revoker, stored=True)
    assert (expires, revoked_at is not None, revoked_by) == (None, True, probe[revoker])


async def test_the_trigger_refuses_a_revoked_grant_unless_the_setting_names_this_row(probe):
    row = uuid.uuid4()
    for marker in (None, '', naming('grant', probe['actor'], row=uuid.uuid4()), '{"grant_id": 7}', '{not json'):
        await refused(probe, 'grant', marker=marker, by='actor', revoked_by='actor', row=row)
    assert await insert(probe, 'grant', marker=naming('grant', probe['actor']), by='actor', revoked_by='actor', row=row) == 1


async def test_a_grant_the_setting_names_keeps_its_time_a_grant_the_setting_does_not_name_is_stamped(probe):
    """`created_at` is the trigger's own clock on every normal write, and the archive's on the one row the import names."""
    named = await insert(probe, 'grant', marker=naming('grant', probe['actor']), by='actor', created=MADE_AT, stored=True)
    assert named[0] == MADE_AT
    stamped = await insert(probe, 'grant', marker=None, by='actor', created=MADE_AT, stored=True)
    assert stamped[0] > datetime.now(timezone.utc) - timedelta(minutes=5)
    # Another row's setting is no setting for this one.
    other = await insert(probe, 'grant', marker=naming('grant', probe['actor'], row=uuid.uuid4()), by='actor', created=MADE_AT, stored=True)
    assert other[0] > datetime.now(timezone.utc) - timedelta(minutes=5)


async def test_the_table_still_refuses_an_expiry_before_the_grant_was_made_unless_the_setting_names_the_row(probe):
    """The stamp makes the expiry of an expired archived grant precede its creation, which the table forbids (`expires_at >
    created_at`). The setting that keeps the archived `created_at` is the whole remedy: the table itself is unchanged."""
    row = uuid.uuid4()
    for marker in (None, naming('grant', probe['actor'], row=uuid.uuid4())):
        await refused(probe, 'grant', marker=marker, by='actor', created=MADE_AT, expires=EXPIRED_AT, row=row,
                      sqlstate='23514', message='person_data_grant_check1')
    stored = await insert(probe, 'grant', marker=naming('grant', probe['actor']), by='actor', created=MADE_AT, expires=EXPIRED_AT,
                          row=row, stored=True)
    assert stored[:2] == (MADE_AT, EXPIRED_AT)
    # An expiry that does not precede the creation is still refused: the archive is not a way round the table's rule.
    await refused(probe, 'grant', marker=naming('grant', probe['actor']), by='actor', created=EXPIRED_AT, expires=MADE_AT,
                  sqlstate='23514', message='person_data_grant_check1')


async def test_the_trigger_accepts_a_revoked_and_expired_grant_of_two_placeholders(probe):
    """Every condition at once, the case that opened the grantor in issue #817: grantor and grantee are placeholders."""
    marker = naming('grant', probe['placeholder'], grantee=probe['placeholder'])
    stored = await insert(probe, 'grant', marker=marker, by='placeholder', grantee='placeholder', revoked_by='placeholder',
                          created=MADE_AT, expires=EXPIRED_AT, stored=True)
    assert stored[:2] == (MADE_AT, EXPIRED_AT) and stored[2] is not None and stored[3] == probe['placeholder']


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
                    'grantee': probe['clerk'], 'by': probe['actor'], 'request': uuid.uuid4(), 'created': None, 'expires': None,
                    'revoker': None})
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
    """`import_restores_person_data` as the trigger calls it, with the owner's connection (the runtime role cannot call it).

    `marker` is a setting built by `naming`, a raw string, or None for no setting at all; `actor` names the account the
    trigger passes (the creator, the grantor or the grantee) and is None where the check asks for none.
    """
    row = row or uuid.uuid4()
    engine = create_async_engine(MIGRATOR_URL)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                await connection.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:user,true)"),
                                         {'org': str(org or ids['org']), 'user': str(ids[user] if isinstance(user, str) else user)})
                if marker is not None:
                    value = marker(row) if callable(marker) else marker
                    await connection.execute(text('SELECT set_config(:name,:value,true)'),
                                             {'name': SETTINGS[kind], 'value': value if isinstance(value, str) else json.dumps(value)})
                return await connection.scalar(text('SELECT import_restores_person_data(:kind,:row,:org,:actor)'),
                                               {'kind': kind, 'row': row, 'org': ids['org'], 'actor': ids[actor] if actor else None})
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


functions = pytest.mark.parametrize('kind', ['domain', 'grant', 'grantee', 'history'])


@functions
async def test_the_check_wants_an_active_administrator_of_the_organization_in_the_session(probe, kind):
    marker = naming(kind, probe['placeholder'])
    assert await check(probe, kind, marker=marker, actor='placeholder') is True
    for user, org in (('clerk', None), ('foreign_admin', None), ('foreign_admin', probe['foreign_org'])):
        assert await check(probe, kind, marker=marker, actor='placeholder', user=user, org=org) is False, (user, org)
    assert await check(probe, kind, marker=marker, actor='placeholder', user=uuid.uuid4()) is False


@functions
async def test_the_check_wants_the_setting_to_name_this_row(probe, kind):
    assert await check(probe, kind, marker=None, actor='placeholder') is False
    assert await check(probe, kind, marker='', actor='placeholder') is False
    assert await check(probe, kind, marker=naming(kind, probe['placeholder'], row=uuid.uuid4()), actor='placeholder') is False


@pytest.mark.parametrize('kind', ['domain', 'grant', 'grantee'])
async def test_the_check_wants_the_account_to_be_the_one_the_setting_names_and_a_placeholder(probe, kind):
    marker = naming(kind, probe['placeholder'])
    assert await check(probe, kind, marker=marker, actor='placeholder') is True
    for account in ('colleague', 'deactivated_colleague', 'active_unusable', 'foreign_placeholder', 'clerk'):
        assert await check(probe, kind, marker=naming(kind, probe[account]), actor=account) is False, account
        assert await check(probe, kind, marker=marker, actor=account) is False, account


async def test_the_check_of_a_grants_history_names_no_account(probe):
    """The revocation and the creation time of a grant are no account: the setting needs to name the grant only."""
    assert await check(probe, 'history', marker=naming('history'), actor=None) is True
    assert await check(probe, 'history', marker=naming('history'), actor='colleague') is True


@functions
@pytest.mark.parametrize('marker', MALFORMED)
async def test_the_check_answers_false_for_a_setting_of_any_shape_and_never_raises(probe, kind, marker):
    row = uuid.uuid4()
    answer = await check(probe, kind, marker=malformed(marker, kind, row, probe['placeholder']), actor='placeholder', row=row)
    # The one shape that names the row and nothing else is the setting of a grant's history, which names no account.
    assert answer is (kind == 'history' and marker == '{"row": "THIS_ROW"}')


async def test_the_check_answers_false_for_a_kind_it_does_not_know(probe):
    """A session that would pass for a domain or a grant (an active administrator naming a placeholder) gets false for
    a kind the check does not know, so no other caller can use it for a table it was not written for."""
    assert await check(probe, 'person', marker=None, actor='placeholder') is False


# ---- the migrations --------------------------------------------------------------------------------

FUNCTIONS = text("""SELECT p.oid::regprocedure::text,pg_get_functiondef(p.oid),p.proacl::text,p.proconfig,p.proowner,p.prosecdef
    FROM pg_proc p WHERE p.pronamespace='public'::regnamespace ORDER BY 1""")
TRIGGERS = text("""SELECT tgrelid::regclass::text,tgname,pg_get_triggerdef(oid) FROM pg_trigger
    WHERE tgfoid='protect_person_data_setup'::regproc ORDER BY 1""")
GUARD, CHECK = 'protect_person_data_setup()', 'import_restores_person_data(text,uuid,uuid,uuid)'


def body_of(sql):
    """The body between the first pair of dollar quotes of a CREATE statement, whitespace aside."""
    return ' '.join(re.search(r'AS \$\$(.*?END) \$\$;', sql, re.S).group(1).split())


def stored_body(definition):
    """The same body as `pg_get_functiondef` prints it."""
    return ' '.join(definition.split('$function$')[1].split())


def snapshot(db):
    return {row[0]: tuple(row[1:]) for row in db.execute(FUNCTIONS)}, db.execute(TRIGGERS).all()


def test_0202_replaces_the_check_and_the_trigger_function_and_its_downgrade_restores_the_text_of_0201():
    older_migration = importlib.import_module('migrations.versions.0201_import_restores_person_data_actor')
    migration = importlib.import_module('migrations.versions.0202_import_restores_person_data_grants')
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
            savepoint = db.begin_nested()
            try:
                head, head_triggers = snapshot(db)
                migration.downgrade()
                older, older_triggers = snapshot(db)
                # Exactly the two functions change, none is added or dropped, and both triggers stay as they were.
                assert set(head) == set(older) and sorted(name for name in older if head[name] != older[name]) == sorted([CHECK, GUARD])
                assert older_triggers == head_triggers and len(head_triggers) == 2
                # Definer rights, search path, owner and ACL stay those of the functions they replace.
                for name in (CHECK, GUARD):
                    assert head[name][1:] == older[name][1:] and head[name][4] is True, name
                # The check still has definer rights, a fixed search path and no privilege beyond its owner's.
                assert head[CHECK][2] == ['search_path=pg_catalog, pg_temp', 'row_security=off']
                assert head[CHECK][1].strip('{}').split(',') == ['app_migrator=X/app_migrator']
                assert stored_body(older[GUARD][0]) == body_of(older_migration.RESTORING)
                assert stored_body(older[CHECK][0]) == body_of(older_migration.CHECK)
                assert stored_body(head[GUARD][0]) != stored_body(older[GUARD][0])
                assert stored_body(head[CHECK][0]) != stored_body(older[CHECK][0])
                migration.upgrade()
                assert snapshot(db) == (head, head_triggers)
            finally:
                savepoint.rollback()
    finally:
        engine.dispose()


def test_0201_replaces_only_the_trigger_function_and_its_downgrade_restores_the_text_of_0068():
    migration = importlib.import_module('migrations.versions.0201_import_restores_person_data_actor')
    later = importlib.import_module('migrations.versions.0202_import_restores_person_data_grants')
    original = Path(migration.__file__).with_name('0068_person_data_domains.py').read_text()
    # Whitespace aside, the body 0068 wrote between its dollar quotes.
    wrote = ' '.join(re.search(r'CREATE FUNCTION protect_person_data_setup\(\).*?AS \$\$(.*?END) \$\$;', original, re.S).group(1).split())
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
            savepoint = db.begin_nested()
            try:
                # The head is 0202; the state this test is about is the one 0201 left.
                head, head_triggers = snapshot(db)
                later.downgrade()
                at_0201, at_0201_triggers = snapshot(db)
                migration.downgrade()
                older, older_triggers = snapshot(db)
                # Only the trigger function changes, and the check goes with it; both triggers stay as they were.
                assert set(at_0201) - set(older) == {CHECK} and not set(older) - set(at_0201)
                assert [name for name in older if at_0201[name] != older[name]] == [GUARD]
                assert older_triggers == at_0201_triggers and len(at_0201_triggers) == 2
                # Definer rights, search path, owner and ACL are those of the function it replaces.
                assert at_0201[GUARD][1:] == older[GUARD][1:] and at_0201[GUARD][4] is True
                assert DOMAIN_MARKER not in older[GUARD][0] and GRANT_MARKER not in older[GUARD][0]
                assert ' '.join(older[GUARD][0].split('$function$')[1].split()) == wrote
                # The check has definer rights with a fixed search path and no other privilege than its owner's: only the
                # trigger function, which runs as that owner, calls it.
                definition, acl, config, owner, definer = at_0201[CHECK]
                assert definer is True and config == ['search_path=pg_catalog, pg_temp', 'row_security=off']
                assert acl.strip('{}').split(',') == ['app_migrator=X/app_migrator']
                assert DOMAIN_MARKER in definition and GRANT_MARKER in definition
                assert 'import_restores_person_data' in at_0201[GUARD][0]
                migration.upgrade()
                assert snapshot(db) == (at_0201, at_0201_triggers)
                later.upgrade()
                assert snapshot(db) == (head, head_triggers)
            finally:
                savepoint.rollback()
    finally:
        engine.dispose()
