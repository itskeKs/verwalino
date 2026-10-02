"""Private, durable manifests and individually bounded file transfers."""
import uuid
from fastapi import APIRouter,Depends,File,Form,Header,Query,UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.tenant import AuthContext,get_current_user
from app.core.live_principal import get_live_member_db,get_live_writer_db
from app.core.uploads import read_upload_limited
from app.schemas.intake import IntakeBatchCreate,IntakeRetry
from app.services.intake_service import IntakeService

router=APIRouter(prefix='/api/intake-batches',tags=['intake'])


@router.post('',status_code=201)
async def create(body:IntakeBatchCreate,idempotency_key:uuid.UUID=Header(),
                 auth:AuthContext=Depends(get_current_user),db:AsyncSession=Depends(get_live_writer_db)):
    return await IntakeService(db,auth).create(body,idempotency_key)


@router.get('')
async def headers(limit:int=Query(50,ge=1,le=200),cursor:str|None=Query(None,max_length=4096),
                  auth:AuthContext=Depends(get_current_user),db:AsyncSession=Depends(get_live_member_db)):
    return await IntakeService(db,auth).headers(limit,cursor)


@router.get('/{batch_id}')
async def status(batch_id:uuid.UUID,limit:int=Query(50,ge=1,le=200),cursor:str|None=Query(None,max_length=4096),
                 auth:AuthContext=Depends(get_current_user),db:AsyncSession=Depends(get_live_member_db)):
    return await IntakeService(db,auth).status(batch_id,limit,cursor)


@router.post('/{batch_id}/items',status_code=202)
async def upload(batch_id:uuid.UUID,client_item_key:str=Form(max_length=128),file:UploadFile=File(),
                 auth:AuthContext=Depends(get_current_user),db:AsyncSession=Depends(get_live_writer_db)):
    data=await read_upload_limited(file)
    return await IntakeService(db,auth).upload(batch_id,client_item_key,data,file.content_type or '')


@router.post('/retry',status_code=202)
async def retry(body:IntakeRetry,idempotency_key:uuid.UUID=Header(),
                auth:AuthContext=Depends(get_current_user),db:AsyncSession=Depends(get_live_writer_db)):
    return await IntakeService(db,auth).call('SELECT retry_intake_inspection(:org,:job,:request,:fence,:reason)',
        job=body.job_id,request=idempotency_key,fence=body.expected_fence,reason=body.reason)


@router.get('/originals/{original_id}/content')
async def content(original_id:uuid.UUID,auth:AuthContext=Depends(get_current_user),db:AsyncSession=Depends(get_live_member_db)):
    import hashlib
    from fastapi import HTTPException,Response
    from app.core.storage import get_store,attachment_content_disposition
    original=await IntakeService(db,auth).call('SELECT intake_export_original(:org,:original)',original=original_id)
    # Validate the bounded original under the same live authorization lock before
    # sending headers. The response cannot expose changed or partial storage bytes.
    chunks=[];total=0
    try:
        for chunk in get_store().iter_chunks(original['storage_uri']):
            total+=len(chunk)
            if total>original['content_byte_size']:
                raise ValueError('Stored original size changed')
            chunks.append(chunk)
    except (OSError,ValueError):
        raise HTTPException(409,'Stored original unavailable or changed') from None
    data=b''.join(chunks)
    if total!=original['content_byte_size'] or hashlib.sha256(data).hexdigest()!=original['content_sha256']:
        raise HTTPException(409,'Stored original integrity mismatch')
    return Response(data,media_type=original['mime_type'],headers={
        'Content-Disposition':attachment_content_disposition(original['filename']),
        'X-Content-Type-Options':'nosniff','Content-Security-Policy':"sandbox; default-src 'none'",
        'Cache-Control':'private, no-store'})
