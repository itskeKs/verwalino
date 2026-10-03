"""Source consumption survives corrections and derivative identity changes."""
import uuid
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from tests.test_financial_scope_boundary import worker_tenant, scope_seed  # noqa: F401
from tests.test_invoice_source_custody import store, fixture
from tests.test_scoped_account_statements import client
from tests.test_scope_payment_reconciliation import read_value
from tests.test_allocation_adoptions import confirm_grant, ready, command

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize('first,second,expected', [
    ((None,None),(1,1),True), ((1,1),(None,None),True), ((None,None),(None,None),True),
    ((1,2),(3,4),False), ((3,4),(1,2),False), ((1,3),(3,4),True), ((3,4),(1,3),True),
    ((1,8),(2,3),True), ((2,3),(1,8),True), ((2147483647,2147483647),(1,1),False),
])
async def test_inclusive_source_interval_semantics(migrator_session, first, second, expected):
    assert await migrator_session.scalar(text('SELECT source_intervals_overlap(:a,:b,:c,:d)'), dict(zip('abcd', (*first,*second)))) is expected


@pytest.mark.parametrize('span', [(None,1),(1,None),(0,1),(3,2)])
async def test_invalid_source_interval_is_not_an_unused_range(migrator_session, span):
    with pytest.raises(DBAPIError, match='Exact inclusive'):
        async with migrator_session.begin_nested():
            await migrator_session.scalar(text('SELECT source_intervals_overlap(:a,:b,NULL,NULL)'), dict(zip('ab', span)))


async def test_atomic_whole_source_claim_replay_and_private_roles(migrator_session, scope_seed, store):
    import asyncio
    ids = await fixture(migrator_session, scope_seed); await confirm_grant(ids)
    async with client(ids) as api:
        _, _, review, preview, url = await ready(api, ids)
        body = command(review, preview)
        results = await asyncio.gather(api.post(url, **body), api.post(url, **body))
        assert all(result.status_code == 201 for result in results), [r.text for r in results]
        assert results[0].json() == results[1].json()
        root = uuid.UUID(results[0].json()['id'])
    assert await read_value('SELECT count(*) FROM allocation_source_claim WHERE organization_id=:org', ids) == 1
    assert await read_value('SELECT count(*) FROM allocation_source_member WHERE organization_id=:org', ids) == 1
    claim = await read_value('SELECT to_jsonb(c) FROM allocation_source_claim c WHERE organization_id=:org', ids)
    assert claim['root_allocation_id'] == str(root) and claim['page_start'] is None and claim['page_end'] is None
    assert not claim['migration_overlap']
    assert await read_value('SELECT allocation_source_overlap(:org,:hash,1,1,NULL)', {**ids, 'hash': claim['source_sha256']})
    for role in ('app_rw','app_worker','app_review'):
        for table in ('allocation_source_claim','allocation_source_member'):
            assert not await read_value("SELECT has_table_privilege(:role,:table,'SELECT,INSERT,UPDATE,DELETE')", {'role': role,'table': table})
        assert not await read_value("SELECT has_function_privilege(:role,'allocation_source_overlap(uuid,text,integer,integer,uuid)','EXECUTE')", {'role': role})
    with pytest.raises(DBAPIError, match='permission denied'):
        await read_value('SELECT allocation_source_overlap(:org,:hash,1,1,NULL)', {**ids, 'hash': claim['source_sha256']}, actor_key='clerk')
    for table in ('allocation_source_claim','allocation_source_member'):
        with pytest.raises(DBAPIError, match='immutable|Immutable'):
            await read_value(f'DELETE FROM {table} WHERE organization_id=:org RETURNING id', ids)


