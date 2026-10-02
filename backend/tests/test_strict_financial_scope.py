"""Strict organizations never fall back to unclassified financial access."""
import uuid
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from tests.test_financial_scope_boundary import scope_seed, worker_tenant  # noqa: F401
from tests.test_scope_grants import scope_fixture
from tests.test_scoped_account_statements import client, mutate
from tests.test_scope_payment_reconciliation import read_value
from tests.test_property_source_enforcement import synthetic_enforcement, change_mode

pytestmark = pytest.mark.asyncio


async def fixture(session, seed):
    ids = await scope_fixture(session, seed)
    await session.commit()
    return ids


async def test_strict_unclassified_bank_is_hidden_and_retained(migrator_session, scope_seed):
    ids = await fixture(migrator_session, scope_seed)
    assert await read_value('SELECT count(*) FROM bank_account WHERE id=:bank', ids, actor_key='clerk') == 1
    async with synthetic_enforcement(scope_seed):
        for who in ['actor', 'clerk']:
            assert await read_value('SELECT count(*) FROM bank_account WHERE id=:bank', ids, actor_key=who) == 0
            async with client(ids, who) as api:
                assert (await api.get(f"/api/bank-accounts/{ids['bank']}")).status_code == 404
    assert await read_value('SELECT count(*) FROM bank_account WHERE id=:bank', ids) == 1


async def test_strict_first_financial_insert_requires_scope(migrator_session, scope_seed):
    ids = await fixture(migrator_session, scope_seed)
    async with synthetic_enforcement(scope_seed):
        with pytest.raises(DBAPIError):
            await read_value("INSERT INTO bank_account(organization_id,property_id,account_type,label) VALUES (:org,:other_property,'LAUFEND','No scope') RETURNING id", ids, actor_key='clerk')
        async with client(ids) as api:
            response = await api.post('/api/bank-accounts', json={'property_id': str(ids['other_property']), 'account_type': 'LAUFEND', 'label': 'No scope'})
            assert response.status_code == 409, response.text


async def money_fixture(session, seed):
    ids = await scope_fixture(session, seed)
    ids.update({key: uuid.uuid4() for key in ['cost','plan','line','run','document','payment','attempt','person','owner']})
    await session.execute(text("""INSERT INTO cost_entry(id,organization_id,property_id,cost_category_id,cost_category_plan_version,
        invoice_date,service_period_from,service_period_to,gross_amount,tenant_apportionable,status,source)
        SELECT :cost,:org,:property,id,1,'2025-06-01','2025-01-01','2025-12-31',100,'UMLAGEFAEHIG','needs_review','MANUAL'
        FROM cost_category WHERE code='GRUNDSTEUER' AND plan_version=1"""), ids)
    await session.execute(text("""INSERT INTO economic_plan(id,organization_id,property_id,plan_year,period_start,period_end,status,engine_version)
        VALUES(:plan,:org,:property,2025,'2025-01-01','2025-12-31','draft','test')"""), ids)
    await session.execute(text("""INSERT INTO economic_plan_line(id,organization_id,economic_plan_id,line_type,label,planned_amount,sort_order)
        VALUES(:line,:org,:plan,'PLANNED_COST','Unclassified',100,1)"""), ids)
    await session.execute(text("""INSERT INTO settlement_run(id,organization_id,property_id,run_type,period_start,period_end,status,engine_version,advance_payments_source)
        VALUES(:run,:org,:property,'VERMOEGENSBERICHT','2025-01-01','2025-12-31','draft','test','MANUAL')"""), ids)
    await session.execute(text("INSERT INTO person(id,organization_id,last_name,street,zip_code,city) VALUES(:person,:org,'Synthetic owner','','','')"), ids)
    await session.execute(text('INSERT INTO owner_profile(id,organization_id,person_id) VALUES(:owner,:org,:person)'), ids)
    await session.execute(text("""INSERT INTO payment(id,organization_id,property_id,bank_account_id,direction,amount,value_date,payment_type,status,owner_profile_id)
        VALUES(:payment,:org,:property,:bank,'IN',10,'2025-06-01','HAUSGELD_VORSCHUSS','draft',:owner)"""), ids)
    await session.execute(text("""INSERT INTO invoice_document(id,organization_id,cost_entry_id,storage_uri,storage_backend,mime_type,
        content_byte_size,content_sha256,version,chain_root_id,status,processing_status)
        VALUES(:document,:org,:cost,'synthetic/unknown.pdf','LOCAL_FS','application/pdf',1,repeat('a',64),1,:document,'active','queued')"""), ids)
    await session.execute(text("""INSERT INTO invoice_processing_attempt(id,organization_id,document_id,attempt_no,status,initiated_by)
        VALUES(:attempt,:org,:document,1,'queued',:clerk)"""), ids)
    await session.execute(text("INSERT INTO audit_event(organization_id,entity_type,entity_id,action,payload_diff) VALUES(:org,'cost_entry',:cost,'cost.created','{}')"),ids)
    await session.commit()
    return ids


