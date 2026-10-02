"""Attest safe runtime budget settings for one application transaction."""
import json
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from app.core.config import settings
from app.core.db import get_review_tenant_session


def runtime_budget_policy() -> str:
    return json.dumps({'enabled':settings.declaration_qa_enabled,
        'max_cost_usd':str(settings.declaration_qa_max_cost_usd),
        'window_minutes':settings.declaration_qa_budget_window_minutes,
        'reservation_seconds':settings.declaration_qa_reservation_seconds})


async def attest_runtime_budget(db,organization_id,user_id,policy: str):
    identity = (await db.execute(text('SELECT pg_backend_pid() AS pid,pg_current_xact_id()::text AS xid'))).one()
    try:
        async with get_review_tenant_session(organization_id) as review:
            await review.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(user_id)})
            return await review.scalar(text('SELECT attest_runtime_budget(:org,:pid,CAST(:xid AS xid8),CAST(:policy AS jsonb))'),
                {'org':organization_id,'pid':identity.pid,'xid':int(identity.xid),'policy':policy})
    except DBAPIError as exc:
        code = getattr(exc.orig, 'sqlstate', '') or ''
        if exc.connection_invalidated or code.startswith('08'):
            raise HTTPException(503,'Trusted budget runtime unavailable') from None
        raise
    except (RuntimeError,OSError):
        raise HTTPException(503,'Trusted budget runtime unavailable') from None
