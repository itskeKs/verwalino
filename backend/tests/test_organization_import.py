"""Portable archive validation and transactional, tenant-scoped restoration."""
import hashlib
import io
import json
import tarfile
import uuid

import pytest
from sqlalchemy import text

from tests.test_rent_changes import rent_db as rent_db, rent_world as rent_world, api_client
from tests.test_organization_export import export_store as export_store, unpack


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


def archive_with_user_email(files, user_id, email):
    """The archive with one user's sign-in address replaced."""
    rows = []
    for line in files['entities/user.jsonl'].splitlines():
        row = json.loads(line)
        if row['id'] == str(user_id):
            row['email'] = email
        rows.append(json.dumps(row).encode() + b'\n')
    return {**files, 'entities/user.jsonl': b''.join(rows)}


@pytest.fixture
async def import_target(rent_db, rent_world):
    target = {'org': uuid.uuid4(), 'admin': uuid.uuid4(), 'email': f"target-{uuid.uuid4().hex}@rent-change.invalid"}
    # The target administrator has their own sign-in address: one address
    # belongs to one organization (issue #134), so every archived address is
    # still owned by the live source organization and the imported
    # placeholders receive deterministic synthetic addresses.
    await rent_db.execute(text("INSERT INTO organization(id,name) VALUES (:org,'Empty target')"), target)
    await rent_db.execute(text('''INSERT INTO "user"(id,organization_id,email,password_hash,display_name)
        VALUES (:admin,:org,:email,'keep-this-account','Target admin')'''), target)
    await rent_db.execute(text("INSERT INTO membership(organization_id,user_id,role) VALUES (:org,:admin,'OWNER_ADMIN')"), target)
    await rent_db.commit()
    return target


async def exported(w):
    async with api_client(w) as api:
        answer = await api.post('/api/organizations/export')
        assert answer.status_code == 200, answer.text
        return answer.content


async def source_organization_deleted(rent_db, source_org, source_admin, target):
    """The supported restore: the archive was taken, then the source organization was deleted.

    Deletion rewrites every account address of the deleted organization to
    `<id>@deleted.invalid` (organization_deletion_service.py), which frees the
    archived administrator's address. The importing administrator's account
    carries it, so the address match maps the archived administrator to the
    session user, whose actor columns the insert policies accept.
    """
    email = await rent_db.scalar(text('SELECT email FROM "user" WHERE id=:id'), {'id': source_admin})
    await rent_db.execute(text("""UPDATE "user" SET email=id::text||'@deleted.invalid'
        WHERE organization_id=:org"""), {'org': source_org})
    await rent_db.execute(text('UPDATE "user" SET email=:email WHERE id=:admin'),
                          {'email': email, 'admin': target['admin']})
    await rent_db.commit()


