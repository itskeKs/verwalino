"""T32: persisted, scoped one-off owner obligations on the real test DB."""
import uuid
from datetime import date
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from tests.conftest import MIGRATOR_URL

from app.core.config import settings
from app.core.security import create_session_token
from app.engine.mietkonto import special_levy_expected_charges
from app.main import app
from tests.test_financial_scope_boundary import actor, scope_seed  # noqa: F401
from tests.test_invoice_extraction_worker import worker_tenant  # noqa: F401
from tests.test_scope_grants import scope_fixture, bind

pytestmark = pytest.mark.asyncio


def client(ids, who="clerk", *, raise_errors=True):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=raise_errors),
        base_url="http://test", cookies={settings.session_cookie_name: create_session_token(ids[who], ids["org"])})


def body(ids, **extra):
    return {"property_id": str(ids["property"]), "accounting_scope_id": str(ids["scope"]),
        "amount": "10000.00", "due_date": "2025-06-15", **extra}


def query(ids):
    return {"accounting_scope_id": str(ids["scope"])}


@pytest_asyncio.fixture
async def levy_db(migrator_session, scope_seed):
    await migrator_session.commit()
    engine = create_async_engine(MIGRATOR_URL)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            yield session
            await session.commit()
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def levy_fixture(levy_db, scope_seed):
    s = levy_db
    ids = await scope_fixture(s, scope_seed)
    await bind(s, ids)
    await s.execute(text("UPDATE scope_grant SET revoked_at=now(),revoked_by=:actor WHERE id=:grant"), ids)
    ids["grant"] = uuid.uuid4()
    await s.execute(text("INSERT INTO scope_grant(id,organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES (:grant,:org,:scope,:clerk,ARRAY['read','prepare','confirm'],:actor,'Levy and payment test capabilities')"), ids)
    ids["readonly"] = uuid.uuid4()
    await s.execute(text('INSERT INTO "user"(id,organization_id,email,password_hash,display_name) VALUES (:readonly,:org,:email,\'fixture\',\'Reader\')'), {**ids, "email": f"{ids['readonly']}@levy.invalid"})
    await s.execute(text("INSERT INTO membership(organization_id,user_id,role) VALUES (:org,:readonly,'READ_ONLY')"), ids)
    await s.execute(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES (:org,:scope,:readonly,ARRAY['read'],:actor,'Read levies')"), ids)
    ids["units"], ids["owners"], ids["histories"] = [], [], []
    for n, mea in enumerate([300, 300, 400]):
        unit, person, owner, history = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        params = {**ids, "unit": unit, "person": person, "owner": owner, "history": history, "mea": mea, "number": str(n + 1)}
        await s.execute(text("INSERT INTO unit(id,organization_id,property_id,number,usage_type,mea) VALUES (:unit,:org,:property,:number,'WOHNUNG',:mea)"), params)
        await s.execute(text("INSERT INTO person(id,organization_id,first_name,last_name,street,zip_code,city) VALUES (:person,:org,'Levy',:number,'','','')"), params)
        await s.execute(text("INSERT INTO owner_profile(id,organization_id,person_id,notes) VALUES (:owner,:org,:person,'')"), params)
        await s.execute(text("INSERT INTO ownership_history(id,organization_id,unit_id,owner_profile_id,valid_from) VALUES (:history,:org,:unit,:owner,'2024-01-01')"), params)
        ids["units"].append(unit)
        ids["owners"].append(owner)
        ids["histories"].append(history)
    await s.commit()
    return ids


async def create(api, ids, **overrides):
    response = await api.post("/api/special-levies", json=body(ids, **overrides))
    assert response.status_code == 201, response.text
    return response.json()


async def test_special_levy_route_requires_auth():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as api:
        assert (await api.get("/api/special-levies")).status_code == 401
        assert (await api.post("/api/special-levies", json={})).status_code == 401


async def test_golden_persist_get_readonly_and_audit(levy_db, levy_fixture):
    ids = levy_fixture
    async with client(ids) as api:
        levy = await create(api, ids)
        assert levy["amount"] == "10000.00"
        amounts = {a["owner_profile_id"]: a["amount"] for a in levy["allocations"]}
        assert [amounts[str(o)] for o in ids["owners"]] == ["3000.00", "3000.00", "4000.00"]
        assert sum(Decimal(a["amount"]) for a in levy["allocations"]) == Decimal("10000.00")
    before = await levy_db.scalar(text("SELECT count(*) FROM audit_event WHERE organization_id=:org"), ids)
    async with client(ids, "readonly") as api:
        for _ in range(2):
            result = await api.get("/api/special-levies", params=query(ids))
            assert result.status_code == 200, result.text
            assert result.json() == [levy]
        for filters in [{}, {"property_id": str(ids["property"])}, {**query(ids), "property_id": str(ids["property"])}]:
            result = await api.get("/api/special-levies", params=filters)
            assert result.status_code == 200, result.text
            assert result.json() == [levy]
        assert (await api.post("/api/special-levies", json=body(ids))).status_code == 403
    assert await levy_db.scalar(text("SELECT count(*) FROM audit_event WHERE organization_id=:org"), ids) == before
    assert await levy_db.scalar(text("SELECT count(*) FROM audit_event WHERE entity_type='special_levy' AND entity_id=:id AND action='special_levy.created'"), {"id": uuid.UUID(levy["id"])}) == 1


@pytest.mark.parametrize("amount", ["0", "-1", "0.001", "NaN", "Infinity", "-Infinity", "1000000000000.00", "not money"])
async def test_invalid_money_is_rejected(levy_fixture, amount):
    async with client(levy_fixture) as api:
        result = await api.post("/api/special-levies", json=body(levy_fixture, amount=amount))
        assert result.status_code == 422, result.text


@pytest.mark.parametrize("extra", [{"unknown": True}, {"due_date": "2025-02-30"}, {"due_date": "wrong"}, {"accounting_scope_id": None}])
async def test_invalid_shape_and_dates(levy_fixture, extra):
    async with client(levy_fixture) as api:
        assert (await api.post("/api/special-levies", json=body(levy_fixture, **extra))).status_code == 422


async def test_exact_cent_tie_and_no_mutation_endpoints(levy_db, levy_fixture):
    ids = levy_fixture
    await levy_db.execute(text("UPDATE unit SET mea=1 WHERE property_id=:property"), ids)
    await levy_db.commit()
    async with client(ids) as api:
        levy = await create(api, ids, amount="0.02")
        assert [a["amount"] for a in levy["allocations"]] == ["0.01", "0.01", "0.00"]
        assert sum(Decimal(a["amount"]) for a in levy["allocations"]) == Decimal("0.02")
        assert (await api.patch("/api/special-levies/" + levy["id"], json={})).status_code in (404, 405)
        assert (await api.delete("/api/special-levies/" + levy["id"])).status_code in (404, 405)


async def test_basis_is_frozen_after_mea_and_owner_history_changes(levy_db, levy_fixture):
    ids = levy_fixture
    async with client(ids) as api:
        levy = await create(api, ids)
    await levy_db.execute(text("UPDATE unit SET mea=999 WHERE id=:unit"), {"unit": ids["units"][0]})
    await levy_db.execute(text("UPDATE ownership_history SET valid_to='2025-06-15' WHERE id=:id"), {"id": ids["histories"][0]})
    await levy_db.commit()
    async with client(ids) as api:
        assert (await api.get("/api/special-levies", params=query(ids))).json() == [levy]
        result = await api.get(f"/api/payments/mietkonto/owner/{ids['owners'][0]}", params={**query(ids), "period_start": "2025-06-01", "period_end": "2025-06-30"})
        assert result.status_code == 200, result.text
        assert result.json()["soll_total"] == "3000.00"


async def test_owner_statement_without_plan_due_date_and_booked_payment(levy_db, levy_fixture):
    ids = levy_fixture
    async with client(ids) as api:
        await create(api, ids)
        url = f"/api/payments/mietkonto/owner/{ids['owners'][0]}"
        for start, end, expected in [("2025-01-01", "2025-12-31", "3000.00"), ("2025-06-01", "2025-06-14", "0.00"), ("2025-06-15", "2025-06-15", "3000.00"), ("2025-06-16", "2025-12-31", "0.00")]:
            response = await api.get(url, params={**query(ids), "period_start": start, "period_end": end})
            assert response.status_code == 200, response.text
            assert response.json()["soll_total"] == expected
        payment = await api.post("/api/payments", json={"bank_account_id": str(ids["bank"]), "amount": "1000.00", "direction": "IN", "value_date": "2025-06-15", "payment_type": "SONDERZAHLUNG", "owner_profile_id": str(ids["owners"][0]), "period_year": 2025, "period_month": 6})
        assert payment.status_code == 201, payment.text
        booked = await api.post(f"/api/payments/{payment.json()['id']}/book")
        assert booked.status_code == 200, booked.text
        response = await api.get(url, params={**query(ids), "period_start": "2025-01-01", "period_end": "2025-12-31"})
        assert response.json()["soll_total"] == "3000.00"
        assert response.json()["ist_total"] == "1000.00"
        assert response.json()["balance"] == "2000.00"


async def test_due_date_exclusive_ownership_end_and_coowners(levy_db, levy_fixture):
    ids = levy_fixture
    params = {**ids, "unit": ids["units"][0], "old": ids["histories"][0], "owner": ids["owners"][1], "other": ids["owners"][2]}
    await levy_db.execute(text("UPDATE ownership_history SET valid_to='2025-06-15' WHERE id=:old"), params)
    for owner, share in [(ids["owners"][1], 1), (ids["owners"][2], 3)]:
        await levy_db.execute(text("INSERT INTO ownership_history(organization_id,unit_id,owner_profile_id,valid_from,mea_share) VALUES (:org,:unit,:owner,'2025-06-15',:share)"), {**params, "owner": owner, "share": share})
    await levy_db.commit()
    async with client(ids) as api:
        levy = await create(api, ids)
        cells = [a for a in levy["allocations"] if a["unit_id"] == str(ids["units"][0])]
        assert {a["owner_profile_id"]: a["amount"] for a in cells} == {str(ids["owners"][1]): "750.00", str(ids["owners"][2]): "2250.00"}
        assert sum(Decimal(a["amount"]) for a in levy["allocations"]) == Decimal("10000.00")


@pytest.mark.parametrize("problem", ["missing_owner", "missing_mea", "missing_coowner_share"])
async def test_incomplete_basis_fails_without_rows_or_audit(levy_db, levy_fixture, problem):
    ids = levy_fixture
    if problem == "missing_owner":
        await levy_db.execute(text("UPDATE ownership_history SET valid_to='2025-01-01' WHERE id=:id"), {"id": ids["histories"][0]})
    elif problem == "missing_mea":
        await levy_db.execute(text("UPDATE unit SET mea=NULL WHERE id=:id"), {"id": ids["units"][0]})
    else:
        await levy_db.execute(text("INSERT INTO ownership_history(organization_id,unit_id,owner_profile_id,valid_from) VALUES (:org,:unit,:owner,'2025-01-01')"), {**ids, "unit": ids["units"][0], "owner": ids["owners"][1]})
    await levy_db.commit()
    async with client(ids) as api:
        result = await api.post("/api/special-levies", json=body(ids))
        assert result.status_code == 422, result.text
    assert await levy_db.scalar(text("SELECT count(*) FROM special_levy WHERE organization_id=:org"), ids) == 0
    assert await levy_db.scalar(text("SELECT count(*) FROM audit_event WHERE organization_id=:org AND entity_type='special_levy'"), ids) == 0


async def test_scope_roles_unknowns_and_revocation(levy_db, levy_fixture, client_b_admin):
    ids = levy_fixture
    async with client(ids) as api:
        assert (await api.get("/api/special-levies")).status_code == 200
        assert (await api.post("/api/special-levies", json=body(ids, property_id=str(uuid.uuid4())))).status_code == 404
        assert (await api.post("/api/special-levies", json=body(ids, property_id=str(ids["other_property"])))).status_code == 422
        assert (await api.post("/api/special-levies", json=body(ids, accounting_scope_id=str(uuid.uuid4())))).status_code == 404
        await create(api, ids)
    async with client(ids, "actor") as api:
        assert (await api.get("/api/special-levies", params=query(ids))).status_code == 404
    assert (await client_b_admin.get("/api/special-levies", params=query(ids))).status_code == 404
    await levy_db.execute(text("UPDATE scope_grant SET revoked_at=now(),revoked_by=:actor WHERE id=:grant"), ids)
    await levy_db.commit()
    async with client(ids) as api:
        assert (await api.get("/api/special-levies", params=query(ids))).status_code == 404
        assert (await api.post("/api/special-levies", json=body(ids))).status_code == 404
        result = await api.get(f"/api/payments/mietkonto/owner/{ids['owners'][0]}", params={**query(ids), "period_start": "2025-01-01", "period_end": "2025-12-31"})
        assert result.status_code == 404


async def test_real_rls_fail_closed_and_append_only(levy_db, levy_fixture):
    ids = levy_fixture
    async with client(ids) as api:
        levy = await create(api, ids)
    ids["levy"] = uuid.UUID(levy["id"])
    await actor(levy_db, ids)
    assert await levy_db.scalar(text("SELECT count(*) FROM special_levy WHERE id=:levy"), ids) == 1
    for statement in ["UPDATE special_levy SET amount=1 WHERE id=:levy", "DELETE FROM special_levy WHERE id=:levy", "UPDATE special_levy_allocation SET amount=1 WHERE special_levy_id=:levy", "DELETE FROM special_levy_allocation WHERE special_levy_id=:levy"]:
        with pytest.raises(DBAPIError):
            async with levy_db.begin_nested():
                await levy_db.execute(text(statement), ids)
    await levy_db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {"actor": str(ids["actor"])})
    for table in ("special_levy", "special_levy_allocation"):
        assert await levy_db.scalar(text(f"SELECT count(*) FROM {table} WHERE organization_id=:org"), ids) == 0
    await levy_db.execute(text("SELECT set_config('app.current_org_id','',true),set_config('app.current_user_id','',true)"))
    for table in ("special_levy", "special_levy_allocation"):
        assert await levy_db.scalar(text(f"SELECT count(*) FROM {table}")) == 0
    await levy_db.execute(text("RESET ROLE"))


