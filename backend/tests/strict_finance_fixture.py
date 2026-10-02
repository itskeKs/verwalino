"""Explicit synthetic accounting coverage for source-authority integration tests."""
import uuid
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from tests.conftest import MIGRATOR_URL


async def classify_property_finance(seed, property_id, *, scope_type='WEG'):
    ids = {'org': seed['org_a_id'], 'actor': seed['user_a_admin_id'], 'property': uuid.UUID(str(property_id)),
           'scope': uuid.uuid4(), 'kind': scope_type}
    engine = create_async_engine(MIGRATOR_URL)
    try:
        async with engine.begin() as db:
            await db.execute(text("""INSERT INTO accounting_scope(id,organization_id,property_id,scope_type,label,valid_from,whole_property,origin,created_by)
                VALUES(:scope,:org,:property,:kind,'Explicit synthetic finance coverage','1900-01-01',true,'REVIEWED',:actor)"""), ids)
            # Insert all deferred bindings in one transaction, preserving chains
            # and bank-before-payment coherence without rewriting financial rows.
            for table in ['bank_account','payment','cost_entry','economic_plan','settlement_run']:
                extra = ',payment_bank_account_id' if table == 'payment' else ''
                value = ',r.bank_account_id' if table == 'payment' else ''
                await db.execute(text(f"""INSERT INTO accounting_scope_binding(organization_id,property_id,scope_id,{table}_id,origin,reason,reviewed_by{extra})
                    SELECT :org,:property,:scope,r.id,'REVIEWED','Explicit synthetic fixture coverage',:actor{value}
                    FROM {table} r WHERE r.organization_id=:org AND r.property_id=:property
                    AND NOT EXISTS(SELECT 1 FROM accounting_scope_binding b WHERE b.organization_id=:org AND b.{table}_id=r.id)"""), ids)
            await db.execute(text("""INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason)
                VALUES(:org,:scope,:actor,ARRAY['read','prepare','confirm','approve','export'],:actor,'Explicit synthetic finance coverage')"""), ids)
    finally:
        await engine.dispose()
    return ids
