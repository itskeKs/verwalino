"""Create frozen special levies; project them into the existing owner ledger."""
import uuid
from collections import defaultdict
from datetime import date
from decimal import Decimal
from fractions import Fraction

from fastapi import HTTPException
from sqlalchemy import select, text

from app.core.rbac import ScopeCapability
from app.core.tenant import AuthContext
from app.engine.rounding import distribute_amount
from app.models.audit_event import AuditEvent
from app.models.ownership_history import OwnershipHistory
from app.models.property import Property
from app.models.special_levy import (
    SpecialLevy, SpecialLevyAllocation, SpecialLevyAllocationSupersession, counting_allocation,
)
from app.models.unit import Unit
from app.repositories.special_levy_repo import SpecialLevyRepository
from app.services.authorization_service import AuthorizationService
from app.services.financial_scope_service import FinancialScopeService


class SpecialLevyService:
    def __init__(self, session, org_id, user_id):
        self.db, self.org, self.user = session, org_id, user_id
        self.context = FinancialScopeService(session, org_id, user_id)
        self.repo = SpecialLevyRepository(session, org_id)

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
        if resolution_id is None:
            raise HTTPException(422, "Eine Sonderumlage braucht einen Beschluss.")
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
        await self.require_resolution(resolution_id, property_id)
        units = list((await self.db.scalars(select(Unit).where(Unit.organization_id == self.org,
            Unit.property_id == property_id).order_by(Unit.id).with_for_update())).all())
        if not units:
            raise HTTPException(422, "A complete positive MEA and ownership basis is required")
        history = await self._due_date_owners([u.id for u in units], due_date, lock=True)
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
            for owner, weight in zip(owners, self._owner_weights(unit.mea, owners), strict=True):
                if not await self.db.scalar(text("SELECT profile_source_access(:org,'owner_profile',:owner,'read')"),
                        {"org": self.org, "owner": owner.owner_profile_id}):
                    raise HTTPException(404, "Complete owner sources not accessible")
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

    async def _due_date_owners(self, unit_ids, due_date, *, lock=False):
        """The uncorrected ownership rows holding the units on the due date, by unit then owner."""
        stmt = select(OwnershipHistory).where(
            OwnershipHistory.organization_id == self.org, OwnershipHistory.unit_id.in_(unit_ids),
            OwnershipHistory.corrected_at.is_(None), OwnershipHistory.valid_from <= due_date,
            (OwnershipHistory.valid_to.is_(None) | (OwnershipHistory.valid_to > due_date)),
        ).order_by(OwnershipHistory.unit_id, OwnershipHistory.owner_profile_id)
        return list((await self.db.scalars(stmt.with_for_update() if lock else stmt)).all())

    @staticmethod
    def _owner_weights(unit_weight, owners):
        """The weight of each owner of one unit: the unit's weight, among co-owners split by their shares."""
        if len(owners) == 1:
            return [unit_weight]
        total = sum((o.mea_share for o in owners), Decimal(0))
        return [unit_weight * o.mea_share / total for o in owners]

    @staticmethod
    def _owner_fractions(owners):
        """Each owner's exact fraction of the unit, or None while co-owners lack a positive share."""
        if len(owners) == 1:
            return {owners[0].owner_profile_id: Fraction(1)}
        if any(o.mea_share is None or o.mea_share <= 0 for o in owners):
            return None
        total = sum((Fraction(o.mea_share) for o in owners), Fraction(0))
        return {o.owner_profile_id: Fraction(o.mea_share) / total for o in owners}

    @staticmethod
    def _levy_access_refusal():
        return HTTPException(409, {"code": "ownership_levy_access", "message":
            "Diese Einheit ist in einer Sonderumlage genannt, deren Zuordnung der Eigentümerwechsel ändert, "
            "und Sie haben darauf keinen Zugriff. Bitten Sie jemanden mit Schreibzugriff auf den "
            "Abrechnungskreis der Sonderumlage, die Eigentümer-Historie der Einheit zu ändern."})

    async def follow_ownership(self, unit_id):
        """Move the unit's amount of every levy naming it to the owners of the levy's due date (#681).

        Called in the transaction of every write to the unit's ownership history, after the write
        (also a write that changes nothing, which repairs an allocation that predates the rule).
        Allocation rows are immutable evidence, so a unit whose counting rows differ from the
        owners and shares of the due date gets its rows superseded (a marker with who and when)
        and new rows for the owners of the due date, the cents of the unit's amount preserved.
        Nothing moves while the due date has no owner or co-owners without positive shares: the
        rows stay as they are (the sale is recorded in two steps, and the settlement refuses an
        ownership gap or missing shares on its own). Whether a levy moves is decided from the
        definer view of the unit's counting rows, so a levy the actor may not read refuses the
        write only when it would move through it, instead of staying with the previous owner unseen.
        """
        named = (await self.db.execute(text("SELECT * FROM special_levy_unit_allocations(:org,:unit)"),
            {"org": self.org, "unit": unit_id})).all()
        if not named:
            return
        held = {row.id: row for row in (await self.db.scalars(select(OwnershipHistory).where(
            OwnershipHistory.organization_id == self.org,
            OwnershipHistory.id.in_({row.history_id for row in named})))).all()}
        by_levy = defaultdict(list)
        for row in named:
            by_levy[row.levy_id].append(row)
        for levy_id, rows in by_levy.items():  # by due date, then id
            owners = await self._due_date_owners([unit_id], rows[0].due)
            target = self._owner_fractions(owners) if owners else None
            basis = [held.get(row.history_id) for row in rows]
            if target is None or (None not in basis and self._owner_fractions(basis) == target):
                continue
            levy = await self.db.scalar(select(SpecialLevy).where(SpecialLevy.organization_id == self.org,
                SpecialLevy.id == levy_id))
            if levy is None:
                raise self._levy_access_refusal()
            await self._move(levy, unit_id, owners)

    async def _move(self, levy, unit_id, owners):
        rows = list((await self.db.scalars(select(SpecialLevyAllocation).where(
            SpecialLevyAllocation.organization_id == self.org, SpecialLevyAllocation.special_levy_id == levy.id,
            SpecialLevyAllocation.unit_id == unit_id, counting_allocation(),
        ).order_by(SpecialLevyAllocation.owner_profile_id))).all())
        if not rows or not await self.db.scalar(text("SELECT has_scope_capability(:org,:scope,'prepare')"),
                {"org": self.org, "scope": levy.accounting_scope_id}):
            raise self._levy_access_refusal()
        for owner in owners:
            if not await self.db.scalar(text("SELECT profile_source_access(:org,'owner_profile',:owner,'read')"),
                    {"org": self.org, "owner": owner.owner_profile_id}):
                raise self._levy_access_refusal()
        unit_amount = sum((row.amount for row in rows), Decimal(0))
        weights = self._owner_weights(sum((row.mea for row in rows), Decimal(0)), owners)
        amounts = distribute_amount(unit_amount, weights)
        self.db.add_all([SpecialLevyAllocationSupersession(allocation_id=row.id, organization_id=self.org,
            superseded_by=self.user) for row in rows])
        await self.db.flush()
        created = [SpecialLevyAllocation(id=uuid.uuid4(), organization_id=self.org, property_id=levy.property_id,
            special_levy_id=levy.id, unit_id=unit_id, owner_profile_id=owner.owner_profile_id,
            ownership_history_id=owner.id, mea=weight, amount=amount)
            for owner, weight, amount in zip(owners, weights, amounts, strict=True)]
        self.db.add_all(created)
        self.db.add(AuditEvent(organization_id=self.org, actor_user_id=self.user, entity_type="special_levy",
            entity_id=levy.id, action="special_levy.allocation_moved", payload_diff={
                "unit_id": str(unit_id), "due_date": levy.due_date.isoformat(),
                "superseded": [{"allocation_id": str(row.id), "owner_profile_id": str(row.owner_profile_id),
                    "amount": str(row.amount)} for row in rows],
                "allocated": [{"allocation_id": str(row.id), "owner_profile_id": str(row.owner_profile_id),
                    "amount": str(row.amount)} for row in created]}))
        await self.db.flush()

    async def require_resolution(self, resolution_id, property_id):
        resolution = await self.repo.resolution(resolution_id, property_id)
        if resolution is None:
            raise HTTPException(404, "Beschluss in diesem Objekt nicht gefunden.")
        if resolution.result != "ANGENOMMEN":
            raise HTTPException(422, "Eine Sonderumlage braucht einen angenommenen Beschluss.")
        return resolution

    async def link_resolution(self, levy_id, resolution_id):
        # Share creation's ACL/input lock order before locking the frozen row.
        await self.context.lock_inputs()
        levy = await self.repo.get(levy_id)
        if levy is None:
            raise HTTPException(404, "Sonderumlage nicht gefunden.")
        await self.scope(levy.accounting_scope_id, levy.property_id, write=True)
        levy = await self.repo.get(levy_id, lock=True)
        if levy is None:
            raise HTTPException(404, "Sonderumlage nicht gefunden.")
        if levy.resolution_id is not None:
            if levy.resolution_id != resolution_id:
                raise HTTPException(409, "Der Beschlussbezug ist bereits festgeschrieben.")
            return levy, await self.repo.allocations(levy.id)
        await self.require_resolution(resolution_id, levy.property_id)
        await self.repo.link_resolution(levy, resolution_id)
        self.db.add(AuditEvent(organization_id=self.org, actor_user_id=self.user,
            entity_type="special_levy", entity_id=levy.id, action="special_levy.resolution_linked",
            payload_diff={"resolution_id": str(resolution_id)}))
        await self.db.flush()
        return levy, await self.repo.allocations(levy.id)

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
                    SpecialLevyAllocation.organization_id == self.org, SpecialLevyAllocation.special_levy_id == row.id,
                    counting_allocation(),
                ).order_by(SpecialLevyAllocation.unit_id, SpecialLevyAllocation.owner_profile_id))).all())
                result.append((row, allocations))
        return sorted(result, key=lambda item: (item[0].due_date, item[0].id))

    def _allocations_due(self, start: date, end: date, scope_id, property_id):
        """Counting allocations of the levies falling due from `start` to `end`, both inclusive."""
        stmt = select(SpecialLevyAllocation, SpecialLevy.due_date).join(SpecialLevy,
            SpecialLevy.id == SpecialLevyAllocation.special_levy_id).where(
                SpecialLevy.organization_id == self.org, SpecialLevyAllocation.organization_id == self.org,
                SpecialLevy.due_date >= start, SpecialLevy.due_date <= end, counting_allocation())
        if scope_id is not None:
            stmt = stmt.where(SpecialLevy.accounting_scope_id == scope_id)
        if property_id is not None:
            stmt = stmt.where(SpecialLevy.property_id == property_id)
        return stmt

    async def owner_allocations(self, owner_id, start: date, end: date, scope_id, property_id=None):
        stmt = self._allocations_due(start, end, scope_id, property_id).where(
            SpecialLevyAllocation.owner_profile_id == owner_id)
        return (await self.db.execute(stmt.order_by(SpecialLevy.due_date, SpecialLevyAllocation.id))).all()

    async def property_allocations(self, property_id, start: date, end: date, scope_id):
        """Every owner's counting share of the property's levies due in the period (the settlement, #405)."""
        stmt = self._allocations_due(start, end, scope_id, property_id)
        return (await self.db.execute(stmt.order_by(SpecialLevy.due_date, SpecialLevyAllocation.id))).all()
