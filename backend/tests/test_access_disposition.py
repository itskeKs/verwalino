"""Retention decisions follow enforced denial, not combined coverage readiness."""
import uuid
import pytest
from sqlalchemy import text
from tests.test_financial_scope_boundary import worker_tenant,scope_seed  # noqa: F401
from tests.test_access_coverage import fixture,capture
from tests.test_scoped_account_statements import client,statement_fixture
from tests.test_runtime_budget_reservation import review_budget
from tests.test_property_source_enforcement import synthetic_enforcement
from tests.test_scope_payment_reconciliation import read_value

pytestmark = pytest.mark.asyncio


async def reviewed(api):
    await review_budget(api)
    report = await capture(api)
    response = await api.post(f"/api/access-preparation/snapshots/{report['id']}/review",json={
        'manifest_digest':report['manifest_digest'],'acknowledged':True},headers={'Idempotency-Key':str(uuid.uuid4())})
    assert response.status_code==201,response.text
    return report


async def assessment(api,report):
    response = await api.get(f"/api/access-preparation/snapshots/{report['id']}/disposition")
    assert response.status_code==200,response.text
    return response.json()


async def decisions(api,report):
    response = await api.get(f"/api/access-preparation/snapshots/{report['id']}/disposition/items",params={'limit':200})
    assert response.status_code==200,response.text
    assert response.json()['next_cursor'] is None
    return response.json()['items']


async def test_readable_bank_with_empty_combined_audience_is_not_retained(migrator_session,scope_seed):
    ids = await statement_fixture(migrator_session,scope_seed,'lease')
    async with client(ids,'actor') as admin:
        report = await reviewed(admin)
        state = await assessment(admin,report)
        assert state['current'] and state['blocker_count']>0 and not state['acceptance_available']
        bank = next(row for row in await decisions(admin,report) if row['resource_id']==str(ids['bank']))
        assert bank['disposition']=='BLOCKED'
        async with synthetic_enforcement(scope_seed):
            assert await read_value('SELECT count(*) FROM bank_account WHERE id=:bank',ids,actor_key='clerk')==1


async def test_unbound_financial_root_can_be_explicitly_retained_without_permission(migrator_session,scope_seed):
    ids = await fixture(migrator_session,scope_seed)
    async with client(ids,'actor') as admin:
        report = await reviewed(admin)
        await assessment(admin,report)
        bank = next(row for row in await decisions(admin,report) if row['resource_id']==str(ids['bank']))
        assert bank['disposition']=='RETAIN_BLOCKED'
        async with synthetic_enforcement(scope_seed):
            for who in ('actor','clerk'):
                assert await read_value('SELECT count(*) FROM bank_account WHERE id=:bank',ids,actor_key=who)==0
        assert not await read_value('SELECT property_source_activation_ready(:org)',ids)


async def retained_fixture(db,seed,*,queued=False,commit=True):
    ids = {'org':seed['org_a_id'],'actor':seed['user_a_admin_id'],'clerk':seed['user_a_clerk_id'],'document':uuid.uuid4()}
    await db.execute(text("""INSERT INTO invoice_document(id,organization_id,storage_uri,storage_backend,mime_type,content_byte_size,
        content_sha256,version,chain_root_id,status,processing_status) VALUES(:document,:org,'synthetic/unknown','LOCAL_FS','application/pdf',1,repeat('a',64),1,:document,'active',:processing)"""),
        {**ids,'processing':'queued' if queued else 'pending'})
    if commit: await db.commit()
    return ids


def acceptance_body(report,state):
    return {'manifest_digest':report['manifest_digest'],'assessment_digest':state['assessment_digest'],
        'reason':'Retain the inaccessible original and review explicit team assignments',
        'team_acknowledged':True,'retention_acknowledged':True}


