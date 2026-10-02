"""Exact accounting scope boundaries for bound money and its dependent sources."""
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.test_scope_grants import scope_fixture, bind

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def scope_seed(migrator_session, worker_tenant):
    # Direct DB fixtures commit at teardown. Keep classified finance out of the
    # shared legacy organization used by organization-wide regression suites.
    _, seed = worker_tenant
    seed = {**seed, 'user_a_clerk_id': uuid.uuid4()}
    params = {'clerk': seed['user_a_clerk_id'], 'org': seed['org_a_id'],
              'email': str(seed['user_a_clerk_id'])+'@scope-test.invalid'}
    await migrator_session.execute(text("""INSERT INTO "user" (id,organization_id,email,password_hash,display_name)
      VALUES (:clerk,:org,:email,'fixture','Scope clerk')"""), params)
    await migrator_session.execute(text("INSERT INTO membership(organization_id,user_id,role) VALUES (:org,:clerk,'CLERK')"), params)
    return seed


async def financial_fixture(session, seed):
    ids = await scope_fixture(session, seed)
    ids.update(cost=uuid.uuid4(), other_cost=uuid.uuid4(), document=uuid.uuid4(), plan=uuid.uuid4(), plan_line=uuid.uuid4())
    for key in ('cost', 'other_cost'):
        await session.execute(text("""INSERT INTO cost_entry(id,organization_id,property_id,cost_category_id,
          cost_category_plan_version,invoice_date,service_period_from,service_period_to,gross_amount,
          tenant_apportionable,status,source)
          SELECT :id,:org,:property,id,1,'2025-06-01','2025-01-01','2025-12-31',100,'UMLAGEFAEHIG','needs_review','MANUAL'
          FROM cost_category WHERE code='GRUNDSTEUER' AND plan_version=1"""), {**ids, 'id': ids[key]})
    await session.execute(text("""INSERT INTO invoice_document(id,organization_id,cost_entry_id,storage_uri,
      storage_backend,mime_type,content_byte_size,content_sha256,version,chain_root_id,status)
      VALUES (:document,:org,:cost,'synthetic/scoped.pdf','LOCAL_FS','application/pdf',4,:hash,1,:document,'active')"""), {**ids, 'hash': 'a' * 64})
    await session.execute(text("""INSERT INTO economic_plan(id,organization_id,property_id,plan_year,
      period_start,period_end,status,engine_version)
      VALUES (:plan,:org,:property,2025,'2025-01-01','2025-12-31','draft','test')"""), ids)
    await session.execute(text("""INSERT INTO economic_plan_line(id,organization_id,economic_plan_id,line_type,label,planned_amount,sort_order)
      VALUES (:plan_line,:org,:plan,'PLANNED_COST','Scoped line',100,1)"""), ids)
    await bind(session, ids)
    for kind, key, sphere in (('cost_entry','cost','scope'), ('cost_entry','other_cost','other_scope'), ('economic_plan','plan','scope')):
        await session.execute(text(f"""INSERT INTO accounting_scope_binding(organization_id,property_id,scope_id,{kind}_id,origin,reason,reviewed_by)
          VALUES (:org,:property,:sphere,:resource,'REVIEWED','Exact source classification',:actor)"""),
          {**ids, 'sphere': ids[sphere], 'resource': ids[key]})
    await session.execute(text("INSERT INTO audit_event(organization_id,actor_user_id,entity_type,entity_id,action,payload_diff) VALUES (:org,:actor,'cost_entry',:cost,'cost.created',jsonb_build_object('amount',100))"), ids)
    return ids


async def actor(session, ids, key='clerk'):
    await session.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:actor,true)"), {'org': str(ids['org']), 'actor': str(ids[key])})
    await session.execute(text('SET LOCAL ROLE app_rw'))


async def test_bound_financial_reads_require_exact_grant_even_for_administrator(migrator_session, scope_seed):
    session = migrator_session
    ids = await financial_fixture(session, scope_seed)
    await actor(session, ids)
    assert await session.scalar(text('SELECT count(*) FROM bank_account WHERE id=:bank'), ids) == 1
    assert await session.scalar(text('SELECT count(*) FROM cost_entry WHERE id IN (:cost,:other_cost)'), ids) == 1
    await session.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {'actor': str(ids['actor'])})
    for table, key in (('bank_account','bank'), ('cost_entry','cost'), ('economic_plan','plan'), ('economic_plan_line','plan_line'), ('invoice_document','document')):
        assert await session.scalar(text(f'SELECT count(*) FROM {table} WHERE id=:id'), {'id': ids[key]}) == 0
    assert await session.scalar(text('SELECT count(*) FROM audit_event WHERE entity_id=:cost'), ids) == 0
    await session.execute(text('RESET ROLE'))