async def test_failure_after_allocation_rolls_back_everything(levy_db, levy_fixture, monkeypatch):
    from app.services.special_levy_service import SpecialLevyService
    original = SpecialLevyService.create
    async def fail(self, **kwargs):
        await original(self, **kwargs)
        raise RuntimeError("synthetic failure after audit")
    monkeypatch.setattr(SpecialLevyService, "create", fail)
    async with client(levy_fixture, raise_errors=False) as api:
        assert (await api.post("/api/special-levies", json=body(levy_fixture))).status_code == 500
    for table in ("special_levy", "special_levy_allocation", "audit_event"):
        suffix = " AND entity_type='special_levy'" if table == "audit_event" else ""
        assert await levy_db.scalar(text(f"SELECT count(*) FROM {table} WHERE organization_id=:org{suffix}"), levy_fixture) == 0


async def test_pure_engine_levy_is_one_off_due_month_only():
    owner = uuid.uuid4()
    assert special_levy_expected_charges([(date(2025,6,15), Decimal("3000.00"))], owner, date(2025,1,1), date(2025,12,31))[0].payment_type == "SONDERZAHLUNG"
    assert special_levy_expected_charges([(date(2025,6,15), Decimal("3000.00"))], owner, date(2025,6,16), date(2025,12,31)) == []


