"""Administrative preparation; no implicit source or financial entitlement."""
from datetime import datetime, timezone
import uuid
from fastapi import HTTPException
from sqlalchemy import select, text
from app.models.person_data_domain import PersonDataDomain, PersonDataGrant
from app.schemas.person_data_domain import DomainCreate, DataGrantCreate
from app.services.authorization_service import AuthorizationService


class PersonDataDomainService(AuthorizationService):
    async def get(self, domain_id):
        row = await self.session.scalar(select(PersonDataDomain).where(
            PersonDataDomain.organization_id == self.auth.organization_id, PersonDataDomain.id == domain_id))
        if row is None:
            raise HTTPException(404,'Person data domain not accessible')
        return row

    async def create(self, command: DomainCreate, key):
        await self._require_administration()
        previous = await self.session.scalar(select(PersonDataDomain).where(
            PersonDataDomain.organization_id == self.auth.organization_id,
            PersonDataDomain.created_by == self.auth.user_id, PersonDataDomain.request_key == key))
        if previous:
            if any(getattr(previous, field) != getattr(command, field) for field in DomainCreate.model_fields):
                raise HTTPException(409,'Idempotency-Key already used with a different request')
            return previous
        if not await self.session.scalar(text('SELECT person_data_setup_target_exists(:org,:person)'),
                {'org':self.auth.organization_id,'person':command.person_id}):
            raise HTTPException(404,'Person not accessible')
        if await self.session.scalar(select(PersonDataDomain.id).where(
                PersonDataDomain.organization_id == self.auth.organization_id, PersonDataDomain.person_id == command.person_id)):
            raise HTTPException(409,'Person already has an immutable data domain')
        row = PersonDataDomain(organization_id=self.auth.organization_id,created_by=self.auth.user_id,
                                 request_key=key,**command.model_dump())
        self.session.add(row)
        await self.session.flush()
        return row

    async def grant_data(self, domain_id, command: DataGrantCreate, key):
        await self._require_administration()
        await self.get(domain_id)
        previous = await self.session.scalar(select(PersonDataGrant).where(
            PersonDataGrant.organization_id == self.auth.organization_id,
            PersonDataGrant.granted_by == self.auth.user_id, PersonDataGrant.request_key == key))
        if previous:
            if (previous.domain_id != domain_id or previous.user_id != command.user_id
                    or set(previous.capabilities) != command.capabilities or previous.reason_code != command.reason_code
                    or previous.expires_at != command.expires_at):
                raise HTTPException(409,'Idempotency-Key already used with a different request')
            return previous
        if command.expires_at is not None and command.expires_at <= datetime.now(timezone.utc):
            raise HTTPException(422,'Grant expiry must be in the future')
        if not await self.session.scalar(text('SELECT EXISTS(SELECT 1 FROM membership m JOIN "user" u ON u.organization_id=m.organization_id AND u.id=m.user_id WHERE m.organization_id=:org AND m.user_id=:user AND u.is_active)'),
            {'org':self.auth.organization_id,'user':command.user_id}):
            raise HTTPException(404,'Active team member not found')
        if await self.session.scalar(select(PersonDataGrant.id).where(
                PersonDataGrant.organization_id == self.auth.organization_id, PersonDataGrant.domain_id == domain_id,
                PersonDataGrant.user_id == command.user_id, PersonDataGrant.revoked_at.is_(None))):
            raise HTTPException(409,'Revoke the previous grant before replacing it')
        row = PersonDataGrant(organization_id=self.auth.organization_id,domain_id=domain_id,
            granted_by=self.auth.user_id,request_key=key,**command.model_dump(exclude={'capabilities'}),
            capabilities=sorted(command.capabilities))
        self.session.add(row)
        await self.session.flush()
        return row

    async def revoke_data(self, domain_id, grant_id):
        await self._require_administration()
        row = await self.session.scalar(select(PersonDataGrant).where(
            PersonDataGrant.organization_id == self.auth.organization_id, PersonDataGrant.domain_id == domain_id,
            PersonDataGrant.id == grant_id).with_for_update())
        if row is None:
            raise HTTPException(404,'Person data grant not found')
        if row.revoked_at is not None:
            return
        await self.session.execute(text('UPDATE person_data_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE organization_id=:org AND id=:grant'),
            {'actor':self.auth.user_id,'org':self.auth.organization_id,'grant':grant_id})
