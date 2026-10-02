"""Preparation of shared-data audiences; does not activate source restrictions."""
import uuid
from fastapi import APIRouter, Depends, Header, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.live_principal import get_live_member_db
from app.core.tenant import AuthContext, get_current_user, get_db, require_admin_dep
from app.models.property_data_domain import PropertyDataDomain, PropertyDataGrant
from app.models.user import User
from app.repositories.pagination import Page, PageQuery, page_query, page_rows
from app.schemas.property_data_domain import DomainCreate, DomainOut, DataGrantCreate, DataGrantOut, DataGrantListOut
from app.services.property_data_domain_service import PropertyDataDomainService

router = APIRouter(prefix='/api/property-data-domains',tags=['property_data_domains'])


@router.get('/page',response_model=Page[DomainOut])
async def page_domains(property_id: uuid.UUID | None = None, query: PageQuery = Depends(page_query),
                       auth: AuthContext = Depends(get_current_user), db: AsyncSession = Depends(get_live_member_db)):
    rows, cursor = await page_rows(db,auth,PropertyDataDomain,PropertyDataDomain.label,[PropertyDataDomain.label],query,
        filters=(PropertyDataDomain.property_id == property_id,) if property_id else (),context=str(property_id or ''))
    return Page(items=[DomainOut.model_validate(row) for row in rows],next_cursor=cursor)


@router.post('',response_model=DomainOut,status_code=201)
async def create_domain(body: DomainCreate,idempotency_key: uuid.UUID = Header(),
                        auth: AuthContext = Depends(require_admin_dep),db: AsyncSession = Depends(get_db)):
    return await PropertyDataDomainService(db,auth).create(body,idempotency_key)


@router.get('/{domain_id}',response_model=DomainOut)
async def domain_detail(domain_id: uuid.UUID,auth: AuthContext = Depends(get_current_user),db: AsyncSession = Depends(get_live_member_db)):
    return await PropertyDataDomainService(db,auth).get(domain_id)


@router.get('/{domain_id}/grants/page',response_model=Page[DataGrantListOut])
async def page_grants(domain_id: uuid.UUID,query: PageQuery = Depends(page_query),
                      auth: AuthContext = Depends(require_admin_dep),db: AsyncSession = Depends(get_db)):
    service = PropertyDataDomainService(db,auth)
    await service._require_administration()
    await service.get(domain_id)
    rows,cursor = await page_rows(db,auth,PropertyDataGrant,PropertyDataGrant.reason,[PropertyDataGrant.reason],query,
                                  filters=(PropertyDataGrant.domain_id == domain_id,),context=str(domain_id))
    users = (await db.scalars(select(User).where(User.organization_id == auth.organization_id,
        User.id.in_([row.user_id for row in rows])))).all() if rows else []
    names = {user.id:user.display_name or user.email for user in users}
    return Page(items=[DataGrantListOut(**DataGrantOut.model_validate(row).model_dump(),
        user_display_name=names.get(row.user_id,'Unavailable team member')) for row in rows],next_cursor=cursor)


@router.post('/{domain_id}/grants',response_model=DataGrantOut,status_code=201)
async def grant_data(domain_id: uuid.UUID,body: DataGrantCreate,idempotency_key: uuid.UUID = Header(),
                     auth: AuthContext = Depends(require_admin_dep),db: AsyncSession = Depends(get_db)):
    return await PropertyDataDomainService(db,auth).grant_data(domain_id,body,idempotency_key)


@router.post('/{domain_id}/grants/{grant_id}/revoke',status_code=204)
async def revoke_data(domain_id: uuid.UUID,grant_id: uuid.UUID,
                      auth: AuthContext = Depends(require_admin_dep),db: AsyncSession = Depends(get_db)):
    await PropertyDataDomainService(db,auth).revoke_data(domain_id,grant_id)
    return Response(status_code=204)
