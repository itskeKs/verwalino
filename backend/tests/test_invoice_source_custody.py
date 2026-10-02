"""Strict invoice intake belongs to its recorded uploader until financial binding."""
import uuid
import asyncio
import pytest
import pytest_asyncio
from sqlalchemy import text
from app.core.storage import LocalFsDocumentStore, set_store
from tests.test_financial_scope_boundary import scope_seed, worker_tenant  # noqa: F401
from tests.test_scope_grants import scope_fixture
from tests.test_scoped_account_statements import client, mutate
from tests.test_property_source_enforcement import synthetic_enforcement, change_mode
from tests.test_scope_payment_reconciliation import read_value

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def store(tmp_path):
    value = LocalFsDocumentStore(tmp_path)
    set_store(value)
    try:
        yield value
    finally:
        set_store(None)


async def upload(api):
    response = await api.post('/api/invoice-documents/upload', files={'file': ('private-invoice.pdf', b'%PDF-1.4 synthetic private invoice', 'application/pdf')})
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()['id'])


async def fixture(db, seed):
    ids = await scope_fixture(db, seed)
    await db.commit()
    return ids


async def wait_for_advisory_waiter(ids, holder_pid):
    for _ in range(200):
        if await read_value("SELECT count(*) FROM pg_stat_activity WHERE wait_event='advisory' AND :holder=ANY(pg_blocking_pids(pid))", {**ids, 'holder':holder_pid}):
            return
        await asyncio.sleep(.01)
    pytest.fail('Concurrent request did not reach the financial-input lock')


async def test_retry_waits_for_financial_lock_before_document_lock(migrator_session, scope_seed, store):
    from app.core.db import get_session_factory, set_tenant_context
    from app.services.financial_scope_service import FinancialScopeService
    ids = await fixture(migrator_session, scope_seed)
    async with client(ids) as api:
        ids['document'] = await upload(api)
        async with get_session_factory()() as holder:
            async with holder.begin():
                await set_tenant_context(holder, ids['org'])
                await holder.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {'actor':str(ids['clerk'])})
                await FinancialScopeService(holder, ids['org'], ids['clerk']).lock_inputs()
                retry = asyncio.create_task(api.post(f"/api/invoice-documents/{ids['document']}/retry"))
                try:
                    await wait_for_advisory_waiter(ids, await holder.scalar(text('SELECT pg_backend_pid()')))
                    # A version/claim holding financial inputs can still acquire
                    # the document; retry must not retain it while waiting.
                    assert await holder.scalar(text('SELECT id FROM invoice_document WHERE id=:document FOR UPDATE NOWAIT'), ids) == ids['document']
                except BaseException:
                    retry.cancel()
                    await asyncio.gather(retry, return_exceptions=True)
                    raise
            assert (await asyncio.wait_for(retry, 5)).status_code == 200


async def test_download_holds_authorization_until_last_chunk(migrator_session, scope_seed, store, monkeypatch):
    from app.main import app
    from app.core.config import settings
    from app.core.security import create_session_token
    ids = await fixture(migrator_session, scope_seed)
    async with client(ids) as api:
        document = await upload(api)
    first_chunk, resume = asyncio.Event(), asyncio.Event()
    bodies = []
    async def chunks(_uri):
        yield b'first'
        await resume.wait()
        yield b'last'
    monkeypatch.setattr(store, 'iter_chunks', chunks)
    async def send(message):
        if message['type'] == 'http.response.body':
            bodies.append(message.get('body', b''))
            first_chunk.set()
    async def receive():
        await asyncio.Event().wait()
    token = create_session_token(ids['clerk'], ids['org'])
    scope = {'type':'http', 'asgi':{'version':'3.0','spec_version':'2.4'}, 'http_version':'1.1',
             'method':'GET','scheme':'http','path':f'/api/invoice-documents/{document}/content',
             'raw_path':f'/api/invoice-documents/{document}/content'.encode(),'query_string':b'',
             'headers':[(b'host',b'test'),(b'cookie', f'{settings.session_cookie_name}={token}'.encode())],
             'client':('127.0.0.1',123), 'server':('test',80), 'root_path':''}
    async with synthetic_enforcement(scope_seed):
        download = asyncio.create_task(app(scope, receive, send))
        revoke = None
        try:
            await asyncio.wait_for(first_chunk.wait(), 5)
            assert bodies == [b'first']
            revoke = asyncio.create_task(mutate(text('UPDATE "user" SET is_active=false WHERE id=:clerk'), ids))
            # Observe PostgreSQL itself waiting for the shared authorization
            # lock, rather than inferring it from an arbitrary sleep.
            for _ in range(200):
                if await read_value("SELECT count(*) FROM pg_stat_activity WHERE wait_event='advisory' AND query LIKE 'UPDATE \"user\" SET is_active=false%'", ids):
                    break
                if revoke.done():
                    pytest.fail('Revocation committed while original bytes were still streaming')
                await asyncio.sleep(.01)
            else:
                pytest.fail('Revocation did not reach the authorization lock')
            resume.set()
            await asyncio.wait_for(asyncio.gather(download, revoke), 5)
            assert b''.join(bodies) == b'firstlast'
            async with client(ids) as api:
                assert (await api.get(f'/api/invoice-documents/{document}/content')).status_code == 401
        finally:
            resume.set()
            for task in (download,revoke):
                if task and not task.done():
                    task.cancel()
            await asyncio.gather(*[t for t in (download,revoke) if t], return_exceptions=True)