async def test_retention_acceptance_replays_without_grant_or_source_mutation(migrator_session,scope_seed):
    from tests.test_scoped_account_statements import mutate
    ids = await retained_fixture(migrator_session,scope_seed)
    before = await read_value('SELECT to_jsonb(d) FROM invoice_document d WHERE id=:document',ids)
    async with client(ids,'actor') as admin:
        report = await reviewed(admin)
        state = await assessment(admin,report)
        assert state['acceptance_available'] and state['retained_count']==1 and state['team_count']==2
        path = f"/api/access-preparation/snapshots/{report['id']}/disposition/accept"
        body = acceptance_body(report,state)
        headers = {'Idempotency-Key':str(uuid.uuid4())}
        assert (await admin.post(path,json={**body,'retention_acknowledged':False},headers=headers)).status_code==422
        response = await admin.post(path,json=body,headers=headers)
        assert response.status_code==201,response.text
        assert (await admin.post(path,json=body,headers=headers)).json()==response.json()
        assert (await admin.post(path,json={**body,'reason':'Different decision'},headers=headers)).status_code==409
        assert (await assessment(admin,report))['acceptance']['id']==response.json()['id']
        await mutate(text("UPDATE membership SET role='READ_ONLY' WHERE organization_id=:org AND user_id=:clerk"),ids)
        assert not (await assessment(admin,report))['current']
        assert (await admin.post(path,json=body,headers=headers)).json()==response.json()
    assert await read_value('SELECT to_jsonb(d) FROM invoice_document d WHERE id=:document',ids)==before
    assert await read_value('SELECT count(*) FROM scope_grant WHERE organization_id=:org',ids)==0
    assert not await read_value('SELECT property_source_activation_ready(:org)',ids)
    async with synthetic_enforcement(scope_seed):
        for who in ('actor','clerk'):
            assert await read_value('SELECT count(*) FROM invoice_document WHERE id=:document',ids,actor_key=who)==0


async def test_active_work_cannot_be_accepted_as_retained(migrator_session,scope_seed):
    ids = await retained_fixture(migrator_session,scope_seed,queued=True)
    async with client(ids,'actor') as admin:
        report = await reviewed(admin)
        state = await assessment(admin,report)
        assert state['active_work_count']==1 and state['blocker_count']>0 and not state['acceptance_available']
        response = await admin.post(f"/api/access-preparation/snapshots/{report['id']}/disposition/accept",json=acceptance_body(report,state),
            headers={'Idempotency-Key':str(uuid.uuid4())})
        assert response.status_code==409,response.text


async def test_disposition_pages_are_private_bounded_and_cursors_not_interchangeable(migrator_session,scope_seed,client_b_admin):
    ids = await retained_fixture(migrator_session,scope_seed)
    async with client(ids,'actor') as admin:
        report = await reviewed(admin)
        path = f"/api/access-preparation/snapshots/{report['id']}/disposition"
        first = (await admin.get(path+'/items',params={'limit':1})).json()
        assert len(first['items'])==1 and first['next_cursor']
        assert (await admin.get(path+'/items',params={'cursor':first['next_cursor']})).status_code==200
        assert (await admin.get(f"/api/access-preparation/snapshots/{report['id']}/items",params={'cursor':first['next_cursor']})).status_code==422
        assert (await admin.get(path+'/items',params={'limit':201})).status_code==422
        assert (await client_b_admin.get(path)).status_code==404
        assert (await client_b_admin.get(path+'/items')).status_code==404
    async with client(ids) as clerk:
        assert (await clerk.get(path)).status_code==403
        assert (await clerk.get(path+'/items')).status_code==403


