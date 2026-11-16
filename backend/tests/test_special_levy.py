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
from tests.test_financial_scope_boundary import actor, scope_seed as scope_seed
from tests.test_invoice_extraction_worker import worker_tenant as worker_tenant
from tests.migration_rehearsal import project_superseded_levy_allocations
from tests.test_scope_grants import scope_fixture, bind

pytestmark = pytest.mark.asyncio


def client(ids, who="clerk", *, raise_errors=True):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=raise_errors),
        base_url="http://test", cookies={settings.session_cookie_name: create_session_token(ids[who], ids["org"])})


def body(ids, **extra):
    return {"property_id": str(ids["property"]), "accounting_scope_id": str(ids["scope"]),
        "amount": "10000.00", "due_date": "2025-06-15", "resolution_id": str(ids["resolution"]), **extra}


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
    ids['meeting'], ids['resolution'] = uuid.uuid4(), uuid.uuid4()
    await s.execute(text("INSERT INTO owners_meeting(id,organization_id,property_id,meeting_date,location,meeting_type,status) VALUES (:meeting,:org,:property,'2025-05-15','Test','ORDENTLICH','HELD')"), ids)
    await s.execute(text("INSERT INTO resolution(id,organization_id,meeting_id,wording,votes_yes,votes_no,votes_abstain,result) VALUES (:resolution,:org,:meeting,'Dachreparatur',2,0,0,'ANGENOMMEN')"), ids)
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
            assert (await api.put(f"/api/special-levies/{levy['id']}/resolution", json={'resolution_id': str(ids['resolution'])})).status_code == 404
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
    supersession = importlib.import_module('migrations.versions.0191_levy_allocation_supersession')
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
            savepoint = db.begin_nested()
            try:
                project_superseded_levy_allocations(db)
                supersession.downgrade()  # 0191 builds on the tables of 0127
                migration.downgrade()
                assert db.scalar(text("SELECT to_regclass('special_levy')")) is None
                assert db.scalar(text("SELECT to_regprocedure('financial_scope_id(uuid,text,uuid)')")) is not None
                assert db.scalar(text("SELECT to_regprocedure('report_scope_source_access(uuid,uuid,text)')")) is not None
                migration.upgrade()
                supersession.upgrade()
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


# --- The allocation follows the owners of the due date (#681) -------------------------------------

async def new_owner(db, ids):
    person, owner = uuid.uuid4(), uuid.uuid4()
    params = {**ids, "person": person, "owner": owner, "name": str(owner)[:8]}
    await db.execute(text("INSERT INTO person(id,organization_id,first_name,last_name,street,zip_code,city) VALUES (:person,:org,'Buyer',:name,'','','')"), params)
    await db.execute(text("INSERT INTO owner_profile(id,organization_id,person_id,notes) VALUES (:owner,:org,:person,'')"), params)
    await db.commit()
    return owner


async def end(api, history, last_day):
    response = await api.patch(f"/api/ownership/{history}", json={"valid_to": last_day})
    assert response.status_code == 200, response.text


async def start(api, unit, owner, first_day, share=None):
    response = await api.post("/api/ownership", json={"unit_id": str(unit), "owner_profile_id": str(owner),
        "valid_from": first_day, "mea_share": share})
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["id"])


async def correct_row(api, history, first_day, share):
    response = await api.post(f"/api/ownership/{history}/correction", json={"valid_from": first_day, "mea_share": share})
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["id"])


async def levies(api, ids):
    response = await api.get("/api/special-levies", params=query(ids))
    assert response.status_code == 200, response.text
    return {levy["due_date"]: levy for levy in response.json()}


def cells(levy, unit):
    return {a["owner_profile_id"]: a["amount"] for a in levy["allocations"] if a["unit_id"] == str(unit)}


async def unit_rows(db, levy, unit):
    """Every row of the unit, newest last: (owner, amount, superseded by)."""
    rows = await db.execute(text("""SELECT a.owner_profile_id,a.amount,s.superseded_by,a.id FROM special_levy_allocation a
        LEFT JOIN special_levy_allocation_supersession s ON s.allocation_id=a.id
        WHERE a.special_levy_id=:levy AND a.unit_id=:unit ORDER BY (s.allocation_id IS NULL),s.superseded_at,a.id"""),
        {"levy": uuid.UUID(levy["id"]), "unit": unit})
    return rows.all()


