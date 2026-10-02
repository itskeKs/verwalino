"""Synthetic browser acceptance control, restricted to disposable localhost DB."""
import asyncio
import json
import sys
import uuid
import psycopg2
from app.core.config import settings
from app.core.declaration_test_database import require_disposable_database_settings
from app.core.db import get_tenant_session
from sqlalchemy import text

_, _, sync_url = require_disposable_database_settings(settings)
job = uuid.UUID(sys.argv[1])
action = sys.argv[2]
assert action in {'revoke', 'restore'}
with psycopg2.connect(sync_url) as connection, connection.cursor() as cursor:
    cursor.execute('SELECT q.organization_id,q.actor_user_id,i.lease_generation,i.lease_token,i.queue_request_id FROM declaration_import i JOIN declaration_queue_request q ON q.id=i.queue_request_id WHERE i.id=%s', (str(job),))
    org, actor, generation, token, request = cursor.fetchone()
    cursor.execute('UPDATE membership SET role=%s WHERE organization_id=%s AND user_id=%s',
                   ('READ_ONLY' if action == 'revoke' else 'CLERK', org, actor))

async def cancel():
    async with get_tenant_session(uuid.UUID(str(org))) as session:
        cancelled = await session.scalar(text('SELECT cancel_unauthorized_declaration(:org,:job,:request,:generation,:token)'),
            {'org':uuid.UUID(str(org)), 'job':job, 'request':uuid.UUID(str(request)), 'generation':generation, 'token':token})
        assert cancelled, 'Revoked job must be cancelled'
if action == 'revoke':
    asyncio.run(cancel())
print(json.dumps({'action':action, 'job':str(job)}))
