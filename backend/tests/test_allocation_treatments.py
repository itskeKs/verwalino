"""Explicit line treatment is recorded separately from financial adoption."""
import uuid
import pytest
from tests.test_financial_scope_boundary import worker_tenant,scope_seed  # noqa: F401
from tests.test_invoice_source_custody import store,fixture
from tests.test_scoped_account_statements import client
from tests.test_scope_payment_reconciliation import read_value
from tests.test_allocation_proposals import context,lines,command as allocation_command

pytestmark=pytest.mark.asyncio


async def allocation(api,ids,credit=False):
    child,_,_,decision=await context(api,ids,credit)
    reply=await api.post('/api/logical-documents/'+child['id']+'/allocation-proposals',**allocation_command(decision,await lines(ids,credit)))
    assert reply.status_code==201,reply.text
    return child,reply.json()


def command(allocation,preview,previous=None,key=None):
    return {'headers':{'Idempotency-Key':key or str(uuid.uuid4()),'If-Match':'"'+(previous or 'none')+'"'},'json':{
      'allocation_proposal_id':allocation['id'],'preview_digest':preview['snapshot_digest'],'reason':'Explicit cost treatment compared with documents','treatment_reviewed':True,
      'lines':[{'allocation_line_id':line['id'],'expense_intent':'operating_expense','tax_intent':'gross_without_input_vat',
        'tenant_treatment':'none','tenant_amount':'0.00','tenant_distribution_key':None,'owner_distribution_key':'MEA',
        'documentary_basis':'Recorded human interpretation; no legal approval'} for line in allocation['lines']]}}


@pytest.mark.parametrize('credit',[False,True])
async def test_treatment_complete_signed_membership_and_historical_replay(migrator_session,scope_seed,store,credit):
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        child,a=await allocation(api,ids,credit)
        preview=await api.get('/api/allocation-proposals/'+a['id']+'/treatment-preview');assert preview.status_code==200,preview.text
        body=command(a,preview.json());url='/api/logical-documents/'+child['id']+'/allocation-treatments'
        first=await api.post(url,**body);assert first.status_code==201,first.text
        value=first.json();assert value['financial_state']=='review_only' and value['marker_policy']=='allocation-treatment-v1'
        assert {r['allocation_line_id'] for r in value['lines']}=={r['id'] for r in a['lines']}
        assert all(r['tenant_amount']=='0.00' for r in value['lines'])
        assert (await api.post(url,**body)).json()==value
        assert (await api.get(url)).json()['items']==[value]
        assert (await api.get('/api/allocation-treatments/'+value['id'])).json()==value
    assert await read_value('SELECT count(*) FROM cost_entry WHERE organization_id=:org',ids)==0


async def test_treatment_rejects_inexact_or_incomplete_lines_and_preserves_unknown(migrator_session,scope_seed,store):
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        child,a=await allocation(api,ids);preview=await api.get('/api/allocation-proposals/'+a['id']+'/treatment-preview')
        assert preview.status_code==200,preview.text
        url='/api/logical-documents/'+child['id']+'/allocation-treatments'
        for change in ({'tenant_amount':'0.001'},{'tenant_amount':'NaN'},{'tenant_amount':0},{'tenant_amount':None},
                       {'tenant_treatment':'full','tenant_amount':'58.00','tenant_distribution_key':'WOHNFLAECHE'},
                       {'tenant_treatment':'partial','tenant_amount':'59.00','tenant_distribution_key':'WOHNFLAECHE'},
                       {'tenant_treatment':'partial','tenant_amount':'-1.00','tenant_distribution_key':'WOHNFLAECHE'},
                       {'tenant_treatment':'none','tenant_distribution_key':'WOHNFLAECHE'},{'owner_distribution_key':'not_applicable'}):
            body=command(a,preview.json());body['json']['lines'][0].update(change)
            reply=await api.post(url,**body);assert reply.status_code==422,(change,reply.text)
        body=command(a,preview.json());body['json']['lines'].pop();assert (await api.post(url,**body)).status_code==422
        body=command(a,preview.json());body['json']['lines'][1]=dict(body['json']['lines'][0]);assert (await api.post(url,**body)).status_code==422
        body=command(a,preview.json());body['json']['lines'][0].update(tenant_treatment='unresolved',tenant_amount=None,expense_intent='unresolved',tax_intent='unresolved',owner_distribution_key='unresolved')
        reply=await api.post(url,**body);assert reply.status_code==201,reply.text
        line=reply.json()['lines'][0];assert line['tenant_amount'] is None
        assert {'tenant_treatment_unresolved','expense_unresolved','tax_unresolved','owner_distribution_unresolved'}<=set(line['markers'])