async def test_upload_admission_does_not_wait_behind_queued_revocation(migrator_session, scope_seed, store, monkeypatch):
    from app.services.invoice_document_service import InvoiceDocumentService
    ids = await fixture(migrator_session, scope_seed)
    original = InvoiceDocumentService.admit_uploaded_document
    ready, resume = asyncio.Event(), asyncio.Event()
    holder = {}
    async def paused_admission(service, document):
        holder['pid'] = await service._session.scalar(text('SELECT pg_backend_pid()'))
        ready.set()
        await resume.wait()
        return await original(service, document)
    monkeypatch.setattr(InvoiceDocumentService, 'admit_uploaded_document', paused_admission)
    async with synthetic_enforcement(scope_seed), client(ids) as api:
        request = asyncio.create_task(upload(api))
        revoke = None
        try:
            await asyncio.wait_for(ready.wait(), 5)
            revoke = asyncio.create_task(mutate(text('UPDATE "user" SET is_active=false WHERE id=:clerk'), ids))
            await wait_for_advisory_waiter(ids, holder['pid'])
            resume.set()
            document, _ = await asyncio.wait_for(asyncio.gather(request,revoke), 5)
            assert await read_value('SELECT uploaded_by FROM invoice_document WHERE id=:document', {**ids,'document':document}) == ids['clerk']
        finally:
            resume.set()
            for task in (request,revoke):
                if task and not task.done():
                    task.cancel()
            await asyncio.gather(*[t for t in (request,revoke) if t], return_exceptions=True)


async def test_strict_standalone_invoice_is_private_to_uploader(migrator_session, scope_seed, store, monkeypatch):
    ids = await fixture(migrator_session, scope_seed)
    async with client(ids) as api:
        document = await upload(api)
    async with synthetic_enforcement(scope_seed):
        async with client(ids) as owner:
            assert (await owner.get(f'/api/invoice-documents/{document}')).status_code == 200
            assert (await owner.get(f'/api/invoice-documents/{document}/content')).content.startswith(b'%PDF')
        monkeypatch.setattr(store, 'iter_chunks', lambda *a, **k: pytest.fail('Denied source reached byte streaming'))
        async with client(ids, 'actor') as other:
            for suffix in ['', '/content', '/verify', '/processing-attempts']:
                result = await other.get(f'/api/invoice-documents/{document}{suffix}')
                assert result.status_code == 404, (suffix,result.text)
            assert (await other.post(f'/api/invoice-documents/{document}/retry')).status_code == 404
            assert str(document) not in [row['id'] for row in (await other.get('/api/invoice-documents')).json()]
        assert await read_value('SELECT count(*) FROM audit_event WHERE entity_id=:document', {**ids,'document':document}, actor_key='actor') == 0


async def test_paused_invoice_upload_is_denied_before_storage(migrator_session, scope_seed, store, monkeypatch):
    ids = await fixture(migrator_session, scope_seed)
    async with synthetic_enforcement(scope_seed), client(ids) as api:
        await change_mode(scope_seed, 'PAUSED')
        monkeypatch.setattr(store, 'put', lambda *a, **k: pytest.fail('Paused upload reached storage'))
        response = await api.post('/api/invoice-documents/upload', files={'file':('blocked.pdf',b'%PDF-1.4 paused','application/pdf')})
        assert response.status_code == 404, response.text


