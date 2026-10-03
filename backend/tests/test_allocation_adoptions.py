"""Atomic split adoption must preserve source custody, publication and exact money."""
import uuid
import pytest
from sqlalchemy import text
from tests.test_financial_scope_boundary import worker_tenant,scope_seed  # noqa: F401
from tests.test_invoice_source_custody import store,fixture
from tests.test_scoped_account_statements import client,mutate
from tests.test_scope_payment_reconciliation import read_value
from tests.test_allocation_proposals import lines,command as allocation_command
from tests.test_allocation_treatments import command as treatment_command

pytestmark=pytest.mark.asyncio


async def confirm_grant(ids,actor_key='clerk'):
    await mutate(text('UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:scope AND user_id=:subject AND revoked_at IS NULL'),{**ids,'subject':ids[actor_key]})
    await mutate(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES(:org,:scope,:subject,ARRAY['read','prepare','confirm','export'],:actor,'Explicit adoption fixture')"),{**ids,'subject':ids[actor_key]})


async def reviewed(api,ids,credit=False,scoped=False,source_scope=None,field_revisions=1,**fields):
    from tests.test_assignment_proposals import prepared,command as proposal_command
    from tests.test_invoice_reviews import command as field_command
    from tests.test_context_decisions import command as context_command
    ref,child,partition,extraction=await prepared(api,{**ids,'scope':source_scope or ids['scope']},scoped=scoped or source_scope is not None)
    reply=await api.post('/api/logical-documents/'+child['id']+'/assignment-proposals',**proposal_command(partition,extraction,ids['scope']));assert reply.status_code==201,reply.text
    proposal=reply.json()
    reply=await api.post('/api/logical-documents/'+child['id']+'/invoice-reviews',**field_command(proposal,
      document_kind='credit_note' if credit else 'invoice',gross_amount='-119.00' if credit else '119.00',**fields));assert reply.status_code==201,reply.text
    field_review=reply.json()
    for _ in range(field_revisions-1):
        reply=await api.post('/api/logical-documents/'+child['id']+'/invoice-reviews',**field_command(proposal,previous=field_review['id'],
          document_kind='credit_note' if credit else 'invoice',gross_amount='-119.00' if credit else '119.00',**fields));assert reply.status_code==201,reply.text
        field_review=reply.json()
    preview=(await api.get('/api/assignment-proposals/'+proposal['id']+'/context-preview')).json()
    reply=await api.post('/api/logical-documents/'+child['id']+'/context-decisions',**context_command(proposal,field_review,preview));assert reply.status_code==201,reply.text
    decision=reply.json()
    category=await read_value("SELECT id FROM cost_category WHERE is_active AND plan_version=1 AND NOT reserve_relevant AND owner_billing_relevant_default AND cost_group='UMLAGEFAEHIG' ORDER BY id LIMIT 1",ids)
    assert category is not None
    rows=await lines(ids,credit)
    for row in rows:row['cost_category_id']=str(category)
    allocation=await api.post('/api/logical-documents/'+child['id']+'/allocation-proposals',**allocation_command(decision,rows));assert allocation.status_code==201,allocation.text
    allocation=allocation.json();preview=(await api.get('/api/allocation-proposals/'+allocation['id']+'/treatment-preview')).json()
    body=treatment_command(allocation,preview)
    body['json']['lines'][0].update(tenant_treatment='partial',tenant_amount='-10.00' if credit else '10.00',tenant_distribution_key='WOHNFLAECHE')
    reply=await api.post('/api/logical-documents/'+child['id']+'/allocation-treatments',**body);assert reply.status_code==201,reply.text
    assert all(not line['markers'] for line in reply.json()['lines'])
    return child,allocation,reply.json()


def command(review,preview,key=None):
    return {'headers':{'Idempotency-Key':key or str(uuid.uuid4()),'If-Match':'"'+review['id']+'"'},'json':{
      'treatment_review_id':review['id'],'preview_digest':preview['preview_digest'],
      'reason':'Confirm exact split costs and publish the complete original to this scope',
      'costs_confirmed':True,'whole_original_disclosure_reviewed':True}}


@pytest.mark.parametrize('credit',[False,True])
async def test_adoption_confirms_complete_split_once_and_preserves_exact_source(migrator_session,scope_seed,store,credit):
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids)
    async with client(ids) as api:
        child,allocation,review=await reviewed(api,ids,credit)
        preview=await api.get('/api/allocation-treatments/'+review['id']+'/adoption-preview');assert preview.status_code==200,preview.text
        assert preview.json()['can_adopt'] is True
        body=command(review,preview.json());url='/api/logical-documents/'+child['id']+'/allocation-adoptions'
        result=await api.post(url,**body);assert result.status_code==201,result.text
        adopted=result.json();assert adopted['financial_state']=='confirmed' and len(adopted['costs'])==2
        assert (await api.post(url,**body)).json()==adopted
        source=(await api.get('/api/invoice-documents/'+adopted['source_document_id'])).json()
        assert source['released_at'] is not None and source['extraction_payload'] is None
        assert source['cost_entry_id']==adopted['costs'][0]['cost_entry_id']
        original_hash=await read_value('SELECT content_sha256 FROM document_ref WHERE organization_id=:org AND id=:ref',{**ids,'ref':uuid.UUID(allocation['document_ref_id'])})
        assert source['content_sha256']==original_hash
        for i,line in enumerate(adopted['costs']):
            cost=(await api.get('/api/cost-entries/'+line['cost_entry_id'])).json()
            assert cost['status']=='confirmed' and cost['source']=='PDF_IMPORT'
            assert cost['gross_amount']==allocation['lines'][i]['gross_amount'] and cost['vat_amount'] is None
            assert cost['tenant_apportionable']==('TEILWEISE_PRUEFEN' if i==0 else 'NICHT_UMLAGEFAEHIG')
            assert cost['apportionable_tenant_amount']==(('-10.00' if credit else '10.00') if i==0 else '0.00')
            from datetime import date
            from decimal import Decimal
            from app.engine.types import CostPositionInput
            from app.engine.distribution import effective_total,is_billable_to_tenant
            position=CostPositionInput(cost_entry_id=uuid.UUID(cost['id']),category_id=None,category_code='TEST',label='Reviewed adoption',
              gross_amount=Decimal(cost['gross_amount']),service_period_from=date.fromisoformat(cost['service_period_from']),
              service_period_to=date.fromisoformat(cost['service_period_to']),tenant_distribution_key=cost['tenant_distribution_key'],
              tenant_apportionable=cost['tenant_apportionable'],apportionable_tenant_amount=Decimal(cost['apportionable_tenant_amount']))
            assert is_billable_to_tenant(position) is (i==0)
            if i==0:assert effective_total(position)==Decimal('-10.00' if credit else '10.00')
        assert await read_value('SELECT count(*) FROM cost_entry WHERE organization_id=:org',ids)==2
        assert await read_value('SELECT count(*) FROM invoice_document WHERE organization_id=:org',ids)==1