@pytest.mark.parametrize('credit',[False,True])
async def test_treatment_partial_full_and_evidence_markers(migrator_session,scope_seed,store,credit):
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        child,a=await allocation(api,ids,credit);preview=(await api.get('/api/allocation-proposals/'+a['id']+'/treatment-preview')).json()
        body=command(a,preview);body['json']['lines'][0].update(tenant_treatment='partial',tenant_amount='-10.00' if credit else '10.00',tenant_distribution_key='MANUELLER_BETRAG',owner_distribution_key='VERBRAUCH',tax_intent='input_vat_requested',expense_intent='reserve_related')
        body['json']['lines'][1].update(tenant_treatment='full',tenant_amount=a['lines'][1]['gross_amount'],tenant_distribution_key='WOHNFLAECHE',expense_intent='capitalization_related')
        reply=await api.post('/api/logical-documents/'+child['id']+'/allocation-treatments',**body);assert reply.status_code==201,reply.text
        values=reply.json()['lines']
        assert values[0]['tenant_amount']==('-10.00' if credit else '10.00')
        assert {'reserve_treatment_unsupported','input_vat_treatment_unsupported','tenant_distribution_evidence_required','owner_distribution_evidence_required'}<=set(values[0]['markers'])
        assert 'capitalization_treatment_unsupported' in values[1]['markers']
        assert reply.json()['snapshot']==preview['snapshot']


async def test_treatment_category_mutation_and_authorized_historical_replay(migrator_session,scope_seed,store,client_b_admin):
    from sqlalchemy import text
    from tests.test_scoped_account_statements import mutate
    from tests.test_property_source_enforcement import synthetic_enforcement,change_mode
    from tests.test_property_source_access import grant
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api,client(ids,'actor') as admin:
        child,a=await allocation(api,ids);preview=(await api.get('/api/allocation-proposals/'+a['id']+'/treatment-preview')).json()
        domain,receipt=await grant(admin,scope_seed,ids['property'],actor=ids['clerk'],caps=['read'])
        body=command(a,preview);url='/api/logical-documents/'+child['id']+'/allocation-treatments'
        first=await api.post(url,**body);assert first.status_code==201,first.text
        first=first.json();detail='/api/allocation-treatments/'+first['id']
        for denied in (admin,client_b_admin):
            assert (await denied.get(detail)).status_code==404
            assert (await denied.post(url,**body)).status_code==404
        category=uuid.UUID(a['lines'][0]['cost_category_id']);old=preview['snapshot']['lines'][0]['category']
        await mutate(text("UPDATE cost_category SET label='Changed treatment label',reserve_relevant=NOT reserve_relevant,cost_group='NICHT_UMLAGEFAEHIG' WHERE id=:category"),{'category':category})
        try:
            assert (await api.post(url,**body)).json()==first
            assert (await api.post(url,**command(a,preview,first['id']))).status_code==409
            fresh=(await api.get('/api/allocation-proposals/'+a['id']+'/treatment-preview')).json()
            assert fresh['snapshot_digest']!=preview['snapshot_digest']
            altered=command(a,fresh,first['id']);altered['json']['lines'][0].update(tenant_treatment='full',tenant_amount=a['lines'][0]['gross_amount'],tenant_distribution_key='MEA',expense_intent='reserve_related' if old['reserve_relevant'] else 'operating_expense')
            second=await api.post(url,**altered);assert second.status_code==201,second.text
            assert {'category_tenant_conflict','category_expense_conflict'}<=set(second.json()['lines'][0]['markers'])
            await mutate(text('UPDATE cost_category SET is_active=false WHERE id=:category'),{'category':category})
            assert (await api.post(url,**body)).json()==first
            assert (await api.get(detail)).json()==first
            assert (await api.post(url,**command(a,fresh,second.json()['id']))).status_code==422
        finally:
            await mutate(text('UPDATE cost_category SET is_active=true,label=:label,reserve_relevant=:reserve,cost_group=:group WHERE id=:category'),{'category':category,'label':old['label'],'reserve':old['reserve_relevant'],'group':old['cost_group']})
        async with synthetic_enforcement(scope_seed):
            await change_mode(scope_seed,'PAUSED')
            assert (await api.get(detail)).status_code==200
            assert (await api.post(url,**body)).status_code==404
            await change_mode(scope_seed,'ENFORCED')
            assert (await admin.post(f'/api/property-data-domains/{domain}/grants/{receipt}/revoke')).status_code==204
            assert (await api.get(detail)).status_code==404
            assert (await api.get('/api/allocation-proposals/'+a['id']+'/treatment-preview')).status_code==404
            assert (await api.get(url)).json()['items']==[]
            assert (await api.post(url,**body)).status_code==404
            assert await read_value('SELECT count(*) FROM allocation_treatment_line WHERE organization_id=:org',ids,actor_key='clerk')==0


