"""Property endpoints -- CRUD within the current organization."""

from __future__ import annotations

import uuid
from decimal import Decimal
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi.responses import Response
from app.services.master_delete_service import MasterDeleteService, master_delete_writer_db
from app.core.live_principal import get_live_member_db, get_live_writer_db, get_live_publication_db
from app.services.property_master_service import require_property_master

from app.core.tenant import (
    AuthContext,
    get_current_user,
    get_db,
    require_write_dep,
)
from app.repositories.property_repo import PropertyRepository
from app.repositories.unit_repo import UnitRepository
from app.repositories.audit_repo import AuditRepository
from app.models.property import Property
from app.models.audit_event import AuditEvent

from app.repositories.pagination import Page, PageQuery, page_query, page_rows

from app.services.master_creation_service import (
    PropertyCreate, PropertyFacts, create_property as create_property_record, _validate_management_type,
)

from app.services.master_import_service import (
    MasterImportRequest, MasterImportApplyRequest, MasterImportService, template_csv,
)

router = APIRouter(prefix="/api/properties", tags=["properties"])


@router.get('/import/template.csv')
async def master_import_template(
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_writer_db),
):
    return Response(template_csv(), media_type='text/csv; charset=utf-8',
        headers={'Content-Disposition': 'attachment; filename="master-import-template.csv"'})


@router.post('/import/preview')
async def preview_master_import(
    body: MasterImportRequest,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_writer_db),
):
    return await MasterImportService(db, auth).preview(body)


async def master_import_publication_db(
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_db, scope='function'),
):
    return await get_live_publication_db(auth=auth, db=db)


@router.post('/import/apply', status_code=201)
async def apply_master_import(
    body: MasterImportApplyRequest,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(master_import_publication_db),
):
    return await MasterImportService(db, auth).apply(body)


class PropertyOut(BaseModel):
    id: str
    name: str
    street: str
    zip_code: str
    city: str
    management_type: str
    fiscal_year_start_month: int
    construction_year: int | None
    total_area: str | None
    mea_denominator: int | None
    mea_scale_status: str

    model_config = {"from_attributes": True}


class PropertyUpdate(PropertyFacts):
    name: str | None = None
    street: str | None = None
    zip_code: str | None = None
    city: str | None = None
    management_type: str | None = None
    fiscal_year_start_month: int | None = None


class MeaScaleConfirmation(BaseModel):
    denominator: int = Field(ge=1, le=1_000_000_000_000)
    expected_scale_status: str
    confirm_existing_values_as_numerators: Literal[True]


@router.get("", response_model=list[PropertyOut])
async def list_properties(
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_live_member_db),
):
    repo = PropertyRepository(db, auth.organization_id)
    items = await repo.list_all()
    return [_to_out(p) for p in items]


@router.get("/page", response_model=Page[PropertyOut])
async def page_properties(
    query: PageQuery = Depends(page_query),
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_live_member_db),
):
    items, cursor = await page_rows(db, auth, Property, Property.name, [Property.name, Property.street, Property.city], query)
    return Page(items=[_to_out(item) for item in items], next_cursor=cursor)


@router.get("/{property_id}", response_model=PropertyOut)
async def get_property(
    property_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_live_member_db),
):
    repo = PropertyRepository(db, auth.organization_id)
    p = await repo.get_by_id(property_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Property not found")
    return _to_out(p)


class SettlementPeriodOut(BaseModel):
    year: int
    label: str
    period_start: date
    period_end: date


@router.get("/{property_id}/settlement-periods", response_model=list[SettlementPeriodOut])
async def settlement_periods(
    property_id: uuid.UUID,
    year: int | None = Query(default=None, ge=3, le=9997),
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_live_member_db),
):
    from app.engine.periods import period_starting_in, period_label

    prop = await PropertyRepository(db, auth.organization_id).get_by_id(property_id)
    if prop is None:
        raise HTTPException(404, "Property not found")
    reference_year = year if year is not None else date.today().year
    periods = []
    for start_year in range(reference_year - 2, reference_year + 2):
        start, end = period_starting_in(prop.fiscal_year_start_month, start_year)
        periods.append(SettlementPeriodOut(year=start_year, label=period_label(start, end),
            period_start=start, period_end=end))
    return periods


