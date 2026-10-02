"""Prepared source audiences resolve without granting implicit administration access."""
import uuid
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from app.core.db import get_tenant_session
from tests.test_property_source_context import graph

pytestmark=pytest.mark.asyncio


async def access(seed,kind,resource,cap='read',actor=None,org=None):
    organization=org or seed['org_a_id']
    async with get_tenant_session(organization) as db:
        await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),
            {'actor':str(actor or seed['user_a_admin_id'])})
        return await db.scalar(text('SELECT property_source_access(:org,:kind,:resource,:cap)'),
            {'org':organization,'kind':kind,'resource':resource,'cap':cap})


async def test_creator_intake_has_no_implicit_publication_or_other_member_access(seed_data):
    _,rows=await graph(seed_data)
    for kind in ('declaration_import','property_document','declaration_import_document','declaration_candidate',
                 'declaration_candidate_evidence','declaration_candidate_issue','declaration_extraction_run'):
        assert await access(seed_data,kind,rows[kind])
        assert await access(seed_data,kind,rows[kind],'prepare')
        assert await access(seed_data,kind,rows[kind],'export')
        assert not await access(seed_data,kind,rows[kind],'approve')
        assert not await access(seed_data,kind,rows[kind],actor=seed_data['user_a_clerk_id'])
    assert not await access(seed_data,'property',rows['property'])
    assert not await access(seed_data,'unit',rows['unit'])
    assert not await access(seed_data,'unrecognized',rows['property'])
    assert not await access(seed_data,'declaration_import',uuid.uuid4())
    assert not await access(seed_data,'declaration_import',rows['declaration_import'],'send')


async def grant(api,seed,property,actor=None,caps=None):
    result=await api.post('/api/property-data-domains',headers={'Idempotency-Key':str(uuid.uuid4())},
        json={'property_id':str(property),'label':'Resolver audience','reason':'Explicit test audience'})
    assert result.status_code==201,result.text
    domain=result.json()['id']
    result=await api.post(f'/api/property-data-domains/{domain}/grants',headers={'Idempotency-Key':str(uuid.uuid4())},
        json={'user_id':str(actor or seed['user_a_admin_id']),'capabilities':caps or ['read','prepare','approve','export'],
              'reason':'Explicit source capabilities'})
    assert result.status_code==201,result.text
    return domain,result.json()['id']


async def publish(seed,rows):
    async with get_tenant_session(seed['org_a_id']) as db:
        await db.execute(text("UPDATE declaration_import SET status='APPLIED',applied_property_id=:property,"
            "approved_by=:actor,approved_at=clock_timestamp(),approval_idempotency_key='resolver-test',"
            "approval_result_json='{}' WHERE id=:id"),
            {'property':rows['property'],'actor':seed['user_a_admin_id'],'id':rows['declaration_import']})
        await db.execute(text('UPDATE property_document SET property_id=:property WHERE id=:id'),
            {'property':rows['property'],'id':rows['property_document']})


async def test_publication_changes_creator_access_to_only_explicit_property_grants(client_a_admin,seed_data):
    _,rows=await graph(seed_data)
    await publish(seed_data,rows)
    for kind,resource in rows.items():
        assert not await access(seed_data,kind,resource),kind
    domain,receipt=await grant(client_a_admin,seed_data,rows['property'],actor=seed_data['user_a_clerk_id'])
    for kind,resource in rows.items():
        for cap in ('read','prepare','approve','export'):
            assert await access(seed_data,kind,resource,cap,actor=seed_data['user_a_clerk_id']),(kind,cap)
        assert not await access(seed_data,kind,resource),kind
    assert (await client_a_admin.post(f'/api/property-data-domains/{domain}/grants/{receipt}/revoke')).status_code==204
    for kind,resource in rows.items():
        assert not await access(seed_data,kind,resource,actor=seed_data['user_a_clerk_id']),kind


