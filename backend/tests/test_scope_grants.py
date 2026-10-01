"""Scope foundation: exact grants and immutable, tenant-coherent money bindings."""
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.asyncio


async def scope_fixture(session, seed):
    ids = {key: uuid.uuid4() for key in ("property", "other_property", "scope", "other_scope", "bank", "grant")}
    ids.update(org=seed["org_a_id"], actor=seed["user_a_admin_id"], clerk=seed["user_a_clerk_id"])
    for key in ("property", "other_property"):
        await session.execute(text("INSERT INTO property(id,organization_id,name,street,zip_code,city,management_type) VALUES (:id,:org,'Test','','','','MISCH')"), {**ids, "id": ids[key]})
    for key, kind in (("scope", "WEG"), ("other_scope", "MIET")):
        await session.execute(text("INSERT INTO accounting_scope(id,organization_id,property_id,scope_type,label,valid_from,whole_property,origin,created_by) VALUES (:id,:org,:property,:kind,'Reviewed sphere','2025-01-01',true,'REVIEWED',:actor)"), {**ids, "id": ids[key], "kind": kind})
    await session.execute(text("INSERT INTO bank_account(id,organization_id,property_id,account_type,label) VALUES (:bank,:org,:property,'LAUFEND','Test')"), ids)
    await session.execute(text("INSERT INTO scope_grant(id,organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES (:grant,:org,:scope,:clerk,ARRAY['read','prepare'],:actor,'Reviewed team assignment')"), ids)
    return ids


async def bind(session, ids, **override):
    await session.execute(text("INSERT INTO accounting_scope_binding(organization_id,property_id,scope_id,bank_account_id,origin,reason,reviewed_by) VALUES (:org,:property,:scope,:bank,'REVIEWED','Reviewed original',:actor)"), {**ids, **override})


async def test_one_canonical_binding_and_immutable_evidence(migrator_session, seed_data):
    s = migrator_session
    ids = await scope_fixture(s, seed_data)
    await bind(s, ids)
    for statement, params in [
        ("INSERT INTO accounting_scope_binding(organization_id,property_id,scope_id,bank_account_id,origin,reason) VALUES (:org,:property,:other_scope,:bank,'PROVEN_LEGACY','Duplicate')", ids),
        ("UPDATE accounting_scope_binding SET scope_id=:other_scope WHERE bank_account_id=:bank", ids),
        ("DELETE FROM accounting_scope_binding WHERE bank_account_id=:bank", ids),
    ]:
        with pytest.raises(DBAPIError):
            async with s.begin_nested():
                await s.execute(text(statement), params)


async def test_binding_cannot_cross_property_or_tenant(migrator_session, seed_data):
    s = migrator_session
    ids = await scope_fixture(s, seed_data)
    for override in ({"property": ids["other_property"]}, {"org": seed_data["org_b_id"]}):
        with pytest.raises(DBAPIError):
            async with s.begin_nested():
                await bind(s, ids, **override)
                await s.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))


async def test_grant_can_only_be_revoked_once(migrator_session, seed_data):
    s = migrator_session
    ids = await scope_fixture(s, seed_data)
    for statement in ("UPDATE scope_grant SET capabilities=ARRAY['read','approve'] WHERE id=:grant", "DELETE FROM scope_grant WHERE id=:grant"):
        with pytest.raises(DBAPIError):
            async with s.begin_nested():
                await s.execute(text(statement), ids)
    await s.execute(text("UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE id=:grant"), ids)
    with pytest.raises(DBAPIError):
        async with s.begin_nested():
            await s.execute(text("UPDATE scope_grant SET revoked_at=NULL,revoked_by=NULL WHERE id=:grant"), ids)


async def test_permission_uses_exact_scope_and_live_grant(migrator_session, seed_data):
    s = migrator_session
    ids = await scope_fixture(s, seed_data)
    await s.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:actor,true)"), {"org": str(ids["org"]), "actor": str(ids["clerk"])})
    async def allowed(scope, capability):
        return await s.scalar(text("SELECT has_scope_capability(:org,:scope,:capability)"), {"org": ids["org"], "scope": scope, "capability": capability})
    assert await allowed(ids["scope"], "prepare") is True
    assert await allowed(ids["scope"], "approve") is False
    assert await allowed(ids["other_scope"], "read") is False
    await s.execute(text("UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE id=:grant"), ids)
    assert await allowed(ids["scope"], "read") is False


