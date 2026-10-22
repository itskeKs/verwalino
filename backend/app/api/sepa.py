"""Local-only direct-debit configuration and file export; no bank transport."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.live_principal import get_live_member_db, get_live_writer_db
from app.core.storage import attachment_content_disposition
from app.core.tenant import AuthContext, get_current_user, require_write_dep
from app.services.sepa_export import CreditorRequest, MandateRequest, MandateUsageRequest, SepaRequest, render_sepa
from app.services.sepa_service import SepaService

router=APIRouter()


@router.get('/sepa/accounts/{bank_id}')
async def account(bank_id:UUID,auth:AuthContext=Depends(get_current_user),
                  db:AsyncSession=Depends(get_live_member_db)):
    return await SepaService(db,auth).account(bank_id)


@router.put('/sepa/accounts/{bank_id}/creditor')
async def save_creditor(bank_id:UUID,body:CreditorRequest,auth:AuthContext=Depends(require_write_dep),
                        db:AsyncSession=Depends(get_live_writer_db,scope='function')):
    return await SepaService(db,auth).save_creditor(bank_id,body)


@router.post('/sepa/accounts/{bank_id}/mandates',status_code=201)
async def create_mandate(bank_id:UUID,body:MandateRequest,auth:AuthContext=Depends(require_write_dep),
                         db:AsyncSession=Depends(get_live_writer_db,scope='function')):
    return await SepaService(db,auth).create_mandate(bank_id,body)


@router.put('/sepa/mandates/{mandate_id}')
async def change_mandate(mandate_id:UUID,body:MandateUsageRequest,auth:AuthContext=Depends(require_write_dep),
                         db:AsyncSession=Depends(get_live_writer_db,scope='function')):
    return await SepaService(db,auth).change_mandate(mandate_id,body)


@router.post('/sepa/mandates/{mandate_id}/revoke',status_code=204)
async def revoke_mandate(mandate_id:UUID,auth:AuthContext=Depends(require_write_dep),
                         db:AsyncSession=Depends(get_live_writer_db,scope='function')):
    await SepaService(db,auth).change_mandate(mandate_id)
    return Response(status_code=204)


@router.post('/sepa/preview')
async def preview(body:SepaRequest,auth:AuthContext=Depends(get_current_user),
                  db:AsyncSession=Depends(get_live_member_db)):
    result,*_=await SepaService(db,auth).prepare(body)
    return result


@router.post('/sepa.xml')
async def export(body:SepaRequest,auth:AuthContext=Depends(get_current_user),
                 db:AsyncSession=Depends(get_live_member_db)):
    if body.expected_source_digest is None:
        raise HTTPException(422,'Preview the exact monthly collection first')
    service=SepaService(db,auth)
    preview,creditor,bank,rows,source,scope=await service.prepare(body)
    if preview['source_digest']!=body.expected_source_digest:
        raise HTTPException(409,'Monthly rent, consent or configuration changed; preview again')
    content=render_sepa(body,creditor,bank,rows)
    await service.require_export(source,scope)
    # Privacy state can change independently from financial statement fields.
    for row in rows:
        await service._person(UUID(row['tenant_profile_id']))
    return Response(content,media_type='application/xml',headers={
        'Content-Disposition':attachment_content_disposition(f'SEPA_{body.year}_{body.month:02d}.xml'),
        'X-Content-Type-Options':'nosniff','Cache-Control':'private, no-store',
    })