@pytest.mark.parametrize('problem',['unknown_uploader','different_creator','mixed_property','multiple_imports'])
async def test_ambiguous_or_mixed_intake_fails_closed_without_recursive_access(seed_data,problem):
    from app.models.property_document import PropertyDocument
    from app.models.declaration_import import DeclarationImportDocument
    _,rows=await graph(seed_data)
    _,other=await graph(seed_data)
    async with get_tenant_session(seed_data['org_a_id']) as db:
        if problem in ('unknown_uploader','different_creator'):
            document=PropertyDocument(organization_id=seed_data['org_a_id'],doc_type='ANNEX',title='Ambiguous',
                original_filename='ambiguous.pdf',mime_type='application/pdf',content_byte_size=1,
                content_sha256='a'*64,storage_uri='synthetic/ambiguous',storage_backend='TEST',page_count=1,
                uploaded_by=None if problem=='unknown_uploader' else seed_data['user_a_clerk_id'])
            db.add(document)
            await db.flush()
            db.add(DeclarationImportDocument(organization_id=seed_data['org_a_id'],import_id=rows['declaration_import'],
                document_id=document.id,sequence_no=2,document_role='ANNEX'))
        elif problem=='mixed_property':
            await db.execute(text('UPDATE property_document SET property_id=:property WHERE id=:id'),
                {'property':other['property'],'id':rows['property_document']})
        else:
            db.add(DeclarationImportDocument(organization_id=seed_data['org_a_id'],import_id=other['declaration_import'],
                document_id=rows['property_document'],sequence_no=2,document_role='ANNEX'))
    for kind in ('declaration_import','property_document','declaration_candidate','declaration_candidate_evidence'):
        assert not await access(seed_data,kind,rows[kind]),(problem,kind)


async def test_legacy_target_requires_admission_not_merely_a_later_grant(client_a_admin,seed_data):
    from tests.test_declaration_pipeline import _seed_pipeline_import,make_text_pdf
    _,rows=await graph(seed_data)
    domain,_=await grant(client_a_admin,seed_data,rows['property'],actor=seed_data['user_a_clerk_id'])
    lease,document,_=await _seed_pipeline_import(seed_data,make_text_pdf('Existing audience'),
        mode='EXISTING_PROPERTY',target_property_id=rows['property'])
    resources=[('declaration_import',lease.import_id),('property_document',uuid.UUID(document))]
    for kind,resource in resources:
        # The uploader has no data grant, even though it is a configuration admin.
        assert not await access(seed_data,kind,resource,actor=seed_data['user_a_clerk_id'])
        assert not await access(seed_data,kind,resource)
    response=await client_a_admin.post(f'/api/property-data-domains/{domain}/grants',headers={'Idempotency-Key':str(uuid.uuid4())},
        json={'user_id':str(seed_data['user_a_admin_id']),'capabilities':['read','prepare'],'reason':'Explicit source admission'})
    assert response.status_code==201,response.text
    for kind,resource in resources:
        # A later grant cannot fabricate proof of the earlier attachment.
        assert not await access(seed_data,kind,resource,actor=seed_data['user_a_clerk_id'])
    assert (await client_a_admin.post(f"/api/property-data-domains/{domain}/grants/{response.json()['id']}/revoke")).status_code==204
    for kind,resource in resources:
        assert not await access(seed_data,kind,resource,actor=seed_data['user_a_clerk_id'])
    # Once reviewed publication has bound the source, the explicit destination
    # controls its audience; it does not depend on the former uploader staying.
    await publish(seed_data,{**rows,'declaration_import':lease.import_id,'property_document':uuid.UUID(document)})
    for kind,resource in resources:
        assert await access(seed_data,kind,resource,actor=seed_data['user_a_clerk_id'])


