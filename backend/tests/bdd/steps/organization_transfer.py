"""Organization archive steps use HTTP for every product operation."""
import hashlib
import io
import json
import tarfile

import pytest
from pytest_bdd import given, when, then

from tests.bdd.browser import browser_world  # noqa: F401
from tests.test_rent_changes import rent_db, rent_world, api_client  # noqa: F401
from tests.test_organization_export import export_store  # noqa: F401
from tests.signup_support import signup_mail  # noqa: F401


@pytest.fixture
def export_client(world, rent_world, export_store):
    source = api_client(rent_world)
    try:
        yield source
    finally:
        world.run(source.aclose())


@given("a separate organization has rental records and an original for export")
def export_organization(world, rent_world, export_client):
    world.actor = export_client
    world.data["export_source"] = rent_world
    original = b"%PDF-1.4\n% Original for the portable archive\n%%EOF\n"
    answer = world.act("POST", "/api/attachments", data={"property_id": str(rent_world["property"])},
                       files={"file": ("Original.pdf", original, "application/pdf")})
    assert answer.status_code == 201, world.describe_response()
    world.data["export_original"] = original
    world.data["export_document_id"] = answer.json()["id"]


@when("its administrator downloads the organization archive")
def download_archive(world):
    answer = world.act("POST", "/api/organizations/export")
    assert answer.status_code == 200, world.describe_response()
    with tarfile.open(fileobj=io.BytesIO(answer.content), mode="r:gz") as archive:
        world.data["export_files"] = {member.name: archive.extractfile(member).read() for member in archive}
    world.data["export_id"] = answer.headers["X-Export-ID"]


@then("the archive contains its rental records and the unchanged original")
def archive_has_records(world):
    files, source = world.data["export_files"], world.data["export_source"]
    manifest = json.loads(files["manifest.json"])
    for name, digest in manifest["sha256"].items():
        assert hashlib.sha256(files[name]).hexdigest() == digest
    for entity, key in (("property", "property"), ("person", "person"), ("lease_agreement", "lease")):
        rows = [json.loads(line) for line in files[f"entities/{entity}.jsonl"].splitlines()]
        assert str(source[key]) in {row["id"] for row in rows}
    assert files[f"documents/{world.data['export_document_id']}"] == world.data["export_original"]


@then("the archive contains no credentials or data of the other organization")
def archive_is_private(world):
    files, source = world.data["export_files"], world.data["export_source"]
    content = b"".join(files.values())
    assert str(source["foreign_org"]).encode() not in content
    assert str(source["foreign_person"]).encode() not in content
    assert b"unused-synthetic-password" not in content
    assert all("password_hash" not in row for row in
               [json.loads(line) for line in files["entities/user.jsonl"].splitlines()])
    async def refuse_foreign():
        async with api_client(source, foreign=True) as foreign:
            return await foreign.get(f"/api/organizations/export/{world.data['export_id']}")
    assert world.run(refuse_foreign()).status_code == 404


@pytest.fixture
async def empty_import_organization(rent_db, rent_world):
    from tests.test_organization_import import import_target
    # Reuse the same fixture implementation; all product operations use HTTP.
    return await import_target.__wrapped__(rent_db, rent_world)


@when('the archive is imported into an empty organization')
def import_archive(world, empty_import_organization):
    from tests.test_organization_import import repack
    async def restore():
        async with api_client(empty_import_organization) as target:
            return await target.post('/api/organizations/import', files={'file': ('organization.tar.gz', repack(world.data['export_files']))})
    answer = world.run(restore())
    assert answer.status_code == 200, answer.text
    world.data['import_result'] = answer.json()
    world.data['import_target'] = empty_import_organization