async def test_correction_inherits_root_and_failed_transaction_leaves_no_membership(migrator_session, scope_seed, store, monkeypatch):
    from fastapi import HTTPException
    from tests.test_allocation_correction_preview import prepared, preview_url
    from tests.test_allocation_corrections import command as correction_command
    from app.services.allocation_adoption_service import AllocationAdoptionService
    ids = await fixture(migrator_session, scope_seed); await confirm_grant(ids)
    async with client(ids) as api:
        old, _, review, _ = await prepared(api, ids)
        original_call = AllocationAdoptionService.call
        async def interrupted(self, sql, **params):
            result = await original_call(self, sql, **params)
            if sql.startswith('SELECT correct_allocation('): raise HTTPException(503,'Injected post-write rollback')
            return result
        preview = (await api.get(preview_url(old,review))).json()
        body = correction_command(old,review,preview)
        url = '/api/allocation-sets/' + old['id'] + '/corrections'
        with monkeypatch.context() as patch:
            patch.setattr(AllocationAdoptionService,'call',interrupted)
            assert (await api.post(url,**body)).status_code == 503
        assert await read_value('SELECT count(*) FROM allocation_source_member WHERE organization_id=:org',ids) == 1
        result = await api.post(url,**body); assert result.status_code == 201,result.text
        assert (await api.post(url,**body)).json() == result.json()
    assert await read_value('SELECT count(*) FROM allocation_source_claim WHERE organization_id=:org',ids) == 1
    assert await read_value('SELECT count(*) FROM allocation_source_member WHERE organization_id=:org',ids) == 2
    assert await read_value('SELECT count(DISTINCT claim_id) FROM allocation_source_member WHERE organization_id=:org',ids) == 1
    assert str(await read_value('SELECT root_allocation_id FROM allocation_source_claim WHERE organization_id=:org',ids)) == old['id']


async def test_migration_preserves_legacy_collisions_and_seals_private_claims(migrator_session, scope_seed):
    import importlib
    from sqlalchemy import create_engine
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    ids = await fixture(migrator_session, scope_seed)
    migration = importlib.import_module('migrations.versions.0119_allocation_source_claims')
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
            before = db.scalar(text("SELECT pg_get_functiondef('access_coverage_manifest(uuid,jsonb)'::regprocedure)"))
            point = db.begin_nested()
            try:
                db.execute(text('TRUNCATE allocation_source_claim CASCADE'))
                importlib.import_module('migrations.versions.0121_visual_allocation_sources').downgrade()
                db.execute(text('TRUNCATE visual_extract_attestation CASCADE'))
                importlib.import_module('migrations.versions.0120_allocation_source_aliases').downgrade()
                migration.downgrade()
                roots = []
                # Historically retained legacy roots may legitimately predate the
                # reviewed-root uniqueness boundary. Preserve both, never guess.
                for number in range(2):
                    row = {**ids, 'cost': uuid.uuid4(), 'document': uuid.uuid4(), 'allocation': uuid.uuid4(), 'hash': 'c'*64, 'number': 'LEGACY-'+str(number)}
                    db.execute(text("""INSERT INTO cost_entry(id,organization_id,property_id,cost_category_id,cost_category_plan_version,invoice_date,
                      service_period_from,service_period_to,gross_amount,tenant_apportionable,status,source,confirmed_at,confirmed_by,document_number)
                      SELECT :cost,:org,:property,id,1,'2025-01-01','2025-01-01','2025-12-31',17,'NICHT_UMLAGEFAEHIG','confirmed','MANUAL',now(),:actor,:number
                      FROM cost_category WHERE code='GRUNDSTEUER' AND plan_version=1"""), row)
                    db.execute(text("""INSERT INTO invoice_document(id,organization_id,cost_entry_id,storage_uri,storage_backend,mime_type,content_byte_size,
                      content_sha256,version,chain_root_id,status,released_at,released_by,retention_until)
                      VALUES(:document,:org,:cost,'synthetic/legacy-source','LOCAL_FS','application/pdf',1,:hash,1,:document,'active',now(),:actor,'2033-12-31')"""), row)
                    db.execute(text("""INSERT INTO allocation_set(id,organization_id,kind,source_document_id,primary_cost_id,property_id,cost_count,
                      content_sha256,actor_id,reason,whole_original_disclosure_reviewed,confirmed_at)
                      VALUES(:allocation,:org,'proven_legacy',:document,:cost,:property,1,:hash,:actor,'Proven historical source link',false,now())"""), row)
                    db.execute(text("INSERT INTO document_cost_link(organization_id,allocation_set_id,source_document_id,cost_entry_id,position) VALUES(:org,:allocation,:document,:cost,1)"), row)
                    roots.append(row['allocation'])
                db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
                migration.upgrade()
                assert before == db.scalar(text("SELECT pg_get_functiondef('access_coverage_manifest(uuid,jsonb)'::regprocedure)"))
                assert db.scalar(text('SELECT count(*) FROM allocation_source_claim WHERE organization_id=:org AND migration_overlap'),ids) == 2
                assert db.scalar(text('SELECT count(DISTINCT claim_id) FROM allocation_source_member WHERE organization_id=:org'),ids) == 2
                assert db.scalar(text('SELECT allocation_source_overlap(:org,:hash,2,3,NULL)'),{**ids,'hash':'c'*64})
                with pytest.raises(DBAPIError,match='Retain allocation source'), db.begin_nested(): migration.downgrade()
                # A whole-root claim cannot be forged into an apparently unused
                # interval or a different original hash, even by a direct caller.
                for change in ({'first':1,'last':1,'hash':'c'*64}, {'first':None,'last':None,'hash':'d'*64}):
                    with pytest.raises(DBAPIError,match='Exact whole-original'), db.begin_nested():
                        db.execute(text('INSERT INTO allocation_source_claim(organization_id,root_allocation_id,source_sha256,page_start,page_end) VALUES(:org,:root,:hash,:first,:last)'),{**ids,'root':roots[0],**change})
                with db.begin_nested() as missing:
                    db.execute(text('TRUNCATE allocation_source_claim CASCADE'))
                    assert db.scalar(text('SELECT allocation_source_overlap(:org,:hash,1,1,NULL)'),{**ids,'hash':'c'*64})
                    with pytest.raises(DBAPIError,match='Physical source already consumed'), db.begin_nested():
                        db.execute(text('INSERT INTO allocation_source_claim(organization_id,root_allocation_id,source_sha256) VALUES(:org,:root,:hash)'),{**ids,'root':roots[0],'hash':'c'*64})
                    missing.rollback()
            finally: point.rollback()
    finally: engine.dispose()


