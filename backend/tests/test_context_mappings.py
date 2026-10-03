"""Explicit advisory mappings retain private proof and independent revocation."""
import uuid
import pytest
from sqlalchemy import text
from tests.test_financial_scope_boundary import worker_tenant,scope_seed  # noqa: F401
from tests.test_invoice_source_custody import store,fixture
from tests.test_scoped_account_statements import client,mutate
from tests.test_scope_payment_reconciliation import read_value
from tests.test_allocation_adoptions import confirm_grant,ready,command as adoption_command
from tests.test_context_candidates import document,endpoint
from tests.test_assignment_proposals import prepared

pytestmark=pytest.mark.asyncio


async def adopted(api,ids,monkeypatch,lines=None,**fields):
    monkeypatch.setattr('tests.test_intake_extraction_worker.pdf_bytes',lambda:document(lines or ['Objekt-ID: '+str(ids['property'])]))
    child,allocation,review,preview,url=await ready(api,ids,**fields)
    response=await api.post(url,**adoption_command(review,preview));assert response.status_code==201,response.text
    return child,response.json()


def activation(adoption,option,previous=None,key=None,**changes):
    return {'headers':{'Idempotency-Key':key or str(uuid.uuid4()),'If-Match':'"'+(previous or 'none')+'"'},'json':{
      'allocation_set_id':adoption['id'],'identifier_kind':option['kind'],'identifier_value':option['value'],
      'page_number':option['page_number'],'valid_from':'2026-01-01','valid_to':'2027-12-31',
      'reason':'Private source rationale must never be in reusable descriptors','reuse_consented':True,**changes}}


def revocation(version,key=None):
    return {'headers':{'Idempotency-Key':key or str(uuid.uuid4()),'If-Match':'"'+version['id']+'"'},'json':{'reason':'Stop reuse without exposing private origin'}}


async def test_mapping_consent_private_origin_second_user_reuse_and_revocation(migrator_session,scope_seed,store,monkeypatch):
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids);await confirm_grant(ids,'actor')
    async with client(ids) as api,client(ids,'actor') as other:
        child,adoption=await adopted(api,ids,monkeypatch)
        url='/api/allocation-sets/'+adoption['id']+'/mapping-options'
        response=await api.get(url);assert response.status_code==200,response.text
        option=response.json()['options'][0]
        assert (await other.get(url)).status_code==404
        assert (await api.post('/api/context-mappings',**activation(adoption,option,reuse_consented=False))).status_code==422
        request=activation(adoption,option)
        response=await api.post('/api/context-mappings',**request);assert response.status_code==201,response.text
        saved=response.json()
        assert saved['state']=='active' and saved['identifier_value']==str(ids['property'])
        assert 'reason' not in saved and 'allocation_set_id' not in saved
        assert (await api.post('/api/context-mappings',**request)).json()==saved
        # A different uploader receives only the explicitly consented descriptor.
        monkeypatch.setattr('tests.test_intake_extraction_worker.pdf_bytes',lambda:document(['Objekt-ID: '+str(ids['property']),'Independent later document']))
        _,new_child,p,e=await prepared(other,ids)
        candidate_url,params=endpoint(new_child,p,e)
        candidates=await other.get(candidate_url,params=params);assert candidates.status_code==200,candidates.text
        assert candidates.json()['items'][0]['mappings']==[saved]
        for secret in (adoption['id'],child['id'],request['json']['reason'],request['headers']['Idempotency-Key']):assert secret not in candidates.text
        assert (await other.get('/api/context-mappings/'+saved['id'])).json()['current']==saved
        # Removing the origin owner's authority must not strand the opt-in.
        await mutate(text('UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE organization_id=:org AND user_id=:clerk AND revoked_at IS NULL'),ids)
        revoked=await other.post('/api/context-mappings/'+saved['id']+'/revocations',**revocation(saved));assert revoked.status_code==201,revoked.text
        assert revoked.json()['state']=='revoked'
        assert (await other.get(candidate_url,params=params)).json()['items'][0]['mappings']==[]
        assert (await api.post('/api/context-mappings',**request)).status_code==404
    assert await read_value('SELECT count(*) FROM cost_entry WHERE organization_id=:org',ids)==2


