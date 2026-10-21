"""Fristenmonitor API -- M9 (read-only).

GET /api/fristen returns the assembled deadline monitor (BK §556 III hard
deadlines, provisional WEG/Wirtschaftsplan deadlines, overdue-items + retention
info). Read-only and available to all roles (it surfaces obligations, it does
not change anything). Tenant-scoped via the session as everywhere else.
"""

from __future__ import annotations

from datetime import date
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.live_principal import require_live_principal
from app.core.tenant import AuthContext, get_current_user, get_db
from app.services.fristen_service import FristenService

router = APIRouter(prefix="/api/fristen", tags=["fristen"])


async def fristen_reader_db(
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope='function'),
):
    await require_live_principal(db, auth, writer=False)
    role = await db.scalar(text('SELECT scope_actor_role(:org)'), {'org': auth.organization_id})
    if role not in {'OWNER_ADMIN', 'CLERK', 'READ_ONLY'}:
        raise HTTPException(403, 'Organization member role required')
    return db


class FristenContextOut(BaseModel):
    requires_accounting_scope: bool


@router.get('/context', response_model=FristenContextOut)
async def fristen_context(
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(fristen_reader_db),
):
    svc = FristenService(db, auth.organization_id, auth.user_id)
    return FristenContextOut(requires_accounting_scope=await svc.requires_accounting_scope())


class DeadlineItemOut(BaseModel):
    kind: str
    reference: str
    due_date: str | None
    status: str
    is_statutory: bool
    is_provisional: bool
    property_id: str | None
    detail: str


@router.get("", response_model=list[DeadlineItemOut])
async def list_fristen(
    reference_date: date | None = None,
    accounting_scope_id: uuid.UUID | None = None,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(fristen_reader_db),
):
    ref = reference_date or date.today()
    svc = FristenService(db, auth.organization_id, auth.user_id)
    items = await svc.build_monitor(ref, accounting_scope_id)
    return [
        DeadlineItemOut(
            kind=i.kind,
            reference=i.reference,
            due_date=i.due_date.isoformat() if i.due_date else None,
            status=i.status,
            is_statutory=i.is_statutory,
            is_provisional=i.is_provisional,
            property_id=str(i.property_id) if i.property_id else None,
            detail=i.detail,
        )
        for i in items
    ]
