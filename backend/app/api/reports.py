"""Read-only financial object reports."""
import csv
import io
from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.csv_exports import csv_text as _csv_text, csv_money as _csv_money
from app.core.live_principal import get_live_member_db
from app.core.rbac import ScopeCapability
from app.core.storage import attachment_content_disposition
from app.core.tenant import AuthContext, get_current_user
from app.services.authorization_service import AuthorizationService
from app.services.report_service import PropertyYearReport, ReportService
from app.services.datev_export import DatevExportService, DatevRequest, render_datev

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


async def _require_year_export(db: AsyncSession, auth: AuthContext, report: PropertyYearReport) -> None:
    if report.accounting_scope_id is not None:
        await AuthorizationService(db, auth).require(report.accounting_scope_id, ScopeCapability.EXPORT)
    if not await db.scalar(text(
        "SELECT property_financial_disclosure_access(:org,:property,'export')"
    ), {'org': auth.organization_id, 'property': report.property_id}):
        raise HTTPException(404, 'Financial source export not accessible')
    if not await db.scalar(text(
        "SELECT report_scope_source_access(:org,:scope,'export')"
    ), {'org': auth.organization_id, 'scope': report.accounting_scope_id}):
        raise HTTPException(404, 'Complete report sources not exportable')


def _render_property_year_csv(report: PropertyYearReport) -> bytes:
    output = io.StringIO(newline='')
    writer = csv.writer(output, delimiter=';', lineterminator='\r\n')
    writer.writerow(['Objekt', 'Jahr', 'Bereich', 'Zahlungstyp', 'Kategorie', 'Umlagefähigkeit', 'Betrag (EUR)'])
    name = _csv_text(report.property_name)
    for row in report.income:
        writer.writerow([name, report.year, 'Einnahmen', _csv_text(row.payment_type), '', '', _csv_money(row.amount)])
    for row in report.expenses:
        writer.writerow([name, report.year, 'Ausgaben', '', _csv_text(row.category_name),
                         _csv_text(row.tenant_apportionability), _csv_money(row.amount)])
    for label, amount in [('Einnahmen gesamt', report.income_total),
                          ('Ausgaben gesamt', report.expense_total), ('Saldo', report.balance)]:
        writer.writerow([name, report.year, label, '', '', '', _csv_money(amount)])
    return output.getvalue().encode('utf-8-sig')


@router.get('/property-year.csv')
async def property_year_csv(
    property_id: UUID,
    year: Annotated[int, Query(ge=1, le=9999)],
    accounting_scope_id: UUID | None = None,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_live_member_db),
) -> Response:
    report = await ReportService(db, auth.organization_id, auth.user_id).property_year(
        property_id, year, accounting_scope_id)
    await _require_year_export(db, auth, report)
    content = _render_property_year_csv(report)
    # Keep the source context/transaction locks through rendering and recheck expiry
    # before handing any bytes to the response (never stream a partial report).
    await _require_year_export(db, auth, report)
    return Response(content, media_type='text/csv; charset=utf-8', headers={
        'Content-Disposition': attachment_content_disposition(f'objekt-jahresuebersicht-{year}.csv'),
        'X-Content-Type-Options': 'nosniff',
        'Cache-Control': 'private, no-store',
    })


@router.get('/datev/sources')
async def datev_sources(
    property_id: UUID, period_start: date, period_end: date,
    accounting_scope_id: UUID | None = None,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_live_member_db),
):
    return await DatevExportService(db, auth).sources(
        property_id, period_start, period_end, accounting_scope_id)


@router.post('/datev/preview')
async def datev_preview(
    body: DatevRequest,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_live_member_db),
):
    preview, _, _ = await DatevExportService(db, auth).preview(body)
    return preview


@router.post('/datev.csv')
async def datev_csv(
    body: DatevRequest,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_live_member_db),
) -> Response:
    if body.expected_source_digest is None:
        raise HTTPException(422, 'Preview and confirm the complete batch before downloading')
    service = DatevExportService(db, auth)
    preview, rows, scope = await service.preview(body)
    if preview['source_digest'] != body.expected_source_digest:
        raise HTTPException(409, 'Financial sources or account configuration changed; preview again')
    content = render_datev(body, rows)
    await service.require_export(body.property_id, scope)
    return Response(content, media_type='text/csv; charset=utf-8', headers={
        'Content-Disposition': attachment_content_disposition(
            f'EXTF_Buchungsstapel_{body.period_start}_{body.period_end}.csv'),
        'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'private, no-store',
    })
