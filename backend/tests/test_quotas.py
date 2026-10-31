"""Hosted quota boundaries, atomic refusals and policy selection."""
from dataclasses import replace
from decimal import Decimal
import uuid

import pytest

from app.core.config import Settings, settings
from tests.test_rent_changes import rent_db, rent_world, api_client  # noqa: F401
from tests.test_master_import import body, preview, apply, inventory
from tests.test_financial_scope_boundary import scope_seed, worker_tenant  # noqa: F401
from sqlalchemy import text
from fastapi import HTTPException


@pytest.mark.parametrize("kind,limit,message", [
    ("storage_bytes", 2 * 1024**3, "Speicherkontingent erschöpft (2 GiB)."),
    ("upload_bytes", 25 * 1024**2, "Datei zu groß (maximal 25 MiB)."),
    ("properties", 20, "Objektkontingent erschöpft (20 Objekte)."),
    ("units", 200, "Einheitenkontingent erschöpft (200 Einheiten)."),
    ("ai_budget_usd", Decimal("0"), "Das monatliche KI-Budget ist ausgeschöpft."),
])
def test_exact_limit_is_allowed_and_one_more_is_refused(kind, limit, message):
    from app.core.quotas import FREE_PLAN, check_quota
    assert check_quota(limit - 1, FREE_PLAN, kind, 1).allowed
    denied = check_quota(limit, FREE_PLAN, kind, 1)
    assert not denied.allowed
    assert message in denied.message


@pytest.mark.parametrize("mode,plan", [("hosted", "free"), ("selfhost", "unlimited")])
def test_environment_selects_the_quota_plan(monkeypatch, mode, plan):
    from app.core.quotas import get_quota_plan, check_quota
    monkeypatch.setenv("HV_DEPLOYMENT_MODE", mode)
    monkeypatch.setattr(settings, "deployment_mode", Settings(_env_file=None).deployment_mode)
    selected = get_quota_plan()
    assert selected.name == plan
    assert check_quota(10**15, selected, "properties", 1).allowed == (mode == "selfhost")


@pytest.mark.asyncio
async def test_quota_is_authenticated_and_scoped_to_the_current_organization(
        rent_world, client_unauth, monkeypatch):
    monkeypatch.setattr(settings, "deployment_mode", "hosted")
    assert (await client_unauth.get("/api/organization/quota")).status_code == 401
    async with api_client(rent_world, "reader") as api:
        answer = await api.get("/api/organization/quota")
        assert answer.status_code == 200, answer.text
        assert answer.json()["usage"]["properties"] == 2
        assert answer.json()["usage"]["units"] == 2
        assert answer.json()["limits"]["storage_bytes"] == 2 * 1024**3


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["properties", "units"])
async def test_count_refusal_leaves_the_atomic_import_unchanged(rent_world, monkeypatch, kind):
    from app.core import quotas
    monkeypatch.setattr(settings, "deployment_mode", "hosted")
    monkeypatch.setattr(quotas, "FREE_PLAN", replace(quotas.FREE_PLAN, **{kind: 2}))
    async with api_client(rent_world) as api:
        before = await inventory(api)
        command = body()
        projected = await preview(api, command)
        refused = await apply(api, command, projected)
        assert refused.status_code == 409, refused.text
        assert "kontingent erschöpft" in refused.json()["detail"]
        assert await inventory(api) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,path,payload", [
    ("properties", "/api/properties", {"name": "Weiteres Objekt", "management_type": "MIET"}),
    ("units", "/api/units", {"number": "Weitere Einheit", "usage_type": "WOHNUNG"}),
])
async def test_individual_count_writes_obey_the_limit(rent_world, monkeypatch, kind, path, payload):
    from app.core import quotas
    monkeypatch.setattr(settings, "deployment_mode", "hosted")
    monkeypatch.setattr(quotas, "FREE_PLAN", replace(quotas.FREE_PLAN, **{kind: 3}))
    if kind == "units":
        payload = {**payload, "property_id": str(rent_world["property"])}
    async with api_client(rent_world) as api:
        assert (await api.post(path, json=payload)).status_code == 201
        refused = await api.post(path, json=payload)
        assert refused.status_code == 409, refused.text
        assert len((await api.get(path)).json()) == 3


