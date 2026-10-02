"""One durable inspection stage; no database transaction during scanner work."""
import argparse
import asyncio
import hashlib
import json
import uuid
from sqlalchemy import text
from app.core.db import set_tenant_context
from app.core.intake_inspection import run_isolated_inspection,valid_result,VERSION
from app.core.storage import get_store


async def process_once(session_factory,org_id,*,inspector=run_isolated_inspection):
    async with session_factory() as db,db.begin():
        await set_tenant_context(db,org_id)
        claim=await db.scalar(text('SELECT claim_intake_inspection(:org)'),{'org':org_id})
    if claim is None:
        return False
    params={'org':org_id,'job':uuid.UUID(claim['job_id']),'fence':uuid.UUID(claim['fence'])}
    failure=None;chunks=[];total=0
    async with session_factory() as db,db.begin():
        await set_tenant_context(db,org_id)
        context=await db.scalar(text('SELECT intake_worker_context(:org,:job,:fence)'),params)
        if context is None:
            return False
        # Hold the live authorization lock while loading the bounded original.
        try:
            for chunk in get_store().iter_chunks(context['storage_uri']):
                total+=len(chunk)
                if total>context['content_byte_size']:
                    failure='storage_integrity_mismatch';break
                chunks.append(chunk)
        except (OSError,ValueError):
            failure='stored_original_unavailable'
    data=b''.join(chunks)
    if failure is None and (total!=claim['content_byte_size'] or hashlib.sha256(data).hexdigest()!=claim['content_sha256']):
        failure='storage_integrity_mismatch'
    if failure:
        inspection={'status':'blocked','error_code':failure,'content_sha256':claim['content_sha256'],
            'content_byte_size':claim['content_byte_size'],'mime_type':claim['mime_type'],'page_count':None,'inspector_version':VERSION}
    else:
        try:
            inspection=await asyncio.to_thread(inspector,data,claim['mime_type'])
            if not valid_result(inspection,data,claim['mime_type']):
                raise ValueError('Invalid inspector result')
        except Exception:
            inspection={'status':'blocked','error_code':'inspector_failed','content_sha256':claim['content_sha256'],
                'content_byte_size':claim['content_byte_size'],'mime_type':claim['mime_type'],'page_count':None,'inspector_version':VERSION}
    async with session_factory() as db,db.begin():
        await set_tenant_context(db,org_id)
        return bool(await db.scalar(text('SELECT complete_intake_inspection(:org,:job,:fence,CAST(:inspection AS jsonb))'),
            {**params,'inspection':json.dumps(inspection)}))


async def main():
    from app.core.db import get_worker_session_factory
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--organization',action='append',required=True,type=uuid.UUID)
    parser.add_argument('--once',action='store_true')
    args=parser.parse_args()
    while True:
        for org in args.organization:
            await process_once(get_worker_session_factory(),org)
        if args.once:
            return
        await asyncio.sleep(2)


if __name__=='__main__':
    asyncio.run(main())
