"""New financial records and corrections retain their exact reviewed sphere."""
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.test_financial_scope_boundary import actor, financial_fixture, scope_seed
from tests.test_invoice_extraction_worker import worker_tenant

pytestmark = pytest.mark.asyncio


async def test_new_bank_inherits_selected_scope_atomically(migrator_session, scope_seed):
    session = migrator_session
    ids = await financial_fixture(session, scope_seed)
    ids['new_bank'] = uuid.uuid4()
    await actor(session, ids)
    await session.execute(text("SELECT set_config('app.current_scope_id',:scope,true)"), {'scope': str(ids['scope'])})
    await session.execute(text("INSERT INTO bank_account(id,organization_id,property_id,account_type,label) VALUES (:new_bank,:org,:property,'LAUFEND','New scoped bank')"), ids)
    assert await session.scalar(text('SELECT scope_id FROM accounting_scope_binding WHERE bank_account_id=:new_bank'), ids) == ids['scope']
    await session.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
    await session.execute(text('RESET ROLE'))


async def test_classified_property_cannot_accept_unclassified_new_bank(migrator_session, scope_seed):
    session = migrator_session
    ids = await financial_fixture(session, scope_seed)
    await actor(session, ids)
    with pytest.raises(DBAPIError) as denied:
        async with session.begin_nested():
            await session.execute(text("INSERT INTO bank_account(organization_id,property_id,account_type,label) VALUES (:org,:property,'LAUFEND','Missing scope')"), ids)
    assert 'accounting scope' in str(denied.value).lower()
    await session.execute(text('RESET ROLE'))


@pytest.mark.parametrize('kind', ['cost', 'plan'])
async def test_correction_inherits_original_scope(migrator_session, scope_seed, kind):
    from app.services.cost_entry_service import CostEntryService
    from app.services.economic_plan_service import EconomicPlanService
    session = migrator_session
    ids = await financial_fixture(session, scope_seed)
    await session.execute(text('UPDATE scope_grant SET revoked_at=now(),revoked_by=:actor WHERE id=:grant'), ids)
    await session.execute(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES (:org,:scope,:clerk,ARRAY['read','prepare','confirm','approve'],:actor,'Correction authority')"), ids)
    if kind == 'cost':
        await session.execute(text("UPDATE cost_entry SET status='confirmed',confirmed_at=now(),confirmed_by=:actor WHERE id=:cost"), ids)
    else:
        await session.execute(text("UPDATE economic_plan SET status='finalized',finalized_at=now(),finalized_by=:actor,snapshot_payload='{}',snapshot_sha256=:sha WHERE id=:plan"), {**ids, 'sha': 'b'*64})
    await actor(session, ids)
    if kind == 'cost':
        successor = await CostEntryService(session, ids['org'], ids['clerk']).create_correction_version(ids['cost'], {'gross_amount': Decimal('125')})
        column = 'cost_entry_id'
    else:
        successor = await EconomicPlanService(session, ids['org'], ids['clerk']).create_correction_run(ids['plan'])
        column = 'economic_plan_id'
    assert await session.scalar(text(f'SELECT scope_id FROM accounting_scope_binding WHERE {column}=:id'), {'id': successor.id}) == ids['scope']
    await session.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
    await session.execute(text('RESET ROLE'))


async def test_scoped_creation_api_and_upload_first_link(migrator_session, scope_seed, tmp_path):
    import httpx
    from app.core.config import settings
    from app.core.security import create_session_token
    from app.core.storage import LocalFsDocumentStore, set_store
    from app.main import app
    from tests.test_invoice_extraction_worker import pdf_bytes
    session = migrator_session
    ids = await financial_fixture(session, scope_seed)
    category = await session.scalar(text("SELECT id FROM cost_category WHERE code='GRUNDSTEUER' AND plan_version=1"))
    await session.commit()
    set_store(LocalFsDocumentStore(str(tmp_path)))
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                cookies={settings.session_cookie_name: create_session_token(ids['clerk'], ids['org'])}) as client:
            bank = {'property_id': str(ids['property']), 'account_type': 'LAUFEND', 'label': 'Explicit scoped bank'}
            assert (await client.post('/api/bank-accounts', json=bank)).status_code == 409
            bank['accounting_scope_id'] = str(ids['other_scope'])
            assert (await client.post('/api/bank-accounts', json=bank)).status_code == 404
            bank['accounting_scope_id'] = str(ids['scope'])
            created_bank = await client.post('/api/bank-accounts', json=bank)
            assert created_bank.status_code == 201, created_bank.text
            assert (await client.get('/api/bank-accounts/'+created_bank.json()['id'])).status_code == 200
            bank['property_id'] = str(ids['other_property'])
            assert (await client.post('/api/bank-accounts', json=bank)).status_code == 422
            source = await client.post('/api/invoice-documents/upload', files={'file': ('scope.pdf', pdf_bytes(), 'application/pdf')})
            assert source.status_code == 201, source.text
            body = {'accounting_scope_id': str(ids['scope']), 'source_document_id': source.json()['id'],
                'property_id': str(ids['property']), 'cost_category_id': str(category), 'invoice_date': '2025-06-01',
                'service_period_from': '2025-01-01', 'service_period_to': '2025-12-31', 'gross_amount': '119.00',
                'tenant_apportionable': 'UMLAGEFAEHIG', 'source': 'MANUAL'}
            key = str(uuid.uuid4())
            cost = await client.post('/api/cost-entries', json=body, headers={'Idempotency-Key': key})
            assert cost.status_code == 201, cost.text
            replay = await client.post('/api/cost-entries', json=body, headers={'Idempotency-Key': key})
            assert replay.status_code == 201 and replay.json()['id'] == cost.json()['id']
            linked = await client.get('/api/invoice-documents/'+source.json()['id'])
            assert linked.status_code == 200 and linked.json()['cost_entry_id'] == cost.json()['id']
            from sqlalchemy.ext.asyncio import create_async_engine
            from tests.conftest import MIGRATOR_URL
            engine = create_async_engine(MIGRATOR_URL)
            try:
                async with engine.connect() as connection:
                    assert await connection.scalar(text('SELECT scope_id FROM accounting_scope_binding WHERE cost_entry_id=:id'), {'id': uuid.UUID(cost.json()['id'])}) == ids['scope']
            finally:
                await engine.dispose()
    finally:
        set_store(None)