@pytest.mark.asyncio
async def test_upload_at_storage_limit_succeeds_then_refuses_without_rows_or_files(
        rent_world, monkeypatch, tmp_path):
    from app.core import quotas, storage
    monkeypatch.setattr(settings, "deployment_mode", "hosted")
    monkeypatch.setattr(quotas, "FREE_PLAN", replace(quotas.FREE_PLAN, storage_bytes=8))
    monkeypatch.setattr(storage, "_store", storage.LocalFsDocumentStore(tmp_path))
    async with api_client(rent_world) as api:
        upload = dict(data={"property_id": str(rent_world["property"])},
                      files={"file": ("a.pdf", b"%PDF-123", "application/pdf")})
        first = await api.post("/api/attachments", **upload)
        assert first.status_code == 201, first.text
        before = sorted(tmp_path.rglob("*.bin"))
        refused = await api.post("/api/attachments", data=upload["data"],
                                 files={"file": ("b.pdf", b"%PDF-456", "application/pdf")})
        assert refused.status_code == 413, refused.text
        assert refused.json()["detail"].startswith("Speicherkontingent erschöpft")
        assert sorted(tmp_path.rglob("*.bin")) == before
        listed = await api.get("/api/attachments", params=upload["data"])
        assert len(listed.json()["items"]) == 1
        assert (await api.get("/api/organization/quota")).json()["usage"]["storage_bytes"] == 8


@pytest.mark.asyncio
@pytest.mark.parametrize("nested", [False, True])
async def test_database_failure_removes_new_bytes_and_preserves_committed_duplicates(
        rent_world, tmp_path, nested):
    from app.core.db import get_tenant_session
    from app.core.storage import LocalFsDocumentStore, put_document
    store = LocalFsDocumentStore(tmp_path)
    async with get_tenant_session(rent_world["org"]) as db:
        kept = await put_document(db, rent_world["org"], b"committed", content_type="application/pdf", store=store)
    before = sorted(tmp_path.rglob("*.bin"))
    with pytest.raises(RuntimeError, match="DB write failed"):
        async with get_tenant_session(rent_world["org"]) as db:
            await put_document(db, rent_world["org"], b"committed", content_type="application/pdf", store=store)
            if nested:
                async with db.begin_nested():
                    await put_document(db, rent_world["org"], b"new nested", content_type="application/pdf", store=store)
            else:
                await put_document(db, rent_world["org"], b"new", content_type="application/pdf", store=store)
            raise RuntimeError("DB write failed")
    assert sorted(tmp_path.rglob("*.bin")) == before
    assert store.get(kept.uri) == b"committed"


@pytest.mark.asyncio
async def test_failed_database_statement_removes_the_new_original(rent_world, tmp_path):
    from sqlalchemy.exc import DBAPIError
    from app.core.db import get_tenant_session
    from app.core.storage import LocalFsDocumentStore, put_document
    store = LocalFsDocumentStore(tmp_path)
    with pytest.raises(DBAPIError):
        async with get_tenant_session(rent_world["org"]) as db:
            await put_document(db, rent_world["org"], b"not committed", content_type="application/pdf", store=store)
            await db.execute(text("SELECT 1/0"))
    assert list(tmp_path.rglob("*.bin")) == []


@pytest.mark.asyncio
async def test_upload_service_checks_size_before_storing_any_bytes(rent_world, tmp_path, monkeypatch):
    from app.core import quotas
    from app.core.db import get_tenant_session
    from app.core.storage import LocalFsDocumentStore, put_document
    monkeypatch.setattr(settings, "deployment_mode", "hosted")
    monkeypatch.setattr(quotas, "FREE_PLAN", replace(quotas.FREE_PLAN, upload_bytes=8))
    store = LocalFsDocumentStore(tmp_path)
    async with get_tenant_session(rent_world["org"]) as db:
        await put_document(db, rent_world["org"], b"12345678", content_type="application/pdf", store=store)
    before = sorted(tmp_path.rglob("*.bin"))
    with pytest.raises(HTTPException) as error:
        async with get_tenant_session(rent_world["org"]) as db:
            await put_document(db, rent_world["org"], b"123456789", content_type="application/pdf", store=store)
    assert error.value.status_code == 413
    assert error.value.detail.startswith("Datei zu groß")
    assert sorted(tmp_path.rglob("*.bin")) == before


