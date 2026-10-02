"""Bounded metadata review never opens the original or changes its audience."""
import uuid
from fastapi import HTTPException
from itsdangerous import BadData, URLSafeTimedSerializer
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from app.core.config import settings


class DunningQuarantineService:
    def __init__(self, db, auth):
        self.db, self.auth = db, auth

    async def page(self, query):
        if query.q:
            raise HTTPException(422, 'Quarantine inventory supports paging without content search')
        signer = URLSafeTimedSerializer(settings.session_secret, salt='hv-dunning-quarantine-v1')
        scope = [str(self.auth.organization_id), str(self.auth.user_id)]
        after = None
        if query.cursor:
            try:
                payload = signer.loads(query.cursor, max_age=86400)
                if not isinstance(payload, dict) or payload.get('scope') != scope:
                    raise ValueError('Cursor mismatch')
                after = uuid.UUID(payload['id'])
            except (BadData, ValueError, TypeError, KeyError):
                raise HTTPException(422, 'Invalid or expired page cursor') from None
        rows = await self.db.scalar(text('SELECT dunning_quarantine_page(:org,:after,:take)'),
            {'org': self.auth.organization_id, 'after': after, 'take': query.limit + 1})
        visible = rows[:query.limit]
        cursor = signer.dumps({'scope': scope, 'id': visible[-1]['id']}) if len(rows) > query.limit else None
        return visible, cursor

    async def review(self, body, key):
        try:
            return await self.db.scalar(text('SELECT review_dunning_quarantine(:org,:notice,:request,:digest,:reason)'),
                {'org': self.auth.organization_id, 'notice': body.notice_id, 'request': key,
                 'digest': body.snapshot_digest, 'reason': body.reason})
        except DBAPIError as exc:
            code = getattr(exc.orig, 'sqlstate', None)
            if code == '42501':
                raise HTTPException(404, 'Quarantine observation not accessible') from None
            if code in {'23505', '40001'}:
                raise HTTPException(409, 'Quarantine decision conflicts with a prior request or snapshot; reload the inventory') from None
            if code == '22023':
                raise HTTPException(422, 'Invalid quarantine decision') from None
            raise