@pytest.mark.parametrize("capabilities", [[], ["approve"], ["read", "superuser"]])
async def test_invalid_capabilities_rejected(migrator_session, seed_data, capabilities):
    s = migrator_session
    ids = await scope_fixture(s, seed_data)
    with pytest.raises(DBAPIError):
        async with s.begin_nested():
            await s.execute(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES (:org,:other_scope,:clerk,:caps,:actor,'Test')"), {**ids, "caps": capabilities})


async def test_payment_binding_must_follow_its_exact_bank_scope(migrator_session, seed_data):
    s = migrator_session
    ids = await scope_fixture(s, seed_data)
    # Bind the account to the rental sphere. A payment cannot claim the WEG sphere.
    ids.update(person=uuid.uuid4(), tenant=uuid.uuid4(), payment=uuid.uuid4())
    await s.execute(text("INSERT INTO person(id,organization_id,first_name,last_name,street,zip_code,city) VALUES (:person,:org,'Test','Tenant','','','')"), ids)
    await s.execute(text("INSERT INTO tenant_profile(id,organization_id,person_id,notes) VALUES (:tenant,:org,:person,'')"), ids)
    await s.execute(text("INSERT INTO payment(id,organization_id,property_id,bank_account_id,amount,direction,value_date,payment_type,tenant_profile_id) VALUES (:payment,:org,:property,:bank,10,'IN','2025-01-01','KALTMIETE',:tenant)"), ids)
    await bind(s, ids, scope=ids["other_scope"])
    statement = text("INSERT INTO accounting_scope_binding(organization_id,property_id,scope_id,payment_id,payment_bank_account_id,origin,reason,reviewed_by) VALUES (:org,:property,:scope,:payment,:bank,'REVIEWED','Verified account',:actor)")
    with pytest.raises(DBAPIError):
        async with s.begin_nested():
            await s.execute(statement, ids)
            await s.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    await s.execute(statement, {**ids, "scope": ids["other_scope"]})
    await s.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    await s.execute(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES (:org,:other_scope,:clerk,ARRAY['read','prepare'],:actor,'Date review')"), ids)
    await s.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:actor,true)"), {"org": str(ids['org']), "actor": str(ids['clerk'])})
    with pytest.raises(DBAPIError) as out_of_period:
        async with s.begin_nested():
            await s.execute(text("UPDATE payment SET value_date='2024-12-31' WHERE id=:payment"), ids)
            await s.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    assert 'Financial period is outside' in str(out_of_period.value)


async def test_database_denies_self_grants_and_hides_other_spheres(migrator_session, seed_data):
    s = migrator_session
    ids = await scope_fixture(s, seed_data)
    await s.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:actor,true)"), {"org": str(ids["org"]), "actor": str(ids["clerk"])})
    await s.execute(text("SET LOCAL ROLE app_rw"))
    visible = (await s.execute(text("SELECT id FROM accounting_scope WHERE property_id=:property"), ids)).scalars().all()
    assert visible == [ids["scope"]]
    with pytest.raises(DBAPIError):
        async with s.begin_nested():
            await s.execute(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES (:org,:other_scope,:clerk,ARRAY['read','approve'],:clerk,'Self grant')"), ids)
    await s.execute(text("RESET ROLE"))


async def test_readonly_and_expired_grants_never_authorize_effects(migrator_session, seed_data):
    s = migrator_session
    ids = await scope_fixture(s, seed_data)
    ids["reader"] = seed_data["user_a_readonly_id"]
    await s.execute(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES (:org,:scope,:reader,ARRAY['read','prepare'],:actor,'Reader remains read only')"), ids)
    await s.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:actor,true)"), {"org": str(ids["org"]), "actor": str(ids["reader"])})
    assert await s.scalar(text("SELECT has_scope_capability(:org,:scope,'read')"), ids)
    assert not await s.scalar(text("SELECT has_scope_capability(:org,:scope,'prepare')"), ids)
    await s.execute(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason,created_at,expires_at) VALUES (:org,:other_scope,:reader,ARRAY['read'],:actor,'Expired',now()-interval '2 days',now()-interval '1 day')"), ids)
    assert not await s.scalar(text("SELECT has_scope_capability(:org,:other_scope,'read')"), ids)
    await s.execute(text("SELECT set_config('app.current_user_id','',true)"))
    assert not await s.scalar(text("SELECT has_scope_capability(:org,:scope,'read')"), ids)


async def test_service_administration_does_not_imply_money_permission(migrator_session, seed_data):
    from fastapi import HTTPException
    from app.core.rbac import ScopeCapability
    from app.core.tenant import AuthContext
    from app.services.authorization_service import AuthorizationService
    s = migrator_session
    ids = await scope_fixture(s, seed_data)
    await s.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:actor,true)"), {"org": str(ids["org"]), "actor": str(ids["actor"])})
    await s.execute(text("SET LOCAL ROLE app_rw"))
    service = AuthorizationService(s, AuthContext(ids["actor"], ids["org"], "OWNER_ADMIN"))
    with pytest.raises(HTTPException) as error:
        await service.require(ids["scope"], ScopeCapability.APPROVE)
    assert error.value.status_code == 404
    grant = await service.grant(ids["scope"], ids["actor"], {ScopeCapability.READ, ScopeCapability.APPROVE}, "Explicit reviewed finance permission")
    assert (await service.require(ids["scope"], ScopeCapability.APPROVE)).id == ids["scope"]
    await service.revoke(grant.id)
    await service.revoke(grant.id)
    with pytest.raises(HTTPException):
        await service.require(ids["scope"], ScopeCapability.APPROVE)
    assert await s.scalar(text("SELECT count(*) FROM audit_event WHERE entity_id=:id"), {"id": grant.id}) == 2
    await s.execute(text("RESET ROLE"))


