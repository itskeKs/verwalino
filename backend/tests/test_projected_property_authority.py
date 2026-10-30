"""The strict evaluator must not change mode or impersonate its target actor."""
import uuid
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from tests.conftest import MIGRATOR_URL
from tests.test_financial_scope_boundary import scope_seed,worker_tenant  # noqa: F401
from tests.test_declaration_import_api import isolated_document_store, enable_declaration_imports  # noqa: F401
from tests.test_access_coverage import fixture
from tests.test_scoped_account_statements import client,mutate
from tests.test_property_source_access import grant
from tests.test_property_source_enforcement import synthetic_enforcement,change_mode
from tests.test_scope_payment_reconciliation import read_value

pytestmark=pytest.mark.asyncio


async def evaluate(ids,kind,resource,cap='read',mode='ENFORCED',actor='clerk',graph=False):
    engine=create_async_engine(MIGRATOR_URL)
    try:
        async with engine.begin() as db:
            await db.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:current,true)"),
                {'org':str(ids['org']),'current':str(ids['actor'])})
            before=(await db.execute(text("SELECT property_source_security_mode(:org),current_setting('app.current_user_id')"),ids)).one()
            sql='SELECT property_complete_graph_actor_access_at_mode(:org,:target,:resource,:cap,false,:mode)' if graph else 'SELECT property_source_actor_permitted_at_mode(:org,:target,:kind,:resource,:cap,:mode)'
            result=await db.scalar(text(sql),{**ids,'target':ids[actor],'kind':kind,'resource':resource,'cap':cap,'mode':mode})
            after=(await db.execute(text("SELECT property_source_security_mode(:org),current_setting('app.current_user_id')"),ids)).one()
            assert after==before
            return result
    finally: await engine.dispose()


async def test_projected_denial_while_preparation_compatibility_is_unchanged(migrator_session,scope_seed):
    ids=await fixture(migrator_session,scope_seed)
    assert await read_value("SELECT property_source_permitted(:org,'property',:property,'read')",ids,actor_key='clerk')
    assert not await evaluate(ids,'property',ids['property'])
    async with client(ids,'actor') as admin:
        domain,receipt=await grant(admin,scope_seed,ids['property'],actor=ids['clerk'])
        assert await evaluate(ids,'property',ids['property'])
        assert await evaluate(ids,'property',ids['property'],'prepare')
        assert await evaluate(ids,'property',ids['property'],graph=True)
        assert (await admin.post(f'/api/property-data-domains/{domain}/grants/{receipt}/revoke')).status_code==204
        assert not await evaluate(ids,'property',ids['property'])
    assert await read_value("SELECT property_source_security_mode(:org)",ids,actor_key='actor')=='PREPARATION'
    assert await read_value('SELECT property_source_activation_ready(:org)',ids) is False


async def test_projected_bound_proof_does_not_inherit_preparation_bypass(migrator_session,scope_seed):
    ids=await fixture(migrator_session,scope_seed);ids['document']=uuid.uuid4()
    await mutate(text("INSERT INTO property_document(id,organization_id,property_id,doc_type,title,original_filename,mime_type,content_byte_size,content_sha256,storage_uri,storage_backend,page_count) VALUES(:document,:org,:property,'ANNEX','Unknown original','test.pdf','application/pdf',1,repeat('a',64),'synthetic/test','TEST',1)"),ids)
    async with client(ids,'actor') as admin: await grant(admin,scope_seed,ids['property'],actor=ids['clerk'])
    assert await read_value("SELECT property_source_permitted(:org,'property_document',:document,'read')",ids,actor_key='clerk')
    assert not await evaluate(ids,'property_document',ids['document'])
    assert not await evaluate(ids,'property',ids['property'],graph=True)