async def moves(db, levy):
    return await db.scalar(text("SELECT count(*) FROM audit_event WHERE entity_type='special_levy' AND entity_id=:id AND action='special_levy.allocation_moved'"),
                           {"id": uuid.UUID(levy["id"])})


async def statement(api, ids, owner, start="2025-06-01", end="2025-06-30"):
    response = await api.get(f"/api/payments/mietkonto/owner/{owner}", params={**query(ids), "period_start": start, "period_end": end})
    assert response.status_code == 200, response.text
    return response.json()


async def test_a_sale_recorded_after_the_levy_moves_the_unit_amount_to_the_buyer(levy_db, levy_fixture):
    ids = levy_fixture
    unit, seller, others = ids["units"][0], ids["owners"][0], ids["units"][1:]
    buyer = await new_owner(levy_db, ids)
    async with client(ids) as api:
        levy = await create(api, ids)
        before = {u: cells(levy, u) for u in others}
        await end(api, ids["histories"][0], "2025-05-31")
        # Only the end is recorded: nobody owns the unit on the due date yet, so nothing moves and nothing is written.
        assert cells((await levies(api, ids))["2025-06-15"], unit) == {str(seller): "3000.00"}
        assert await moves(levy_db, levy) == 0
        await start(api, unit, buyer, "2025-06-01")
        moved = (await levies(api, ids))["2025-06-15"]
        assert cells(moved, unit) == {str(buyer): "3000.00"}
        assert {u: cells(moved, u) for u in others} == before
        assert sum(Decimal(a["amount"]) for a in moved["allocations"]) == Decimal("10000.00")
        assert (await statement(api, ids, seller))["soll_total"] == "0.00"
        assert (await statement(api, ids, buyer))["soll_total"] == "3000.00"
    old, new = await unit_rows(levy_db, levy, unit)
    assert (old.owner_profile_id, old.amount, old.superseded_by) == (seller, Decimal("3000.00"), ids["clerk"])
    assert (new.owner_profile_id, new.amount, new.superseded_by) == (buyer, Decimal("3000.00"), None)
    assert await moves(levy_db, levy) == 1
    payload = await levy_db.scalar(text("SELECT payload_diff FROM audit_event WHERE entity_id=:id AND action='special_levy.allocation_moved'"), {"id": uuid.UUID(levy["id"])})
    assert payload["unit_id"] == str(unit) and payload["due_date"] == "2025-06-15"
    assert [(r["owner_profile_id"], r["amount"]) for r in payload["superseded"]] == [(str(seller), "3000.00")]
    assert [(r["owner_profile_id"], r["amount"]) for r in payload["allocated"]] == [(str(buyer), "3000.00")]


async def test_a_levy_due_before_the_sale_stays_with_the_seller(levy_db, levy_fixture):
    ids = levy_fixture
    unit, seller = ids["units"][0], ids["owners"][0]
    buyer = await new_owner(levy_db, ids)
    async with client(ids) as api:
        levy = await create(api, ids, due_date="2025-03-01")
        await end(api, ids["histories"][0], "2025-05-31")
        await start(api, unit, buyer, "2025-06-01")
        assert (await levies(api, ids))["2025-03-01"] == levy
        assert (await statement(api, ids, seller, "2025-03-01", "2025-03-31"))["soll_total"] == "3000.00"
        assert (await statement(api, ids, buyer, "2025-03-01", "2025-03-31"))["soll_total"] == "0.00"
    assert await moves(levy_db, levy) == 0
    assert await levy_db.scalar(text("SELECT count(*) FROM special_levy_allocation_supersession WHERE organization_id=:org"), ids) == 0


