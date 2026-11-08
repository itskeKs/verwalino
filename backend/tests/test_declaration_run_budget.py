"""Run price classification uses immutable provider provenance."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4
from app.api.declaration_imports import _run_out


def test_custom_run_named_like_openai_is_unpriced_and_keeps_tokens_and_runtime():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    run = SimpleNamespace(id=uuid4(), provider="CompatibleDeclarationProvider", model_name="gpt-5.6-luna",
        input_tokens=42, output_tokens=17, estimated_cost_usd=Decimal("0"), started_at=start,
        completed_at=start+timedelta(seconds=123), status="FAILED")
    result = _run_out(run)
    assert result.estimated_cost_usd is None
    assert (result.input_tokens, result.output_tokens, result.runtime_seconds) == (42,17,123)
    run.provider = "OpenAiDeclarationProvider"
    assert _run_out(run).estimated_cost_usd == Decimal("0")
    run.provider = "UnknownProvider"
    assert _run_out(run).estimated_cost_usd is None


import pytest


@pytest.mark.parametrize('status', ['RUNNING','FAILED','SUCCEEDED'])
def test_without_pass_usage_ledger_tokens_are_only_reservations(status):
    start=datetime.now(timezone.utc)
    run=SimpleNamespace(id=uuid4(),provider='CompatibleDeclarationProvider',model_name='synthetic',
        input_tokens=10000,output_tokens=12000,estimated_cost_usd=Decimal('0'),started_at=start,
        completed_at=start if status!='RUNNING' else None,status=status)
    result=_run_out(run, [])
    assert result.usage_status=='RESERVED'
    assert result.reported_input_tokens is None and result.reported_output_tokens is None
    assert result.input_tokens==10000 and result.output_tokens==12000


def test_zero_ledger_without_usage_is_unknown_not_measured_zero():
    start=datetime.now(timezone.utc)
    run=SimpleNamespace(id=uuid4(),provider='CompatibleDeclarationProvider',model_name='synthetic',
        input_tokens=0,output_tokens=0,estimated_cost_usd=Decimal('0'),started_at=start,completed_at=start,status='FAILED')
    assert _run_out(run, []).usage_status=='UNKNOWN'


def test_reported_schema_failure_usage_is_preserved_but_unknown_remainder_is_reserved():
    start=datetime.now(timezone.utc)
    run=SimpleNamespace(id=uuid4(),provider='CompatibleDeclarationProvider',model_name='synthetic',
        input_tokens=100,output_tokens=200,estimated_cost_usd=Decimal('0'),started_at=start,completed_at=start,status='FAILED')
    passes=[{'input_tokens':30,'output_tokens':20,'failure_class':'PROVIDER_SCHEMA_FAILURE'}]
    partial=_run_out(run, passes)
    assert partial.usage_status=='PARTIAL'
    assert (partial.reported_input_tokens,partial.reported_output_tokens)==(30,20)
    run.input_tokens=30;run.output_tokens=20
    assert _run_out(run, passes).usage_status=='REPORTED'