@pytest.mark.parametrize('same_key',[False,True])
async def test_concurrent_acceptance_has_one_immutable_effect(migrator_session,scope_seed,same_key):
    import asyncio
    ids = await retained_fixture(migrator_session,scope_seed)
    async with client(ids,'actor') as admin:
        report = await reviewed(admin)
        state = await assessment(admin,report)
        first,second = str(uuid.uuid4()),str(uuid.uuid4())
        results = await asyncio.gather(*(admin.post(f"/api/access-preparation/snapshots/{report['id']}/disposition/accept",
            json=acceptance_body(report,state),headers={'Idempotency-Key':key}) for key in (first,first if same_key else second)))
        assert sorted(r.status_code for r in results)==([201,201] if same_key else [201,409])
        if same_key: assert results[0].json()==results[1].json()
    assert await read_value('SELECT count(*) FROM access_disposition_acceptance WHERE organization_id=:org',ids)==1
    assert await read_value("SELECT count(*) FROM audit_event WHERE organization_id=:org AND action='access.disposition_accepted'",ids)==1


async def test_audit_failure_rolls_back_acceptance(migrator_session,scope_seed):
    from sqlalchemy.exc import DBAPIError
    from tests.test_scoped_account_statements import mutate
    ids = await retained_fixture(migrator_session,scope_seed)
    async with client(ids,'actor') as admin:
        report = await reviewed(admin)
        state = await assessment(admin,report)
        await mutate(text("""CREATE FUNCTION test_reject_disposition_audit() RETURNS trigger LANGUAGE plpgsql AS $$
          BEGIN IF NEW.action='access.disposition_accepted' THEN RAISE EXCEPTION 'Injected disposition failure'; END IF; RETURN NEW; END $$"""),{})
        await mutate(text('CREATE TRIGGER zz_test_reject_disposition_audit BEFORE INSERT ON audit_event FOR EACH ROW EXECUTE FUNCTION test_reject_disposition_audit()'),{})
        try:
            with pytest.raises(DBAPIError,match='Injected disposition failure'):
                await admin.post(f"/api/access-preparation/snapshots/{report['id']}/disposition/accept",json=acceptance_body(report,state),headers={'Idempotency-Key':str(uuid.uuid4())})
            assert await read_value('SELECT count(*) FROM access_disposition_acceptance WHERE organization_id=:org',ids)==0
            assert (await assessment(admin,report))['acceptance_available']
        finally:
            await mutate(text('DROP TRIGGER zz_test_reject_disposition_audit ON audit_event'),{})
            await mutate(text('DROP FUNCTION test_reject_disposition_audit()'),{})


async def test_retained_acceptance_is_private_immutable_and_prevents_downgrade(migrator_session,scope_seed):
    import importlib
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    from tests.test_scoped_account_statements import mutate
    ids = await retained_fixture(migrator_session,scope_seed)
    async with client(ids,'actor') as admin:
        report = await reviewed(admin)
        state = await assessment(admin,report)
        accepted = await admin.post(f"/api/access-preparation/snapshots/{report['id']}/disposition/accept",json=acceptance_body(report,state),headers={'Idempotency-Key':str(uuid.uuid4())})
        assert accepted.status_code==201,accepted.text
    with pytest.raises(DBAPIError,match='immutable'):
        await mutate(text("UPDATE access_disposition_acceptance SET reason='Rewritten' WHERE organization_id=:org"),ids)
    engine = create_engine(MIGRATOR_SYNC_URL)
    migration = importlib.import_module('migrations.versions.0090_access_disposition')
    try:
        with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
            for role in ('app_rw','app_worker','app_review'):
                assert not db.scalar(text("SELECT has_table_privilege(:role,'access_disposition_acceptance','SELECT,INSERT,UPDATE,DELETE')"),{'role':role})
                assert not db.scalar(text("SELECT has_function_privilege(:role,'access_item_disposition(jsonb)','EXECUTE')"),{'role':role})
            with pytest.raises(DBAPIError,match='Retain disposition evidence'):
                with db.begin_nested(): migration.downgrade()
            point = db.begin_nested()
            try:
                before = db.scalar(text('SELECT jsonb_agg(to_jsonb(s) ORDER BY id)::text FROM access_coverage_snapshot s'))
                db.execute(text('TRUNCATE access_disposition_acceptance'))
                migration.downgrade();migration.upgrade()
                assert db.scalar(text('SELECT jsonb_agg(to_jsonb(s) ORDER BY id)::text FROM access_coverage_snapshot s'))==before
            finally: point.rollback()
    finally: engine.dispose()