async def test_co_owner_buyers_split_the_unit_amount_by_their_shares(levy_db, levy_fixture):
    ids = levy_fixture
    unit = ids["units"][0]
    first, second = await new_owner(levy_db, ids), await new_owner(levy_db, ids)
    async with client(ids) as api:
        levy = await create(api, ids)
        await end(api, ids["histories"][0], "2025-05-31")
        await start(api, unit, first, "2025-06-01", "1")
        assert cells((await levies(api, ids))["2025-06-15"], unit) == {str(first): "3000.00"}
        await start(api, unit, second, "2025-06-01", "3")
        moved = (await levies(api, ids))["2025-06-15"]
        assert cells(moved, unit) == {str(first): "750.00", str(second): "2250.00"}
        assert sum(Decimal(a["amount"]) for a in moved["allocations"]) == Decimal("10000.00")
    rows = await unit_rows(levy_db, levy, unit)
    assert sorted(str(r.owner_profile_id) for r in rows if r.superseded_by is not None) == sorted([str(ids["owners"][0]), str(first)])
    assert {r.owner_profile_id for r in rows if r.superseded_by is None} == {first, second}
    assert len(rows) == 4
    assert await moves(levy_db, levy) == 2


async def test_a_share_correction_moves_the_unit_amount_by_the_corrected_shares(levy_db, levy_fixture):
    ids = levy_fixture
    unit = ids["units"][0]
    bernd, carla = await new_owner(levy_db, ids), await new_owner(levy_db, ids)
    async with client(ids) as api:
        await end(api, ids["histories"][0], "2024-12-31")
        b_row, c_row = await start(api, unit, bernd, "2025-01-01", "50"), await start(api, unit, carla, "2025-01-01", "50")
        levy = await create(api, ids)
        assert cells(levy, unit) == {str(bernd): "1500.00", str(carla): "1500.00"}
        b_row = await correct_row(api, b_row, "2025-01-01", "70")
        assert cells((await levies(api, ids))["2025-06-15"], unit) == {str(bernd): "1750.00", str(carla): "1250.00"}
        await correct_row(api, c_row, "2025-01-01", "30")
        moved = (await levies(api, ids))["2025-06-15"]
        assert cells(moved, unit) == {str(bernd): "2100.00", str(carla): "900.00"}
        assert sum(Decimal(a["amount"]) for a in moved["allocations"]) == Decimal("10000.00")
        # A correction of the start that leaves the owners and shares on the due date moves nothing.
        counting = await unit_rows(levy_db, levy, unit)
        await correct_row(api, b_row, "2025-02-01", "70")
        assert await unit_rows(levy_db, levy, unit) == counting
    assert await moves(levy_db, levy) == 2


async def test_a_correction_across_the_due_date_moves_the_allocation_both_ways(levy_db, levy_fixture):
    ids = levy_fixture
    unit, seller = ids["units"][0], ids["owners"][0]
    buyer = await new_owner(levy_db, ids)
    async with client(ids) as api:
        levy = await create(api, ids)
        await end(api, ids["histories"][0], "2025-09-30")
        buyer_row = await start(api, unit, buyer, "2025-10-01")  # the sale was entered as of October: after the due date
        assert cells((await levies(api, ids))["2025-06-15"], unit) == {str(seller): "3000.00"}
        # The real start was 1 June: seller and buyer overlap on the due date until the seller's end is corrected.
        buyer_row = await correct_row(api, buyer_row, "2025-06-01", None)
        assert cells((await levies(api, ids))["2025-06-15"], unit) == {str(seller): "3000.00"}
        await end(api, ids["histories"][0], "2025-05-31")
        assert cells((await levies(api, ids))["2025-06-15"], unit) == {str(buyer): "3000.00"}
        # And back: the buyer's start is corrected to a day after the due date and the seller's end moves out.
        await correct_row(api, buyer_row, "2025-10-01", None)
        assert cells((await levies(api, ids))["2025-06-15"], unit) == {str(buyer): "3000.00"}
        await end(api, ids["histories"][0], "2025-09-30")
        back = (await levies(api, ids))["2025-06-15"]
        assert cells(back, unit) == {str(seller): "3000.00"}
        assert sum(Decimal(a["amount"]) for a in back["allocations"]) == Decimal("10000.00")
    assert len(await unit_rows(levy_db, levy, unit)) == 3 and await moves(levy_db, levy) == 2


