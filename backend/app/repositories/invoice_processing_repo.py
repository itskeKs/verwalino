"""Tenant-scoped processing history and bounded queue queries."""
import uuid
from types import SimpleNamespace
from sqlalchemy import text
from app.models.invoice_processing_attempt import InvoiceProcessingAttempt as Attempt
from app.repositories.base import TenantRepository


class InvoiceProcessingRepository(TenantRepository[Attempt]):
    model = Attempt

    async def history(self, document_id):
        result = await self._session.execute(self._base_query().where(Attempt.document_id == document_id)
                                             .order_by(Attempt.attempt_no.desc()).execution_options(populate_existing=True))
        return list(result.scalars())

    async def queue_count_locked(self):
        # Whole-tenant admission must include jobs hidden by source RLS.
        return await self._session.scalar(text('SELECT invoice_queue_count(:org)'), {'org': self._org_id})

    async def principal_metadata(self, attempt_id=None):
        """Only immutable principal and fence metadata cross the scheduling boundary."""
        row = await self._session.scalar(text('SELECT invoice_queue_metadata(:org,:attempt)'),
                                         {'org': self._org_id, 'attempt': attempt_id})
        return SimpleNamespace(**{key: uuid.UUID(value) if value else None for key, value in row.items()}) if row else None
