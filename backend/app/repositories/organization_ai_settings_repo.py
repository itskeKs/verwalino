"""Tenant-scoped AI configuration and serialized monthly accounting."""
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from app.models.organization_ai_settings import OrganizationAiSettings
from app.core.ai_models import MODEL_PRICES


class OrganizationAiSettingsRepository:
    def __init__(self, db, organization_id):
        self.db, self.organization_id = db, organization_id

    async def get(self):
        return await self.db.scalar(select(OrganizationAiSettings).where(
            OrganizationAiSettings.organization_id == self.organization_id))

    async def save(self, values):
        await self.lock_budget()
        statement = insert(OrganizationAiSettings).values(organization_id=self.organization_id, **values)
        await self.db.execute(statement.on_conflict_do_update(
            index_elements=['organization_id'], set_=values))
        await self.db.flush()
        self.db.expire_all()
        return await self.get()

    async def lock_budget(self):
        from app.core.locking import bound_lock_waits
        await bound_lock_waits(self.db)
        await self.db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                              {'key': f'hosted-ai:{self.organization_id}'})

    async def monthly_usage(self):
        return await self.db.scalar(text('SELECT organization_ai_monthly_usage(:org,CAST(:priced_models AS text[]))'),
            {'org': self.organization_id, 'priced_models': list(MODEL_PRICES)})