async def test_the_cents_of_the_unit_amount_are_preserved_when_it_moves(levy_db, levy_fixture):
    ids = levy_fixture
    await levy_db.execute(text("UPDATE unit SET mea=1 WHERE property_id=:property"), ids)
    await levy_db.commit()
    first, second = await new_owner(levy_db, ids), await new_owner(levy_db, ids)
    async with client(ids) as api:
        levy = await create(api, ids, amount="100.01")  # 33.34, 33.34 and 33.33 by largest remainder
        odd = next(i for i, unit in enumerate(ids["units"]) if cells(levy, unit) == {str(ids["owners"][i]): "33.33"})
        unit = ids["units"][odd]
        await end(api, ids["histories"][odd], "2025-05-31")
        await start(api, unit, first, "2025-06-01", "1")
        assert cells((await levies(api, ids))["2025-06-15"], unit) == {str(first): "33.33"}
        await start(api, unit, second, "2025-06-01", "1")
        moved = (await levies(api, ids))["2025-06-15"]
        # Equal shares of 33.33 are 16.665 each: the odd cent goes to the owner first in order, as at creation.
        expected = dict(zip(sorted([str(first), str(second)]), ["16.67", "16.66"], strict=True))
        assert cells(moved, unit) == expected
        assert sum(Decimal(a["amount"]) for a in moved["allocations"]) == Decimal("100.01")
    assert await levy_db.scalar(text("SELECT sum(a.amount) FROM special_levy_allocation a WHERE a.special_levy_id=:levy AND NOT EXISTS(SELECT 1 FROM special_levy_allocation_supersession s WHERE s.allocation_id=a.id)"),
                                {"levy": uuid.UUID(levy["id"])}) == Decimal("100.01")


async def test_payments_booked_before_the_move_stay_with_the_payer(levy_db, levy_fixture):
    ids = levy_fixture
    unit, seller = ids["units"][0], ids["owners"][0]
    buyer = await new_owner(levy_db, ids)
    async with client(ids) as api:
        await create(api, ids)
        paid = await api.post("/api/payments", json={"bank_account_id": str(ids["bank"]), "amount": "1000.00", "direction": "IN",
            "value_date": "2025-06-15", "payment_type": "SONDERZAHLUNG", "owner_profile_id": str(seller), "period_year": 2025, "period_month": 6})
        assert paid.status_code == 201, paid.text
        assert (await api.post(f"/api/payments/{paid.json()['id']}/book")).status_code == 200
        await end(api, ids["histories"][0], "2025-05-31")
        await start(api, unit, buyer, "2025-06-01")
        gone, owed = await statement(api, ids, seller), await statement(api, ids, buyer)
        assert (gone["soll_total"], gone["ist_total"], gone["balance"]) == ("0.00", "1000.00", "-1000.00")
        assert (owed["soll_total"], owed["ist_total"], owed["balance"]) == ("3000.00", "0.00", "3000.00")
        assert (await api.post(f"/api/payments/{paid.json()['id']}/reverse")).status_code == 201
        gone, owed = await statement(api, ids, seller), await statement(api, ids, buyer)
        assert (gone["soll_total"], gone["ist_total"], gone["balance"]) == ("0.00", "0.00", "0.00")
        assert (owed["soll_total"], owed["ist_total"], owed["balance"]) == ("3000.00", "0.00", "3000.00")


async def test_two_sales_end_with_the_last_owner_and_a_buy_back_with_the_seller_again(levy_db, levy_fixture):
    ids = levy_fixture
    unit, seller = ids["units"][0], ids["owners"][0]
    second, third = await new_owner(levy_db, ids), await new_owner(levy_db, ids)
    async with client(ids) as api:
        levy = await create(api, ids)
        await end(api, ids["histories"][0], "2025-05-31")
        second_row = await start(api, unit, second, "2025-06-01")
        await end(api, second_row, "2025-06-10")
        await start(api, unit, third, "2025-06-11")
        assert cells((await levies(api, ids))["2025-06-15"], unit) == {str(third): "3000.00"}
        assert len(await unit_rows(levy_db, levy, unit)) == 3 and await moves(levy_db, levy) == 2
        # The seller buys the unit back from the third owner before the due date: the seller holds a second row.
        third_row = await levy_db.scalar(text("SELECT id FROM ownership_history WHERE unit_id=:unit AND owner_profile_id=:owner"), {"unit": unit, "owner": third})
        await end(api, third_row, "2025-06-12")
        await start(api, unit, seller, "2025-06-13")
        moved = (await levies(api, ids))["2025-06-15"]
        assert cells(moved, unit) == {str(seller): "3000.00"}
        assert sum(Decimal(a["amount"]) for a in moved["allocations"]) == Decimal("10000.00")
    rows = await unit_rows(levy_db, levy, unit)
    assert [r.owner_profile_id for r in rows if r.superseded_by is None] == [seller]
    assert len({r.id for r in rows}) == 4 and sum(r.superseded_by is None for r in rows) == 1