@pytest.mark.asyncio
async def test_selfhost_uploads_and_counts_ignore_hosted_allowances(rent_world, tmp_path, monkeypatch):
    from app.core import quotas, storage
    monkeypatch.setattr(settings, "deployment_mode", "selfhost")
    monkeypatch.setattr(quotas, "FREE_PLAN", replace(quotas.FREE_PLAN, storage_bytes=0, upload_bytes=0, properties=0, units=0))
    monkeypatch.setattr(storage, "_store", storage.LocalFsDocumentStore(tmp_path))
    async with api_client(rent_world) as api:
        assert (await api.post("/api/properties", json={"name": "Unbegrenzt", "management_type": "MIET"})).status_code == 201
        assert (await api.post("/api/attachments", data={"property_id": str(rent_world["property"])},
                               files={"file": ("a.pdf", b"%PDF-123", "application/pdf")})).status_code == 201
        assert (await api.get("/api/organization/quota")).json()["plan"] == "unlimited"


@pytest.mark.asyncio
async def test_hosted_knowledge_is_off_by_default_without_a_provider_call(
        approved_knowledge_graph, client_a_admin, monkeypatch):
    from app.api.property_knowledge import get_knowledge_provider
    from app.declarations.fake_knowledge_provider import FakeKnowledgeProvider
    from app.main import app
    monkeypatch.setattr(settings, "deployment_mode", "hosted")
    provider = FakeKnowledgeProvider()
    app.dependency_overrides[get_knowledge_provider] = lambda: provider
    try:
        response = await client_a_admin.post(f"/api/properties/{approved_knowledge_graph.property_id}/knowledge/query",
                                            json={"question": "Heizkosten"})
        assert response.status_code == 409, response.text
        assert response.json()["detail"].startswith("Das monatliche KI-Budget")
        assert not provider.calls
    finally:
        app.dependency_overrides.pop(get_knowledge_provider, None)


@pytest.mark.asyncio
async def test_monthly_budget_reuses_durable_reservations_across_short_windows_and_organizations(
        rent_world, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from app.core import quotas
    from app.core.db import get_tenant_session
    from app.repositories.property_knowledge_query_repo import PropertyKnowledgeQueryRepository
    from app.services.quota_service import require_ai_budget
    monkeypatch.setattr(settings, "deployment_mode", "hosted")
    monkeypatch.setattr(quotas, "FREE_PLAN", replace(quotas.FREE_PLAN, ai_budget_usd=Decimal("0.01")))
    async def actor(db, user):
        await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {"actor": str(user)})
    now = datetime.now(timezone.utc)
    async with get_tenant_session(rent_world["org"]) as db:
        await actor(db, rent_world["admin"])
        await require_ai_budget(db, rent_world["org"], Decimal("0.01"))
        reservation = await PropertyKnowledgeQueryRepository(db, rent_world["org"]).reserve_query(
            property_id=rent_world["property"], actor_user_id=rent_world["admin"],
            budget_window_start=now - timedelta(hours=1), now=now,
            reservation_expires_at=now + timedelta(seconds=120), provider="test", model="test",
            reserved_input_tokens=1, max_output_tokens=1,
            reserved_cost_usd=Decimal("0.01"), budget_cap_usd=Decimal("0.01"))
        assert reservation is not None
    # New transaction: no process-local counters, and no reliance on the old hour window.
    with pytest.raises(HTTPException) as error:
        async with get_tenant_session(rent_world["org"]) as db:
            await actor(db, rent_world["admin"])
            await require_ai_budget(db, rent_world["org"], Decimal("0.000001"))
    assert error.value.status_code == 409
    async with get_tenant_session(rent_world["foreign_org"]) as db:
        await actor(db, rent_world["foreign_admin"])
        await require_ai_budget(db, rent_world["foreign_org"], Decimal("0.01"))


