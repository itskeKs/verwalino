"""Immutable single-scope breakdowns conserve reviewed cents without adopting money."""
import uuid
import pytest
from sqlalchemy import text
from tests.test_financial_scope_boundary import worker_tenant,scope_seed  # noqa: F401
from tests.test_invoice_source_custody import store,fixture
from tests.test_scoped_account_statements import client,mutate
from tests.test_scope_payment_reconciliation import read_value
from tests.test_context_decisions import basis,command as context_command
from tests.test_invoice_reviews import command as review_command

pytestmark=pytest.mark.asyncio


async def context(api,ids,credit=False,known_vat=False):
    ref,child,partition,extraction,p,review,preview=await basis(api,ids,False)
    if credit or known_vat:
        reply=await api.post('/api/logical-documents/'+child['id']+'/invoice-reviews',**review_command(p,previous=review['id'],
          document_kind='credit_note' if credit else 'invoice',gross_amount='-119.00' if credit else '119.00',vat_amount=('-19.00' if credit else '19.00') if known_vat else None))
        assert reply.status_code==201,reply.text
        review=reply.json()
    result=await api.post('/api/logical-documents/'+child['id']+'/context-decisions',**context_command(p,review,preview))
    assert result.status_code==201,result.text
    return child,p,review,result.json()


async def lines(ids,credit=False,known_vat=False):
    category=await read_value('SELECT id FROM cost_category WHERE plan_version=1 ORDER BY id LIMIT 1',ids)
    return [{'cost_category_id':str(category),'cost_category_plan_version':1,'description':'Reviewed line '+str(index+1),
      'gross_amount':('-' if credit else '')+amount,'vat_amount':(('-' if credit else '')+vat) if known_vat else None}
      for index,(amount,vat) in enumerate([('59.00','9.00'),('60.00','10.00')])]


def command(decision,rows,previous=None,key=None):
    return {'headers':{'Idempotency-Key':key or str(uuid.uuid4()),'If-Match':'"'+(previous or 'none')+'"'},'json':{
      'context_decision_id':decision['id'],'lines':rows,'reason':'Compared the cost breakdown with the selected original','allocation_reviewed':True}}


@pytest.mark.parametrize('credit,known_vat',[(False,False),(True,True)])
async def test_allocation_conserves_signed_cents_and_replays_without_costs(migrator_session,scope_seed,store,credit,known_vat):
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        child,p,review,decision=await context(api,ids,credit,known_vat)
        rows=await lines(ids,credit,known_vat);body=command(decision,rows);url='/api/logical-documents/'+child['id']+'/allocation-proposals'
        result=await api.post(url,**body);assert result.status_code==201,result.text
        preview=(await api.get('/api/context-decisions/'+decision['id']+'/allocation-preview')).json()
        assert preview['can_propose'] and preview['gross_amount']==review['gross_amount'] and preview['vat_amount']==review['vat_amount']
        first=result.json();assert first['invoice_review_id']==review['id'] and first['context_decision_id']==decision['id']
        assert first['financial_state']=='proposal_only' and first['gross_amount']==review['gross_amount']
        assert [r['gross_amount'] for r in first['lines']]==[r['gross_amount'] for r in rows]
        assert (await api.post(url,**body)).json()==first
        assert (await api.get(url)).json()['items'][0]['id']==first['id']
        changed=[{**r,'description':'Changed line'} for r in rows]
        assert (await api.post(url,**command(decision,changed,key=body['headers']['Idempotency-Key']))).status_code==409
        second=await api.post(url,**command(decision,changed,previous=first['id']));assert second.status_code==201,second.text
        assert second.json()['previous_revision_id']==first['id']
        assert (await api.post(url,**body)).json()==first
    assert await read_value('SELECT count(*) FROM cost_entry WHERE organization_id=:org',ids)==0
    assert await read_value('SELECT count(*) FROM invoice_document WHERE organization_id=:org',ids)==0


async def test_allocation_rejects_rounding_imbalance_unknown_vat_and_wrong_category(migrator_session,scope_seed,store):
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        child,_,_,decision=await context(api,ids);rows=await lines(ids);url='/api/logical-documents/'+child['id']+'/allocation-proposals'
        for changes in ({'gross_amount':'59.01'},{'gross_amount':'59.001'},{'gross_amount':'NaN'},{'gross_amount':59},
                        {'gross_amount':'-59.00'},{'gross_amount':'0.00'},{'vat_amount':'0.00'},
                        {'cost_category_plan_version':999},{'cost_category_id':str(uuid.uuid4())},{'description':' '}):
            result=await api.post(url,**command(decision,[{**rows[0],**changes},rows[1]]));assert result.status_code==422,(changes,result.text)
        for invalid in ([],rows*51):
            assert (await api.post(url,**command(decision,invalid))).status_code==422
    assert await read_value('SELECT count(*) FROM allocation_proposal WHERE organization_id=:org',ids)==0


