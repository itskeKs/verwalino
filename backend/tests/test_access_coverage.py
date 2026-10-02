"""Materialized administrative coverage never grants original access or activation."""
import uuid
import pytest
from sqlalchemy import text
from tests.test_financial_scope_boundary import scope_seed, worker_tenant  # noqa: F401
from tests.test_scope_grants import scope_fixture
from tests.test_scoped_account_statements import client, mutate
from tests.test_scope_payment_reconciliation import read_value

pytestmark = pytest.mark.asyncio


async def fixture(db, seed):
    ids = await scope_fixture(db, seed)
    await db.commit()
    return ids


async def capture(api, key=None, predecessor=None):
    response = await api.post('/api/access-preparation/snapshots',
        json={'predecessor_id':predecessor}, headers={'Idempotency-Key':key or str(uuid.uuid4())})
    assert response.status_code == 201, response.text
    return response.json()


async def test_snapshot_is_private_materialized_and_idempotent(migrator_session, scope_seed):
    ids = await fixture(migrator_session, scope_seed)
    async with client(ids, 'actor') as admin:
        key = str(uuid.uuid4())
        report = await capture(admin, key)
        assert report == await capture(admin, key)
        assert report['activation_available'] is False
        page = await admin.get(f"/api/access-preparation/snapshots/{report['id']}/items", params={'limit':1})
        assert page.status_code == 200, page.text
        assert len(page.json()['items']) == 1 and page.json()['next_cursor']
        assert report['item_count'] > 1
        serialized = page.text
        for forbidden in ['password_hash','storage_uri','content_sha256','gross_amount','street','@scope-test.invalid']:
            assert forbidden not in serialized
        before = await admin.get(f"/api/access-preparation/snapshots/{report['id']}/items", params={'limit':200})
        await mutate(text('UPDATE scope_grant SET revoked_at=now(),revoked_by=:actor WHERE id=:grant'), ids)
        after = await admin.get(f"/api/access-preparation/snapshots/{report['id']}/items", params={'limit':200})
        assert after.json() == before.json()
        stale = await admin.post(f"/api/access-preparation/snapshots/{report['id']}/review",
            headers={'Idempotency-Key':str(uuid.uuid4())}, json={'manifest_digest':report['manifest_digest'],'acknowledged':True})
        assert stale.status_code == 409, stale.text
    async with client(ids) as clerk:
        assert (await clerk.post('/api/access-preparation/snapshots',json={},headers={'Idempotency-Key':str(uuid.uuid4())})).status_code == 403
        assert (await clerk.get(f"/api/access-preparation/snapshots/{report['id']}/items")).status_code == 403


async def test_snapshot_review_retains_unresolved_findings_without_activation(migrator_session, scope_seed):
    ids = await fixture(migrator_session, scope_seed)
    async with client(ids, 'actor') as admin:
        report = await capture(admin)
        assert report['finding_count'] > 0
        headers = {'Idempotency-Key':str(uuid.uuid4())}
        body = {'manifest_digest':report['manifest_digest'],'acknowledged':True}
        path = f"/api/access-preparation/snapshots/{report['id']}/review"
        reviewed = await admin.post(path, json=body, headers=headers)
        assert reviewed.status_code == 201, reviewed.text
        assert (await admin.post(path, json=body, headers=headers)).json() == reviewed.json()
        assert reviewed.json()['finding_count'] == report['finding_count']
        assert reviewed.json()['activation_available'] is False
        assert await read_value('SELECT property_source_activation_ready(:org)', ids) is False


