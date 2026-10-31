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


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["preview", "apply"])
@pytest.mark.parametrize("content_length", [False, True])
async def test_import_body_refusal_reports_its_actual_limit(path, content_length):
    import json
    from app.core.uploads import RequestBodyLimitMiddleware
    from tests.test_upload_limits import _run_asgi_app

    async def forbidden(*args):
        pytest.fail("Oversized import reached the parser")

    limit = 7 * 256 * 1024
    sent = await _run_asgi_app(RequestBodyLimitMiddleware(forbidden),
        path=f"/api/properties/import/{path}",
        headers=[(b"content-length", str(limit + 1).encode())] if content_length else [],
        messages=[] if content_length else [{"type": "http.request", "body": b"x" * (limit + 1)}])
    assert sent[0]["status"] == 413
    assert "maximal 1,75 MiB" in json.loads(sent[1]["body"])["detail"]["message"]


@pytest.mark.asyncio
async def test_quota_function_has_fixed_security_metadata_and_narrow_acl(app_rw_session):
    signature = {"signature": "public.organization_quota_usage(uuid)"}
    security = (await app_rw_session.execute(text("""SELECT pg_get_userbyid(proowner) AS owner,prosecdef,proconfig
        FROM pg_proc WHERE oid=CAST(:signature AS regprocedure)"""), signature)).one()
    assert security.owner == "app_migrator"
    assert security.prosecdef is True
    assert security.proconfig == ["search_path=pg_catalog, pg_temp", "row_security=off"]
    grants = (await app_rw_session.execute(text("""SELECT pg_get_userbyid(a.grantee),a.privilege_type,a.is_grantable
        FROM pg_proc p CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
        WHERE p.oid=CAST(:signature AS regprocedure) AND a.grantee<>p.proowner"""), signature)).all()
    assert grants == [("app_rw", "EXECUTE", False)]


def test_quota_migration_downgrade_is_exact_and_preserves_original_metadata(run_migrations):
    import importlib
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine
    from tests.conftest import MIGRATOR_SYNC_URL
    migration = importlib.import_module("migrations.versions.0146_organization_quota_usage")
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.connect() as db:
            restore = db.begin()
            try:
                metadata = text("""SELECT pg_get_functiondef(oid),proacl::text,proconfig,proowner
                    FROM pg_proc WHERE oid='organization_quota_usage(uuid)'::regprocedure""")
                function = db.execute(metadata).one()
                columns = text("""SELECT column_name,data_type,is_nullable,column_default FROM information_schema.columns
                    WHERE table_schema='public' AND table_name='letter_document' AND column_name<>'size_bytes' ORDER BY ordinal_position""")
                old_columns = db.execute(columns).all()
                originals = text("SELECT to_jsonb(d)-'size_bytes' FROM letter_document d ORDER BY id")
                old_rows = db.execute(originals).all()
                guards = text("""SELECT relacl::text,pg_get_triggerdef(t.oid) FROM pg_class c JOIN pg_trigger t ON t.tgrelid=c.oid
                    WHERE c.oid='letter_document'::regclass ORDER BY t.tgname""")
                old_guards = db.execute(guards).all()
                with Operations.context(MigrationContext.configure(db)):
                    migration.downgrade()
                    assert db.scalar(text("SELECT to_regprocedure('organization_quota_usage(uuid)')")) is None
                    assert not db.scalar(text("""SELECT EXISTS(SELECT 1 FROM information_schema.columns
                        WHERE table_schema='public' AND table_name='letter_document' AND column_name='size_bytes')"""))
                    assert db.execute(columns).all() == old_columns
                    assert db.execute(originals).all() == old_rows
                    assert db.execute(guards).all() == old_guards
                    migration.upgrade()
                assert db.execute(metadata).one() == function
                assert db.execute(guards).all() == old_guards
                assert db.execute(originals).all() == old_rows
            finally:
                restore.rollback()
    finally:
        engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("foreign_target", [False, True])
