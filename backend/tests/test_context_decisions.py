"""Exact human context assertions, isolated from financial acceptance."""
import uuid
import pytest
from sqlalchemy import text
from tests.test_financial_scope_boundary import worker_tenant,scope_seed  # noqa: F401
from tests.test_invoice_source_custody import store,fixture
from tests.test_scoped_account_statements import client,mutate
from tests.test_scope_payment_reconciliation import read_value
from tests.test_invoice_reviews import command as review_command,proposal
from tests.test_assignment_proposals import command as proposal_command
from tests.test_exception_cases import setup_case,event_command

pytestmark=pytest.mark.asyncio


async def basis(api,ids,blocked=True):
    result=await setup_case(api,ids) if blocked else await proposal(api,ids)
    ref,child,partition,extraction,p=result[:5]
    reply=await api.post('/api/logical-documents/'+child['id']+'/invoice-reviews',**review_command(p))
    assert reply.status_code==201,reply.text
    preview=await api.get('/api/assignment-proposals/'+p['id']+'/context-preview')
    assert preview.status_code==200,preview.text
    return ref,child,partition,extraction,p,reply.json(),preview.json()


def command(p,review,preview,previous=None,**changes):
    return {'headers':{'Idempotency-Key':str(uuid.uuid4()),'If-Match':'"'+(previous or 'none')+'"'},'json':{
      'assignment_proposal_id':p['id'],'invoice_review_id':review['id'],'snapshot_digest':preview['snapshot_digest'],
      'expected_case_event_id':preview['case_event_id'],'quotation':'Synthetic property reference on page 1',
      'page_number':1,'xml_path':None,'comparison_note':'Compared property identity and destination scope with the original',
      'context_reviewed':True,**changes}}


async def test_context_exact_dependency_replay_and_new_evidence(migrator_session,scope_seed,store):
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        ref,child,partition,extraction,p,review,preview=await basis(api,ids)
        url='/api/logical-documents/'+child['id']+'/context-decisions';body=command(p,review,preview)
        result=await api.post(url,**body);assert result.status_code==201,result.text
        assert preview['can_review'] and preview['page_start']==1 and preview['page_end']==1
        first=result.json();assert first['assignment_proposal_id']==p['id'] and first['invoice_review_id']==review['id']
        assert first['method']=='manual_document_comparison' and first['snapshot']==preview['snapshot']
        assert (await api.post(url,**body)).json()==first
        case=(await api.get('/api/exceptions/'+preview['case_id'])).json()
        assert case['resolution_state']=='unresolved' and case['context_decision_id']==first['id']
        action=await api.post('/api/exceptions/'+case['id']+'/events',**event_command(case['revision_id'],'claim'))
        assert action.status_code==201,action.text
        assert action.json()['context_decision_id']==first['id']
        new_p=await api.post('/api/logical-documents/'+child['id']+'/assignment-proposals',**proposal_command(partition,extraction,ids['other_scope'],previous=p['id']))
        assert new_p.status_code==201,new_p.text
        case=(await api.get('/api/exceptions/'+case['id'])).json()
        assert case['dependency_count']==2 and case['context_decision_id'] is None and case['workflow_state']=='open'
        assert (await api.post(url,**body)).json()==first
        assert (await api.post(url,**command(p,review,preview,previous=first['id']))).status_code==409
        assert (await api.get(url)).json()['items'][0]['id']==first['id']
    assert await read_value('SELECT count(*) FROM cost_entry WHERE organization_id=:org',ids)==0
    assert await read_value('SELECT count(*) FROM invoice_document WHERE organization_id=:org',ids)==0
    with pytest.raises(Exception,match='immutable|Immutable'):
        await mutate(text("UPDATE context_decision SET quotation='changed' WHERE organization_id=:org"),ids)


async def test_context_stale_snapshot_review_and_invalid_locators(migrator_session,scope_seed,store):
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        _,child,_,_,p,review,preview=await basis(api,ids,False)
        url='/api/logical-documents/'+child['id']+'/context-decisions'
        for changes in ({'page_number':2},{'page_number':None},{'xml_path':'/Invoice'},{'quotation':' '},{'context_reviewed':False}):
            result=await api.post(url,**command(p,review,preview,**changes));assert result.status_code==422,(changes,result.text)
        body=command(p,review,preview)
        await mutate(text("UPDATE property SET street='Changed synthetic address' WHERE organization_id=:org AND id=:property"),ids)
        assert (await api.post(url,**body)).status_code==409
        preview=(await api.get('/api/assignment-proposals/'+p['id']+'/context-preview')).json()
        review2=await api.post('/api/logical-documents/'+child['id']+'/invoice-reviews',**review_command(p,previous=review['id']))
        assert review2.status_code==201,review2.text
        assert (await api.post(url,**command(p,review,preview))).status_code==409
        assert (await api.post(url,**command(p,review2.json(),preview))).status_code==201