async def test_only_the_levies_of_the_unit_due_after_the_sale_move(levy_db, levy_fixture):
    ids = levy_fixture
    unit = ids["units"][0]
    buyer = await new_owner(levy_db, ids)
    async with client(ids) as api:
        early = await create(api, ids, due_date="2025-03-01")
        late = await create(api, ids, due_date="2025-09-01")
        snapshot = await levy_db.execute(text("SELECT id FROM special_levy_allocation WHERE organization_id=:org AND unit_id<>:unit ORDER BY id"), {**ids, "unit": unit})
        untouched = snapshot.all()
        await end(api, ids["histories"][0], "2025-05-31")
        await start(api, unit, buyer, "2025-06-01")
        found = await levies(api, ids)
        assert found["2025-03-01"] == early
        assert cells(found["2025-09-01"], unit) == {str(buyer): "3000.00"}
        for other in ids["units"][1:]:
            assert cells(found["2025-09-01"], other) == cells(late, other)
    assert (await levy_db.execute(text("SELECT a.id FROM special_levy_allocation a WHERE a.organization_id=:org AND a.unit_id<>:unit ORDER BY a.id"), {**ids, "unit": unit})).all() == untouched
    assert await levy_db.scalar(text("SELECT count(*) FROM special_levy_allocation_supersession WHERE organization_id=:org"), ids) == 1


@pytest.mark.parametrize("capabilities", [None, "ARRAY['read']"])
async def test_an_actor_who_may_not_move_a_levy_cannot_record_the_change_that_moves_it(levy_db, levy_fixture, capabilities):
    ids = levy_fixture
    unit = ids["units"][0]
    buyer = await new_owner(levy_db, ids)
    async with client(ids) as api:
        levy = await create(api, ids)
        await levy_db.execute(text("UPDATE scope_grant SET revoked_at=now(),revoked_by=:actor WHERE id=:grant"), ids)
        if capabilities:
            await levy_db.execute(text(f"INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES (:org,:scope,:clerk,{capabilities},:actor,'Read only')"), ids)
        await levy_db.commit()
        # Ending the seller alone moves nothing (nobody owns the unit on the due date yet) and is accepted; adding the
        # buyer would move the levy, which the actor may not do.
        ended = await api.patch(f"/api/ownership/{ids['histories'][0]}", json={"valid_to": "2025-05-31"})
        assert ended.status_code == 200, ended.text
        refused = await api.post("/api/ownership", json={"unit_id": str(unit), "owner_profile_id": str(buyer), "valid_from": "2025-06-01"})
        assert refused.status_code == 409, refused.text
        assert refused.json()["detail"]["code"] == "ownership_levy_access"
    assert await levy_db.scalar(text("SELECT valid_to FROM ownership_history WHERE id=:id"), {"id": ids["histories"][0]}) == date(2025, 6, 1)
    assert await levy_db.scalar(text("SELECT count(*) FROM ownership_history WHERE unit_id=:unit"), {"unit": unit}) == 1
    assert await levy_db.scalar(text("SELECT count(*) FROM special_levy_allocation_supersession WHERE organization_id=:org"), ids) == 0
    assert await moves(levy_db, levy) == 0