async def test_selected_scope_does_not_mix_two_authorized_spheres(migrator_session, scope_seed):
    session = migrator_session
    ids = await financial_fixture(session, scope_seed)
    await session.execute(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES (:org,:other_scope,:clerk,ARRAY['read','prepare'],:actor,'Second exact sphere')"), ids)
    await actor(session, ids)
    assert await session.scalar(text('SELECT count(*) FROM cost_entry WHERE property_id=:property'), ids) == 2
    await session.execute(text("SELECT set_config('app.current_scope_id',:scope,true)"), {'scope': str(ids['scope'])})
    assert await session.scalar(text('SELECT sum(gross_amount) FROM cost_entry WHERE property_id=:property'), ids) == 100
    assert await session.scalar(text('SELECT count(*) FROM invoice_document WHERE id=:document'), ids) == 1
    await session.execute(text("SELECT set_config('app.current_scope_id',:scope,true)"), {'scope': str(ids['other_scope'])})
    assert await session.scalar(text('SELECT count(*) FROM invoice_document WHERE id=:document'), ids) == 0
    await session.execute(text('RESET ROLE'))


async def test_preparation_grant_cannot_confirm_cost_or_finalize_plan(migrator_session, scope_seed):
    session = migrator_session
    ids = await financial_fixture(session, scope_seed)
    await actor(session, ids)
    await session.execute(text("UPDATE cost_entry SET document_number='Prepared' WHERE id=:cost"), ids)
    with pytest.raises(DBAPIError) as confirmation:
        async with session.begin_nested():
            await session.execute(text("UPDATE cost_entry SET status='confirmed',confirmed_at=now(),confirmed_by=:clerk WHERE id=:cost"), ids)
    assert 'scope capability' in str(confirmation.value).lower()
    with pytest.raises(DBAPIError) as approval:
        async with session.begin_nested():
            await session.execute(text("UPDATE economic_plan SET status='finalized',finalized_at=now(),finalized_by=:clerk,snapshot_payload='{}',snapshot_sha256=:sha WHERE id=:plan"), {**ids, 'sha': 'b'*64})
    assert 'scope capability' in str(approval.value).lower()
    await session.execute(text('RESET ROLE'))


async def test_computation_context_rejects_unclassified_inputs(migrator_session, scope_seed):
    from fastapi import HTTPException
    from app.models.economic_plan import EconomicPlan
    from app.services.financial_scope_service import FinancialScopeService
    session = migrator_session
    ids = await financial_fixture(session, scope_seed)
    await session.execute(text("INSERT INTO bank_account(organization_id,property_id,account_type,label) VALUES (:org,:property,'LAUFEND','Unclassified input')"), ids)
    await actor(session, ids)
    subject = await session.get(EconomicPlan, ids['plan'])
    with pytest.raises(HTTPException) as incomplete:
        await FinancialScopeService(session, ids['org'], ids['clerk']).enter(subject, computation=True)
    assert incomplete.value.status_code == 409
    assert 'unclassified financial inputs' in incomplete.value.detail
    await session.execute(text('RESET ROLE'))


async def test_partial_scope_filters_units_and_dependent_master_rows(migrator_session, scope_seed):
    session = migrator_session
    ids = await scope_fixture(session, scope_seed)
    ids.update(partial=uuid.uuid4(), unit=uuid.uuid4(), excluded=uuid.uuid4())
    for key in ('unit', 'excluded'):
        await session.execute(text("INSERT INTO unit(id,organization_id,property_id,number,usage_type) VALUES (:id,:org,:property,:number,'WOHNUNG')"), {**ids, 'id': ids[key], 'number': key})
    await session.execute(text("INSERT INTO accounting_scope(id,organization_id,property_id,scope_type,label,valid_from,whole_property,origin,created_by) VALUES (:partial,:org,:property,'MIET','Partial rental','2025-01-01',false,'REVIEWED',:actor)"), ids)
    await session.execute(text('INSERT INTO accounting_scope_unit(organization_id,scope_id,unit_id,property_id) VALUES (:org,:partial,:unit,:property)'), ids)
    await session.execute(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES (:org,:partial,:clerk,ARRAY['read','prepare'],:actor,'Reviewed partial sphere')"), ids)
    await actor(session, ids)
    await session.execute(text("SELECT set_config('app.current_scope_id',:scope,true)"), {'scope': str(ids['partial'])})
    assert (await session.execute(text('SELECT id FROM unit WHERE property_id=:property'), ids)).scalars().all() == [ids['unit']]
    await session.execute(text('RESET ROLE'))


async def test_source_download_checks_current_grant_and_revocation(migrator_session, worker_tenant, tmp_path):
    import httpx
    from app.core.config import settings
    from app.core.security import create_session_token
    from app.core.storage import LocalFsDocumentStore, set_store
    from app.main import app
    client_admin, seed = worker_tenant
    seed = {**seed, 'user_a_clerk_id': uuid.uuid4()}
    session = migrator_session
    await session.execute(text("""INSERT INTO "user" (id,organization_id,email,password_hash,display_name) VALUES (:clerk,:org,:email,'fixture','Scope clerk')"""),
        {'clerk': seed['user_a_clerk_id'], 'org': seed['org_a_id'], 'email': str(seed['user_a_clerk_id'])+'@scope-test.invalid'})
    await session.execute(text("INSERT INTO membership(organization_id,user_id,role) VALUES (:org,:clerk,'CLERK')"),
        {'clerk': seed['user_a_clerk_id'], 'org': seed['org_a_id']})
    ids = await financial_fixture(session, seed)
    await session.execute(text('UPDATE scope_grant SET revoked_at=now(),revoked_by=:actor WHERE id=:grant'), ids)
    ids['grant'] = uuid.uuid4()
    await session.execute(text("INSERT INTO scope_grant(id,organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES(:grant,:org,:scope,:clerk,ARRAY['read','export'],:actor,'Explicit original export')"), ids)
    await session.commit()
    store = LocalFsDocumentStore(str(tmp_path))
    store.put('synthetic/scoped.pdf', b'test', content_type='application/pdf')
    set_store(store)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                cookies={settings.session_cookie_name: create_session_token(ids['clerk'], ids['org'])}) as client_clerk:
            url = f"/api/invoice-documents/{ids['document']}/content"
            assert (await client_admin.get(url)).status_code == 404
            response = await client_clerk.get(url)
            assert response.status_code == 200 and response.content == b'test'
            revoked = await client_admin.post(f"/api/accounting-scopes/{ids['scope']}/grants/{ids['grant']}/revoke")
            assert revoked.status_code == 204
            assert (await client_clerk.get(url)).status_code == 404
    finally:
        set_store(None)


from tests.test_invoice_extraction_worker import worker_tenant  # dedicated queue fixture


@pytest.mark.parametrize('phase', ['before_claim', 'before_finish'])
@pytest.mark.parametrize('change', ['revoke', 'deactivate', 'role'])
async def test_invoice_job_rechecks_persisted_principal(migrator_session, worker_tenant, phase, change):
    from sqlalchemy.ext.asyncio import create_async_engine
    from app.core.db import get_worker_session_factory, set_tenant_context
    from app.services.invoice_processing_service import InvoiceProcessingService
    from tests.conftest import MIGRATOR_URL
    _, seed = worker_tenant
    seed = {**seed, 'user_a_clerk_id': seed['user_a_admin_id']}
    session = migrator_session
    ids = await financial_fixture(session, seed)
    ids['attempt'] = uuid.uuid4()
    await session.execute(text("INSERT INTO invoice_processing_attempt(id,organization_id,document_id,attempt_no,status,initiated_by,requested_scope_id) VALUES (:attempt,:org,:document,1,'queued',:clerk,:scope)"), ids)
    await session.execute(text("UPDATE invoice_document SET processing_status='queued' WHERE id=:document"), ids)
    await session.commit()
    claim = None
    if phase == 'before_finish':
        async with get_worker_session_factory()() as worker, worker.begin():
            await set_tenant_context(worker, ids['org'])
            claim = await InvoiceProcessingService(worker, ids['org']).claim()
            assert claim is not None
    engine = create_async_engine(MIGRATOR_URL)
    try:
        async with engine.begin() as connection:
            command = {
                'revoke': 'UPDATE scope_grant SET revoked_at=now(),revoked_by=:actor WHERE id=:grant',
                'deactivate': 'UPDATE "user" SET is_active=false WHERE id=:actor',
                'role': "UPDATE membership SET role='READ_ONLY' WHERE organization_id=:org AND user_id=:actor",
            }[change]
            await connection.execute(text(command), ids)
        async with get_worker_session_factory()() as worker, worker.begin():
            await set_tenant_context(worker, ids['org'])
            service = InvoiceProcessingService(worker, ids['org'])
            if phase == 'before_claim':
                assert await service.claim() is None  # no URI/bytes supplied to the processor
            else:
                assert not await service.finish(claim, {'status': 'succeeded', 'payload': {'gross_amount': '999.00'}})
        async with engine.begin() as connection:
            document = (await connection.execute(text('SELECT processing_status,processing_error_code,extraction_payload FROM invoice_document WHERE id=:document'), ids)).one()
            attempt = (await connection.execute(text('SELECT status,error_code FROM invoice_processing_attempt WHERE id=:attempt'), ids)).one()
            assert document.processing_status == attempt.status == 'canceled'
            assert document.processing_error_code == attempt.error_code == 'scope_permission_changed'
            assert document.extraction_payload is None
            assert await connection.scalar(text("SELECT count(*) FROM audit_event WHERE entity_id=:document AND action='invoice.processing_canceled'"), ids) == 1
    finally:
        await engine.dispose()


async def test_old_cancellation_cannot_override_newer_invoice_attempt(migrator_session, scope_seed):
    session = migrator_session
    ids = await financial_fixture(session, scope_seed)
    await session.execute(text("""INSERT INTO invoice_processing_attempt(organization_id,document_id,attempt_no,status,
      initiated_by,requested_scope_id,error_code) VALUES
      (:org,:document,1,'canceled',:clerk,:scope,'scope_permission_changed'),
      (:org,:document,2,'succeeded',:clerk,:scope,NULL)"""), ids)
    await session.execute(text("UPDATE invoice_document SET processing_status='succeeded' WHERE id=:document"), ids)
    await session.execute(text('UPDATE scope_grant SET revoked_at=now(),revoked_by=:actor WHERE id=:grant'), ids)
    # The definer cancellation function bypasses row visibility. Exercise its
    # underlying trigger at the same privilege level with the revoked actor.
    await session.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:clerk,true)"),
        {'org': str(ids['org']), 'clerk': str(ids['clerk'])})
    with pytest.raises(DBAPIError) as denied:
        async with session.begin_nested():
            await session.execute(text("""UPDATE invoice_document SET processing_status='canceled',
              processing_error_code='scope_permission_changed' WHERE id=:document"""), ids)
    assert 'scope capability' in str(denied.value).lower()
    assert await session.scalar(text('SELECT processing_status FROM invoice_document WHERE id=:document'), ids) == 'succeeded'