async def test_quota_function_refuses_foreign_targets_and_nonmembers(rent_world, foreign_target):
    from sqlalchemy.exc import DBAPIError
    from app.core.db import get_tenant_session
    with pytest.raises(DBAPIError) as error:
        async with get_tenant_session(rent_world["org"]) as db:
            await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),
                {"actor": str(rent_world["admin"] if foreign_target else rent_world["foreign_admin"])})
            await db.scalar(text("SELECT organization_quota_usage(:org)"),
                {"org": rent_world["foreign_org"] if foreign_target else rent_world["org"]})
    assert error.value.orig.sqlstate == "42501"


@pytest.mark.asyncio
async def test_selfhost_document_writes_take_no_organization_quota_locks(rent_world, monkeypatch, tmp_path):
    from app.core import storage
    from app.core.db import get_tenant_session
    from app.services import quota_service
    monkeypatch.setattr(settings, "deployment_mode", "selfhost")
    def forbidden(*args, **kwargs):
        pytest.fail("Selfhost took an organization quota lock")
    monkeypatch.setattr(quota_service, "lock_quota", forbidden)
    store = storage.LocalFsDocumentStore(tmp_path)
    async with get_tenant_session(rent_world["org"]) as db:
        result = await storage.put_document(db, rent_world["org"], b"selfhost", content_type="application/pdf", store=store)
    assert store.get(result.uri) == b"selfhost"
    assert not (tmp_path / ".upload-locks").exists()
    assert not (tmp_path / ".file-locks").exists()
    assert not db.info.get("storage_write_locks")


@pytest.mark.asyncio
async def test_selfhost_duplicate_survives_the_creators_rollback(monkeypatch, tmp_path):
    import asyncio
    from types import SimpleNamespace
    from sqlalchemy.orm import Session
    from app.core import storage
    monkeypatch.setattr(settings, "deployment_mode", "selfhost")
    store = storage.LocalFsDocumentStore(tmp_path)
    org = uuid.uuid4()
    first, second = Session(), Session()
    first.begin()
    second.begin()
    def proxy(session):
        return SimpleNamespace(info=session.info, sync_session=session)
    attempt = asyncio.Event()
    original = storage.run_in_threadpool
    async def tracked(function, *args, **kwargs):
        if function is storage.fcntl.flock:
            attempt.set()
        return await original(function, *args, **kwargs)
    task = waiting = None
    try:
        await storage.put_document(proxy(first), org, b"shared", content_type="application/pdf", store=store)
        monkeypatch.setattr(storage, "run_in_threadpool", tracked)
        task = asyncio.create_task(storage.put_document(proxy(second), org, b"shared", content_type="application/pdf", store=store))
        waiting = asyncio.create_task(attempt.wait())
        done, _ = await asyncio.wait({task, waiting}, timeout=3, return_when=asyncio.FIRST_COMPLETED)
        assert done, "Second upload neither reached its file lock nor completed"
        if task in done:
            second.commit()
        first.rollback()
        reference = await task
        if second.in_transaction():
            second.commit()
        assert store.get(reference.uri) == b"shared"
    finally:
        first.close()
        if waiting:
            waiting.cancel()
        if task:
            await asyncio.gather(task, return_exceptions=True)
        second.close()


@pytest.mark.asyncio
async def test_hosted_storage_locks_the_shared_directory_without_creating_lock_files(monkeypatch, tmp_path):
    import os
    import stat
    from types import SimpleNamespace
    from sqlalchemy.orm import Session
    from app.core import storage
    monkeypatch.setattr(settings, "deployment_mode", "hosted")
    store = storage.LocalFsDocumentStore(tmp_path)
    session = Session()
    session.begin()
    try:
        org = uuid.uuid4()
        await storage.lock_document_storage(SimpleNamespace(info=session.info), org, store)
        handle = next(iter(session.info["storage_write_locks"].values()))
        metadata = os.fstat(handle if isinstance(handle, int) else handle.fileno())
        assert stat.S_ISDIR(metadata.st_mode)
        assert metadata.st_ino == (tmp_path / str(org)).stat().st_ino
        assert list(tmp_path.rglob("*.*")) == []
    finally:
        session.close()


