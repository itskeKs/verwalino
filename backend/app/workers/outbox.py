"""Internal outbox delivery; no external messages or provider side effects."""
import uuid

from app.core.request_id import correlation_context
from sqlalchemy import text
from app.core.db import set_tenant_context


async def process_once(session_factory,org_id):
    async with session_factory() as db,db.begin():
        await set_tenant_context(db,org_id)
        claim=await db.scalar(text('SELECT claim_outbox_delivery(:org)'),{'org':org_id})
    if claim is None:return False
    with correlation_context(claim['delivery_id']):
        async with session_factory() as db,db.begin():
            await set_tenant_context(db,org_id)
            return bool(await db.scalar(text('SELECT apply_intake_logical_delivery(:org,:delivery,:fence)'),
                {'org':org_id,'delivery':uuid.UUID(claim['delivery_id']),'fence':uuid.UUID(claim['fence'])}))