async def test_strict_versions_serialize_and_pause_precedes_storage(migrator_session, scope_seed, store, monkeypatch):
    ids = await fixture(migrator_session, scope_seed)
    async with synthetic_enforcement(scope_seed), client(ids) as api:
        root = await upload(api)
        responses = await asyncio.gather(*[api.post(f'/api/invoice-documents/{root}/version',
            files={'file':('replacement.pdf',f'%PDF-version-{i}'.encode(),'application/pdf')}) for i in range(2)])
        assert [r.status_code for r in responses] == [201,201], [r.text for r in responses]
        rows = sorted([r.json() for r in responses], key=lambda r:r['version'])
        assert [r['version'] for r in rows] == [2,3]
        assert rows[0]['supersedes_id'] == str(root)
        assert rows[1]['supersedes_id'] == rows[0]['id']
        await change_mode(scope_seed, 'PAUSED')
        monkeypatch.setattr(store, 'put', lambda *a, **k: pytest.fail('Paused replacement reached storage'))
        response = await api.post(f'/api/invoice-documents/{root}/version', files={'file':('blocked.pdf',b'%PDF-paused','application/pdf')})
        assert response.status_code == 404
        assert (await api.get(f"/api/invoice-documents/{rows[1]['id']}/content")).status_code == 200


async def test_runtime_cannot_forge_custody_or_alias_a_stored_original(migrator_session, scope_seed, store):
    import json
    from app.core.db import get_worker_session_factory, get_review_tenant_session, set_tenant_context
    from sqlalchemy.exc import DBAPIError
    ids = await fixture(migrator_session, scope_seed)
    async with client(ids) as api:
        ids['document'] = await upload(api)
    row = await read_value('SELECT to_jsonb(d) FROM invoice_document d WHERE id=:document', ids)
    assert row['uploaded_by'] == str(ids['clerk'])
    for field,value in [('uploaded_by',str(ids['actor'])),('content_sha256','b'*64),('storage_uri','synthetic/forged'),('chain_root_id',str(uuid.uuid4()))]:
        with pytest.raises(DBAPIError, match='immutable'):
            await read_value(f'UPDATE invoice_document SET {field}=CAST(:value AS '+('uuid' if field in ['uploaded_by','chain_root_id'] else 'text')+') WHERE id=:document RETURNING id', {**ids,'value':value}, actor_key='clerk')
    forged = {**row, 'id':str(uuid.uuid4())}
    forged['chain_root_id'] = forged['id']
    with pytest.raises(DBAPIError, match='fresh upload admission'):
        await read_value('INSERT INTO invoice_document SELECT (jsonb_populate_record(NULL::invoice_document,CAST(:payload AS jsonb))).* RETURNING id',
                         {**ids,'payload':json.dumps(forged)}, actor_key='clerk')
    with pytest.raises(DBAPIError, match='permission denied'):
        await read_value('SELECT admit_invoice_upload(:org,:document,CAST(:payload AS jsonb))', {**ids,'payload':json.dumps(forged)}, actor_key='clerk')
    with pytest.raises(DBAPIError, match='permission denied'):
        await read_value('SELECT count(*) FROM invoice_upload_admission', ids, actor_key='clerk')
    async with get_worker_session_factory()() as worker, worker.begin():
        await set_tenant_context(worker, ids['org'])
        await worker.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {'actor':str(ids['clerk'])})
        for statement, expected in [
            ('SELECT admit_invoice_upload(:org,:document,CAST(:payload AS jsonb))','permission denied'),
            ('INSERT INTO invoice_document SELECT (jsonb_populate_record(NULL::invoice_document,CAST(:payload AS jsonb))).* RETURNING id','fresh upload admission')]:
            with pytest.raises(DBAPIError, match=expected):
                async with worker.begin_nested():
                    await worker.execute(text(statement), {**ids,'payload':json.dumps(forged)})
    async with get_review_tenant_session(ids['org']) as review:
        with pytest.raises(DBAPIError, match='permission denied'):
            async with review.begin_nested():
                await review.execute(text('SELECT count(*) FROM invoice_upload_admission'))


async def test_upload_admission_requires_exact_stored_payload(migrator_session, scope_seed, store):
    import importlib
    import json
    from sqlalchemy.exc import DBAPIError
    from app.core.db import get_review_tenant_session
    fields = importlib.import_module('migrations.versions.0082_invoice_source_custody').FIELDS
    ids = await fixture(migrator_session, scope_seed)
    async with client(ids) as api:
        ids['document'] = await upload(api)
    row = await read_value('SELECT to_jsonb(d) FROM invoice_document d WHERE id=:document', ids)
    row['id'] = row['chain_root_id'] = str(uuid.uuid4())
    payload = {field:row[field] for field in fields}
    async with get_review_tenant_session(ids['org']) as review:
        await review.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {'actor':str(ids['clerk'])})
        await review.execute(text('SELECT admit_invoice_upload(:org,:document,CAST(:payload AS jsonb))'),
                             {**ids,'document':uuid.UUID(row['id']),'payload':json.dumps(payload)})
    row['content_sha256'] = 'f'*64
    with pytest.raises(DBAPIError, match='fresh upload admission'):
        await read_value('INSERT INTO invoice_document SELECT (jsonb_populate_record(NULL::invoice_document,CAST(:payload AS jsonb))).* RETURNING id',
                         {**ids,'payload':json.dumps(row)}, actor_key='clerk')


