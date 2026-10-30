"""Payment + Mietkonto endpoints -- CRUD, book, Storno, Soll/Ist views.

m6-zahlungen-mietkonto.md section 8.4.

RBAC (like M0-M4): GET for all roles; POST/PATCH/book/reverse/DELETE require a
write role. Foreign ID -> 404 (not 403). A booked payment is never hard-deleted
(409 -> reverse via Storno); the DB trigger is the final guard.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import date
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant import AuthContext, get_current_user, get_db, require_write_dep
from app.core.live_principal import get_live_member_db, get_live_writer_db
from app.core.csv_exports import csv_text, csv_money
from app.core.rbac import ScopeCapability
from app.core.storage import attachment_content_disposition
from app.repositories.bank_account_repo import BankAccountRepository
from app.repositories.tenant_profile_repo import TenantProfileRepository
from app.repositories.owner_profile_repo import OwnerProfileRepository
from app.services.property_master_service import require_property_master
from app.models.payment import Payment
from app.repositories.payment_repo import PaymentRepository
from app.services.payment_service import PaymentService
from app.services.financial_scope_service import FinancialScopeService
from app.repositories.lease_agreement_repo import LeaseAgreementRepository

from app.api.sepa import router as sepa_router

router = APIRouter(prefix="/api/payments", tags=["payments"])
router.include_router(sepa_router)


def _dec(value: str, field: str) -> Decimal:
    try:
        return Decimal(value)
    except (InvalidOperation, TypeError):
        raise HTTPException(status_code=422, detail=f"{field} is not a valid decimal: {value!r}")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class PaymentCreate(BaseModel):
    bank_account_id: str
    bank_transaction_id: uuid.UUID | None = None
    direction: str  # IN | OUT
    amount: str
    value_date: date
    payment_type: str
    tenant_profile_id: str | None = None
    owner_profile_id: str | None = None
    lease_agreement_id: str | None = None
    period_year: int | None = None
    period_month: int | None = None
    settlement_run_id: str | None = None
    note: str | None = None


class PaymentUpdate(BaseModel):
    bank_account_id: str | None = None
    bank_transaction_id: uuid.UUID | None = None
    direction: str | None = None
    amount: str | None = None
    value_date: date | None = None
    payment_type: str | None = None
    tenant_profile_id: str | None = None
    owner_profile_id: str | None = None
    lease_agreement_id: str | None = None
    period_year: int | None = None
    period_month: int | None = None
    settlement_run_id: str | None = None
    note: str | None = None


class ReverseRequest(BaseModel):
    note: str | None = None


class PaymentOut(BaseModel):
    bank_transaction_id: str | None = None
    id: str
    organization_id: str
    bank_account_id: str
    property_id: str
    management_mandate_id: str | None
    direction: str
    amount: str
    value_date: date
    payment_type: str
    tenant_profile_id: str | None
    owner_profile_id: str | None
    lease_agreement_id: str | None
    period_year: int | None
    period_month: int | None
    settlement_run_id: str | None
    status: str
    booked_at: str | None
    booked_by: str | None
    reverses_id: str | None
    reversed_by_id: str | None
    retention_until: date | None
    note: str | None

    model_config = {"from_attributes": True}


class OpenItemOut(BaseModel):
    period_year: int | None
    period_month: int | None
    payment_type: str
    soll: str
    ist: str
    open_amount: str


class LeaseOpenItemsOut(BaseModel):
    lease_agreement_id: str
    items: list[OpenItemOut]


class MietkontoOut(BaseModel):
    open_items: list[OpenItemOut]
    soll_total: str
    ist_total: str
    balance: str


def _to_out(p: Payment) -> PaymentOut:
    return PaymentOut(
        id=str(p.id),
        bank_transaction_id=str(p.bank_transaction_id) if p.bank_transaction_id else None,
        organization_id=str(p.organization_id),
        bank_account_id=str(p.bank_account_id),
        property_id=str(p.property_id),
        management_mandate_id=str(p.management_mandate_id) if p.management_mandate_id else None,
        direction=p.direction,
        amount=str(p.amount),
        value_date=p.value_date,
        payment_type=p.payment_type,
        tenant_profile_id=str(p.tenant_profile_id) if p.tenant_profile_id else None,
        owner_profile_id=str(p.owner_profile_id) if p.owner_profile_id else None,
        lease_agreement_id=str(p.lease_agreement_id) if p.lease_agreement_id else None,
        period_year=p.period_year,
        period_month=p.period_month,
        settlement_run_id=str(p.settlement_run_id) if p.settlement_run_id else None,
        status="cancelled" if p.cancelled_at is not None else p.status,
        booked_at=str(p.booked_at) if p.booked_at else None,
        booked_by=str(p.booked_by) if p.booked_by else None,
        reverses_id=str(p.reverses_id) if p.reverses_id else None,
        reversed_by_id=str(p.reversed_by_id) if p.reversed_by_id else None,
        retention_until=p.retention_until,
        note=p.note,
    )


def _mietkonto_out(result) -> MietkontoOut:
    return MietkontoOut(
        open_items=[
            OpenItemOut(
                period_year=i.period_year,
                period_month=i.period_month,
                payment_type=i.payment_type,
                soll=str(i.soll),
                ist=str(i.ist),
                open_amount=str(i.open_amount),
            )
            for i in result.open_items
        ],
        soll_total=str(result.soll_total),
        ist_total=str(result.ist_total),
        balance=str(result.balance),
    )


# ---------------------------------------------------------------------------
# Mietkonto views (read-only) -- declared BEFORE /{payment_id} so the literal
# path segments are not captured as a UUID payment id.
# ---------------------------------------------------------------------------
class DepositOut(BaseModel):
    lease_agreement_id: str
    tenant_profile_id: str
    unit_id: str
    property_id: str
    tenant_name: str
    unit_label: str
    property_name: str
    expected_amount: str
    paid_amount: str
    refunded_amount: str
    held_amount: str


@router.get("/deposits", response_model=list[DepositOut])
async def deposits(
    accounting_scope_id: uuid.UUID | None = None,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_live_member_db),
):
    rows = await PaymentService(db, auth.organization_id, auth.user_id).deposits(accounting_scope_id)
    return [DepositOut(**{key: format(value, ".2f") if isinstance(value, Decimal) else value
                          for key, value in row.items()}) for row in rows]


@router.get("/mietkonto/lease/{lease_agreement_id}", response_model=MietkontoOut)
async def lease_statement(
    lease_agreement_id: uuid.UUID,
    period_start: date,
    period_end: date,
    accounting_scope_id: uuid.UUID | None = None,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = PaymentService(db, auth.organization_id, auth.user_id)
    result = await svc.account_statement_for_lease(
        lease_agreement_id, period_start, period_end, accounting_scope_id
    )
    return _mietkonto_out(result)


@router.get("/mietkonto/owner/{owner_profile_id}", response_model=MietkontoOut)
async def owner_statement(
    owner_profile_id: uuid.UUID,
    period_start: date,
    period_end: date,
    economic_plan_id: uuid.UUID | None = None,
    accounting_scope_id: uuid.UUID | None = None,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = PaymentService(db, auth.organization_id, auth.user_id)
    result = await svc.account_statement_for_owner(
        owner_profile_id, economic_plan_id, period_start, period_end, accounting_scope_id
    )
    return _mietkonto_out(result)


@router.get("/open-items", response_model=list[LeaseOpenItemsOut])
async def all_lease_open_items(
    period_start: date,
    period_end: date,
    accounting_scope_id: uuid.UUID | None = None,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return one complete view; a denied lease must never disappear silently."""
    await FinancialScopeService(db, auth.organization_id, auth.user_id).select_report_scope(
        accounting_scope_id, scope_type='MIET', period_start=period_start, period_end=period_end)
    leases = await LeaseAgreementRepository(db, auth.organization_id).list_all()
    service = PaymentService(db, auth.organization_id, auth.user_id)
    rows = []
    for lease in leases:
        result = await service.account_statement_for_lease(lease.id, period_start, period_end, accounting_scope_id)
        items = [item for item in _mietkonto_out(result).open_items if Decimal(item.open_amount) != 0]
        if items:
            rows.append(LeaseOpenItemsOut(lease_agreement_id=str(lease.id), items=items))
    return rows


