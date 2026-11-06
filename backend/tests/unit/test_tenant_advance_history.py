"""Payment-service history regressions with scoped repository results."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi import HTTPException

from app.engine.mietkonto import PaymentInput, PT_HK_VORAUSZAHLUNG, PT_NK_VORAUSZAHLUNG
from app.repositories.settlement_repo import SettlementRunRepository
from app.services.payment_service import PaymentService

LEASE_ID = UUID(int=1)
YEAR_START = date(2025, 1, 1)
YEAR_END = date(2025, 12, 31)


@pytest.fixture(params=[PT_NK_VORAUSZAHLUNG, PT_HK_VORAUSZAHLUNG])
def advance_history(request, monkeypatch):
    kind = request.param
    prefix = 'heating_' if kind == PT_HK_VORAUSZAHLUNG else ''
    service = PaymentService(None, UUID(int=2), UUID(int=3))
    lease = SimpleNamespace(id=LEASE_ID, valid_from=YEAR_START, valid_to=None)
    inputs = []
    runs = []
    monkeypatch.setattr(service, '_nk_advance_inputs', AsyncMock(return_value=(lease, inputs)))
    monkeypatch.setattr(SettlementRunRepository, 'finalized_bk_for_advance_years', AsyncMock(return_value=runs))

    def payment(direction, amount, month):
        inputs.append(PaymentInput(UUID(int=10 + len(inputs)), kind, direction, Decimal(amount), 2025, month,
                                   lease_agreement_id=LEASE_ID))

    def finalize(start, end, total, buckets):
        data = {prefix + 'advance_payments_source': 'DERIVED',
                'lease_periods': [{'lease_agreement_id': str(LEASE_ID), 'from': '2025-01-01', 'to': None}]}
        if buckets is not None:
            data[prefix + 'advance_payment_credits'] = [
                {'year': 2025, 'month': month, 'amount': amount} for month, amount in buckets]
        payload = {'period': {'start': start, 'end': end}, 'inputs': data,
                   'result': {prefix + 'advance_payments_total': total}}
        runs.append(SimpleNamespace(snapshot_payload=payload))

    async def derive(start):
        return await service.derive_nk_advance_total_for_period(LEASE_ID, start, YEAR_END, payment_type=kind)

    return payment, finalize, derive


@pytest.mark.parametrize('buckets', [[], [(8, '0.00')]])
async def test_a_late_refund_accounts_for_an_omitted_zero_in_known_history(advance_history, buckets):
    payment, finalize, derive = advance_history
    payment('IN', '50.00', 8)
    finalize('2025-07-01', '2025-07-31', '0.00', buckets)
    payment('OUT', '70.00', 7)

    assert await derive(date(2025, 8, 1)) == Decimal('-20.00')


async def test_a_refund_requires_the_affected_legacy_breakdown_even_with_fixed_lease_dates(advance_history):
    payment, finalize, derive = advance_history
    payment('IN', '50.00', None)
    finalize('2025-01-01', '2025-06-30', '24.96', None)
    payment('OUT', '70.00', None)

    with pytest.raises(HTTPException) as caught:
        await derive(date(2025, 7, 1))
    assert caught.value.status_code == 409
    assert caught.value.detail['code'] == 'advance_credit_history_missing'


async def test_a_refund_assigned_outside_the_legacy_window_can_be_derived(advance_history):
    payment, finalize, derive = advance_history
    payment('IN', '50.00', 6)
    finalize('2025-01-01', '2025-06-30', '50.00', None)
    payment('OUT', '70.00', 7)

    assert await derive(date(2025, 7, 1)) == Decimal('-70.00')
