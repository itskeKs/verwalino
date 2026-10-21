"""Create frozen special levies; project them into the existing owner ledger."""
import uuid
from collections import defaultdict
from datetime import date
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select, text

from app.core.rbac import ScopeCapability
from app.core.tenant import AuthContext
from app.engine.rounding import distribute_amount
from app.models.audit_event import AuditEvent
from app.models.ownership_history import OwnershipHistory
from app.models.property import Property
from app.models.resolution import Resolution
from app.models.owners_meeting import OwnersMeeting
from app.models.special_levy import SpecialLevy, SpecialLevyAllocation
from app.models.unit import Unit
from app.services.authorization_service import AuthorizationService
from app.services.financial_scope_service import FinancialScopeService


class SpecialLevyService:
    def __init__(self, session, org_id, user_id):
        self.db, self.org, self.user = session, org_id, user_id
        self.context = FinancialScopeService(session, org_id, user_id)

    async def scope(self, scope_id, property_id=None, *, write=False):
        if scope_id is None:
            raise HTTPException(422, "Select an explicit whole-property WEG accounting scope")
        sphere = await AuthorizationService(self.db, AuthContext(self.user, self.org, "live role")).require(
            scope_id, ScopeCapability.PREPARE if write else ScopeCapability.READ)
        await self.context.lock_inputs()
        if sphere.scope_type != "WEG" or not sphere.whole_property:
            raise HTTPException(422, "Special levies support only whole-property WEG scopes")
        if property_id is not None and property_id != sphere.property_id:
            exists = await self.db.scalar(select(Property.id).where(Property.organization_id == self.org, Property.id == property_id))
            raise HTTPException(422 if exists else 404, "Property does not match accounting scope")
        await self.db.execute(text("SELECT set_config('app.current_scope_id',:scope,true)"), {"scope": str(scope_id)})
        await self.context.select_read_scope(scope_id)
        return sphere

    async def create(self, *, property_id, accounting_scope_id, amount, due_date, resolution_id=None):
        sphere = await self.scope(accounting_scope_id, property_id, write=True)
        levy = SpecialLevy(id=uuid.uuid4(), organization_id=self.org, property_id=property_id,
            accounting_scope_id=accounting_scope_id, amount=amount, due_date=due_date, resolution_id=resolution_id)
        await self.context.prepare_new(levy, accounting_scope_id)
        if due_date < sphere.valid_from or sphere.valid_to is not None and due_date > sphere.valid_to:
            raise HTTPException(422, "Due date is outside accounting scope")
        if not await self.db.scalar(text("SELECT property_financial_source_access(:org,:property)"), {"org": self.org, "property": property_id}):
            raise HTTPException(404, "Complete property sources not accessible")
        prop = await self.db.scalar(select(Property).where(Property.organization_id == self.org, Property.id == property_id).with_for_update())
        if prop is None:
            raise HTTPException(404, "Property not accessible")
        if resolution_id is not None:
            resolution = await self.db.scalar(select(Resolution).join(OwnersMeeting, OwnersMeeting.id == Resolution.meeting_id).where(
                Resolution.organization_id == self.org, Resolution.id == resolution_id,
                OwnersMeeting.organization_id == self.org, OwnersMeeting.property_id == property_id).with_for_update())
            if resolution is None:
                raise HTTPException(404, "Resolution not accessible in this property")
        units = list((await self.db.scalars(select(Unit).where(Unit.organization_id == self.org,
            Unit.property_id == property_id).order_by(Unit.id).with_for_update())).all())
        if not units:
            raise HTTPException(422, "A complete positive MEA and ownership basis is required")
        history = list((await self.db.scalars(select(OwnershipHistory).where(
            OwnershipHistory.organization_id == self.org, OwnershipHistory.unit_id.in_([u.id for u in units]),
            OwnershipHistory.valid_from <= due_date,
            (OwnershipHistory.valid_to.is_(None) | (OwnershipHistory.valid_to > due_date)),
        ).order_by(OwnershipHistory.unit_id, OwnershipHistory.owner_profile_id).with_for_update())).all())
        by_unit = defaultdict(list)
        for row in history:
            by_unit[row.unit_id].append(row)
        cells = []
        for unit in units:
            owners = by_unit[unit.id]
            if unit.mea is None or unit.mea <= 0 or not owners:
                raise HTTPException(422, "Every unit needs positive MEA and due-date ownership")
            if len(owners) > 1 and any(o.mea_share is None or o.mea_share <= 0 for o in owners):
                raise HTTPException(422, "Co-owners require positive ownership shares")
            total = sum((o.mea_share for o in owners), Decimal(0)) if len(owners) > 1 else Decimal(1)
            for owner in owners:
                if not await self.db.scalar(text("SELECT profile_source_access(:org,'owner_profile',:owner,'read')"),
                        {"org": self.org, "owner": owner.owner_profile_id}):
                    raise HTTPException(404, "Complete owner sources not accessible")
                weight = unit.mea * owner.mea_share / total if len(owners) > 1 else unit.mea
                cells.append((unit, owner, weight))
        allocations = [SpecialLevyAllocation(id=uuid.uuid4(), organization_id=self.org, property_id=property_id,
            special_levy_id=levy.id, unit_id=unit.id, owner_profile_id=owner.owner_profile_id,
            ownership_history_id=owner.id, mea=weight, amount=share)
            for (unit, owner, weight), share in zip(cells, distribute_amount(amount, [c[2] for c in cells]), strict=True)]
        self.db.add(levy)
        await self.db.flush()
        self.db.add_all(allocations)
        await self.db.flush()
        self.db.add(AuditEvent(organization_id=self.org, actor_user_id=self.user, entity_type="special_levy",
            entity_id=levy.id, action="special_levy.created", payload_diff={"amount": str(amount),
                "due_date": due_date.isoformat(), "property_id": str(property_id), "accounting_scope_id": str(accounting_scope_id)}))
        await self.db.flush()
        return levy, allocations

    async def list(self, accounting_scope_id=None, property_id=None):
        if accounting_scope_id is None:
            await self.context.lock_inputs()
            scope_ids = await self.db.scalar(text("SELECT special_levy_list_scopes(:org,:property)"),
                {"org": self.org, "property": property_id})
            if scope_ids is None:
                raise HTTPException(404, "Complete WEG list scope and sources not accessible")
        else:
            scope_ids = [accounting_scope_id]
        result = []
        for scope_id in scope_ids:
            await self.scope(scope_id, property_id)
            rows = (await self.db.scalars(select(SpecialLevy).where(SpecialLevy.organization_id == self.org,
                SpecialLevy.accounting_scope_id == scope_id).order_by(SpecialLevy.due_date, SpecialLevy.id))).all()
            for row in rows:
                allocations = list((await self.db.scalars(select(SpecialLevyAllocation).where(
                    SpecialLevyAllocation.organization_id == self.org, SpecialLevyAllocation.special_levy_id == row.id
                ).order_by(SpecialLevyAllocation.unit_id, SpecialLevyAllocation.owner_profile_id))).all())
                result.append((row, allocations))
        return sorted(result, key=lambda item: (item[0].due_date, item[0].id))

    async def owner_allocations(self, owner_id, start: date, end: date, scope_id, property_id=None):
        stmt = select(SpecialLevyAllocation, SpecialLevy.due_date).join(SpecialLevy,
            SpecialLevy.id == SpecialLevyAllocation.special_levy_id).where(
                SpecialLevy.organization_id == self.org, SpecialLevyAllocation.organization_id == self.org,
                SpecialLevyAllocation.owner_profile_id == owner_id,
                SpecialLevy.due_date >= start, SpecialLevy.due_date <= end)
        if scope_id is not None:
            stmt = stmt.where(SpecialLevy.accounting_scope_id == scope_id)
        if property_id is not None:
            stmt = stmt.where(SpecialLevy.property_id == property_id)
        return (await self.db.execute(stmt.order_by(SpecialLevy.due_date, SpecialLevyAllocation.id))).all()