@pytest.mark.parametrize('mode',['ENFORCED','PAUSED'])
async def test_live_permissions_match_same_mode_evaluator(migrator_session,scope_seed,mode):
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids,'actor') as admin: await grant(admin,scope_seed,ids['property'],actor=ids['clerk'])
    async with synthetic_enforcement(scope_seed):
        if mode=='PAUSED': await change_mode(scope_seed,mode)
        for kind,resource in [('property',ids['property']),('property',ids['other_property'])]:
            for capability in ('read','prepare','approve','export'):
                projected=await evaluate(ids,kind,resource,capability,mode)
                actual=await read_value('SELECT property_source_permitted(:org,:kind,:resource,:cap)',
                    {**ids,'kind':kind,'resource':resource,'cap':capability},actor_key='clerk')
                assert projected==actual,(kind,capability,mode)
        assert await evaluate(ids,'property',ids['property'],mode=mode,graph=True)
        assert not await evaluate(ids,'property',ids['property'],'prepare',mode='PAUSED')
        # Independent hypothetical ENFORCED still does not resume the organization.
        assert await evaluate(ids,'property',ids['property'],'prepare',mode='ENFORCED')


async def test_no_mode_bypass_or_collective_actor_authority(migrator_session,scope_seed):
    from sqlalchemy.exc import DBAPIError
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids,'actor') as admin: await grant(admin,scope_seed,ids['property'],actor=ids['clerk'],caps=['read'])
    assert await evaluate(ids,'property',ids['property'],'read')
    assert not await evaluate(ids,'property',ids['property'],'read',actor='actor')
    assert not await evaluate(ids,'property',ids['property'],'export')
    assert not await evaluate(ids,'property',ids['property'],mode=None)
    assert not await evaluate(ids,'property',ids['property'],mode='UNRECOGNIZED')
    with pytest.raises(DBAPIError,match='permission denied'):
        await read_value("SELECT property_source_actor_permitted_at_mode(:org,:clerk,'property',:property,'read','PREPARATION')",ids,actor_key='actor')
    import importlib
    migration=importlib.import_module('migrations.versions.0085_property_authority_projection')
    for name,(signature,_) in migration.EXPECTED.items():
        internal=signature.replace(name+'(',name+'_at_mode(')[:-1]+',text)'
        for role in ('app_rw','app_worker','app_review'):
            assert not await read_value('SELECT has_function_privilege(:role,:signature,\'EXECUTE\')',{'role':role,'signature':internal})
        body=await read_value('SELECT prosrc FROM pg_proc WHERE oid=CAST(:signature AS regprocedure)',{'signature':internal})
        assert 'public.property_source_security_mode(org)' not in body
        for original in migration.EXPECTED: assert 'public.'+original+'(' not in body


async def test_migration_roundtrip_preserves_original_bodies_oids_and_acls(migrator_session):
    import hashlib,importlib
    from sqlalchemy import create_engine
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    await migrator_session.commit()
    migration=importlib.import_module('migrations.versions.0085_property_authority_projection')
    engine=create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
            def identities():
                return {name:db.execute(text('SELECT oid,proowner,proacl::text,provolatile,prosecdef,proconfig FROM pg_proc WHERE oid=CAST(:signature AS regprocedure)'),{'signature':signature}).one()
                        for name,(signature,_) in migration.EXPECTED.items()}
            before=identities()
            migration.downgrade()
            assert identities()==before
            for name,(signature,digest) in migration.EXPECTED.items():
                body=db.scalar(text('SELECT prosrc FROM pg_proc WHERE oid=CAST(:signature AS regprocedure)'),{'signature':signature})
                assert hashlib.sha256(body.encode()).hexdigest()==digest
            migration.upgrade()
            assert identities()==before
    finally:engine.dispose()