async def ready(api,ids,**fields):
    child,allocation,review=await reviewed(api,ids,**fields)
    preview=(await api.get('/api/allocation-treatments/'+review['id']+'/adoption-preview')).json()
    return child,allocation,review,preview,'/api/logical-documents/'+child['id']+'/allocation-adoptions'


async def assert_no_adoption(ids):
    for table in ('cost_entry','invoice_document','allocation_set','document_cost_link'):
        assert await read_value('SELECT count(*) FROM '+table+' WHERE organization_id=:org',ids)==0,table


async def test_adoption_rejects_unsupported_retention_before_effects(migrator_session,scope_seed,store):
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids)
    async with client(ids) as api:
        _,_,review,preview,url=await ready(api,ids,invoice_date='9992-01-01')
        assert not preview['can_adopt'] and preview['preview_digest'] is None
        result=await api.post(url,**command(review,{'preview_digest':'a'*64}))
        assert result.status_code==422,result.text
    await assert_no_adoption(ids)
    assert await read_value('SELECT count(*) FROM allocation_integrity_attestation WHERE organization_id=:org',ids)==0


async def test_adoption_requires_both_acknowledgments_and_unchanged_preview(migrator_session,scope_seed,store):
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids)
    async with client(ids) as api:
        _,allocation,review,preview,url=await ready(api,ids)
        for field in ('costs_confirmed','whole_original_disclosure_reviewed'):
            body=command(review,preview);body['json'][field]=False
            assert (await api.post(url,**body)).status_code==422
        stale=command(review,preview);stale['json']['preview_digest']='a'*64
        assert (await api.post(url,**stale)).status_code==409
        await mutate(text("UPDATE cost_category SET label=label||' Changed' WHERE id=:category"),{'category':uuid.UUID(allocation['lines'][0]['cost_category_id'])})
        try:
            assert (await api.post(url,**command(review,preview))).status_code==409
            assert not (await api.get('/api/allocation-treatments/'+review['id']+'/adoption-preview')).json()['can_adopt']
        finally:
            await mutate(text("UPDATE cost_category SET label=left(label,length(label)-8) WHERE id=:category"),{'category':uuid.UUID(allocation['lines'][0]['cost_category_id'])})
    await assert_no_adoption(ids)