async def test_private_explicit_actor_functions_cannot_be_called_or_used_cross_tenant(seed_data):
    _,rows=await graph(seed_data)
    async with get_tenant_session(seed_data['org_a_id']) as db:
        functions=(await db.execute(text("SELECT oid,proname FROM pg_proc WHERE proname=ANY(:names)"),{'names':[
            'property_source_actor_role','property_data_domain_actor_access','property_data_property_actor_access',
            'property_source_import_actor_access','property_source_document_actor_access','property_source_actor_access']})).all()
        assert len(functions)==6
        for oid,name in functions:
            assert not await db.scalar(text("SELECT has_function_privilege('app_rw',CAST(:oid AS oid),'EXECUTE')"),{'oid':oid}),name
        with pytest.raises(DBAPIError,match='permission denied'):
            async with db.begin_nested():
                await db.execute(text("SELECT property_source_actor_access(:org,:actor,'declaration_import',:job,'read')"),
                    {'org':seed_data['org_a_id'],'actor':seed_data['user_a_admin_id'],'job':rows['declaration_import']})
        await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(seed_data['user_b_admin_id'])})
        assert not await db.scalar(text("SELECT property_source_access(:org,'declaration_import',:job,'read')"),
            {'org':seed_data['org_a_id'],'job':rows['declaration_import']})
    assert not await access(seed_data,'declaration_import',rows['declaration_import'],
        actor=seed_data['user_b_admin_id'],org=seed_data['org_b_id'])


async def test_creator_membership_loss_and_read_only_role_are_live(seed_data):
    from tests.test_scoped_account_statements import mutate
    _,rows=await graph(seed_data)
    actor=seed_data['user_a_admin_id']
    await mutate(text("UPDATE membership SET role='READ_ONLY' WHERE user_id=:actor"),{'actor':actor})
    try:
        assert await access(seed_data,'declaration_import',rows['declaration_import'])
        assert await access(seed_data,'property_document',rows['property_document'],'export')
        assert not await access(seed_data,'declaration_import',rows['declaration_import'],'prepare')
        await mutate(text('UPDATE "user" SET is_active=false WHERE id=:actor'),{'actor':actor})
        assert not await access(seed_data,'property_document',rows['property_document'])
    finally:
        await mutate(text('UPDATE "user" SET is_active=true WHERE id=:actor'),{'actor':actor})
        await mutate(text("UPDATE membership SET role='OWNER_ADMIN' WHERE user_id=:actor"),{'actor':actor})