async def test_strict_financial_children_audit_download_and_job_are_closed(migrator_session, scope_seed, monkeypatch):
    from app.core.db import get_worker_session_factory, set_tenant_context
    from app.services.invoice_processing_service import InvoiceProcessingService
    from app.core.storage import get_store
    ids = await money_fixture(migrator_session, scope_seed)
    monkeypatch.setattr(get_store(), 'iter_chunks', lambda *a, **k: pytest.fail('Unknown finance must not reach storage'))
    async with synthetic_enforcement(scope_seed):
        for table, key in [('cost_entry','cost'),('economic_plan','plan'),('economic_plan_line','line'),('settlement_run','run'),('payment','payment'),('invoice_document','document')]:
            assert await read_value(f'SELECT count(*) FROM {table} WHERE id=:{key}', ids, actor_key='clerk') == 0
            assert await read_value(f'SELECT count(*) FROM {table} WHERE id=:{key}', ids) == 1
        assert await read_value('SELECT count(*) FROM audit_event WHERE entity_id=:cost', ids, actor_key='actor') == 0
        async with client(ids) as api:
            assert (await api.get(f"/api/invoice-documents/{ids['document']}/download")).status_code == 404
        async with get_worker_session_factory()() as db, db.begin():
            await set_tenant_context(db, ids['org'])
            assert await InvoiceProcessingService(db, ids['org']).claim() is None
        assert await read_value('SELECT status FROM invoice_processing_attempt WHERE id=:attempt', ids) == 'canceled'
        assert await read_value("UPDATE bank_account SET label='No edit' WHERE id=:bank RETURNING id", ids, actor_key='clerk') is None
        assert await read_value('DELETE FROM cost_entry WHERE id=:cost RETURNING id', ids, actor_key='clerk') is None


async def test_scoped_first_creation_and_pause_preserve_reads(migrator_session, scope_seed):
    ids = await fixture(migrator_session, scope_seed)
    async with synthetic_enforcement(scope_seed), client(ids) as api:
        body = {'property_id': str(ids['property']), 'account_type': 'LAUFEND', 'label': 'Reviewed', 'accounting_scope_id': str(ids['scope'])}
        result = await api.post('/api/bank-accounts', json=body)
        assert result.status_code == 201, result.text
        ids['new_bank'] = uuid.UUID(result.json()['id'])
        assert await read_value('SELECT scope_id FROM accounting_scope_binding WHERE bank_account_id=:new_bank', ids) == ids['scope']
        assert (await api.get(f"/api/bank-accounts/{ids['new_bank']}")).status_code == 200
        await change_mode(scope_seed, 'PAUSED')
        assert (await api.get(f"/api/bank-accounts/{ids['new_bank']}")).status_code == 200
        # DB protection applies independently of the HTTP adapter's preflight.
        with pytest.raises(DBAPIError, match='scope|capability'):
            await read_value("UPDATE bank_account SET label='Paused write' WHERE id=:new_bank RETURNING id", ids, actor_key='clerk')
        denied = await api.post('/api/bank-accounts', json=body)
        assert denied.status_code in [403, 404], denied.text
        await change_mode(scope_seed, 'ENFORCED')
        await mutate(text('UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE id=:grant'), ids)
        assert (await api.get(f"/api/bank-accounts/{ids['new_bank']}")).status_code == 404
        assert (await api.post('/api/bank-accounts', json=body)).status_code == 404


