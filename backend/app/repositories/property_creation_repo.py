"""Actor-scoped immutable receipts for atomic property creation."""
from sqlalchemy import text


class PropertyCreationRepository:
    def __init__(self, session, org_id, actor_id):
        self.session, self.org_id, self.actor_id = session, org_id, actor_id

    async def get(self, key):
        # The caller holds the exclusive organization ACL before this lookup.
        return (await self.session.execute(text('SELECT property_id,request_digest FROM property_creation_receipt '
            'WHERE organization_id=:org AND actor_user_id=:actor AND request_key=:key'),
            {'org':self.org_id,'actor':self.actor_id,'key':key})).mappings().one_or_none()

    async def record(self, key, digest, property_id):
        await self.session.execute(text('INSERT INTO property_creation_receipt(organization_id,actor_user_id,request_key,request_digest,property_id) '
            'VALUES (:org,:actor,:key,:digest,:property)'),
            {'org':self.org_id,'actor':self.actor_id,'key':key,'digest':digest,'property':property_id})