async def test_unknown_standalone_history_cannot_be_reowned_or_versioned(migrator_session, scope_seed, store, monkeypatch):
    from sqlalchemy.exc import DBAPIError
    ids = await fixture(migrator_session, scope_seed)
    ids['document'] = uuid.uuid4()
    await mutate(text("""INSERT INTO invoice_document(id,organization_id,storage_uri,storage_backend,mime_type,content_byte_size,
        content_sha256,version,chain_root_id,status) VALUES(:document,:org,'synthetic/historical','LOCAL_FS','application/pdf',1,repeat('a',64),1,:document,'active')"""), ids)
    before = await read_value('SELECT md5(to_jsonb(d)::text) FROM invoice_document d WHERE id=:document', ids)
    monkeypatch.setattr(store, 'put', lambda *a, **k: pytest.fail('Unknown historical chain reached storage'))
    async with client(ids) as api:
        assert (await api.get(f"/api/invoice-documents/{ids['document']}")).status_code == 200
        assert (await api.post(f"/api/invoice-documents/{ids['document']}/version", files={'file':('new.pdf',b'%PDF-new','application/pdf')})).status_code == 404
    with pytest.raises(DBAPIError, match='immutable'):
        await read_value('UPDATE invoice_document SET uploaded_by=:clerk WHERE id=:document RETURNING id', ids, actor_key='clerk')
    async with synthetic_enforcement(scope_seed), client(ids, 'actor') as api:
        for suffix in ['', '/content', '/verify', '/processing-attempts']:
            assert (await api.get(f"/api/invoice-documents/{ids['document']}{suffix}")).status_code == 404
    assert await read_value('SELECT md5(to_jsonb(d)::text) FROM invoice_document d WHERE id=:document', ids) == before


async def test_current_uploader_role_controls_source_effects_and_worker(migrator_session, scope_seed, store):
    from app.core.db import get_worker_session_factory, set_tenant_context
    from app.services.invoice_processing_service import InvoiceProcessingService
    from sqlalchemy.exc import DBAPIError
    ids = await fixture(migrator_session, scope_seed)
    async with client(ids) as api:
        ids['document'] = await upload(api)
    async with synthetic_enforcement(scope_seed):
        await mutate(text("UPDATE membership SET role='READ_ONLY' WHERE organization_id=:org AND user_id=:clerk"), ids)
        async with client(ids) as api:
            assert (await api.get(f"/api/invoice-documents/{ids['document']}/content")).status_code == 200
            assert (await api.post(f"/api/invoice-documents/{ids['document']}/retry")).status_code == 403
        with pytest.raises(DBAPIError, match='preparation authority'):
            await read_value("UPDATE invoice_document SET extraction_payload='{}' WHERE id=:document RETURNING id", ids, actor_key='clerk')
        async with get_worker_session_factory()() as db, db.begin():
            await set_tenant_context(db, ids['org'])
            assert await InvoiceProcessingService(db, ids['org']).claim() is None
        assert await read_value('SELECT processing_status FROM invoice_document WHERE id=:document', ids) == 'canceled'
        await mutate(text('UPDATE "user" SET is_active=false WHERE id=:clerk'), ids)
        assert await read_value('SELECT count(*) FROM invoice_document WHERE id=:document', ids, actor_key='clerk') == 0


