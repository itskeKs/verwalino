"""Session organization, quota summary and audited portable exports."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant import AuthContext, get_current_user, get_db, require_admin_dep
from app.repositories.organization_repo import OrganizationRepository
from app.core.live_principal import get_live_member_db
from app.services.quota_service import quota_summary
from app.core.live_principal import require_live_principal
from app.core.db import get_session_factory, set_tenant_context
from app.services.organization_export_service import create_export, download_export
from fastapi import Request
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask
from app.core.storage import attachment_content_disposition

router = APIRouter(prefix="/api/organizations", tags=["organizations"])


async def get_export_db(auth: AuthContext = Depends(require_admin_dep)):
    # Commit the successful audit and artifact before the response discloses
    # bytes. A failure rolls back and removes the unpublished artifact.
    async with get_session_factory()() as db:
        try:
            async with db.begin():
                await set_tenant_context(db, auth.organization_id)
                from sqlalchemy import text
                await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {"actor": str(auth.user_id)})
                await require_live_principal(db, auth, writer=False, administrator=True)
                yield db
        except BaseException:
            for stream in db.info.pop("export_response_streams", []):
                stream.close()
            raise


def _export_response(db, path, export_id):
    # Open before releasing the transaction. Replacing an old artifact can then
    # unlink its pathname without interrupting an already authorized download.
    stream = path.open("rb")
    db.info.setdefault("export_response_streams", []).append(stream)
    def chunks():
        try:
            while chunk := stream.read(64 * 1024):
                yield chunk
        finally:
            stream.close()
    return StreamingResponse(chunks(), media_type="application/gzip", background=BackgroundTask(stream.close),
        headers={"X-Export-ID": str(export_id), "Cache-Control": "private, no-store",
                 "Content-Disposition": attachment_content_disposition(f"verwalino-{export_id}.tar.gz"),
                 "X-Content-Type-Options": "nosniff"})


@router.post("/export")
async def export_organization(request: Request,
    auth: AuthContext = Depends(require_admin_dep),
    db: AsyncSession = Depends(get_export_db, scope="function"),
):
    export_id, path = await create_export(db, auth, app_version=request.app.version)
    return _export_response(db, path, export_id)


@router.get("/export/{export_id}")
async def get_organization_export(export_id: uuid.UUID,
    auth: AuthContext = Depends(require_admin_dep),
    db: AsyncSession = Depends(get_export_db, scope="function"),
):
    path = await download_export(db, auth, export_id)
    return _export_response(db, path, export_id)


class QuotaUsage(BaseModel):
    storage_bytes: int
    properties: int
    units: int
    ai_budget_usd: str


class QuotaLimits(BaseModel):
    storage_bytes: int | None
    upload_bytes: int | None
    properties: int | None
    units: int | None
    ai_budget_usd: str | None


class QuotaOut(BaseModel):
    plan: str
    usage: QuotaUsage
    limits: QuotaLimits


@router.get("/quota", response_model=QuotaOut)
async def get_organization_quota(
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_live_member_db),
):
    return await quota_summary(db, auth.organization_id)


class OrgOut(BaseModel):
    id: str
    name: str
    import_available: bool = False
    deletion: dict | None = None

    model_config = {"from_attributes": True}


@router.get("/me", response_model=OrgOut)
async def get_my_org(
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the current user's organization."""
    repo = OrganizationRepository(db)
    org = await repo.get_by_id(auth.organization_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    from app.services.organization_deletion_service import deletion_status
    from app.services.organization_import_service import admission
    empty = (await admission(db, auth.organization_id))["empty"] if auth.role == "OWNER_ADMIN" else False
    return OrgOut(id=str(org.id), name=org.name, import_available=empty, deletion=await deletion_status(db, auth.organization_id))


async def get_import_db(auth: AuthContext = Depends(require_admin_dep), db: AsyncSession = Depends(get_db, scope="function")):
    await require_live_principal(db, auth, writer=True, exclusive=True, administrator=True)
    return db


@router.post("/import")
async def import_archive(file: UploadFile = File(...), auth: AuthContext = Depends(require_admin_dep),
                         db: AsyncSession = Depends(get_import_db)):
    from app.services.organization_import_service import import_organization
    return await import_organization(db, auth, file.file)


class DeletionConfirmation(BaseModel):
    organization_name: str


@router.post("/deletion")
async def request_organization_deletion(auth: AuthContext = Depends(require_admin_dep),
                                      db: AsyncSession = Depends(get_import_db)):
    from app.services.organization_deletion_service import request_deletion
    return await request_deletion(db, auth)


@router.post("/deletion/confirm")
async def confirm_organization_deletion(body: DeletionConfirmation, auth: AuthContext = Depends(require_admin_dep),
                                      db: AsyncSession = Depends(get_import_db)):
    from app.services.organization_deletion_service import confirm_deletion
    return await confirm_deletion(db, auth, body.organization_name)


@router.post("/deletion/cancel")
async def cancel_organization_deletion(auth: AuthContext = Depends(require_admin_dep),
                                     db: AsyncSession = Depends(get_import_db)):
    from app.services.organization_deletion_service import cancel_deletion
    return await cancel_deletion(db, auth)