async def test_unknown_mixed_and_unsupported_findings_fail_closed(migrator_session,scope_seed):
    import json
    await migrator_session.commit()
    base = {'kind':'bank_account','references':{'strict_audience':[]},'findings':['FINANCIAL_BINDING_REQUIRED','COMBINED_READER_REQUIRED']}
    for item in ({**base,'findings':base['findings']+['NEW_UNMODELED_RISK']},
                 {**base,'references':{'strict_audience':[],'binding_id':str(uuid.uuid4())}},
                 {**base,'kind':'unknown_future_resource'},
                 {**base,'kind':'accounting_scope','findings':['SEV_FINANCIAL_ADAPTER_UNSUPPORTED']},
                 {**base,'kind':'knowledge_budget_policy','findings':['BUDGET_POLICY_REVIEW_REQUIRED']}):
        assert await read_value('SELECT access_item_disposition(CAST(:item AS jsonb))',{'item':json.dumps(item)})=='BLOCKED'


@pytest.mark.parametrize('kind',['property_document','mandate_document','dunning_notice'])
async def test_reviewed_quarantine_is_retained_with_actual_strict_read_denial(migrator_session,scope_seed,kind):
    from tests.test_scoped_account_statements import mutate
    if kind=='mandate_document':
        from tests.test_scoped_mandate_documents import document_fixture
        from tests.mandate_legacy_fixture import seed_legacy_mandate
        ids = await document_fixture(migrator_session,scope_seed)
        resource = await seed_legacy_mandate(ids)
    elif kind=='dunning_notice':
        from tests.test_scoped_dunning import legacy_notice
        ids = await statement_fixture(migrator_session,scope_seed,'lease')
        await legacy_notice(ids)
        resource = ids['notice']
    else:
        ids = await fixture(migrator_session,scope_seed)
        resource = uuid.uuid4()
        await mutate(text("INSERT INTO property_document(id,organization_id,property_id,doc_type,title,original_filename,mime_type,content_byte_size,content_sha256,storage_uri,storage_backend,page_count) VALUES(:document,:org,:property,'ANNEX','Retained original','retained.pdf','application/pdf',1,repeat('b',64),'synthetic/retained','TEST',1)"),{**ids,'document':resource})
    async with client(ids,'actor') as admin:
        if kind=='property_document':
            domain = await admin.post('/api/property-data-domains',json={'property_id':str(ids['property']),'label':'Review','reason':'Explicit source review'},headers={'Idempotency-Key':str(uuid.uuid4())})
            assert domain.status_code==201,domain.text
            grant = await admin.post(f"/api/property-data-domains/{domain.json()['id']}/grants",json={'user_id':str(ids['actor']),'capabilities':['read','prepare','approve'],'reason':'Review quarantine'},headers={'Idempotency-Key':str(uuid.uuid4())})
            assert grant.status_code==201,grant.text
            from tests.test_bound_source_access import classify
            await classify(admin,{**ids,'document':resource},'QUARANTINED')
        else:
            path = '/api/mandate-quarantine' if kind=='mandate_document' else '/api/dunning-quarantine'
            field = 'document_id' if kind=='mandate_document' else 'notice_id'
            candidates = (await admin.get(path+'/page')).json()['items']
            row = next(r for r in candidates if r[field]==str(resource))
            response = await admin.post(path,json={field:str(resource),'snapshot_digest':row['snapshot_digest'],
                'reason':'CUSTODY_UNVERIFIED' if kind=='mandate_document' else 'SOURCE_CONTEXT_UNVERIFIED'},headers={'Idempotency-Key':str(uuid.uuid4())})
            assert response.status_code==201,response.text
        report = await reviewed(admin)
        entry = next(row for row in await decisions(admin,report) if row['resource_id']==str(resource))
        assert entry['disposition']=='RETAIN_BLOCKED',entry
        async with synthetic_enforcement(scope_seed):
            for who in ('actor','clerk'):
                assert await read_value(f'SELECT count(*) FROM {kind} WHERE id=:resource',{**ids,'resource':resource},actor_key=who)==0