async def test_strict_upload_extract_and_idempotent_link_preserves_custody(migrator_session, scope_seed, store, monkeypatch):
    from app.core.db import get_worker_session_factory
    from app.core.invoice_ocr import extract_invoice
    from app.workers.invoice_extraction import process_once
    from tests.test_invoice_extraction_worker import pdf_bytes
    ids = await fixture(migrator_session, scope_seed)
    category = await read_value("SELECT id FROM cost_category WHERE code='GRUNDSTEUER' AND plan_version=1", ids)
    async with client(ids, 'actor') as admin:
        domain = await admin.post('/api/property-data-domains', headers={'Idempotency-Key':str(uuid.uuid4())},
                                 json={'property_id':str(ids['property']), 'label':'Invoice review property', 'reason':'Explicit property visibility'})
        assert domain.status_code == 201, domain.text
        grant = await admin.post(f"/api/property-data-domains/{domain.json()['id']}/grants", headers={'Idempotency-Key':str(uuid.uuid4())},
                                 json={'user_id':str(ids['clerk']), 'capabilities':['read'], 'reason':'Review cost destination'})
        assert grant.status_code == 201, grant.text
    async with synthetic_enforcement(scope_seed), client(ids) as api:
        uploaded = await api.post('/api/invoice-documents/upload', files={'file':('invoice.pdf',pdf_bytes(),'application/pdf')})
        assert uploaded.status_code == 201, uploaded.text
        ids['document'] = uuid.UUID(uploaded.json()['id'])
        assert await process_once(get_worker_session_factory(), ids['org'], processor=extract_invoice)
        ready = await api.get(f"/api/invoice-documents/{ids['document']}")
        assert ready.json()['extraction_payload']['gross_amount'] == '119.00'
        body = {'accounting_scope_id':str(ids['scope']), 'source_document_id':str(ids['document']),
                'property_id':str(ids['property']), 'cost_category_id':str(category), 'invoice_date':'2025-06-01',
                'service_period_from':'2025-01-01', 'service_period_to':'2025-12-31', 'gross_amount':'119.00',
                'tenant_apportionable':'UMLAGEFAEHIG','source':'MANUAL'}
        headers = {'Idempotency-Key':str(uuid.uuid4())}
        linked = await api.post('/api/cost-entries', json=body, headers=headers)
        assert linked.status_code == 201, linked.text
        replay = await api.post('/api/cost-entries', json=body, headers=headers)
        assert replay.status_code == 201 and replay.json()['id'] == linked.json()['id']
        assert await read_value('SELECT uploaded_by FROM invoice_document WHERE id=:document', ids) == ids['clerk']
        # Binding switches to the exact scope audience. Its prepare/read grant
        # does not silently authorize export of the original.
        assert (await api.get(f"/api/invoice-documents/{ids['document']}")).status_code == 200
        assert (await api.get(f"/api/invoice-documents/{ids['document']}/content")).status_code == 404
        draft = await api.post('/api/cost-entries', json={k:v for k,v in body.items() if k != 'source_document_id'},
                               headers={'Idempotency-Key':str(uuid.uuid4())})
        assert draft.status_code == 201, draft.text
        attached = await api.post('/api/invoice-documents/upload', data={'cost_entry_id':draft.json()['id']},
                                  files={'file':('linked.pdf',pdf_bytes(),'application/pdf')})
        assert attached.status_code == 201, attached.text
        assert attached.json()['cost_entry_id'] == draft.json()['id']
        monkeypatch.setattr(store, 'put', lambda *a, **k: pytest.fail('Unauthorized linked upload reached storage'))
        async with client(ids, 'actor') as other:
            denied = await other.post('/api/invoice-documents/upload', data={'cost_entry_id':draft.json()['id']},
                                      files={'file':('denied.pdf',pdf_bytes(),'application/pdf')})
            assert denied.status_code == 404, denied.text


async def test_custody_migration_retains_evidence_and_empty_roundtrip(migrator_session, scope_seed, store):
    import importlib
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    ids = await fixture(migrator_session, scope_seed)
    async with client(ids) as api:
        ids['document'] = await upload(api)
    migration = importlib.import_module('migrations.versions.0082_invoice_source_custody')
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
            with pytest.raises(DBAPIError, match='Retain invoice custody'):
                with db.begin_nested():
                    migration.downgrade()
            point = db.begin_nested()
            try:
                db.execute(text('TRUNCATE invoice_document,invoice_upload_admission CASCADE'))
                oid = db.scalar(text("SELECT 'financial_scope_access(uuid,text,uuid,text)'::regprocedure::oid"))
                migration.downgrade()
                migration.upgrade()
                assert db.scalar(text("SELECT 'financial_scope_access(uuid,text,uuid,text)'::regprocedure::oid")) == oid
                assert not db.scalar(text('SELECT property_source_activation_ready(:org)'), ids)
            finally:
                point.rollback()
        assert await read_value('SELECT uploaded_by FROM invoice_document WHERE id=:document', ids) == ids['clerk']
        assert await read_value('SELECT count(*) FROM invoice_upload_admission WHERE document_id=:document', ids) == 1
    finally:
        engine.dispose()