@router.post("", response_model=PropertyOut, status_code=201)
async def create_property(
    body: PropertyCreate,
    idempotency_key: uuid.UUID | None = Header(default=None),
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_publication_db),
):
    return _to_out(await create_property_record(db, auth, body, idempotency_key))


@router.put("/{property_id}", response_model=PropertyOut)
async def update_property(
    property_id: uuid.UUID,
    body: PropertyUpdate,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_writer_db),
):
    await require_property_master(db, auth.organization_id, property_id)
    repo = PropertyRepository(db, auth.organization_id)
    prop = await repo.get_by_id(property_id)
    if prop is None:
        raise HTTPException(status_code=404, detail="Property not found")

    if body.management_type is not None:
        _validate_management_type(body.management_type)

    for field in ("name", "street", "zip_code", "city", "management_type", "fiscal_year_start_month"):
        val = getattr(body, field)
        if val is not None:
            setattr(prop, field, val)

    if "construction_year" in body.model_fields_set:
        prop.construction_year = body.construction_year
    if "total_area" in body.model_fields_set:
        prop.total_area = Decimal(body.total_area) if body.total_area is not None else None

    await db.flush()
    return _to_out(prop)


@router.post("/{property_id}/mea-scale/confirm", response_model=PropertyOut)
async def confirm_mea_scale(
    property_id: uuid.UUID,
    body: MeaScaleConfirmation,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_writer_db),
):
    await require_property_master(db, auth.organization_id, property_id)
    property_repo = PropertyRepository(db, auth.organization_id)
    prop = await property_repo.get_by_id_for_update(property_id)
    if prop is None:
        raise HTTPException(status_code=404, detail="Property not found")
    if (
        prop.mea_scale_status != "LEGACY_UNCONFIRMED"
        or body.expected_scale_status != prop.mea_scale_status
    ):
        raise HTTPException(status_code=409, detail="MEA_SCALE_STATUS_CHANGED")

    units = await UnitRepository(db, auth.organization_id).list_by_property_for_update(
        property_id
    )
    numerator_sum = Decimal(0)
    denominator = Decimal(body.denominator)
    for unit in units:
        if unit.mea is None:
            continue
        mea = unit.mea
        if not mea.is_finite() or mea < 0 or mea != mea.to_integral_value():
            raise HTTPException(status_code=409, detail="LEGACY_MEA_NOT_INTEGER")
        if mea > denominator:
            raise HTTPException(
                status_code=409, detail="LEGACY_MEA_EXCEEDS_DENOMINATOR"
            )
        numerator_sum += mea

    if numerator_sum > denominator:
        raise HTTPException(status_code=409, detail="LEGACY_MEA_EXCEEDS_DENOMINATOR")

    old_scale = {
        "mea_scale_status": prop.mea_scale_status,
        "mea_denominator": prop.mea_denominator,
    }
    prop.mea_scale_status = "CONFIRMED"
    prop.mea_denominator = body.denominator
    await AuditRepository(db, auth.organization_id).create(
        AuditEvent(
            actor_user_id=auth.user_id,
            entity_type="property",
            entity_id=prop.id,
            action="property.mea_scale_confirmed",
            payload_diff={
                "old_scale": old_scale,
                "new_scale": {
                    "mea_scale_status": prop.mea_scale_status,
                    "mea_denominator": prop.mea_denominator,
                },
                "existing_values_confirmed_as_numerators": True,
            },
        )
    )
    return _to_out(prop)


def _to_out(p: Property) -> PropertyOut:
    return PropertyOut(
        id=str(p.id),
        name=p.name,
        street=p.street,
        zip_code=p.zip_code,
        city=p.city,
        management_type=p.management_type,
        fiscal_year_start_month=p.fiscal_year_start_month,
        construction_year=p.construction_year,
        total_area=format(p.total_area, ".2f") if p.total_area is not None else None,
        mea_denominator=p.mea_denominator,
        mea_scale_status=p.mea_scale_status,
    )


@router.delete("/{property_id}", status_code=204, response_class=Response)
async def delete_property(
    property_id: uuid.UUID,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(master_delete_writer_db),
):
    await MasterDeleteService(db, auth).delete("property", property_id)
    return Response(status_code=204)