async def test_financial_scope_migration_preserves_rows_and_function_identity(migrator_session, scope_seed):
    import importlib
    from sqlalchemy import create_engine
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    ids = await money_fixture(migrator_session, scope_seed)
    migration = importlib.import_module('migrations.versions.0081_strict_financial_scope')
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        async with synthetic_enforcement(scope_seed):
            with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
                with pytest.raises(DBAPIError, match='Retain strict financial scope access'):
                    with db.begin_nested():
                        migration.downgrade()
        with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
            point = db.begin_nested()
            try:
                def hashes():
                    return [db.scalar(text(f"SELECT md5(COALESCE(string_agg(to_jsonb(r)::text,',' ORDER BY id),'')) FROM {table} r")) for table in migration.TABLES]
                before = hashes()
                oid = db.scalar(text("SELECT 'financial_scope_access(uuid,text,uuid,text)'::regprocedure::oid"))
                migration.downgrade()
                migration.upgrade()
                assert db.scalar(text("SELECT 'financial_scope_access(uuid,text,uuid,text)'::regprocedure::oid")) == oid
                assert hashes() == before
                assert not db.scalar(text('SELECT property_source_activation_ready(:org)'), ids)
            finally:
                point.rollback()
    finally:
        engine.dispose()


async def test_all_strict_financial_insert_paths_require_scope_and_pause_effects(migrator_session, scope_seed):
    import json
    from app.core.db import get_tenant_session
    ids = await money_fixture(migrator_session, scope_seed)
    kinds = [('bank_account','bank'),('payment','payment'),('cost_entry','cost'),('economic_plan','plan'),('settlement_run','run')]
    rows = {table: await read_value(f'SELECT to_jsonb(r) FROM {table} r WHERE id=:{key}', ids) for table,key in kinds}
    async def insert(table, row, selected=False):
        async with get_tenant_session(ids['org']) as db:
            await db.execute(text("SELECT set_config('app.current_user_id',:actor,true),set_config('app.current_scope_id',:scope,true)"),
                             {'actor':str(ids['clerk']), 'scope':str(ids['scope']) if selected else ''})
            await db.execute(text(f'INSERT INTO {table} SELECT (jsonb_populate_record(NULL::{table},CAST(:record AS jsonb))).*'),
                             {'record':json.dumps(row)})
    async with synthetic_enforcement(scope_seed):
        for table,_ in kinds:
            with pytest.raises(DBAPIError):
                await insert(table, {**rows[table], 'id':str(uuid.uuid4())})
        # Reviewed classification remains a separate authorized operation, and
        # creation can consume that retained bank without inventing another scope.
        await mutate(text("INSERT INTO accounting_scope_binding(organization_id,property_id,scope_id,bank_account_id,origin,reason,reviewed_by) VALUES(:org,:property,:scope,:bank,'REVIEWED','Synthetic reviewed bank',:actor)"), ids)
        created = []
        for table,_ in kinds:
            new_id = uuid.uuid4()
            await insert(table, {**rows[table], 'id':str(new_id)}, selected=True)
            params = {**ids, 'created':new_id}
            assert await read_value(f'SELECT scope_id FROM accounting_scope_binding WHERE {table}_id=:created', params) == ids['scope']
            created.append((table,new_id))
        await change_mode(scope_seed, 'PAUSED')
        for table,_ in kinds:
            with pytest.raises(DBAPIError):
                await insert(table, {**rows[table], 'id':str(uuid.uuid4())}, selected=True)
        for table,resource in created:
            # No data update is allowed even when the financial grant is intact.
            with pytest.raises(DBAPIError):
                await read_value(f'UPDATE {table} SET updated_at=clock_timestamp() WHERE id=:resource RETURNING id',
                                 {**ids,'resource':resource}, actor_key='clerk')


