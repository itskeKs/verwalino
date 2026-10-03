"""Trusted rendering and private receipt storage; no destination publication."""
from datetime import datetime
import hashlib
import json
import uuid

from fastapi import HTTPException
from itsdangerous import BadData, URLSafeTimedSerializer
from sqlalchemy import text
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.db import get_review_tenant_session
from app.core.storage import get_store, make_storage_key
from app.core.visual_extract import run_isolated_visual_extract, MAX_INPUT_BYTES
from app.services.intake_service import IntakeService


def verified_bytes(store, binding, *, hash_key='content_sha256'):
    size = binding['content_byte_size']
    if type(size) is not int or not 0 < size <= MAX_INPUT_BYTES:
        raise HTTPException(409, 'Stored visual source size unsupported')
    chunks = []
    total = 0
    try:
        for chunk in store.iter_chunks(binding['storage_uri']):
            total += len(chunk)
            if total > size:
                raise ValueError('size changed')
            chunks.append(chunk)
    except (OSError, ValueError):
        raise HTTPException(409, 'Stored visual source unavailable or changed') from None
    data = b''.join(chunks)
    if total != size or hashlib.sha256(data).hexdigest() != binding[hash_key]:
        raise HTTPException(409, 'Stored visual source integrity mismatch')
    return data


class VisualExtractService(IntakeService):
    async def create(self, logical, body, key):
        params = {'logical': logical, 'partition': body.partition_revision_id, 'request': key, 'reason': body.reason}
        args = ':org,:logical,:partition,:request,:reason'
        prepared = await self.call('SELECT prepare_visual_extract(' + args + ')', **params)
        if prepared['replay'] is not None:
            return prepared['replay']
        store = get_store()
        data = await run_in_threadpool(verified_bytes, store, prepared, hash_key='source_sha256')
        rendered = await run_in_threadpool(run_isolated_visual_extract, data, prepared['source_sha256'],
            prepared['page_start'], prepared['page_end'], prepared['verified_page_count'])
        if rendered.status != 'succeeded':
            raise HTTPException(422, {'code': 'VISUAL_EXTRACT_UNAVAILABLE', 'reason': rendered.error_code})
        stored = await run_in_threadpool(store.put, make_storage_key(str(self.auth.organization_id), rendered.content_sha256),
                                        rendered.pdf_bytes, content_type='application/pdf')
        if stored.sha256 != rendered.content_sha256 or stored.byte_size != rendered.content_byte_size:
            raise HTTPException(409, 'Stored derivative integrity mismatch')
        # Verify storage readback before trusted attestation; store metadata alone
        # does not prove the bytes that a subsequent preview will retrieve.
        await run_in_threadpool(verified_bytes, store, {'storage_uri': stored.uri,
            'content_sha256': rendered.content_sha256, 'content_byte_size': rendered.content_byte_size})
        payload = {name: prepared[name] for name in ('source_sha256', 'page_start', 'page_end', 'verified_page_count',
                                                    'logical_document_id', 'partition_revision_id')}
        payload.update(content_sha256=rendered.content_sha256, content_byte_size=rendered.content_byte_size,
                       output_page_count=rendered.output_page_count, renderer_profile=rendered.renderer_profile,
                       storage_uri=stored.uri, storage_backend=stored.backend, storage_version=stored.version)
        async with get_review_tenant_session(self.auth.organization_id) as review:
            await review.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {'actor': str(self.auth.user_id)})
            proof = await IntakeService(review, self.auth).call(
                'SELECT attest_visual_extract(:org,:request,:digest,:pid,:transaction,CAST(:payload AS jsonb))',
                request=key, digest=prepared['request_digest'], pid=prepared['backend_pid'], transaction=prepared['transaction_id'],
                payload=json.dumps(payload))
        return await self.call('SELECT complete_visual_extract(' + args + ',:proof)', **params, proof=proof)

    async def history(self, logical, limit, cursor):
        signer = URLSafeTimedSerializer(settings.session_secret, salt='hv-visual-extract-history-v1')
        context = [str(self.auth.organization_id), str(self.auth.user_id), str(logical)]
        before_time = before_id = None
        if cursor:
            try:
                value = signer.loads(cursor, max_age=86400)
                if not isinstance(value, dict) or value.get('context') != context:
                    raise ValueError()
                before_time, before_id = datetime.fromisoformat(value['time']), uuid.UUID(value['id'])
                if before_time.tzinfo is None:
                    raise ValueError()
            except (BadData, ValueError, TypeError, KeyError):
                raise HTTPException(422, 'Invalid or expired extract history cursor') from None
        rows = await self.call('SELECT visual_extract_history(:org,:logical,:before_time,:before_id,:take)',
                               logical=logical, before_time=before_time, before_id=before_id, take=limit)
        next_cursor = None
        if len(rows) > limit:
            rows = rows[:limit]
            next_cursor = signer.dumps({'context': context, 'time': rows[-1]['created_at'], 'id': rows[-1]['id']})
        return {'items': rows, 'next_cursor': next_cursor}

    async def content(self, target):
        binding = await self.call('SELECT visual_extract_content(:org,:target)', target=target)
        return await run_in_threadpool(verified_bytes, get_store(), binding)
