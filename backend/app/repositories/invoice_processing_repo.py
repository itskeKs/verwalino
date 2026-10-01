"""Tenant-scoped processing history and bounded queue queries."""
from datetime import datetime, timezone
from sqlalchemy import func, or_, select, text
from app.models.invoice_processing_attempt import InvoiceProcessingAttempt as Attempt
from app.repositories.base import TenantRepository


class InvoiceProcessingRepository(TenantRepository[Attempt]):
    model = Attempt

    async def history(self, document_id):
        result = await self._session.execute(self._base_query().where(Attempt.document_id == document_id)
                                             .order_by(Attempt.attempt_no.desc()).execution_options(populate_existing=True))
        return list(result.scalars())

    async def queue_count_locked(self):
        # Serialize admission per org, independent of the idempotency key domain.
        await self._session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
                                    {"scope": f"invoice-queue:{self._org_id}"})
        result = await self._session.execute(select(func.count()).select_from(Attempt).where(
            Attempt.organization_id == self._org_id, Attempt.status.in_(["queued", "running"])))
        return result.scalar_one()

    async def next_document(self):
        now = datetime.now(timezone.utc)
        result = await self._session.execute(self._base_query().where(or_(
            Attempt.status == "queued", (Attempt.status == "running") & (Attempt.lease_until < now)))
            .order_by(Attempt.created_at, Attempt.id).limit(1))
        attempt = result.scalar_one_or_none()
        return attempt.document_id if attempt else None