@then('the imported organization has equal entity counts and normalized content')
def import_equals_source(world):
    from app.services.organization_archive import json_bytes
    source = world.data['export_files']
    async def export_target():
        async with api_client(world.data['import_target']) as target:
            return await target.post('/api/organizations/export')
    answer = world.run(export_target())
    assert answer.status_code == 200, answer.text
    from tests.test_organization_export import unpack
    target = unpack(answer.content)
    # Account credentials/custody and operation audit are deliberately local.
    # Every domain entity is compared after reversing its transfer identity map.
    mapping = world.data['import_result']['id_map']
    from app.services.organization_import_service import remap
    reverse = {v: k for values in mapping.values() for k, v in values.items()}
    for name, count in json.loads(source['manifest.json'])['row_counts'].items():
        actual_count = json.loads(target['manifest.json'])['row_counts'][name]
        assert actual_count == count + (1 if name == 'audit_event' else 0), name
        if name in {'user', 'membership', 'audit_event'}:
            continue
        original = [json.loads(line) for line in source[f'entities/{name}.jsonl'].splitlines()]
        restored = [remap(json.loads(line), reverse) for line in target[f'entities/{name}.jsonl'].splitlines()]
        assert len(restored) == count, name
        def normalize(rows):
            return sorted(json_bytes({k: v for k, v in row.items() if k not in {'created_by', 'storage_uri', 'registered_at'}}) for row in rows)
        assert hashlib.sha256(b''.join(normalize(original))).digest() == hashlib.sha256(b''.join(normalize(restored))).digest(), name


@when('a required person is removed from the archive before import')
def missing_person(world, empty_import_organization):
    from tests.test_organization_import import repack
    files = dict(world.data['export_files'])
    files['entities/person.jsonl'] = b''
    manifest = json.loads(files['manifest.json'])
    manifest['row_counts']['person'] = 0
    files['manifest.json'] = json.dumps(manifest).encode()
    async def restore():
        async with api_client(empty_import_organization) as target:
            return await target.post('/api/organizations/import', files={'file': ('organization.tar.gz', repack(files))})
    world.data['missing_import'] = world.run(restore())
    world.data['import_target'] = empty_import_organization


@then('the import lists missing dependencies and writes nothing')
def missing_dependency_refusal(world):
    answer = world.data['missing_import']
    assert answer.status_code == 422, answer.text
    assert any(item['entity'] == 'person' and item['count'] == 2 for item in answer.json()['detail']['missing_dependencies'])
    async def properties():
        async with api_client(world.data['import_target']) as target:
            return await target.get('/api/properties')
    assert world.run(properties()).json() == []


@when('the administrator inspects the import refusal in the browser')
def browser_import_refusal(world, browser_world, empty_import_organization):
    import uuid
    from playwright.async_api import expect
    from tests.test_organization_import import repack
    email = f"import-browser-{uuid.uuid4()}@example.invalid"
    password = 'Synthetic-Import-Browser-123'
    async def prepare_user():
        async with api_client(empty_import_organization) as api:
            answer = await api.post('/api/users', json={'email': email, 'password': password,
                'display_name': 'Synthetische Importprüfung', 'role': 'OWNER_ADMIN'})
            assert answer.status_code == 201, answer.text
    world.run(prepare_user())
    files = dict(world.data['export_files'])
    files['entities/person.jsonl'] = b''
    manifest = json.loads(files['manifest.json'])
    manifest['row_counts']['person'] = 0
    files['manifest.json'] = json.dumps(manifest).encode()
    archive = repack(files)

    async def operate():
        page = browser_world.page
        await browser_world.visit('/users')
        await page.get_by_role('button', name='Abmelden', exact=True).click()
        await page.get_by_label('E-Mail', exact=True).fill(email)
        await page.get_by_label('Passwort', exact=True).fill(password)
        await page.get_by_role('button', name='Anmelden', exact=True).click()
        await expect(page.get_by_role('link', name='Objekte', exact=True)).to_be_visible()
        await browser_world.visit('/users')
        await page.get_by_label('Exportarchiv', exact=True).set_input_files({
            'name': 'synthetic-organization.tar.gz', 'mimeType': 'application/gzip', 'buffer': archive})
        await page.get_by_role('button', name='Daten importieren', exact=True).click()
        alert = page.get_by_role('alert')
        await expect(alert).to_contain_text('Fehlende Abhängigkeiten')
        await expect(alert).not_to_contain_text('tenant_profile')
        disclosure = page.locator('details').filter(has=page.get_by_text('Technische Details', exact=True))
        await expect(disclosure).not_to_have_attribute('open', '')
        await disclosure.get_by_text('Technische Details', exact=True).click()
        await expect(disclosure).to_contain_text('person')
        await expect(disclosure).to_contain_text(str(world.data['export_source']['person']))
        for width, height, label in ((1280, 1000, 'desktop'), (360, 844, '360')):
            await page.set_viewport_size({'width': width, 'height': height})
            if width == 360:
                await expect(page.locator('aside').filter(has=page.locator('nav'))).not_to_be_in_viewport()
            assert await page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            await page.screenshot(path=f'/tmp/issue23-import-error-{label}.png', full_page=True)
        assert await page.get_by_label('Exportarchiv').input_value()
        assert not browser_world.errors
    browser_world.run(operate())