async def test_treatment_current_allocation_and_context_required(migrator_session,scope_seed,store):
    from sqlalchemy import text
    from tests.test_scoped_account_statements import mutate
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        child,a=await allocation(api,ids);preview=(await api.get('/api/allocation-proposals/'+a['id']+'/treatment-preview')).json()
        body=command(a,preview);url='/api/logical-documents/'+child['id']+'/allocation-treatments'
        first=await api.post(url,**body);assert first.status_code==201,first.text
        changed=await api.post('/api/logical-documents/'+child['id']+'/allocation-proposals',**allocation_command({'id':a['context_decision_id']},await lines(ids),a['id']))
        assert changed.status_code==201,changed.text
        assert (await api.post(url,**command(a,preview,first.json()['id']))).status_code==409
        assert (await api.post(url,**body)).json()==first.json()
        a2=changed.json();p2=(await api.get('/api/allocation-proposals/'+a2['id']+'/treatment-preview')).json()
        await mutate(text("UPDATE property SET street='Changed treatment context' WHERE organization_id=:org AND id=:property"),ids)
        assert (await api.post(url,**command(a2,p2,first.json()['id']))).status_code==409


async def test_treatment_roles_concurrency_and_atomicity(migrator_session,scope_seed,store):
    import asyncio,json
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError
    from tests.test_scoped_account_statements import mutate
    from app.core.db import get_worker_session_factory,get_review_session_factory,set_tenant_context
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        child,a=await allocation(api,ids);preview=(await api.get('/api/allocation-proposals/'+a['id']+'/treatment-preview')).json()
        body=command(a,preview);url='/api/logical-documents/'+child['id']+'/allocation-treatments'
        params={**ids,'logical':uuid.UUID(child['id']),'allocation':uuid.UUID(a['id']),'request':uuid.uuid4(),'preview':preview['snapshot_digest'],'lines':json.dumps(body['json']['lines'])}
        for factory in (get_worker_session_factory(),get_review_session_factory()):
            with pytest.raises(DBAPIError,match='Interactive runtime required|permission denied'):
                async with factory() as db,db.begin():
                    await set_tenant_context(db,ids['org']);await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(ids['clerk'])})
                    await db.execute(text("SELECT create_allocation_treatment(:org,:logical,:allocation,NULL,:request,:preview,CAST(:lines AS jsonb),'Explicit treatment',true)"),params)
        await mutate(text("""DO $setup$ BEGIN
          CREATE OR REPLACE FUNCTION synthetic_treatment_failure() RETURNS trigger LANGUAGE plpgsql AS $f$ BEGIN
          RAISE EXCEPTION 'Synthetic treatment child failure' USING ERRCODE='23514';END $f$;
          CREATE TRIGGER synthetic_treatment_failure BEFORE INSERT ON allocation_treatment_line FOR EACH ROW EXECUTE FUNCTION synthetic_treatment_failure();END $setup$"""),{})
        try:
            assert (await api.post(url,**body)).status_code==422
            assert await read_value('SELECT count(*) FROM allocation_treatment_review WHERE organization_id=:org',ids)==0
        finally:
            await mutate(text('DO $$ BEGIN DROP TRIGGER IF EXISTS synthetic_treatment_failure ON allocation_treatment_line;DROP FUNCTION IF EXISTS synthetic_treatment_failure();END $$'),{})
        replies=await asyncio.gather(api.post(url,**body),api.post(url,**body))
        assert [r.status_code for r in replies]==[201,201],[r.text for r in replies]
        assert replies[0].json()==replies[1].json()
        first=replies[0].json();changed=command(a,preview,key=body['headers']['Idempotency-Key']);changed['json']['reason']='Changed reason'
        assert (await api.post(url,**changed)).status_code==409
        replies=await asyncio.gather(*[api.post(url,**command(a,preview,first['id'])) for _ in range(2)])
        assert sorted(r.status_code for r in replies)==[201,409]
        with pytest.raises(DBAPIError,match='permission denied'):
            await read_value('DELETE FROM allocation_treatment_line WHERE organization_id=:org RETURNING id',ids,actor_key='clerk')