async def test_backfill_reconstructs_correction_chain_and_rejects_corrupt_ancestry(migrator_session, scope_seed, store):
    import importlib
    from sqlalchemy import create_engine
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    from tests.test_allocation_correction_preview import prepared, preview_url
    from tests.test_allocation_corrections import command as correction_command
    ids = await fixture(migrator_session,scope_seed); await confirm_grant(ids)
    async with client(ids) as api:
        old,_,review,_ = await prepared(api,ids)
        result = await api.post('/api/allocation-sets/'+old['id']+'/corrections',
            **correction_command(old,review,(await api.get(preview_url(old,review))).json()))
        assert result.status_code == 201,result.text
        successor = uuid.UUID(result.json()['allocation']['id'])
    migration = importlib.import_module('migrations.versions.0119_allocation_source_claims')
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
            for scenario in ('roundtrip','cycle','hash'):
                point = db.begin_nested()
                try:
                    db.execute(text('TRUNCATE allocation_source_claim CASCADE'))
                    importlib.import_module('migrations.versions.0121_visual_allocation_sources').downgrade()
                    db.execute(text('TRUNCATE visual_extract_attestation CASCADE'))
                    importlib.import_module('migrations.versions.0120_allocation_source_aliases').downgrade()
                    migration.downgrade()
                    if scenario == 'roundtrip':
                        migration.upgrade()
                        assert db.scalar(text('SELECT count(*) FROM allocation_source_claim WHERE organization_id=:org'),ids) == 1
                        assert db.scalar(text('SELECT count(*) FROM allocation_source_member WHERE organization_id=:org'),ids) == 2
                        assert str(db.scalar(text('SELECT root_allocation_id FROM allocation_source_claim WHERE organization_id=:org'),ids)) == old['id']
                    else:
                        # Rehearse pre-migration corruption inside a rolled-back
                        # fixture only; production admission remains immutable.
                        db.execute(text('ALTER TABLE allocation_set DISABLE TRIGGER allocation_set_immutable'))
                        update = 'predecessor_id=id' if scenario == 'cycle' else "content_sha256=repeat('0',64)"
                        db.execute(text('UPDATE allocation_set SET '+update+' WHERE organization_id=:org AND id=:target'),{**ids,'target':successor})
                        db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
                        db.execute(text('ALTER TABLE allocation_set ENABLE TRIGGER allocation_set_immutable'))
                        with pytest.raises(DBAPIError,match='Incomplete or incoherent|Unproven allocation source'), db.begin_nested(): migration.upgrade()
                finally: point.rollback()
    finally: engine.dispose()
