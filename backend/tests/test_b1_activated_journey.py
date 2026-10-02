"""B1 qualification through real activation, never a substituted readiness gate."""
import uuid
from datetime import datetime,timezone
import pytest
from sqlalchemy import text
from app.core.db import get_worker_tenant_session
from app.repositories.declaration_import_repo import DeclarationImportRepository
from tests.test_financial_scope_boundary import worker_tenant,scope_seed  # noqa: F401
from tests.test_declaration_import_api import isolated_document_store,enable_declaration_imports  # noqa: F401
from tests.test_invoice_source_custody import store,upload  # noqa: F401
from tests.test_scoped_account_statements import client,mutate
from tests.test_scope_payment_reconciliation import read_value
from tests.test_source_security_lifecycle import accepted,status,transition,transition_body
from tests.test_declaration_job_principal import new_draft

pytestmark=pytest.mark.asyncio


def key():
    return {'Idempotency-Key':str(uuid.uuid4())}


async def create(api,path,body):
    response=await api.post(path,json=body,headers=key())
    assert response.status_code==201,(path,response.text)
    return response.json()


async def activate(api):
    decision=await accepted(api)
    response=await transition(api,transition_body(await status(api),'ENFORCED',decision))
    assert response.status_code==201,response.text


async def page_ids(api,path,query):
    rows=[];cursor=None
    while True:
        response=await api.get(path,params={**query,'limit':1,**({'cursor':cursor} if cursor else {})})
        assert response.status_code==200,response.text
        data=response.json();rows.extend(row['id'] for row in data['items']);cursor=data['next_cursor']
        if not cursor: return rows


