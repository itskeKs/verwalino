"""Explicit publication of exact visual bytes without original access grants."""
from datetime import datetime
import uuid
from fastapi import HTTPException
from itsdangerous import BadData, URLSafeTimedSerializer
from sqlalchemy import text
from starlette.concurrency import run_in_threadpool
from app.core.config import settings
from app.core.db import get_review_tenant_session
from app.core.storage import get_store
from app.services.intake_service import IntakeService
from app.services.visual_extract_service import verified_bytes


class VisualDisclosureService(IntakeService):
    async def publish(self, target, body, key, expected):
        params = {'target': target, 'destination': body.scope_id, 'partition': body.partition_revision_id,
                  'expected': expected, 'hash': body.content_sha256, 'request': key, 'reason': body.reason,
                  'reviewed': body.visible_content_reviewed}
        args = ':org,:target,:destination,:partition,:expected,:hash,:request,:reason,:reviewed'
        prepared = await self.call('SELECT prepare_visual_disclosure(' + args + ')', **params)
        if prepared['replay'] is not None:
            return prepared['replay']
        await run_in_threadpool(verified_bytes, get_store(), prepared)
        async with get_review_tenant_session(self.auth.organization_id) as review:
            await review.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {'actor': str(self.auth.user_id)})
            proof = await IntakeService(review, self.auth).call(
                'SELECT attest_visual_disclosure(:org,:target,:request,:digest,:pid,:transaction,:hash,:size)',
                target=target, request=key, digest=prepared['request_digest'], pid=prepared['backend_pid'],
                transaction=prepared['transaction_id'], hash=prepared['content_sha256'], size=prepared['content_byte_size'])
        return await self.call('SELECT publish_visual_disclosure(' + args + ',:proof)', **params, proof=proof)

    async def withdraw(self, target, body, key, expected):
        return await self.call('SELECT withdraw_visual_disclosure(:org,:target,:expected,:request,:reason,:confirmed)',
            target=target, expected=expected, request=key, reason=body.reason, confirmed=body.withdrawal_confirmed)

    async def target(self, artifact, destination):
        return await self.call('SELECT visual_disclosure_target(:org,:target,:destination)', target=artifact, destination=destination)

    async def detail(self, target):
        return await self.call('SELECT visual_disclosure_detail(:org,:target)', target=target)

    async def content(self, target):
        binding = await self.call('SELECT visual_disclosure_content(:org,:target)', target=target)
        return await run_in_threadpool(verified_bytes, get_store(), binding)

    async def listing(self, destination, artifact, limit, cursor):
        signer = URLSafeTimedSerializer(settings.session_secret, salt='hv-visual-disclosure-page-v1')
        context = [str(self.auth.organization_id), str(self.auth.user_id), str(destination), str(artifact)]
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
                raise HTTPException(422, 'Invalid or expired disclosure cursor') from None
        query = ('SELECT visual_disclosure_outgoing(:org,:before_time,:before_id,:take)' if destination is None and artifact is None
                 else 'SELECT visual_disclosure_list(:org,:destination,:artifact,:before_time,:before_id,:take)')
        rows = await self.call(query,
            destination=destination, artifact=artifact, before_time=before_time, before_id=before_id, take=limit)
        next_cursor = None
        if len(rows) > limit:
            rows = rows[:limit]
            last = rows[-1]['receipt']
            next_cursor = signer.dumps({'context': context, 'time': last['created_at'], 'id': last['id']})
        return {'items': rows, 'next_cursor': next_cursor}
