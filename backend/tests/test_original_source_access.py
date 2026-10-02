"""Ordinary original/import rows and explicit legacy-review exceptions."""
import uuid
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from app.core.db import get_tenant_session
from tests.test_derived_source_access import committed_scope, actor_session  # noqa: F401
from tests.test_financial_scope_boundary import scope_seed, worker_tenant  # noqa: F401
from tests.test_scoped_account_statements import client
from tests.test_declaration_job_principal import new_draft
from tests.test_property_source_enforcement import synthetic_enforcement,change_mode
from tests.test_declaration_import_api import isolated_document_store,enable_declaration_imports  # noqa: F401

pytestmark=pytest.mark.asyncio


def actors(seed):
    return {'org':seed['org_a_id'],'actor':seed['user_a_admin_id'],'clerk':seed['user_a_clerk_id']}


async def test_strict_original_and_import_rows_are_private_and_pause_rejects_writes(committed_scope):
    seed=committed_scope
    ids=actors(seed)
    job=uuid.UUID(await new_draft(ids))
    async with synthetic_enforcement(seed),get_tenant_session(ids['org']) as db:
        await actor_session(db,ids['clerk'])
        assert await db.scalar(text('SELECT count(*) FROM declaration_import WHERE id=:id'),{'id':job})==0
        assert await db.scalar(text('SELECT count(*) FROM property_document'))==0
        await actor_session(db,ids['actor'])
        assert await db.scalar(text('SELECT count(*) FROM declaration_import WHERE id=:id'),{'id':job})==1
        assert await db.scalar(text('SELECT count(*) FROM property_document'))==1
    async with synthetic_enforcement(seed):
        await change_mode(seed,'PAUSED')
        async with get_tenant_session(ids['org']) as db:
            await actor_session(db,ids['actor'])
            assert await db.scalar(text('SELECT count(*) FROM property_document'))==1
            assert (await db.execute(text("UPDATE property_document SET title='Paused'"))).rowcount==0
            assert (await db.execute(text("UPDATE declaration_import SET progress_percent=1 WHERE id=:id"),{'id':job})).rowcount==0
        async with client(ids,'actor') as api:
            assert (await api.post('/api/declaration-imports',json={'mode':'NEW_PROPERTY'})).status_code==404


async def test_raw_creation_cannot_forge_original_or_import_creator(committed_scope):
    seed=committed_scope
    ids=actors(seed)
    async with synthetic_enforcement(seed),get_tenant_session(ids['org']) as db:
        await actor_session(db,ids['clerk'])
        for sql in [
            "INSERT INTO declaration_import(organization_id,mode,status,initiated_by,max_cost_usd) VALUES(:org,'NEW_PROPERTY','DRAFT',:actor,1)",
            "INSERT INTO property_document(organization_id,doc_type,title,original_filename,mime_type,content_byte_size,content_sha256,storage_uri,storage_backend,page_count,uploaded_by) VALUES(:org,'DECLARATION','Forged','forged.pdf','application/pdf',1,repeat('a',64),'synthetic/forged','TEST',1,:actor)",
        ]:
            with pytest.raises(DBAPIError) as denied:
                async with db.begin_nested(): await db.execute(text(sql),ids)
            assert denied.value.orig.sqlstate=='42501'


@pytest.mark.parametrize('purpose',['admission','classification'])
async def test_review_exception_does_not_open_ordinary_rows(committed_scope,purpose):
    from tests.test_property_source_export import content_source,export_grant
    from tests.test_property_source_admission_review import pending_body
    from tests.test_bound_source_access import classify
    seed=committed_scope
    ids=actors(seed)
    async with client(ids,'actor') as api:
        source,old,url=await content_source(api,seed,purpose)
        await export_grant(api,seed,source,old)
        async with synthetic_enforcement(seed):
            async with get_tenant_session(ids['org']) as db:
                await actor_session(db,ids['actor'])
                assert await db.scalar(text('SELECT count(*) FROM property_document WHERE id=:document'),source)==0
                if purpose=='admission':
                    assert await db.scalar(text('SELECT count(*) FROM declaration_import WHERE id=:job'),source)==0
            route='/api/property-source-admission-reviews/pending/page' if purpose=='admission' else '/api/property-source-classifications/pending/page'
            page=await api.get(route,params={'limit':1})
            assert page.status_code==200,page.text
            assert any(row['document_id']==str(source['document']) for row in page.json()['items'])
            preview=await api.get(url)
            assert preview.status_code==200 and preview.content==b'%PDF-1.4\n%%EOF',preview.text
            if purpose=='admission':
                response=await api.post('/api/property-source-admission-reviews',headers={'Idempotency-Key':str(uuid.uuid4())},json=await pending_body(api,source))
                assert response.status_code==201,response.text
            else: await classify(api,source)
            async with get_tenant_session(ids['org']) as db:
                await actor_session(db,ids['actor'])
                assert await db.scalar(text('SELECT count(*) FROM property_document WHERE id=:document'),source)==1


