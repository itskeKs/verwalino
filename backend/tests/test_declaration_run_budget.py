"""Run price classification uses immutable provider provenance."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4
from app.api.declaration_imports import _run_out


def test_custom_run_named_like_openai_is_unpriced_and_keeps_tokens_and_runtime():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    run = SimpleNamespace(id=uuid4(), provider="CompatibleDeclarationProvider", model_name="gpt-5.6-luna", document_model_name=None,
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
    run=SimpleNamespace(id=uuid4(),provider='CompatibleDeclarationProvider',model_name='synthetic',document_model_name=None,
        input_tokens=10000,output_tokens=12000,estimated_cost_usd=Decimal('0'),started_at=start,
        completed_at=start if status!='RUNNING' else None,status=status)
    result=_run_out(run, [])
    assert result.usage_status=='RESERVED'
    assert result.reported_input_tokens is None and result.reported_output_tokens is None
    assert result.input_tokens==10000 and result.output_tokens==12000


def test_zero_ledger_without_usage_is_unknown_not_measured_zero():
    start=datetime.now(timezone.utc)
    run=SimpleNamespace(id=uuid4(),provider='CompatibleDeclarationProvider',model_name='synthetic',document_model_name=None,
        input_tokens=0,output_tokens=0,estimated_cost_usd=Decimal('0'),started_at=start,completed_at=start,status='FAILED')
    assert _run_out(run, []).usage_status=='UNKNOWN'


def test_reported_schema_failure_usage_is_preserved_but_unknown_remainder_is_reserved():
    start=datetime.now(timezone.utc)
    run=SimpleNamespace(id=uuid4(),provider='CompatibleDeclarationProvider',model_name='synthetic',document_model_name=None,
        input_tokens=100,output_tokens=200,estimated_cost_usd=Decimal('0'),started_at=start,completed_at=start,status='FAILED')
    passes=[{'pass_id':'inventory','estimated_cost_usd':'0','input_tokens':30,'output_tokens':20,'failure_class':'PROVIDER_SCHEMA_FAILURE'}]
    partial=_run_out(run, passes)
    assert partial.usage_status=='PARTIAL'
    assert (partial.reported_input_tokens,partial.reported_output_tokens)==(30,20)
    run.input_tokens=30;run.output_tokens=20
    assert _run_out(run, passes).usage_status=='REPORTED'


# ---- the two stages of a run: reading and extraction (issue #499) ------------------------------------------------

READING, EXTRACTION = 'gpt-5.4-mini', 'gpt-5.6-luna'


def _entry(pass_id, tokens, *, cost='0', failure=None):
    return {'pass_id': pass_id, 'input_tokens': tokens[0], 'output_tokens': tokens[1], 'estimated_cost_usd': cost, 'failure_class': failure}


def _stage_run(tokens, cost, *, provider='OpenAiDeclarationProvider', document_model=READING, closed=True):
    start = datetime.now(timezone.utc)
    return SimpleNamespace(id=uuid4(), provider=provider, model_name=EXTRACTION, document_model_name=document_model,
        input_tokens=tokens[0], output_tokens=tokens[1], estimated_cost_usd=Decimal(cost), started_at=start,
        completed_at=start + timedelta(seconds=5) if closed else None, status='SUCCEEDED' if closed else 'RUNNING')


def _stages(result):
    return [(stage.stage, stage.model, stage.usage_status, stage.reported_input_tokens, stage.reported_output_tokens,
             stage.input_tokens, stage.output_tokens, stage.estimated_cost_usd) for stage in result.stages]


def test_each_stage_reports_its_own_model_tokens_and_cost_and_the_total_stays_the_sum():
    passes = [_entry('read_page', (100, 20), cost='0.000165'), _entry('read_page', (80, 10), cost='0.000105'),
              _entry('inventory', (300, 50), cost='0.000135'), _entry('extract_core', (400, 60), cost='0.000152')]
    run = _stage_run((880, 140), '0.000557')
    result = _run_out(run, passes)
    assert _stages(result) == [
        ('READING', READING, 'REPORTED', 180, 30, 180, 30, Decimal('0.000270')),
        ('EXTRACTION', EXTRACTION, 'REPORTED', 700, 110, 700, 110, Decimal('0.000287')),
    ]
    assert (result.usage_status, result.input_tokens, result.output_tokens) == ('REPORTED', 880, 140)
    assert result.estimated_cost_usd == Decimal('0.000557')


def test_a_run_that_read_no_page_has_no_reading_stage():
    # A text-layer document: the run carries a reading model, but never made a reading call.
    result = _run_out(_stage_run((300, 50), '0.000135'), [_entry('inventory', (300, 50), cost='0.000135')])
    assert [stage.stage for stage in result.stages] == ['EXTRACTION']


def test_a_run_without_any_consumption_has_no_stage_block():
    result = _run_out(_stage_run((0, 0), '0'), [])
    assert result.stages == [] and result.usage_status == 'UNKNOWN'


def test_a_run_that_failed_in_reading_shows_what_the_failed_call_consumed_and_no_extraction_stage():
    result = _run_out(_stage_run((40, 3), '0.000002'), [_entry('read_page', (40, 3), cost='0.000002', failure='PROVIDER_REFUSAL')])
    assert _stages(result) == [('READING', READING, 'REPORTED', 40, 3, 40, 3, Decimal('0.000002'))]


def test_a_reading_call_with_unknown_usage_shows_its_reservation_on_the_reading_stage():
    # The failed call reported nothing; its reservation stays in the run's totals and belongs to the reading stage.
    result = _run_out(_stage_run((17, 12000), '0.054'), [_entry('read_page', (0, 0), failure='PROVIDER_TIMEOUT')])
    assert _stages(result) == [('READING', READING, 'RESERVED', None, None, 17, 12000, Decimal('0.054000'))]
    assert result.usage_status == 'RESERVED'


def test_a_run_that_failed_in_extraction_keeps_the_finished_reading_and_the_reserved_extraction_call():
    passes = [_entry('read_page', (100, 20), cost='0.000165'), _entry('inventory', (0, 0), failure='PROVIDER_TIMEOUT')]
    result = _run_out(_stage_run((600, 12020), '0.0145'), passes)
    assert _stages(result) == [
        ('READING', READING, 'REPORTED', 100, 20, 100, 20, Decimal('0.000165')),
        ('EXTRACTION', EXTRACTION, 'RESERVED', None, None, 500, 12000, Decimal('0.014335')),
    ]


def test_partial_provider_usage_of_a_stage_is_kept_apart_from_its_reserved_remainder():
    passes = [_entry('read_page', (100, 20), cost='0.000165'), _entry('inventory', (30, 5), cost='0.00001', failure='PROVIDER_SCHEMA_FAILURE')]
    result = _run_out(_stage_run((600, 12020), '0.0145'), passes)
    assert _stages(result)[1] == ('EXTRACTION', EXTRACTION, 'PARTIAL', 30, 5, 500, 12000, Decimal('0.014335'))


@pytest.mark.parametrize(('import_status', 'stage'), [('PREFLIGHT', 'READING'), ('EXTRACTING', 'EXTRACTION'), ('RECONCILING', 'EXTRACTION')])
def test_the_reservation_of_a_call_in_flight_belongs_to_the_stage_the_import_is_in(import_status, stage):
    # The reading is finished and settled; a live run holds one more reservation that no ledger entry reports yet.
    passes = [_entry('read_page', (100, 20), cost='0.000165')] if import_status != 'PREFLIGHT' else []
    run = _stage_run((100 + 500, 20 + 12000), '0.0145', closed=False)
    result = _run_out(run, passes, import_status)
    held = [item for item in result.stages if item.usage_status in {'RESERVED', 'PARTIAL'}]
    assert [item.stage for item in held] == [stage]


def test_an_unpriced_stage_shows_no_price_and_makes_the_total_unpriced_too():
    # The reading model has no price: its cost in the ledger is zero and must not read as a known zero.
    run = _stage_run((880, 140), '0.000135', document_model='unpriced-vision-model')
    passes = [_entry('read_page', (180, 30)), _entry('inventory', (700, 110), cost='0.000135')]
    result = _run_out(run, passes)
    assert [(stage.stage, stage.estimated_cost_usd) for stage in result.stages] == [('READING', None), ('EXTRACTION', Decimal('0.000135'))]
    assert result.estimated_cost_usd is None


def test_an_unpriced_model_that_made_no_call_does_not_hide_the_known_total():
    run = _stage_run((300, 50), '0.000135', document_model='unpriced-vision-model')
    result = _run_out(run, [_entry('inventory', (300, 50), cost='0.000135')])
    assert result.estimated_cost_usd == Decimal('0.000135'), 'the unpriced model made no call'


def test_stages_of_a_custom_endpoint_are_unpriced_even_for_a_known_model_name():
    result = _run_out(_stage_run((880, 140), '0', provider='CompatibleDeclarationProvider'),
                      [_entry('read_page', (180, 30)), _entry('inventory', (700, 110))])
    assert [stage.estimated_cost_usd for stage in result.stages] == [None, None]