@pytest.mark.parametrize('scoped', [False, True])
@pytest.mark.parametrize('corrected_source', [False, True])
async def test_cost_bank_links_and_corrected_coverage_survive_archive_round_trip(rent_world, import_target, rent_db, export_store, scoped, corrected_source):
    from tests.test_bank_transaction_assign import cost_assignment_graph, cost_open
    async with api_client(rent_world) as api:
        scope_id = None
        if scoped:
            response = await api.post('/api/accounting-scopes', headers={'Idempotency-Key': str(uuid.uuid4())}, json={
                'property_id': str(rent_world['property']), 'scope_type': 'MIET', 'label': 'Electricity',
                'valid_from': '2026-01-01', 'valid_to': '2026-12-31', 'whole_property': True,
            })
            assert response.status_code == 201, response.text
            scope_id = response.json()['id']
            response = await api.post(f'/api/accounting-scopes/{scope_id}/grants',
                headers={'Idempotency-Key': str(uuid.uuid4())}, json={'user_id': str(rent_world['admin']),
                    'capabilities': ['read', 'prepare', 'confirm', 'approve', 'export'], 'reason': 'Archive regression'})
            assert response.status_code == 201, response.text
        cost, bank, tids = await cost_assignment_graph(api, rent_world['property'], scope_id=scope_id)
        assert await cost_open(api, cost) == 60
        successor = cost
        if corrected_source:
            corrected = await api.post(f'/api/cost-entries/{cost}/correction', json={'gross_amount': '120.00'})
            assert corrected.status_code == 201, corrected.text
            successor = corrected.json()['id']
            assert (await api.post(f'/api/cost-entries/{successor}/confirm')).status_code == 200
        archive = await api.post('/api/organizations/export')
        assert archive.status_code == 200, archive.text
    await source_organization_deleted(rent_db, rent_world['org'], rent_world['admin'], import_target)
    async with api_client(import_target) as api:
        response = await api.post('/api/organizations/import', files={'file': ('costs.tar.gz', archive.content)})
        assert response.status_code == 200, response.text
        ids = response.json()['id_map']
        mapped_cost = ids['cost_entry'][cost]
        mapped_successor = ids['cost_entry'][successor]
        assert await cost_open(api, mapped_successor) == 60
        page = await api.get('/api/bank-transactions', params={'bank_account_id': ids['bank_account'][bank], 'status': 'ALL'})
        assert page.status_code == 200, page.text
        rows = {row['id']: row for row in page.json()['items']}
        assert rows[ids['bank_transaction'][tids[0]]]['cost_entry_id'] == mapped_cost
        for tid, target in zip(tids, (mapped_cost, mapped_successor), strict=True):
            response = await api.post(f"/api/bank-transactions/{ids['bank_transaction'][tid]}/assign", json={'cost_entry_id': target})
            assert response.status_code == 200, response.text
        assert await cost_open(api, mapped_successor) == 0


async def classify_scope(api, world, property_id, scope_type):
    response = await api.post('/api/accounting-scopes', headers={'Idempotency-Key': str(uuid.uuid4())}, json={
        'property_id': str(property_id), 'scope_type': scope_type, 'label': 'Archived evidence',
        'valid_from': '2026-01-01', 'valid_to': '2026-12-31', 'whole_property': True,
    })
    assert response.status_code == 201, response.text
    scope_id = response.json()['id']
    response = await api.post(f'/api/accounting-scopes/{scope_id}/grants',
        headers={'Idempotency-Key': str(uuid.uuid4())}, json={'user_id': str(world['admin']),
            'capabilities': ['read', 'prepare', 'confirm', 'approve', 'export'], 'reason': 'Archive regression'})
    assert response.status_code == 201, response.text
    return scope_id


@pytest.mark.parametrize('bound', ['bank_account', 'cost_entry'])
async def test_partially_classified_bank_cost_archive_restores_the_existing_link(
    rent_world, import_target, rent_db, export_store, bound,
):
    from tests.test_bank_transaction_assign import cost_assignment_graph, cost_open

    async with api_client(rent_world) as api:
        property_id = rent_world['property']
        cost, bank, (assigned, unassigned) = await cost_assignment_graph(api, property_id)
        scope = await classify_scope(api, rent_world, property_id, 'MIET')
        classified = bank if bound == 'bank_account' else cost
        response = await api.post(f'/api/accounting-scopes/{scope}/bindings', json={
            'resource_type': bound, 'resource_id': classified, 'reason': 'Classify archived evidence'})
        assert response.status_code == 201, response.text
    archive = await exported(rent_world)
    await source_organization_deleted(rent_db, rent_world['org'], rent_world['admin'], import_target)
    async with api_client(import_target) as api:
        restored = await api.post('/api/organizations/import', files={'file': ('partial.tar.gz', archive)})
        assert restored.status_code == 200, restored.text
        ids = restored.json()['id_map']
        bank_id, cost_id = ids['bank_account'][bank], ids['cost_entry'][cost]
        rows = (await api.get('/api/bank-transactions', params={'bank_account_id': bank_id, 'status': 'ALL'})).json()['items']
        assert {row['id']: row['cost_entry_id'] for row in rows} == {
            ids['bank_transaction'][assigned]: cost_id, ids['bank_transaction'][unassigned]: None}
        new_match = await api.post(f"/api/bank-transactions/{ids['bank_transaction'][unassigned]}/assign",
                                   json={'cost_entry_id': cost_id})
        assert new_match.status_code in (404, 422), new_match.text
        missing, resource = ('cost_entry', cost_id) if bound == 'bank_account' else ('bank_account', bank_id)
        response = await api.post(f"/api/accounting-scopes/{ids['accounting_scope'][scope]}/bindings", json={
            'resource_type': missing, 'resource_id': resource, 'reason': 'Complete archived classification'})
        assert response.status_code == 201, response.text
        assert await cost_open(api, cost_id) == 60


