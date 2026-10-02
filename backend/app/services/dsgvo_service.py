"""DSGVO service -- Auskunft / Loeschung (Art. 15/17/18 DSGVO) over the DB.

Boundary discipline (SDD section 16): this service loads ORM rows, builds the
pure ``app.engine.dsgvo`` reconciliation inputs, lets the engine DECIDE
(RESTRICT vs. ANONYMIZE_NOW), and applies the decision. It NEVER re-implements
the retention preview. Effects use an authoritative database command that changes
only the person row and canonical audit, never retained financial records.

Three operations:
  * request_deletion -- the Art. 17 entry point. Gathers retention bindings,
    asks the engine, then either RESTRICTS (bound) or ANONYMIZES (unbound).
  * anonymize        -- explicit purge / force. Strictly refuses while the
    person is still retention-bound (only permitted once all retention expired).
  * export_person_data -- Art. 15/20 data export (read-only).

A LIVE contractual relationship (current/future lease or ownership) blocks
erasure entirely with 409: the processing purpose (Art. 6 I b) still exists; the
relationship must be ended first. This is a DB-driven service rule, kept out of
the pure engine (which only knows retention dates).
"""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import HTTPException

from app.engine.dsgvo import (
    BINDING_COST_ENTRY_VENDOR,
    BINDING_DUNNING,
    BINDING_FINALIZED_OWNERSHIP,
    BINDING_SETTLEMENT_ADDRESSEE,
    RetentionBinding,
    decide_deletion,
    retention_from_year_end,
)
from app.repositories.dsgvo_repo import DsgvoRepository