async def test_mapping_historical_replays_never_change_head_and_changed_body_conflicts(migrator_session,scope_seed,store,monkeypatch):
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids)
    async with client(ids) as api:
        _,adoption=await adopted(api,ids,monkeypatch)
        option=(await api.get('/api/allocation-sets/'+adoption['id']+'/mapping-options')).json()['options'][0]
        request=activation(adoption,option);saved=(await api.post('/api/context-mappings',**request)).json()
        revoke=revocation(saved);revoke_url='/api/context-mappings/'+saved['id']+'/revocations'
        revoked=(await api.post(revoke_url,**revoke)).json()
        assert (await api.post('/api/context-mappings',**request)).json()==saved
        assert (await api.get('/api/context-mappings/'+saved['id'])).json()['current']==revoked
        activate=activation(adoption,option,previous=revoked['id'])
        reactivated=await api.post('/api/context-mappings',**activate);assert reactivated.status_code==201,reactivated.text
        assert (await api.post(revoke_url,**revoke)).json()==revoked
        assert (await api.get('/api/context-mappings/'+saved['id'])).json()['current']==reactivated.json()
        request['json']['reason']='Changed'
        assert (await api.post('/api/context-mappings',**request)).status_code==409
        assert (await api.post('/api/context-mappings',**activation(adoption,option))).status_code==409


async def test_mapping_meter_exact_case_duplicates_reassignment_and_period(migrator_session,scope_seed,store,monkeypatch):
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids)
    await mutate(text("INSERT INTO meter(organization_id,property_id,meter_number,meter_type) VALUES(:org,:property,'M-123','STROM')"),ids)
    async with client(ids) as api:
        child,a=await adopted(api,ids,monkeypatch,['Zähler-Nr.: M-123','Zählernummer: m-123','Objekt-ID: '+str(ids['other_property'])])
        options_url='/api/allocation-sets/'+a['id']+'/mapping-options'
        options=(await api.get(options_url)).json()['options'];assert len(options)==1
        option=options[0];assert option['value']=='M-123'
        for changes in ({'page_number':2},{'identifier_value':'m-123'},{'identifier_value':'invented'},{'valid_from':'2024-01-01'},
                        {'valid_from':'2028-01-01'},{'identifier_kind':'customer_id'}):
            r=await api.post('/api/context-mappings',**activation(a,option,**changes));assert r.status_code==422,r.text
        saved=await api.post('/api/context-mappings',**activation(a,option));assert saved.status_code==201,saved.text
        basis=await read_value('SELECT source_basis FROM mapping_version WHERE organization_id=:org',ids)
        candidate_url,params=endpoint(child,{'id':basis['partition_revision_id']},{'id':basis['extraction_revision_id']})
        assert (await api.get(candidate_url,params=params)).json()['items'][0]['mappings']==[saved.json()]
        # Full service interval, not the invoice date, determines reuse.
        short=await api.post('/api/context-mappings',**activation(a,option,previous=saved.json()['id'],valid_to='2026-09-15'));assert short.status_code==201,short.text
        assert (await api.get(candidate_url,params=params)).json()['items'][0]['mappings']==[]
        restored=await api.post('/api/context-mappings',**activation(a,option,previous=short.json()['id']));assert restored.status_code==201,restored.text
        await mutate(text("INSERT INTO meter(organization_id,property_id,meter_number,meter_type) VALUES(:org,:property,'M-123','WASSER')"),ids)
        assert (await api.get(options_url)).json()['options']==[]
        assert (await api.get(candidate_url,params=params)).json()['items'][0]['mappings']==[]
        assert (await api.post('/api/context-mappings',**activation(a,option,previous=restored.json()['id']))).status_code==422
        await mutate(text("DELETE FROM meter WHERE organization_id=:org AND meter_type='WASSER'"),ids)
        await mutate(text("UPDATE meter SET property_id=:other_property WHERE organization_id=:org AND meter_number='M-123'"),ids)
        assert (await api.get('/api/context-mappings/'+saved.json()['id'])).json()['master_current'] is False
        # Revocation remains possible after master drift.
        assert (await api.post('/api/context-mappings/'+restored.json()['id']+'/revocations',**revocation(restored.json()))).status_code==201