async def test_released_scoped_reserve_draft_and_later_cost_link_survive_archive(
    rent_world, import_target, rent_db, export_store,
):
    from tests.test_bank_transaction_assign import cost_assignment_graph, cost_open

    async with api_client(rent_world) as api:
        response = await api.post('/api/properties', json={'name': 'Reserve evidence', 'management_type': 'WEG'})
        assert response.status_code == 201, response.text
        property_id = response.json()['id']
        scope = await classify_scope(api, rent_world, property_id, 'WEG')
        cost, bank, (_, debit) = await cost_assignment_graph(api, property_id, scope_id=scope)
        response = await api.post(f'/api/bank-transactions/{debit}/assign', json={'lines': [{
            'payment_type': 'RUECKLAGENENTNAHME', 'amount': '60.00', 'period_year': 2026, 'period_month': 2}]})
        assert response.status_code == 200, response.text
        draft, = response.json()['payments']
        payment_id = draft['id']
        assert draft['status'] == 'draft'
        response = await api.delete(f'/api/payments/{payment_id}')
        assert response.status_code == 204, response.text
        retained = (await api.get(f'/api/payments/{payment_id}')).json()
        assert (retained['status'], retained['bank_transaction_id']) == ('cancelled', debit)
        response = await api.post(f'/api/bank-transactions/{debit}/assign', json={'cost_entry_id': cost})
        assert response.status_code == 200, response.text
        assert response.json()['payments'] == []
        assert await cost_open(api, cost) == 0
    archive = await exported(rent_world)
    binding_sql = text('SELECT to_jsonb(b) FROM accounting_scope_binding b WHERE payment_id=CAST(:payment AS uuid)')
    payment_sql = text('SELECT cancelled_at FROM payment WHERE id=CAST(:payment AS uuid)')
    original_binding = await rent_db.scalar(binding_sql, {'payment': payment_id})
    original_cancellation = await rent_db.scalar(payment_sql, {'payment': payment_id})
    assert original_binding is not None and original_cancellation is not None
    await rent_db.rollback()
    await source_organization_deleted(rent_db, rent_world['org'], rent_world['admin'], import_target)
    async with api_client(import_target) as api:
        restored = await api.post('/api/organizations/import', files={'file': ('cancelled-draft.tar.gz', archive)})
        assert restored.status_code == 200, restored.text
        ids = restored.json()['id_map']
        debit_id, cost_id = ids['bank_transaction'][debit], ids['cost_entry'][cost]
        retained = await api.get(f"/api/payments/{ids['payment'][payment_id]}")
        assert retained.status_code == 200, retained.text
        assert (retained.json()['status'], retained.json()['bank_transaction_id']) == ('cancelled', debit_id)
        assert await cost_open(api, cost_id) == 0
        restored_payment = {'payment': ids['payment'][payment_id]}
        assert await rent_db.scalar(payment_sql, restored_payment) == original_cancellation
        restored_binding = await rent_db.scalar(binding_sql, restored_payment)
        assert restored_binding == {**original_binding, 'organization_id': str(import_target['org']),
            'id': ids['accounting_scope_binding'][original_binding['id']],
            'property_id': ids['property'][original_binding['property_id']],
            'scope_id': ids['accounting_scope'][original_binding['scope_id']],
            'payment_id': restored_payment['payment'], 'payment_bank_account_id': ids['bank_account'][bank],
            'reviewed_by': ids['user'][original_binding['reviewed_by']]}
        await rent_db.rollback()
        page = await api.get('/api/bank-transactions', params={'bank_account_id': ids['bank_account'][bank], 'status': 'ALL'})
        line = next(row for row in page.json()['items'] if row['id'] == debit_id)
        assert line['cost_entry_id'] == cost_id