async def test_context_case_version_conflict_rolls_back_decision(migrator_session,scope_seed,store):
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        _,child,_,_,p,review,preview=await basis(api,ids)
        action=await api.post('/api/exceptions/'+preview['case_id']+'/events',**event_command(preview['case_event_id'],'claim'))
        assert action.status_code==201,action.text
        result=await api.post('/api/logical-documents/'+child['id']+'/context-decisions',**command(p,review,preview))
        assert result.status_code==409,result.text
    assert await read_value('SELECT count(*) FROM context_decision WHERE organization_id=:org',ids)==0


async def test_context_property_audience_pause_roles_and_rollback(migrator_session,scope_seed,store,client_b_admin):
    from app.core.db import get_tenant_session,get_worker_session_factory,get_review_session_factory,set_tenant_context
    from sqlalchemy.exc import DBAPIError
    from tests.test_property_source_enforcement import synthetic_enforcement,change_mode
    from tests.test_property_source_access import grant
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api,client(ids,'actor') as admin:
        _,child,_,_,p,review,preview=await basis(api,ids)
        domain,receipt=await grant(admin,scope_seed,ids['property'],actor=ids['clerk'],caps=['read'])
        url='/api/logical-documents/'+child['id']+'/context-decisions';body=command(p,review,preview)
        params={**ids,'logical':uuid.UUID(child['id']),'proposal':uuid.UUID(p['id']),'review':uuid.UUID(review['id']),
          'request':uuid.UUID(body['headers']['Idempotency-Key']),'digest':preview['snapshot_digest'],'event':uuid.UUID(preview['case_event_id'])}
        sql="SELECT create_context_decision(:org,:logical,:proposal,:review,NULL,:request,:digest,:event,'Synthetic property reference on page 1',1,NULL,'Compared property identity and destination scope with the original',true)"
        async with synthetic_enforcement(scope_seed):
            for factory in (get_worker_session_factory(),get_review_session_factory()):
                with pytest.raises(DBAPIError,match='Interactive runtime required|permission denied'):
                    async with factory() as db,db.begin():
                        await set_tenant_context(db,ids['org'])
                        await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(ids['clerk'])})
                        await db.execute(text(sql),params)
            with pytest.raises(RuntimeError,match='simulated crash'):
                async with get_tenant_session(ids['org']) as db:
                    await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(ids['clerk'])})
                    assert await db.scalar(text(sql),params)
                    raise RuntimeError('simulated crash')
            assert await read_value('SELECT count(*) FROM context_decision WHERE organization_id=:org',ids)==0
            first=await api.post(url,**body);assert first.status_code==201,first.text
            target='/api/context-decisions/'+first.json()['id']
            for denied in (admin,client_b_admin):
                assert (await denied.get(target)).status_code==404
                assert (await denied.post(url,**body)).status_code==404
            await change_mode(scope_seed,'PAUSED')
            assert (await api.get(target)).status_code==200
            assert (await api.post(url,**body)).status_code==404
            await change_mode(scope_seed,'ENFORCED')
            assert (await admin.post(f'/api/property-data-domains/{domain}/grants/{receipt}/revoke')).status_code==204
            # Target scope remains readable; copied property facts and case audience do not.
            assert await read_value("SELECT has_scope_capability(:org,:other_scope,'read')",ids,actor_key='clerk')
            assert (await api.get(target)).status_code==404
            assert (await api.get(url)).json()['items']==[]
            assert (await api.get('/api/assignment-proposals/'+p['id']+'/context-preview')).status_code==404
            for suffix in ('','/events','/dependencies'):
                assert (await api.get('/api/exceptions/'+preview['case_id']+suffix)).status_code==404
            with pytest.raises(DBAPIError,match='inaccessible'):
                await read_value('SELECT context_comparison_snapshot(:org,:proposal)',{**ids,'proposal':uuid.UUID(p['id'])},actor_key='clerk')
            assert (await api.post(url,**body)).status_code==404