async def test_bound_correction_pointer_cannot_cross_scope(migrator_session, scope_seed):
    session = migrator_session
    ids = await financial_fixture(session, scope_seed)
    # Definer validation must enforce the chain even for privileged fixtures.
    with pytest.raises(DBAPIError) as denied:
        async with session.begin_nested():
            await session.execute(text('UPDATE cost_entry SET superseded_by_id=:other_cost WHERE id=:cost'), ids)
            await session.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
    assert 'successor must retain' in str(denied.value).lower()


async def test_bound_weg_run_cannot_link_another_accounting_scope(migrator_session, scope_seed):
    session = migrator_session
    ids = await financial_fixture(session, scope_seed)
    ids.update(second_weg=uuid.uuid4(), run=uuid.uuid4())
    await session.execute(text("INSERT INTO accounting_scope(id,organization_id,property_id,scope_type,label,valid_from,whole_property,origin,created_by) VALUES (:second_weg,:org,:property,'WEG','Separate WEG','2025-01-01',true,'REVIEWED',:actor)"), ids)
    await session.execute(text("INSERT INTO settlement_run(id,organization_id,property_id,run_type,economic_plan_id,period_start,period_end,status,engine_version,advance_payments_source) VALUES (:run,:org,:property,'WEG_JAHRESABRECHNUNG',:plan,'2025-01-01','2025-12-31','draft','test','MANUAL')"), ids)
    with pytest.raises(DBAPIError) as denied:
        async with session.begin_nested():
            await session.execute(text("INSERT INTO accounting_scope_binding(organization_id,property_id,scope_id,settlement_run_id,origin,reason,reviewed_by) VALUES (:org,:property,:second_weg,:run,'REVIEWED','Wrong source scope',:actor)"), ids)
            await session.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
    assert 'plan must share accounting scope' in str(denied.value).lower()