async def test_adoption_integrity_failure_then_historical_retry_without_new_blob_read(migrator_session,scope_seed,store,monkeypatch):
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids)
    import app.services.allocation_adoption_service as service
    real_verify=service.verify_document_integrity_and_size
    async with client(ids) as api:
        _,_,review,preview,url=await ready(api,ids);body=command(review,preview)
        monkeypatch.setattr(service,'verify_document_integrity_and_size',lambda *args:False)
        assert (await api.post(url,**body)).status_code==409
        await assert_no_adoption(ids)
        monkeypatch.setattr(service,'verify_document_integrity_and_size',real_verify)
        first=await api.post(url,**body);assert first.status_code==201,first.text
        monkeypatch.setattr(service,'verify_document_integrity_and_size',lambda *args:pytest.fail('Historical receipt must not read changed storage'))
        assert (await api.post(url,**body)).json()==first.json()
        body['json']['reason']='Different request under the same key'
        assert (await api.post(url,**body)).status_code==409


@pytest.mark.parametrize('table,condition',[('cost_entry',"NEW.gross_amount=60"),('invoice_document','true'),('audit_event',"NEW.action='document.allocation_released'")])
async def test_adoption_atomic_failure_and_exact_concurrent_retry(migrator_session,scope_seed,store,table,condition):
    import asyncio
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids)
    async with client(ids) as api:
        _,_,review,preview,url=await ready(api,ids);body=command(review,preview)
        await mutate(text("CREATE FUNCTION synthetic_adoption_failure() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF "+condition+" THEN RAISE EXCEPTION 'Synthetic adoption rollback' USING ERRCODE='23514';END IF;RETURN NEW;END $$"),{})
        await mutate(text('CREATE TRIGGER synthetic_adoption_failure BEFORE INSERT ON '+table+' FOR EACH ROW EXECUTE FUNCTION synthetic_adoption_failure()'),{})
        try:
            result=await api.post(url,**body);assert result.status_code==422,result.text
            await assert_no_adoption(ids)
            assert await read_value("SELECT count(*) FROM audit_event WHERE organization_id=:org AND action IN ('document.allocation_released','cost_entry.allocation_confirmed')",ids)==0
        finally:
            await mutate(text('DROP TRIGGER synthetic_adoption_failure ON '+table),{})
            await mutate(text('DROP FUNCTION synthetic_adoption_failure()'),{})
        responses=await asyncio.gather(api.post(url,**body),api.post(url,**body))
        assert [r.status_code for r in responses]==[201,201],[r.text for r in responses]
        assert responses[0].json()==responses[1].json()
        assert (await api.post(url,**command(review,preview))).status_code==409


