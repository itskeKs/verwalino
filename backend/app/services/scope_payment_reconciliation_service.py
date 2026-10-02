"""Reviewable bounded preview and atomic proof recheck for legacy money."""
import json
from fastapi import HTTPException
from sqlalchemy import String, cast, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import aliased
from app.core.rbac import ScopeCapability
from app.models.accounting_scope import AccountingScopeBinding
from app.models.bank_account import BankAccount
from app.models.payment import Payment
from app.repositories.pagination import Page, page_rows
from app.schemas.scope_payment_reconciliation import LegacyPaymentCandidate, ReconciliationResult
from app.services.authorization_service import AuthorizationService
from app.services.financial_scope_service import FinancialScopeService


class ScopePaymentReconciliationService(AuthorizationService):
    async def authorize(self, scope_id):
        await self._require_administration()
        await self.require(scope_id, ScopeCapability.PREPARE)
        await FinancialScopeService(self.session, self.auth.organization_id, self.auth.user_id).lock_inputs()

    async def preview(self, scope_id, query):
        await self.authorize(scope_id)
        bank_binding = aliased(AccountingScopeBinding)
        already_bound = select(AccountingScopeBinding.id).where(
            AccountingScopeBinding.organization_id == self.auth.organization_id,
            AccountingScopeBinding.payment_id == Payment.id).exists()
        rows, cursor = await page_rows(self.session, self.auth, Payment, cast(Payment.id, String),
            [cast(Payment.id, String)], query, context=f'legacy-payment-proof-v1:{scope_id}',
            joins=((bank_binding, (bank_binding.organization_id == Payment.organization_id)
                    & (bank_binding.bank_account_id == Payment.bank_account_id)),),
            filters=(bank_binding.scope_id == scope_id, ~already_bound))
        banks = (await self.session.scalars(select(BankAccount).where(
            BankAccount.organization_id == self.auth.organization_id,
            BankAccount.id.in_([row.bank_account_id for row in rows])))).all() if rows else []
        labels = {bank.id: bank.label for bank in banks}
        items = []
        for row in rows:
            proof = await self.session.scalar(text('SELECT legacy_payment_proof(:org,:scope,:payment)'),
                {'org': self.auth.organization_id, 'scope': scope_id, 'payment': row.id})
            items.append(LegacyPaymentCandidate(id=row.id, bank_account_id=row.bank_account_id,
                bank_label=labels.get(row.bank_account_id, 'Unavailable account'), amount=str(row.amount),
                direction=row.direction, value_date=row.value_date, payment_type=row.payment_type, **proof))
        return Page(items=items, next_cursor=cursor)

    async def apply(self, scope_id, command, request_key):
        await self.authorize(scope_id)
        try:
            async with self.session.begin_nested():
                result = await self.session.scalar(text('SELECT reconcile_legacy_payments(:org,:scope,:key,CAST(:items AS jsonb),:reason)'),
                    {'org': self.auth.organization_id, 'scope': scope_id, 'key': request_key,
                     'items': json.dumps([item.model_dump(mode='json') for item in command.items]), 'reason': command.reason})
        except DBAPIError as error:
            code = getattr(error.orig, 'sqlstate', None)
            if code == 'P0001':
                raise HTTPException(409, 'Payment evidence changed, needs review, or the retry key belongs to another command. Reload the preview.') from None
            if code in {'22023', '22P02'}:
                raise HTTPException(422, 'Invalid payment reconciliation command') from None
            if code == '42501':
                raise HTTPException(404, 'Reconciliation context not accessible') from None
            raise
        return ReconciliationResult.model_validate(result)