@pytest.mark.asyncio
async def test_openai_adapter_refuses_hosted_default_before_network(monkeypatch):
    from app.declarations.openai_provider import OpenAiDeclarationProvider
    from app.declarations.provider import ProviderFailure
    monkeypatch.setattr(settings, "deployment_mode", "hosted")
    provider = OpenAiDeclarationProvider(client=object())
    with pytest.raises(ProviderFailure) as error:
        await provider.inventory([], max_output_tokens=10)
    assert error.value.code == "ORGANIZATION_AI_BUDGET_EXCEEDED"


@pytest.mark.asyncio
@pytest.mark.parametrize("database_timezone", ["UTC", "America/Los_Angeles"])
async def test_expired_previous_month_reservations_do_not_consume_the_new_month(
        rent_world, monkeypatch, database_timezone):
    from datetime import datetime, timedelta, timezone
    from app.core import quotas
    from app.core.db import get_tenant_session
    from app.models.property_knowledge_query_run import PropertyKnowledgeQueryRun
    from app.services.quota_service import require_ai_budget
    monkeypatch.setattr(settings, "deployment_mode", "hosted")
    monkeypatch.setattr(quotas, "FREE_PLAN", replace(quotas.FREE_PLAN, ai_budget_usd=Decimal("0.01")))
    month_start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    async with get_tenant_session(rent_world["org"]) as db:
        await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {"actor": str(rent_world["admin"])})
        db.add(PropertyKnowledgeQueryRun(
            organization_id=rent_world["org"], property_id=rent_world["property"], initiated_by=rent_world["admin"],
            created_at=(month_start - timedelta(minutes=10)).replace(tzinfo=None), budget_window_start=month_start - timedelta(hours=1),
            reservation_expires_at=month_start - timedelta(minutes=8), reserved_cost_usd=Decimal("0.01"),
            provider="test", model="test"))
    async with get_tenant_session(rent_world["org"]) as db:
        await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {"actor": str(rent_world["admin"])})
        await db.execute(text("SELECT set_config('TimeZone',:zone,true)"), {"zone": database_timezone})
        await require_ai_budget(db, rent_world["org"], Decimal("0.01"))


@pytest.mark.asyncio
async def test_hosted_declaration_start_refuses_before_creating_a_queue_receipt(
        rent_world, monkeypatch, tmp_path):
    from app.core import storage
    monkeypatch.setattr(settings, "deployment_mode", "hosted")
    monkeypatch.setattr(settings, "declaration_import_enabled", True)
    monkeypatch.setattr(storage, "_store", storage.LocalFsDocumentStore(tmp_path))
    async with api_client(rent_world) as api:
        created = await api.post("/api/declaration-imports", json={"mode": "NEW_PROPERTY"})
        assert created.status_code == 201, created.text
        root = "/api/declaration-imports/" + created.json()["id"]
        uploaded = await api.post(root + "/documents", data={"document_role": "DECLARATION", "sequence_no": "1"},
                                  files={"file": ("a.pdf", b"%PDF-123", "application/pdf")})
        assert uploaded.status_code == 201, uploaded.text
        refused = await api.post(root + "/start", headers={"Idempotency-Key": str(uuid.uuid4())})
        assert refused.status_code == 409, refused.text
        assert refused.json()["detail"].startswith("Das monatliche KI-Budget")
        saved = (await api.get(root)).json()
        assert saved["status"] == "DRAFT"
        assert saved["command_receipt_id"] is None


