"""DSGVO repository -- read-only retention-binding discovery + person lookup.

No mutation here. Person is fetched as a managed ORM instance; the service refreshes
it after the authoritative database privacy command. All other methods only READ the
authoritative SQL bindings through the capability-checked read/export adapter.
The category, association and deadline definitions live in the database, shared
with the individual command and organization retention classification.

Plus ``has_active_lease`` / ``has_active_ownership``: a LIVE (current or future)
contractual relationship -- ongoing processing purpose (Art. 6 I b DSGVO) -- which
blocks erasure entirely (the service raises 409; you must end the relationship
first). This is a SERVICE-layer rule, not a retention binding, hence checked here
against the DB rather than in the pure engine.
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import DBAPIError
from fastapi import HTTPException

from app.models.lease_agreement import LeaseAgreement
from app.models.owner_profile import OwnerProfile
from app.models.ownership_history import OwnershipHistory
from app.models.person import Person
from app.models.tenant_profile import TenantProfile
from app.models.vendor import Vendor
from app.engine.dsgvo import RetentionBinding


class DsgvoRepository:
    def __init__(self, session, org_id: uuid.UUID):
        self._session = session
        self._org_id = org_id

    # ------------------------------------------------------------------
    # Person + role profiles
    # ------------------------------------------------------------------
    async def require_complete_financial_context(self, person_id, *, capability='read'):
        complete = await self._session.scalar(text(
            'SELECT privacy_financial_context_complete(:org,:person,:capability)'),
            {'org': self._org_id, 'person': person_id, 'capability': capability})
        if not complete:
            raise HTTPException(409, 'Complete financial and source access is required to review retention or export this person')

    async def get_person(self, person_id: uuid.UUID) -> Person | None:
        stmt = (
            select(Person)
            .where(Person.organization_id == self._org_id)
            .where(Person.id == person_id)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def _ids(self, model, person_id: uuid.UUID) -> list[uuid.UUID]:
        stmt = (
            select(model.id)
            .where(model.organization_id == self._org_id)
            .where(model.person_id == person_id)
        )
        result = await self._session.execute(stmt)
        return [row[0] for row in result.all()]

    async def vendor_ids(self, person_id: uuid.UUID) -> list[uuid.UUID]:
        return await self._ids(Vendor, person_id)

    async def tenant_profile_ids(self, person_id: uuid.UUID) -> list[uuid.UUID]:
        return await self._ids(TenantProfile, person_id)

    async def owner_profile_ids(self, person_id: uuid.UUID) -> list[uuid.UUID]:
        return await self._ids(OwnerProfile, person_id)

    # ------------------------------------------------------------------
    # Retention bindings
    # ------------------------------------------------------------------
    async def retention_bindings(self, person_id: uuid.UUID, *, capability='read') -> list[RetentionBinding]:
        try:
            # A clock-expired grant must leave a controlled refusal and a usable
            # transaction, even when admission passed immediately beforehand.
            async with self._session.begin_nested():
                rows = (await self._session.execute(text(
                    'SELECT * FROM person_retention_view(:org,:person,:capability)'),
                    {'org': self._org_id, 'person': person_id, 'capability': capability})).mappings().all()
        except DBAPIError as error:
            if getattr(error.orig, 'sqlstate', None) == '42501':
                raise HTTPException(409, 'Complete financial and source access is required to review retention or export this person') from error
            raise
        return [RetentionBinding(kind=row['kind'], entity_type=row['entity_type'],
            entity_id=row['entity'], retention_until=row['until_date']) for row in rows]

    # ------------------------------------------------------------------
    # Live contractual relationship (blocks erasure)
    # ------------------------------------------------------------------
    async def has_active_lease(
        self, tenant_ids: list[uuid.UUID], reference_date: date
    ) -> bool:
        """A lease that is current or future: valid_to NULL or exclusive > ref."""
        if not tenant_ids:
            return False
        stmt = (
            select(func.count())
            .select_from(LeaseAgreement)
            .where(LeaseAgreement.organization_id == self._org_id)
            .where(LeaseAgreement.tenant_profile_id.in_(tenant_ids))
            .where(
                or_(
                    LeaseAgreement.valid_to.is_(None),
                    LeaseAgreement.valid_to > reference_date,
                )
            )
        )
        return (await self._session.execute(stmt)).scalar_one() > 0

    async def has_active_ownership(
        self, owner_ids: list[uuid.UUID], reference_date: date
    ) -> bool:
        if not owner_ids:
            return False
        stmt = (
            select(func.count())
            .select_from(OwnershipHistory)
            .where(OwnershipHistory.organization_id == self._org_id)
            .where(OwnershipHistory.owner_profile_id.in_(owner_ids))
            .where(
                or_(
                    OwnershipHistory.valid_to.is_(None),
                    OwnershipHistory.valid_to > reference_date,
                )
            )
        )
        return (await self._session.execute(stmt)).scalar_one() > 0