async def test_coverage_roles_paging_and_strict_audit(migrator_session, scope_seed, client_b_admin):
    from sqlalchemy.exc import DBAPIError
    from app.core.db import get_worker_session_factory, set_tenant_context
    from tests.test_property_source_enforcement import synthetic_enforcement
    ids = await fixture(migrator_session, scope_seed)
    async with client(ids,'actor') as admin:
        first = await capture(admin)
        second = await capture(admin,predecessor=first['id'])
        history = (await admin.get('/api/access-preparation/snapshots',params={'limit':1})).json()
        assert history['items'][0]['id'] == second['id'] and history['next_cursor']
        older = (await admin.get('/api/access-preparation/snapshots',params={'limit':1,'cursor':history['next_cursor']})).json()
        assert older['items'][0]['id'] == first['id'] and older['next_cursor'] is None
        path = f"/api/access-preparation/snapshots/{first['id']}/items"
        page = (await admin.get(path,params={'limit':1})).json()
        seen = page['items']
        cursor = page['next_cursor']
        assert (await admin.get(f"/api/access-preparation/snapshots/{second['id']}/items",params={'cursor':cursor})).status_code == 422
        while cursor:
            page = (await admin.get(path,params={'limit':1,'cursor':cursor})).json()
            seen.extend(page['items']); cursor = page['next_cursor']
        assert len({row['id'] for row in seen}) == first['item_count']
        assert (await client_b_admin.get(path)).status_code == 404
        assert (await admin.get(path,params={'limit':201})).status_code == 422
        async with synthetic_enforcement(scope_seed):
            params = {**ids,'snapshot':uuid.UUID(first['id'])}
            assert await read_value('SELECT count(*) FROM audit_event WHERE entity_id=:snapshot',params,actor_key='actor') == 1
            assert await read_value('SELECT count(*) FROM audit_event WHERE entity_id=:snapshot',params,actor_key='clerk') == 0
    for table in ('access_coverage_snapshot','access_coverage_review'):
        with pytest.raises(DBAPIError,match='permission denied'):
            await read_value(f'SELECT count(*) FROM {table}',ids,actor_key='actor')
    async with get_worker_session_factory()() as worker, worker.begin():
        await set_tenant_context(worker,ids['org'])
        await worker.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(ids['actor'])})
        with pytest.raises(DBAPIError,match='Current coverage administrator'):
            await worker.execute(text('SELECT access_coverage_headers(:org,NULL,NULL,50)'),ids)


async def test_unknown_bound_proof_propagates_without_content_disclosure(migrator_session, scope_seed):
    ids = await fixture(migrator_session, scope_seed)
    ids.update(document=uuid.uuid4(),unit=uuid.uuid4())
    await mutate(text("INSERT INTO property_document(id,organization_id,property_id,doc_type,title,original_filename,mime_type,content_byte_size,content_sha256,storage_uri,storage_backend,page_count) VALUES(:document,:org,:property,'ANNEX','Private protected title','private.pdf','application/pdf',1,repeat('b',64),'synthetic/private-secret','TEST',1)"),ids)
    await mutate(text("INSERT INTO unit(id,organization_id,property_id,number,usage_type) VALUES(:unit,:org,:property,'secret-number','WOHNUNG')"),ids)
    async with client(ids,'actor') as admin:
        report = await capture(admin)
        response = await admin.get(f"/api/access-preparation/snapshots/{report['id']}/items",params={'limit':200})
        assert response.status_code == 200,response.text
        rows = response.json()['items']
        source = next(row for row in rows if row['resource_id']==str(ids['document']))
        assert 'VALID_BOUND_PROOF_REQUIRED' in source['findings']
        child = next(row for row in rows if row['resource_id']==str(ids['unit']))
        assert 'SOURCE_DEPENDENCY_BLOCKS_OPERATIONS' in child['findings']
        for secret in ['Private protected title','private.pdf','synthetic/private-secret','secret-number','b'*64]:
            assert secret not in response.text
        domain = await admin.post('/api/property-data-domains',json={'property_id':str(ids['property']),'label':'Reviewed audience','reason':'Synthetic explicit review'},headers={'Idempotency-Key':str(uuid.uuid4())})
        assert domain.status_code == 201,domain.text
        grant = await admin.post(f"/api/property-data-domains/{domain.json()['id']}/grants",json={'user_id':str(ids['actor']),'capabilities':['read','prepare','approve'],'reason':'Synthetic explicit review'},headers={'Idempotency-Key':str(uuid.uuid4())})
        assert grant.status_code == 201,grant.text
        candidates = await admin.get('/api/property-source-classifications/pending/page')
        candidate = next(r for r in candidates.json()['items'] if r['document_id']==str(ids['document']))
        reviewed = await admin.post('/api/property-source-classifications',json={
            'document_id':str(ids['document']),'property_id':str(ids['property']),
            'content_sha256':candidate['content_sha256'],'source_context_digest':candidate['source_context_digest'],
            'decision':'QUARANTINED','reason':'Unknown historical custody retained'},headers={'Idempotency-Key':str(uuid.uuid4())})
        assert reviewed.status_code == 201,reviewed.text
        new = await capture(admin,predecessor=report['id'])
        rows = (await admin.get(f"/api/access-preparation/snapshots/{new['id']}/items",params={'limit':200})).json()['items']
        assert 'REVIEWED_QUARANTINE' in next(r for r in rows if r['resource_id']==str(ids['document']))['findings']
        assert 'QUARANTINED_INPUTS_BLOCK_OPERATIONS' in next(r for r in rows if r['kind']=='property' and r['resource_id']==str(ids['property']))['findings']
        changed = await admin.post('/api/access-preparation/snapshots',json={'predecessor_id':str(uuid.uuid4())},headers={'Idempotency-Key':str(uuid.uuid4())})
        assert changed.status_code == 404