@pytest.mark.asyncio
async def test_usage_and_uploads_use_database_sizes_without_a_directory_walk(rent_world, monkeypatch, tmp_path):
    from app.core import storage
    monkeypatch.setattr(settings, "deployment_mode", "hosted")
    monkeypatch.setattr(storage, "_store", storage.LocalFsDocumentStore(tmp_path))
    def forbidden(*args):
        pytest.fail("Request walked the organization directory")
    monkeypatch.setattr(storage.LocalFsDocumentStore, "organization_bytes", forbidden)
    async with api_client(rent_world) as api:
        upload = dict(data={"property_id": str(rent_world["property"])},
            files={"file": ("a.pdf", b"%PDF-123", "application/pdf")})
        for _ in range(2):
            answer = await api.post("/api/attachments", **upload)
            assert answer.status_code == 201, answer.text
        summary = await api.get("/api/organizations/quota")
        assert summary.status_code == 200, summary.text
        assert summary.json()["usage"]["storage_bytes"] == 8
        assert not (tmp_path / ".upload-locks").exists()


@pytest.mark.asyncio
async def test_letter_and_mail_sizes_share_the_deduplicated_database_usage(rent_world, monkeypatch, tmp_path, rent_db):
    import json
    from app.core import storage
    from tests.test_letters import create_template, issue_body, add_recipient
    monkeypatch.setattr(settings, "deployment_mode", "hosted")
    monkeypatch.setattr(storage, "_store", storage.LocalFsDocumentStore(tmp_path))
    await add_recipient(rent_world)
    async with api_client(rent_world) as api:
        template = await create_template(api)
        issued = await api.post("/api/letters/issue", json=issue_body(rent_world, template))
        assert issued.status_code == 201, issued.text
        assert len(issued.json()) == 2
        sizes = []
        for document in issued.json():
            sizes.append(len((await api.get(document["pdf_url"])).content))
        assert (await api.get("/api/organizations/quota")).json()["usage"]["storage_bytes"] == sum(sizes)
        row = (await rent_db.execute(text("SELECT storage_uri,size_bytes FROM letter_document WHERE organization_id=:org LIMIT 1"),
            {"org": rent_world["org"]})).one()
        # A mail receipt referencing the same original must not charge it again.
        for uri, size in ((row.storage_uri, row.size_bytes), ("synthetic/mail.pdf", 11)):
            await rent_db.execute(text("""INSERT INTO audit_event(organization_id,entity_type,action,payload_diff)
                VALUES (:org,'dunning_notice','mail_prepared',CAST(:payload AS jsonb))"""),
                {"org": rent_world["org"], "payload": json.dumps({"storage_uri": uri, "bytes": size})})
        await rent_db.commit()
        assert (await api.get("/api/organizations/quota")).json()["usage"]["storage_bytes"] == sum(sizes) + 11


@pytest.mark.asyncio
async def test_quota_recount_records_legacy_and_orphan_sizes_idempotently(rent_world, monkeypatch, tmp_path, capsys, rent_db):
    from app import admin_cli
    from app.core import storage
    monkeypatch.setattr(settings, "document_storage_root", str(tmp_path))
    store = storage.LocalFsDocumentStore(tmp_path)
    key = storage.make_storage_key(str(rent_world["org"]), storage.compute_sha256(b"legacy"))
    store.put(key, b"legacy", content_type="application/pdf")
    monkeypatch.setattr("sys.argv", ["admin_cli", "quota-recount", "--org", str(rent_world["org"])])
    for _ in range(2):
        admin_cli.main()
        assert "6 bytes" in capsys.readouterr().out
        async with api_client(rent_world) as api:
            summary = await api.get("/api/organizations/quota")
            assert summary.json()["usage"]["storage_bytes"] == 6
        assert await rent_db.scalar(text("SELECT count(*) FROM audit_event WHERE organization_id=:org AND action='quota_recount'"),
            {"org": rent_world["org"]}) == 1
    assert store.get(key) == b"legacy"


