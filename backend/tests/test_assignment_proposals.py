"""Manual context proposals pin evidence without adopting fields or money."""
import asyncio
import uuid
import pytest
from sqlalchemy import text
from tests.test_financial_scope_boundary import worker_tenant,scope_seed  # noqa: F401
from tests.test_invoice_source_custody import store,fixture
from tests.test_scoped_account_statements import client,mutate
from tests.test_scope_payment_reconciliation import read_value
from tests.test_intake_extraction_worker import source
from tests.test_source_partitions import command as partition_command

pytestmark=pytest.mark.asyncio


async def prepared(api,ids,scoped=False):
    from app.core.db import get_worker_session_factory
    from app.core.intake_extraction import extract_logical
    from app.workers.intake_extraction import process_once
    ref,child=await source(api,ids,scoped=scoped)
    while await process_once(get_worker_session_factory(),ids['org'],processor=extract_logical):pass
    partition=await api.post('/api/document-refs/'+ref['id']+'/partition',**partition_command([child]))
    assert partition.status_code==201,partition.text
    history=(await api.get('/api/document-refs/'+ref['id']+'/extractions',params={'logical_document_id':child['id']})).json()['items']
    return ref,child,partition.json(),history[0]


def command(partition,extraction,scope,previous=None,key=None,reason='Manually selected after source review'):
    return dict(json={'partition_revision_id':partition['id'],'extraction_revision_id':extraction['id'],'scope_id':str(scope),'reason':reason},
        headers={'Idempotency-Key':key or str(uuid.uuid4()),'If-Match':'"'+(previous or 'none')+'"'})


async def test_manual_proposal_pins_source_and_replays_historical_revision(migrator_session,scope_seed,store):
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        ref,child,partition,extraction=await prepared(api,ids)
        url='/api/logical-documents/'+child['id']+'/assignment-proposals';body=command(partition,extraction,ids['scope'])
        reply=await api.post(url,**body);assert reply.status_code==201,reply.text
        proposal=reply.json();assert proposal['method']=='manual_selection' and proposal['state']=='proposed' and proposal['blockers']==[]
        assert proposal['partition_revision_id']==partition['id'] and proposal['extraction_revision_id']==extraction['id']
        assert proposal['logical_document_id']==child['id'] and proposal['revision_number']==1
        assert (await api.post(url,**body)).json()==proposal
        changed=command(partition,extraction,ids['scope'],key=body['headers']['Idempotency-Key'],reason='Changed request')
        assert (await api.post(url,**changed)).status_code==409
        second=await api.post(url,**command(partition,extraction,ids['scope'],previous=proposal['id'],reason='More precise manual reason'))
        assert second.status_code==201,second.text
        assert (await api.post(url,**body)).json()==proposal
        items=(await api.get(url)).json()['items'];assert [r['revision_number'] for r in items]==[2,1]
        page=(await api.get(url,params={'limit':1})).json();assert len(page['items'])==1 and page['next_before_revision']==2
        assert (await api.get(url,params={'before_revision':2})).json()['items']==[proposal]
        replaced=await api.post('/api/document-refs/'+ref['id']+'/partition',**partition_command([child],previous=partition['id']))
        assert replaced.status_code==201,replaced.text
        assert (await api.post(url,**command(partition,extraction,ids['scope'],previous=second.json()['id']))).status_code==409
        assert (await api.post(url,**body)).json()==proposal
    assert await read_value('SELECT count(*) FROM cost_entry WHERE organization_id=:org',ids)==0
    assert await read_value('SELECT count(*) FROM accounting_scope_binding WHERE organization_id=:org',ids)==0
    with pytest.raises(Exception,match='immutable|Immutable'):
        await mutate(text("UPDATE assignment_proposal SET reason='Changed' WHERE organization_id=:org"),ids)


async def test_proposal_requires_source_destination_and_exact_evidence(migrator_session,scope_seed,store,client_b_admin):
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api,client(ids,'actor') as admin:
        _,child,partition,extraction=await prepared(api,ids)
        _,other,other_partition,other_extraction=await prepared(api,ids)
        url='/api/logical-documents/'+child['id']+'/assignment-proposals'
        for p,e in [(other_partition,extraction),(partition,other_extraction)]:
            reply=await api.post(url,**command(p,e,ids['scope']));assert reply.status_code==422,reply.text
        assert (await api.post(url,**command(partition,extraction,ids['other_scope']))).status_code==404
        body=command(partition,extraction,ids['scope']);created=await api.post(url,**body);assert created.status_code==201,created.text
        for denied in (admin,client_b_admin):
            assert (await denied.get(url)).status_code==404
            assert (await denied.post(url,**body)).status_code==404
        await mutate(text('UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE id=:grant'),ids)
        assert (await api.get(url)).json()['items']==[]
        assert (await api.post(url,**body)).status_code==404
        assert await read_value('SELECT count(*) FROM assignment_proposal WHERE organization_id=:org',ids,actor_key='clerk')==0


