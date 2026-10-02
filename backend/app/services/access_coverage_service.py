"""Versioned metadata evidence; no source publication or permission mutation."""
import json
import uuid
from datetime import datetime
from fastapi import HTTPException
from itsdangerous import BadData, URLSafeTimedSerializer
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from app.core.config import settings


class AccessCoverageService:
    def __init__(self, db, auth):
        self.db, self.auth = db, auth

    def policy(self):
        return json.dumps({'enabled':settings.declaration_qa_enabled,
            'max_cost_usd':str(settings.declaration_qa_max_cost_usd),
            'window_minutes':settings.declaration_qa_budget_window_minutes,
            'reservation_seconds':settings.declaration_qa_reservation_seconds})

    async def command(self, sql, values):
        try:
            return await self.db.scalar(text(sql), {'org':self.auth.organization_id, **values})
        except DBAPIError as exc:
            code = getattr(exc.orig, 'sqlstate', None)
            if code == '42501':
                raise HTTPException(404, 'Coverage evidence not accessible') from None
            if code in {'23505','40001'}:
                raise HTTPException(409, 'Coverage request conflicts or evidence changed; create a new snapshot') from None
            if code == '22023':
                raise HTTPException(422, 'Invalid coverage request') from None
            raise

    async def capture(self, body, key):
        return await self.command('SELECT capture_access_coverage(:org,:request,:predecessor,CAST(:policy AS jsonb))',
            {'request':key,'predecessor':body.predecessor_id,'policy':self.policy()})

    async def review(self, snapshot, body, key):
        return await self.command('SELECT review_access_coverage(:org,:snapshot,:request,:digest,CAST(:policy AS jsonb))',
            {'snapshot':snapshot,'request':key,'digest':body.manifest_digest,'policy':self.policy()})

    async def page(self, snapshot, query):
        if query.q:
            raise HTTPException(422, 'Coverage pages do not search protected source content')
        signer = URLSafeTimedSerializer(settings.session_secret, salt='hv-access-coverage-v1')
        scope = [str(self.auth.organization_id),str(self.auth.user_id),str(snapshot)]
        kind, resource = None, None
        if query.cursor:
            try:
                value = signer.loads(query.cursor, max_age=86400)
                if not isinstance(value,dict) or value.get('scope') != scope or not isinstance(value.get('kind'),str):
                    raise ValueError('Cursor context mismatch')
                kind, resource = value['kind'], uuid.UUID(value['id'])
            except (BadData,ValueError,TypeError,KeyError):
                raise HTTPException(422, 'Invalid or expired coverage cursor') from None
        rows = await self.command('SELECT access_coverage_page(:org,:snapshot,:kind,:resource,:take)',
            {'snapshot':snapshot,'kind':kind,'resource':resource,'take':query.limit+1})
        visible = rows[:query.limit]
        cursor = signer.dumps({'scope':scope,'kind':visible[-1]['kind'],'id':visible[-1]['resource_id']}) if len(rows)>query.limit else None
        return visible,cursor

    async def headers(self, query):
        if query.q:
            raise HTTPException(422, 'Coverage history does not search protected content')
        signer = URLSafeTimedSerializer(settings.session_secret, salt='hv-access-coverage-history-v1')
        scope = [str(self.auth.organization_id),str(self.auth.user_id)]
        instant, resource = None, None
        if query.cursor:
            try:
                value = signer.loads(query.cursor, max_age=86400)
                if not isinstance(value,dict) or value.get('scope') != scope:
                    raise ValueError('Cursor context mismatch')
                instant, resource = datetime.fromisoformat(value['at']), uuid.UUID(value['id'])
                if instant.tzinfo is None:
                    raise ValueError('Timestamp must include timezone')
            except (BadData,ValueError,TypeError,KeyError):
                raise HTTPException(422, 'Invalid or expired coverage cursor') from None
        rows = await self.command('SELECT access_coverage_headers(:org,:instant,:resource,:take)',
                                  {'instant':instant,'resource':resource,'take':query.limit+1})
        visible = rows[:query.limit]
        cursor = signer.dumps({'scope':scope,'at':visible[-1]['generated_at'],'id':visible[-1]['id']}) if len(rows)>query.limit else None
        return visible,cursor