@pytest.mark.asyncio
async def test_recount_does_not_add_permanent_charges_for_sized_live_rows(rent_world, rent_db, monkeypatch, tmp_path, capsys):
    from app import admin_cli
    from app.core import storage
    monkeypatch.setattr(settings, "deployment_mode", "hosted")
    monkeypatch.setattr(settings, "document_storage_root", str(tmp_path))
    monkeypatch.setattr(storage, "_store", storage.LocalFsDocumentStore(tmp_path))
    async with api_client(rent_world) as api:
        response = await api.post("/api/attachments", data={"property_id": str(rent_world["property"])},
            files={"file": ("a.pdf", b"%PDF-123", "application/pdf")})
        assert response.status_code == 201, response.text
        before = (await api.get("/api/organizations/quota")).json()["usage"]["storage_bytes"]
        admin_cli.quota_recount(["--org", str(rent_world["org"])])
        assert "8 bytes" in capsys.readouterr().out
        assert (await api.get("/api/organizations/quota")).json()["usage"]["storage_bytes"] == before
    assert await rent_db.scalar(text("SELECT count(*) FROM audit_event WHERE organization_id=:org AND action='quota_recount'"), {"org": rent_world["org"]}) == 0


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


@pytest.mark.parametrize("kind,code", [
    ("storage_bytes", "QUOTA_STORAGE_EXCEEDED"),
    ("properties", "QUOTA_PROPERTIES_EXCEEDED"),
    ("units", "QUOTA_UNITS_EXCEEDED"),
    ("upload_bytes", "UPLOAD_TOO_LARGE"),
    ("ai_budget_usd", "ORGANIZATION_AI_BUDGET_EXCEEDED"),
])
def test_quota_refusals_include_a_stable_code(kind, code):
    from app.core.quotas import FREE_PLAN
    from app.services.quota_service import require_quota
    with pytest.raises(HTTPException) as error:
        require_quota(getattr(FREE_PLAN, kind), kind, 1, plan=FREE_PLAN)
    assert error.value.detail["code"] == code
    assert isinstance(error.value.detail["message"], str)


def test_property_row_locks_follow_quota_admission():
    import inspect
    from app.services.letter_service import LetterService
    from app.services.declaration_approval_service import DeclarationApprovalService
    from app.services.master_creation_service import create_unit
    for function, admission, row_lock in (
        (LetterService.issue, "await lock_quota(", "FOR SHARE"),
        (DeclarationApprovalService.approve, "await lock_quota(", "get_by_id_for_update("),
        (create_unit, "await require_counts(", "await _require_confirmed_mea_scale("),
    ):
        source = inspect.getsource(function)
        assert admission in source, function.__qualname__
        assert source.index(admission) < source.index(row_lock), function.__qualname__


@pytest.mark.asyncio
async def test_selfhost_upload_safety_refusal_uses_the_actual_limit(monkeypatch):
    from app.core.uploads import read_upload_limited
    from tests.test_upload_limits import ChunkedUpload
    monkeypatch.setattr(settings, "deployment_mode", "selfhost")
    with pytest.raises(HTTPException) as error:
        await read_upload_limited(ChunkedUpload(b"abcde"), max_bytes=4)
    assert error.value.detail == {"code": "UPLOAD_TOO_LARGE", "message": "Datei zu groß (maximal 4 Bytes). Bitte wählen Sie eine kleinere Datei."}