async def test_treatment_sql_membership_coverage_and_guarded_downgrade(migrator_session,scope_seed,store):
    import importlib
    from sqlalchemy import text,create_engine
    from sqlalchemy.exc import DBAPIError
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    from tests.test_access_coverage import capture
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api,client(ids,'actor') as admin:
        child,a=await allocation(api,ids);preview=(await api.get('/api/allocation-proposals/'+a['id']+'/treatment-preview')).json()
        before=await capture(admin)
        reply=await api.post('/api/logical-documents/'+child['id']+'/allocation-treatments',**command(a,preview));assert reply.status_code==201,reply.text
        first=reply.json();assert (await capture(admin))['manifest_digest']!=before['manifest_digest']
        another=await api.post('/api/logical-documents/'+child['id']+'/allocation-proposals',**allocation_command({'id':a['context_decision_id']},await lines(ids),a['id']))
        assert another.status_code==201,another.text
        alien=another.json()['lines'][0]['id']
    migration=importlib.import_module('migrations.versions.0108_allocation_treatments');engine=create_engine(MIGRATOR_SYNC_URL)
    params={'first':uuid.UUID(first['id']),'alien':uuid.UUID(alien)}
    clone='''INSERT INTO allocation_treatment_review SELECT (jsonb_populate_record(NULL::allocation_treatment_review,to_jsonb(a)||
      jsonb_build_object('id',CAST(:new AS uuid),'previous_revision_id',a.id,'revision_number',2,'request_key',gen_random_uuid()))).*
      FROM allocation_treatment_review a WHERE a.id=:first'''
    children='''INSERT INTO allocation_treatment_line SELECT (jsonb_populate_record(NULL::allocation_treatment_line,to_jsonb(l)||
      jsonb_build_object('id',gen_random_uuid(),'treatment_review_id',CAST(:new AS uuid))||CAST(:change AS jsonb))).*
      FROM allocation_treatment_line l WHERE treatment_review_id=:first LIMIT :take'''
    try:
        with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
            with pytest.raises(DBAPIError,match='Retain treatment'),db.begin_nested():migration.downgrade()
            for take,change in ((0,'{}'),(1,'{}'),(2,'{"allocation_line_id":"'+alien+'"}')):
                with pytest.raises(DBAPIError,match='Every exact|foreign key|unique constraint'),db.begin_nested():
                    current={**params,'new':uuid.uuid4()};db.execute(text(clone),current)
                    db.execute(text(children),{**current,'take':take,'change':change});db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
            with pytest.raises(DBAPIError,match='immutable|Immutable'),db.begin_nested():
                db.execute(text("UPDATE allocation_treatment_line SET documentary_basis='changed' WHERE treatment_review_id=:first"),params)
            with pytest.raises(DBAPIError,match='unique constraint'),db.begin_nested():
                db.execute(text('''INSERT INTO allocation_treatment_line SELECT (jsonb_populate_record(NULL::allocation_treatment_line,to_jsonb(l)||jsonb_build_object('id',gen_random_uuid()))).*
                  FROM allocation_treatment_line l WHERE treatment_review_id=:first LIMIT 1'''),params)
            point=db.begin_nested()
            try:
                db.execute(text('TRUNCATE allocation_treatment_review CASCADE'))
                oid=db.scalar(text("SELECT 'access_coverage_manifest(uuid,jsonb)'::regprocedure::oid"))
                db.execute(text('TRUNCATE allocation_integrity_attestation CASCADE'))
                db.execute(text('TRUNCATE exception_completion CASCADE'))
                db.execute(text('TRUNCATE financial_computation_attestation CASCADE'))
                importlib.import_module('migrations.versions.0115_financial_computation_evidence').downgrade()
                importlib.import_module('migrations.versions.0114_allocation_corrections').downgrade()
                importlib.import_module('migrations.versions.0113_allocation_correction_preview').downgrade()
                importlib.import_module('migrations.versions.0112_context_mappings').downgrade()
                importlib.import_module('migrations.versions.0111_context_candidate_source').downgrade()
                importlib.import_module('migrations.versions.0110_exception_completions').downgrade()
                importlib.import_module('migrations.versions.0109_allocation_adoptions').downgrade()
                migration.downgrade();migration.upgrade()
                assert db.scalar(text("SELECT 'access_coverage_manifest(uuid,jsonb)'::regprocedure::oid"))==oid
            finally:point.rollback()
        with pytest.raises(DBAPIError,match='Every exact'):
            with engine.begin() as db:db.execute(text(clone),{**params,'new':uuid.uuid4()})
    finally:engine.dispose()