@pytest.mark.parametrize('corrected_source', [False, True])
async def test_commercial_rules_and_finalized_statements_survive_an_archive_round_trip(rent_world, import_target, rent_db, export_store, corrected_source):
    from tests.commercial_preallocation_support import commercial_property, commercial_rule, REASON
    from app.engine.canonical import canonical_sha256
    async with api_client(rent_world) as api:
        data = await commercial_property(api)
        run_id = data['rows'][0]['run']['id']
        result = await api.post(f'/api/settlements/{run_id}/finalize', json={'commercial_preallocations': [commercial_rule(data)]})
        assert result.status_code == 200, result.text
        if corrected_source:
            result = await api.post(f"/api/cost-entries/{data['cost']['id']}/correction", json={'gross_amount': '1200.00'})
            assert result.status_code == 201, result.text
        archive = await api.post('/api/organizations/export')
        assert archive.status_code == 200, archive.text
    async with api_client(import_target) as api:
        restored = await api.post('/api/organizations/import', files={'file': ('commercial.tar.gz', archive.content)})
        assert restored.status_code == 200, restored.text
        ids = restored.json()['id_map']
        restored_run = ids['settlement_run'][run_id]
        result = await api.get(f'/api/settlements/{restored_run}')
        assert result.status_code == 200 and result.json()['total_cost'] == '600.00', result.text
        pdf = await api.get(f'/api/settlements/{restored_run}/pdf')
        assert pdf.status_code == 200 and pdf.content.startswith(b'%PDF-'), pdf.text
    rule = (await rent_db.execute(text('SELECT commercial_reason FROM commercial_preallocation WHERE organization_id=:org'), import_target)).scalar_one()
    assert rule == REASON
    payload, digest = (await rent_db.execute(text('SELECT snapshot_payload,snapshot_sha256 FROM settlement_run WHERE id=:id'),
        {'id': uuid.UUID(restored_run)})).one()
    assert canonical_sha256(payload) == digest
    await rent_db.rollback()


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
    # The archived administrator is an inactive placeholder too: its sign-in
    # address still belongs to the live source organization (issue #134).
    people = (await rent_db.execute(text('SELECT is_active,password_hash FROM "user" WHERE organization_id=:org AND id<>:admin'), import_target)).all()
    assert len(people) == 3 and all(not active and password == '!' for active, password in people)
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
    await source_organization_deleted(rent_db, rent_world['org'], rent_world['admin'], import_target)
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
    # A destination account that already holds the archived address is reused
    # (the address is free of the source organization, for example after its
    # deletion), deactivated and left without a usable password.
    email = f"relinked-{uuid.uuid4().hex}@rent-change.invalid"
    existing_id = uuid.uuid4()
    await rent_db.execute(text('''INSERT INTO "user"(id,organization_id,email,password_hash,display_name)
      VALUES (:id,:org,:email,'previous-password','Existing clerk')'''), {**import_target, 'id': existing_id, 'email': email})
    await rent_db.execute(text("INSERT INTO membership(organization_id,user_id,role) VALUES (:org,:id,'CLERK')"), {**import_target, 'id': existing_id})
    await rent_db.commit()
    files = archive_with_user_email(unpack(await exported(rent_world)), rent_world['clerk'], email)
    async with api_client(import_target) as api:
        answer = await api.post('/api/organizations/import', files={'file': ('org.tar.gz', repack(files))})
        assert answer.status_code == 200, answer.text
        assert answer.json()['id_map']['user'][str(rent_world['clerk'])] == str(existing_id)
    assert (await rent_db.execute(text('SELECT is_active,password_hash FROM "user" WHERE id=:id'), {'id': existing_id})).one() == (False, '!')
    await rent_db.rollback()