@pytest.mark.asyncio
@pytest.mark.parametrize("copies", [1, 2])
@pytest.mark.parametrize("rollback", [None, "outer", "savepoint", "released_savepoint"])
async def test_accepted_unprotected_deletion_unlinks_only_the_last_committed_reference(
    monkeypatch, tmp_path, copies, rollback
):
    """Current mandate classes reject deletion; simulate an accepted repository delete.

    Do not disable quarantine or immutability triggers to create a deletable class.
    The real session exercises commit/savepoint callbacks; the repository and its
    guarded aggregate are the boundary doubles for a permitted document class.
    """
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from sqlalchemy.orm import Session
    from app.core import storage
    from app.services import mandate_document_service, quota_service
    from app.services.mandate_document_service import MandateDocumentService
    monkeypatch.setattr(settings, "deployment_mode", "hosted")
    store = storage.LocalFsDocumentStore(tmp_path)
    monkeypatch.setattr(storage, "_store", store)
    org, actor = uuid.uuid4(), uuid.uuid4()
    content = b"unprotected source"
    key = storage.make_storage_key(str(org), storage.compute_sha256(content))
    store.put(key, content, content_type="application/pdf")
    rows = {uuid.uuid4(): SimpleNamespace(accounting_scope_id=None, storage_uri=key,
        content_sha256=storage.compute_sha256(content), content_byte_size=len(content),
        storage_backend="LOCAL_FS", doc_type="SONSTIGES") for _ in range(copies)}
    db = Session()
    db.begin()
    proxy = SimpleNamespace(info=db.info, sync_session=db, flush=AsyncMock())
    service = MandateDocumentService(proxy, org, actor)
    service._repo.get_by_id = AsyncMock(side_effect=lambda ident: rows.get(ident))
    service._repo.delete = AsyncMock(side_effect=lambda doc: rows.pop(next(ident for ident, row in rows.items() if row is doc)))
    service._audit.create = AsyncMock()
    usage = AsyncMock(side_effect=lambda *_: {"storage_bytes": len(content) if rows else 0})
    monkeypatch.setattr(quota_service, "database_usage", usage)
    monkeypatch.setattr(quota_service, "lock_quota", AsyncMock())
    monkeypatch.setattr(mandate_document_service, "lock_quota", AsyncMock())
    # The service imports the shared aggregate, not a source-filtered row count.
    monkeypatch.setattr(mandate_document_service, "database_usage", usage, raising=False)
    try:
        for index, ident in enumerate(list(rows)):
            nested = db.begin_nested() if rollback in {"savepoint", "released_savepoint"} else None
            await service.delete(ident)
            assert store.get(key) == content  # No unlink before the owning commit.
            if nested:
                nested.commit() if rollback == "released_savepoint" else nested.rollback()
            if rollback:
                db.rollback() if db.in_transaction() else None
                assert store.get(key) == content
                # Reset only the boundary double, preserving the same session hooks.
                if index == copies - 1:
                    rows[ident] = SimpleNamespace(accounting_scope_id=None, storage_uri=key,
                        content_sha256=storage.compute_sha256(content), content_byte_size=len(content),
                        storage_backend="LOCAL_FS", doc_type="SONSTIGES")
                    db.begin()
                    await service.delete(ident)
                    db.commit()
                else:
                    db.begin()
            else:
                db.commit()
                if rows:
                    assert store.get(key) == content
                    db.begin()
        assert not store.exists(key)
        assert (await quota_service.database_usage(proxy, org))["storage_bytes"] == store.organization_bytes(str(org)) == 0
        # Exercise the real recount scanner after unlink. Its connection is a
        # boundary double, like the accepted deletion: current classes retain.
        from contextlib import redirect_stdout
        from io import StringIO
        from unittest.mock import MagicMock
        from app import admin_cli
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = (org,)
        cursor.fetchall.return_value = []
        monkeypatch.setattr(admin_cli.psycopg2, "connect", lambda *_: connection)
        monkeypatch.setattr(settings, "document_storage_root", str(tmp_path))
        output = StringIO()
        with redirect_stdout(output):
            admin_cli.quota_recount(["--org", str(org)])
        assert "0 originals, 0 bytes" in output.getvalue()
        assert not any("INSERT" in call.args[0] for call in cursor.execute.call_args_list)
    finally:
        db.close()


