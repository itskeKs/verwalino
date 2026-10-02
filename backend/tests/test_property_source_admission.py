"""Authorized attachment records durable audience admission, not inferred ownership."""
import uuid
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from app.core.db import get_tenant_session
from tests.test_property_source_access import grant,access
from tests.test_declaration_import_api import isolated_document_store,enable_declaration_imports  # noqa: F401

pytestmark=pytest.mark.asyncio


async def setup(api,seed,authorized=True):
    response=await api.post('/api/properties',json={'name':'Admission target','management_type':'WEG'})
    assert response.status_code==201,response.text
    prop=uuid.UUID(response.json()['id'])
    domain,reader_grant=await grant(api,seed,prop,actor=seed['user_a_clerk_id'],caps=['read'])
    uploader_grant=None
    if authorized:
        response=await api.post(f'/api/property-data-domains/{domain}/grants',headers={'Idempotency-Key':str(uuid.uuid4())},
            json={'user_id':str(seed['user_a_admin_id']),'capabilities':['read','prepare'],'reason':'Source upload authorization'})
        assert response.status_code==201,response.text
        uploader_grant=response.json()['id']
    response=await api.post('/api/declaration-imports',json={'mode':'EXISTING_PROPERTY','target_property_id':str(prop)})
    assert response.status_code==201,response.text
    job=response.json()['id']
    response=await api.post(f'/api/declaration-imports/{job}/documents',
        files={'file':('source.pdf',b'%PDF-1.4\n%%EOF','application/pdf')},data={'document_role':'DECLARATION','sequence_no':'1'})
    assert response.status_code==201,response.text
    return {'property':prop,'domain':domain,'reader_grant':reader_grant,'uploader_grant':uploader_grant,
            'job':uuid.UUID(job),'document':uuid.UUID(response.json()['id'])}


async def test_admission_survives_uploader_revocation_but_never_viewer_revocation(client_a_admin,seed_data):
    ids=await setup(client_a_admin,seed_data)
    async with get_tenant_session(seed_data['org_a_id']) as db:
        await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(seed_data['user_a_clerk_id'])})
        receipt=(await db.execute(text('SELECT * FROM property_source_admission WHERE document_id=:document'),ids)).mappings().one()
        assert receipt['import_id']==ids['job'] and receipt['property_id']==ids['property']
        assert receipt['domain_id']==uuid.UUID(ids['domain']) and receipt['grant_id']==uuid.UUID(ids['uploader_grant'])
        assert receipt['admitted_by']==seed_data['user_a_admin_id']
    assert (await client_a_admin.post(f"/api/property-data-domains/{ids['domain']}/grants/{ids['uploader_grant']}/revoke")).status_code==204
    assert await access(seed_data,'property_document',ids['document'],actor=seed_data['user_a_clerk_id'])
    assert not await access(seed_data,'property_document',ids['document'])
    assert (await client_a_admin.post(f"/api/property-data-domains/{ids['domain']}/grants/{ids['reader_grant']}/revoke")).status_code==204
    assert not await access(seed_data,'property_document',ids['document'],actor=seed_data['user_a_clerk_id'])


async def test_legacy_upload_has_no_receipt_and_later_grants_do_not_backfill_it(client_a_admin,seed_data):
    from sqlalchemy import create_engine
    from tests.conftest import MIGRATOR_SYNC_URL
    ids=await setup(client_a_admin,seed_data,authorized=False)
    response=await client_a_admin.post(f"/api/property-data-domains/{ids['domain']}/grants",headers={'Idempotency-Key':str(uuid.uuid4())},
        json={'user_id':str(seed_data['user_a_admin_id']),'capabilities':['read','prepare'],'reason':'Later grant is not historical proof'})
    assert response.status_code==201,response.text
    engine=create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as connection:
            connection.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:actor,true)"),
                {'org':str(seed_data['org_a_id']),'actor':str(seed_data['user_a_admin_id'])})
            with pytest.raises(DBAPIError,match='current authorized attachment'):
                with connection.begin_nested():
                    connection.execute(text('INSERT INTO property_source_admission '
                        '(organization_id,import_id,document_id,property_id,domain_id,grant_id,admitted_by) '
                        'VALUES(:org,:job,:document,:property,:domain,:grant,:actor)'),
                        {**ids,'org':seed_data['org_a_id'],'actor':seed_data['user_a_admin_id'],'grant':response.json()['id']})
            assert connection.scalar(text('SELECT count(*) FROM property_source_admission WHERE document_id=:document'),ids)==0
            assert connection.scalar(text("SELECT count(*) FROM audit_event WHERE entity_id=:document AND action='property_document.admitted'"),ids)==0
    finally:
        engine.dispose()
    assert not await access(seed_data,'property_document',ids['document'],actor=seed_data['user_a_clerk_id'])
    async with get_tenant_session(seed_data['org_a_id']) as db:
        await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(seed_data['user_a_admin_id'])})
        assert await db.scalar(text('SELECT count(*) FROM property_source_admission WHERE document_id=:document'),ids)==0
        assert await db.scalar(text("SELECT count(*) FROM audit_event WHERE entity_id=:document AND action='property_document.admitted'"),ids)==0