async def test_bound_account_identity_cannot_be_rewritten(migrator_session, seed_data):
    s = migrator_session
    ids = await scope_fixture(s, seed_data)
    await bind(s, ids)
    await s.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    with pytest.raises(DBAPIError):
        async with s.begin_nested():
            await s.execute(text("UPDATE bank_account SET account_type='KAUTION' WHERE id=:bank"), ids)
    # Presentation metadata can still be corrected.
    await s.execute(text("UPDATE bank_account SET label='Corrected label' WHERE id=:bank"), ids)


async def test_incompatible_account_type_rejected_at_commit_boundary(migrator_session, seed_data):
    s = migrator_session
    ids = await scope_fixture(s, seed_data)
    await s.execute(text("UPDATE bank_account SET account_type='KAUTION' WHERE id=:bank"), ids)
    with pytest.raises(DBAPIError):
        async with s.begin_nested():
            await bind(s, ids)
            await s.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))


async def test_mandate_type_and_dates_define_scope(migrator_session, seed_data):
    s = migrator_session
    ids = await scope_fixture(s, seed_data)
    for kind, start in (("MIET", "2025-01-01"), ("WEG", "2024-12-31")):
        with pytest.raises(DBAPIError):
            async with s.begin_nested():
                await s.execute(text("INSERT INTO accounting_scope(organization_id,property_id,management_mandate_id,scope_type,label,valid_from,whole_property,origin,created_by) VALUES (:org,:property,:mandate,:kind,'Invalid',CAST(:start AS date),true,'REVIEWED',:actor)"), {**ids, "mandate": seed_data["mandate_a_id"], "kind": kind, "start": start})


async def test_empty_sev_binding_fails_without_poisoning_unit_review(migrator_session, seed_data):
    s = migrator_session
    ids = await scope_fixture(s, seed_data)
    ids.update(sev=uuid.uuid4(), unit=uuid.uuid4(), person=uuid.uuid4(), owner=uuid.uuid4())
    await s.execute(text("INSERT INTO accounting_scope(id,organization_id,property_id,scope_type,label,valid_from,whole_property,origin,created_by) VALUES (:sev,:org,:property,'SEV','Reviewed unit set','2025-01-01',false,'REVIEWED',:actor)"), ids)
    with pytest.raises(DBAPIError):
        async with s.begin_nested():
            await bind(s, ids, scope=ids['sev'])
            await s.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    await s.execute(text("INSERT INTO unit(id,organization_id,property_id,number,location,usage_type) VALUES (:unit,:org,:property,'1','','WOHNUNG')"), ids)
    await s.execute(text("INSERT INTO person(id,organization_id,first_name,last_name,street,zip_code,city) VALUES (:person,:org,'Test','Owner','','','')"), ids)
    await s.execute(text("INSERT INTO owner_profile(id,organization_id,person_id,notes) VALUES (:owner,:org,:person,'')"), ids)
    await s.execute(text("INSERT INTO accounting_scope_unit(organization_id,scope_id,property_id,unit_id,owner_profile_id) VALUES (:org,:sev,:property,:unit,:owner)"), ids)
    await bind(s, ids, scope=ids['sev'])
    await s.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    # The now-used unit set cannot be extended under an existing grant/binding.
    with pytest.raises(DBAPIError):
        async with s.begin_nested():
            await s.execute(text("INSERT INTO accounting_scope_unit(organization_id,scope_id,property_id,unit_id,owner_profile_id) VALUES (:org,:sev,:property,:unit,:owner)"), ids)