@pytest.mark.parametrize("capabilities", [None, "ARRAY['read']"])
async def test_a_levy_that_cannot_move_through_a_write_does_not_refuse_it(levy_db, levy_fixture, capabilities):
    ids = levy_fixture
    unit, seller = ids["units"][0], ids["owners"][0]
    buyer = await new_owner(levy_db, ids)
    async with client(ids) as api:
        levy = await create(api, ids, due_date="2025-03-01")
        await levy_db.execute(text("UPDATE scope_grant SET revoked_at=now(),revoked_by=:actor WHERE id=:grant"), ids)
        if capabilities:
            await levy_db.execute(text(f"INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES (:org,:scope,:clerk,{capabilities},:actor,'Read only')"), ids)
        await levy_db.commit()
        # The levy falls due before the sale: the seller owns the unit on that day before and after the write.
        await end(api, ids["histories"][0], "2025-05-31")
        await start(api, unit, buyer, "2025-06-01")
    assert await levy_db.scalar(text("SELECT count(*) FROM ownership_history WHERE unit_id=:unit"), {"unit": unit}) == 2
    assert [(r.owner_profile_id, r.superseded_by) for r in await unit_rows(levy_db, levy, unit)] == [(seller, None)]
    assert await moves(levy_db, levy) == 0


async def test_the_unit_lookup_names_only_counting_rows_of_the_callers_organization(levy_db, levy_fixture):
    ids = levy_fixture
    async with client(ids) as api:
        levy = await create(api, ids)
    await actor(levy_db, ids)
    named = (await levy_db.execute(text("SELECT * FROM special_levy_unit_allocations(:org,:unit)"), {"org": ids["org"], "unit": ids["units"][0]})).all()
    assert [(r.levy_id, r.scope_id, r.due, r.owner_id, r.history_id) for r in named] == [
        (uuid.UUID(levy["id"]), ids["scope"], date(2025, 6, 15), ids["owners"][0], ids["histories"][0])]
    assert (await levy_db.execute(text("SELECT * FROM special_levy_unit_allocations(:org,:unit)"), {"org": uuid.uuid4(), "unit": ids["units"][0]})).all() == []
    assert (await levy_db.execute(text("SELECT * FROM special_levy_unit_allocations(:org,:unit)"), {"org": ids["org"], "unit": uuid.uuid4()})).all() == []
    await levy_db.execute(text("RESET ROLE"))
    await levy_db.rollback()


async def test_superseded_rows_stay_readable_and_only_a_balanced_move_is_accepted(levy_db, levy_fixture):
    ids = levy_fixture
    unit, seller = ids["units"][0], ids["owners"][0]
    buyer = await new_owner(levy_db, ids)
    async with client(ids) as api:
        levy = await create(api, ids)
        await end(api, ids["histories"][0], "2025-05-31")
        buyer_row = await start(api, unit, buyer, "2025-06-01")
    ids["levy"], ids["unit"], ids["buyer"], ids["buyer_row"] = uuid.UUID(levy["id"]), unit, buyer, buyer_row
    old = (await unit_rows(levy_db, levy, unit))[0]
    ids["old"] = old.id
    await levy_db.commit()
    insert = ("INSERT INTO special_levy_allocation(organization_id,property_id,special_levy_id,unit_id,owner_profile_id,"
              "ownership_history_id,mea,amount) VALUES (:org,:property,:levy,:unit,:buyer,:buyer_row,1,:amount)")
    for statement_text, params in [
        ("UPDATE special_levy_allocation SET amount=1 WHERE id=:old", ids),
        ("DELETE FROM special_levy_allocation WHERE id=:old", ids),
        ("UPDATE special_levy_allocation_supersession SET superseded_by=:clerk WHERE allocation_id=:old", ids),
        ("DELETE FROM special_levy_allocation_supersession WHERE allocation_id=:old", ids),
        # A row for an existing levy without a supersession in the same transaction.
        (insert, {**ids, "amount": 1}),
    ]:
        with pytest.raises(DBAPIError):
            async with levy_db.begin_nested():
                await levy_db.execute(text(statement_text), params)
    await actor(levy_db, ids)
    for statement_text in ("UPDATE special_levy_allocation_supersession SET superseded_at=now()", "DELETE FROM special_levy_allocation_supersession"):
        with pytest.raises(DBAPIError):
            async with levy_db.begin_nested():
                await levy_db.execute(text(statement_text))
    assert await levy_db.scalar(text("SELECT count(*) FROM special_levy_allocation_supersession")) == 1
    await levy_db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {"actor": str(ids["actor"])})
    assert await levy_db.scalar(text("SELECT count(*) FROM special_levy_allocation_supersession")) == 0  # no scope grant: not readable
    await levy_db.execute(text("RESET ROLE"))
    await levy_db.rollback()
    # A supersession whose replacement does not carry the unit's exact amount is refused at commit.
    for amount in (Decimal("2999.99"), Decimal("3000.01")):
        marker = ("INSERT INTO special_levy_allocation_supersession(allocation_id,organization_id,superseded_by) "
                  "SELECT id,organization_id,:clerk FROM special_levy_allocation WHERE special_levy_id=:levy AND unit_id=:unit AND owner_profile_id=:buyer")
        with pytest.raises(DBAPIError, match="conserve the exact total"):
            async with levy_db.begin_nested():
                await levy_db.execute(text(marker), ids)
                await levy_db.execute(text(insert), {**ids, "amount": amount})
                await levy_db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    # Two counting rows of one owner and unit are refused, a balanced replacement is accepted.
    with pytest.raises(DBAPIError, match="one counting allocation per unit"):
        async with levy_db.begin_nested():
            await levy_db.execute(text(marker), ids)
            await levy_db.execute(text(insert), {**ids, "amount": Decimal("1500.00")})
            await levy_db.execute(text(insert), {**ids, "amount": Decimal("1500.00")})
            await levy_db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    async with levy_db.begin_nested():
        await levy_db.execute(text(marker), ids)
        await levy_db.execute(text(insert), {**ids, "amount": Decimal("3000.00")})
        await levy_db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        await levy_db.execute(text("SET CONSTRAINTS ALL DEFERRED"))
    await levy_db.rollback()
    assert seller == old.owner_profile_id


