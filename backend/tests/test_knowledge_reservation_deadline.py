"""Offline reservation lifetime contracts, including delayed overlapping calls."""
import asyncio
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['PREPARATION', 'STRICT'])
async def test_reservation_deadline_uses_actual_database_expiry_and_elapsed_setup(monkeypatch, mode):
    import app.repositories.property_knowledge_query_repo as module
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    clock = SimpleNamespace(monotonic=1000.0, database_now=now)

    def advance(seconds):
        clock.monotonic += seconds
        clock.database_now += timedelta(seconds=seconds)

    # Patch only the repository's clock, never asyncio's global clock.
    monkeypatch.setattr(module, 'time', SimpleNamespace(monotonic=lambda: clock.monotonic))

    class Result:
        def __init__(self, lifetime=None): self.lifetime = lifetime
        def mappings(self): return self
        def one_or_none(self): return {'run_id':uuid4(),'budget_window_start':now,'reserved_cost_usd':Decimal('1')}
        def one(self): return self.lifetime

    class Session:
        async def scalar(self, statement, params):
            advance(.75)  # Policy/setup work before the reservation exists.
            return mode

        async def execute(self, statement, params):
            if 'reservation_expires_at' in str(statement):
                lifetime = SimpleNamespace(reservation_expires_at=self.expires, database_now=clock.database_now)
                advance(3)  # Time returning the sampled expiry/clock result.
                return Result(lifetime)
            self.expires = clock.database_now + timedelta(seconds=120)
            advance(2)  # Reservation SQL/result time consumes its lifetime.
            return Result()

    monkeypatch.setattr(module,'attest_runtime_budget',AsyncMock())
    started=clock.monotonic
    reservation=await module.PropertyKnowledgeQueryRepository(Session(),uuid4()).reserve_query(
        property_id=uuid4(),actor_user_id=uuid4(),budget_window_start=now,now=now,
        reservation_expires_at=now+timedelta(seconds=4000),provider='synthetic',model='synthetic',
        reserved_input_tokens=17,max_output_tokens=1000,reserved_cost_usd=Decimal('1'),budget_cap_usd=Decimal('1'))
    advance(4)  # Commit/setup after the repository returns, before provider I/O.
    # Strict SQL may ignore the caller's expiry; both paths must use the stored row.
    # The reservation starts after policy setup, and retains the ten-second margin.
    assert reservation.provider_deadline_monotonic == started + .75 + 120 - 10
    assert reservation.provider_deadline_monotonic - clock.monotonic == 120 - 10 - 2 - 3 - 4


@pytest.mark.asyncio
@pytest.mark.parametrize('endpoint_timeout', [180, 3600])
async def test_delayed_call_finalizes_conservatively_before_overlap_can_reclaim_capacity(monkeypatch, endpoint_timeout):
    import app.services.property_knowledge_service as module
    import app.services.ai_settings_service as ai
    from app.declarations.provider import ProviderReservationEstimate
    org,actor,prop=uuid4(),uuid4(),uuid4()
    rows=[]
    class Repo:
        def __init__(self,*args): pass
        async def reserve_query(self,**values):
            for row in rows:
                if row.status=='RESERVED' and time.monotonic()>=row.expires:
                    row.status='EXPIRED'
            if any(row.status in {'RESERVED','FINALIZED'} for row in rows): return None
            row=SimpleNamespace(run_id=uuid4(),model=values['model'],reserved_input_tokens=17,
                max_output_tokens=values['max_output_tokens'],reserved_cost_usd=values['reserved_cost_usd'],
                provider_deadline_monotonic=time.monotonic()+.08,expires=time.monotonic()+.12,status='RESERVED')
            rows.append(row);return row
        async def finalize_query(self,reservation,**values):
            if reservation.status!='RESERVED': return False
            reservation.status='FINALIZED';reservation.cost=values['estimated_cost_usd'];reservation.outcome=values['outcome'];return True
    @asynccontextmanager
    async def session(org):
        yield SimpleNamespace(execute=AsyncMock(),scalar=AsyncMock(return_value=actor))
        # Includes transaction completion in the pre-provider deadline.
        await asyncio.sleep(.005)
    class Service:
        def __init__(self,*args): pass
        async def prepare_question(self,*args): return SimpleNamespace(hits=[object()],request=object())
    class Provider:
        _timeout_seconds=endpoint_timeout
        _model='gpt-5.6-luna'
        def __init__(self): self.entered=asyncio.Event();self.calls=0;self.cancelled=False
        def estimate_reservation(self,*args,**kwargs): return ProviderReservationEstimate(17,1,(17,))
        async def answer(self,*args,**kwargs):
            self.calls+=1;self.entered.set()
            try: await asyncio.sleep(.25)
            except asyncio.CancelledError:
                self.cancelled=True;raise
            raise RuntimeError('synthetic delayed response')
    monkeypatch.setattr(module,'get_tenant_session',session)
    monkeypatch.setattr(module,'_knowledge_actor',AsyncMock())
    monkeypatch.setattr(module,'PropertyKnowledgeService',Service)
    monkeypatch.setattr(module,'PropertyKnowledgeQueryRepository',Repo)
    monkeypatch.setattr(ai,'effective_ai_settings',AsyncMock(return_value=SimpleNamespace(qa_enabled=True)))
    monkeypatch.setattr(ai,'require_organization_ai_budget',AsyncMock())
    provider=Provider()
    async def query():
        return await module.query_property_knowledge(organization_id=org,actor_user_id=actor,property_id=prop,
            question=object(),provider=provider,concurrency_guard=module._KnowledgeConcurrencyGuard())
    first=asyncio.create_task(query())
    try:
        await asyncio.wait_for(provider.entered.wait(),timeout=1)
        with pytest.raises(module.KnowledgeQueryBudgetExceeded): await query()
        await asyncio.sleep(.14)
        with pytest.raises(module.KnowledgeQueryBudgetExceeded): await query()
        with pytest.raises(module.KnowledgeQueryBudgetExceeded): await first
        assert provider.calls==1 and provider.cancelled
        assert rows[0].status=='FINALIZED' and rows[0].cost==rows[0].reserved_cost_usd
        assert rows[0].outcome=='PROVIDER_USAGE_EXCEEDED_RESERVATION'
    finally:
        if not first.done(): first.cancel()
        await asyncio.gather(first,return_exceptions=True)