async def test_context_property_edits_serialize_in_both_orders(migrator_session,scope_seed,store):
    import asyncio
    from sqlalchemy.ext.asyncio import create_async_engine
    from app.core.db import get_tenant_session
    from tests.conftest import MIGRATOR_URL
    ids=await fixture(migrator_session,scope_seed);engine=create_async_engine(MIGRATOR_URL)
    try:
        async with client(ids) as api:
            _,child,_,_,p,review,preview=await basis(api,ids,False)
            url='/api/logical-documents/'+child['id']+'/context-decisions'
            async with engine.connect() as editor:
                tx=await editor.begin()
                await editor.execute(text("UPDATE property SET street='Pending address' WHERE organization_id=:org AND id=:property"),ids)
                # Completed editor trigger also holds financial-inputs: command waits, then rejects the stale digest.
                from tests.test_invoice_source_custody import wait_for_advisory_waiter
                task=asyncio.create_task(api.post(url,**command(p,review,preview)))
                try:
                    await wait_for_advisory_waiter(ids,await editor.scalar(text('SELECT pg_backend_pid()')))
                    await tx.commit()
                    result=await asyncio.wait_for(task,5)
                    assert result.status_code==409,result.text
                finally:
                    if not task.done():task.cancel()
                    await asyncio.gather(task,return_exceptions=True)
            preview=(await api.get('/api/assignment-proposals/'+p['id']+'/context-preview')).json()
            async with get_tenant_session(ids['org']) as decision:
                await decision.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(ids['clerk'])})
                decision_id=await decision.scalar(text("SELECT create_context_decision(:org,:logical,:proposal,:review,NULL,:request,:digest,NULL,'Human quotation',1,NULL,'Compared current identity',true)"),
                  {**ids,'logical':uuid.UUID(child['id']),'proposal':uuid.UUID(p['id']),'review':uuid.UUID(review['id']),'request':uuid.uuid4(),'digest':preview['snapshot_digest']})
                async with engine.connect() as editor:
                    tx=await editor.begin()
                    from sqlalchemy.exc import DBAPIError
                    await editor.execute(text("SET LOCAL lock_timeout='150ms'"))
                    with pytest.raises(DBAPIError,match='lock timeout'):
                        await editor.execute(text("UPDATE property SET street='Would overwrite reviewed snapshot' WHERE organization_id=:org AND id=:property"),ids)
                    await tx.rollback()
            assert (await api.get('/api/context-decisions/'+str(decision_id))).json()['snapshot']==preview['snapshot']
            await mutate(text("UPDATE property SET street='Later address' WHERE organization_id=:org AND id=:property"),ids)
            assert (await api.get('/api/context-decisions/'+str(decision_id))).json()['snapshot']==preview['snapshot']
    finally:await engine.dispose()


async def test_context_coverage_constraints_and_guarded_downgrade(migrator_session,scope_seed,store):
    import importlib
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    from tests.test_access_coverage import capture
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api,client(ids,'actor') as admin:
        _,child,_,_,p,review,preview=await basis(api,ids)
        before=await capture(admin)
        reply=await api.post('/api/logical-documents/'+child['id']+'/context-decisions',**command(p,review,preview));assert reply.status_code==201,reply.text
        decision=reply.json();assert (await capture(admin))['manifest_digest']!=before['manifest_digest']
        _,_,_,_,other,other_review,_=await basis(api,ids,False)
    migration=importlib.import_module('migrations.versions.0104_context_decisions');engine=create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
            with pytest.raises(DBAPIError,match='Retain context comparison'),db.begin_nested():migration.downgrade()
            for field,value in [('invoice_review_id',other_review['id']),('assignment_proposal_id',other['id']),('property_id',str(uuid.uuid4()))]:
                with pytest.raises(DBAPIError,match='foreign key'),db.begin_nested():
                    db.execute(text('''INSERT INTO context_decision SELECT (jsonb_populate_record(NULL::context_decision,
                      to_jsonb(d)||jsonb_build_object('id',gen_random_uuid(),'request_key',gen_random_uuid(),'previous_revision_id',d.id,'revision_number',2,CAST(:field AS text),CAST(:value AS text)))).*
                      FROM context_decision d WHERE d.id=:decision'''),{'decision':uuid.UUID(decision['id']),'field':field,'value':value})
            point=db.begin_nested()
            try:
                db.execute(text('TRUNCATE allocation_proposal CASCADE'))
                importlib.import_module('migrations.versions.0105_allocation_proposals').downgrade()
                db.execute(text('TRUNCATE context_decision CASCADE'))
                oid=db.scalar(text("SELECT 'access_coverage_manifest(uuid,jsonb)'::regprocedure::oid"))
                migration.downgrade();migration.upgrade()
                assert db.scalar(text("SELECT 'access_coverage_manifest(uuid,jsonb)'::regprocedure::oid"))==oid
                assert db.scalar(text('SELECT count(*) FROM context_decision'))==0
            finally:point.rollback()
    finally:engine.dispose()