@pytest.mark.asyncio
async def test_quota_is_authenticated_and_scoped_to_the_current_organization(
        rent_world, client_unauth, monkeypatch):
    monkeypatch.setattr(settings, "deployment_mode", "hosted")
    assert (await client_unauth.get("/api/organizations/quota")).status_code == 401
    async with api_client(rent_world, "reader") as api:
        answer = await api.get("/api/organizations/quota")
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
        assert "kontingent erschöpft" in refused.json()["detail"]["message"]
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
        assert refused.json()["detail"]["message"].startswith("Speicherkontingent erschöpft")
        assert sorted(tmp_path.rglob("*.bin")) == before
        listed = await api.get("/api/attachments", params=upload["data"])
        assert len(listed.json()["items"]) == 1
        assert (await api.get("/api/organizations/quota")).json()["usage"]["storage_bytes"] == 8


@pytest.mark.asyncio
@pytest.mark.parametrize("nested", [False, True])
async def test_database_failure_removes_new_bytes_and_preserves_committed_duplicates(
        rent_world, tmp_path, nested, monkeypatch):
    from app.core.db import get_tenant_session
    from app.core.storage import LocalFsDocumentStore, put_document
    monkeypatch.setattr(settings, "deployment_mode", "hosted")
    store = LocalFsDocumentStore(tmp_path)
    async with get_tenant_session(rent_world["org"]) as db:
        await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {"actor": str(rent_world["admin"])})
        kept = await put_document(db, rent_world["org"], b"committed", content_type="application/pdf", store=store)
    before = sorted(tmp_path.rglob("*.bin"))
    with pytest.raises(RuntimeError, match="DB write failed"):
        async with get_tenant_session(rent_world["org"]) as db:
            await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {"actor": str(rent_world["admin"])})
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
async def test_failed_database_statement_removes_the_new_original(rent_world, tmp_path, monkeypatch):
    from sqlalchemy.exc import DBAPIError
    from app.core.db import get_tenant_session
    from app.core.storage import LocalFsDocumentStore, put_document
    monkeypatch.setattr(settings, "deployment_mode", "hosted")
    store = LocalFsDocumentStore(tmp_path)
    with pytest.raises(DBAPIError):
        async with get_tenant_session(rent_world["org"]) as db:
            await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {"actor": str(rent_world["admin"])})
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
        await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {"actor": str(rent_world["admin"])})
        await put_document(db, rent_world["org"], b"12345678", content_type="application/pdf", store=store)
    before = sorted(tmp_path.rglob("*.bin"))
    with pytest.raises(HTTPException) as error:
        async with get_tenant_session(rent_world["org"]) as db:
            await put_document(db, rent_world["org"], b"123456789", content_type="application/pdf", store=store)
    assert error.value.status_code == 413
    assert error.value.detail["message"].startswith("Datei zu groß")
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
        assert (await api.get("/api/organizations/quota")).json()["plan"] == "unlimited"


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
        assert response.json()["detail"]["message"].startswith("Das monatliche KI-Budget")
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
        assert refused.json()["detail"]["message"].startswith("Das monatliche KI-Budget")
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
    summary = await client_a_admin.get("/api/organizations/quota")
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
        assert second.json()["detail"]["message"].startswith("Das monatliche KI-Budget")
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
        before = (await api.get("/api/organizations/quota")).json()["usage"]
        assert before["properties"] > 0 and before["units"] > 0
        async with synthetic_enforcement(scope_seed):
            assert (await api.get("/api/properties")).json() == []
            assert (await api.get("/api/units")).json() == []
            summary = await api.get("/api/organizations/quota")
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
