"""Explicit synthetic contact grants for tests focused on another source boundary."""
import uuid
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from tests.conftest import MIGRATOR_URL


async def grant_property_contacts(api,seed,property_id,*,caps=('read','prepare','export')):
    engine=create_async_engine(MIGRATOR_URL)
    try:
        async with engine.connect() as db:
            people=(await db.execute(text('''
                SELECT p.person_id FROM tenant_profile p JOIN lease_agreement h ON h.tenant_profile_id=p.id
                  JOIN unit u ON u.id=h.unit_id WHERE u.property_id=:property
                UNION SELECT p.person_id FROM owner_profile p JOIN ownership_history h ON h.owner_profile_id=p.id
                  JOIN unit u ON u.id=h.unit_id WHERE u.property_id=:property
                UNION SELECT p.person_id FROM vendor p JOIN cost_entry c ON c.vendor_id=p.id WHERE c.property_id=:property
                UNION SELECT p.person_id FROM tenant_profile p JOIN settlement_run r ON r.tenant_profile_id=p.id WHERE r.property_id=:property
                UNION SELECT p.person_id FROM owner_profile p JOIN settlement_line ln ON ln.addressee_owner_profile_id=p.id
                  JOIN settlement_run r ON r.id=ln.settlement_run_id WHERE r.property_id=:property
                UNION SELECT p.person_id FROM owner_profile p JOIN economic_plan_line ln ON ln.owner_profile_id=p.id
                  JOIN economic_plan e ON e.id=ln.economic_plan_id WHERE e.property_id=:property
            '''),{'property':uuid.UUID(str(property_id))})).scalars().all()
    finally: await engine.dispose()
    for person in people:
        await grant_contact(api,seed,person,caps=caps)
    return people


async def grant_contact(api,seed,person,*,caps=('read','prepare','export')):
    result=await api.post('/api/person-data-domains',json={'person_id':str(person),'reason_code':'INITIAL_SETUP'},headers={'Idempotency-Key':str(uuid.uuid4())})
    assert result.status_code==201,result.text
    domain=result.json()['id']
    result=await api.post(f'/api/person-data-domains/{domain}/grants',json={'user_id':str(seed['user_a_admin_id']),'capabilities':list(caps),'reason_code':'TEAM_ACCESS'},headers={'Idempotency-Key':str(uuid.uuid4())})
    assert result.status_code==201,result.text
    return domain,result.json()['id']