@pytest.mark.asyncio
async def test_concurrent_knowledge_calls_share_the_hosted_monthly_cap(
        approved_knowledge_graph, client_a_admin, monkeypatch):
    import asyncio
    from app.api.property_knowledge import get_knowledge_provider
    from app.core import quotas
    from app.declarations.fake_knowledge_provider import FakeKnowledgeProvider
    from app.main import app
    monkeypatch.setattr(settings, "deployment_mode", "hosted")
    summary = await client_a_admin.get("/api/organization/quota")
    assert summary.status_code == 200, summary.text
    # Registered source graphs and their charges can survive earlier test cases.
    # Leave room for one new reservation above that real organization usage.
    prior_usage = Decimal(summary.json()["usage"]["ai_budget_usd"])
    monkeypatch.setattr(quotas, "FREE_PLAN", replace(quotas.FREE_PLAN, ai_budget_usd=prior_usage + Decimal("0.0013")))
    # This test isolates the monthly cap from the older short-window limit.
    monkeypatch.setattr(settings, "declaration_qa_max_cost_usd", prior_usage + Decimal("1"))
    class BlockingProvider(FakeKnowledgeProvider):
        entered = asyncio.Event()
        release = asyncio.Event()
        async def answer(self, request, *, max_output_tokens):
            self.entered.set()
            await self.release.wait()
            return await super().answer(request, max_output_tokens=max_output_tokens)
    provider = BlockingProvider()
    app.dependency_overrides[get_knowledge_provider] = lambda: provider
    first = None
    entered = None
    try:
        route = f"/api/properties/{approved_knowledge_graph.property_id}/knowledge/query"
        first = asyncio.create_task(client_a_admin.post(route, json={"question": "Heizkosten"}))
        entered = asyncio.create_task(provider.entered.wait())
        done, _pending = await asyncio.wait({first, entered}, timeout=5, return_when=asyncio.FIRST_COMPLETED)
        if first in done:
            response = await first
            pytest.fail(f"Provider was not reached: {response.status_code} {response.text}")
        assert entered in done, "Provider reservation did not complete"
        second = await client_a_admin.post(route, json={"question": "Heizkosten"})
        assert second.status_code == 409, second.text
        assert second.json()["detail"].startswith("Das monatliche KI-Budget")
        provider.release.set()
        assert (await first).status_code == 200
        assert len(provider.calls) == 1
    finally:
        provider.release.set()
        if entered:
            entered.cancel()
        try:
            await asyncio.gather(*(task for task in (first, entered) if task is not None), return_exceptions=True)
        finally:
            app.dependency_overrides.pop(get_knowledge_provider, None)


@pytest.mark.asyncio
async def test_quota_counts_include_source_restricted_rows(scope_seed, migrator_session, monkeypatch):
    from tests.test_property_source_enforcement import synthetic_enforcement
    from tests.test_scoped_account_statements import client
    await migrator_session.commit()
    monkeypatch.setattr(settings, "deployment_mode", "hosted")
    ids = {"org": scope_seed["org_a_id"], "actor": scope_seed["user_a_admin_id"]}
    async with client(ids, "actor") as api:
        created = await api.post("/api/properties", json={"name": "Geschütztes Objekt", "management_type": "MIET"})
        assert created.status_code == 201, created.text
        created_unit = await api.post("/api/units", json={"property_id": created.json()["id"], "number": "1", "usage_type": "WOHNUNG"})
        assert created_unit.status_code == 201, created_unit.text
        before = (await api.get("/api/organization/quota")).json()["usage"]
        assert before["properties"] > 0 and before["units"] > 0
        async with synthetic_enforcement(scope_seed):
            assert (await api.get("/api/properties")).json() == []
            assert (await api.get("/api/units")).json() == []
            summary = await api.get("/api/organization/quota")
            assert summary.status_code == 200, summary.text
            assert summary.json()["usage"]["properties"] == before["properties"]
            assert summary.json()["usage"]["units"] == before["units"]


@pytest.mark.asyncio
async def test_racing_unit_creates_cannot_exceed_the_count_limit(rent_world, monkeypatch):
    import asyncio
    from app.core import quotas
    monkeypatch.setattr(settings, "deployment_mode", "hosted")
    monkeypatch.setattr(quotas, "FREE_PLAN", replace(quotas.FREE_PLAN, units=3))
    async with api_client(rent_world) as api:
        async def create(number):
            return await api.post("/api/units", json={"property_id": str(rent_world["property"]),
                "number": number, "usage_type": "WOHNUNG"})
        answers = await asyncio.gather(create("A"), create("B"))
        assert sorted(answer.status_code for answer in answers) == [201, 409]
        assert len((await api.get("/api/units")).json()) == 3
