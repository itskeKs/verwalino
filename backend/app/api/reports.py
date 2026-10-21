"""Read-only financial object reports."""
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.live_principal import get_live_member_db
from app.core.tenant import AuthContext, get_current_user
from app.services.report_service import PropertyYearReport, ReportService

router = APIRouter(prefix='/api/reports', tags=['reports'])


@router.get('/property-year', response_model=PropertyYearReport)
async def property_year_report(
    property_id: UUID,
    year: Annotated[int, Query(ge=1, le=9999)],
    accounting_scope_id: UUID | None = None,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_live_member_db),
) -> PropertyYearReport:
    return await ReportService(db, auth.organization_id, auth.user_id).property_year(
        property_id, year, accounting_scope_id)
