"""The metering ledger of the model-assisted invoice path: reserve a call before it leaves, settle it afterwards."""
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import update
from app.models.invoice_model_call import InvoiceModelCall as Call
from app.repositories.base import TenantRepository


class InvoiceModelCallRepository(TenantRepository[Call]):
    model = Call

    async def reserve(self, *, attempt_id: uuid.UUID, stage: str, pdf_page: int | None, model_name: str,
                      input_tokens: int, output_tokens: int, cost_usd: Decimal) -> uuid.UUID:
        """Durably hold the worst case of one request; the monthly total counts it from this commit on."""
        call = await self.create(Call(attempt_id=attempt_id, stage=stage, pdf_page=pdf_page, model_name=model_name,
            status="RESERVED", reserved_input_tokens=input_tokens, reserved_output_tokens=output_tokens, reserved_cost_usd=cost_usd))
        return call.id

    async def settle(self, call_id: uuid.UUID, *, input_tokens: int, output_tokens: int, cost_usd: Decimal,
                     usage_complete: bool, error_code: str | None) -> bool:
        """Replace the reservation by the usage the request consumed; a settled call is never changed again."""
        result = await self._session.execute(update(Call).where(
            Call.organization_id == self._org_id, Call.id == call_id, Call.status == "RESERVED").values(
            status="SETTLED", input_tokens=input_tokens, output_tokens=output_tokens, cost_usd=cost_usd,
            usage_complete=usage_complete, error_code=error_code, settled_at=datetime.now(timezone.utc)))
        return result.rowcount == 1

    async def for_attempt(self, attempt_id: uuid.UUID) -> list[Call]:
        result = await self._session.execute(self._base_query().where(Call.attempt_id == attempt_id)
                                             .order_by(Call.created_at, Call.id).execution_options(populate_existing=True))
        return list(result.scalars())