async def test_mapping_current_source_prepare_export_and_no_confirm_requirement(migrator_session,scope_seed,store,monkeypatch):
    from tests.test_source_partitions import command as partition_command
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids)
    async with client(ids) as api:
        child,a=await adopted(api,ids,monkeypatch,source_scope=ids['scope'])
        options_url='/api/allocation-sets/'+a['id']+'/mapping-options'
        option=(await api.get(options_url)).json()['options'][0]
        await mutate(text('UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE organization_id=:org AND user_id=:clerk AND revoked_at IS NULL'),ids)
        await mutate(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES(:org,:scope,:clerk,ARRAY['read','prepare','export'],:actor,'Mapping does not confirm money')"),ids)
        request=activation(a,option);saved=await api.post('/api/context-mappings',**request);assert saved.status_code==201,saved.text
        basis=await read_value('SELECT source_basis FROM mapping_version WHERE organization_id=:org',ids)
        replaced=await api.post('/api/document-refs/'+basis['document_ref_id']+'/partition',**partition_command([child],previous=basis['partition_revision_id']));assert replaced.status_code==201,replaced.text
        assert (await api.get(options_url)).json()['can_activate'] is False
        assert (await api.post('/api/context-mappings',**activation(a,option,previous=saved.json()['id']))).status_code==409
        assert (await api.post('/api/context-mappings',**request)).json()==saved.json()
        await mutate(text('UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE organization_id=:org AND user_id=:clerk AND revoked_at IS NULL'),ids)
        await mutate(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES(:org,:scope,:clerk,ARRAY['read','prepare'],:actor,'Source export withdrawn')"),ids)
        assert (await api.get(options_url)).status_code==404
        assert (await api.post('/api/context-mappings',**request)).status_code==404
        assert (await api.post('/api/context-mappings/'+saved.json()['id']+'/revocations',**revocation(saved.json()))).status_code==201


async def test_mapping_true_head_retained_targets_and_concurrent_predecessor(migrator_session,scope_seed,store,monkeypatch):
    import asyncio
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids);await confirm_grant({**ids,'scope':ids['other_scope']})
    async with client(ids) as api:
        _,a=await adopted(api,ids,monkeypatch)
        option=(await api.get('/api/allocation-sets/'+a['id']+'/mapping-options')).json()['options'][0]
        replies=await asyncio.gather(*[api.post('/api/context-mappings',**activation(a,option)) for _ in range(2)])
        assert sorted(r.status_code for r in replies)==[201,409],[r.text for r in replies]
        first=next(r.json() for r in replies if r.status_code==201)
        from tests.test_allocation_treatments import command as treatment_command
        def miet_treatment(*args,**kwargs):
            body=treatment_command(*args,**kwargs)
            for row in body['json']['lines']:row['owner_distribution_key']='not_applicable'
            return body
        monkeypatch.setattr('tests.test_allocation_adoptions.treatment_command',miet_treatment)
        _,b=await adopted(api,{**ids,'scope':ids['other_scope']},monkeypatch,['Objekt-ID: '+str(ids['property']),'Another sphere'],document_number='R-OTHER')
        other_option=(await api.get('/api/allocation-sets/'+b['id']+'/mapping-options')).json()['options'][0]
        second=await api.post('/api/context-mappings',**activation(b,other_option,previous=first['id']));assert second.status_code==201,second.text
        assert second.json()['scope_id']==str(ids['other_scope'])
        await mutate(text('UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE organization_id=:org AND user_id=:clerk AND scope_id=:other_scope AND revoked_at IS NULL'),ids)
        assert (await api.get('/api/context-mappings/'+first['id'])).status_code==404
        assert (await api.get('/api/allocation-sets/'+a['id']+'/mapping-options')).json()['options']==[]
        assert (await api.post('/api/context-mappings',**activation(a,option,previous=first['id']))).status_code==404
        assert (await api.post('/api/context-mappings/'+first['id']+'/revocations',**revocation(first))).status_code==404
        await confirm_grant({**ids,'scope':ids['other_scope']})
        # A selected current target does not require every retained target to equal it.
        from app.core.db import get_session_factory,set_tenant_context
        async with get_session_factory()() as db,db.begin():
            await set_tenant_context(db,ids['org'])
            await db.execute(text("SELECT set_config('app.current_user_id',:actor,true),set_config('app.current_scope_id',:sphere,true)"),{'actor':str(ids['clerk']),'sphere':str(ids['other_scope'])})
            status=await db.scalar(text('SELECT context_mapping_status(:org,:target)'),{'org':ids['org'],'target':uuid.UUID(first['id'])})
            assert status['current']==second.json()


async def test_mapping_roles_pause_private_table_seal_and_guarded_downgrade(migrator_session,scope_seed,store,monkeypatch):
    import importlib
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from app.core.db import get_worker_session_factory,get_review_session_factory,set_tenant_context
    from tests.conftest import MIGRATOR_SYNC_URL
    from tests.test_property_source_enforcement import synthetic_enforcement,change_mode
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids)
    migration=importlib.import_module('migrations.versions.0112_context_mappings');engine=create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
            before=db.scalar(text("SELECT pg_get_functiondef('access_coverage_manifest(uuid,jsonb)'::regprocedure)"))
            point=db.begin_nested()
            try:
                db.execute(text('TRUNCATE mapping_version'))
                db.execute(text('TRUNCATE allocation_integrity_attestation CASCADE'))
                corrections=importlib.import_module('migrations.versions.0114_allocation_corrections')
                computation=importlib.import_module('migrations.versions.0115_financial_computation_evidence')
                db.execute(text('TRUNCATE financial_computation_attestation CASCADE'))
                followup=importlib.import_module('migrations.versions.0116_correction_followup_completions')
                db.execute(text('TRUNCATE allocation_correction_context,allocation_correction_completion'))
                db.execute(text('TRUNCATE visual_disclosure_attestation CASCADE'))
                db.execute(text('TRUNCATE allocation_source_claim CASCADE'))
                db.execute(text('TRUNCATE visual_extract_attestation CASCADE'))
                importlib.import_module('migrations.versions.0120_allocation_source_aliases').downgrade()
                importlib.import_module('migrations.versions.0119_allocation_source_claims').downgrade()
                importlib.import_module('migrations.versions.0118_visual_disclosures').downgrade()
                importlib.import_module('migrations.versions.0117_visual_extract_artifacts').downgrade()
                followup.downgrade()
                computation.downgrade()
                corrections.downgrade()
                migration.downgrade();migration.upgrade()
                corrections.upgrade()
                computation.upgrade()
                followup.upgrade()
                importlib.import_module('migrations.versions.0117_visual_extract_artifacts').upgrade()
                importlib.import_module('migrations.versions.0118_visual_disclosures').upgrade()
                importlib.import_module('migrations.versions.0119_allocation_source_claims').upgrade()
                importlib.import_module('migrations.versions.0120_allocation_source_aliases').upgrade()
                assert before==db.scalar(text("SELECT pg_get_functiondef('access_coverage_manifest(uuid,jsonb)'::regprocedure)"))
            finally:point.rollback()
        async with client(ids) as api:
            _,a=await adopted(api,ids,monkeypatch)
            option=(await api.get('/api/allocation-sets/'+a['id']+'/mapping-options')).json()['options'][0]
            body=activation(a,option);saved=(await api.post('/api/context-mappings',**body)).json()
            for factory in (get_worker_session_factory(),get_review_session_factory()):
                with pytest.raises(DBAPIError,match='permission denied|Interactive runtime required'):
                    async with factory() as db,db.begin():
                        await set_tenant_context(db,ids['org'])
                        await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(ids['clerk'])})
                        await db.execute(text('SELECT context_mapping_status(:org,:target)'),{'org':ids['org'],'target':uuid.UUID(saved['id'])})
            with pytest.raises(DBAPIError,match='permission denied'):
                await read_value('SELECT source_basis FROM mapping_version WHERE organization_id=:org',ids,actor_key='clerk')
            with pytest.raises(DBAPIError,match='immutable'):
                await mutate(text("UPDATE mapping_version SET reason='rewrite' WHERE organization_id=:org"),ids)
            async with synthetic_enforcement(scope_seed):
                await change_mode(scope_seed,'PAUSED')
                assert (await api.post('/api/context-mappings',**body)).status_code in (403,404)
                assert (await api.post('/api/context-mappings/'+saved['id']+'/revocations',**revocation(saved))).status_code in (403,404)
            with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
                with pytest.raises(DBAPIError,match='Retain opted-in'),db.begin_nested():migration.downgrade()
                assert "'mapping_version'" in db.scalar(text("SELECT pg_get_functiondef('access_coverage_manifest(uuid,jsonb)'::regprocedure)"))
    finally:engine.dispose()