async def test_import_refuses_a_scope_created_by_an_account_whose_address_the_source_still_holds(
    rent_world, import_target, rent_db, export_store,
):
    # One sign-in address belongs to one organization (issue #134). While the
    # source organization still holds its administrator's address, the archived
    # administrator cannot be the importing administrator, so the accounting
    # scope they created cannot be restored: the whole import is refused and
    # the destination stays as it was.
    async with api_client(rent_world) as api:
        scope = await classify_scope(api, rent_world, rent_world['property'], 'MIET')
    archive = await exported(rent_world)
    async with api_client(import_target) as api:
        answer = await api.post('/api/organizations/import', files={'file': ('org.tar.gz', archive)})
    assert answer.status_code == 422, answer.text
    assert answer.json()['detail'] == {
        'message': 'Die Daten können unter den aktuellen Integritäts- und Freigaberegeln nicht importiert werden.',
        'code': 'IMPORT_INTEGRITY_REFUSAL', 'entity': 'accounting_scope', 'source_ids': [scope], 'count': 1}
    for table in ('property', 'accounting_scope', 'scope_grant', 'audit_event'):
        assert await rent_db.scalar(text(f'SELECT count(*) FROM {table} WHERE organization_id=:org'), import_target) == 0, table
    # Only the destination's own administrator account remains.
    assert await rent_db.scalar(text('SELECT count(*) FROM "user" WHERE organization_id=:org'), import_target) == 1
    assert await rent_db.scalar(text('SELECT count(*) FROM membership WHERE organization_id=:org'), import_target) == 1
    assert await rent_db.scalar(text('SELECT name FROM organization WHERE id=:org'), import_target) == 'Empty target'
    assert not list((export_store._root / str(import_target['org'])).rglob('*.bin'))
    await rent_db.rollback()


async def test_imported_account_with_globally_taken_address_is_inactive_placeholder(rent_world, import_target, rent_db, export_store):
    # One sign-in address belongs to one organization (issue #134): the source
    # clerk's address is still owned by the live source organization, so the
    # imported placeholder keeps a deterministic synthetic address and cannot
    # sign in.
    email = await rent_db.scalar(text('SELECT email FROM "user" WHERE id=:clerk'), rent_world)
    async with api_client(import_target) as api:
        answer = await api.post('/api/organizations/import', files={'file': ('org.tar.gz', await exported(rent_world))})
        assert answer.status_code == 200, answer.text
        placeholder_id = answer.json()['id_map']['user'][str(rent_world['clerk'])]
    row = (await rent_db.execute(text('SELECT email,is_active,password_hash FROM "user" WHERE id=:id'),
                                 {'id': uuid.UUID(placeholder_id)})).one()
    assert row == (f"{placeholder_id}-{import_target['org']}@imported.archive.invalid", False, '!')
    assert row[0] != email
    await rent_db.rollback()


async def test_imported_account_keeps_an_address_nobody_holds(rent_world, import_target, rent_db, export_store):
    # Only a globally taken address becomes a synthetic placeholder (issue
    # #134): an address nobody holds is restored unchanged so the placeholder
    # stays identifiable with the archived account.
    fresh_email = f"free-{uuid.uuid4().hex}@example.invalid"
    files = archive_with_user_email(unpack(await exported(rent_world)), rent_world['clerk'], fresh_email)
    async with api_client(import_target) as api:
        answer = await api.post('/api/organizations/import', files={'file': ('org.tar.gz', repack(files))})
        assert answer.status_code == 200, answer.text
        placeholder_id = answer.json()['id_map']['user'][str(rent_world['clerk'])]
    row = (await rent_db.execute(text('SELECT email,is_active,password_hash FROM "user" WHERE id=:id'),
                                 {'id': uuid.UUID(placeholder_id)})).one()
    assert row == (fresh_email, False, '!')
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