@router.get("/open-items/lease/{lease_agreement_id}", response_model=list[OpenItemOut])
async def lease_open_items(
    lease_agreement_id: uuid.UUID,
    period_start: date,
    period_end: date,
    accounting_scope_id: uuid.UUID | None = None,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """The non-zero offene Posten of a lease (Rueckstand/Guthaben) -- M7 input."""
    svc = PaymentService(db, auth.organization_id, auth.user_id)
    result = await svc.account_statement_for_lease(
        lease_agreement_id, period_start, period_end, accounting_scope_id
    )
    return [
        OpenItemOut(
            period_year=i.period_year,
            period_month=i.period_month,
            payment_type=i.payment_type,
            soll=str(i.soll),
            ist=str(i.ist),
            open_amount=str(i.open_amount),
        )
        for i in result.open_items
        if i.open_amount != Decimal("0.00")
    ]


# ---------------------------------------------------------------------------
# Payment CRUD
# ---------------------------------------------------------------------------
@router.get("", response_model=list[PaymentOut])
async def list_payments(
    bank_account_id: uuid.UUID | None = None,
    lease_agreement_id: uuid.UUID | None = None,
    tenant_profile_id: uuid.UUID | None = None,
    owner_profile_id: uuid.UUID | None = None,
    payment_type: str | None = None,
    period_year: int | None = None,
    status: str | None = None,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    repo = PaymentRepository(db, auth.organization_id)
    any_filter = any(
        v is not None
        for v in (
            bank_account_id, lease_agreement_id, tenant_profile_id,
            owner_profile_id, payment_type, period_year, status,
        )
    )
    if any_filter:
        items = await repo.list_by_filters(
            bank_account_id=bank_account_id,
            lease_agreement_id=lease_agreement_id,
            tenant_profile_id=tenant_profile_id,
            owner_profile_id=owner_profile_id,
            payment_type=payment_type,
            period_year=period_year,
            status=status,
        )
    else:
        items = await repo.list_all_ordered()
    return [_to_out(p) for p in items]


def _render_payments_csv(items: list[Payment]) -> bytes:
    output = io.StringIO(newline='')
    writer = csv.writer(output, delimiter=';', lineterminator='\r\n')
    writer.writerow(['ID', 'Objekt-ID', 'Konto-ID', 'Wertstellung', 'Richtung', 'Zahlungsart',
                     'Betrag (EUR)', 'Saldo-Betrag (EUR)', 'Status', 'Periodenjahr', 'Periodenmonat',
                     'Mieter-ID', 'Eigentümer-ID', 'Vertrag-ID', 'Notiz'])
    for item in items:
        fields = [item.id, item.property_id, item.bank_account_id, item.value_date,
                  item.direction, item.payment_type, _to_out(item).status, item.period_year,
                  item.period_month, item.tenant_profile_id, item.owner_profile_id,
                  item.lease_agreement_id, item.note]
        fields = [csv_text(str(value)) if value is not None else '' for value in fields]
        amount = csv_money(str(item.amount))
        # copy_negate is exact even under a caller's low Decimal precision.
        balance = csv_money(str(item.amount.copy_negate() if item.direction == 'OUT' else item.amount))
        fields[6:6] = [amount, balance]
        writer.writerow(fields)
    return output.getvalue().encode('utf-8-sig')


async def _require_payment_exports(db, auth, items, *, bank_account_id=None,
                                   lease_agreement_id=None, tenant_profile_id=None, owner_profile_id=None):
    scopes = FinancialScopeService(db, auth.organization_id, auth.user_id)

    async def profile(kind, profile_id, repository):
        if profile_id is None:
            return
        if await repository(db, auth.organization_id).get_by_id(profile_id) is None or not await db.scalar(text(
            "SELECT profile_source_access(:org,:kind,:profile,'export')"),
            {'org': auth.organization_id, 'kind': kind, 'profile': profile_id}):
            raise HTTPException(404, 'Payment contact not exportable')

    async def lease(lease_id):
        if lease_id is None:
            return
        if not await db.scalar(text(
            "SELECT property_history_row_access(l.organization_id,'lease_agreement',to_jsonb(l),'export') "
            "FROM lease_agreement l WHERE l.organization_id=:org AND l.id=:lease"),
            {'org': auth.organization_id, 'lease': lease_id}):
            raise HTTPException(404, 'Payment lease not exportable')

    if bank_account_id is not None:
        bank = await BankAccountRepository(db, auth.organization_id).get_by_id(bank_account_id)
        if bank is None:
            raise HTTPException(404, 'Payment account not accessible')
        if not await db.scalar(text(
            "SELECT financial_scope_access(:org,'bank_account',:id,'export')"),
            {'org': auth.organization_id, 'id': bank.id}):
            raise HTTPException(404, 'Payment account not exportable')
        async with scopes.using(bank, capability=ScopeCapability.EXPORT) as scope_id:
            if scope_id is None:
                await scopes.require_export(bank)
            await require_property_master(db, auth.organization_id, bank.property_id, 'export')
    await lease(lease_agreement_id)
    await profile('tenant_profile', tenant_profile_id, TenantProfileRepository)
    await profile('owner_profile', owner_profile_id, OwnerProfileRepository)
    for item in items:
        # Binding rows may disappear under RLS when a grant expires. The canonical
        # predicate must deny that case rather than treating it as legacy data.
        if not await db.scalar(text(
            "SELECT financial_scope_access(:org,'payment',:id,'export')"),
            {'org': auth.organization_id, 'id': item.id}):
            raise HTTPException(404, 'Financial row not exportable')
        async with scopes.using(item, capability=ScopeCapability.EXPORT) as scope_id:
            if scope_id is None:
                await scopes.require_export(item)
            await require_property_master(db, auth.organization_id, item.property_id, 'export')
            await lease(item.lease_agreement_id)
            await profile('tenant_profile', item.tenant_profile_id, TenantProfileRepository)
            await profile('owner_profile', item.owner_profile_id, OwnerProfileRepository)


@router.get('/export.csv')
async def export_payments_csv(
    bank_account_id: uuid.UUID | None = None,
    lease_agreement_id: uuid.UUID | None = None,
    tenant_profile_id: uuid.UUID | None = None,
    owner_profile_id: uuid.UUID | None = None,
    payment_type: str | None = None,
    period_year: int | None = None,
    status: str | None = None,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_live_member_db),
) -> Response:
    await FinancialScopeService(db, auth.organization_id, auth.user_id).lock_inputs()
    items = await PaymentRepository(db, auth.organization_id).list_by_filters(
        bank_account_id=bank_account_id, lease_agreement_id=lease_agreement_id,
        tenant_profile_id=tenant_profile_id, owner_profile_id=owner_profile_id,
        payment_type=payment_type, period_year=period_year, status=status)
    parents = dict(bank_account_id=bank_account_id, lease_agreement_id=lease_agreement_id,
                   tenant_profile_id=tenant_profile_id, owner_profile_id=owner_profile_id)
    await _require_payment_exports(db, auth, items, **parents)
    content = _render_payments_csv(items)
    await _require_payment_exports(db, auth, items, **parents)
    return Response(content, media_type='text/csv; charset=utf-8', headers={
        'Content-Disposition': attachment_content_disposition('zahlungen.csv'),
        'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'private, no-store',
    })


@router.get("/{payment_id}", response_model=PaymentOut)
async def get_payment(
    payment_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    repo = PaymentRepository(db, auth.organization_id)
    payment = await repo.get_by_id(payment_id)
    if payment is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    return _to_out(payment)


@router.post("", response_model=PaymentOut, status_code=201)
async def create_payment(
    body: PaymentCreate,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_writer_db),
):
    svc = PaymentService(db, auth.organization_id, auth.user_id)
    payment = await svc.create_draft(
        bank_account_id=uuid.UUID(body.bank_account_id),
        bank_transaction_id=body.bank_transaction_id,
        direction=body.direction,
        amount=_dec(body.amount, "amount"),
        value_date=body.value_date,
        payment_type=body.payment_type,
        tenant_profile_id=uuid.UUID(body.tenant_profile_id) if body.tenant_profile_id else None,
        owner_profile_id=uuid.UUID(body.owner_profile_id) if body.owner_profile_id else None,
        lease_agreement_id=uuid.UUID(body.lease_agreement_id) if body.lease_agreement_id else None,
        period_year=body.period_year,
        period_month=body.period_month,
        settlement_run_id=uuid.UUID(body.settlement_run_id) if body.settlement_run_id else None,
        note=body.note,
    )
    return _to_out(payment)


@router.patch("/{payment_id}", response_model=PaymentOut)
async def update_payment(
    payment_id: uuid.UUID,
    body: PaymentUpdate,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_writer_db),
):
    svc = PaymentService(db, auth.organization_id, auth.user_id)
    updates: dict = {}
    for field_name, value in body.model_dump(exclude_unset=True).items():
        if value is None:
            continue
        if field_name == "amount":
            updates[field_name] = _dec(value, "amount")
        elif field_name in (
            "bank_account_id", "tenant_profile_id", "owner_profile_id",
            "lease_agreement_id", "settlement_run_id",
        ):
            updates[field_name] = uuid.UUID(value)
        else:
            updates[field_name] = value
    payment = await svc.update_draft(payment_id, updates)
    return _to_out(payment)


@router.post("/{payment_id}/book", response_model=PaymentOut)
async def book_payment(
    payment_id: uuid.UUID,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_writer_db),
):
    svc = PaymentService(db, auth.organization_id, auth.user_id)
    payment = await svc.book(payment_id)
    return _to_out(payment)


@router.post("/{payment_id}/reverse", response_model=PaymentOut, status_code=201)
async def reverse_payment(
    payment_id: uuid.UUID,
    body: ReverseRequest | None = None,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_writer_db),
):
    svc = PaymentService(db, auth.organization_id, auth.user_id)
    storno = await svc.reverse_payment(payment_id, note=body.note if body else None)
    return _to_out(storno)


@router.delete("/{payment_id}", status_code=204)
async def delete_payment(
    payment_id: uuid.UUID,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_writer_db),
):
    svc = PaymentService(db, auth.organization_id, auth.user_id)
    await svc.delete_draft(payment_id)
