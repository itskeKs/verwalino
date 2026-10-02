"""Cost entry endpoints -- CRUD + state machine transitions.

RBAC: GET for all roles; POST/PATCH/confirm/reject/correction require write role.
Foreign ID -> 404 (not 403), consistent with M0/M1.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant import AuthContext, get_current_user, get_db, require_write_dep
from app.models.cost_entry import CostEntry
from app.repositories.cost_entry_repo import CostEntryRepository
from app.services.cost_entry_service import CostEntryService

router = APIRouter(prefix="/api/cost-entries", tags=["cost-entries"])


class CostEntryOut(BaseModel):
    id: str
    organization_id: str
    property_id: str
    management_mandate_id: str | None
    cost_category_id: str
    cost_category_plan_version: int
    vendor_id: str | None
    invoice_date: date
    service_period_from: date
    service_period_to: date
    gross_amount: str
    vat_amount: str | None
    vat_rate: str | None
    document_number: str | None
    owner_distribution_key: str | None
    tenant_distribution_key: str | None
    tenant_apportionable: str
    apportionable_tenant_amount: str | None
    status: str
    source: str
    confirmed_at: str | None
    confirmed_by: str | None
    superseded_by_id: str | None

    model_config = {"from_attributes": True}


class CostEntryCreate(BaseModel):
    accounting_scope_id: uuid.UUID | None = None
    source_document_id: uuid.UUID | None = None
    property_id: str
    management_mandate_id: str | None = None
    cost_category_id: str
    cost_category_plan_version: int = 1
    vendor_id: str | None = None
    invoice_date: date
    service_period_from: date
    service_period_to: date
    gross_amount: str
    vat_amount: str | None = None
    vat_rate: str | None = None
    document_number: str | None = None
    owner_distribution_key: str | None = None
    tenant_distribution_key: str | None = None
    tenant_apportionable: str
    apportionable_tenant_amount: str | None = None
    source: str = "MANUAL"
    status: str = "needs_review"


class CostEntryUpdate(BaseModel):
    gross_amount: str | None = None
    vat_amount: str | None = None
    vat_rate: str | None = None
    invoice_date: date | None = None
    service_period_from: date | None = None
    service_period_to: date | None = None
    cost_category_id: str | None = None
    cost_category_plan_version: int | None = None
    vendor_id: str | None = None
    document_number: str | None = None
    owner_distribution_key: str | None = None
    tenant_distribution_key: str | None = None
    tenant_apportionable: str | None = None
    apportionable_tenant_amount: str | None = None


class CostEntryCorrectionCreate(BaseModel):
    gross_amount: str | None = None
    vat_amount: str | None = None
    vat_rate: str | None = None
    invoice_date: date | None = None
    service_period_from: date | None = None
    service_period_to: date | None = None
    cost_category_id: str | None = None
    cost_category_plan_version: int | None = None
    vendor_id: str | None = None
    document_number: str | None = None
    owner_distribution_key: str | None = None
    tenant_distribution_key: str | None = None
    tenant_apportionable: str | None = None
    apportionable_tenant_amount: str | None = None


@router.get("", response_model=list[CostEntryOut])
async def list_cost_entries(
    status: str | None = None,
    property_id: uuid.UUID | None = None,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    repo = CostEntryRepository(db, auth.organization_id)
    if status:
        items = await repo.list_by_status(status)
    elif property_id:
        items = await repo.list_by_property(property_id)
    else:
        items = await repo.list_all()
    return [_to_out(e) for e in items]


@router.get("/{entry_id}", response_model=CostEntryOut)
async def get_cost_entry(
    entry_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    repo = CostEntryRepository(db, auth.organization_id)
    entry = await repo.get_by_id(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Cost entry not found")
    return _to_out(entry)


@router.post("", response_model=CostEntryOut, status_code=201)
async def create_cost_entry(
    body: CostEntryCreate,
    idempotency_key: str | None = Header(None, max_length=128),
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_db),
):
    svc = CostEntryService(db, auth.organization_id, auth.user_id)
    entry = CostEntry(
        property_id=uuid.UUID(body.property_id),
        management_mandate_id=uuid.UUID(body.management_mandate_id) if body.management_mandate_id else None,
        cost_category_id=uuid.UUID(body.cost_category_id),
        cost_category_plan_version=body.cost_category_plan_version,
        vendor_id=uuid.UUID(body.vendor_id) if body.vendor_id else None,
        invoice_date=body.invoice_date,
        service_period_from=body.service_period_from,
        service_period_to=body.service_period_to,
        gross_amount=Decimal(body.gross_amount),
        vat_amount=Decimal(body.vat_amount) if body.vat_amount else None,
        vat_rate=Decimal(body.vat_rate) if body.vat_rate else None,
        document_number=body.document_number,
        owner_distribution_key=body.owner_distribution_key,
        tenant_distribution_key=body.tenant_distribution_key,
        tenant_apportionable=body.tenant_apportionable,
        apportionable_tenant_amount=Decimal(body.apportionable_tenant_amount) if body.apportionable_tenant_amount else None,
        status=body.status,
        source=body.source,
    )
    from app.services.idempotency_service import IdempotencyService
    entry = await svc.create(entry, accounting_scope_id=body.accounting_scope_id, source_document_id=body.source_document_id,
        idempotency_key=idempotency_key,
        request_digest=IdempotencyService.digest(body.model_dump(mode="json")))
    return _to_out(entry)


@router.patch("/{entry_id}", response_model=CostEntryOut)
async def update_cost_entry(
    entry_id: uuid.UUID,
    body: CostEntryUpdate,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_db),
):
    svc = CostEntryService(db, auth.organization_id, auth.user_id)
    updates = {}
    for field_name, value in body.model_dump(exclude_unset=True).items():
        if value is not None:
            if field_name in ("gross_amount", "vat_amount", "vat_rate", "apportionable_tenant_amount"):
                updates[field_name] = Decimal(value)
            elif field_name in ("cost_category_id", "vendor_id"):
                updates[field_name] = uuid.UUID(value)
            else:
                updates[field_name] = value
    entry = await svc.update_draft(entry_id, updates)
    return _to_out(entry)


@router.post("/{entry_id}/confirm", response_model=CostEntryOut)
async def confirm_cost_entry(
    entry_id: uuid.UUID,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_db),
):
    svc = CostEntryService(db, auth.organization_id, auth.user_id)
    entry = await svc.confirm(entry_id)
    return _to_out(entry)


@router.post("/{entry_id}/reject", response_model=CostEntryOut)
async def reject_cost_entry(
    entry_id: uuid.UUID,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_db),
):
    svc = CostEntryService(db, auth.organization_id, auth.user_id)
    entry = await svc.reject(entry_id)
    return _to_out(entry)


@router.post("/{entry_id}/correction", response_model=CostEntryOut, status_code=201)
async def create_correction(
    entry_id: uuid.UUID,
    body: CostEntryCorrectionCreate,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_db),
):
    svc = CostEntryService(db, auth.organization_id, auth.user_id)
    corrections = {}
    for field_name, value in body.model_dump(exclude_unset=True).items():
        if value is not None:
            if field_name in ("gross_amount", "vat_amount", "vat_rate", "apportionable_tenant_amount"):
                corrections[field_name] = Decimal(value)
            elif field_name in ("cost_category_id", "vendor_id"):
                corrections[field_name] = uuid.UUID(value)
            else:
                corrections[field_name] = value
    entry = await svc.create_correction_version(entry_id, corrections)
    return _to_out(entry)


def _to_out(e: CostEntry) -> CostEntryOut:
    return CostEntryOut(
        id=str(e.id),
        organization_id=str(e.organization_id),
        property_id=str(e.property_id),
        management_mandate_id=str(e.management_mandate_id) if e.management_mandate_id else None,
        cost_category_id=str(e.cost_category_id),
        cost_category_plan_version=e.cost_category_plan_version,
        vendor_id=str(e.vendor_id) if e.vendor_id else None,
        invoice_date=e.invoice_date,
        service_period_from=e.service_period_from,
        service_period_to=e.service_period_to,
        gross_amount=str(e.gross_amount),
        vat_amount=str(e.vat_amount) if e.vat_amount is not None else None,
        vat_rate=str(e.vat_rate) if e.vat_rate is not None else None,
        document_number=e.document_number,
        owner_distribution_key=e.owner_distribution_key,
        tenant_distribution_key=e.tenant_distribution_key,
        tenant_apportionable=e.tenant_apportionable,
        apportionable_tenant_amount=str(e.apportionable_tenant_amount) if e.apportionable_tenant_amount is not None else None,
        status=e.status,
        source=e.source,
        confirmed_at=str(e.confirmed_at) if e.confirmed_at else None,
        confirmed_by=str(e.confirmed_by) if e.confirmed_by else None,
        superseded_by_id=str(e.superseded_by_id) if e.superseded_by_id else None,
    )
