"""Only current source evidence and authorized master data enter suggestions."""
import io
import uuid
import pytest
from sqlalchemy import text
from tests.test_financial_scope_boundary import worker_tenant,scope_seed  # noqa: F401
from tests.test_invoice_source_custody import store,fixture
from tests.test_scoped_account_statements import client,mutate
from tests.test_scope_payment_reconciliation import read_value
from tests.test_assignment_proposals import prepared
from tests.test_allocation_adoptions import confirm_grant

pytestmark=pytest.mark.asyncio


def document(lines):
    from reportlab.pdfgen import canvas
    out=io.BytesIO();pdf=canvas.Canvas(out)
    for index,line in enumerate(['Synthetic supplier','Rechnungsdatum: 01.10.2026','Gesamtbetrag: 119,00 EUR','Leistungszeitraum: 01.09.2026 - 30.09.2026',*lines]):
        pdf.drawString(40,800-index*20,line)
    pdf.showPage();pdf.save();return out.getvalue()


def endpoint(child,partition,extraction):
    return '/api/logical-documents/'+child['id']+'/assignment-candidates',{'partition_revision_id':partition['id'],'extraction_revision_id':extraction['id']}


async def test_candidates_exact_pages_authorized_scopes_multiple_spheres_and_no_effect(migrator_session,scope_seed,store,monkeypatch,client_b_admin):
    ids=await fixture(migrator_session,scope_seed)
    monkeypatch.setattr('tests.test_intake_extraction_worker.pdf_bytes',lambda:document(['Objekt-ID: '+str(ids['property']),'Zählernummer: M-123']))
    await mutate(text("INSERT INTO meter(organization_id,property_id,meter_number,meter_type) VALUES(:org,:property,'M-123','STROM')"),ids)
    async with client(ids) as api,client(ids,'actor') as other:
        ref,child,partition,extraction=await prepared(api,ids);url,params=endpoint(child,partition,extraction)
        result=await api.get(url,params=params);assert result.status_code==200,result.text
        value=result.json();assert value['status']=='manual_review' and len(value['items'])==1
        row=value['items'][0];assert row['scope_id']==str(ids['scope']) and row['requires_manual_review']
        assert [e['kind'] for e in row['evidence']]==['property_id','meter_number']
        assert all(e['page_number']==1 and e['method']=='text_layer' for e in row['evidence'])
        assert row['meter_period_unverified'] and row['exclusions']==[]
        for denied in (other,client_b_admin):assert (await denied.get(url,params=params)).status_code==404
        await confirm_grant({**ids,'scope':ids['other_scope']})
        both=(await api.get(url,params=params)).json();assert len(both['items'])==2 and both['ambiguous_scopes'] and not both['conflicting_properties']
        assert all(row['requires_manual_review'] for row in both['items'])
        assert (await api.get(url,params={**params,'partition_revision_id':str(uuid.uuid4())})).status_code==409
        assert (await api.get(url,params={**params,'extraction_revision_id':str(uuid.uuid4())})).status_code==409
    for table in ('assignment_proposal','cost_entry','context_decision'):
        assert await read_value('SELECT count(*) FROM '+table+' WHERE organization_id=:org',ids)==0