@pytest.mark.parametrize('field,value', [('initiated_by','actor'), ('requested_scope_id','other_scope')])
async def test_invoice_attempt_principal_is_immutable(migrator_session, scope_seed, field, value):
    session = migrator_session
    ids = await financial_fixture(session, scope_seed)
    ids['attempt'] = uuid.uuid4()
    await session.execute(text("""INSERT INTO invoice_processing_attempt(id,organization_id,document_id,attempt_no,status,
      initiated_by,requested_scope_id) VALUES (:attempt,:org,:document,1,'queued',:clerk,:scope)"""), ids)
    with pytest.raises(DBAPIError) as denied:
        async with session.begin_nested():
            await session.execute(text(f'UPDATE invoice_processing_attempt SET {field}=:replacement WHERE id=:attempt'),
                {**ids, 'replacement': ids[value]})
    assert 'principal and scope are immutable' in str(denied.value).lower()


async def test_statement_cannot_treat_hidden_scoped_money_as_zero(migrator_session, scope_seed):
    from fastapi import HTTPException
    from app.services.payment_service import PaymentService
    from datetime import date
    session = migrator_session
    ids = await financial_fixture(session, scope_seed)
    ids.update(unit=uuid.uuid4(), person=uuid.uuid4(), tenant=uuid.uuid4(), lease=uuid.uuid4())
    await session.execute(text("INSERT INTO unit(id,organization_id,property_id,number,usage_type) VALUES (:unit,:org,:property,'1','WOHNUNG')"), ids)
    await session.execute(text("INSERT INTO person(id,organization_id,first_name,last_name,street,zip_code,city) VALUES (:person,:org,'Scope','Tenant','','','')"), ids)
    await session.execute(text("INSERT INTO tenant_profile(id,organization_id,person_id,notes) VALUES (:tenant,:org,:person,'')"), ids)
    await session.execute(text("""INSERT INTO lease_agreement(id,organization_id,unit_id,tenant_profile_id,valid_from,base_rent,advance_payment,notes)
      VALUES (:lease,:org,:unit,:tenant,'2025-01-01',500,100,'')"""), ids)
    await actor(session, ids, 'actor')  # administrator without financial grants
    service = PaymentService(session, ids['org'], ids['actor'])
    from app.services.fristen_service import FristenService
    with pytest.raises(HTTPException) as monitor_denied:
        await FristenService(session, ids['org']).build_monitor(date(2025,12,31))
    assert monitor_denied.value.status_code == 409
    with pytest.raises(HTTPException) as denied:
        await service.account_statement_for_lease(ids['lease'], date(2025,1,1), date(2025,12,31))
    assert denied.value.status_code == 409
    await session.execute(text('RESET ROLE'))