async def test_conflicting_hint_is_retained_blocked_and_concurrent_edits_do_not_overwrite(migrator_session,scope_seed,store):
    ids=await fixture(migrator_session,scope_seed)
    await mutate(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES(:org,:other_scope,:clerk,ARRAY['read','prepare'],:actor,'Second authorized scope')"),ids)
    async with client(ids) as api:
        _,child,partition,extraction=await prepared(api,ids,scoped=True)
        url='/api/logical-documents/'+child['id']+'/assignment-proposals'
        body=command(partition,extraction,ids['other_scope'],reason='A reason does not resolve conflicting context')
        replies=await asyncio.gather(api.post(url,**body),api.post(url,**body));assert all(r.status_code==201 for r in replies),[r.text for r in replies]
        proposal=replies[0].json();assert replies[1].json()==proposal
        assert proposal['state']=='blocked' and proposal['blockers']==['scope_hint_conflict']
        assert proposal['scope_hint_id']==str(ids['scope'])
        replies=await asyncio.gather(*[api.post(url,**command(partition,extraction,ids['scope'],previous=proposal['id'])) for _ in range(2)])
        assert sorted(r.status_code for r in replies)==[201,409]
    assert await read_value('SELECT count(*) FROM assignment_proposal WHERE organization_id=:org',ids)==2


async def test_assignment_role_scope_pause_and_atomicity(migrator_session,scope_seed,store):
    from app.core.db import get_tenant_session,get_worker_session_factory,get_review_session_factory,set_tenant_context
    from sqlalchemy.exc import DBAPIError
    from tests.test_property_source_enforcement import synthetic_enforcement,change_mode
    ids=await fixture(migrator_session,scope_seed)
    async with synthetic_enforcement(scope_seed),client(ids) as api:
        _,child,partition,extraction=await prepared(api,ids)
        params={**ids,'logical':uuid.UUID(child['id']),'partition':uuid.UUID(partition['id']),'extraction':uuid.UUID(extraction['id']),'request':uuid.uuid4()}
        sql="SELECT create_assignment_proposal(:org,:logical,:partition,:extraction,:scope,NULL,:request,'Explicit manual choice')"
        for factory in (get_worker_session_factory(),get_review_session_factory()):
            with pytest.raises(DBAPIError,match='Interactive runtime required|permission denied'):
                async with factory() as db,db.begin():
                    await set_tenant_context(db,ids['org'])
                    await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(ids['clerk'])})
                    await db.execute(text(sql),params)
        with pytest.raises(DBAPIError,match='inaccessible'):
            async with get_tenant_session(ids['org']) as db:
                await db.execute(text("SELECT set_config('app.current_user_id',:actor,true),set_config('app.current_scope_id',:scope,true)"),{'actor':str(ids['clerk']),'scope':str(ids['other_scope'])})
                await db.execute(text(sql),params)
        with pytest.raises(RuntimeError,match='simulated crash'):
            async with get_tenant_session(ids['org']) as db:
                await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(ids['clerk'])})
                assert await db.scalar(text(sql),params)
                raise RuntimeError('simulated crash')
        assert await read_value('SELECT count(*) FROM assignment_proposal WHERE organization_id=:org',ids)==0
        body=command(partition,extraction,ids['scope']);url='/api/logical-documents/'+child['id']+'/assignment-proposals'
        reply=await api.post(url,**body);assert reply.status_code==201,reply.text
        with pytest.raises(DBAPIError,match='permission denied'):
            await read_value('DELETE FROM assignment_proposal WHERE organization_id=:org RETURNING id',ids,actor_key='clerk')
        await change_mode(scope_seed,'PAUSED')
        assert len((await api.get(url)).json()['items'])==1
        assert (await api.post(url,**body)).status_code==404


