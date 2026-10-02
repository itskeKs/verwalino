"""Administrative preparation; no implicit source or financial entitlement."""
from datetime import datetime, timezone
import uuid
from fastapi import HTTPException
from sqlalchemy import select, text
from app.models.property_data_domain import PropertyDataDomain, PropertyDataGrant
from app.schemas.property_data_domain import DomainCreate, DataGrantCreate
from app.services.authorization_service import AuthorizationService


class PropertyDataDomainService(AuthorizationService):
    async def get(self, domain_id):
        row = await self.session.scalar(select(PropertyDataDomain).where(
            PropertyDataDomain.organization_id == self.auth.organization_id, PropertyDataDomain.id == domain_id))
        if row is None:
            raise HTTPException(404,'Property data domain not accessible')
        return row

    async def create(self, command: DomainCreate, key, *, new_property=False):
        await self._require_administration()
        previous = await self.session.scalar(select(PropertyDataDomain).where(
            PropertyDataDomain.organization_id == self.auth.organization_id,
            PropertyDataDomain.created_by == self.auth.user_id, PropertyDataDomain.request_key == key))
        if previous:
            if any(getattr(previous, field) != getattr(command, field) for field in DomainCreate.model_fields):
                raise HTTPException(409,'Idempotency-Key already used with a different request')
            return previous
        if not new_property and not await self.session.scalar(text('SELECT property_data_setup_target_exists(:org,:property)'),
                {'org':self.auth.organization_id,'property':command.property_id}):
            raise HTTPException(404,'Property not accessible')
        if await self.session.scalar(select(PropertyDataDomain.id).where(
                PropertyDataDomain.organization_id == self.auth.organization_id, PropertyDataDomain.property_id == command.property_id)):
            raise HTTPException(409,'Property already has an immutable data domain')
        row = PropertyDataDomain(organization_id=self.auth.organization_id,created_by=self.auth.user_id,
                                 request_key=key,**command.model_dump())
        self.session.add(row)
        await self.session.flush()
        return row

    async def prepare_new_audience(self, property_id, audience):
        """Explicit authority precedes a new parent in the same deferred-FK transaction."""
        await self._require_administration()
        if not any(grant.user_id == self.auth.user_id and {'read','prepare'} <= grant.capabilities
                   for grant in audience.grants):
            raise HTTPException(422, 'Explicit creator read and prepare grant required')
        domain = await self.create(DomainCreate(property_id=property_id, label=audience.label, reason=audience.reason),
            uuid.uuid5(property_id, 'creation-domain'), new_property=True)
        publisher_grant = None
        for command in audience.grants:
            receipt = await self.grant_data(domain.id, command, uuid.uuid5(property_id, f'creation-grant:{command.user_id}'))
            if command.user_id == self.auth.user_id:
                publisher_grant = receipt.id
        return domain, publisher_grant

    async def grant_data(self, domain_id, command: DataGrantCreate, key):
        await self._require_administration()
        await self.get(domain_id)
        previous = await self.session.scalar(select(PropertyDataGrant).where(
            PropertyDataGrant.organization_id == self.auth.organization_id,
            PropertyDataGrant.granted_by == self.auth.user_id, PropertyDataGrant.request_key == key))
        if previous:
            if (previous.domain_id != domain_id or previous.user_id != command.user_id
                    or set(previous.capabilities) != command.capabilities or previous.reason != command.reason
                    or previous.expires_at != command.expires_at):
                raise HTTPException(409,'Idempotency-Key already used with a different request')
            return previous
        if command.expires_at is not None and command.expires_at <= datetime.now(timezone.utc):
            raise HTTPException(422,'Grant expiry must be in the future')
        if not await self.session.scalar(text('SELECT EXISTS(SELECT 1 FROM membership m JOIN "user" u ON u.organization_id=m.organization_id AND u.id=m.user_id WHERE m.organization_id=:org AND m.user_id=:user AND u.is_active)'),
            {'org':self.auth.organization_id,'user':command.user_id}):
            raise HTTPException(404,'Active team member not found')
        if await self.session.scalar(select(PropertyDataGrant.id).where(
                PropertyDataGrant.organization_id == self.auth.organization_id, PropertyDataGrant.domain_id == domain_id,
                PropertyDataGrant.user_id == command.user_id, PropertyDataGrant.revoked_at.is_(None))):
            raise HTTPException(409,'Revoke the previous grant before replacing it')
        row = PropertyDataGrant(organization_id=self.auth.organization_id,domain_id=domain_id,
            granted_by=self.auth.user_id,request_key=key,**command.model_dump(exclude={'capabilities'}),
            capabilities=sorted(command.capabilities))
        self.session.add(row)
        await self.session.flush()
        return row

    async def revoke_data(self, domain_id, grant_id):
        await self._require_administration()
        row = await self.session.scalar(select(PropertyDataGrant).where(
            PropertyDataGrant.organization_id == self.auth.organization_id, PropertyDataGrant.domain_id == domain_id,
            PropertyDataGrant.id == grant_id).with_for_update())
        if row is None:
            raise HTTPException(404,'Property data grant not found')
        if row.revoked_at is not None:
            return
        await self.session.execute(text('UPDATE property_data_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE organization_id=:org AND id=:grant'),
            {'actor':self.auth.user_id,'org':self.auth.organization_id,'grant':grant_id})