async def test_capture_waits_for_live_admin_and_migration_retains_snapshots(migrator_session, scope_seed):
    import asyncio
    import importlib
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    ids = await fixture(migrator_session, scope_seed)
    async with client(ids,'actor') as admin:
        report = await capture(admin)
        engine = create_engine(MIGRATOR_SYNC_URL)
        try:
            with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
                migration = importlib.import_module('migrations.versions.0083_access_coverage_snapshots')
                with pytest.raises(DBAPIError,match='Retain reviewed coverage'):
                    with db.begin_nested():
                        migration.downgrade()
                point = db.begin_nested()
                try:
                    db.execute(text('TRUNCATE access_coverage_review,access_coverage_snapshot CASCADE'))
                    importlib.import_module('migrations.versions.0090_access_disposition').downgrade()
                    proposal_migration = importlib.import_module('migrations.versions.0084_access_grant_proposals')
                    db.execute(text('TRUNCATE runtime_budget_review,runtime_budget_attestation CASCADE'))
                    importlib.import_module('migrations.versions.0088_runtime_budget_review').downgrade()
                    importlib.import_module('migrations.versions.0087_projected_access_coverage').downgrade()
                    proposal_migration.downgrade()
                    migration.downgrade(); migration.upgrade()
                    proposal_migration.upgrade()
                    assert not db.scalar(text('SELECT property_source_activation_ready(:org)'),ids)
                finally:
                    point.rollback()
        finally:
            engine.dispose()
        # A stale authentication result never replaces the current DB role.
        from app.core.db import get_session_factory, set_tenant_context
        async with get_session_factory()() as holder, holder.begin():
            await set_tenant_context(holder,ids['org'])
            await holder.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),{'key':f"scope-acl:{ids['org']}"})
            task = asyncio.create_task(admin.post('/api/access-preparation/snapshots',json={},headers={'Idempotency-Key':str(uuid.uuid4())}))
            try:
                holder_pid = await holder.scalar(text('SELECT pg_backend_pid()'))
                for _ in range(200):
                    if await read_value("SELECT count(*) FROM pg_stat_activity WHERE wait_event='advisory' AND :holder=ANY(pg_blocking_pids(pid))",{**ids,'holder':holder_pid}):
                        break
                    await asyncio.sleep(.01)
                else: pytest.fail('Capture did not wait for authorization lock')
            finally:
                task.cancel()
                await asyncio.gather(task,return_exceptions=True)
        assert report['activation_available'] is False


async def test_expired_grant_invalidates_review_and_immutable_receipt(migrator_session, scope_seed):
    from sqlalchemy.exc import DBAPIError
    ids = await fixture(migrator_session,scope_seed)
    await mutate(text('UPDATE scope_grant SET revoked_at=now(),revoked_by=:actor WHERE id=:grant'),ids)
    await mutate(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason,expires_at) VALUES(:org,:scope,:clerk,ARRAY['read','prepare'],:actor,'Short review window',clock_timestamp()+interval '2 seconds')"),ids)
    async with client(ids,'actor') as admin:
        report = await capture(admin)
        await read_value("SELECT pg_sleep(GREATEST(0,EXTRACT(EPOCH FROM max(expires_at)-clock_timestamp()))+.02) FROM scope_grant WHERE organization_id=:org",ids)
        response = await admin.post(f"/api/access-preparation/snapshots/{report['id']}/review",json={'manifest_digest':report['manifest_digest'],'acknowledged':True},headers={'Idempotency-Key':str(uuid.uuid4())})
        assert response.status_code == 409,response.text
        with pytest.raises(DBAPIError,match='immutable'):
            await mutate(text("UPDATE access_coverage_snapshot SET manifest='{}' WHERE id=:snapshot"),{'snapshot':uuid.UUID(report['id'])})