async def test_attempt_metadata_boundary_and_linked_invoice_insertion(migrator_session, scope_seed):
    ids = await money_fixture(migrator_session, scope_seed)
    async with synthetic_enforcement(scope_seed):
        for mode in ['ENFORCED','PAUSED']:
            await change_mode(scope_seed, mode)
            assert await read_value('SELECT count(*) FROM invoice_processing_attempt WHERE id=:attempt', ids, actor_key='clerk') == 0
            assert await read_value("UPDATE invoice_processing_attempt SET processor_version='forged' WHERE id=:attempt RETURNING id", ids, actor_key='clerk') is None
            with pytest.raises(DBAPIError, match='row-level security'):
                await read_value("INSERT INTO invoice_processing_attempt(organization_id,document_id,attempt_no,status,initiated_by) VALUES(:org,:document,2,'queued',:clerk) RETURNING id", ids, actor_key='clerk')
            with pytest.raises(DBAPIError, match='Source scope capability required'):
                await read_value("""INSERT INTO invoice_document(id,organization_id,cost_entry_id,storage_uri,storage_backend,mime_type,
                    content_byte_size,content_sha256,version,chain_root_id,status)
                    VALUES(:new_document,:org,:cost,'synthetic/forged','TEST','application/pdf',1,repeat('b',64),1,:new_document,'active') RETURNING id""",
                    {**ids, 'new_document':uuid.uuid4()}, actor_key='clerk')
        from app.core.db import get_worker_tenant_session
        with pytest.raises(DBAPIError, match='permission denied'):
            await read_value('SELECT invoice_queue_metadata(:org,:attempt)', ids, actor_key='clerk')
        async with get_worker_tenant_session(ids['org']) as db:
            metadata = await db.scalar(text('SELECT invoice_queue_metadata(:org,:attempt)'), ids)
        assert set(metadata) == {'id','document_id','initiated_by','token'}
        assert metadata['id'] == str(ids['attempt'])
        assert await read_value('SELECT invoice_queue_count(:org)', ids, actor_key='clerk') == 1
        with pytest.raises(DBAPIError, match='worker and tenant required'):
            async with get_worker_tenant_session(ids['org']) as db:
                await db.scalar(text('SELECT invoice_queue_metadata(:other_org,:attempt)'), {**ids,'other_org':uuid.uuid4()})
        assert await read_value('SELECT processor_version FROM invoice_processing_attempt WHERE id=:attempt', ids) is None


async def test_strict_payment_insert_waits_for_acl_before_financial_lock(migrator_session, scope_seed):
    import asyncio
    import json
    from app.core.db import get_tenant_session
    from sqlalchemy.ext.asyncio import create_async_engine
    from tests.conftest import MIGRATOR_URL
    ids = await money_fixture(migrator_session, scope_seed)
    row = await read_value('SELECT to_jsonb(r) FROM payment r WHERE id=:payment', ids)
    row['id'] = str(uuid.uuid4())
    marker = 'strict-payment-lock-'+uuid.uuid4().hex
    async def write():
        with pytest.raises(DBAPIError, match='Strict finance requires'):
            await read_value('INSERT INTO payment SELECT (jsonb_populate_record(NULL::payment,CAST(:record AS jsonb))).* /* '+marker+' */',
                             {**ids, 'record':json.dumps(row)}, actor_key='clerk')
    engine = create_async_engine(MIGRATOR_URL)
    task = None
    try:
        async with synthetic_enforcement(scope_seed):
            async with get_tenant_session(ids['org']) as locker:
                await locker.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"), {'key':'scope-acl:'+str(ids['org'])})
                task = asyncio.create_task(write())
                async with engine.connect() as observer:
                    blocked = False
                    for _ in range(300):
                        await observer.execute(text('SELECT pg_stat_clear_snapshot()'))
                        blocked = await observer.scalar(text("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE wait_event_type='Lock' AND query LIKE :query)"), {'query':'%'+marker+'%'})
                        if blocked: break
                        await asyncio.sleep(.01)
                    assert blocked and not task.done()
                assert await locker.scalar(text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key,0))"), {'key':'financial-inputs:'+str(ids['org'])})
            await asyncio.wait_for(task, 5)
    finally:
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await engine.dispose()