async def test_archive_cannot_supply_a_reviewed_bank_successor_without_native_provenance(rent_world, import_target, rent_db, export_store):
    from tests.test_bank_transaction_assign import cost_assignment_graph
    async with api_client(rent_world) as api:
        first, _, _ = await cost_assignment_graph(api, rent_world['property'])
        predecessor = first
        for _ in range(2):
            response = await api.post(f'/api/cost-entries/{predecessor}/correction', json={'gross_amount': '120.00'})
            assert response.status_code == 201, response.text
            predecessor = response.json()['id']
            response = await api.post(f'/api/cost-entries/{predecessor}/confirm')
            assert response.status_code == 200, response.text
        archive = await api.post('/api/organizations/export')
        assert archive.status_code == 200, archive.text
    files = unpack(archive.content)
    costs = [json.loads(line) for line in files['entities/cost_entry.jsonl'].splitlines()]
    old = next(row for row in costs if row['id'] == first)
    assert old['superseded_by_id'] != predecessor and old['bank_coverage_successor_id'] is None
    old['bank_coverage_successor_id'] = predecessor
    files['entities/cost_entry.jsonl'] = b''.join(json.dumps(row).encode() + b'\n' for row in costs)
    async with api_client(import_target) as api:
        response = await api.post('/api/organizations/import', files={'file': ('tampered.tar.gz', repack(files))})
        assert response.status_code == 422 and response.json()['detail']['code'] == 'IMPORT_INTEGRITY_REFUSAL', response.text
    assert await rent_db.scalar(text('SELECT count(*) FROM cost_entry WHERE organization_id=:org'), import_target) == 0
    await rent_db.rollback()


@pytest.mark.parametrize('archive_case', ['legacy', 'wrong_property', 'orphan', 'current_missing', 'current_snapshot'])
async def test_register_archive_upgrade_preserves_both_dates_and_refuses_invalid_sources(
    rent_world, import_target, rent_db, export_store, archive_case,
):
    """A real pre-0167 row shape must work, not merely a relabelled current export."""
    async with api_client(rent_world) as api:
        prop = await api.post('/api/properties', json={'name': 'Gartenstraße 5', 'management_type': 'WEG'})
        assert prop.status_code == 201, prop.text
        prop_id = prop.json()['id']
        meeting = await api.post('/api/meetings', json={'property_id': prop_id, 'meeting_date': '2026-06-15',
            'meeting_type': 'ORDENTLICH', 'location': 'Historic meeting place'})
        assert meeting.status_code == 201, meeting.text
        resolution = await api.post('/api/meetings/resolutions', json={'meeting_id': meeting.json()['id'],
            'wording': 'Historic registered wording', 'votes_yes': 5, 'votes_no': 0, 'votes_abstain': 0,
            'result': 'ANGENOMMEN'})
        assert resolution.status_code == 201, resolution.text
        entry = await api.post(f"/api/meetings/resolutions/{resolution.json()['id']}/register")
        assert entry.status_code == 201, entry.text
    files = unpack(await exported(rent_world))
    manifest = json.loads(files['manifest.json'])
    manifest['schema_head'] = '0167' if archive_case.startswith('current_') else '0166'
    files['manifest.json'] = json.dumps(manifest).encode()
    name = 'entities/resolution_register_entry.jsonl'
    rows = [json.loads(line) for line in files[name].splitlines()]
    historic = next(row for row in rows if row['id'] == entry.json()['id'])
    historic['entry_date'] = '2026-11-01'  # Synthetic archive evidence, never a backdated live entry.
    if archive_case == 'current_snapshot':
        historic['resolution_date'] = '2026-06-14'  # Existing snapshots are not re-derived on import.
    else:
        historic.pop('resolution_date')
    if archive_case == 'wrong_property':
        historic['property_id'] = str(rent_world['property'])
    if archive_case == 'orphan':
        historic['resolution_id'] = str(uuid.uuid4())
    files[name] = b''.join(json.dumps(row).encode() + b'\n' for row in rows)
    async with api_client(import_target) as api:
        response = await api.post('/api/organizations/import', files={'file': ('legacy.tar.gz', repack(files))})
    if archive_case in ('legacy', 'current_snapshot'):
        assert response.status_code == 200, response.text
        mapping = response.json()['id_map']
        stored = (await rent_db.execute(text('SELECT * FROM resolution_register_entry WHERE id=:id'),
            {'id': uuid.UUID(mapping['resolution_register_entry'][historic['id']])})).mappings().one()
        assert str(stored['resolution_date']) == ('2026-06-14' if archive_case == 'current_snapshot' else '2026-06-15')
        assert str(stored['entry_date']) == '2026-11-01'
        assert stored['location'] == historic['location']
        assert stored['wording'] == historic['wording']
        assert stored['sequence_number'] == historic['sequence_number']
        assert str(stored['property_id']) == mapping['property'][historic['property_id']]
        assert str(stored['resolution_id']) == mapping['resolution'][historic['resolution_id']]
    else:
        assert response.status_code == 422, response.text
        assert await rent_db.scalar(text('SELECT count(*) FROM property WHERE organization_id=:org'), import_target) == 0
    await rent_db.rollback()


