"""Property repository -- tenant-scoped."""

import uuid

from sqlalchemy import select, text

from app.models.property import Property
from app.models.property_assignment import PropertyAssignment
from app.models.property_document_relation import PropertyDocumentRelation
from app.models.property_rule import PropertyRule
from app.repositories.base import TenantRepository


class PropertyRepository(TenantRepository[Property]):
    model = Property

    async def exists_for_financial_creation(self, property_id: uuid.UUID) -> bool:
        """Check organization ownership without requiring property master access."""
        return bool(await self._session.scalar(text('SELECT financial_property_exists(:org,:property)'),
            {'org': self._org_id, 'property': property_id}))

    async def get_by_id_for_update(self, property_id: uuid.UUID) -> Property | None:
        stmt = self._base_query().where(Property.id == property_id).with_for_update()
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_effective_rules_for_update(
        self, property_id: uuid.UUID
    ) -> tuple[PropertyRule, ...]:
        result = await self._session.execute(
            select(PropertyRule)
            .where(PropertyRule.organization_id == self._org_id)
            .where(PropertyRule.property_id == property_id)
            .where(PropertyRule.status == "EFFECTIVE")
            .order_by(PropertyRule.stable_key, PropertyRule.id)
            .with_for_update()
        )
        return tuple(result.scalars().all())

    async def list_assignments_for_update(
        self, property_id: uuid.UUID
    ) -> tuple[PropertyAssignment, ...]:
        result = await self._session.execute(
            select(PropertyAssignment)
            .where(PropertyAssignment.organization_id == self._org_id)
            .where(PropertyAssignment.property_id == property_id)
            .order_by(PropertyAssignment.stable_key, PropertyAssignment.id)
            .with_for_update()
        )
        return tuple(result.scalars().all())

    async def list_document_relations_for_update(
        self, property_id: uuid.UUID
    ) -> tuple[PropertyDocumentRelation, ...]:
        result = await self._session.execute(
            select(PropertyDocumentRelation)
            .where(PropertyDocumentRelation.organization_id == self._org_id)
            .where(PropertyDocumentRelation.property_id == property_id)
            .order_by(
                PropertyDocumentRelation.source_document_id,
                PropertyDocumentRelation.target_document_id,
                PropertyDocumentRelation.relation_type,
                PropertyDocumentRelation.id,
            )
            .with_for_update()
        )
        return tuple(result.scalars().all())
