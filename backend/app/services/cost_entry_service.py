"""CostEntry service -- state machine, atomic confirm, correction versioning.

m2-gobd-belegfluss.md section 3, section 8.4.

Key invariant: the confirm() method is an ATOMIC transaction (section 3.6):
  cost_entry.confirmed + document.released + retention_until + hash re-verify
  + audit_event -- all in one session.begin().
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi import HTTPException
from app.core.rbac import ScopeCapability, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.storage import DocumentStore, get_store, verify_document_integrity
from app.models.audit_event import AuditEvent
from app.services.financial_scope_service import FinancialScopeService
from app.models.cost_entry import CostEntry
from app.models.invoice_document import InvoiceDocument
from app.repositories.property_repo import PropertyRepository
from app.repositories.vendor_repo import VendorRepository
from app.repositories.mandate_repo import MandateRepository
from app.repositories.cost_category_repo import CostCategoryRepository
from app.services.idempotency_service import IdempotencyService
from app.repositories.audit_repo import AuditRepository
from app.repositories.cost_entry_repo import CostEntryRepository
from app.repositories.invoice_document_repo import InvoiceDocumentRepository


# Allowed state transitions (m2-gobd-belegfluss.md section 3.2)
ALLOWED_TRANSITIONS = {
    "draft_extracted": {"needs_review", "confirmed", "rejected"},
    "needs_review": {"confirmed", "rejected"},
    # confirmed: NO further status change (correction = new row)
    # rejected: could be reopened to needs_review in the future, not in M2
}


def _compute_retention_until(invoice_date: date) -> date:
    """Compute retention_until for a document.

    PROVISIONALLY: end of calendar year of invoice_date + 8 years.
    (m2-gobd-belegfluss.md section 7.3, decision O-1)

    This is a PROVISIONAL assumption, clearly marked.
    The correct legal start date per document type is to be confirmed
    by tax advisor / lawyer before M9.
    """
    year_end = date(invoice_date.year, 12, 31)
    return year_end.replace(year=year_end.year + 8)


class CostEntryService:
    def __init__(
        self,
        session: AsyncSession,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
    ):
        self._session = session
        self._org_id = org_id
        self._user_id = user_id
        self._repo = CostEntryRepository(session, org_id)
        self._doc_repo = InvoiceDocumentRepository(session, org_id)
        self._audit = AuditRepository(session, org_id)

    async def _validate_scope(self, entry: CostEntry) -> None:
        prop = await PropertyRepository(self._session, self._org_id).get_by_id(entry.property_id)
        if prop is None:
            raise HTTPException(404, "Property not found")
        if entry.vendor_id and await VendorRepository(self._session, self._org_id).get_by_id(entry.vendor_id) is None:
            raise HTTPException(404, "Vendor not found")
        if entry.vendor_id and not await self._session.scalar(text(
            "SELECT profile_reference_access(:org,'vendor',:vendor)"),
            {'org': self._org_id, 'vendor': entry.vendor_id}):
            raise HTTPException(404, "Vendor not found")
        if entry.management_mandate_id:
            mandate = await MandateRepository(self._session, self._org_id).get_by_id(entry.management_mandate_id)
            if mandate is None:
                raise HTTPException(404, "Management mandate not found")
            if prop.management_type != "MISCH" and mandate.mandate_type != prop.management_type:
                raise HTTPException(422, "Management mandate does not match property management type")
        category = await CostCategoryRepository(self._session).get_by_id(entry.cost_category_id)
        if category is None or category.plan_version != entry.cost_category_plan_version:
            raise HTTPException(422, "Cost category/version not found")

    async def create(self, entry: CostEntry, *, accounting_scope_id: uuid.UUID | None = None, source_document_id: uuid.UUID | None = None,
                     idempotency_key: str | None = None, request_digest: str | None = None) -> CostEntry:
        """Create a new cost entry (manual or from import).

        Decision E-6: manual entries start as editable drafts;
        confirm is always an explicit second step.
        """
        if entry.status not in {"needs_review", "draft_extracted"}:
            raise HTTPException(422, "New costs must be drafts; confirmation is a separate action")
        if entry.source != "MANUAL" and source_document_id is None:
            raise HTTPException(422, "Imported costs require source_document_id")
        await FinancialScopeService(self._session, self._org_id, self._user_id).lock_inputs()
        await self._validate_scope(entry)
        idem = IdempotencyService(self._session, self._org_id, self._user_id)
        if idempotency_key is not None:
            if request_digest is None:
                raise ValueError("Request digest required with idempotency key")
            previous_id = await idem.replay("cost_entry.create", idempotency_key, request_digest)
            if previous_id:
                previous = await self._repo.get_by_id(previous_id)
                if previous is None:
                    raise HTTPException(404, "Cost entry not found")
                if source_document_id and await self._doc_repo.get_by_id(source_document_id) is None:
                    raise HTTPException(404, "Source document not found")
                return previous
        doc = None
        if source_document_id:
            doc = await self._doc_repo.get_by_id_for_update(source_document_id)
            if doc is None:
                raise HTTPException(404, "Source document not found")
            if doc.status != "active" or doc.released_at or doc.cost_entry_id:
                raise HTTPException(409, "Source document is already linked, released or superseded")
        sphere = await FinancialScopeService(self._session, self._org_id, self._user_id).prepare_new(entry, accounting_scope_id)
        entry.organization_id = self._org_id
        entry = await self._repo.create(entry)
        if doc:
            # This locked original was authorized before selecting the destination
            # sphere. Its old unclassified row must remain visible for the link;
            # the DB write guard checks the destination's prepare capability.
            # SET LOCAL rolls back with this savepoint if linking fails; a
            # finally-query would itself fail after a PostgreSQL flush error.
            async with self._session.begin_nested():
                if sphere is not None:
                    await self._session.execute(text("SELECT set_config('app.current_scope_id','',true)"))
                doc.cost_entry_id = entry.id
                await self._doc_repo.update(doc)
                if sphere is not None:
                    await self._session.execute(text("SELECT set_config('app.current_scope_id',:scope,true)"), {'scope': str(sphere.id)})
            await self._audit.create(AuditEvent(actor_user_id=self._user_id,
                entity_type="invoice_document", entity_id=doc.id, action="document.linked",
                payload_diff={"cost_entry_id": str(entry.id)}))
        if idempotency_key is not None:
            await idem.record("cost_entry.create", idempotency_key, request_digest, entry.id)

        await self._audit.create(
            AuditEvent(
                actor_user_id=self._user_id,
                entity_type="cost_entry",
                entity_id=entry.id,
                action="cost_entry.created",
                payload_diff={
                    "source": entry.source,
                    "status": entry.status,
                    "gross_amount": str(entry.gross_amount),
                    "cost_category_id": str(entry.cost_category_id),
                },
            )
        )
        return entry

    async def update_draft(
        self, entry_id: uuid.UUID, updates: dict
    ) -> CostEntry:
        """Update a draft/needs_review entry. Confirmed entries cannot be updated."""
        entry = await self._repo.get_by_id_for_update(entry_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="Cost entry not found")

        if entry.status == "confirmed":
            raise HTTPException(
                status_code=409,
                detail="Cannot edit a confirmed cost entry; create a correction version instead",
            )

        changed = {}
        for field_name, value in updates.items():
            if hasattr(entry, field_name):
                old_val = getattr(entry, field_name)
                setattr(entry, field_name, value)
                changed[field_name] = {"old": str(old_val), "new": str(value)}

        await self._validate_scope(entry)
        entry = await self._repo.update(entry)

        if changed:
            await self._audit.create(
                AuditEvent(
                    actor_user_id=self._user_id,
                    entity_type="cost_entry",
                    entity_id=entry.id,
                    action="cost_entry.updated",
                    payload_diff=changed,
                )
            )

        return entry

    async def confirm(
        self,
        entry_id: uuid.UUID,
        store: DocumentStore | None = None,
    ) -> CostEntry:
        """Confirm a cost entry -- atomic transaction (section 3.6).

        1. Validate required fields (section 2.3).
        2. Set cost_entry.status = 'confirmed', confirmed_at, confirmed_by.
        3. If document attached: release it (released_at, released_by, retention_until),
           re-verify hash against stored file.
        4. Write audit events.
        5. All within one transaction (session.begin already active from get_db).

        Raises 422 on validation failure, 409 on illegal transition.
        """
        if store is None:
            store = get_store()

        entry = await self._repo.get_by_id_for_update(entry_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="Cost entry not found")

        # Check transition is allowed
        allowed = ALLOWED_TRANSITIONS.get(entry.status, set())
        if "confirmed" not in allowed:
            raise HTTPException(
                status_code=409,
                detail=f"Cannot confirm from status '{entry.status}'",
            )

        # Validate required fields for confirmation (section 2.3)
        errors = []
        if entry.cost_category_id is None:
            errors.append("cost_category_id is required")
        if entry.gross_amount is None:
            errors.append("gross_amount is required")
        if entry.invoice_date is None:
            errors.append("invoice_date is required")
        if entry.service_period_from is None:
            errors.append("service_period_from is required")
        if entry.service_period_to is None:
            errors.append("service_period_to is required")
        if entry.tenant_apportionable is None:
            errors.append("tenant_apportionable is required")
        if errors:
            raise HTTPException(status_code=422, detail="; ".join(errors))

        now = datetime.now(timezone.utc)

        await self._validate_scope(entry)
        doc = await self._doc_repo.get_active_for_cost_entry(entry_id)
        if entry.source != "MANUAL" and doc is None:
            raise HTTPException(422, "Imported cost has no active source document")

        # 2. Freeze the cost entry
        entry.status = "confirmed"
        entry.confirmed_at = now
        entry.confirmed_by = self._user_id
        entry = await self._repo.update(entry)

        # 3. Release attached document if present
        doc = await self._doc_repo.get_active_for_cost_entry(entry_id)
        if doc is not None:
            # Re-verify hash (section 4.5, section 3.6 step 3)
            integrity_ok = verify_document_integrity(
                doc.content_sha256, doc.storage_uri, store
            )
            if not integrity_ok:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Document integrity check failed for document {doc.id} "
                        f"(hash mismatch between upload and confirm). "
                        f"Confirm aborted."
                    ),
                )

            doc.released_at = now
            doc.released_by = self._user_id
            # Provisional retention calculation (O-1)
            doc.retention_until = _compute_retention_until(entry.invoice_date)
            await self._doc_repo.update(doc)

            # Audit: document released
            await self._audit.create(
                AuditEvent(
                    actor_user_id=self._user_id,
                    entity_type="invoice_document",
                    entity_id=doc.id,
                    action="document.released",
                    payload_diff={
                        "released_by": str(self._user_id),
                        "sha256": doc.content_sha256,
                        "retention_until": str(doc.retention_until),
                    },
                )
            )

        # 4. Audit: cost entry confirmed
        await self._audit.create(
            AuditEvent(
                actor_user_id=self._user_id,
                entity_type="cost_entry",
                entity_id=entry.id,
                action="cost_entry.confirmed",
                payload_diff={
                    "confirmed_by": str(self._user_id),
                    "gross_amount": str(entry.gross_amount),
                    "cost_category_id": str(entry.cost_category_id),
                    "invoice_date": str(entry.invoice_date),
                    "service_period": f"{entry.service_period_from} - {entry.service_period_to}",
                },
            )
        )

        return entry

    async def reject(self, entry_id: uuid.UUID) -> CostEntry:
        """Reject a cost entry (discard from workflow)."""
        entry = await self._repo.get_by_id_for_update(entry_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="Cost entry not found")

        allowed = ALLOWED_TRANSITIONS.get(entry.status, set())
        if "rejected" not in allowed:
            raise HTTPException(
                status_code=409,
                detail=f"Cannot reject from status '{entry.status}'",
            )

        entry.status = "rejected"
        entry = await self._repo.update(entry)

        await self._audit.create(
            AuditEvent(
                actor_user_id=self._user_id,
                entity_type="cost_entry",
                entity_id=entry.id,
                action="cost_entry.rejected",
                payload_diff={"status": "rejected"},
            )
        )
        return entry

    async def create_correction_version(
        self,
        original_id: uuid.UUID,
        corrections: dict,
    ) -> CostEntry:
        """Create a correction version of a confirmed entry (section 3.4).

        The original is linked via superseded_by_id; its content stays frozen.
        The new entry starts as needs_review with corrected values.
        """
        scope = FinancialScopeService(self._session, self._org_id, self._user_id)
        original = await self._repo.get_by_id(original_id)
        if original is None:
            raise HTTPException(404, "Financial original not found")
        await scope.enter(original, capability=ScopeCapability.CONFIRM)
        await scope.lock_inputs()
        original = await self._repo.get_by_id_for_update(original_id)
        if original is None:
            raise HTTPException(status_code=404, detail="Cost entry not found")

        if original.status != "confirmed":
            raise HTTPException(
                status_code=409,
                detail="Only confirmed entries can be corrected via new version",
            )

        if original.superseded_by_id is not None:
            raise HTTPException(
                status_code=409,
                detail="This entry has already been superseded",
            )

        # Create the new entry with corrected values
        new_entry = CostEntry(
            organization_id=self._org_id,
            property_id=corrections.get("property_id", original.property_id),
            management_mandate_id=corrections.get(
                "management_mandate_id", original.management_mandate_id
            ),
            cost_category_id=corrections.get(
                "cost_category_id", original.cost_category_id
            ),
            cost_category_plan_version=corrections.get(
                "cost_category_plan_version", original.cost_category_plan_version
            ),
            vendor_id=corrections.get("vendor_id", original.vendor_id),
            invoice_date=corrections.get("invoice_date", original.invoice_date),
            service_period_from=corrections.get(
                "service_period_from", original.service_period_from
            ),
            service_period_to=corrections.get(
                "service_period_to", original.service_period_to
            ),
            gross_amount=corrections.get("gross_amount", original.gross_amount),
            vat_amount=corrections.get("vat_amount", original.vat_amount),
            vat_rate=corrections.get("vat_rate", original.vat_rate),
            document_number=corrections.get(
                "document_number", original.document_number
            ),
            owner_distribution_key=corrections.get(
                "owner_distribution_key", original.owner_distribution_key
            ),
            tenant_distribution_key=corrections.get(
                "tenant_distribution_key", original.tenant_distribution_key
            ),
            tenant_apportionable=corrections.get(
                "tenant_apportionable", original.tenant_apportionable
            ),
            apportionable_tenant_amount=corrections.get(
                "apportionable_tenant_amount", original.apportionable_tenant_amount
            ),
            status="needs_review",
            source="MANUAL",
        )
        await scope.prepare_new(new_entry)
        new_entry = await self._repo.create(new_entry)

        # Link original to new version (the only allowed mutation on confirmed rows)
        original.superseded_by_id = new_entry.id
        await self._repo.update(original)

        # Audit events
        await self._audit.create(
            AuditEvent(
                actor_user_id=self._user_id,
                entity_type="cost_entry",
                entity_id=original.id,
                action="cost_entry.superseded",
                payload_diff={"superseded_by_id": str(new_entry.id)},
            )
        )
        await self._audit.create(
            AuditEvent(
                actor_user_id=self._user_id,
                entity_type="cost_entry",
                entity_id=new_entry.id,
                action="cost_entry.created",
                payload_diff={
                    "source": "MANUAL",
                    "status": "needs_review",
                    "correction_of": str(original.id),
                    "gross_amount": str(new_entry.gross_amount),
                },
            )
        )

        return new_entry
