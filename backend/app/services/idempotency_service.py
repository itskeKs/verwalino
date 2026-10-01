"""Request digest validation; receipts commit with their business result."""
import hashlib
import json
from fastapi import HTTPException
from app.models.operation_receipt import OperationReceipt
from app.repositories.operation_receipt_repo import OperationReceiptRepository


class IdempotencyService:
    def __init__(self, session, org_id, actor_id):
        self.repo = OperationReceiptRepository(session, org_id)
        self.actor_id = actor_id

    @staticmethod
    def digest(payload: dict) -> str:
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    async def replay(self, operation, key, digest):
        if not key or len(key) > 128:
            raise HTTPException(422, "Idempotency-Key must contain 1–128 characters")
        receipt = await self.repo.lock_and_get(self.actor_id, operation, key)
        if receipt and receipt.request_digest != digest:
            raise HTTPException(409, "Idempotency-Key already used with a different request")
        return receipt.result_id if receipt else None

    async def record(self, operation, key, digest, result_id):
        await self.repo.create(OperationReceipt(actor_user_id=self.actor_id, operation=operation,
            key=key, request_digest=digest, result_id=result_id))