async def test_treatment_miet_explicit_owner_not_applicable(migrator_session,scope_seed,store):
    from sqlalchemy import text
    from tests.test_scoped_account_statements import mutate
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids,'actor') as admin:
        result=await admin.post('/api/properties',json={'name':'Treatment rental fixture','management_type':'MIET'})
        assert result.status_code==201,result.text
        result=await admin.post('/api/accounting-scopes',json={'property_id':result.json()['id'],'scope_type':'MIET','label':'Treatment rental scope','valid_from':'2025-01-01','whole_property':True,'units':[]},headers={'Idempotency-Key':str(uuid.uuid4())})
        assert result.status_code==201,result.text
        ids={**ids,'scope':uuid.UUID(result.json()['id'])}
        await mutate(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES(:org,:scope,:clerk,ARRAY['read','prepare'],:actor,'Treatment rental fixture')"),ids)
    async with client(ids) as api:
        child,a=await allocation(api,ids);preview=(await api.get('/api/allocation-proposals/'+a['id']+'/treatment-preview')).json()
        assert preview['snapshot']['scope_type']=='MIET'
        url='/api/logical-documents/'+child['id']+'/allocation-treatments';body=command(a,preview)
        assert (await api.post(url,**body)).status_code==422
        for line in body['json']['lines']:line['owner_distribution_key']='not_applicable'
        result=await api.post(url,**body);assert result.status_code==201,result.text
        assert all(line['owner_distribution_key']=='not_applicable' for line in result.json()['lines'])


async def test_treatment_category_row_locks_in_both_orders(migrator_session,scope_seed,store):
    import json
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.exc import DBAPIError
    from tests.conftest import MIGRATOR_URL
    from app.core.db import get_tenant_session
    ids=await fixture(migrator_session,scope_seed);engine=create_async_engine(MIGRATOR_URL)
    try:
        async with client(ids) as api:
            child,a=await allocation(api,ids);preview=(await api.get('/api/allocation-proposals/'+a['id']+'/treatment-preview')).json()
            body=command(a,preview);url='/api/logical-documents/'+child['id']+'/allocation-treatments'
            params={**ids,'logical':uuid.UUID(child['id']),'allocation':uuid.UUID(a['id']),'request':uuid.uuid4(),'preview':preview['snapshot_digest'],'lines':json.dumps(body['json']['lines']),'category':uuid.UUID(a['lines'][0]['cost_category_id'])}
            async with engine.connect() as editor:
                tx=await editor.begin()
                await editor.execute(text('SELECT id FROM cost_category WHERE id=:category FOR UPDATE'),params)
                assert (await api.post(url,**body)).status_code==409
                await tx.rollback()
            async with get_tenant_session(ids['org']) as reviewer:
                await reviewer.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(ids['clerk'])})
                await reviewer.execute(text("SELECT create_allocation_treatment(:org,:logical,:allocation,NULL,:request,:preview,CAST(:lines AS jsonb),'Explicit treatment',true)"),params)
                async with engine.connect() as editor:
                    tx=await editor.begin();await editor.execute(text("SET LOCAL lock_timeout='150ms'"))
                    with pytest.raises(DBAPIError,match='lock timeout'):
                        await editor.execute(text('SELECT id FROM cost_category WHERE id=:category FOR UPDATE'),params)
                    await tx.rollback()
    finally:await engine.dispose()