async def test_new_plan_inherits_mandate_and_rejects_period_outside_scope(migrator_session, scope_seed):
    from datetime import date
    from fastapi import HTTPException
    from app.services.economic_plan_service import EconomicPlanService
    from tests.test_scope_grants import scope_fixture
    session = migrator_session
    ids = await scope_fixture(session, scope_seed)
    ids.update(mandate=uuid.uuid4(), period_scope=uuid.uuid4())
    await session.execute(text("INSERT INTO management_mandate(id,organization_id,mandate_type,label,start_date,end_date) VALUES (:mandate,:org,'WEG','Year mandate','2025-01-01','2025-12-31')"), ids)
    await session.execute(text("INSERT INTO accounting_scope(id,organization_id,property_id,management_mandate_id,scope_type,label,valid_from,valid_to,whole_property,origin,created_by) VALUES (:period_scope,:org,:property,:mandate,'WEG','Year scope','2025-01-01','2025-12-31',true,'REVIEWED',:actor)"), ids)
    await session.execute(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES (:org,:period_scope,:clerk,ARRAY['read','prepare'],:actor,'Prepare budget')"), ids)
    await actor(session, ids)
    service = EconomicPlanService(session, ids['org'], ids['clerk'])
    with pytest.raises(HTTPException) as outside:
        await service.create_draft(ids['property'], 2026, date(2026,1,1), date(2026,12,31), accounting_scope_id=ids['period_scope'])
    assert outside.value.status_code == 422
    plan = await service.create_draft(ids['property'], 2025, date(2025,1,1), date(2025,12,31), accounting_scope_id=ids['period_scope'])
    assert plan.management_mandate_id == ids['mandate']
    assert await session.scalar(text('SELECT scope_id FROM accounting_scope_binding WHERE economic_plan_id=:id'), {'id': plan.id}) == ids['period_scope']
    await session.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
    await session.execute(text('RESET ROLE'))


async def test_upload_link_failure_restores_context_and_rolls_back_cost(migrator_session, scope_seed, monkeypatch):
    from datetime import date
    from app.models.cost_entry import CostEntry
    from app.services.cost_entry_service import CostEntryService
    session = migrator_session
    ids = await financial_fixture(session, scope_seed)
    ids['unlinked'] = uuid.uuid4()
    await session.execute(text("""INSERT INTO invoice_document(id,organization_id,storage_uri,storage_backend,mime_type,
      content_byte_size,content_sha256,version,chain_root_id,status) VALUES
      (:unlinked,:org,'synthetic/failure.pdf','LOCAL_FS','application/pdf',4,:hash,1,:unlinked,'active')"""), {**ids, 'hash': 'c'*64})
    category = await session.scalar(text("SELECT id FROM cost_category WHERE code='GRUNDSTEUER' AND plan_version=1"))
    await actor(session, ids)
    before = await session.scalar(text('SELECT count(*) FROM cost_entry WHERE property_id=:property'), ids)
    service = CostEntryService(session, ids['org'], ids['clerk'])
    async def fail_link(document):
        raise RuntimeError('synthetic link failure')
    monkeypatch.setattr(service._doc_repo, 'update', fail_link)
    with pytest.raises(RuntimeError, match='synthetic link failure'):
        async with session.begin_nested():
            try:
                await service.create(CostEntry(property_id=ids['property'], cost_category_id=category,
                    cost_category_plan_version=1, invoice_date=date(2025,6,1), service_period_from=date(2025,1,1),
                    service_period_to=date(2025,12,31), gross_amount=Decimal('119'), tenant_apportionable='UMLAGEFAEHIG',
                    status='needs_review', source='MANUAL'), accounting_scope_id=ids['scope'], source_document_id=ids['unlinked'])
            except RuntimeError:
                assert await session.scalar(text("SELECT NULLIF(current_setting('app.current_scope_id',true),'')::uuid")) == ids['scope']
                raise
    assert await session.scalar(text('SELECT count(*) FROM cost_entry WHERE property_id=:property'), ids) == before
    assert await session.scalar(text('SELECT cost_entry_id FROM invoice_document WHERE id=:unlinked'), ids) is None
    await session.execute(text('RESET ROLE'))