async def test_allocation_stale_context_property_and_current_authority(migrator_session,scope_seed,store,client_b_admin):
    from tests.test_property_source_enforcement import synthetic_enforcement,change_mode
    from tests.test_property_source_access import grant
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api,client(ids,'actor') as admin:
        child,p,review,decision=await context(api,ids);rows=await lines(ids)
        domain,receipt=await grant(admin,scope_seed,ids['property'],actor=ids['clerk'],caps=['read'])
        url='/api/logical-documents/'+child['id']+'/allocation-proposals';body=command(decision,rows)
        result=await api.post(url,**body);assert result.status_code==201,result.text
        first=result.json();target='/api/allocation-proposals/'+first['id']
        for denied in (admin,client_b_admin):
            assert (await denied.get(target)).status_code==404
            assert (await denied.post(url,**body)).status_code==404
        await mutate(text("UPDATE property SET street='New allocation address' WHERE organization_id=:org AND id=:property"),ids)
        assert (await api.post(url,**command(decision,rows,previous=first['id']))).status_code==409
        assert (await api.post(url,**body)).json()==first
        preview=(await api.get('/api/assignment-proposals/'+p['id']+'/context-preview')).json()
        next_context=await api.post('/api/logical-documents/'+child['id']+'/context-decisions',**context_command(p,review,preview,previous=decision['id']))
        assert next_context.status_code==201,next_context.text
        assert (await api.post(url,**command(decision,rows,previous=first['id']))).status_code==409
        second=await api.post(url,**command(next_context.json(),rows,previous=first['id']));assert second.status_code==201,second.text
        changed_review=await api.post('/api/logical-documents/'+child['id']+'/invoice-reviews',**review_command(p,previous=review['id']))
        assert changed_review.status_code==201,changed_review.text
        assert (await api.post(url,**command(next_context.json(),rows,previous=second.json()['id']))).status_code==409
        async with synthetic_enforcement(scope_seed):
            assert (await api.get(target)).status_code==200
            await change_mode(scope_seed,'PAUSED')
            assert (await api.get(target)).status_code==200
            assert (await api.post(url,**body)).status_code==404
            await change_mode(scope_seed,'ENFORCED')
            assert (await admin.post(f'/api/property-data-domains/{domain}/grants/{receipt}/revoke')).status_code==204
            assert await read_value("SELECT has_scope_capability(:org,:scope,'read')",ids,actor_key='clerk')
            assert (await api.get(target)).status_code==404
            assert (await api.get('/api/context-decisions/'+decision['id']+'/allocation-preview')).status_code==404
            assert (await api.get(url)).json()['items']==[]
            assert (await api.post(url,**body)).status_code==404
            assert await read_value('SELECT count(*) FROM allocation_proposal_line WHERE organization_id=:org',ids,actor_key='clerk')==0