async def test_payment_service_inherits_mandate_and_reversal_scope(migrator_session, seed_data):
    from datetime import date
    from decimal import Decimal
    from app.services.payment_service import PaymentService
    s = migrator_session
    ids = await scope_fixture(s, seed_data)
    ids.update(mandate=uuid.uuid4(), rental=uuid.uuid4(), person=uuid.uuid4(), tenant=uuid.uuid4())
    await s.execute(text("INSERT INTO management_mandate(id,organization_id,mandate_type,label,start_date) VALUES (:mandate,:org,'MIET','Rental mandate','2025-01-01')"), ids)
    await s.execute(text("INSERT INTO accounting_scope(id,organization_id,property_id,management_mandate_id,scope_type,label,valid_from,whole_property,origin,created_by) VALUES (:rental,:org,:property,:mandate,'MIET','Rental','2025-01-01',true,'REVIEWED',:actor)"), ids)
    await s.execute(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES (:org,:rental,:actor,ARRAY['read','prepare','confirm'],:actor,'Reviewed operator')"), ids)
    await bind(s, ids, scope=ids['rental'])
    await s.execute(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES (:org,:rental,:clerk,ARRAY['read','prepare'],:actor,'Prepare only')"), ids)
    await s.execute(text("INSERT INTO person(id,organization_id,first_name,last_name,street,zip_code,city) VALUES (:person,:org,'Test','Tenant','','','')"), ids)
    await s.execute(text("INSERT INTO tenant_profile(id,organization_id,person_id,notes) VALUES (:tenant,:org,:person,'')"), ids)
    await s.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:actor,true)"), {"org": str(ids['org']), "actor": str(ids['actor'])})
    await s.execute(text("SET LOCAL ROLE app_rw"))
    service = PaymentService(s, ids['org'], ids['actor'])
    payment = await service.create_draft(bank_account_id=ids['bank'], direction='IN', amount=Decimal('10'), value_date=date(2025, 2, 1), payment_type='KALTMIETE', tenant_profile_id=ids['tenant'])
    assert payment.management_mandate_id == ids['mandate']
    await service.book(payment.id)
    reversal = await service.reverse_payment(payment.id)
    assert reversal.management_mandate_id == ids['mandate']
    for record in (payment, reversal):
        assert await s.scalar(text("SELECT scope_id FROM accounting_scope_binding WHERE payment_id=:id"), {"id": record.id}) == ids['rental']
    await s.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {"actor": str(ids['clerk'])})
    clerk_service = PaymentService(s, ids['org'], ids['clerk'])
    draft = await clerk_service.create_draft(bank_account_id=ids['bank'], direction='IN', amount=Decimal('10'), value_date=date(2025, 2, 1), payment_type='KALTMIETE', tenant_profile_id=ids['tenant'])
    draft_id = draft.id
    with pytest.raises(DBAPIError):
        async with s.begin_nested():
            await clerk_service.book(draft_id)
    assert await s.scalar(text("SELECT status FROM payment WHERE id=:id"), {"id": draft_id}) == 'draft'
    await clerk_service.delete_draft(draft_id)
    await clerk_service.delete_draft(draft_id)
    assert await s.scalar(text("SELECT cancelled_at IS NOT NULL FROM payment WHERE id=:id"), {"id": draft_id})
    assert await s.scalar(text("SELECT count(*) FROM accounting_scope_binding WHERE payment_id=:id"), {"id": draft_id}) == 1
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:
        await clerk_service.book(draft_id)
    assert error.value.status_code == 409
    with pytest.raises(DBAPIError):
        async with s.begin_nested():
            await s.execute(text("UPDATE payment SET status='booked' WHERE id=:id"), {"id": draft_id})
    from app.api.payments import _to_out
    assert _to_out(await clerk_service._repo.get_by_id(draft_id)).status == 'cancelled'
    await s.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    await s.execute(text("RESET ROLE"))


