"""Delete only unused master rows, without bypassing source RLS or retention."""
from __future__ import annotations

from fastapi import Depends, HTTPException
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError

from app.core.live_principal import require_live_principal
from app.core.tenant import AuthContext, get_db, require_write_dep
from app.models.lease_agreement import LeaseAgreement
from app.models.person import Person
from app.models.property import Property
from app.models.unit import Unit
from app.services.financial_scope_service import FinancialScopeService
from app.services.history_source_access import require_history_prepare
from app.services.person_source_access import require_person_prepare
from app.services.property_master_service import require_property_master


async def master_delete_writer_db(
    auth: AuthContext = Depends(require_write_dep),
    db=Depends(get_db, scope="function"),
):
    # Commit (and any late constraint error) before a success response is sent.
    await require_live_principal(db, auth, writer=True)
    return db


_REASONS = {
    "unit": "Property still has a unit (Einheit).",
    "lease": "Unit still has a lease (Mietvertrag).",
    "tenant_profile": "Person still has a tenant profile (Mieter-Profil).",
    "owner_profile": "Person still has an owner profile (Eigentümer-Profil).",
    "vendor": "Person still has a vendor profile (Lieferanten-Profil).",
    "rent_history": "Lease still has rent history (Miethistorie).",
    "financial": "Financial records still reference this master record.",
    "audience": "Property still has a protected data audience or grant.",
    "receipt": "An immutable creation or import receipt retains this record.",
    "snapshot": "A retained financial snapshot references this record.",
    "source": "Approved source evidence still references this record.",
    "audit": "Retained audit history references this record.",
    "privacy": "Privacy processing or retention protection prevents deletion.",
    "reference": "A retained dependent record still references this master record.",
}
_MODELS = {"property": Property, "unit": Unit, "person": Person, "lease_agreement": LeaseAgreement}


class MasterDeleteService:
    def __init__(self, db, auth):
        self.db, self.auth = db, auth
        self.org = auth.organization_id

    async def _get(self, model, resource_id, *, lock=False):
        query = select(model).where(model.organization_id == self.org, model.id == resource_id)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        row = await self.db.scalar(query)
        if row is None:
            raise HTTPException(404, "Master record not found or not accessible")
        return row

    async def delete(self, kind, resource_id):
        # The route supplies a fixed kind, never a user-selected SQL identifier.
        model = _MODELS[kind]
        await require_live_principal(self.db, self.auth, writer=True)
        await FinancialScopeService(self.db, self.org, self.auth.user_id).lock_inputs()
        row = await self._get(model, resource_id)
        if kind in {"unit", "lease_agreement"}:
            unit = row if kind == "unit" else await self._get(Unit, row.unit_id)
            await self._get(Property, unit.property_id, lock=True)
            await self.db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                {"key": f"scope-unit-owner:{self.org}:{unit.id}"})
            unit = await self._get(Unit, unit.id, lock=True)
        row = await self._get(model, resource_id, lock=True)
        if kind == "property":
            await require_property_master(self.db, self.org, row.id)
        elif kind == "unit":
            await require_property_master(self.db, self.org, row.property_id)
        elif kind == "person":
            await require_person_prepare(self.db, self.org, row.id)
            if row.processing_restricted or row.deletion_requested_at is not None or row.anonymized_at is not None:
                raise HTTPException(409, _REASONS["privacy"])
        else:
            await require_history_prepare(self.db, self.org, "lease_agreement", row)
        reason = await self.db.scalar(text(
            "SELECT master_delete_reference_kind(:org,:kind,:resource)"),
            {"org": self.org, "kind": kind, "resource": resource_id})
        if reason is not None:
            if reason == "inaccessible":
                raise HTTPException(404, "Master record not found or not accessible")
            raise HTTPException(409, _REASONS[reason])
        try:
            async with self.db.begin_nested():
                deleted = await self.db.scalar(delete(model).where(
                    model.organization_id == self.org, model.id == resource_id,
                ).returning(model.id).execution_options(synchronize_session=False))
                if deleted != resource_id:
                    raise HTTPException(409, "Source policy protects this record from deletion.")
                # Include deferred FKs in the response decision, not only commit.
                await self.db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        except IntegrityError as exc:
            if getattr(exc.orig, "sqlstate", None) == "23503":
                raise HTTPException(409, _REASONS["reference"]) from exc
            raise
