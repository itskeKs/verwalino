"""Trusted evidence recorded only after a successful server engine computation."""
import json
from fastapi import HTTPException
from sqlalchemy.exc import DBAPIError
from sqlalchemy import text
from app.core.db import get_review_tenant_session


async def _record_computation(session, org_id, user_id, subject, operation, basis):
    # The caller holds the financial/context locks and has persisted the actual
    # engine snapshot and relational lines in this same outer transaction.
    await session.flush()
    kind=subject.__tablename__
    await session.execute(text('SELECT verify_financial_computation_basis(:org,:kind,:target,:operation,CAST(:basis AS jsonb))'),
        {'org':org_id,'kind':kind,'target':subject.id,'operation':operation,'basis':json.dumps(basis)})
    prepared=await session.scalar(text('SELECT prepare_financial_computation(:org,:kind,:target,:operation)'),
        {'org':org_id,'kind':kind,'target':subject.id,'operation':operation})
    async with get_review_tenant_session(org_id) as reviewer:
        await reviewer.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(user_id)})
        proof=await reviewer.scalar(text('SELECT attest_financial_computation(:org,:kind,:target,:operation,CAST(:state AS jsonb),:pid,:transaction)'),
            {'org':org_id,'kind':kind,'target':subject.id,'operation':operation,'state':json.dumps(prepared['state']),
             'pid':prepared['backend_pid'],'transaction':prepared['transaction_id']})
    return await session.scalar(text('SELECT record_financial_computation(:org,:proof)'),{'org':org_id,'proof':proof})


async def record_computation(session, org_id, user_id, subject, operation, basis):
    try:
        return await _record_computation(session, org_id, user_id, subject, operation, basis)
    except DBAPIError as error:
        code=getattr(error.orig,'sqlstate',None)
        if code in {'42501','22023','23505','23514','23503'}:
            raise HTTPException({'42501':404,'23505':409}.get(code,422),
                'Complete financial computation evidence unavailable; review current inputs and authority') from None
        raise


async def begin_computation(session, org_id, subject, operation):
    try:
        return await session.scalar(text('SELECT begin_financial_computation(:org,:kind,:target,:operation)'),
            {'org':org_id,'kind':subject.__tablename__,'target':subject.id,'operation':operation})
    except DBAPIError as error:
        code=getattr(error.orig,'sqlstate',None)
        if code in {'42501','23514'}:
            raise HTTPException(404 if code == '42501' else 422,
                'Complete stable computation inputs unavailable; review inputs and retry') from None
        raise