class DsgvoService:
    def __init__(self, session, org_id: uuid.UUID, user_id: uuid.UUID):
        self._session = session
        self._org_id = org_id
        self._user_id = user_id
        self._repo = DsgvoRepository(session, org_id)

    # ==================================================================
    # Public operations
    # ==================================================================
    async def status(self, person_id: uuid.UUID) -> dict:
        """Read-only preview: current DSGVO state + what an erasure would do."""
        person = await self._require_person(person_id)
        ref = date.today()
        has_active = await self._has_active_relationship(person_id, ref)
        bindings = await self._gather_bindings(person_id)
        decision = decide_deletion(bindings, ref)
        return self._state_dict(person, decision, has_active, applied="NONE")

    async def request_deletion(self, person_id: uuid.UUID) -> dict:
        return await self._apply_privacy_command(person_id, "REQUEST_DELETION")

    async def anonymize(self, person_id: uuid.UUID) -> dict:
        return await self._apply_privacy_command(person_id, "ANONYMIZE")

    async def _apply_privacy_command(self, person_id, command):
        from sqlalchemy import text
        person = await self._require_person(person_id)
        bindings = await self._gather_bindings(person_id)
        decision = decide_deletion(bindings, date.today())
        outcome = await self._session.scalar(text('SELECT apply_person_privacy(:org,:person,:command)'),
            {'org': self._org_id, 'person': person_id, 'command': command})
        errors = {
            'INVALID': (422, 'Invalid privacy command'),
            'FORBIDDEN': (403, 'Current administrator and contact preparation rights required'),
            'NOT_FOUND': (404, 'Person not found'),
            'PAUSED': (409, 'Source effects are paused'),
            'INCOMPLETE': (409, 'Complete financial and source access is required'),
            'ACTIVE': (409, 'Person hat eine aktive vertragliche Beziehung'),
            'RETAINED': (409, f'Person ist aufbewahrungspflichtig gebunden bis {_iso(decision.governing_retention_until)}'),
        }
        if outcome in errors:
            status, detail = errors[outcome]
            raise HTTPException(status, detail)
        if outcome not in {'RESTRICTED', 'ANONYMIZED', 'NONE_ALREADY_ANONYMIZED'}:
            raise RuntimeError('Unknown privacy command result')
        await self._session.refresh(person)
        return self._state_dict(person, decision, False, applied=outcome)

    async def export_person_data(self, person_id: uuid.UUID) -> dict:
        """Art. 15/20 export: the person's stored PII, roles, and bindings."""
        person = await self._require_person(person_id, capability='export')
        bindings = await self._gather_bindings(person_id, capability='export')
        return {
            "person": {
                "id": str(person.id),
                "first_name": person.first_name,
                "last_name": person.last_name,
                "company_name": person.company_name,
                "email": person.email,
                "phone": person.phone,
                "street": person.street,
                "zip_code": person.zip_code,
                "city": person.city,
                "iban": person.iban,
                "bic": person.bic,
                "bank_name": person.bank_name,
                "created_at": _iso(person.created_at),
            },
            "roles": {
                "vendor_ids": [str(i) for i in await self._repo.vendor_ids(person_id)],
                "tenant_profile_ids": [
                    str(i) for i in await self._repo.tenant_profile_ids(person_id)
                ],
                "owner_profile_ids": [
                    str(i) for i in await self._repo.owner_profile_ids(person_id)
                ],
            },
            "processing": {
                "anonymized_at": _iso(person.anonymized_at),
                "processing_restricted": person.processing_restricted,
                "deletion_requested_at": _iso(person.deletion_requested_at),
            },
            "retention_bindings": [_binding_dict(b) for b in bindings],
        }

    # ==================================================================
    # Internals
    # ==================================================================
    async def _require_person(self, person_id: uuid.UUID, *, capability="read"):
        person = await self._repo.get_person(person_id)
        if person is None:
            raise HTTPException(status_code=404, detail="Person not found")
        await self._repo.require_complete_financial_context(person_id, capability=capability)
        return person

    async def _gather_bindings(self, person_id: uuid.UUID, *, capability='read') -> list[RetentionBinding]:
        vendor_ids = await self._repo.vendor_ids(person_id)
        tenant_ids = await self._repo.tenant_profile_ids(person_id)
        owner_ids = await self._repo.owner_profile_ids(person_id)
        bindings: list[RetentionBinding] = []

        for ce in await self._repo.confirmed_cost_entries_for_vendors(vendor_ids):
            ret = await self._repo.max_retention_for_cost_entry(ce.id)
            if ret is None:
                ret = retention_from_year_end(ce.service_period_to)
            bindings.append(
                RetentionBinding(
                    kind=BINDING_COST_ENTRY_VENDOR,
                    entity_type="cost_entry",
                    entity_id=ce.id,
                    retention_until=ret,
                )
            )

        for run in await self._repo.finalized_bk_runs_for_tenants(tenant_ids):
            bindings.append(
                RetentionBinding(
                    kind=BINDING_SETTLEMENT_ADDRESSEE,
                    entity_type="settlement_run",
                    entity_id=run.id,
                    retention_until=retention_from_year_end(run.period_end),
                )
            )

        for run in await self._repo.finalized_weg_runs_for_owners(owner_ids):
            bindings.append(
                RetentionBinding(
                    kind=BINDING_FINALIZED_OWNERSHIP,
                    entity_type="settlement_run",
                    entity_id=run.id,
                    retention_until=retention_from_year_end(run.period_end),
                )
            )

        for plan in await self._repo.finalized_plans_for_owners(owner_ids):
            bindings.append(
                RetentionBinding(
                    kind=BINDING_FINALIZED_OWNERSHIP,
                    entity_type="economic_plan",
                    entity_id=plan.id,
                    retention_until=retention_from_year_end(plan.period_end),
                )
            )

        for dn in await self._repo.issued_dunning(tenant_ids, owner_ids):
            bindings.append(
                RetentionBinding(
                    kind=BINDING_DUNNING,
                    entity_type="dunning_notice",
                    entity_id=dn.id,
                    retention_until=retention_from_year_end(dn.issue_date),
                )
            )

        # ACL locking serializes revocation, but grants can expire while these
        # ordinary RLS-filtered reads run. Recheck completeness before treating
        # an empty/partial result as an erasure decision or exportable evidence.
        await self._repo.require_complete_financial_context(person_id, capability=capability)
        return bindings

    async def _has_active_relationship(
        self, person_id: uuid.UUID, ref: date
    ) -> bool:
        tenant_ids = await self._repo.tenant_profile_ids(person_id)
        if await self._repo.has_active_lease(tenant_ids, ref):
            return True
        owner_ids = await self._repo.owner_profile_ids(person_id)
        if await self._repo.has_active_ownership(owner_ids, ref):
            return True
        return False

    def _state_dict(self, person, decision, has_active: bool, applied: str) -> dict:
        return {
            "person_id": str(person.id),
            "applied": applied,
            "already_anonymized": person.anonymized_at is not None,
            "anonymized_at": _iso(person.anonymized_at),
            "processing_restricted": person.processing_restricted,
            "deletion_requested_at": _iso(person.deletion_requested_at),
            "has_active_relationship": has_active,
            "erasable": (not has_active) and person.anonymized_at is None,
            "decision": decision.decision,
            "is_bound": decision.is_bound,
            "purgeable_now": decision.purgeable_now,
            "governing_retention_until": _iso(decision.governing_retention_until),
            "bindings": [_binding_dict(b) for b in decision.bindings],
        }


def _iso(value) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _binding_dict(b: RetentionBinding) -> dict:
    return {
        "kind": b.kind,
        "entity_type": b.entity_type,
        "entity_id": str(b.entity_id),
        "retention_until": _iso(b.retention_until),
    }