async def test_candidates_hidden_matching_property_and_unmatched_conflicting_assertion(migrator_session,scope_seed,store,monkeypatch):
    ids=await fixture(migrator_session,scope_seed)
    await mutate(text("UPDATE property SET street='Hauptstraße 1',zip_code='10115',city='Berlin' WHERE organization_id=:org AND id=:other_property"),ids)
    await mutate(text("INSERT INTO accounting_scope(organization_id,property_id,scope_type,label,valid_from,whole_property,origin,created_by) VALUES(:org,:other_property,'WEG','Private hidden target','2025-01-01',true,'REVIEWED',:actor)"),ids)
    monkeypatch.setattr('tests.test_intake_extraction_worker.pdf_bytes',lambda:document(['Objekt-ID: '+str(ids['property']),'Leistungsort: Hauptstraße 1, 10115 Berlin']))
    async with client(ids) as api:
        _,child,partition,extraction=await prepared(api,ids);url,params=endpoint(child,partition,extraction)
        result=await api.get(url,params=params);assert result.status_code==200,result.text
        assert len(result.json()['items'])==1 and len(result.json()['unmatched_evidence'])==1
        assert 'Private hidden target' not in result.text and str(ids['other_property']) not in result.text
        hidden=await read_value('SELECT id FROM accounting_scope WHERE organization_id=:org AND property_id=:other_property',ids)
        await confirm_grant({**ids,'scope':hidden})
        result=await api.get(url,params=params);assert result.status_code==200,result.text
        assert result.json()['conflicting_properties'] and len(result.json()['items'])==2
        assert result.json()['unmatched_evidence']==[]


async def test_candidates_supplier_address_is_not_property_context_and_missing_period_excluded(migrator_session,scope_seed,store,monkeypatch):
    ids=await fixture(migrator_session,scope_seed)
    await mutate(text("UPDATE property SET street='Hauptstraße 1',zip_code='10115',city='Berlin' WHERE organization_id=:org AND id=:property"),ids)
    monkeypatch.setattr('tests.test_intake_extraction_worker.pdf_bytes',lambda:document(['Hauptstraße 1, 10115 Berlin']))
    async with client(ids) as api:
        _,child,p,e=await prepared(api,ids);url,params=endpoint(child,p,e)
        value=(await api.get(url,params=params)).json();assert value['status']=='no_labelled_evidence' and value['items']==[]


async def test_candidates_caps_after_authority_filter_and_meter_ambiguity(migrator_session,scope_seed,store,monkeypatch):
    ids=await fixture(migrator_session,scope_seed)
    monkeypatch.setattr('tests.test_intake_extraction_worker.pdf_bytes',lambda:document(['Objekt-ID: '+str(ids['property']),'Zählernummer: M-123']))
    # Inaccessible scopes must not contribute even to cap/overflow status.
    await mutate(text("INSERT INTO accounting_scope(organization_id,property_id,scope_type,label,valid_from,whole_property,origin,created_by) SELECT :org,:other_property,'WEG','Hidden '||n,'2025-01-01',true,'REVIEWED',:actor FROM generate_series(1,201) n"),ids)
    async with client(ids) as api:
        _,child,p,e=await prepared(api,ids);url,params=endpoint(child,p,e)
        value=(await api.get(url,params=params)).json();assert value['status']=='manual_review' and len(value['items'])==1
        await mutate(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) SELECT :org,id,:clerk,ARRAY['read','prepare'],:actor,'Visible cap fixture' FROM accounting_scope WHERE organization_id=:org AND property_id=:other_property"),ids)
        value=(await api.get(url,params=params)).json();assert value['status']=='comparison_limit' and value['items']==[]
        await mutate(text('UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE organization_id=:org AND scope_id IN(SELECT id FROM accounting_scope WHERE organization_id=:org AND property_id=:other_property)'),ids)
        await mutate(text("INSERT INTO meter(organization_id,property_id,meter_number,meter_type) SELECT :org,:property,'M-'||n,'STROM' FROM generate_series(1,501) n"),ids)
        value=(await api.get(url,params=params)).json();assert value['status']=='comparison_limit' and value['items']==[]