async def test_real_activated_portfolio_scopes_and_revoked_effects(migrator_session,scope_seed,client_b_admin,store,isolated_document_store,enable_declaration_imports,monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings,'invoice_extraction_enabled',False)
    await migrator_session.commit()
    ids={'org':scope_seed['org_a_id'],'actor':scope_seed['user_a_admin_id'],'clerk':scope_seed['user_a_clerk_id']}
    async with client(ids,'actor') as admin,client(ids) as clerk:
        await activate(admin)
        # First enforced mandate/property/scope does not bestow financial rights.
        properties=[];people=[];units=[]
        for index in range(3):
            grants=[{'user_id':str(ids['actor']),'capabilities':['read','prepare','approve','export'],'reason':'Responsible administrator'}]
            if index<2: grants.append({'user_id':str(ids['clerk']),'capabilities':['read','prepare','export'],'reason':'Explicit shared building'})
            prop=await create(admin,'/api/properties',{'name':f'Qualification property {index}','management_type':'MISCH',
                'data_audience':{'label':'Qualification building data','reason':'Reviewed onboarding','grants':grants}})
            properties.append(prop['id'])
            unit=await create(admin,'/api/units',{'property_id':prop['id'],'number':f'Qualification unit {index}','usage_type':'WOHNUNG'})
            units.append(unit['id'])
            person=await create(admin,'/api/persons',{'last_name':f'Qualification contact {index}'})
            people.append(person['id'])
            if index<2:
                domain=await create(admin,'/api/person-data-domains',{'person_id':person['id'],'reason_code':'INITIAL_SETUP'})
                for actor in ('actor','clerk'):
                    await create(admin,f"/api/person-data-domains/{domain['id']}/grants",{'user_id':str(ids[actor]),'capabilities':['read','prepare','export'],'reason_code':'TEAM_ACCESS'})
        owner=await create(admin,'/api/owner-profiles',{'person_id':people[0]})
        scopes=[];banks=[];clerk_grant=None
        for kind in ('WEG','MIET'):
            mandate=await create(admin,'/api/mandates',{'mandate_type':kind,'label':'Qualification '+kind,'start_date':'2025-01-01'})
            scope=await create(admin,'/api/accounting-scopes',{'property_id':properties[0],'management_mandate_id':mandate['id'],
                'scope_type':kind,'label':'Qualification '+kind,'valid_from':'2025-01-01','whole_property':True,'units':[]})
            scopes.append(scope['id'])
            bank_body={'property_id':properties[0],'account_type':'LAUFEND','label':kind+' account','accounting_scope_id':scope['id']}
            assert (await admin.post('/api/bank-accounts',json=bank_body)).status_code==404
            await create(admin,f"/api/accounting-scopes/{scope['id']}/grants",{'user_id':str(ids['actor']),
                'capabilities':['read','prepare','confirm','approve','send','export'],'reason':'Explicit financial authority'})
            if kind=='WEG':
                clerk_grant=await create(admin,f"/api/accounting-scopes/{scope['id']}/grants",{'user_id':str(ids['clerk']),
                    'capabilities':['read','prepare','confirm','export'],'reason':'One reviewed sphere'})
            banks.append((await create(admin,'/api/bank-accounts',bank_body))['id'])
        # Requalify the populated graph under the real PAUSED→ENFORCED path.
        assert (await transition(admin,transition_body(await status(admin),'PAUSED'))).status_code==201
        await activate(admin)
        for path,query,visible,hidden in [
            ('properties',{'q':'Qualification'},properties[:2],properties[2]),
            ('persons',{'q':'Qualification'},people[:2],people[2]),
            ('units',{'q':'Qualification'},units[:2],units[2]),
        ]:
            assert set(await page_ids(clerk,f'/api/{path}/page',query))==set(visible)
            assert await page_ids(client_b_admin,f'/api/{path}/page',query)==[]
            assert (await clerk.get(f'/api/{path}/{visible[0]}')).status_code==200
            assert (await clerk.get(f'/api/{path}/{hidden}')).status_code==404
            assert (await client_b_admin.get(f'/api/{path}/{visible[0]}')).status_code==404
        assert await page_ids(clerk,'/api/accounting-scopes/page',{'property_id':properties[0],'capability':'prepare'})==[scopes[0]]
        assert await page_ids(clerk,'/api/accounting-scopes/page',{'property_id':properties[0],'capability':'approve'})==[]
        assert (await clerk.get(f'/api/accounting-scopes/{scopes[1]}')).status_code==404
        assert (await clerk.get(f'/api/bank-accounts/{banks[1]}')).status_code==404
        forged={'property_id':properties[0],'account_type':'LAUFEND','label':'Forged sphere','accounting_scope_id':scopes[1]}
        assert (await clerk.post('/api/bank-accounts',json=forged)).status_code==404
        payment={'bank_account_id':banks[0],'direction':'IN','amount':'100.00','value_date':'2025-06-01',
            'payment_type':'HAUSGELD_VORSCHUSS','owner_profile_id':owner['id']}
        assert (await clerk.post('/api/payments',json={**payment,'bank_account_id':banks[1]})).status_code==404
        created=await create(clerk,'/api/payments',payment)
        assert (await clerk.patch(f"/api/payments/{created['id']}",json={'bank_account_id':banks[1]})).status_code in (404,409)
        # The canonical binding is still the original exact account and sphere.
        assert await read_value('SELECT scope_id FROM accounting_scope_binding WHERE payment_id=:payment',
            {**ids,'payment':uuid.UUID(created['id'])})==uuid.UUID(scopes[0])
        assert (await admin.post(f"/api/accounting-scopes/{scopes[0]}/grants/{clerk_grant['id']}/revoke")).status_code==204
        assert await page_ids(clerk,'/api/accounting-scopes/page',{'property_id':properties[0],'capability':'prepare'})==[]
        assert (await clerk.get(f'/api/bank-accounts/{banks[0]}')).status_code==404
        assert (await clerk.get(f"/api/payments/{created['id']}")).status_code==404
        assert (await clerk.get(f'/api/properties/{properties[0]}')).status_code==200

        # Known intake custody survives activation without granting another actor access.
        document=await upload(clerk)
        assert (await clerk.get(f'/api/invoice-documents/{document}/content')).content==b'%PDF-1.4 synthetic private invoice'
        assert (await admin.get(f'/api/invoice-documents/{document}/content')).status_code==404
        assert (await client_b_admin.get(f'/api/invoice-documents/{document}/content')).status_code==404
        creator_ids={**ids,'actor':ids['clerk']}
        review_job=uuid.UUID(await new_draft(creator_ids))
        source=await read_value('SELECT document_id FROM declaration_import_document WHERE import_id=:job',{**ids,'job':review_job})
        candidates=[uuid.uuid4(),uuid.uuid4()]
        for index,candidate in enumerate(candidates):
            values={**ids,'job':review_job,'candidate':candidate,'stable':f'qualification-{index}','subject':f'unit:{index}','source':source}
            await mutate(text("""INSERT INTO declaration_candidate(id,organization_id,import_id,stable_key,candidate_type,subject_key,field_key,operation,
              proposed_value_json,confidence,risk_class,requires_individual_review) VALUES(:candidate,:org,:job,:stable,'UNIT_FIELD',:subject,'area','CREATE',
              jsonb_build_object('kind','DECIMAL','value','50','unit','m2'),.99,'LOW',false)"""),values)
            await mutate(text("INSERT INTO declaration_candidate_evidence(organization_id,candidate_id,document_id,pdf_page,quote) VALUES(:org,:candidate,:source,1,'Synthetic qualification')"),values)
        await mutate(text("UPDATE declaration_import SET status='NEEDS_REVIEW' WHERE id=:job"),{**ids,'job':review_job})
        body={'draft_revision':1,'decision':'ACCEPTED','candidate_ids':list(map(str,candidates))};headers=key()
        bulk=f'/api/declaration-imports/{review_job}/candidates/bulk-review'
        result=await clerk.post(bulk,json=body,headers=headers)
        assert result.status_code==200,result.text
        assert (await clerk.post(bulk,json=body,headers=headers)).json()==result.json()
        queued=uuid.UUID(await new_draft(creator_ids))
        response=await clerk.post(f'/api/declaration-imports/{queued}/start',headers=key())
        assert response.status_code==200,response.text
        await mutate(text("UPDATE membership SET role='READ_ONLY' WHERE organization_id=:org AND user_id=:clerk"),ids)
        assert (await clerk.post(bulk,json=body,headers=headers)).status_code==403
        async with get_worker_tenant_session(ids['org']) as worker:
            assert await DeclarationImportRepository(worker,ids['org']).claim_next('qualification-worker',datetime.now(timezone.utc),120) is None
        assert await read_value('SELECT status FROM declaration_import WHERE id=:job',{**ids,'job':queued})=='CANCELLED'
        assert await read_value('SELECT error_code FROM declaration_import WHERE id=:job',{**ids,'job':queued})=='JOB_PERMISSION_CHANGED'
        assert await read_value('SELECT draft_revision FROM declaration_import WHERE id=:job',{**ids,'job':review_job})==2
        await mutate(text('UPDATE "user" SET is_active=false WHERE id=:clerk'),ids)
        assert (await clerk.get(f'/api/invoice-documents/{document}/content')).status_code==401
