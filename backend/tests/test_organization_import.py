"""Portable archive validation and transactional, tenant-scoped restoration."""
import hashlib
import io
import json
import tarfile
import uuid

import pytest
from sqlalchemy import text

from tests.test_rent_changes import rent_db, rent_world, api_client  # noqa: F401
from tests.test_organization_export import export_store, unpack  # noqa: F401


def repack(files):
    manifest = json.loads(files['manifest.json'])
    manifest['sha256'] = {name: hashlib.sha256(data).hexdigest()
                          for name, data in files.items() if name != 'manifest.json'}
    files = {**files, 'manifest.json': json.dumps(manifest).encode()}
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w:gz') as archive:
        for name, data in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    return output.getvalue()


@pytest.fixture
async def import_target(rent_db, rent_world):
    target = {'org': uuid.uuid4(), 'admin': uuid.uuid4()}
    # A fresh install's administrator uses the same mailbox as the source admin.
    email = await rent_db.scalar(text('SELECT email FROM "user" WHERE id=:id'), {'id': rent_world['admin']})
    await rent_db.execute(text("INSERT INTO organization(id,name) VALUES (:org,'Empty target')"), target)
    await rent_db.execute(text('''INSERT INTO "user"(id,organization_id,email,password_hash,display_name)
        VALUES (:admin,:org,:email,'keep-this-account','Target admin')'''), {**target, 'email': email})
    await rent_db.execute(text("INSERT INTO membership(organization_id,user_id,role) VALUES (:org,:admin,'OWNER_ADMIN')"), target)
    await rent_db.commit()
    return target


async def exported(w):
    async with api_client(w) as api:
        answer = await api.post('/api/organizations/export')
        assert answer.status_code == 200, answer.text
        return answer.content


async def test_import_remaps_collisions_and_preserves_source_identity(rent_world, import_target, rent_db, export_store):
    archive = await exported(rent_world)
    async with api_client(import_target) as api:
        answer = await api.post('/api/organizations/import', files={'file': ('organization.tar.gz', archive)})
        assert answer.status_code == 200, answer.text
        result = answer.json()
        assert result['row_counts']['property'] == 2
        mapped = result['id_map']['property'][str(rent_world['property'])]
        assert mapped != str(rent_world['property'])
        again = await api.post('/api/organizations/export')
        assert again.status_code == 200, again.text
        files = unpack(again.content)
        rows = [json.loads(line) for line in files['entities/property.jsonl'].splitlines()]
        assert {row['source_id'] for row in rows} == {str(rent_world['property']), str(rent_world['hidden_property'])}
    assert await rent_db.scalar(text('SELECT password_hash FROM "user" WHERE id=:admin'), import_target) == 'keep-this-account'
    people = (await rent_db.execute(text('SELECT is_active,password_hash FROM "user" WHERE organization_id=:org AND id<>:admin'), import_target)).all()
    assert len(people) == 2 and all(not active and password == '!' for active, password in people)
    await rent_db.rollback()


async def test_import_lists_missing_dependencies_and_writes_nothing(rent_world, import_target, rent_db, export_store, caplog):
    files = unpack(await exported(rent_world))
    files['entities/person.jsonl'] = b''
    manifest = json.loads(files['manifest.json'])
    manifest['row_counts']['person'] = 0
    files['manifest.json'] = json.dumps(manifest).encode()
    async with api_client(import_target) as api:
        answer = await api.post('/api/organizations/import', files={'file': ('organization.tar.gz', repack(files))})
    assert answer.status_code == 422, answer.text
    missing = answer.json()['detail']['missing_dependencies']
    assert any(item['entity'] == 'person' and str(rent_world['person']) in item['source_ids'] for item in missing)
    assert 'Fehlende Abhängigkeiten' in answer.json()['detail']['message']
    assert 'missing_dependencies' in caplog.text
    assert await rent_db.scalar(text('SELECT count(*) FROM property WHERE organization_id=:org'), import_target) == 0
    await rent_db.rollback()