async def test_resolution_reference_and_scope_context(levy_db, levy_fixture):
    ids = levy_fixture
    resolutions = []
    for prop in (ids['property'], ids['other_property']):
        meeting, resolution = uuid.uuid4(), uuid.uuid4()
        params = {**ids, 'meeting': meeting, 'resolution': resolution, 'prop': prop}
        await levy_db.execute(text("INSERT INTO owners_meeting(id,organization_id,property_id,meeting_date,location,meeting_type,status) VALUES (:meeting,:org,:prop,'2025-01-01','','ORDENTLICH','PLANNED')"), params)
        await levy_db.execute(text("INSERT INTO resolution(id,organization_id,meeting_id,wording,votes_yes,votes_no,votes_abstain,result) VALUES (:resolution,:org,:meeting,'Special levy',1,0,0,'ANGENOMMEN')"), params)
        resolutions.append(resolution)
    await levy_db.commit()
    async with client(ids) as api:
        levy = await create(api, ids, resolution_id=str(resolutions[0]))
        assert levy['resolution_id'] == str(resolutions[0])
        for resolution in [resolutions[1], uuid.uuid4()]:
            response = await api.post('/api/special-levies', json=body(ids, resolution_id=str(resolution)))
            assert response.status_code == 404, response.text
    for overrides in [{'property_id': ids['other_property']}, {'resolution_id': resolutions[1]}]:
        params = {**ids, 'resolution_id': None, 'property_id': ids['property'], **overrides}
        with pytest.raises(DBAPIError):
            async with levy_db.begin_nested():
                await levy_db.execute(text("INSERT INTO special_levy(organization_id,property_id,accounting_scope_id,amount,due_date,resolution_id) VALUES (:org,:property_id,:scope,1,'2025-06-15',:resolution_id)"), params)