@pytest.mark.parametrize('operation', ['status', 'request_deletion', 'anonymize', 'export_person_data'])
async def test_privacy_cannot_erase_or_export_incomplete_retention(migrator_session, scope_seed, operation):
    from fastapi import HTTPException
    from app.services.dsgvo_service import DsgvoService
    session = migrator_session
    ids = await financial_fixture(session, scope_seed)
    ids.update(person=uuid.uuid4(), vendor=uuid.uuid4())
    await session.execute(text("INSERT INTO person(id,organization_id,first_name,last_name,street,zip_code,city) VALUES (:person,:org,'Retained','Vendor','','','')"), ids)
    await session.execute(text("INSERT INTO vendor(id,organization_id,person_id,notes) VALUES (:vendor,:org,:person,'')"), ids)
    await session.execute(text("UPDATE cost_entry SET vendor_id=:vendor,status='confirmed',confirmed_at=now(),confirmed_by=:actor WHERE id=:cost"), ids)
    await actor(session, ids, 'actor')
    service = DsgvoService(session, ids['org'], ids['actor'])
    with pytest.raises(HTTPException) as denied:
        await getattr(service, operation)(ids['person'])
    assert denied.value.status_code == 409
    assert await session.scalar(text('SELECT anonymized_at FROM person WHERE id=:person'), ids) is None
    if operation == 'status':
        await session.execute(text("SELECT set_config('app.current_user_id',:clerk,true)"), {'clerk': str(ids['clerk'])})
        clerk = DsgvoService(session, ids['org'], ids['clerk'])
        state = await clerk.status(ids['person'])
        assert state['is_bound'] and len(state['bindings']) == 1
        # Read permission never implies export permission.
        with pytest.raises(HTTPException) as export_denied:
            await clerk.export_person_data(ids['person'])
        assert export_denied.value.status_code == 409
        await session.execute(text('RESET ROLE'))
        await session.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {'actor': str(ids['actor'])})
        await session.execute(text('UPDATE scope_grant SET revoked_at=now(),revoked_by=:actor WHERE id=:grant'), ids)
        await session.execute(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES (:org,:scope,:clerk,ARRAY['read','export'],:actor,'Reviewed privacy export')"), ids)
        await actor(session, ids)
        exported = await clerk.export_person_data(ids['person'])
        assert len(exported['retention_bindings']) == 1
    await session.execute(text('RESET ROLE'))


async def test_plan_payment_derivation_enters_exact_scope_before_summing(migrator_session, scope_seed):
    from decimal import Decimal
    from fastapi import HTTPException
    from app.services.weg_settlement_service import WegSettlementService
    from app.services.payment_service import PaymentService
    session = migrator_session
    ids = await scope_fixture(session, scope_seed)
    ids.update(person=uuid.uuid4(), owner=uuid.uuid4(), outside_owner=uuid.uuid4(), outside_unit=uuid.uuid4(), plan=uuid.uuid4(), second_scope=uuid.uuid4(), second_bank=uuid.uuid4())
    await session.execute(text("INSERT INTO person(id,organization_id,first_name,last_name,street,zip_code,city) VALUES (:person,:org,'Shared','Owner','','','')"), ids)
    await session.execute(text("INSERT INTO owner_profile(id,organization_id,person_id,notes) VALUES (:owner,:org,:person,''),(:outside_owner,:org,:person,'Separate profile outside scope')"), ids)
    await session.execute(text("INSERT INTO unit(id,organization_id,property_id,number,usage_type) VALUES (:outside_unit,:org,:other_property,'Outside','WOHNUNG')"), ids)
    await session.execute(text("INSERT INTO ownership_history(organization_id,unit_id,owner_profile_id,valid_from) VALUES (:org,:outside_unit,:outside_owner,'2025-01-01')"), ids)
    await session.execute(text("INSERT INTO accounting_scope(id,organization_id,property_id,scope_type,label,valid_from,whole_property,origin,created_by) VALUES (:second_scope,:org,:other_property,'WEG','Second WEG','2025-01-01',true,'REVIEWED',:actor)"), ids)
    await session.execute(text("INSERT INTO bank_account(id,organization_id,property_id,account_type,label) VALUES (:second_bank,:org,:other_property,'LAUFEND','Second bank')"), ids)
    for bank, prop, sphere, amount in [('bank','property','scope',100), ('second_bank','other_property','second_scope',900)]:
        params = {**ids, 'pbank': ids[bank], 'prop': ids[prop], 'sphere': ids[sphere], 'amount': amount, 'payment': uuid.uuid4()}
        await session.execute(text("""INSERT INTO payment(id,organization_id,property_id,bank_account_id,amount,direction,value_date,payment_type,owner_profile_id,period_year,status)
          VALUES (:payment,:org,:prop,:pbank,:amount,'IN','2025-06-01','HAUSGELD_VORSCHUSS',:owner,2025,'booked')"""), params)
        await session.execute(text("INSERT INTO accounting_scope_binding(organization_id,property_id,scope_id,bank_account_id,origin,reason,reviewed_by) VALUES (:org,:prop,:sphere,:pbank,'REVIEWED','Reviewed bank',:actor)"), params)
        await session.execute(text("INSERT INTO accounting_scope_binding(organization_id,property_id,scope_id,payment_id,payment_bank_account_id,origin,reason,reviewed_by) VALUES (:org,:prop,:sphere,:payment,:pbank,'REVIEWED','Reviewed payment',:actor)"), params)
    await session.execute(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES (:org,:second_scope,:clerk,ARRAY['read','prepare'],:actor,'Other WEG')"), ids)
    await session.execute(text("INSERT INTO economic_plan(id,organization_id,property_id,plan_year,period_start,period_end,status,engine_version) VALUES (:plan,:org,:property,2025,'2025-01-01','2025-12-31','draft','test')"), ids)
    await session.execute(text("INSERT INTO economic_plan_line(organization_id,economic_plan_id,line_type,owner_profile_id,label,planned_amount,monthly_amount,sort_order) VALUES (:org,:plan,'OWNER_ADVANCE',:owner,'Owner advance',1200,100,1)"), ids)
    await session.execute(text("INSERT INTO accounting_scope_binding(organization_id,property_id,scope_id,economic_plan_id,origin,reason,reviewed_by) VALUES (:org,:property,:scope,:plan,'REVIEWED','Reviewed plan',:actor)"), ids)
    await actor(session, ids)
    with pytest.raises(HTTPException) as ambiguous:
        await PaymentService(session, ids['org'], ids['clerk']).derive_hausgeld_total(ids['owner'], 2025)
    assert ambiguous.value.status_code == 409
    result = await WegSettlementService(session, ids['org'], ids['clerk']).derive_paid_advances(ids['plan'], 2025)
    assert len(result) == 1 and result[0].paid_total == Decimal('100.00')
    with pytest.raises(HTTPException) as outside:
        await PaymentService(session, ids['org'], ids['clerk']).derive_hausgeld_total(ids['outside_owner'], 2025)
    assert outside.value.status_code == 409
    await session.execute(text('RESET ROLE'))