async def test_derived_sources_and_ledgers_use_the_same_audience(client_a_admin,seed_data):
    from datetime import datetime,timedelta,timezone
    from decimal import Decimal
    from app.models.property_rule import PropertyRule,PropertyRuleSource,PropertyRuleRelation
    from app.models.approved_fact_source import ApprovedFactSource
    from app.models.property_legal_reference import PropertyLegalReference
    from app.models.property_assignment import PropertyAssignment
    from app.models.property_document import PropertyDocument
    from app.models.property_document_relation import PropertyDocumentRelation
    from app.models.declaration_import import DeclarationImportDocument,DeclarationImportPass
    from app.models.property_knowledge_query_run import PropertyKnowledgeQueryRun
    from tests.test_declaration_pass_ledger import _entry
    org,rows=await graph(seed_data)
    await publish(seed_data,rows)
    domain,receipt=await grant(client_a_admin,seed_data,rows['property'])
    now=datetime.now(timezone.utc)
    async with get_tenant_session(org) as db:
        await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(seed_data['user_a_admin_id'])})
        common={'organization_id':org,'property_id':rows['property']}
        origin={**common,'source_import_id':rows['declaration_import']}
        rules=[PropertyRule(**origin,stable_key=str(uuid.uuid4()),topic='other',title='Synthetic rule',summary='Synthetic',
            scope_json={'all_units':True},effect_json={},approved_by=seed_data['user_a_admin_id'],approved_at=now) for _ in range(2)]
        doc=PropertyDocument(**common,doc_type='ANNEX',title='Second original',original_filename='second.pdf',
            mime_type='application/pdf',content_byte_size=1,content_sha256='b'*64,storage_uri='synthetic/second',
            storage_backend='TEST',page_count=1,uploaded_by=seed_data['user_a_admin_id'])
        fact=ApprovedFactSource(**origin,subject_kind='UNIT',subject_id=rows['unit'],field_key='number',
            approved_value_json={'kind':'TEXT','value':'1'},value_fingerprint='b'*64,valid_from=now,
            property_document_id=rows['property_document'],pdf_page=1,quote='Synthetic')
        legal=PropertyLegalReference(**origin,reference_type='LAND_REGISTER')
        assignment=PropertyAssignment(**origin,unit_id=rows['unit'],stable_key=str(uuid.uuid4()),
            assignment_type='SPECIAL_USE',designation='Synthetic')
        ledger=PropertyKnowledgeQueryRun(**common,initiated_by=seed_data['user_a_admin_id'],budget_window_start=now,
            provider='test',model='test',reserved_cost_usd=Decimal('.01'),reservation_expires_at=now+timedelta(minutes=10))
        attempt=DeclarationImportPass(organization_id=org,declaration_extraction_run_id=rows['declaration_extraction_run'],
            attempt_no=1,safe_metadata=_entry().as_safe_dict())
        db.add_all([*rules,doc,fact,legal,assignment,ledger,attempt])
        await db.flush()
        source=PropertyRuleSource(**common,property_rule_id=rules[0].id,property_document_id=rows['property_document'],
            pdf_page=1,quote='Synthetic')
        relation=PropertyRuleRelation(**origin,source_rule_id=rules[0].id,target_rule_id=rules[1].id,relation_type='SUPPLEMENTS')
        docrelation=PropertyDocumentRelation(**origin,source_document_id=doc.id,target_document_id=rows['property_document'],relation_type='SUPPLEMENTS')
        db.add_all([source,relation,docrelation,DeclarationImportDocument(organization_id=org,import_id=rows['declaration_import'],
            document_id=doc.id,sequence_no=2,document_role='ANNEX')])
        await db.flush()
        resources={obj.__tablename__:obj.id for obj in [rules[0],fact,legal,assignment,source,relation,docrelation,ledger,attempt]}
        resources['declaration_queue_request']=await db.scalar(text('SELECT queue_request_id FROM declaration_import WHERE id=:id'),{'id':rows['declaration_import']})
    for kind,resource in resources.items():
        assert await access(seed_data,kind,resource),kind
        assert not await access(seed_data,kind,uuid.uuid4()),kind
    assert (await client_a_admin.post(f'/api/property-data-domains/{domain}/grants/{receipt}/revoke')).status_code==204
    for kind,resource in resources.items():
        assert not await access(seed_data,kind,resource),kind


async def test_unactivated_resolver_roundtrip_preserves_domain_grants(client_a_admin,seed_data):
    import importlib
    from sqlalchemy import create_engine
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    _,rows=await graph(seed_data)
    domain,_=await grant(client_a_admin,seed_data,rows['property'])
    migration=importlib.import_module('migrations.versions.0049_property_source_access')
    engine=create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as connection:
            nested=connection.begin_nested()
            try:
                connection.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:actor,true)"),
                    {'org':str(seed_data['org_a_id']),'actor':str(seed_data['user_a_admin_id'])})
                with Operations.context(MigrationContext.configure(connection)):
                    # Later admission policies depend on this resolver. Rehearse
                    # the historical boundary below them, inside the rollback.
                    connection.execute(text('TRUNCATE property_source_admission'))
                    connection.execute(text('TRUNCATE property_source_admission_review'))
                    importlib.import_module('migrations.versions.0052_property_source_admission_review').downgrade()
                    importlib.import_module('migrations.versions.0051_declaration_publication_audience').downgrade()
                    importlib.import_module('migrations.versions.0050_property_source_admission').downgrade()
                    migration.downgrade()
                    assert connection.scalar(text("SELECT has_property_data_capability(:org,:domain,'read')"),
                        {'org':seed_data['org_a_id'],'domain':uuid.UUID(domain)})
                    migration.upgrade()
                    assert connection.scalar(text("SELECT property_source_access(:org,'property',:property,'read')"),
                        {'org':seed_data['org_a_id'],'property':rows['property']})
            finally:
                nested.rollback()
    finally:
        engine.dispose()