async def test_allocation_roles_concurrent_receipts_and_atomic_line_failure(migrator_session,scope_seed,store):
    import asyncio,json
    from app.core.db import get_worker_session_factory,get_review_session_factory,set_tenant_context
    from sqlalchemy.exc import DBAPIError
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        child,_,_,decision=await context(api,ids);rows=await lines(ids);url='/api/logical-documents/'+child['id']+'/allocation-proposals'
        params={**ids,'logical':uuid.UUID(child['id']),'decision':uuid.UUID(decision['id']),'request':uuid.uuid4(),'lines':json.dumps(rows)}
        for factory in (get_worker_session_factory(),get_review_session_factory()):
            with pytest.raises(DBAPIError,match='Interactive runtime required|permission denied'):
                async with factory() as db,db.begin():
                    await set_tenant_context(db,ids['org'])
                    await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(ids['clerk'])})
                    await db.execute(text("SELECT create_allocation_proposal(:org,:logical,:decision,NULL,:request,CAST(:lines AS jsonb),'Reviewed lines',true)"),params)
        await mutate(text("""CREATE FUNCTION synthetic_allocation_failure() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
          IF NEW.position=2 THEN RAISE EXCEPTION 'Synthetic second line rejection' USING ERRCODE='23514'; END IF; RETURN NEW; END $$"""),{})
        await mutate(text('CREATE TRIGGER synthetic_allocation_failure BEFORE INSERT ON allocation_proposal_line FOR EACH ROW EXECUTE FUNCTION synthetic_allocation_failure()'),{})
        try:
            assert (await api.post(url,**command(decision,rows))).status_code==422
            assert await read_value('SELECT count(*) FROM allocation_proposal WHERE organization_id=:org',ids)==0
            assert await read_value('SELECT count(*) FROM allocation_proposal_line WHERE organization_id=:org',ids)==0
        finally:
            await mutate(text('DROP TRIGGER synthetic_allocation_failure ON allocation_proposal_line'),{})
            await mutate(text('DROP FUNCTION synthetic_allocation_failure()'),{})
        body=command(decision,rows)
        results=await asyncio.gather(api.post(url,**body),api.post(url,**body))
        assert all(r.status_code==201 for r in results),[r.text for r in results]
        assert results[0].json()==results[1].json()
        first=results[0].json()
        results=await asyncio.gather(*[api.post(url,**command(decision,rows,previous=first['id'])) for _ in range(2)])
        assert sorted(r.status_code for r in results)==[201,409]
        with pytest.raises(DBAPIError,match='permission denied'):
            await read_value('DELETE FROM allocation_proposal_line WHERE organization_id=:org RETURNING id',ids,actor_key='clerk')


async def test_allocation_deferred_sql_constraints_seal_complete_line_sets(migrator_session,scope_seed,store):
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    from tests.conftest import MIGRATOR_SYNC_URL
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        child,_,_,decision=await context(api,ids);rows=await lines(ids)
        result=await api.post('/api/logical-documents/'+child['id']+'/allocation-proposals',**command(decision,rows));assert result.status_code==201,result.text
        first=result.json()
    engine=create_engine(MIGRATOR_SYNC_URL);params={**ids,'first':uuid.UUID(first['id'])}
    clone_parent='''INSERT INTO allocation_proposal SELECT (jsonb_populate_record(NULL::allocation_proposal,to_jsonb(a)||
      jsonb_build_object('id',CAST(:new AS uuid),'previous_revision_id',a.id,'revision_number',2,'request_key',gen_random_uuid()))).*
      FROM allocation_proposal a WHERE a.id=:first'''
    clone_lines='''INSERT INTO allocation_proposal_line SELECT (jsonb_populate_record(NULL::allocation_proposal_line,to_jsonb(l)||
      jsonb_build_object('id',gen_random_uuid(),'allocation_proposal_id',CAST(:new AS uuid))||CAST(:change AS jsonb))).*
      FROM allocation_proposal_line l WHERE l.allocation_proposal_id=:first'''
    try:
        with engine.begin() as db:
            # Existing line membership cannot be extended, including an attempted zero-value line.
            for amount in ('0.00','1.00','1.001'):
                with pytest.raises(DBAPIError,match='check constraint|conserve'),db.begin_nested():
                    db.execute(text('''INSERT INTO allocation_proposal_line SELECT (jsonb_populate_record(NULL::allocation_proposal_line,to_jsonb(l)||
                      jsonb_build_object('id',gen_random_uuid(),'position',3,'gross_amount',CAST(:amount AS text)))).*
                      FROM allocation_proposal_line l WHERE allocation_proposal_id=:first ORDER BY position LIMIT 1'''),{**params,'amount':amount})
                    db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
            for change in (None,'{"gross_amount":"50.00"}','{"vat_amount":"0.00"}','{"cost_category_plan_version":999}'):
                with pytest.raises(DBAPIError,match='conserve|foreign key'),db.begin_nested():
                    current={**params,'new':uuid.uuid4()};db.execute(text(clone_parent),current)
                    if change is not None:db.execute(text(clone_lines),{**current,'change':change})
                    db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
            with pytest.raises(DBAPIError,match='immutable|Immutable'),db.begin_nested():
                db.execute(text("UPDATE allocation_proposal_line SET description='Changed' WHERE allocation_proposal_id=:first"),params)
            # A whole new balanced immutable revision is structurally valid, then rolled back by the test.
            point=db.begin_nested()
            try:
                current={**params,'new':uuid.uuid4()};db.execute(text(clone_parent),current)
                db.execute(text(clone_lines),{**current,'change':'{}'});db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
            finally:point.rollback()
        # Deferred validation also executes on COMMIT, not only manual SET CONSTRAINTS.
        with pytest.raises(DBAPIError,match='conserve'):
            with engine.begin() as db:db.execute(text(clone_parent),{**params,'new':uuid.uuid4()})
    finally:engine.dispose()
    assert await read_value('SELECT count(*) FROM allocation_proposal WHERE organization_id=:org',ids)==1