async def test_unsupported_scope_and_due_date_rejected(levy_db, levy_fixture):
    ids = levy_fixture
    async with client(ids) as api:
        assert (await api.post('/api/special-levies', json=body(ids, due_date='2024-12-31'))).status_code == 422
    ids = {**ids, "scope": uuid.uuid4()}
    await levy_db.execute(text("INSERT INTO accounting_scope(id,organization_id,property_id,scope_type,label,valid_from,whole_property,origin,created_by) VALUES (:scope,:org,:property,'WEG','Partial levy context','2025-01-01',false,'REVIEWED',:actor)"), ids)
    await levy_db.execute(text("INSERT INTO accounting_scope_unit(organization_id,scope_id,property_id,unit_id) VALUES (:org,:scope,:property,:unit)"), {**ids, "unit": ids['units'][0]})
    await levy_db.execute(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES (:org,:scope,:clerk,ARRAY['read','prepare'],:actor,'Explicit unsupported partial context')"), ids)
    await levy_db.commit()
    async with client(ids) as api:
        assert (await api.get('/api/special-levies', params=query(ids))).status_code == 422
        assert (await api.post('/api/special-levies', json=body(ids))).status_code == 422


async def test_sources_revoke_closes_levy_statement_and_audit(levy_db, levy_fixture):
    from tests.test_property_source_access import grant
    from tests.contact_source_fixtures import grant_property_contacts
    from tests.test_property_source_enforcement import synthetic_enforcement
    ids = levy_fixture
    seed = {'org_a_id': ids['org'], 'user_a_admin_id': ids['actor']}
    await levy_db.execute(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES (:org,:scope,:actor,ARRAY['read','prepare'],:actor,'Source boundary test')"), ids)
    await levy_db.commit()
    async with client(ids, 'actor') as api:
        levy = await create(api, ids)
        await grant_property_contacts(api, seed, ids['property'])
        domain, receipt = await grant(api, seed, ids['property'], caps=['read','prepare'])
        async with synthetic_enforcement(seed):
            assert (await api.get('/api/special-levies', params=query(ids))).status_code == 200
            url = f"/api/payments/mietkonto/owner/{ids['owners'][0]}"
            params = {**query(ids), 'period_start': '2025-01-01', 'period_end': '2025-12-31'}
            before = await api.get(url, params=params)
            assert before.status_code == 200, before.text
            assert before.json()['soll_total'] == '3000.00'
            await create(api, ids, amount='1.00')
            assert (await api.post(f'/api/property-data-domains/{domain}/grants/{receipt}/revoke')).status_code == 204
            assert (await api.get('/api/special-levies', params=query(ids))).status_code == 404
            assert (await api.get(url, params=params)).status_code == 404
            assert (await api.post('/api/special-levies', json=body(ids))).status_code == 404
            await actor(levy_db, ids, 'actor')
            for table, column in [('special_levy', 'id'), ('special_levy_allocation', 'special_levy_id'), ('audit_event', 'entity_id')]:
                assert await levy_db.scalar(text(f'SELECT count(*) FROM {table} WHERE {column}=:id'), {'id': uuid.UUID(levy['id'])}) == 0
            await levy_db.execute(text('RESET ROLE'))
            await levy_db.rollback()


async def test_new_migration_roundtrip_preserves_adapters(levy_db):
    import importlib
    from sqlalchemy import create_engine
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    migration = importlib.import_module('migrations.versions.0127_special_levy')
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
            savepoint = db.begin_nested()
            try:
                migration.downgrade()
                assert db.scalar(text("SELECT to_regclass('special_levy')")) is None
                assert db.scalar(text("SELECT to_regprocedure('financial_scope_id(uuid,text,uuid)')")) is not None
                assert db.scalar(text("SELECT to_regprocedure('report_scope_source_access(uuid,uuid,text)')")) is not None
                migration.upgrade()
                assert db.scalar(text("SELECT count(*) FROM pg_class WHERE relname IN ('special_levy','special_levy_allocation') AND relrowsecurity AND relforcerowsecurity")) == 2
                for table in ['special_levy','special_levy_allocation']:
                    assert db.scalar(text("SELECT has_table_privilege('app_rw',:table,'SELECT,INSERT')"), {'table': table})
                    for action in ['UPDATE','DELETE','TRUNCATE']:
                        assert not db.scalar(text("SELECT has_table_privilege('app_rw',:table,:action)"), {'table': table, 'action': action})
            finally:
                savepoint.rollback()
    finally:
        engine.dispose()


@pytest.mark.parametrize("operation,change,expected", [("post", "readonly", 403), ("get", "inactive", 401)])
async def test_levy_rechecks_live_principal_in_data_transaction(levy_db, levy_fixture, operation, change, expected):
    from fastapi import Depends
    from app.core.tenant import get_current_user, get_db
    ids = levy_fixture

    async def changed_principal_db(auth=Depends(get_current_user)):
        assert auth.role == "CLERK"
        if change == "readonly":
            await levy_db.execute(text("UPDATE membership SET role='READ_ONLY' WHERE organization_id=:org AND user_id=:clerk"), ids)
        else:
            await levy_db.execute(text('UPDATE "user" SET is_active=false WHERE organization_id=:org AND id=:clerk'), ids)
        await levy_db.commit()
        async for session in get_db(auth):
            yield session

    previous = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = changed_principal_db
    try:
        async with client(ids) as api:
            response = (await api.post('/api/special-levies', json=body(ids)) if operation == 'post'
                        else await api.get('/api/special-levies', params=query(ids)))
            assert response.status_code == expected, response.text
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_db, None)
        else:
            app.dependency_overrides[get_db] = previous
    assert await levy_db.scalar(text('SELECT count(*) FROM special_levy WHERE organization_id=:org'), ids) == 0
