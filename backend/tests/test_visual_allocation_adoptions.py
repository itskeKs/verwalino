"""Reviewed page adoption must preserve permanent money and private originals."""
import hashlib
from decimal import Decimal
import uuid
import pytest
from sqlalchemy import text
from tests.test_financial_scope_boundary import worker_tenant, scope_seed  # noqa: F401
from tests.test_invoice_source_custody import store, fixture
from tests.test_scoped_account_statements import client, mutate
from tests.test_scope_payment_reconciliation import read_value
from tests.test_visual_extract_artifacts import basis
from tests.test_visual_disclosures import publish_command
from tests.test_allocation_adoptions import reviewed

pytestmark = pytest.mark.asyncio


async def visual_review(api, ids, monkeypatch):
    from app.core.db import get_worker_session_factory
    from app.core.intake_extraction import extract_logical
    from app.workers.outbox import process_once as deliver
    from app.workers.intake_extraction import process_once as extract
    import tests.test_assignment_proposals as assignments
    ref, original, logical, _, artifact_body = await basis(api,ids)
    await mutate(text('UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:scope AND user_id=:clerk AND revoked_at IS NULL'),ids)
    await mutate(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES(:org,:scope,:clerk,ARRAY['read','prepare','confirm','approve','export'],:actor,'Explicit visual financial review')"),ids)
    while await deliver(get_worker_session_factory(),ids['org']): pass
    while await extract(get_worker_session_factory(),ids['org'],processor=extract_logical): pass
    partition = (await api.get('/api/document-refs/'+ref['id']+'/partition')).json()['current']
    extraction = (await api.get('/api/document-refs/'+ref['id']+'/extractions',params={'logical_document_id':logical['id']})).json()['items'][0]
    async def selected(*args, **kwargs): return ref,logical,partition,extraction
    with monkeypatch.context() as patch:
        patch.setattr(assignments,'prepared',selected)
        _, allocation, treatment = await reviewed(api,ids)
    response = await api.post('/api/logical-documents/'+logical['id']+'/visual-extracts',json=artifact_body,headers={'Idempotency-Key':str(uuid.uuid4())})
    assert response.status_code == 201,response.text
    artifact = response.json()
    response = await api.post('/api/visual-extracts/'+artifact['id']+'/disclosures',**publish_command(artifact,{**ids,'destination':ids['scope']}))
    assert response.status_code == 201,response.text
    return ref,original,logical,allocation,treatment,artifact,response.json()


def visual_command(treatment,artifact,disclosure,preview):
    return {'json':{'treatment_review_id':treatment['id'],'disclosure_id':disclosure['receipt']['id'],
                   'visual_content_sha256':artifact['content_sha256'],'preview_digest':preview['preview_digest'],
                   'reason':'Confirm reviewed costs and retain only the exact reviewed visual source',
                   'costs_confirmed':True,'retained_visual_source_reviewed':True},
            'headers':{'Idempotency-Key':str(uuid.uuid4()),'If-Match':'"'+treatment['id']+'"'}}


async def test_page_adoption_releases_only_exact_derivative_and_survives_transient_withdrawal(migrator_session,scope_seed,store,monkeypatch):
    ids = await fixture(migrator_session,scope_seed)
    async with client(ids) as source, client(ids,'actor') as destination:
        ref,original,logical,allocation,treatment,artifact,disclosure = await visual_review(source,ids,monkeypatch)
        preview = await source.get('/api/allocation-treatments/'+treatment['id']+'/visual-adoption-preview',params={'disclosure_id':disclosure['receipt']['id']})
        assert preview.status_code == 200,preview.text
        assert preview.json()['can_adopt'] and preview.json()['source_variant'] == 'visual'
        command = visual_command(treatment,artifact,disclosure,preview.json())
        url = '/api/logical-documents/'+logical['id']+'/visual-allocation-adoptions'
        response = await source.post(url,**command)
        assert response.status_code == 201,response.text
        adopted = response.json()
        assert adopted['source_variant'] == 'visual' and adopted['cost_count'] == 2
        assert sum(Decimal(row['gross_amount']) for row in adopted['costs']) == Decimal('119.00')
        await mutate(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES(:org,:scope,:actor,ARRAY['read','approve','export'],:actor,'Destination only financial source reader')"),ids)
        content_url = '/api/invoice-documents/'+adopted['source_document_id']+'/content'
        content = await destination.get(content_url)
        assert content.status_code == 200,content.text
        assert hashlib.sha256(content.content).hexdigest() == artifact['content_sha256']
        public = await destination.get('/api/invoice-documents/'+adopted['source_document_id'])
        assert public.status_code == 200 and ref['content_sha256'] not in public.text
        assert public.json()['original_filename'] == 'reviewed-visual-source.pdf'
        assert (await destination.get('/api/intake-batches/originals/'+original['original_id']+'/content')).status_code == 404
        withdraw = await destination.post('/api/visual-disclosures/'+disclosure['receipt']['id']+'/withdrawals',
            json={'reason':'Stop transient release; permanent financial source remains separately retained','withdrawal_confirmed':True},
            headers={'Idempotency-Key':str(uuid.uuid4()),'If-Match':'"'+disclosure['receipt']['id']+'"'})
        assert withdraw.status_code == 201,withdraw.text
        assert (await destination.get('/api/visual-disclosures/'+disclosure['receipt']['id']+'/content')).status_code == 404
        assert (await destination.get(content_url)).status_code == 200
        assert (await source.post(url,**command)).json() == adopted
    claim = await read_value('SELECT to_jsonb(c) FROM allocation_source_claim c WHERE organization_id=:org',ids)
    assert claim['source_sha256'] == ref['content_sha256'] and claim['page_start'] == claim['page_end'] == 1
    assert await read_value('SELECT count(*) FROM cost_entry WHERE organization_id=:org',ids) == 2


async def adopt(api, logical, treatment, artifact, disclosure):
    preview = await api.get('/api/allocation-treatments/'+treatment['id']+'/visual-adoption-preview',params={'disclosure_id':disclosure['receipt']['id']})
    assert preview.status_code == 200 and preview.json()['can_adopt'],preview.text
    body = visual_command(treatment,artifact,disclosure,preview.json())
    response = await api.post('/api/logical-documents/'+logical['id']+'/visual-allocation-adoptions',**body)
    assert response.status_code == 201,response.text
    return response.json(),body


def correction_command(old,treatment,artifact,preview):
    body = visual_command(treatment,artifact,{'receipt':{'id':str(uuid.uuid4())}},preview)
    del body['json']['disclosure_id']
    body['json']['downstream_followups_reviewed'] = True
    body['headers']['If-Match'] = '"'+old['id']+'"'
    return body


async def test_visual_correction_keeps_retained_bytes_after_withdrawal(migrator_session,scope_seed,store,monkeypatch):
    from tests.test_allocation_corrections import next_review
    from tests.test_allocation_correction_preview import parent
    ids = await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        ref,_,logical,allocation,treatment,artifact,disclosure = await visual_review(api,ids,monkeypatch)
        old,adoption_body = await adopt(api,logical,treatment,artifact,disclosure)
        response = await api.post('/api/visual-disclosures/'+disclosure['receipt']['id']+'/withdrawals',
            json={'reason':'Withdraw temporary publication after financial retention','withdrawal_confirmed':True},
            headers={'Idempotency-Key':str(uuid.uuid4()),'If-Match':'"'+disclosure['receipt']['id']+'"'})
        assert response.status_code == 201,response.text
        allocation,treatment = await next_review(api,allocation,treatment)
        run = await parent(ids)
        preview = await api.get('/api/allocation-sets/'+old['id']+'/visual-correction-preview',params={'treatment_review_id':treatment['id']})
        assert preview.status_code == 200 and preview.json()['can_review'],preview.text
        assert preview.json()['visual_content_sha256'] == artifact['content_sha256']
        assert not (await api.get('/api/allocation-sets/'+old['id']+'/correction-preview',params={'treatment_review_id':treatment['id']})).json()['can_review']
        body = correction_command(old,treatment,artifact,preview.json())
        url = '/api/allocation-sets/'+old['id']+'/visual-corrections'
        result = await api.post(url,**body)
        assert result.status_code == 201,result.text
        result = result.json(); new = result['allocation']
        assert new['source_variant'] == 'visual' and new['id'] != old['id']
        assert {item['snapshot']['id'] for item in result['receipt']['impacts']} == {str(run)}
        for source_id in [old['source_document_id'],new['source_document_id']]:
            downloaded = await api.get('/api/invoice-documents/'+source_id+'/content')
            assert downloaded.status_code == 200 and hashlib.sha256(downloaded.content).hexdigest() == artifact['content_sha256']
        source = (await api.get('/api/invoice-documents/'+new['source_document_id'])).json()
        assert source['version'] == 2 and source['supersedes_id'] == old['source_document_id']
        assert ref['content_sha256'] not in str(result)
        monkeypatch.setattr('app.services.allocation_adoption_service.verify_document_integrity_and_size',lambda *args:pytest.fail('Historical replay must not reread storage'))
        assert (await api.post(url,**body)).json() == result
        assert (await api.post('/api/logical-documents/'+logical['id']+'/visual-allocation-adoptions',**adoption_body)).json() == old
    assert await read_value('SELECT count(*) FROM allocation_source_claim WHERE organization_id=:org',ids) == 1
    assert await read_value('SELECT count(*) FROM cost_entry WHERE organization_id=:org AND superseded_by_id IS NULL',ids) == 2


@pytest.mark.parametrize('table,condition', [('cost_entry','NEW.gross_amount=60'),('invoice_document','true'),('allocation_source_claim','true'),('audit_event',"NEW.action='document.allocation_released'")])
async def test_visual_adoption_atomic_failure_and_concurrent_replay(migrator_session,scope_seed,store,monkeypatch,table,condition):
    import asyncio
    from tests.test_allocation_adoptions import assert_no_adoption
    ids = await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        _,_,logical,_,treatment,artifact,disclosure = await visual_review(api,ids,monkeypatch)
        preview = (await api.get('/api/allocation-treatments/'+treatment['id']+'/visual-adoption-preview',params={'disclosure_id':disclosure['receipt']['id']})).json()
        body = visual_command(treatment,artifact,disclosure,preview); url = '/api/logical-documents/'+logical['id']+'/visual-allocation-adoptions'
        await mutate(text("CREATE FUNCTION synthetic_visual_adoption_failure() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF "+condition+" THEN RAISE EXCEPTION 'Synthetic visual financial rollback' USING ERRCODE='23514';END IF;RETURN NEW;END $$"),{})
        await mutate(text('CREATE TRIGGER synthetic_visual_adoption_failure BEFORE INSERT ON '+table+' FOR EACH ROW EXECUTE FUNCTION synthetic_visual_adoption_failure()'),{})
        try:
            response = await api.post(url,**body); assert response.status_code == 422,response.text
            await assert_no_adoption(ids)
            assert await read_value('SELECT count(*) FROM allocation_source_claim WHERE organization_id=:org',ids) == 0
        finally:
            await mutate(text('DROP TRIGGER synthetic_visual_adoption_failure ON '+table),{})
            await mutate(text('DROP FUNCTION synthetic_visual_adoption_failure()'),{})
        replies = await asyncio.gather(api.post(url,**body),api.post(url,**body))
        assert [r.status_code for r in replies] == [201,201],[r.text for r in replies]
        assert replies[0].json() == replies[1].json()
        assert (await api.post(url,**visual_command(treatment,artifact,disclosure,preview))).status_code == 409


async def test_visual_adoption_requires_exact_acknowledgments_bytes_publication_and_authority(migrator_session,scope_seed,store,monkeypatch,client_b_admin):
    from tests.test_allocation_adoptions import assert_no_adoption
    import app.services.allocation_adoption_service as service
    ids = await fixture(migrator_session,scope_seed)
    async with client(ids) as api,client(ids,'actor') as other:
        _,_,logical,_,treatment,artifact,disclosure = await visual_review(api,ids,monkeypatch)
        preview = (await api.get('/api/allocation-treatments/'+treatment['id']+'/visual-adoption-preview',params={'disclosure_id':disclosure['receipt']['id']})).json()
        body = visual_command(treatment,artifact,disclosure,preview); url = '/api/logical-documents/'+logical['id']+'/visual-allocation-adoptions'
        for field in ('costs_confirmed','retained_visual_source_reviewed'):
            assert (await api.post(url,**{**body,'json':{**body['json'],field:False}})).status_code == 422
        assert (await api.post(url,**{**body,'json':{**body['json'],'whole_original_disclosure_reviewed':True}})).status_code == 422
        for field in ('visual_content_sha256','preview_digest'):
            response = await api.post(url,**{**body,'json':{**body['json'],field:'a'*64}})
            assert response.status_code == (422 if field == 'visual_content_sha256' else 409),response.text
        assert (await other.post(url,**body)).status_code == 404
        assert (await client_b_admin.post(url,**body)).status_code == 404
        with monkeypatch.context() as patch:
            patch.setattr(service,'verify_document_integrity_and_size',lambda *args:False)
            assert (await api.post(url,**body)).status_code == 409
        uri = await read_value('SELECT storage_uri FROM visual_extract_artifact WHERE id=:target',{'target':uuid.UUID(artifact['id'])})
        path = store._resolve_key(uri); original_bytes = path.read_bytes(); path.chmod(0o600); path.write_bytes(b'Corrupt visual bytes')
        try: assert (await api.post(url,**body)).status_code == 409
        finally: path.write_bytes(original_bytes)
        await assert_no_adoption(ids)
        withdrawal = await api.post('/api/visual-disclosures/'+disclosure['receipt']['id']+'/withdrawals',
            json={'reason':'Withdraw before confirming money','withdrawal_confirmed':True},
            headers={'Idempotency-Key':str(uuid.uuid4()),'If-Match':'"'+disclosure['receipt']['id']+'"'})
        assert withdrawal.status_code == 201,withdrawal.text
        assert (await api.post(url,**body)).status_code == 409
        await mutate(text('UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:scope AND user_id=:clerk AND revoked_at IS NULL'),ids)
        assert (await api.post(url,**body)).status_code == 404
        await assert_no_adoption(ids)


@pytest.mark.parametrize('same_identity',[False,True])
async def test_disjoint_pages_adopt_into_separate_scopes_and_keep_independent_corrections(migrator_session,scope_seed,store,monkeypatch,same_identity):
    import tests.test_assignment_proposals as assignments
    import tests.test_context_decisions as contexts
    import tests.test_allocation_adoptions as adoptions
    from tests.test_allocation_corrections import next_review
    ids = await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        ref,_,first,allocation,treatment,artifact,disclosure = await visual_review(api,ids,monkeypatch)
        first_set,_ = await adopt(api,first,treatment,artifact,disclosure)
        await mutate(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES(:org,:other_scope,:clerk,ARRAY['read','prepare','confirm','approve','export'],:actor,'Separate reviewed invoice scope')"),ids)
        partition = (await api.get('/api/document-refs/'+ref['id']+'/partition')).json()['current']
        logicals = (await api.get('/api/logical-documents',params={'document_ref_id':ref['id']})).json()['items']
        rest = next(item for item in logicals if item['page_start']==2 and item['page_end']==3)
        extraction = (await api.get('/api/document-refs/'+ref['id']+'/extractions',params={'logical_document_id':rest['id']})).json()['items'][0]
        async def selected(*args,**kwargs):return ref,rest,partition,extraction
        with monkeypatch.context() as patch:
            patch.setattr(assignments,'prepared',selected)
            original_context = contexts.command
            patch.setattr(contexts,'command',lambda *args,**kwargs:original_context(*args,**kwargs,page_number=2,quotation='Reviewed separate invoice on page 2'))
            original_treatment = adoptions.treatment_command
            def rental_treatment(*args,**kwargs):
                body=original_treatment(*args,**kwargs)
                for line in body['json']['lines']:line['owner_distribution_key']='not_applicable'
                return body
            patch.setattr(adoptions,'treatment_command',rental_treatment)
            _,other_allocation,other_treatment = await reviewed(api,{**ids,'scope':ids['other_scope']},document_number='R-2026-42' if same_identity else 'SECOND-INVOICE')
        response = await api.post('/api/logical-documents/'+rest['id']+'/visual-extracts',json={'partition_revision_id':partition['id'],'reason':'Review second invoice pages'},headers={'Idempotency-Key':str(uuid.uuid4())})
        assert response.status_code==201,response.text
        other_artifact=response.json()
        response = await api.post('/api/visual-extracts/'+other_artifact['id']+'/disclosures',**publish_command(other_artifact,{**ids,'destination':ids['other_scope']}))
        assert response.status_code==201,response.text
        other_disclosure=response.json()
        if same_identity:
            preview=await api.get('/api/allocation-treatments/'+other_treatment['id']+'/visual-adoption-preview',params={'disclosure_id':other_disclosure['receipt']['id']})
            assert preview.status_code==200 and not preview.json()['can_adopt'],preview.text
            assert await read_value('SELECT count(*) FROM cost_entry WHERE organization_id=:org',ids)==2
            return
        other_set,_ = await adopt(api,rest,other_treatment,other_artifact,other_disclosure)
        from tests.test_exception_completions import command as completion_command,completion_preview
        cases=(await api.get('/api/exceptions')).json()['items']
        case=next(item for item in cases if item['cause']=='scope_hint_conflict')
        completion=await completion_preview(api,case)
        assert completion['can_complete'],completion
        assert [item['allocation_set_id'] for item in completion['members']]==[other_set['id']]
        finished=await api.post('/api/exceptions/'+case['id']+'/completions',**completion_command(completion))
        assert finished.status_code==201,finished.text
        assert other_set['scope_id']==str(ids['other_scope']) and first_set['scope_id']==str(ids['scope'])
        response = await api.post('/api/visual-disclosures/'+disclosure['receipt']['id']+'/withdrawals',json={'reason':'Withdraw first transient disclosure','withdrawal_confirmed':True},headers={'Idempotency-Key':str(uuid.uuid4()),'If-Match':'"'+disclosure['receipt']['id']+'"'})
        assert response.status_code==201,response.text
        allocation,treatment = await next_review(api,allocation,treatment)
        preview = (await api.get('/api/allocation-sets/'+first_set['id']+'/visual-correction-preview',params={'treatment_review_id':treatment['id']})).json()
        assert preview['can_review'],preview
        response = await api.post('/api/allocation-sets/'+first_set['id']+'/visual-corrections',**correction_command(first_set,treatment,artifact,preview))
        assert response.status_code==201,response.text
        assert (await api.get('/api/logical-documents/'+rest['id']+'/allocation-adoptions')).json()['item']==other_set
        assert (await api.get('/api/invoice-documents/'+other_set['source_document_id'])).json()['status']=='active'
        assert not (await api.get('/api/allocation-treatments/'+other_treatment['id']+'/adoption-preview')).json()['can_adopt']
        assert not (await api.get('/api/allocation-treatments/'+other_treatment['id']+'/visual-adoption-preview',params={'disclosure_id':other_disclosure['receipt']['id']})).json()['can_adopt']
    claims=await read_value("SELECT jsonb_agg(jsonb_build_array(page_start,page_end) ORDER BY page_start) FROM allocation_source_claim WHERE organization_id=:org",ids)
    assert claims==[[1,1],[2,3]]
    assert await read_value('SELECT count(*) FROM cost_entry WHERE organization_id=:org AND superseded_by_id IS NULL',ids)==4


async def test_visual_private_columns_helpers_and_downgrade_are_sealed(migrator_session,scope_seed,store,monkeypatch):
    import importlib
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    ids=await fixture(migrator_session,scope_seed)
    migration=importlib.import_module('migrations.versions.0121_visual_allocation_sources')
    engine=create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
            before={signature:db.scalar(text('SELECT pg_get_functiondef(CAST(:signature AS regprocedure))'),{'signature':signature}) for signature in migration.ADAPTED}
            for role in ['app_rw','app_worker','app_review']:
                for signature in migration.ADDED:
                    assert db.scalar(text('SELECT has_function_privilege(:role,:signature,\'execute\')'),{'role':role,'signature':signature}) == (role in ['app_rw','app_worker'] and signature in migration.INTERACTIVE)
                for column in ['content_sha256','visual_artifact_id','visual_disclosure_id','visual_integrity_attestation_id','intake_original_id']:
                    assert not db.scalar(text("SELECT has_column_privilege(:role,'allocation_set',:column,'SELECT')"),{'role':role,'column':column})
            point=db.begin_nested()
            try:
                db.execute(text('TRUNCATE allocation_set CASCADE'))
                importlib.import_module('migrations.versions.0123_replacement_exception_outcomes').downgrade()
                importlib.import_module('migrations.versions.0122_allocation_source_replacements').downgrade()
                migration.downgrade()
                assert db.scalar(text("SELECT has_column_privilege('app_rw','allocation_set','content_sha256','SELECT')"))
                migration.upgrade()
                importlib.import_module('migrations.versions.0122_allocation_source_replacements').upgrade()
                importlib.import_module('migrations.versions.0123_replacement_exception_outcomes').upgrade()
                assert before=={signature:db.scalar(text('SELECT pg_get_functiondef(CAST(:signature AS regprocedure))'),{'signature':signature}) for signature in before}
            finally:point.rollback()
        async with client(ids) as api:
            _,_,logical,_,treatment,artifact,disclosure=await visual_review(api,ids,monkeypatch)
            await adopt(api,logical,treatment,artifact,disclosure)
        with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
            with pytest.raises(DBAPIError,match='Retain visual financial sources'),db.begin_nested():migration.downgrade()
        for column in ['content_sha256','visual_artifact_id','visual_disclosure_id','intake_original_id']:
            with pytest.raises(DBAPIError,match='permission denied'):
                await read_value('SELECT '+column+' FROM allocation_set WHERE organization_id=:org',ids,actor_key='clerk')
    finally:engine.dispose()


@pytest.mark.parametrize('proof_name',['original_proof','visual_proof'])
async def test_visual_adoption_rejects_independent_mismatched_byte_proofs(migrator_session,scope_seed,store,monkeypatch,proof_name):
    from app.services.allocation_adoption_service import AllocationAdoptionService
    from tests.test_allocation_adoptions import assert_no_adoption
    original_call=AllocationAdoptionService.call
    async def mismatch(self,sql,**params):
        if sql.startswith('SELECT adopt_visual_allocation('):params[proof_name]=uuid.uuid4()
        return await original_call(self,sql,**params)
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        _,_,logical,_,treatment,artifact,disclosure=await visual_review(api,ids,monkeypatch)
        preview=(await api.get('/api/allocation-treatments/'+treatment['id']+'/visual-adoption-preview',params={'disclosure_id':disclosure['receipt']['id']})).json()
        with monkeypatch.context() as patch:
            patch.setattr(AllocationAdoptionService,'call',mismatch)
            result=await api.post('/api/logical-documents/'+logical['id']+'/visual-allocation-adoptions',**visual_command(treatment,artifact,disclosure,preview))
            assert result.status_code==404,result.text
        await assert_no_adoption(ids)
        await adopt(api,logical,treatment,artifact,disclosure)


async def test_visual_proofs_cannot_cross_transactions(migrator_session,scope_seed,store,monkeypatch):
    from fastapi import HTTPException
    from sqlalchemy.exc import DBAPIError
    from app.services.allocation_adoption_service import AllocationAdoptionService
    from tests.test_allocation_adoptions import assert_no_adoption
    original_call=AllocationAdoptionService.call;captured={}
    async def interrupted(self,sql,**params):
        if sql.startswith('SELECT adopt_visual_allocation('):
            captured.update(sql=sql,params=params)
            raise HTTPException(503,'Injected interruption after trusted byte verification')
        return await original_call(self,sql,**params)
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        _,_,logical,_,treatment,artifact,disclosure=await visual_review(api,ids,monkeypatch)
        preview=(await api.get('/api/allocation-treatments/'+treatment['id']+'/visual-adoption-preview',params={'disclosure_id':disclosure['receipt']['id']})).json()
        body=visual_command(treatment,artifact,disclosure,preview)
        with monkeypatch.context() as patch:
            patch.setattr(AllocationAdoptionService,'call',interrupted)
            assert (await api.post('/api/logical-documents/'+logical['id']+'/visual-allocation-adoptions',**body)).status_code==503
        await assert_no_adoption(ids)
        with pytest.raises(DBAPIError,match='integrity|proof'):
            await read_value(captured['sql'],{**ids,**captured['params']},actor_key='clerk')
        result=await api.post('/api/logical-documents/'+logical['id']+'/visual-allocation-adoptions',**body)
        assert result.status_code==201,result.text


@pytest.mark.parametrize('table,operation,condition',[
    ('cost_entry','INSERT','true'),('allocation_correction','INSERT','true'),
    ('allocation_correction_impact','INSERT','true'),('cost_entry','UPDATE','NEW.superseded_by_id IS NOT NULL'),
    ('invoice_document','UPDATE',"NEW.status='superseded'"),('invoice_document','INSERT','NEW.version=2'),
    ('audit_event','INSERT',"NEW.action='document.allocation_corrected'")])
async def test_visual_correction_atomic_failure_at_every_financial_stage(migrator_session,scope_seed,store,monkeypatch,table,operation,condition):
    import asyncio
    from tests.test_allocation_corrections import next_review
    from tests.test_allocation_correction_preview import parent
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        _,_,logical,allocation,treatment,artifact,disclosure=await visual_review(api,ids,monkeypatch)
        old,_=await adopt(api,logical,treatment,artifact,disclosure)
        allocation,treatment=await next_review(api,allocation,treatment);await parent(ids)
        preview=(await api.get('/api/allocation-sets/'+old['id']+'/visual-correction-preview',params={'treatment_review_id':treatment['id']})).json()
        assert preview['can_review'],preview
        body=correction_command(old,treatment,artifact,preview);url='/api/allocation-sets/'+old['id']+'/visual-corrections'
        await mutate(text("CREATE FUNCTION synthetic_visual_correction_failure() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF "+condition+" THEN RAISE EXCEPTION 'Synthetic visual correction rollback' USING ERRCODE='23514';END IF;RETURN NEW;END $$"),{})
        await mutate(text('CREATE TRIGGER synthetic_visual_correction_failure BEFORE '+operation+' ON '+table+' FOR EACH ROW EXECUTE FUNCTION synthetic_visual_correction_failure()'),{})
        try:
            result=await api.post(url,**body);assert result.status_code==422,result.text
            for relation,count in [('cost_entry',2),('invoice_document',1),('allocation_set',1),('allocation_source_claim',1),('allocation_correction',0),('allocation_correction_impact',0)]:
                assert await read_value('SELECT count(*) FROM '+relation+' WHERE organization_id=:org',ids)==count
            assert await read_value('SELECT count(*) FROM cost_entry WHERE organization_id=:org AND superseded_by_id IS NOT NULL',ids)==0
        finally:
            await mutate(text('DROP TRIGGER synthetic_visual_correction_failure ON '+table),{})
            await mutate(text('DROP FUNCTION synthetic_visual_correction_failure()'),{})
        replies=await asyncio.gather(api.post(url,**body),api.post(url,**body))
        assert [r.status_code for r in replies]==[201,201],[r.text for r in replies]
        assert replies[0].json()==replies[1].json()


async def test_visual_mapping_and_actual_financial_followup_keep_source_semantics(migrator_session,scope_seed,store,monkeypatch):
    import io
    from reportlab.pdfgen import canvas
    from tests.test_context_mappings import activation
    from tests.test_allocation_corrections import next_review
    from tests.test_correction_followups import declare
    ids=await fixture(migrator_session,scope_seed)
    out=io.BytesIO();pdf=canvas.Canvas(out)
    for page in range(3):
        pdf.drawString(40,800,'Objekt-ID: '+str(ids['property']) if page==0 else 'Other private invoice page')
        pdf.drawString(40,780,'Gesamtbetrag: 119,00 EUR');pdf.showPage()
    pdf.save()
    monkeypatch.setattr('tests.test_logical_documents.pdf_bytes',lambda count:out.getvalue())
    async with client(ids) as api,client(ids,'actor') as destination:
        ref,_,logical,allocation,treatment,artifact,disclosure=await visual_review(api,ids,monkeypatch)
        old,_=await adopt(api,logical,treatment,artifact,disclosure)
        response=await api.get('/api/allocation-sets/'+old['id']+'/mapping-options')
        assert response.status_code==200 and response.json()['options'],response.text
        option=next(item for item in response.json()['options'] if item['value']==str(ids['property']))
        assert option['page_number']==1
        mapping=await api.post('/api/context-mappings',**activation(old,option))
        assert mapping.status_code==201,mapping.text
        assert ref['content_sha256'] not in mapping.text and artifact['id'] not in mapping.text
        assert (await destination.get('/api/allocation-sets/'+old['id']+'/mapping-options')).status_code==404
        await mutate(text("INSERT INTO accounting_scope_binding(organization_id,property_id,scope_id,bank_account_id,origin,reason,reviewed_by) VALUES(:org,:property,:scope,:bank,'REVIEWED','Visual financial report',:actor)"),ids)
        created=await api.post('/api/weg-settlements/asset-reports',json={'property_id':str(ids['property']),'accounting_scope_id':str(ids['scope']),'period_start':'2026-01-01','period_end':'2026-12-31'})
        assert created.status_code==201,created.text
        target=created.json()['id'];url='/api/weg-settlements/asset-reports/'+target
        values={'reserve_override_reason':'Reviewed synthetic reserve totals','reserve_opening_balance':'0.00','reserve_withdrawals_total':'0.00','reserve_contributions_total':'0.00',
          'bank_balances':[{'bank_account_id':str(ids['bank']),'label':'Synthetic visual source account','account_type':'LAUFEND','balance':'0.00'}],'receivables':[],'payables':[]}
        await declare(api,target,values,old['source_document_id'])
        computed=await api.post(url+'/compute',json=values);assert computed.status_code==200,computed.text
        params={**ids,'target':uuid.UUID(target)}
        before=await read_value('SELECT financial_computation_state(:org,\'settlement_run\',:target)',params)
        allocation,treatment=await next_review(api,allocation,treatment)
        preview=(await api.get('/api/allocation-sets/'+old['id']+'/visual-correction-preview',params={'treatment_review_id':treatment['id']})).json()
        corrected=await api.post('/api/allocation-sets/'+old['id']+'/visual-corrections',**correction_command(old,treatment,artifact,preview))
        assert corrected.status_code==201,corrected.text
        result=corrected.json();new=result['allocation'];impact=next(item for item in result['receipt']['impacts'] if item['snapshot']['id']==target)
        status_url='/api/allocation-correction-impacts/'+impact['id']+'/follow-up'
        assert not (await api.get(status_url)).json()['ready']
        await declare(api,target,values,new['source_document_id'])
        computed=await api.post(url+'/compute',json=values);assert computed.status_code==200,computed.text
        after=await read_value('SELECT financial_computation_state(:org,\'settlement_run\',:target)',params)
        assert before['source_digest']!=after['source_digest']
        status=(await api.get(status_url)).json();assert status['ready'] and status['source_review_required'],status
        completed=await api.post('/api/allocation-correction-impacts/'+impact['id']+'/completions',json={'preview_digest':status['preview_digest'],'reason':'Recomputed and reviewed retained visual correction','source_reviewed':True,'confirmed':True},headers={'Idempotency-Key':str(uuid.uuid4())})
        assert completed.status_code==201,completed.text
        assert (await api.get(status_url)).json()['status']=='completed'


async def test_consumed_partial_source_blocks_original_and_regenerated_derivative_reuploads(migrator_session,scope_seed,store,monkeypatch):
    from sqlalchemy.exc import DBAPIError
    from tests.test_allocation_source_aliases import rendered
    from tests.test_allocation_adoptions import ready,command as original_command
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        ref,_,logical,allocation,treatment,artifact,disclosure=await visual_review(api,ids,monkeypatch)
        await adopt(api,logical,treatment,artifact,disclosure)
        again=await rendered(api,logical,artifact['partition_revision_id'])
        for digest in [ref['content_sha256'],artifact['content_sha256'],again['content_sha256']]:
            assert await read_value('SELECT allocation_source_overlap(:org,:hash,NULL,NULL,NULL)',{**ids,'hash':digest})
            with pytest.raises(DBAPIError,match='Source conflict'):
                await mutate(text("""INSERT INTO invoice_document(id,organization_id,uploaded_by,storage_uri,storage_backend,mime_type,
                  content_byte_size,content_sha256,version,chain_root_id,status,released_at,released_by,retention_until)
                  VALUES(:document,:org,:clerk,'synthetic/reuploaded','LOCAL_FS','application/pdf',1,:hash,1,:document,'active',now(),:clerk,'2034-12-31')"""),
                  {**ids,'document':uuid.uuid4(),'hash':digest})
        assert not await read_value('SELECT allocation_source_overlap(:org,:hash,2,3,NULL)',{**ids,'hash':ref['content_sha256']})
        data=(await api.get('/api/visual-extracts/'+again['id']+'/content')).content
        monkeypatch.setattr('tests.test_intake_extraction_worker.pdf_bytes',lambda:data)
        _,_,other,preview,url=await ready(api,ids,document_number='REUPLOADED-PARTIAL-VISUAL')
        assert not preview['can_adopt']
        rejected=await api.post(url,**original_command(other,preview));assert rejected.status_code==409,rejected.text
        assert await read_value('SELECT count(*) FROM cost_entry WHERE organization_id=:org',ids)==2