async def test_allocation_category_snapshot_and_inactive_replay(migrator_session,scope_seed,store):
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        child,_,_,decision=await context(api,ids);rows=await lines(ids);body=command(decision,rows);url='/api/logical-documents/'+child['id']+'/allocation-proposals'
        result=await api.post(url,**body);assert result.status_code==201,result.text
        first=result.json();category=uuid.UUID(rows[0]['cost_category_id'])
        old=await read_value('SELECT label FROM cost_category WHERE id=:category',{'category':category})
        await mutate(text("UPDATE cost_category SET is_active=false,label='Changed category label' WHERE id=:category"),{'category':category})
        try:
            assert (await api.post(url,**body)).json()==first
            assert (await api.post(url,**command(decision,rows,previous=first['id']))).status_code==422
            assert (await api.get('/api/allocation-proposals/'+first['id'])).json()['lines'][0]['category_label']==old
        finally:await mutate(text('UPDATE cost_category SET is_active=true,label=:label WHERE id=:category'),{'category':category,'label':old})


async def test_allocation_coverage_provenance_and_guarded_downgrade(migrator_session,scope_seed,store):
    import importlib
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    from tests.test_access_coverage import capture
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api,client(ids,'actor') as admin:
        child,_,_,decision=await context(api,ids);rows=await lines(ids);before=await capture(admin)
        result=await api.post('/api/logical-documents/'+child['id']+'/allocation-proposals',**command(decision,rows));assert result.status_code==201,result.text
        first=result.json();assert (await capture(admin))['manifest_digest']!=before['manifest_digest']
    migration=importlib.import_module('migrations.versions.0105_allocation_proposals');engine=create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
            with pytest.raises(DBAPIError,match='Retain allocation proposal'),db.begin_nested():migration.downgrade()
            for field in ('invoice_review_id','context_decision_id','scope_id','logical_document_id'):
                with pytest.raises(DBAPIError,match='foreign key'),db.begin_nested():
                    db.execute(text('''INSERT INTO allocation_proposal SELECT (jsonb_populate_record(NULL::allocation_proposal,to_jsonb(a)||
                      jsonb_build_object('id',gen_random_uuid(),'request_key',gen_random_uuid(),'previous_revision_id',a.id,'revision_number',2,CAST(:field AS text),gen_random_uuid()))).*
                      FROM allocation_proposal a WHERE a.id=:first'''),{'field':field,'first':uuid.UUID(first['id'])})
            point=db.begin_nested()
            try:
                db.execute(text('TRUNCATE exception_case CASCADE'))
                db.execute(text('TRUNCATE allocation_treatment_review CASCADE'))
                db.execute(text('TRUNCATE allocation_integrity_attestation CASCADE'))
                db.execute(text('TRUNCATE exception_completion CASCADE'))
                importlib.import_module('migrations.versions.0113_allocation_correction_preview').downgrade()
                importlib.import_module('migrations.versions.0112_context_mappings').downgrade()
                importlib.import_module('migrations.versions.0111_context_candidate_source').downgrade()
                importlib.import_module('migrations.versions.0110_exception_completions').downgrade()
                importlib.import_module('migrations.versions.0109_allocation_adoptions').downgrade()
                importlib.import_module('migrations.versions.0108_allocation_treatments').downgrade()
                importlib.import_module('migrations.versions.0107_duplicate_exceptions').downgrade()
                db.execute(text('TRUNCATE duplicate_comparison CASCADE'))
                importlib.import_module('migrations.versions.0106_duplicate_comparisons').downgrade()
                db.execute(text('TRUNCATE allocation_proposal CASCADE'))
                oid=db.scalar(text("SELECT 'access_coverage_manifest(uuid,jsonb)'::regprocedure::oid"))
                migration.downgrade();migration.upgrade()
                assert db.scalar(text("SELECT 'access_coverage_manifest(uuid,jsonb)'::regprocedure::oid"))==oid
            finally:point.rollback()
    finally:engine.dispose()