async def test_context_row_writer_waiting_for_financial_lock_cannot_deadlock(migrator_session,scope_seed,store):
    import asyncio
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.exc import DBAPIError
    from app.core.db import get_tenant_session
    from tests.conftest import MIGRATOR_URL
    from tests.test_invoice_source_custody import wait_for_advisory_waiter
    ids=await fixture(migrator_session,scope_seed);engine=create_async_engine(MIGRATOR_URL)
    try:
        async with client(ids) as api:
            _,child,_,_,p,review,preview=await basis(api,ids,False)
        async with engine.connect() as editor:
            tx=await editor.begin();task=None
            try:
                with pytest.raises(DBAPIError,match='metadata busy'):
                    async with get_tenant_session(ids['org']) as decision:
                        await decision.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(ids['clerk'])})
                        await decision.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('financial-inputs:'||CAST(CAST(:org AS uuid) AS text),0))"),ids)
                        task=asyncio.create_task(editor.execute(text("UPDATE property SET street='Inverted writer' WHERE organization_id=:org AND id=:property"),ids))
                        await wait_for_advisory_waiter(ids,await decision.scalar(text('SELECT pg_backend_pid()')))
                        await decision.scalar(text("SELECT create_context_decision(:org,:logical,:proposal,:review,NULL,:request,:digest,NULL,'Human quotation',1,NULL,'Compared current identity',true)"),
                          {**ids,'logical':uuid.UUID(child['id']),'proposal':uuid.UUID(p['id']),'review':uuid.UUID(review['id']),'request':uuid.uuid4(),'digest':preview['snapshot_digest']})
                await asyncio.wait_for(task,5)
                await tx.rollback()
            finally:
                if task and not task.done():task.cancel()
                if task:await asyncio.gather(task,return_exceptions=True)
        assert await read_value('SELECT count(*) FROM context_decision WHERE organization_id=:org',ids)==0
    finally:await engine.dispose()


async def test_context_event_insert_failure_is_atomic(migrator_session,scope_seed,store):
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        _,child,_,_,p,review,preview=await basis(api,ids)
        await mutate(text("""CREATE FUNCTION synthetic_context_event_failure() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
          IF NEW.action='context_reviewed' THEN RAISE EXCEPTION 'Synthetic case event rejection' USING ERRCODE='23514'; END IF; RETURN NEW; END $$;
          """),{})
        await mutate(text('CREATE TRIGGER synthetic_context_event_failure BEFORE INSERT ON exception_case_event FOR EACH ROW EXECUTE FUNCTION synthetic_context_event_failure()'),{})
        try:
            result=await api.post('/api/logical-documents/'+child['id']+'/context-decisions',**command(p,review,preview))
            assert result.status_code==422,result.text
            assert await read_value('SELECT count(*) FROM context_decision WHERE organization_id=:org',ids)==0
            assert (await api.get('/api/exceptions/'+preview['case_id'])).json()['revision_id']==preview['case_event_id']
        finally:
            await mutate(text('DROP TRIGGER synthetic_context_event_failure ON exception_case_event'),{})
            await mutate(text('DROP FUNCTION synthetic_context_event_failure()'),{})


async def test_context_period_concurrency_and_historical_replay(migrator_session,scope_seed,store):
    import asyncio
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        _,child,_,_,p,review,preview=await basis(api,ids,False)
        url='/api/logical-documents/'+child['id']+'/context-decisions'
        outside=await api.post('/api/logical-documents/'+child['id']+'/invoice-reviews',**review_command(p,previous=review['id'],service_period_from='1900-01-01',service_period_to='1900-01-02'))
        assert outside.status_code==201,outside.text
        assert (await api.post(url,**command(p,outside.json(),preview))).status_code==422
        current=await api.post('/api/logical-documents/'+child['id']+'/invoice-reviews',**review_command(p,previous=outside.json()['id']))
        assert current.status_code==201,current.text
        body=command(p,current.json(),preview)
        results=await asyncio.gather(api.post(url,**body),api.post(url,**command(p,current.json(),preview)))
        assert sorted(r.status_code for r in results)==[201,409]
        first=next(r.json() for r in results if r.status_code==201)
        await api.post('/api/logical-documents/'+child['id']+'/invoice-reviews',**review_command(p,previous=current.json()['id']))
        assert (await api.post(url,**command(p,current.json(),preview,previous=first['id']))).status_code==409
        if results[0].status_code==201:
            assert (await api.post(url,**body)).json()==first
            changed={**body,'json':{**body['json'],'quotation':'Changed request'}}
            assert (await api.post(url,**changed)).status_code==409
        await mutate(text('UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:scope AND revoked_at IS NULL'),ids)
        assert (await api.get('/api/context-decisions/'+first['id'])).status_code==404
        assert (await api.post(url,**body)).status_code==404