async def test_import_refuses_nonempty_target_and_bad_hash(rent_world, import_target, rent_db, export_store):
    archive = await exported(rent_world)
    async with api_client(rent_world) as source:
        assert (await source.post('/api/organizations/import', files={'file': ('org.tar.gz', archive)})).status_code == 409
    files = unpack(archive)
    files['entities/person.jsonl'] += b'\n'
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w:gz') as tar:
        for name, data in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            tar.addfile(member, io.BytesIO(data))
    async with api_client(import_target) as api:
        answer = await api.post('/api/organizations/import', files={'file': ('org.tar.gz', output.getvalue())})
        assert answer.status_code == 422, answer.text


@pytest.mark.parametrize('preexisting', [False, True])
async def test_import_restores_originals_and_cleans_files_after_late_failure(rent_world, import_target, rent_db, export_store, monkeypatch, preexisting):
    from app.services import organization_import_service as service
    async with api_client(rent_world) as api:
        response = await api.post('/api/attachments', data={'property_id': str(rent_world['property'])},
                                  files={'file': ('original.pdf', b'%PDF-1.4\nportable original\n%%EOF', 'application/pdf')})
        assert response.status_code == 201, response.text
    archive = await exported(rent_world)
    from app.core.storage import make_storage_key
    content = b'%PDF-1.4\nportable original\n%%EOF'
    key = make_storage_key(str(import_target['org']), hashlib.sha256(content).hexdigest())
    if preexisting:
        export_store.put(key, content, content_type='application/pdf')
    import psycopg2
    from contextlib import closing
    from tests.conftest import MIGRATOR_SYNC_URL
    locks_during_cleanup = []
    discard = export_store.discard_uncommitted
    def discard_with_admission_held(key):
        with closing(psycopg2.connect(MIGRATOR_SYNC_URL)) as connection, connection.cursor() as cursor:
            cursor.execute('SELECT pg_try_advisory_xact_lock_shared(hashtextextended(%s,0))',
                           (f"scope-acl:{import_target['org']}",))
            locks_during_cleanup.append(cursor.fetchone()[0])
        discard(key)
    monkeypatch.setattr(export_store, 'discard_uncommitted', discard_with_admission_held)
    original = service.put_document
    async def write_then_fail(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError('synthetic late failure')
    monkeypatch.setattr(service, 'put_document', write_then_fail)
    async with api_client(import_target) as api:
        with pytest.raises(RuntimeError, match='synthetic late failure'):
            await api.post('/api/organizations/import', files={'file': ('org.tar.gz', archive)})
    assert export_store.exists(key) == preexisting
    assert locks_during_cleanup == ([] if preexisting else [False])
    monkeypatch.setattr(service, 'put_document', original)
    async with api_client(import_target) as api:
        response = await api.post('/api/organizations/import', files={'file': ('org.tar.gz', archive)})
        assert response.status_code == 200, response.text
    assert len(list((export_store._root / str(import_target['org'])).rglob('*.bin'))) == 1


async def test_import_supports_v1_and_refuses_newer_schema(rent_world, import_target, export_store):
    files = unpack(await exported(rent_world))
    manifest = json.loads(files['manifest.json'])
    manifest['schema_head'] = '9999'
    files['manifest.json'] = json.dumps(manifest).encode()
    async with api_client(import_target) as api:
        response = await api.post('/api/organizations/import', files={'file': ('org.tar.gz', repack(files))})
        assert response.status_code == 422
        manifest['schema_head'] = '0146'
        manifest['version'] = 1
        files['manifest.json'] = json.dumps(manifest).encode()
        for name in manifest['entity_order']:
            rows = [json.loads(line) for line in files[f'entities/{name}.jsonl'].splitlines()]
            files[f'entities/{name}.jsonl'] = b''.join(json.dumps({k: v for k, v in row.items() if k != 'source_id'}).encode()+b'\n' for row in rows)
        response = await api.post('/api/organizations/import', files={'file': ('org.tar.gz', repack(files))})
        assert response.status_code == 200, response.text


async def test_import_keeps_collision_free_entity_ids(rent_world, import_target, export_store):
    from app.services.organization_import_service import remap
    files = unpack(await exported(rent_world))
    manifest = json.loads(files['manifest.json'])
    fresh_id = str(uuid.uuid4())
    mapping = {str(rent_world['property']): fresh_id}
    for name in manifest['entity_order']:
        rows = [json.loads(line) for line in files[f'entities/{name}.jsonl'].splitlines()]
        files[f'entities/{name}.jsonl'] = b''.join(json.dumps(remap(row, mapping)).encode()+b'\n' for row in rows)
    async with api_client(import_target) as api:
        answer = await api.post('/api/organizations/import', files={'file': ('org.tar.gz', repack(files))})
        assert answer.status_code == 200, answer.text
        assert answer.json()['id_map']['property'][fresh_id] == fresh_id


async def test_deferred_import_refusal_erases_new_files_before_releasing_admission(rent_world, import_target, rent_db, export_store):
    async with api_client(rent_world) as api:
        response = await api.post('/api/attachments', data={'property_id': str(rent_world['property'])},
                                  files={'file': ('original.pdf', b'%PDF-1.4\ndeferred original\n%%EOF', 'application/pdf')})
        assert response.status_code == 201, response.text
    archive = await exported(rent_world)
    await rent_db.execute(text(f'''CREATE FUNCTION reject_synthetic_import() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN IF NEW.organization_id='{import_target['org']}'::uuid THEN
        RAISE EXCEPTION 'Synthetic deferred import refusal' USING ERRCODE='23514';
      END IF; RETURN NEW; END $$'''))
    await rent_db.execute(text('''CREATE CONSTRAINT TRIGGER reject_synthetic_import AFTER INSERT ON attachment
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION reject_synthetic_import()'''))
    await rent_db.commit()
    try:
        async with api_client(import_target) as api:
            answer = await api.post('/api/organizations/import', files={'file': ('org.tar.gz', archive)})
        assert answer.status_code == 422, answer.text
        assert answer.json()['detail']['code'] == 'IMPORT_INTEGRITY_REFUSAL'
        assert not list((export_store._root / str(import_target['org'])).rglob('*.bin'))
    finally:
        await rent_db.rollback()
        async with rent_db.begin():
            await rent_db.execute(text('DROP TRIGGER reject_synthetic_import ON attachment'))
            await rent_db.execute(text('DROP FUNCTION reject_synthetic_import()'))


@pytest.mark.parametrize('actor', ['reader', 'clerk'])
async def test_import_and_deletion_require_owner_administrator(rent_world, actor, export_store):
    async with api_client(rent_world, actor) as api:
        for path in ('/api/organizations/import', '/api/organizations/deletion', '/api/organizations/deletion/cancel'):
            assert (await api.post(path, files={'file': ('invalid.tar.gz', b'invalid')})).status_code == 403


async def test_import_refuses_false_original_size_without_rows_or_files(rent_world, import_target, rent_db, export_store):
    async with api_client(rent_world) as api:
        answer = await api.post('/api/attachments', data={'property_id': str(rent_world['property'])},
                                files={'file': ('original.pdf', b'%PDF-1.4\nActual original bytes\n%%EOF', 'application/pdf')})
        assert answer.status_code == 201, answer.text
    files = unpack(await exported(rent_world))
    rows = [json.loads(line) for line in files['entities/attachment.jsonl'].splitlines()]
    rows[0]['content_byte_size'] = 0
    files['entities/attachment.jsonl'] = b''.join(json.dumps(row).encode()+b'\n' for row in rows)
    async with api_client(import_target) as api:
        answer = await api.post('/api/organizations/import', files={'file': ('org.tar.gz', repack(files))})
        assert answer.status_code == 422, answer.text
    assert not list((export_store._root / str(import_target['org'])).rglob('*.bin'))
    assert await rent_db.scalar(text('SELECT count(*) FROM property WHERE organization_id=:org'), import_target) == 0
    await rent_db.rollback()


async def test_import_refuses_a_forged_negative_quota_recount(rent_world, import_target, rent_db, export_store):
    files = unpack(await exported(rent_world))
    events = [json.loads(line) for line in files['entities/audit_event.jsonl'].splitlines()]
    events.append({'id': str(uuid.uuid4()), 'organization_id': str(rent_world['org']),
                   'entity_type': 'organization', 'entity_id': str(rent_world['org']),
                   'actor_user_id': str(rent_world['admin']), 'action': 'quota_recount',
                   'payload_diff': {'storage_uri': 'made-up-uri', 'bytes': -2147483648}})
    files['entities/audit_event.jsonl'] = b''.join(json.dumps(row).encode()+b'\n' for row in events)
    manifest = json.loads(files['manifest.json'])
    manifest['row_counts']['audit_event'] = len(events)
    files['manifest.json'] = json.dumps(manifest).encode()
    async with api_client(import_target) as api:
        answer = await api.post('/api/organizations/import', files={'file': ('org.tar.gz', repack(files))})
        assert answer.status_code == 422, answer.text
        assert answer.json()['detail']['entity'] == 'audit_event'
    assert await rent_db.scalar(text('SELECT count(*) FROM audit_event WHERE organization_id=:org'), import_target) == 0
    await rent_db.rollback()


async def test_invalid_organization_metadata_is_a_refusal_and_preserves_target(rent_world, import_target, rent_db, export_store):
    files = unpack(await exported(rent_world))
    row = json.loads(files['entities/organization.jsonl'].splitlines()[0])
    row['name'] = None
    files['entities/organization.jsonl'] = json.dumps(row).encode()+b'\n'
    async with api_client(import_target) as api:
        answer = await api.post('/api/organizations/import', files={'file': ('org.tar.gz', repack(files))})
        assert answer.status_code == 422, answer.text
        assert answer.json()['detail']['entity'] == 'organization'
    assert await rent_db.scalar(text('SELECT name FROM organization WHERE id=:org'), import_target) == 'Empty target'
    await rent_db.rollback()


async def test_relinked_nonadmin_account_is_inactive_and_requires_a_reset(rent_world, import_target, rent_db, export_store):
    email = await rent_db.scalar(text('SELECT email FROM "user" WHERE id=:clerk'), rent_world)
    existing_id = uuid.uuid4()
    await rent_db.execute(text('''INSERT INTO "user"(id,organization_id,email,password_hash,display_name)
      VALUES (:id,:org,:email,'previous-password','Existing clerk')'''), {**import_target, 'id': existing_id, 'email': email})
    await rent_db.execute(text("INSERT INTO membership(organization_id,user_id,role) VALUES (:org,:id,'CLERK')"), {**import_target, 'id': existing_id})
    await rent_db.commit()
    async with api_client(import_target) as api:
        answer = await api.post('/api/organizations/import', files={'file': ('org.tar.gz', await exported(rent_world))})
        assert answer.status_code == 200, answer.text
        assert answer.json()['id_map']['user'][str(rent_world['clerk'])] == str(existing_id)
    assert (await rent_db.execute(text('SELECT is_active,password_hash FROM "user" WHERE id=:id'), {'id': existing_id})).one() == (False, '!')
    await rent_db.rollback()


async def test_private_invoice_admission_is_listed_as_a_missing_dependency(rent_world, import_target, rent_db, export_store):
    from app.core.storage import make_storage_key
    files = unpack(await exported(rent_world))
    identity = str(uuid.uuid4())
    original = b'%PDF-1.4\nPortable invoice original\n%%EOF'
    digest = hashlib.sha256(original).hexdigest()
    row = {'id': identity, 'source_id': identity, 'organization_id': str(rent_world['org']),
           'uploaded_by': str(rent_world['admin']), 'cost_entry_id': None, 'version': 1,
           'chain_root_id': identity, 'status': 'active', 'storage_backend': 'LOCAL_FS',
           'storage_uri': make_storage_key(str(rent_world['org']), digest),
           'original_filename': 'invoice.pdf', 'mime_type': 'application/pdf',
           'content_byte_size': len(original), 'content_sha256': digest}
    files['entities/invoice_document.jsonl'] = json.dumps(row).encode()+b'\n'
    files[f'documents/{identity}'] = original
    manifest = json.loads(files['manifest.json'])
    manifest['row_counts']['invoice_document'] = 1
    manifest['documents'].append({'entity': 'invoice_document', 'id': identity, 'member': f'documents/{identity}'})
    files['manifest.json'] = json.dumps(manifest).encode()
    async with api_client(import_target) as api:
        answer = await api.post('/api/organizations/import', files={'file': ('org.tar.gz', repack(files))})
        assert answer.status_code == 422, answer.text
        assert {'entity': 'invoice_upload_admission', 'source_ids': [identity], 'count': 1} in answer.json()['detail']['missing_dependencies']
    assert await rent_db.scalar(text('SELECT count(*) FROM property WHERE organization_id=:org'), import_target) == 0
    await rent_db.rollback()