@then('the import explanation and technical details fit desktop and phone width')
def browser_import_geometry(browser_world):
    assert not browser_world.errors


@when('its administrator requests the retention preview')
def deletion_preview(world):
    answer = world.act('POST', '/api/organizations/deletion')
    assert answer.status_code == 200, world.describe_response()
    world.data['deletion_preview'] = answer.json()


@then('the preview offers a fresh export and explains operator-only retention')
def deletion_notice(world):
    preview = world.data['deletion_preview']
    assert preview['export_url'] == '/api/organizations/export'
    assert 'Nach der Löschung ist keine Anmeldung mehr möglich' in preview['notice']
    assert 'beim Betreiber' in preview['notice']
    assert preview['retention']['retained']['attachment']['count'] == 1


@when('the administrator confirms the exact organization name')
def confirm_deletion(world):
    answer = world.act('POST', '/api/organizations/deletion/confirm', json={'organization_name': world.data['deletion_preview']['organization_name']})
    assert answer.status_code == 200, world.describe_response()


@then('the organization is read-only during cooling-off')
def readonly_deletion(world):
    answer = world.act('POST', '/api/persons', json={'last_name': 'Blocked'})
    assert answer.status_code == 409, world.describe_response()


@when('the administrator revokes the deletion')
def revoke_deletion(world):
    answer = world.act('POST', '/api/organizations/deletion/cancel')
    assert answer.status_code == 200, world.describe_response()


@then('the organization accepts new personal records again')
def writable_deletion(world):
    answer = world.act('POST', '/api/persons', json={'last_name': 'Allowed'})
    assert answer.status_code == 201, world.describe_response()


@when('the operator executes deletion after seven days')
def execute_deletion(world, rent_db):
    from app.services.organization_deletion_service import run_deletion
    from datetime import datetime, timedelta, timezone
    async def execute():
        async with rent_db.begin():
            return await run_deletion(rent_db, world.data['export_source']['org'], now=datetime.now(timezone.utc) + timedelta(days=8))
    assert world.run(execute())


@then('the original remains unchanged and the former owner cannot sign in')
def erased_organization(world, export_store):
    source = world.data['export_source']
    assert (answer := world.act('GET', '/api/auth/session')).status_code == 401, answer.text
    files = list((export_store._root / str(source['org'])).rglob('*.bin'))
    assert len(files) == 1 and files[0].read_bytes() == world.data['export_original']


@when('the destination administrator confirms and revokes deletion')
def destination_deletion(world):
    async def lifecycle():
        async with api_client(world.data['import_target']) as target:
            preview = await target.post('/api/organizations/deletion')
            assert preview.status_code == 200, preview.text
            confirmed = await target.post('/api/organizations/deletion/confirm', json={'organization_name': preview.json()['organization_name']})
            assert confirmed.status_code == 200, confirmed.text
            cancelled = await target.post('/api/organizations/deletion/cancel')
            assert cancelled.status_code == 200, cancelled.text
    world.run(lifecycle())


@then('the source organization still has its original records and session')
def source_unchanged(world):
    async def source():
        async with api_client(world.data['export_source']) as client:
            assert (await client.get('/api/auth/session')).status_code == 200
            people = await client.get('/api/persons')
            assert people.status_code == 200 and len(people.json()) == 2
            properties = await client.get('/api/properties')
            assert properties.status_code == 200 and len(properties.json()) == 2
    world.run(source())