async def test_revocation_orders_against_inflight_effect(migrator_session, seed_data):
    from fastapi import HTTPException
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from app.core.rbac import ScopeCapability
    from app.core.tenant import AuthContext
    from app.services.authorization_service import AuthorizationService
    from tests.conftest import APP_RW_URL
    ids = await scope_fixture(migrator_session, seed_data)
    await migrator_session.commit()
    engine = create_async_engine(APP_RW_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async def actor(session, who):
        await session.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:actor,true)"), {"org": str(ids['org']), "actor": str(ids[who])})
        return AuthorizationService(session, AuthContext(ids[who], ids['org'], 'unused_cached_role'))
    try:
        async with factory() as effect, factory() as admin:
            async with effect.begin():
                service = await actor(effect, 'clerk')
                await service.require(ids['scope'], ScopeCapability.PREPARE)
                with pytest.raises(DBAPIError) as blocked:
                    async with admin.begin():
                        revoker = await actor(admin, 'actor')
                        await admin.execute(text("SET LOCAL lock_timeout='100ms'"))
                        await revoker.revoke(ids['grant'])
                assert 'lock timeout' in str(blocked.value)
            async with admin.begin():
                revoker = await actor(admin, 'actor')
                await revoker.revoke(ids['grant'])
            async with effect.begin():
                service = await actor(effect, 'clerk')
                with pytest.raises(HTTPException) as revoked:
                    await service.require(ids['scope'], ScopeCapability.PREPARE)
                assert revoked.value.status_code == 404
    finally:
        await engine.dispose()


async def test_reviewed_legacy_mandate_classification_keeps_booked_original(migrator_session, seed_data):
    from datetime import date
    from decimal import Decimal
    from app.services.payment_service import PaymentService
    s = migrator_session
    ids = await scope_fixture(s, seed_data)
    ids.update(mandate=uuid.uuid4(), rental=uuid.uuid4(), person=uuid.uuid4(), tenant=uuid.uuid4())
    await s.execute(text("INSERT INTO person(id,organization_id,first_name,last_name,street,zip_code,city) VALUES (:person,:org,'Test','Legacy tenant','','','')"), ids)
    await s.execute(text("INSERT INTO tenant_profile(id,organization_id,person_id,notes) VALUES (:tenant,:org,:person,'')"), ids)
    service = PaymentService(s, ids['org'], ids['actor'])
    original = await service.create_draft(bank_account_id=ids['bank'], direction='IN', amount=Decimal('10'), value_date=date(2025, 2, 1), payment_type='KALTMIETE', tenant_profile_id=ids['tenant'])
    await service.book(original.id)
    contrary = await service.create_draft(bank_account_id=ids['bank'], direction='IN', amount=Decimal('10'), value_date=date(2025, 2, 1), payment_type='KALTMIETE', tenant_profile_id=ids['tenant'])
    contrary.management_mandate_id = seed_data['mandate_a_id']
    await s.flush()
    await service.book(contrary.id)
    before = await s.scalar(text("SELECT to_jsonb(p) FROM payment p WHERE id=:id"), {"id": original.id})
    await s.execute(text("INSERT INTO management_mandate(id,organization_id,mandate_type,label,start_date) VALUES (:mandate,:org,'MIET','Reviewed legacy mandate','2025-01-01')"), ids)
    await s.execute(text("INSERT INTO accounting_scope(id,organization_id,property_id,management_mandate_id,scope_type,label,valid_from,whole_property,origin,created_by) VALUES (:rental,:org,:property,:mandate,'MIET','Legacy review','2025-01-01',true,'REVIEWED',:actor)"), ids)
    await bind(s, ids, scope=ids['rental'])
    with pytest.raises(DBAPIError) as mismatch:
        async with s.begin_nested():
            await s.execute(text("INSERT INTO accounting_scope_binding(organization_id,property_id,scope_id,payment_id,payment_bank_account_id,origin,reason,reviewed_by) VALUES (:org,:property,:rental,:payment,:bank,'REVIEWED','Contrary original mandate',:actor)"), {**ids, 'payment': contrary.id})
            await s.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    assert 'Binding mandate mismatch' in str(mismatch.value)
    await s.execute(text("INSERT INTO accounting_scope_binding(organization_id,property_id,scope_id,payment_id,payment_bank_account_id,origin,reason,reviewed_by) VALUES (:org,:property,:rental,:payment,:bank,'REVIEWED','Explicit classification of original with absent mandate',:actor)"), {**ids, 'payment': original.id})
    await s.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    after = await s.scalar(text("SELECT to_jsonb(p) FROM payment p WHERE id=:id"), {"id": original.id})
    assert before == after
    assert after['management_mandate_id'] is None
    # Resume normal request transaction mode after checking deferred constraints.
    await s.execute(text("SET CONSTRAINTS ALL DEFERRED"))
    await s.execute(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES (:org,:rental,:actor,ARRAY['read','prepare','confirm'],:actor,'Reviewed correction operator')"), ids)
    await s.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:actor,true)"), {"org": str(ids['org']), "actor": str(ids['actor'])})
    reversal = await service.reverse_payment(original.id)
    assert reversal.management_mandate_id == ids['mandate']
    assert original.management_mandate_id is None
    await s.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