from tests.test_special_levy import levy_db as levy_db, scope_seed as scope_seed, worker_tenant, client as levy_client  # noqa: E402,F401
from tests.test_m4_weg import legacy_levy  # noqa: E402


async def test_import_retains_historical_unlinked_levy_and_frozen_allocations(
    levy_db, scope_seed, import_target, rent_db, export_store,
):
    ids = {'org': scope_seed['org_a_id'], 'actor': scope_seed['user_a_admin_id'],
        'property': uuid.uuid4(), 'scope': uuid.uuid4()}
    # This historical source contains only the levy's actual dependencies,
    # without unrelated unclassified bank inputs or revoked grant history.
    await levy_db.execute(text("INSERT INTO property(id,organization_id,name,management_type) VALUES (:property,:org,'Historical WEG','WEG')"), ids)
    await levy_db.execute(text("INSERT INTO accounting_scope(id,organization_id,property_id,scope_type,label,valid_from,whole_property,origin,created_by) VALUES (:scope,:org,:property,'WEG','Historical WEG','2025-01-01',true,'REVIEWED',:actor)"), ids)
    ids.update(unit=uuid.uuid4(), owner=uuid.uuid4(), person=uuid.uuid4(), history=uuid.uuid4())
    for statement in (
        "INSERT INTO unit(id,organization_id,property_id,number,usage_type,mea) VALUES (:unit,:org,:property,'Archive unit','WOHNUNG',1000)",
        "INSERT INTO person(id,organization_id,first_name,last_name) VALUES (:person,:org,'Anna','Archive')",
        "INSERT INTO owner_profile(id,organization_id,person_id) VALUES (:owner,:org,:person)",
        "INSERT INTO ownership_history(id,organization_id,unit_id,owner_profile_id,valid_from) VALUES (:history,:org,:unit,:owner,'2025-01-01')",
    ):
        await levy_db.execute(text(statement), ids)
    ids.update(units=[ids['unit']], owners=[ids['owner']], histories=[ids['history']], legacy_meas=[1000])
    await levy_db.commit()
    levy = await legacy_levy(levy_db, ids)
    await levy_db.execute(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES (:org,:scope,:actor,ARRAY['read','prepare','export'],:actor,'Archive historical levy')"), ids)
    await levy_db.commit()
    async with levy_client(ids, 'actor') as api:
        answer = await api.post('/api/organizations/export')
        assert answer.status_code == 200, answer.text
    files = unpack(answer.content)
    manifest = json.loads(files['manifest.json'])
    manifest['schema_head'] = '0167'
    files['manifest.json'] = json.dumps(manifest).encode()
    await source_organization_deleted(rent_db, ids['org'], ids['actor'], import_target)
    async with api_client(import_target) as api:
        answer = await api.post('/api/organizations/import', files={'file': ('historical.tar.gz', repack(files))})
        assert answer.status_code == 200, answer.text
        mapping = answer.json()['id_map']
        prop = mapping['property'][str(ids['property'])]
        scope = mapping['accounting_scope'][str(ids['scope'])]
        response = await api.get('/api/special-levies', params={'property_id': prop, 'accounting_scope_id': scope})
        assert response.status_code == 200, response.text
        row = next(row for row in response.json() if row['id'] == mapping['special_levy'][str(levy)])
        assert row['resolution_id'] is None
        assert row['amount'] == '1200.00' and row['due_date'] == '2026-07-01'
        assert sorted(a['amount'] for a in row['allocations']) == ['1200.00']
        payload = {'property_id': prop, 'accounting_scope_id': scope, 'amount': '1200.00', 'due_date': '2026-07-01'}
        assert (await api.post('/api/special-levies', json=payload)).status_code == 422
    await rent_db.rollback()