async def test_adoption_scope_recipient_reads_all_member_sources_but_not_private_intake(migrator_session,scope_seed,store,client_b_admin):
    from app.core.db import get_tenant_session
    from app.repositories.invoice_document_repo import InvoiceDocumentRepository
    from app.repositories.dsgvo_repo import DsgvoRepository
    from sqlalchemy.exc import DBAPIError
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids);await confirm_grant(ids,'actor')
    async with client(ids) as api,client(ids,'actor') as recipient:
        child,allocation,review,preview,url=await ready(api,ids)
        result=await api.post(url,**command(review,preview));assert result.status_code==201,result.text
        adopted=result.json();source=adopted['source_document_id']
        assert (await recipient.get('/api/allocation-sets/'+adopted['id'])).json()==adopted
        assert (await recipient.get('/api/invoice-documents/'+source+'/content')).status_code==200
        assert (await recipient.get('/api/document-refs/'+allocation['document_ref_id'])).status_code==404
        assert (await recipient.get('/api/allocation-treatments/'+review['id'])).status_code==404
        for cost in adopted['costs']:
            key=uuid.UUID(cost['cost_entry_id'])
            assert (await recipient.get('/api/cost-entries/'+str(key))).status_code==200
            async with get_tenant_session(ids['org']) as db:
                await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(ids['actor'])})
                repo=InvoiceDocumentRepository(db,ids['org'])
                assert str((await repo.get_active_for_cost_entry(key)).id)==source
                assert [str(d.id) for d in await repo.list_by_cost_entry(key)]==[source]
                assert str(await DsgvoRepository(db,ids['org']).max_retention_for_cost_entry(key))=='2034-12-31'
        assert (await client_b_admin.get('/api/allocation-sets/'+adopted['id'])).status_code==404
        for sql in ('SELECT duplicate_snapshot FROM allocation_set WHERE organization_id=:org','SELECT allocation_line_id FROM document_cost_link WHERE organization_id=:org','SELECT id FROM allocation_integrity_attestation WHERE organization_id=:org'):
            with pytest.raises(DBAPIError,match='permission denied'):
                await read_value(sql,ids,actor_key='clerk')
        for cost in adopted['costs']:
            correction=await api.post('/api/cost-entries/'+cost['cost_entry_id']+'/correction',json={'document_number':'Individual correction'})
            assert correction.status_code==409 and 'complete allocation set' in correction.text
            with pytest.raises(DBAPIError,match='atomic set correction'):
                await mutate(text("UPDATE cost_entry SET document_number='Altered' WHERE id=:cost"),{'cost':uuid.UUID(cost['cost_entry_id'])})
        with pytest.raises(DBAPIError,match='atomic set correction'):
            await mutate(text("UPDATE invoice_document SET status='replaced' WHERE id=:source"),{'source':uuid.UUID(source)})


async def test_adoption_requires_source_export_and_current_confirmation_even_for_retry(migrator_session,scope_seed,store):
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids)
    async with client(ids) as api:
        _,_,review,preview,url=await ready(api,ids,scoped=True);body=command(review,preview)
        assert preview['can_adopt']
        for capabilities in ('read,prepare,confirm','read,prepare,export'):
            await mutate(text("UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:scope AND user_id=:clerk AND revoked_at IS NULL"),ids)
            await mutate(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES(:org,:scope,:clerk,string_to_array(:caps,','),:actor,'Revoked specific adoption capability')"),{**ids,'caps':capabilities})
            assert not (await api.get('/api/allocation-treatments/'+review['id']+'/adoption-preview')).json()['can_adopt']
            assert (await api.post(url,**body)).status_code==404
        await assert_no_adoption(ids)
        await confirm_grant(ids)
        first=await api.post(url,**body);assert first.status_code==201,first.text
        await mutate(text("UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:scope AND user_id=:clerk AND revoked_at IS NULL"),ids)
        assert (await api.post(url,**body)).status_code==404


async def manual_cost(ids):
    await mutate(text("""INSERT INTO cost_entry(organization_id,property_id,cost_category_id,cost_category_plan_version,
      invoice_date,service_period_from,service_period_to,gross_amount,document_number,tenant_apportionable,status,source,confirmed_by,confirmed_at)
      SELECT :org,:property,id,1,'2026-10-01','2026-09-01','2026-09-30',119,'R-2026-42','UMLAGEFAEHIG','confirmed','MANUAL',:actor,clock_timestamp()
      FROM cost_category WHERE code='GRUNDSTEUER' AND plan_version=1"""),ids)


@pytest.mark.parametrize('legacy_first',[True,False])
async def test_adoption_duplicate_manual_without_attachment_in_both_orders(migrator_session,scope_seed,store,legacy_first):
    from sqlalchemy.exc import DBAPIError
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids)
    async with client(ids) as api:
        _,_,review,preview,url=await ready(api,ids)
        if legacy_first:
            await manual_cost(ids)
            updated=(await api.get('/api/allocation-treatments/'+review['id']+'/adoption-preview')).json()
            assert not updated['can_adopt'] and updated['preview_digest']!=preview['preview_digest']
            assert (await api.post(url,**command(review,preview))).status_code==409
            assert (await api.post(url,**command(review,updated))).status_code==409
            assert await read_value('SELECT count(*) FROM allocation_set WHERE organization_id=:org',ids)==0
        else:
            result=await api.post(url,**command(review,preview));assert result.status_code==201,result.text
            with pytest.raises(DBAPIError,match='Source conflict'):
                await manual_cost(ids)
        assert await read_value('SELECT count(*) FROM cost_entry WHERE organization_id=:org',ids)==(1 if legacy_first else 2)