async def test_mapping_activation_replay_respects_selected_target_before_receipt(migrator_session,scope_seed,store,monkeypatch):
    from app.core.db import get_session_factory,set_tenant_context
    from sqlalchemy.exc import DBAPIError
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids);await confirm_grant({**ids,'scope':ids['other_scope']})
    async with client(ids) as api:
        _,a=await adopted(api,ids,monkeypatch,source_scope=ids['other_scope'])
        option=(await api.get('/api/allocation-sets/'+a['id']+'/mapping-options')).json()['options'][0]
        request=activation(a,option);response=await api.post('/api/context-mappings',**request);assert response.status_code==201,response.text
        body=request['json']
        with pytest.raises(DBAPIError,match='Mapping origin inaccessible'):
            async with get_session_factory()() as db,db.begin():
                await set_tenant_context(db,ids['org'])
                await db.execute(text("SELECT set_config('app.current_user_id',:actor,true),set_config('app.current_scope_id',:sphere,true)"),{'actor':str(ids['clerk']),'sphere':str(ids['other_scope'])})
                await db.execute(text('SELECT activate_context_mapping(:org,:target,NULL,:request,:kind,:value,1,DATE \'2026-01-01\',DATE \'2027-12-31\',:reason,true)'),
                  {'org':ids['org'],'target':uuid.UUID(a['id']),'request':uuid.UUID(request['headers']['Idempotency-Key']),'kind':body['identifier_kind'],'value':body['identifier_value'],'reason':body['reason']})


