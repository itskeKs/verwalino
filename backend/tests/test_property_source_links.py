"""Every source edge must stay inside one tenant and declaration import."""
import uuid
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.db import get_tenant_session
from tests.test_property_source_context import graph

pytestmark=pytest.mark.asyncio


async def test_unit_cannot_reference_a_foreign_property(seed_data):
    org,rows=await graph(seed_data)
    async with get_tenant_session(seed_data['org_b_id']) as db:
        with pytest.raises(DBAPIError,match='fk_unit_property_tenant'):
            async with db.begin_nested():
                await db.execute(text("INSERT INTO unit (id,organization_id,property_id,number,usage_type) "
                    "VALUES (:id,:org,:property,'foreign','WOHNUNG')"),
                    {'id':uuid.uuid4(),'org':seed_data['org_b_id'],'property':rows['property']})


@pytest.mark.parametrize('edge',['issue','evidence'])
async def test_same_tenant_cross_import_source_edge_is_rejected(seed_data,edge):
    org,first=await graph(seed_data)
    _,second=await graph(seed_data)
    async with get_tenant_session(org) as db:
        with pytest.raises(DBAPIError,match='fk_declaration_.*_import_context'):
            async with db.begin_nested():
                if edge=='issue':
                    await db.execute(text("INSERT INTO declaration_candidate_issue "
                        "(id,organization_id,import_id,candidate_id,code,severity,message) "
                        "VALUES (:id,:org,:import,:candidate,'CROSS','WARNING','Synthetic')"),
                        {'id':uuid.uuid4(),'org':org,'import':first['declaration_import'],'candidate':second['declaration_candidate']})
                else:
                    await db.execute(text("INSERT INTO declaration_candidate_evidence "
                        "(id,organization_id,candidate_id,document_id,pdf_page,quote) "
                        "VALUES (:id,:org,:candidate,:document,1,'Synthetic')"),
                        {'id':uuid.uuid4(),'org':org,'candidate':first['declaration_candidate'],'document':second['property_document']})


async def test_evidence_parent_is_derived_then_frozen_and_keeps_its_source_link(seed_data):
    org,first=await graph(seed_data)
    _,second=await graph(seed_data)
    async with get_tenant_session(org) as db:
        assert await db.scalar(text('SELECT import_id FROM declaration_candidate_evidence WHERE id=:id'),
            {'id':first['declaration_candidate_evidence']})==first['declaration_import']
        with pytest.raises(DBAPIError,match='source context is immutable'):
            async with db.begin_nested():
                await db.execute(text('UPDATE declaration_candidate_evidence SET import_id=:import WHERE id=:id'),
                    {'id':first['declaration_candidate_evidence'],'import':second['declaration_import']})
        with pytest.raises(DBAPIError,match='fk_declaration_evidence_candidate_import_context'):
            async with db.begin_nested():
                await db.execute(text("INSERT INTO declaration_candidate_evidence "
                    "(id,organization_id,import_id,candidate_id,document_id,pdf_page,quote) "
                    "VALUES (:id,:org,:import,:candidate,:document,1,'Synthetic')"),
                    {'id':uuid.uuid4(),'org':org,'import':second['declaration_import'],
                     'candidate':first['declaration_candidate'],'document':second['property_document']})
        with pytest.raises(DBAPIError,match='fk_declaration_evidence_document_import_context'):
            async with db.begin_nested():
                await db.execute(text('DELETE FROM declaration_import_document WHERE id=:id'),
                    {'id':first['declaration_import_document']})
        # Import-level issues deliberately have no candidate.
        await db.execute(text("INSERT INTO declaration_candidate_issue "
            "(id,organization_id,import_id,code,severity,message) VALUES (:id,:org,:import,'GENERAL','WARNING','Synthetic')"),
            {'id':uuid.uuid4(),'org':org,'import':first['declaration_import']})