@pytest.mark.parametrize('same_bytes',[True,False])
async def test_adoption_exact_pair_resolution_and_retained_duplicate_claim(migrator_session,scope_seed,store,monkeypatch,same_bytes):
    import asyncio
    from tests.test_invoice_extraction_worker import pdf_bytes
    from tests.test_duplicate_comparisons import command as compare_command,comparisons
    from tests.test_invoice_reviews import command as field_command
    if same_bytes:
        data=pdf_bytes();monkeypatch.setattr('tests.test_intake_extraction_worker.pdf_bytes',lambda:data)
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids)
    async with client(ids) as api:
        ca,aa,ra,pa,ua=await ready(api,ids)
        cb,ab,rb,pb,ub=await ready(api,ids)
        assert not pb['can_adopt']
        fa=(await api.get('/api/invoice-reviews/'+aa['invoice_review_id'])).json()
        fb=(await api.get('/api/invoice-reviews/'+ab['invoice_review_id'])).json()
        compared=await api.post(comparisons(fa),**compare_command(fb,verdict='same_document' if same_bytes else 'distinct_documents'))
        assert compared.status_code==201,compared.text
        pa=(await api.get('/api/allocation-treatments/'+ra['id']+'/adoption-preview')).json()
        pb=(await api.get('/api/allocation-treatments/'+rb['id']+'/adoption-preview')).json()
        assert pa['can_adopt'] and pb['can_adopt']
        bodies=[command(ra,pa),command(rb,pb)]
        results=await asyncio.gather(api.post(ua,**bodies[0]),api.post(ub,**bodies[1]))
        assert sorted(r.status_code for r in results)==[201,409],[r.text for r in results]
        winner=0 if results[0].status_code==201 else 1;loser=1-winner
        c,a,r,u=[(ca,aa,ra,ua),(cb,ab,rb,ub)][loser]
        new_preview=(await api.get('/api/allocation-treatments/'+r['id']+'/adoption-preview')).json()
        assert new_preview['can_adopt'] is (not same_bytes)
        if not same_bytes:
            result=await api.post(u,**command(r,new_preview));assert result.status_code==201,result.text
        assert await read_value('SELECT count(*) FROM cost_entry WHERE organization_id=:org',ids)==(2 if same_bytes else 4)
        # Changing the adopted source's current review cannot erase the consumed original identity.
        wc,wa,wr,wu=[(ca,aa,ra,ua),(cb,ab,rb,ub)][winner]
        wf=[fa,fb][winner]
        changed=await api.post('/api/logical-documents/'+wc['id']+'/invoice-reviews',**field_command({'id':wf['assignment_proposal_id']},previous=wf['id'],document_number='Different latest identity'))
        assert changed.status_code==201,changed.text
        assert (await api.post(wu,**bodies[winner])).json()==results[winner].json()
        assert (await api.get(wu)).json()['item']==results[winner].json()


async def test_adoption_new_unprocessed_same_byte_receipt_invalidates_preview(migrator_session,scope_seed,store,monkeypatch):
    from tests.test_intake_batches import receive
    from tests.test_invoice_extraction_worker import pdf_bytes
    data=pdf_bytes();monkeypatch.setattr('tests.test_intake_extraction_worker.pdf_bytes',lambda:data)
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids)
    async with client(ids) as api:
        _,_,review,preview,url=await ready(api,ids)
        await receive(api,data)
        assert (await api.post(url,**command(review,preview))).status_code==409
        current=(await api.get('/api/allocation-treatments/'+review['id']+'/adoption-preview')).json()
        assert not current['can_adopt']
    await assert_no_adoption(ids)