async def test_admin_revoked_after_http_auth_cannot_capture(migrator_session, scope_seed, monkeypatch):
    from app.api import access_preparation
    ids = await fixture(migrator_session,scope_seed)
    original = access_preparation.require_live_principal
    async def revoked_before_check(db,auth,**kwargs):
        await mutate(text("UPDATE membership SET role='READ_ONLY' WHERE organization_id=:org AND user_id=:actor"),ids)
        return await original(db,auth,**kwargs)
    monkeypatch.setattr(access_preparation,'require_live_principal',revoked_before_check)
    async with client(ids,'actor') as admin:
        response = await admin.post('/api/access-preparation/snapshots',json={},headers={'Idempotency-Key':str(uuid.uuid4())})
        assert response.status_code == 403,response.text
    assert await read_value('SELECT count(*) FROM access_coverage_snapshot WHERE organization_id=:org',ids) == 0


async def test_legacy_json_reference_array_never_discloses_payload(migrator_session,scope_seed):
    import json
    from tests.test_financial_scope_boundary import financial_fixture
    ids = await financial_fixture(migrator_session,scope_seed)
    ids.update(coverage=uuid.uuid4(),safe=uuid.uuid4())
    await migrator_session.execute(text("""INSERT INTO coverage_declaration(id,organization_id,economic_plan_id,property_id,component,
        period_start,period_end,revision,state,values,source_document_ids,source_revision,reason,attestor_id)
        VALUES(:coverage,:org,:plan,:property,'planned_costs','2025-01-01','2025-12-31',1,'known','{}',CAST(:sources AS jsonb),repeat('c',64),'Historical reference evidence',:actor)"""),
        {**ids,'sources':json.dumps([str(ids['safe']),'private-source-secret',{'secret':'private-extracted-payload'}])})
    await migrator_session.commit()
    async with client(ids,'actor') as admin:
        report = await capture(admin)
        response = await admin.get(f"/api/access-preparation/snapshots/{report['id']}/items",params={'limit':200})
        assert 'private-source-secret' not in response.text and 'private-extracted-payload' not in response.text
        row = next(r for r in response.json()['items'] if r['resource_id']==str(ids['coverage']))
        assert row['references']['source_document_ids'] == [str(ids['safe'])]
        assert 'INVALID_REFERENCE_ARRAY' in row['findings']


async def test_hidden_vendor_tax_identifiers_are_never_administrative_references(migrator_session,scope_seed):
    from tests.test_property_source_enforcement import synthetic_enforcement
    ids = await fixture(migrator_session,scope_seed)
    ids.update(person=uuid.uuid4(),vendor=uuid.uuid4())
    await mutate(text("INSERT INTO person(id,organization_id,first_name,last_name,street,zip_code,city) VALUES(:person,:org,'Hidden','Contact','','','')"),ids)
    await mutate(text("INSERT INTO vendor(id,organization_id,person_id,tax_id,vat_id,notes) VALUES(:vendor,:org,:person,'TAX-PRIVATE-8372','VAT-PRIVATE-8372','Protected business notes')"),ids)
    async with synthetic_enforcement(scope_seed), client(ids,'actor') as admin:
        assert await read_value('SELECT count(*) FROM vendor WHERE id=:vendor',ids,actor_key='actor') == 0
        report = await capture(admin)
        cursor,rows = None,[]
        while True:
            response = await admin.get(f"/api/access-preparation/snapshots/{report['id']}/items",params={'limit':2,**({'cursor':cursor} if cursor else {})})
            assert response.status_code == 200,response.text
            for forbidden in ['tax_id','vat_id','TAX-PRIVATE-8372','VAT-PRIVATE-8372','Protected business notes','Hidden','Contact']:
                assert forbidden not in response.text
            page = response.json(); rows.extend(page['items']); cursor = page['next_cursor']
            if not cursor: break
        vendor = next(row for row in rows if row['kind']=='vendor')
        assert vendor['references'] == {'person_id':str(ids['person']),'strict_audience':[]}