async def test_review_metadata_paging_is_bounded_and_cursor_is_actor_query_bound(committed_scope):
    from tests.test_property_source_admission_review import legacy_source
    seed=committed_scope
    ids=actors(seed)
    async with client(ids,'actor') as api:
        sources=[await legacy_source(api,seed) for _ in range(2)]
        async with synthetic_enforcement(seed):
            first=await api.get('/api/property-source-admission-reviews/pending/page',params={'limit':1})
            assert first.status_code==200,first.text
            cursor=first.json()['next_cursor']
            assert cursor and len(first.json()['items'])==1
            second=await api.get('/api/property-source-admission-reviews/pending/page',params={'limit':1,'cursor':cursor})
            assert second.status_code==200,second.text
            assert second.json()['next_cursor'] is None
            assert {r['document_id'] for r in first.json()['items']+second.json()['items']}=={str(s['document']) for s in sources}
            assert (await api.get('/api/property-source-admission-reviews/pending/page',params={'q':'different','cursor':cursor})).status_code==422
            async with client(ids) as other:
                assert (await other.get('/api/property-source-admission-reviews/pending/page',params={'cursor':cursor})).status_code==422
            async with get_tenant_session(ids['org']) as db:
                await actor_session(db,ids['actor'])
                for limit in (0,202):
                    assert await db.scalar(text("SELECT count(*) FROM property_source_admission_candidates(:org,NULL,:limit,'')"),{'org':ids['org'],'limit':limit})==0
                for purpose in ('SOURCE','unknown',None):
                    assert await db.scalar(text('SELECT count(*) FROM property_source_review_original(:org,:document,:purpose)'),
                        {**sources[0],'org':ids['org'],'purpose':purpose})==0
                await actor_session(db,ids['clerk'])
                assert await db.scalar(text("SELECT count(*) FROM property_source_admission_candidates(:org,NULL,201,'')"),{'org':ids['org']})==0
                assert await db.scalar(text('SELECT count(*) FROM property_source_admission_metadata(:org,:document)'),
                    {**sources[0],'org':ids['org']})==0
                assert await db.scalar(text("SELECT count(*) FROM property_source_review_original(:org,:document,'ADMISSION_REVIEW')"),
                    {**sources[0],'org':ids['org']})==0


async def test_original_migration_retains_strict_state_and_roundtrips(committed_scope):
    import importlib
    from sqlalchemy import create_engine
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    migration=importlib.import_module('migrations.versions.0063_original_source_access')
    seed=committed_scope
    job=await new_draft(actors(seed))
    engine=create_engine(MIGRATOR_SYNC_URL)
    try:
        async with synthetic_enforcement(seed):
            with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
                with pytest.raises(DBAPIError,match='Retain strict original'):
                    with db.begin_nested(): migration.downgrade()
                savepoint=db.begin_nested()
                try:
                    db.execute(text('TRUNCATE property_source_security'))
                    migration.downgrade()
                    assert db.scalar(text("SELECT count(*) FROM pg_policy WHERE polname LIKE 'original_source_%'"))==0
                    migration.upgrade()
                    assert db.scalar(text("SELECT count(*) FROM pg_policy WHERE polname LIKE 'original_source_%'"))==8
                    assert db.scalar(text('SELECT count(*) FROM declaration_import WHERE id=:id'),{'id':uuid.UUID(job)})==1
                    for signature in ['property_import_row_access(uuid,jsonb,text,boolean)',
                            'property_original_row_access(uuid,uuid,uuid,uuid,text)','property_source_review_original(uuid,uuid,text)',
                            'property_source_admission_metadata(uuid,uuid)','property_source_admission_candidates(uuid,uuid,integer,text)']:
                        config=db.scalar(text('SELECT proconfig FROM pg_proc WHERE oid=CAST(:signature AS regprocedure)'),{'signature':signature})
                        assert 'row_security=off' in config and 'search_path=pg_catalog, pg_temp' in config
                    assert not db.scalar(text("SELECT has_function_privilege('app_rw','property_import_context_actor_access(uuid,uuid,uuid,text,uuid,uuid,uuid,text)','EXECUTE')"))
                finally: savepoint.rollback()
    finally: engine.dispose()