async def test_the_supersession_migration_roundtrips_and_refuses_to_discard_a_marker(levy_db, levy_fixture):
    import importlib
    from sqlalchemy import create_engine
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    ids = levy_fixture
    migration = importlib.import_module('migrations.versions.0191_levy_allocation_supersession')
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
            guard = "SELECT pg_get_functiondef('guard_special_levy()'::regprocedure)"
            total = "SELECT pg_get_functiondef('validate_special_levy_total()'::regprocedure)"
            current = (db.scalar(text(guard)), db.scalar(text(total)))
            savepoint = db.begin_nested()
            try:
                # Earlier tests of the session left moved levies behind, which the populated downgrade
                # (tested below) refuses: drop their superseded rows in this rollback-only probe.
                project_superseded_levy_allocations(db)
                migration.downgrade()
                assert db.scalar(text("SELECT to_regclass('special_levy_allocation_supersession')")) is None
                assert db.scalar(text("SELECT to_regprocedure('special_levy_unit_allocations(uuid,uuid)')")) is None
                assert (db.scalar(text(guard)), db.scalar(text(total))) != current
                assert db.scalar(text("SELECT count(*) FROM pg_constraint WHERE conname='uq_special_levy_owner_unit'")) == 1
                migration.upgrade()
                assert (db.scalar(text(guard)), db.scalar(text(total))) == current
                for action in ('UPDATE', 'DELETE', 'TRUNCATE'):
                    assert not db.scalar(text("SELECT has_table_privilege('app_rw','special_levy_allocation_supersession',:action)"), {'action': action})
                assert db.scalar(text("SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE relname='special_levy_allocation_supersession'"))
            finally:
                savepoint.rollback()
    finally:
        engine.dispose()
    buyer = await new_owner(levy_db, ids)
    async with client(ids) as api:
        await create(api, ids)
        await end(api, ids["histories"][0], "2025-05-31")
        await start(api, ids["units"][0], buyer, "2025-06-01")
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
            savepoint = db.begin_nested()
            try:
                with pytest.raises(Exception, match="Cannot discard superseded special levy allocations"):
                    migration.downgrade()
            finally:
                savepoint.rollback()
    finally:
        engine.dispose()


