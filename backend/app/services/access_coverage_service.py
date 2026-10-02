"""Versioned metadata evidence; no source publication or permission mutation."""
import json
import uuid
from datetime import datetime
from fastapi import HTTPException
from itsdangerous import BadData, URLSafeTimedSerializer
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from app.core.config import settings
from app.core.runtime_budget import attest_runtime_budget, runtime_budget_policy


class AccessCoverageService:
    def __init__(self, db, auth):
        self.db, self.auth = db, auth

    def policy(self):
        if not hasattr(self, '_policy'):
            self._policy = runtime_budget_policy()
        return self._policy

    async def attest_policy(self):
        await attest_runtime_budget(self.db,self.auth.organization_id,self.auth.user_id,self.policy())

    async def runtime_budget_status(self):
        await self.attest_policy()
        return await self.command('SELECT runtime_budget_status(:org)',{})

    async def review_runtime_budget(self,body,key):
        await self.attest_policy()
        return await self.command('SELECT review_runtime_budget(:org,:request,:expected,:digest,:reason)',
            {'request':key,'expected':body.expected_review_id,'digest':body.policy_digest,'reason':body.reason})

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

    async def source_security_status(self):
        return await self.command('SELECT source_security_status(:org)',{})

    async def transition_source_security(self,body,key):
        values={'request':key,'payload':body.model_dump_json()}
        # A replay remains available during registrar outages and never reapplies mode.
        receipt=await self.command('SELECT replay_source_security_transition(:org,:request,CAST(:payload AS jsonb))',values)
        if receipt is not None:
            return receipt
        if body.target_mode=='ENFORCED':
            await self.attest_policy()
        return await self.command('SELECT transition_source_security(:org,:request,CAST(:payload AS jsonb))',values)

    async def capture(self, body, key):
        await self.attest_policy()
        return await self.command('SELECT capture_access_coverage(:org,:request,:predecessor,CAST(:policy AS jsonb))',
            {'request':key,'predecessor':body.predecessor_id,'policy':self.policy()})

    async def review(self, snapshot, body, key):
        await self.attest_policy()
        return await self.command('SELECT review_access_coverage(:org,:snapshot,:request,:digest,CAST(:policy AS jsonb))',
            {'snapshot':snapshot,'request':key,'digest':body.manifest_digest,'policy':self.policy()})

    async def accept_disposition(self, snapshot, body, key):
        await self.attest_policy()
        return await self.command('SELECT accept_access_disposition(:org,:snapshot,:request,:manifest,:assessment,:reason,:team,:retention,CAST(:policy AS jsonb))',
            {'snapshot':snapshot,'request':key,'manifest':body.manifest_digest,'assessment':body.assessment_digest,
             'reason':body.reason,'team':body.team_acknowledged,'retention':body.retention_acknowledged,'policy':self.policy()})

    async def disposition_status(self, snapshot):
        await self.attest_policy()
        return await self.command('SELECT access_disposition_status(:org,:snapshot,CAST(:policy AS jsonb))',
                                  {'snapshot':snapshot,'policy':self.policy()})

    async def page(self, snapshot, query, *, disposition=False):
        if query.q:
            raise HTTPException(422, 'Coverage pages do not search protected source content')
        signer = URLSafeTimedSerializer(settings.session_secret, salt='hv-access-disposition-v1' if disposition else 'hv-access-coverage-v1')
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
        command = 'access_disposition_page' if disposition else 'access_coverage_page'
        rows = await self.command(f'SELECT {command}(:org,:snapshot,:kind,:resource,:take)',
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

    async def propose_grants(self, snapshot, body, key):
        await self.attest_policy()
        return await self.command('SELECT propose_access_grants(:org,:snapshot,:request,:digest,CAST(:changes AS jsonb),CAST(:policy AS jsonb))',
            {'snapshot':snapshot,'request':key,'digest':body.manifest_digest,
             'changes':json.dumps([c.model_dump(mode='json') for c in body.changes]),'policy':self.policy()})

    async def grant_proposal(self, snapshot):
        return await self.command('SELECT access_grant_proposal_for_snapshot(:org,:snapshot)',{'snapshot':snapshot})

    async def accept_grants(self, proposal, body, key):
        await self.attest_policy()
        return await self.command('SELECT accept_access_grants(:org,:proposal,:request,:digest,CAST(:policy AS jsonb))',
            {'proposal':proposal,'request':key,'digest':body.proposal_digest,'policy':self.policy()})
