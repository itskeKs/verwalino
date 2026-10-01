"""Reviewed setup; grants are explicit and never inferred from organization role."""
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.core.rbac import ScopeCapability
from app.models.accounting_scope import AccountingScope, AccountingScopeBinding, AccountingScopeUnit
from app.models.audit_event import AuditEvent
from app.models.management_mandate import ManagementMandate
from app.models.owner_profile import OwnerProfile
from app.models.property import Property
from app.models.scope_grant import ScopeGrant
from app.models.unit import Unit
from app.repositories.accounting_scope_repo import AccountingScopeRepository
from app.schemas.accounting_scope import BindingCreate, GrantCreate, ScopeCreate, ScopeUnitInput
from app.services.authorization_service import AuthorizationService


class AccountingScopeService(AuthorizationService):
    def __init__(self, session, auth):
        super().__init__(session, auth)
        self.repo = AccountingScopeRepository(session, auth.organization_id)

    async def get(self, scope_id):
        scope = await self.repo.get_by_id(scope_id)
        if scope is None:
            raise HTTPException(404, 'Accounting scope not accessible')
        return scope

    async def _reference(self, model, entity_id):
        row = await self.session.scalar(select(model).where(
            model.organization_id == self.auth.organization_id, model.id == entity_id).with_for_update())
        if row is None:
            raise HTTPException(404, 'Referenced record not accessible')
        return row

    async def create_scope(self, command: ScopeCreate, key: uuid.UUID):
        await self._require_administration()
        previous = await self.repo.by_request(self.auth.user_id, key)
        if previous:
            units = await self.repo.units(previous.id)
            saved = {field: getattr(previous, field) for field in ScopeCreate.model_fields if field != 'units'}
            saved['units'] = [ScopeUnitInput(unit_id=u.unit_id, owner_profile_id=u.owner_profile_id) for u in units]
            if ScopeCreate(**saved) != command:
                raise HTTPException(409, 'Idempotency-Key already used with a different request')
            return previous
        await self._reference(Property, command.property_id)
        if command.management_mandate_id:
            mandate = await self._reference(ManagementMandate, command.management_mandate_id)
            if (mandate.mandate_type != command.scope_type or mandate.start_date > command.valid_from
                    or (mandate.end_date is not None and (command.valid_to is None or command.valid_to > mandate.end_date))):
                raise HTTPException(422, 'Scope must match the mandate type and effective dates')
        # Acquire unit history locks before row locks; ownership inserts also
        # take this lock before FK checks, avoiding inverted lock order.
        for unit in command.units:
            await self.session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                {'key': f'scope-unit-owner:{self.auth.organization_id}:{unit.unit_id}'})
            existing = await self._reference(Unit, unit.unit_id)
            if existing.property_id != command.property_id:
                raise HTTPException(422, 'Every selected unit must belong to the property')
            if unit.owner_profile_id:
                await self._reference(OwnerProfile, unit.owner_profile_id)
        sphere = await self.repo.create(AccountingScope(**command.model_dump(exclude={'units'}),
            origin='REVIEWED', created_by=self.auth.user_id, request_key=key))
        for unit in command.units:
            self.session.add(AccountingScopeUnit(organization_id=self.auth.organization_id,
                scope_id=sphere.id, property_id=sphere.property_id, **unit.model_dump()))
        self.session.add(AuditEvent(organization_id=self.auth.organization_id, actor_user_id=self.auth.user_id,
            entity_type='accounting_scope', entity_id=sphere.id, action='scope.created',
            payload_diff=command.model_dump(mode='json')))
        await self.session.flush()
        return sphere

    async def create_grant(self, scope_id, command: GrantCreate, key: uuid.UUID):
        await self._require_administration()
        # Deterministic command identity is retained after revocation. Replaying a
        # lost response returns its original grant, never creates a replacement.
        grant_id = uuid.uuid5(uuid.NAMESPACE_URL, f'hv:scope-grant:{self.auth.organization_id}:{self.auth.user_id}:{key}')
        previous = await self.session.scalar(select(ScopeGrant).where(
            ScopeGrant.organization_id == self.auth.organization_id, ScopeGrant.id == grant_id))
        if previous:
            if (previous.scope_id != scope_id or previous.user_id != command.user_id
                    or set(previous.capabilities) != command.capabilities or previous.reason != command.reason
                    or previous.expires_at != command.expires_at):
                raise HTTPException(409, 'Idempotency-Key already used with a different request')
            return previous
        if command.expires_at is not None and command.expires_at <= datetime.now(timezone.utc):
            raise HTTPException(422, 'Grant expiry must be in the future')
        return await self.grant(scope_id, command.user_id, command.capabilities,
                                command.reason, command.expires_at, grant_id=grant_id)

    async def revoke_grant(self, scope_id, grant_id):
        await self._require_administration()
        grant = await self.session.scalar(select(ScopeGrant).where(
            ScopeGrant.organization_id == self.auth.organization_id,
            ScopeGrant.id == grant_id, ScopeGrant.scope_id == scope_id))
        if grant is None:
            raise HTTPException(404, 'Grant not found')
        await self.revoke(grant_id)

    async def bind_resource(self, scope_id, command: BindingCreate):
        await self._require_administration()
        sphere = await self.require(scope_id, ScopeCapability.PREPARE)
        existing = await self.repo.binding(command.resource_type, command.resource_id)
        if existing:
            if existing.scope_id != sphere.id or existing.reason != command.reason:
                raise HTTPException(409, 'Resource already has a different immutable classification')
            return existing
        resource = await self.repo.resource(command.resource_type, command.resource_id)
        if resource is None:
            raise HTTPException(404, 'Resource not accessible')
        if resource.property_id != sphere.property_id:
            raise HTTPException(422, 'Resource and accounting scope must belong to the same property')
        binding = AccountingScopeBinding(organization_id=self.auth.organization_id,
            property_id=sphere.property_id, scope_id=sphere.id, origin='REVIEWED',
            reason=command.reason, reviewed_by=self.auth.user_id,
            **{command.resource_type + '_id': resource.id})
        if command.resource_type == 'payment':
            account_binding = await self.repo.binding('bank_account', resource.bank_account_id)
            if account_binding is None or account_binding.scope_id != sphere.id:
                raise HTTPException(422, 'Classify the payment account in the exact same scope first')
            binding.payment_bank_account_id = resource.bank_account_id
        try:
            async with self.session.begin_nested():
                self.session.add(binding)
                await self.session.flush()
                # Validate the immutable original now so rejection remains an
                # atomic 422 and is not deferred until response teardown.
                await self.session.execute(text('SET CONSTRAINTS validate_binding_semantics IMMEDIATE'))
                await self.session.execute(text('SET CONSTRAINTS validate_binding_semantics DEFERRED'))
        except IntegrityError:
            raise HTTPException(422, 'Original type, mandate or period conflicts with the selected accounting scope') from None
        self.session.add(AuditEvent(organization_id=self.auth.organization_id, actor_user_id=self.auth.user_id,
            entity_type='accounting_scope_binding', entity_id=binding.id, action='scope.resource_classified',
            payload_diff=command.model_dump(mode='json') | {'scope_id': str(sphere.id)}))
        await self.session.flush()
        return binding