@pytest.mark.parametrize('legacy',['valid','unit','issue','evidence'])
async def test_migration_proves_legacy_links_before_expanding(seed_data,legacy):
    import importlib
    from sqlalchemy import create_engine
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    org,first=await graph(seed_data)
    _,second=await graph(seed_data)
    migration=importlib.import_module('migrations.versions.0048_property_source_links')
    engine=create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as connection:
            nested=connection.begin_nested()
            try:
                with Operations.context(MigrationContext.configure(connection)):
                    # Admission is a later dependent boundary. The outer nested
                    # rollback retains all receipts and restores its policy.
                    connection.execute(text('TRUNCATE property_source_admission'))
                    importlib.import_module('migrations.versions.0050_property_source_admission').downgrade()
                    with pytest.raises(DBAPIError,match='cannot remove populated source link'):
                        with connection.begin_nested():
                            migration.downgrade()
                    connection.execute(text('TRUNCATE unit,declaration_candidate_evidence,declaration_candidate_issue CASCADE'))
                    migration.downgrade()
                    evidence_id=uuid.uuid4()
                    connection.execute(text("INSERT INTO declaration_candidate_evidence "
                        "(id,organization_id,candidate_id,document_id,pdf_page,quote) "
                        "VALUES (:id,:org,:candidate,:document,1,'Unchanged legacy quote')"),
                        {'id':evidence_id,'org':org,'candidate':first['declaration_candidate'],
                         'document':second['property_document'] if legacy=='evidence' else first['property_document']})
                    connection.execute(text("INSERT INTO unit (id,organization_id,property_id,number,usage_type) "
                        "VALUES (:id,:org,:property,'legacy','WOHNUNG')"),
                        {'id':uuid.uuid4(),'org':seed_data['org_b_id'] if legacy=='unit' else org,'property':first['property']})
                    connection.execute(text("INSERT INTO declaration_candidate_issue "
                        "(id,organization_id,import_id,candidate_id,code,severity,message) "
                        "VALUES (:id,:org,:import,:candidate,'LEGACY','WARNING','Synthetic')"),
                        {'id':uuid.uuid4(),'org':org,'import':first['declaration_import'],
                         'candidate':second['declaration_candidate'] if legacy=='issue' else first['declaration_candidate']})
                    if legacy=='valid':
                        migration.upgrade()
                        assert connection.execute(text('SELECT import_id,quote FROM declaration_candidate_evidence WHERE id=:id'),
                            {'id':evidence_id}).one()==(first['declaration_import'],'Unchanged legacy quote')
                    else:
                        expected={'unit':'units=1, issues=0, evidence=0','issue':'units=0, issues=1, evidence=0',
                                  'evidence':'units=0, issues=0, evidence=1'}[legacy]
                        with pytest.raises(DBAPIError,match=expected):
                            with connection.begin_nested():
                                migration.upgrade()
                        assert not connection.scalar(text("SELECT EXISTS(SELECT 1 FROM information_schema.columns "
                            "WHERE table_name='declaration_candidate_evidence' AND column_name='import_id')"))
                        assert connection.scalar(text('SELECT quote FROM declaration_candidate_evidence WHERE id=:id'),
                            {'id':evidence_id})=='Unchanged legacy quote'
            finally:
                nested.rollback()
    finally:
        engine.dispose()


async def test_worker_readiness_requires_expanded_evidence_context(seed_data):
    from app.workers.declaration_import import DeclarationImportWorker
    from tests.test_scoped_account_statements import mutate
    worker=DeclarationImportWorker(object())
    assert await worker.schema_ready()
    # Catalog-only readiness must refuse an older source shape before claiming
    # jobs. Preserve this disposable column and every source row across the probe.
    await mutate(text('ALTER TABLE declaration_candidate_evidence RENAME COLUMN import_id TO test_old_import_id'),{})
    try:
        assert not await worker.schema_ready()
    finally:
        await mutate(text('ALTER TABLE declaration_candidate_evidence RENAME COLUMN test_old_import_id TO import_id'),{})
    assert await worker.schema_ready()


@pytest.mark.parametrize('activity',['read','write'])
async def test_busy_source_graph_refuses_migration_without_blocking_writer(seed_data,activity):
    import importlib
    from sqlalchemy import create_engine
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    org,rows=await graph(seed_data)
    migration=importlib.import_module('migrations.versions.0048_property_source_links')
    engine=create_engine(MIGRATOR_SYNC_URL)
    try:
        async with get_tenant_session(org) as writer:
            await writer.execute(text("SET LOCAL lock_timeout='1s'"))
            statement=('SELECT id FROM declaration_candidate_evidence WHERE id=:id' if activity=='read'
                else 'UPDATE declaration_candidate_evidence SET quote=quote WHERE id=:id')
            await writer.execute(text(statement),{'id':rows['declaration_candidate_evidence']})
            with pytest.raises(DBAPIError) as caught:
                with engine.begin() as connection:
                    connection.execute(text("SET LOCAL lock_timeout='1s'"))
                    with Operations.context(MigrationContext.configure(connection)):
                        migration.upgrade()
            assert caught.value.orig.pgcode=='55P03'  # lock_not_available, never deadlock_detected
            assert 'could not obtain lock' in str(caught.value.orig)
            # The migration released earlier locks; the existing transaction can
            # finish the normal child-first write order after the refusal.
            await writer.execute(text('UPDATE declaration_candidate_issue SET message=message WHERE id=:id'),
                {'id':rows['declaration_candidate_issue']})
            await writer.execute(text('UPDATE declaration_candidate SET confidence=confidence WHERE id=:id'),
                {'id':rows['declaration_candidate']})
    finally:
        engine.dispose()