async def test_assignment_evidence_changes_coverage_and_cannot_be_downgraded(migrator_session,scope_seed,store):
    import importlib
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    from tests.test_access_coverage import capture
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api,client(ids,'actor') as admin:
        _,child,partition,extraction=await prepared(api,ids)
        before=await capture(admin)
        reply=await api.post('/api/logical-documents/'+child['id']+'/assignment-proposals',**command(partition,extraction,ids['scope']));assert reply.status_code==201,reply.text
        assert (await capture(admin))['manifest_digest']!=before['manifest_digest']
        reply=await admin.post(f"/api/access-preparation/snapshots/{before['id']}/review",json={'manifest_digest':before['manifest_digest'],'acknowledged':True},headers={'Idempotency-Key':str(uuid.uuid4())})
        assert reply.status_code==409,reply.text
    migration=importlib.import_module('migrations.versions.0101_assignment_proposals');engine=create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
            with pytest.raises(DBAPIError,match='Retain assignment'),db.begin_nested():migration.downgrade()
            point=db.begin_nested()
            try:
                db.execute(text('TRUNCATE exception_case CASCADE'))
                db.execute(text('TRUNCATE exception_case CASCADE'))
                db.execute(text('TRUNCATE allocation_treatment_review CASCADE'))
                db.execute(text('TRUNCATE allocation_integrity_attestation CASCADE'))
                db.execute(text('TRUNCATE exception_completion CASCADE'))
                db.execute(text('TRUNCATE financial_computation_attestation CASCADE'))
                db.execute(text('TRUNCATE allocation_correction_context,allocation_correction_completion'))
                db.execute(text('TRUNCATE visual_disclosure_attestation CASCADE'))
                importlib.import_module('migrations.versions.0118_visual_disclosures').downgrade()
                importlib.import_module('migrations.versions.0117_visual_extract_artifacts').downgrade()
                importlib.import_module('migrations.versions.0116_correction_followup_completions').downgrade()
                importlib.import_module('migrations.versions.0115_financial_computation_evidence').downgrade()
                importlib.import_module('migrations.versions.0114_allocation_corrections').downgrade()
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
                importlib.import_module('migrations.versions.0105_allocation_proposals').downgrade()
                db.execute(text('TRUNCATE context_decision CASCADE'))
                importlib.import_module('migrations.versions.0104_context_decisions').downgrade()
                db.execute(text('TRUNCATE invoice_review_revision'))
                importlib.import_module('migrations.versions.0103_invoice_field_reviews').downgrade()
                importlib.import_module('migrations.versions.0102_exception_cases').downgrade()
                db.execute(text('TRUNCATE assignment_proposal'))
                oid=db.scalar(text("SELECT 'access_coverage_manifest(uuid,jsonb)'::regprocedure::oid"))
                migration.downgrade();migration.upgrade()
                assert db.scalar(text("SELECT 'access_coverage_manifest(uuid,jsonb)'::regprocedure::oid"))==oid
            finally:point.rollback()
        assert await read_value('SELECT count(*) FROM assignment_proposal WHERE organization_id=:org',ids)==1
    finally:engine.dispose()


async def test_sev_selection_remains_blocked_without_financial_adoption(migrator_session,scope_seed,store):
    ids=await fixture(migrator_session,scope_seed);ids.update({key:uuid.uuid4() for key in ('sev','unit','person','owner')})
    await mutate(text("INSERT INTO accounting_scope(id,organization_id,property_id,scope_type,label,valid_from,whole_property,origin,created_by) VALUES(:sev,:org,:property,'SEV','Unqualified SEV candidate','2025-01-01',false,'REVIEWED',:actor)"),ids)
    for statement in (
        "INSERT INTO unit(id,organization_id,property_id,number,location,usage_type) VALUES(:unit,:org,:property,'1','','WOHNUNG')",
        "INSERT INTO person(id,organization_id,first_name,last_name,street,zip_code,city) VALUES(:person,:org,'Synthetic','Owner','','','')",
        "INSERT INTO owner_profile(id,organization_id,person_id,notes) VALUES(:owner,:org,:person,'')",
        "INSERT INTO ownership_history(organization_id,unit_id,owner_profile_id,valid_from) VALUES(:org,:unit,:owner,'2025-01-01')",
        "INSERT INTO accounting_scope_unit(organization_id,scope_id,property_id,unit_id,owner_profile_id) VALUES(:org,:sev,:property,:unit,:owner)",
    ):await mutate(text(statement),ids)
    await mutate(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES(:org,:sev,:clerk,ARRAY['read','prepare'],:actor,'Synthetic candidate scope')"),ids)
    async with client(ids) as api:
        _,child,partition,extraction=await prepared(api,ids)
        reply=await api.post('/api/logical-documents/'+child['id']+'/assignment-proposals',**command(partition,extraction,ids['sev']))
        assert reply.status_code==201,reply.text
        assert reply.json()['state']=='blocked' and reply.json()['blockers']==['unsupported_scope_type']
    assert await read_value('SELECT count(*) FROM cost_entry WHERE organization_id=:org',ids)==0
