"""Explicit reviewed scope configuration. This API does not activate team rollout."""
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant import AuthContext, get_current_user, get_db, require_admin_dep
from app.models.accounting_scope import AccountingScope
from app.models.scope_grant import ScopeGrant
from app.models.user import User
from app.repositories.pagination import Page, PageQuery, page_query, page_rows
from app.schemas.accounting_scope import (
    BindingCreate, BindingOut, GrantCreate, GrantOut, GrantListOut, ScopeCreate, ScopeDetail, ScopeOut, ScopeUnitInput,
)
from app.services.accounting_scope_service import AccountingScopeService

router = APIRouter(prefix='/api/accounting-scopes', tags=['accounting_scopes'])


@router.get('/page', response_model=Page[ScopeOut])
async def page_scopes(property_id: uuid.UUID | None = None, query: PageQuery = Depends(page_query),
                      auth: AuthContext = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    filters = (AccountingScope.property_id == property_id,) if property_id else ()
    items, cursor = await page_rows(db, auth, AccountingScope, AccountingScope.label,
        [AccountingScope.label], query, filters=filters, context=str(property_id or ''))
    return Page(items=[ScopeOut.model_validate(item) for item in items], next_cursor=cursor)


@router.post('', response_model=ScopeOut, status_code=201)
async def create_scope(body: ScopeCreate, idempotency_key: uuid.UUID = Header(),
                       auth: AuthContext = Depends(require_admin_dep), db: AsyncSession = Depends(get_db)):
    try:
        async with db.begin_nested():
            return await AccountingScopeService(db, auth).create_scope(body, idempotency_key)
    except IntegrityError:
        raise HTTPException(422, 'Scope definition conflicts with the property, mandate or ownership history') from None


@router.get('/{scope_id}', response_model=ScopeDetail)
async def scope_detail(scope_id: uuid.UUID, auth: AuthContext = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    service = AccountingScopeService(db, auth)
    scope = await service.get(scope_id)
    units = await service.repo.units(scope.id)
    return ScopeDetail(**ScopeOut.model_validate(scope).model_dump(),
        units=[ScopeUnitInput(unit_id=u.unit_id, owner_profile_id=u.owner_profile_id) for u in units])


@router.get('/{scope_id}/grants/page', response_model=Page[GrantListOut])
async def page_grants(scope_id: uuid.UUID, query: PageQuery = Depends(page_query),
                      auth: AuthContext = Depends(require_admin_dep), db: AsyncSession = Depends(get_db)):
    service = AccountingScopeService(db, auth)
    await service._require_administration()
    await service.get(scope_id)
    items, cursor = await page_rows(db, auth, ScopeGrant, ScopeGrant.reason, [ScopeGrant.reason],
        query, filters=(ScopeGrant.scope_id == scope_id,), context=str(scope_id))
    users = (await db.scalars(select(User).where(User.organization_id == auth.organization_id,
        User.id.in_([item.user_id for item in items])))).all() if items else []
    names = {user.id: user.display_name or user.email for user in users}
    return Page(items=[GrantListOut(**GrantOut.model_validate(item).model_dump(),
        user_display_name=names.get(item.user_id, 'Unavailable team member')) for item in items], next_cursor=cursor)


@router.post('/{scope_id}/grants', response_model=GrantOut, status_code=201)
async def grant_scope(scope_id: uuid.UUID, body: GrantCreate, idempotency_key: uuid.UUID = Header(),
                      auth: AuthContext = Depends(require_admin_dep), db: AsyncSession = Depends(get_db)):
    return await AccountingScopeService(db, auth).create_grant(scope_id, body, idempotency_key)


@router.post('/{scope_id}/grants/{grant_id}/revoke', status_code=204)
async def revoke_scope(scope_id: uuid.UUID, grant_id: uuid.UUID,
                       auth: AuthContext = Depends(require_admin_dep), db: AsyncSession = Depends(get_db)):
    await AccountingScopeService(db, auth).revoke_grant(scope_id, grant_id)
    return Response(status_code=204)


@router.post('/{scope_id}/bindings', response_model=BindingOut, status_code=201)
async def bind_resource(scope_id: uuid.UUID, body: BindingCreate,
                        auth: AuthContext = Depends(require_admin_dep), db: AsyncSession = Depends(get_db)):
    binding = await AccountingScopeService(db, auth).bind_resource(scope_id, body)
    return BindingOut(id=binding.id, scope_id=binding.scope_id, resource_type=body.resource_type,
        resource_id=body.resource_id, reason=binding.reason)