async def test_candidates_latest_failed_extraction_never_falls_back_and_private_attempt_stays_private(migrator_session,scope_seed,store,monkeypatch):
    from sqlalchemy.exc import DBAPIError
    ids=await fixture(migrator_session,scope_seed)
    monkeypatch.setattr('tests.test_intake_extraction_worker.pdf_bytes',lambda:document(['Objekt-ID: '+str(ids['property'])]))
    async with client(ids) as api:
        _,child,p,e=await prepared(api,ids);url,params=endpoint(child,p,e)
        assert (await api.get(url,params=params)).json()['items']
        with pytest.raises(DBAPIError,match='permission denied'):
            await read_value('SELECT result FROM intake_extraction_attempt WHERE organization_id=:org',ids,actor_key='clerk')
        # A privileged fixture adds a new terminal failed extraction. Immutable
        # prior evidence stays intact; runtime must inspect the latest head first.
        values={**ids,'old':uuid.UUID(e['id']),'attempt':uuid.uuid4(),'revision':uuid.uuid4()}
        await mutate(text("""INSERT INTO intake_extraction_attempt(id,organization_id,job_id,attempt_number,fence,started_at,finished_at,result)
          SELECT :attempt,a.organization_id,a.job_id,2,gen_random_uuid(),clock_timestamp(),clock_timestamp(),
            a.result||jsonb_build_object('status','failed','error_code','processor_failed','payload',NULL,'pages','[]'::jsonb,'evidence','{}'::jsonb)
          FROM intake_extraction_attempt a JOIN extraction_revision e ON e.organization_id=a.organization_id AND e.intake_attempt_id=a.id
          WHERE e.organization_id=:org AND e.id=:old"""),values)
        await mutate(text("""INSERT INTO extraction_revision SELECT (jsonb_populate_record(NULL::extraction_revision,to_jsonb(e)||
          jsonb_build_object('id',CAST(:revision AS uuid),'intake_attempt_id',CAST(:attempt AS uuid),'created_at',clock_timestamp(),'status','failed','payload',NULL,'error_code','processor_failed'))).*
          FROM extraction_revision e WHERE organization_id=:org AND id=:old"""),values)
        assert (await api.get(url,params=params)).status_code==409
        assert (await api.get(url,params={**params,'extraction_revision_id':str(values['revision'])})).status_code==422


async def test_candidates_runtime_roles_pause_and_empty_migration_roundtrip(migrator_session,scope_seed,store,monkeypatch):
    import importlib
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from app.core.db import get_worker_session_factory,get_review_session_factory,set_tenant_context
    from tests.conftest import MIGRATOR_SYNC_URL
    from tests.test_property_source_enforcement import synthetic_enforcement,change_mode
    ids=await fixture(migrator_session,scope_seed)
    monkeypatch.setattr('tests.test_intake_extraction_worker.pdf_bytes',lambda:document(['Objekt-ID: '+str(ids['property'])]))
    async with client(ids) as api:
        _,child,p,e=await prepared(api,ids);url,params=endpoint(child,p,e)
        values={**ids,'logical':uuid.UUID(child['id']),'partition':uuid.UUID(p['id']),'extraction':uuid.UUID(e['id'])}
        for factory in (get_worker_session_factory(),get_review_session_factory()):
            with pytest.raises(DBAPIError,match='permission denied|Interactive runtime required'):
                async with factory() as db,db.begin():
                    await set_tenant_context(db,ids['org'])
                    await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(ids['clerk'])})
                    await db.execute(text('SELECT assignment_candidate_source(:org,:logical,:partition,:extraction)'),values)
        async with synthetic_enforcement(scope_seed):
            await change_mode(scope_seed,'PAUSED')
            response=await api.get(url,params=params);assert response.status_code==200,response.text
            assert not response.json()['can_prepare'] and response.json()['items']==[]
        migration=importlib.import_module('migrations.versions.0111_context_candidate_source');engine=create_engine(MIGRATOR_SYNC_URL)
        try:
            with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
                before=db.scalar(text("SELECT pg_get_functiondef('assignment_candidate_source(uuid,uuid,uuid,uuid)'::regprocedure)"))
                migration.downgrade();migration.upgrade()
                assert before==db.scalar(text("SELECT pg_get_functiondef('assignment_candidate_source(uuid,uuid,uuid,uuid)'::regprocedure)"))
        finally:engine.dispose()