@pytest.mark.parametrize('decision',['ADMITTED','QUARANTINED'])
async def test_projection_propagates_bound_proof_through_imports_and_children(client_a_admin,seed_data,decision):
    from tests.test_property_source_classification import legacy_bound
    from tests.test_bound_source_access import classify
    ids=await legacy_bound(client_a_admin,seed_data)
    ids.update(org=seed_data['org_a_id'],actor=seed_data['user_a_admin_id'],clerk=seed_data['user_a_clerk_id'])
    link=await read_value('SELECT id FROM declaration_import_document WHERE import_id=:job',ids)
    resources=[('property_document',ids['document']),('declaration_import',ids['job']),('declaration_import_document',link)]
    for kind,resource in resources: assert not await evaluate(ids,kind,resource)
    await classify(client_a_admin,ids,decision)
    for kind,resource in resources:
        assert await evaluate(ids,kind,resource) is (decision=='ADMITTED')
    assert await evaluate(ids,'property',ids['property'],graph=True) is (decision=='ADMITTED')
    async with synthetic_enforcement(seed_data):
        for kind,resource in resources:
            actual=await read_value('SELECT property_source_permitted(:org,:kind,:resource,\'read\')',
                {**ids,'kind':kind,'resource':resource},actor_key='clerk')
            assert await evaluate(ids,kind,resource)==actual


async def test_expiry_and_membership_change_are_live_in_projection(migrator_session,scope_seed):
    from datetime import datetime,timedelta,timezone
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids,'actor') as admin:
        domain,_=await grant(admin,scope_seed,ids['property'],actor=ids['actor'])
        expiry=datetime.now(timezone.utc)+timedelta(seconds=2)
        response=await admin.post(f'/api/property-data-domains/{domain}/grants',json={'user_id':str(ids['clerk']),'capabilities':['read'],'reason':'Short test','expires_at':expiry.isoformat()},headers={'Idempotency-Key':str(uuid.uuid4())})
        assert response.status_code==201,response.text
    assert await evaluate(ids,'property',ids['property'])
    await read_value('SELECT pg_sleep(GREATEST(0,EXTRACT(EPOCH FROM CAST(:expiry AS timestamptz)-clock_timestamp()))+.02)',{'expiry':expiry})
    assert not await evaluate(ids,'property',ids['property'])
    await mutate(text("UPDATE membership SET role='READ_ONLY' WHERE organization_id=:org AND user_id=:actor"),ids)
    assert await evaluate(ids,'property',ids['property'],'read',actor='actor')
    assert not await evaluate(ids,'property',ids['property'],'prepare',actor='actor')


async def test_one_actor_cannot_borrow_another_creators_source(seed_data):
    from app.core.db import get_tenant_session
    from app.models.property_document import PropertyDocument
    from app.models.declaration_import import DeclarationImportDocument
    from tests.test_property_source_context import graph
    org,rows=await graph(seed_data)
    ids={'org':org,'actor':seed_data['user_a_admin_id'],'clerk':seed_data['user_a_clerk_id']}
    for kind in ('declaration_import','property_document','declaration_candidate_evidence'):
        assert await evaluate(ids,kind,rows[kind],actor='actor')
        assert not await evaluate(ids,kind,rows[kind])
    async with get_tenant_session(org) as db:
        source=PropertyDocument(organization_id=org,doc_type='ANNEX',title='Separate custody',
            original_filename='test.pdf',mime_type='application/pdf',content_byte_size=1,content_sha256='b'*64,
            storage_uri='synthetic/separate-custody',storage_backend='TEST',page_count=1,uploaded_by=ids['clerk'])
        db.add(source);await db.flush()
        db.add(DeclarationImportDocument(organization_id=org,import_id=rows['declaration_import'],document_id=source.id,sequence_no=2,document_role='ANNEX'))
    for kind in ('declaration_import','property_document','declaration_candidate_evidence'):
        assert not await evaluate(ids,kind,rows[kind],actor='actor')
        assert not await evaluate(ids,kind,rows[kind])