async def test_receipt_is_immutable_audited_tenant_scoped_and_keeps_original_binding(client_a_admin,seed_data):
    ids=await setup(client_a_admin,seed_data)
    other=await client_a_admin.post('/api/properties',json={'name':'Different admission target','management_type':'WEG'})
    assert other.status_code==201
    async with get_tenant_session(seed_data['org_a_id']) as db:
        await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(seed_data['user_a_admin_id'])})
        receipt=await db.scalar(text('SELECT id FROM property_source_admission WHERE document_id=:document'),ids)
        assert receipt is not None
        audit=(await db.execute(text("SELECT actor_user_id,payload_diff FROM audit_event "
            "WHERE entity_id=:document AND action='property_document.admitted'"),ids)).one()
        assert audit.actor_user_id==seed_data['user_a_admin_id']
        assert audit.payload_diff=={key:str(ids[value]) for key,value in [
            ('import_id','job'),('property_id','property'),('domain_id','domain'),('grant_id','uploader_grant')]}
        for statement in ['UPDATE property_source_admission SET admitted_by=admitted_by WHERE id=:id',
                          'DELETE FROM property_source_admission WHERE id=:id',
                          'INSERT INTO property_source_admission SELECT * FROM property_source_admission WHERE id=:id']:
            with pytest.raises(DBAPIError,match='permission denied'):
                async with db.begin_nested():
                    await db.execute(text(statement),{'id':receipt})
        with pytest.raises(DBAPIError,match='admitted property'):
            async with db.begin_nested():
                await db.execute(text('UPDATE property_document SET property_id=:property WHERE id=:document'),
                    {**ids,'property':uuid.UUID(other.json()['id'])})
        with pytest.raises(DBAPIError,match='foreign key'):
            async with db.begin_nested():
                await db.execute(text('DELETE FROM declaration_import_document WHERE import_id=:job AND document_id=:document'),ids)
    async with get_tenant_session(seed_data['org_b_id']) as db:
        await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(seed_data['user_b_admin_id'])})
        assert await db.scalar(text('SELECT count(*) FROM property_source_admission WHERE id=:id'),{'id':receipt})==0


async def test_receipt_and_audit_rollback_with_failed_attachment(client_a_admin,seed_data):
    from app.models.property_document import PropertyDocument
    from app.models.declaration_import import DeclarationImportDocument
    ids=await setup(client_a_admin,seed_data)
    document_id=uuid.uuid4()
    async with get_tenant_session(seed_data['org_a_id']) as db:
        await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(seed_data['user_a_admin_id'])})
        savepoint=await db.begin_nested()
        document=PropertyDocument(id=document_id,organization_id=seed_data['org_a_id'],doc_type='ANNEX',title='Rolled back',
            original_filename='rollback.pdf',mime_type='application/pdf',content_byte_size=1,content_sha256='c'*64,
            storage_uri='synthetic/rollback',storage_backend='TEST',page_count=1,uploaded_by=seed_data['user_a_admin_id'])
        db.add(document)
        await db.flush()
        db.add(DeclarationImportDocument(organization_id=seed_data['org_a_id'],import_id=ids['job'],
            document_id=document_id,sequence_no=2,document_role='ANNEX'))
        await db.flush()
        assert await db.scalar(text('SELECT count(*) FROM property_source_admission WHERE document_id=:id'),{'id':document_id})==1
        await savepoint.rollback()
        assert await db.scalar(text('SELECT count(*) FROM property_source_admission WHERE document_id=:id'),{'id':document_id})==0
        assert await db.scalar(text("SELECT count(*) FROM audit_event WHERE entity_id=:id AND action='property_document.admitted'"),{'id':document_id})==0


async def test_migrator_guard_and_rollback_preserve_admission_evidence(client_a_admin,seed_data):
    import importlib
    from sqlalchemy import create_engine
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    ids=await setup(client_a_admin,seed_data)
    migration=importlib.import_module('migrations.versions.0050_property_source_admission')
    engine=create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as connection:
            for statement in ['UPDATE property_source_admission SET admitted_by=admitted_by WHERE document_id=:document',
                              'DELETE FROM property_source_admission WHERE document_id=:document']:
                with pytest.raises(DBAPIError,match='admission evidence is immutable'):
                    with connection.begin_nested():
                        connection.execute(text(statement),ids)
            connection.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:actor,true)"),
                {'org':str(seed_data['org_a_id']),'actor':str(seed_data['user_a_admin_id'])})
            with pytest.raises(DBAPIError,match='current authorized attachment'):
                with connection.begin_nested():
                    connection.execute(text('INSERT INTO property_source_admission '
                        '(organization_id,import_id,document_id,property_id,domain_id,grant_id,admitted_by) '
                        'SELECT organization_id,import_id,document_id,property_id,domain_id,:invalid,admitted_by '
                        'FROM property_source_admission WHERE document_id=:document'),{**ids,'invalid':uuid.uuid4()})
            with Operations.context(MigrationContext.configure(connection)):
                with pytest.raises(DBAPIError,match='Retain populated'):
                    with connection.begin_nested():
                        migration.downgrade()
                nested=connection.begin_nested()
                try:
                    connection.execute(text('TRUNCATE property_source_admission'))
                    migration.downgrade()
                    assert connection.scalar(text("SELECT to_regclass('property_source_admission')")) is None
                    migration.upgrade()
                finally:
                    nested.rollback()
            assert connection.scalar(text('SELECT count(*) FROM property_source_admission WHERE document_id=:document'),ids)==1
            assert connection.scalar(text("SELECT count(*) FROM audit_event WHERE entity_id=:document AND action='property_document.admitted'"),ids)==1
    finally:
        engine.dispose()