async def test_adoption_runtime_roles_and_transaction_bound_integrity_receipt(migrator_session,scope_seed,store):
    from app.core.db import get_session_factory,get_worker_session_factory,get_review_session_factory,set_tenant_context
    from sqlalchemy.exc import DBAPIError
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids)
    async with client(ids) as api:
        child,_,review,preview,_=await ready(api,ids)
    params={**ids,'logical':uuid.UUID(child['id']),'target':uuid.UUID(review['id']),'request':uuid.uuid4(),'preview':preview['preview_digest'],'proof':uuid.uuid4()}
    arguments=":org,:logical,:target,:target,:request,:preview,'Explicit source and cost review',true,true"
    async def principal(db):
        await set_tenant_context(db,ids['org'])
        await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(ids['clerk'])})
    for factory in (get_worker_session_factory(),get_review_session_factory()):
        with pytest.raises(DBAPIError,match='Interactive runtime required|permission denied'):
            async with factory() as db,db.begin():
                await principal(db)
                await db.execute(text('SELECT prepare_allocation_adoption('+arguments+')'),params)
    async with get_session_factory()() as db,db.begin():
        await principal(db)
        prepared=await db.scalar(text('SELECT prepare_allocation_adoption('+arguments+')'),params)
        with pytest.raises(DBAPIError,match='integrity attestation'):
            async with db.begin_nested():await db.execute(text('SELECT adopt_allocation('+arguments+',:proof)'),params)
        proof_params={**ids,'original':uuid.UUID(prepared['original_id']),'request':params['request'],'digest':prepared['request_digest'],
          'pid':prepared['backend_pid']+1,'transaction':prepared['transaction_id'],'hash':prepared['content_sha256'],'size':prepared['content_byte_size']}
        sql='SELECT attest_allocation_integrity(:org,:original,:request,:digest,:pid,:transaction,:hash,:size)'
        with pytest.raises(DBAPIError,match='permission denied'):
            async with db.begin_nested():await db.execute(text(sql),proof_params)
        async with get_review_session_factory()() as reviewer,reviewer.begin():
            await principal(reviewer)
            proof=await reviewer.scalar(text(sql),proof_params)
        with pytest.raises(DBAPIError,match='integrity attestation'):
            async with db.begin_nested():await db.execute(text('SELECT adopt_allocation('+arguments+',:proof)'),{**params,'proof':proof})
    await assert_no_adoption(ids)


async def test_adoption_retained_evidence_blocks_downgrade_and_empty_roundtrip_restores_functions(migrator_session,scope_seed,store):
    import importlib
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    from tests.conftest import MIGRATOR_SYNC_URL
    ids=await fixture(migrator_session,scope_seed);await confirm_grant(ids)
    async with client(ids) as api:
        _,_,review,preview,url=await ready(api,ids)
        result=await api.post(url,**command(review,preview));assert result.status_code==201,result.text
    migration=importlib.import_module('migrations.versions.0109_allocation_adoptions');engine=create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
            with pytest.raises(DBAPIError,match='Retain adopted'),db.begin_nested():migration.downgrade()
            for table in ('allocation_set','document_cost_link','allocation_integrity_attestation'):
                with pytest.raises(DBAPIError,match='immutable|Immutable'),db.begin_nested():
                    db.execute(text('DELETE FROM '+table+' WHERE organization_id=:org'),ids)
            point=db.begin_nested()
            try:
                db.execute(text('TRUNCATE allocation_integrity_attestation CASCADE'))
                db.execute(text('TRUNCATE exception_completion CASCADE'))
                db.execute(text('TRUNCATE financial_computation_attestation CASCADE'))
                importlib.import_module('migrations.versions.0115_financial_computation_evidence').downgrade()
                importlib.import_module('migrations.versions.0114_allocation_corrections').downgrade()
                importlib.import_module('migrations.versions.0113_allocation_correction_preview').downgrade()
                importlib.import_module('migrations.versions.0112_context_mappings').downgrade()
                importlib.import_module('migrations.versions.0111_context_candidate_source').downgrade()
                importlib.import_module('migrations.versions.0110_exception_completions').downgrade()
                functions=['access_coverage_manifest(uuid,jsonb)','guard_invoice_custody()','privacy_retention_facts(uuid,uuid)']
                before={f:db.scalar(text('SELECT pg_get_functiondef(CAST(:signature AS regprocedure))'),{'signature':f}) for f in functions}
                migration.downgrade();migration.upgrade()
                after={f:db.scalar(text('SELECT pg_get_functiondef(CAST(:signature AS regprocedure))'),{'signature':f}) for f in functions}
                assert before==after
                # Original released legacy links remain provable without guessing an intake history.
                assert db.scalar(text("SELECT count(*) FROM allocation_set WHERE kind='proven_legacy' AND organization_id=:org"),ids)==1
                assert db.scalar(text('SELECT count(*) FROM document_cost_link WHERE organization_id=:org'),ids)==1
            finally:point.rollback()
    finally:engine.dispose()