@pytest.mark.parametrize('table',['property_document','declaration_import','declaration_import_document',
    'property_source_security','property_source_admission_review','property_source_classification'])
async def test_original_migration_refuses_busy_dependencies(committed_scope,table):
    import importlib
    from sqlalchemy import create_engine
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    migration=importlib.import_module('migrations.versions.0063_original_source_access')
    engine=create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as reader:
            reader.execute(text(f'SELECT 1 FROM {table} LIMIT 1'))
            with pytest.raises(DBAPIError) as denied:
                with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
                    db.execute(text("SET LOCAL lock_timeout='1s'"))
                    migration.upgrade()
            assert denied.value.orig.pgcode=='55P03'
    finally: engine.dispose()


async def test_strict_link_cannot_claim_another_creators_unlinked_original(committed_scope):
    seed=committed_scope
    ids=actors(seed)
    async with synthetic_enforcement(seed):
        async with get_tenant_session(ids['org']) as db:
            await actor_session(db,ids['actor'])
            document=await db.scalar(text("INSERT INTO property_document(organization_id,doc_type,title,original_filename,mime_type,content_byte_size,content_sha256,storage_uri,storage_backend,page_count,uploaded_by) VALUES(:org,'DECLARATION','Private','private.pdf','application/pdf',1,repeat('b',64),'synthetic/unlinked','TEST',1,:actor) RETURNING id"),ids)
        async with client(ids) as api:
            response=await api.post('/api/declaration-imports',json={'mode':'NEW_PROPERTY'})
            assert response.status_code==201,response.text
            job=uuid.UUID(response.json()['id'])
        async with get_tenant_session(ids['org']) as db:
            await actor_session(db,ids['clerk'])
            assert await db.scalar(text('SELECT count(*) FROM property_document WHERE id=:document'),{'document':document})==0
            with pytest.raises(DBAPIError) as denied:
                async with db.begin_nested():
                    await db.execute(text("INSERT INTO declaration_import_document(organization_id,import_id,document_id,sequence_no,document_role) VALUES(:org,:job,:document,1,'DECLARATION')"),
                        {**ids,'job':job,'document':document})
            assert denied.value.orig.sqlstate=='42501'
        async with get_tenant_session(ids['org']) as db:
            await actor_session(db,ids['actor'])
            assert await db.scalar(text('SELECT count(*) FROM property_document WHERE id=:document'),{'document':document})==1


async def test_prepare_cannot_record_an_applying_approval(committed_scope):
    from tests.test_property_source_admission import setup
    seed=committed_scope
    ids=actors(seed)
    async with client(ids,'actor') as api:
        source=await setup(api,seed)  # Explicit read/prepare, no approve.
    async with synthetic_enforcement(seed),get_tenant_session(ids['org']) as db:
        await actor_session(db,ids['actor'])
        with pytest.raises(DBAPIError) as denied:
            async with db.begin_nested():
                await db.execute(text("UPDATE declaration_import SET status='APPLYING',approved_by=:actor,approved_at=clock_timestamp() WHERE id=:job"),
                    {**source,'actor':ids['actor']})
        assert denied.value.orig.sqlstate=='42501'
        assert await db.scalar(text('SELECT status FROM declaration_import WHERE id=:job'),source)=='DRAFT'