@pytest.fixture
def browser_deletion_organization(world, signup_mail, monkeypatch):
    import uuid
    import psycopg2
    from app.core.config import settings
    from tests.conftest import MIGRATOR_SYNC_URL, clear_disposable_source_context
    from tests.signup_support import BODY, confirmation_token

    monkeypatch.setattr(settings, 'deployment_mode', 'hosted')
    body = BODY | {'email': f'deletion-{uuid.uuid4().hex}@example.test',
                   'organization_name': 'Hausverwaltung mit langem Namen GmbH'}
    assert world.send(world.anonymous, 'POST', '/api/signup', json=body).status_code == 202
    answer = world.send(world.anonymous, 'POST', '/api/signup/confirm',
                        json={'token': confirmation_token(signup_mail), 'password': body['password']})
    assert answer.status_code == 201, answer.text
    try:
        yield body
    finally:
        # Reuse the guarded disposable-DB cleanup for retained originals; normal
        # DELETE cannot remove immutable attachment/source evidence. The runner
        # owns the shared DB lock. Remove this signup's organization as well,
        # including when the UI failed before cancellation.
        with psycopg2.connect(MIGRATOR_SYNC_URL) as connection, connection.cursor() as cursor:
            org = answer.json()['organization_id']
            cursor.execute('DELETE FROM organization_deletion WHERE organization_id=%s',
                           (org,))
            clear_disposable_source_context(cursor)
            for table in ('audit_event', 'membership', 'management_mandate', 'user'):
                cursor.execute(f'DELETE FROM public."{table}" WHERE organization_id=%s', (org,))
            cursor.execute('DELETE FROM organization WHERE id=%s', (org,))
            cursor.execute('SELECT count(*) FROM organization WHERE id=%s', (org,))
            assert cursor.fetchone() == (0,)


@when('the administrator reviews and cancels deletion in the browser')
def browser_lifecycle(browser_world, browser_deletion_organization, tmp_path):
    import os
    from tests.bdd.browser import ROOT
    from playwright.async_api import expect
    screenshots = ROOT / 'docs/assets/design' if os.environ.get('VERWALINO_CAPTURE_DESIGN') == '1' else tmp_path
    async def operate():
        page = browser_world.page
        credentials = browser_deletion_organization
        login = await page.request.post('/api/auth/login', data={
            'email': credentials['email'], 'password': credentials['password']})
        assert login.status == 200, await login.text()
        prop = await page.request.post('/api/properties', data={
            'name': 'Wohnhaus mit langem synthetischem Namen', 'management_type': 'MIET'})
        assert prop.status == 201, await prop.text()
        original = await page.request.post('/api/attachments', multipart={
            'property_id': (await prop.json())['id'],
            'file': {'name': 'Synthetischer Nachweis.pdf', 'mimeType': 'application/pdf',
                     'buffer': b'%PDF-1.4\n% Synthetic lifecycle original\n%%EOF\n'},
        })
        assert original.status == 201, await original.text()
        await browser_world.visit('/users')
        await expect(page.get_by_role('button', name='Organisation löschen', exact=True)).to_be_visible()
        await page.set_viewport_size({'width': 1280, 'height': 1000})
        await page.screenshot(path=screenshots / 'organization-data-desktop.png', full_page=True)
        await page.get_by_role('button', name='Organisation löschen', exact=True).click()
        dialog = page.get_by_role('dialog', name='Organisation löschen', exact=True)
        await expect(dialog.get_by_role('button', name='Jetzt frischen Export erstellen', exact=True)).to_be_visible()
        await page.screenshot(path=screenshots / 'organization-deletion-desktop.png', full_page=True)
        await page.set_viewport_size({'width': 360, 'height': 844})
        await page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
        box = await dialog.bounding_box()
        await page.screenshot(path=screenshots / 'organization-deletion-360.png', full_page=True)
        assert box and box['y'] >= 0 and box['y'] + box['height'] <= 844, box
        export_box = await dialog.get_by_role('button', name='Jetzt frischen Export erstellen', exact=True).bounding_box()
        assert export_box and export_box['y'] >= 0 and export_box['y'] + export_box['height'] <= 844, export_box
        assert await page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        organization = await page.request.get('/api/organizations/me')
        name = (await organization.json())['name']
        await dialog.get_by_label('Organisationsname zur Bestätigung').fill(name)
        await dialog.get_by_role('button', name='Löschung bestätigen', exact=True).click()
        await expect(dialog).not_to_be_visible()
        banner = page.get_by_role('status').filter(has_text='schreibgeschützt')
        await expect(banner).to_be_visible()
        await page.screenshot(path=screenshots / 'organization-cooling-off-360.png', full_page=True)
        await page.get_by_role('button', name='Löschung widerrufen', exact=True).click()
        await expect(banner).not_to_be_visible()
        await page.screenshot(path=screenshots / 'organization-export-360.png', full_page=True)
    browser_world.run(operate())


@then('the organization controls fit desktop and phone width')
def browser_lifecycle_geometry(browser_world):
    from playwright.async_api import expect
    async def verify():
        page = browser_world.page
        assert await page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        await expect(page.get_by_role('button', name='Organisation löschen', exact=True)).to_be_visible()
        assert not browser_world.errors
    browser_world.run(verify())
