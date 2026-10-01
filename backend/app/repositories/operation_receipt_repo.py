"""Tenant-scoped operation receipts and transaction-scoped serialization."""
import hashlib
import uuid
from sqlalchemy import text
from app.models.operation_receipt import OperationReceipt
from app.repositories.base import TenantRepository


class OperationReceiptRepository(TenantRepository[OperationReceipt]):
    model = OperationReceipt

    async def lock_and_get(self, actor: uuid.UUID, operation: str, key: str):
        identity = f"{self._org_id}:{actor}:{operation}:{key}"
        lock_id = int.from_bytes(hashlib.sha256(identity.encode()).digest()[:8], signed=True)
        await self._session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_id})
        result = await self._session.execute(self._base_query().where(
            OperationReceipt.actor_user_id == actor,
            OperationReceipt.operation == operation,
            OperationReceipt.key == key,
        ))
        return result.scalar_one_or_none()