async def test_a_move_must_keep_the_amount_of_every_unit_it_touches(levy_db, levy_fixture):
    ids = levy_fixture
    async with client(ids) as api:
        levy = await create(api, ids)
    ids["levy"] = uuid.UUID(levy["id"])
    rows = (await levy_db.execute(text("SELECT id,unit_id FROM special_levy_allocation WHERE special_levy_id=:levy"), ids)).all()
    first, second = (next(r.id for r in rows if r.unit_id == ids["units"][n]) for n in (0, 1))
    marker = "INSERT INTO special_levy_allocation_supersession(allocation_id,organization_id,superseded_by) VALUES (:allocation,:org,:clerk)"
    insert = ("INSERT INTO special_levy_allocation(organization_id,property_id,special_levy_id,unit_id,owner_profile_id,"
              "ownership_history_id,mea,amount) VALUES (:org,:property,:levy,:unit,:owner,:history,1,:amount)")
    first_cell = {**ids, "unit": ids["units"][0], "owner": ids["owners"][0], "history": ids["histories"][0]}
    second_cell = {**ids, "unit": ids["units"][1], "owner": ids["owners"][1], "history": ids["histories"][1]}
    # Unit 2's amount is superseded too and one row carries both amounts for unit 1's owner: the levy total is
    # still exact, the amount of each unit is not.
    with pytest.raises(DBAPIError, match="amount of each unit"):
        async with levy_db.begin_nested():
            for allocation in (first, second):
                await levy_db.execute(text(marker), {**ids, "allocation": allocation})
            await levy_db.execute(text(insert), {**first_cell, "amount": Decimal("6000.00")})
            await levy_db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    # Shifting cents between the two units is refused as well, a replacement of exactly each unit's amount is accepted,
    # also when a replacement is superseded again within the same transaction.
    with pytest.raises(DBAPIError, match="amount of each unit"):
        async with levy_db.begin_nested():
            for allocation in (first, second):
                await levy_db.execute(text(marker), {**ids, "allocation": allocation})
            await levy_db.execute(text(insert), {**first_cell, "amount": Decimal("3000.01")})
            await levy_db.execute(text(insert), {**second_cell, "amount": Decimal("2999.99")})
            await levy_db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    async with levy_db.begin_nested():
        for allocation in (first, second):
            await levy_db.execute(text(marker), {**ids, "allocation": allocation})
        await levy_db.execute(text(insert), {**first_cell, "amount": Decimal("3000.00")})
        await levy_db.execute(text(insert), {**second_cell, "amount": Decimal("3000.00")})
        await levy_db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        await levy_db.execute(text("SET CONSTRAINTS ALL DEFERRED"))
        replacement = await levy_db.scalar(text("SELECT id FROM special_levy_allocation WHERE special_levy_id=:levy AND unit_id=:unit AND amount=3000 AND NOT EXISTS(SELECT 1 FROM special_levy_allocation_supersession s WHERE s.allocation_id=special_levy_allocation.id)"), first_cell)
        await levy_db.execute(text(marker), {**ids, "allocation": replacement})
        await levy_db.execute(text(insert), {**first_cell, "amount": Decimal("3000.00")})
        await levy_db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    await levy_db.rollback()


async def test_a_counting_row_must_name_an_owner_of_the_due_date_when_the_transaction_commits(levy_db, levy_fixture):
    ids = levy_fixture
    levy = uuid.uuid4()
    insert_levy = ("INSERT INTO special_levy(id,organization_id,property_id,accounting_scope_id,amount,due_date) "
                   "VALUES (:levy,:org,:property,:scope,1,'2025-06-15')")
    insert_row = ("INSERT INTO special_levy_allocation(organization_id,property_id,special_levy_id,unit_id,owner_profile_id,"
                  "ownership_history_id,mea,amount) VALUES (:org,:property,:levy,:unit,:owner,:history,1,1)")
    params = {**ids, "levy": levy, "unit": ids["units"][0], "owner": ids["owners"][0]}
    # The ownership row belongs to another unit: refused at commit, no longer by the row-level guard.
    with pytest.raises(DBAPIError, match="does not match due-date ownership"):
        async with levy_db.begin_nested():
            await levy_db.execute(text(insert_levy), params)
            await levy_db.execute(text(insert_row), {**params, "history": ids["histories"][1]})
            await levy_db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    # The row's own ownership row covers the due date.
    async with levy_db.begin_nested():
        await levy_db.execute(text(insert_levy), params)
        await levy_db.execute(text(insert_row), {**params, "history": ids["histories"][0]})
        await levy_db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    await levy_db.rollback()