@pytest.mark.parametrize('work_kind',['lease','extraction','invoice_attempt','knowledge'])
async def test_active_work_is_not_hidden_by_resource_coverage(migrator_session,scope_seed,work_kind):
    ids = await retained_fixture(migrator_session,scope_seed,commit=False)
    ids.update(property=uuid.uuid4(),job=uuid.uuid4(),work=uuid.uuid4())
    db = migrator_session
    await db.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:actor,true)"),{'org':str(ids['org']),'actor':str(ids['actor'])})
    await db.execute(text("INSERT INTO property(id,organization_id,name,street,zip_code,city,management_type) VALUES(:property,:org,'Active work','','','','WEG')"),ids)
    if work_kind in ('lease','extraction'):
        await db.execute(text("INSERT INTO declaration_import(id,organization_id,mode,target_property_id,status,initiated_by,max_cost_usd) VALUES(:job,:org,'EXISTING_PROPERTY',:property,'DRAFT',:actor,1)"),ids)
        if work_kind=='lease':
            await db.execute(text("UPDATE declaration_import SET lease_expires_at=clock_timestamp()+interval '1 hour',lease_owner='synthetic',lease_token=:work,lease_generation=1 WHERE id=:job"),ids)
        else:
            await db.execute(text("INSERT INTO declaration_extraction_run(id,organization_id,import_id,provider,model_name,prompt_version,schema_version,harness_version) VALUES(:work,:org,:job,'fake','fake','fake','fake','fake')"),ids)
    elif work_kind=='invoice_attempt':
        await db.execute(text("INSERT INTO invoice_processing_attempt(id,organization_id,document_id,initiated_by,attempt_no,status) VALUES(:work,:org,:document,:actor,1,'queued')"),ids)
    else:
        await db.execute(text("INSERT INTO property_knowledge_query_run(id,organization_id,property_id,initiated_by,budget_window_start,provider,model,reserved_cost_usd,reservation_expires_at) VALUES(:work,:org,:property,:actor,clock_timestamp(),'fake','fake',0.001,clock_timestamp()+interval '1 hour')"),ids)
    await db.commit()
    async with client(ids,'actor') as admin:
        report = await reviewed(admin)
        state = await assessment(admin,report)
        assert state['active_work_count']==1 and not state['acceptance_available']


async def test_unreviewed_and_changed_budget_evidence_cannot_be_accepted(migrator_session,scope_seed,monkeypatch):
    from decimal import Decimal
    from app.core.config import settings
    ids = await retained_fixture(migrator_session,scope_seed)
    async with client(ids,'actor') as admin:
        await review_budget(admin)
        report = await capture(admin)
        state = await assessment(admin,report)
        path = f"/api/access-preparation/snapshots/{report['id']}/disposition/accept"
        assert state['current'] and not state['reviewed'] and not state['acceptance_available']
        assert (await admin.post(path,json=acceptance_body(report,state),headers={'Idempotency-Key':str(uuid.uuid4())})).status_code==409
        review = await admin.post(f"/api/access-preparation/snapshots/{report['id']}/review",json={
            'manifest_digest':report['manifest_digest'],'acknowledged':True},headers={'Idempotency-Key':str(uuid.uuid4())})
        assert review.status_code==201,review.text
        assert (await assessment(admin,report))['acceptance_available']
        monkeypatch.setattr(settings,'declaration_qa_max_cost_usd',settings.declaration_qa_max_cost_usd+Decimal('0.01'))
        assert (await admin.post(path,json=acceptance_body(report,state),headers={'Idempotency-Key':str(uuid.uuid4())})).status_code==409
        assert not (await assessment(admin,report))['current']
    assert await read_value('SELECT count(*) FROM access_disposition_acceptance WHERE organization_id=:org',ids)==0