async def test_projected_complete_graph_rejects_mixed_historical_identity(migrator_session,scope_seed,seed_data):
    ids=await fixture(migrator_session,scope_seed)
    ids.update(unit=uuid.uuid4(),foreign_person=uuid.uuid4(),profile=uuid.uuid4(),history=uuid.uuid4(),foreign=seed_data['org_b_id'])
    async with client(ids,'actor') as admin: await grant(admin,scope_seed,ids['property'],actor=ids['clerk'])
    await mutate(text("INSERT INTO unit(id,organization_id,property_id,number,usage_type) VALUES(:unit,:org,:property,'1','WOHNUNG')"),ids)
    assert await evaluate(ids,'property',ids['property'],graph=True)
    await mutate(text("INSERT INTO person(id,organization_id,first_name,last_name,street,zip_code,city) VALUES(:foreign_person,:foreign,'Foreign','History','','','')"),ids)
    try:
        # Keep the lease/profile edge tenant-coherent; the profile/person edge
        # still permits the malformed identity that the projected graph rejects.
        await mutate(text("INSERT INTO tenant_profile(id,organization_id,person_id) VALUES(:profile,:org,:foreign_person)"),ids)
        await mutate(text("INSERT INTO lease_agreement(id,organization_id,unit_id,tenant_profile_id,valid_from,valid_to,base_rent,advance_payment) VALUES(:history,:org,:unit,:profile,'2000-01-01','2001-01-01',10,1)"),ids)
        assert not await evaluate(ids,'property',ids['property'],graph=True)
        assert await read_value("SELECT property_source_permitted(:org,'property',:property,'read')",ids,actor_key='clerk')
    finally:
        await mutate(text('DELETE FROM lease_agreement WHERE id=:history'),ids)
        await mutate(text('DELETE FROM tenant_profile WHERE id=:profile'),ids)
        await mutate(text('DELETE FROM person WHERE id=:foreign_person'),ids)


@pytest.mark.parametrize('quarantined',[False,True])
async def test_recursive_derived_projection_uses_one_actor_and_exact_source(approved_knowledge_graph,seed_data,client_a_admin,quarantined):
    from tests.test_bound_source_access import classify
    source=approved_knowledge_graph
    ids={'org':source.organization_id,'property':source.property_id,'actor':seed_data['user_a_admin_id'],
         'clerk':seed_data['user_a_clerk_id'],'rule':source.heating_rule_id,'fact':uuid.uuid4()}
    domain,administrator_grant=await grant(client_a_admin,seed_data,source.property_id)
    try:
        rule_source=await read_value('SELECT id FROM property_rule_source WHERE property_rule_id=:rule',ids)
        main=await read_value('SELECT property_document_id FROM property_rule_source WHERE id=:source',{'source':rule_source})
        documents=await read_value('SELECT array_agg(id) FROM property_document WHERE property_id=:property',ids)
        await mutate(text("""INSERT INTO approved_fact_source(id,organization_id,property_id,subject_kind,subject_id,field_key,
            approved_value_json,value_fingerprint,status,valid_from,property_document_id,pdf_page,quote,source_import_id)
            SELECT :fact,organization_id,property_id,'RULE',:rule,'projection_fixture',approved_value_json,value_fingerprint,
            'EFFECTIVE',clock_timestamp(),property_document_id,pdf_page,quote,source_import_id
            FROM approved_fact_source WHERE property_id=:property AND property_document_id=:main LIMIT 1"""),{**ids,'main':main})
        for document in documents:
            await classify(client_a_admin,{'document':document,'property':source.property_id},'QUARANTINED' if quarantined and document==main else 'ADMITTED')
        assert (await client_a_admin.post(f'/api/property-data-domains/{domain}/grants/{administrator_grant}/revoke')).status_code==204
        response=await client_a_admin.post(f'/api/property-data-domains/{domain}/grants',json={'user_id':str(ids['clerk']),'capabilities':['read'],'reason':'Explicit derived source reader'},headers={'Idempotency-Key':str(uuid.uuid4())})
        assert response.status_code==201,response.text
        resources=[('property_rule_source',rule_source),('approved_fact_source',ids['fact'])]
        for kind,resource in resources:
            assert await evaluate(ids,kind,resource) is (not quarantined)
            assert not await evaluate(ids,kind,resource,actor='actor')
        async with synthetic_enforcement(seed_data):
            for kind,resource in resources:
                for actor in ('actor','clerk'):
                    actual=await read_value('SELECT property_source_permitted(:org,:kind,:resource,\'read\')',
                        {**ids,'kind':kind,'resource':resource},actor_key=actor)
                    assert await evaluate(ids,kind,resource,actor=actor)==actual
    finally:
        await mutate(text('TRUNCATE property_data_domain CASCADE'),{})