async def test_mapping_sql_python_supported_signal_vectors(migrator_session,scope_seed):
    import json
    from app.core.context_candidates import source_signals
    values=['Objekt-ID: 12345678-abcd-4567-abcd-1234567890ab','objekt id: {12345678-ABCD-4567-ABCD-1234567890AB}',
      'ObjektID: 12345678abcd4567abcd1234567890ab','Objekt-ID: urn:uuid:12345678-abcd-4567-abcd-1234567890ab',
      'Objekt-ID: invalid','  Zähler-Nr.: M-123  ','Zählernummer: m-123','ZÄHLER NR: M 123','Supplier M-123','Zählernummer: '+'X'*101]
    for value in values:
        pages=[{'number':1,'text':value,'method':'text_layer'}]
        actual=await migrator_session.scalar(text('SELECT mapping_source_signals(CAST(:pages AS jsonb))'),{'pages':json.dumps(pages)})
        assert actual==source_signals(pages),value


async def test_mapping_retained_retry_survives_inaccessible_new_context(migrator_session,scope_seed,store,monkeypatch):
    from tests.test_assignment_proposals import command as proposal_command
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids);await confirm_grant({**ids,'scope':ids['other_scope']})
    async with client(ids) as api:
        child,a=await adopted(api,ids,monkeypatch)
        url='/api/allocation-sets/'+a['id']+'/mapping-options'
        option=(await api.get(url)).json()['options'][0];request=activation(a,option)
        saved=await api.post('/api/context-mappings',**request);assert saved.status_code==201,saved.text
        basis=await read_value('SELECT source_basis FROM mapping_version WHERE organization_id=:org',ids)
        proposals=(await api.get('/api/logical-documents/'+child['id']+'/assignment-proposals')).json()['items']
        newer=await api.post('/api/logical-documents/'+child['id']+'/assignment-proposals',**proposal_command(
          {'id':basis['partition_revision_id']},{'id':basis['extraction_revision_id']},ids['other_scope'],previous=proposals[0]['id']))
        assert newer.status_code==201,newer.text
        await mutate(text('UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:other_scope AND user_id=:clerk AND revoked_at IS NULL'),ids)
        options=await api.get(url,params={'identifier_kind':option['kind'],'identifier_value':option['value']});assert options.status_code==200,options.text
        assert options.json()['can_activate'] is False and options.json()['options']==[]
        assert (await api.post('/api/context-mappings',**request)).json()==saved.json()


@pytest.mark.parametrize('holder',['mapping','master'])
@pytest.mark.parametrize('change',['insert','update'])
async def test_mapping_serializes_meter_changes_in_both_lock_orders(migrator_session,scope_seed,store,monkeypatch,holder,change):
    import asyncio
    from app.core.db import get_session_factory,set_tenant_context
    from sqlalchemy.ext.asyncio import create_async_engine
    from tests.conftest import MIGRATOR_URL
    async def wait_for_writer(pid):
        for _ in range(200):
            if await read_value("SELECT count(*) FROM pg_stat_activity WHERE wait_event_type='Lock' AND :holder=ANY(pg_blocking_pids(pid))",{**ids,'holder':pid}):return
            await asyncio.sleep(.01)
        pytest.fail('Concurrent writer did not wait on mapping transaction')
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids)
    await mutate(text("INSERT INTO meter(organization_id,property_id,meter_number,meter_type) VALUES(:org,:property,'M-123','STROM')"),ids)
    statement=text("INSERT INTO meter(organization_id,property_id,meter_number,meter_type) VALUES(:org,:property,'M-123','WASSER')" if change=='insert' else "UPDATE meter SET meter_number='renumbered' WHERE organization_id=:org AND meter_number='M-123'")
    async with client(ids) as api:
        _,a=await adopted(api,ids,monkeypatch,['Zählernummer: M-123'])
        option=(await api.get('/api/allocation-sets/'+a['id']+'/mapping-options')).json()['options'][0]
        request=activation(a,option);pending=None
        if holder=='master':
            engine=create_async_engine(MIGRATOR_URL)
            try:
                async with engine.connect() as held:
                    await held.execute(statement,ids)
                    pid=await held.scalar(text('SELECT pg_backend_pid()'))
                    pending=asyncio.create_task(api.post('/api/context-mappings',**request))
                    await wait_for_writer(pid)
                    await held.commit()
                    response=await asyncio.wait_for(pending,10);assert response.status_code==422,response.text
            finally:
                if pending and not pending.done():pending.cancel()
                await engine.dispose()
        else:
            async with get_session_factory()() as db:
                try:
                    await set_tenant_context(db,ids['org'])
                    await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(ids['clerk'])})
                    saved=await db.scalar(text("SELECT activate_context_mapping(:org,:target,NULL,:request,'meter_number','M-123',1,DATE '2026-01-01',DATE '2027-12-31',:reason,true)"),
                      {'org':ids['org'],'target':uuid.UUID(a['id']),'request':uuid.UUID(request['headers']['Idempotency-Key']),'reason':request['json']['reason']})
                    pid=await db.scalar(text('SELECT pg_backend_pid()'))
                    pending=asyncio.create_task(mutate(statement,ids))
                    await wait_for_writer(pid)
                    await db.commit();await asyncio.wait_for(pending,10)
                    status=await api.get('/api/context-mappings/'+saved['id']);assert status.status_code==200,status.text
                    assert status.json()['master_current'] is False
                finally:
                    await db.rollback()
                    if pending and not pending.done():pending.cancel()
