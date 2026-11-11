"""Read-only product captures, independent gold, and reusable regression reports."""
import json
from copy import deepcopy
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.evals import declaration_knowledge as harness
from app.core import storage
from app.evals.recorded_import import RecordedCaptureUnavailable, compare_recorded_reports, load_recorded_report, validate_recorded_report
from app.models.declaration_candidate import DeclarationCandidate, DeclarationCandidateEvidence, DeclarationCandidateIssue
from app.models.declaration_import import DeclarationExtractionRun

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / 'fixtures/declaration_eval_cases.json'


@pytest.fixture
def seed_data(seed_data):
    """Keep real worker FIFO claims isolated from other modules' queued jobs."""
    import psycopg2
    from app.core.security import hash_password
    from tests.conftest import MIGRATOR_SYNC_URL

    org, actor = uuid.uuid4(), uuid.uuid4()
    with psycopg2.connect(MIGRATOR_SYNC_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute('INSERT INTO organization (id,name) VALUES (%s,%s)', (str(org), 'Recorded evaluation synthetic tenant'))
            cursor.execute('INSERT INTO "user" (id,organization_id,email,password_hash,display_name) VALUES (%s,%s,%s,%s,%s)',
                (str(actor), str(org), f'{actor}@recorded.invalid', hash_password('synthetic-test-password'), 'Recorded evaluation actor'))
            cursor.execute('INSERT INTO membership (id,organization_id,user_id,role) VALUES (%s,%s,%s,%s)',
                (str(uuid.uuid4()), str(org), str(actor), 'OWNER_ADMIN'))
    return seed_data | {'org_a_id': org, 'user_a_admin_id': actor,
                        'recorded_parent_org_id': seed_data['org_a_id'], 'recorded_parent_actor_id': seed_data['user_a_admin_id']}


@pytest.fixture
def recorded_store(tmp_path):
    previous = storage._store
    storage.set_store(storage.LocalFsDocumentStore(tmp_path / 'documents'))
    try:
        yield storage.get_store()
    finally:
        storage._store = previous


@pytest.fixture
def recorded_gold():
    return json.loads(FIXTURES.read_text())['recorded_import_cases'][0]


async def new_uploaded_draft(client):
    response = await client.post('/api/declaration-imports', json={'mode': 'NEW_PROPERTY'})
    assert response.status_code == 201, response.text
    job = uuid.UUID(response.json()['id'])
    data = (ROOT / 'docs/acceptance/data/teilungserklaerung-lindenallee-12.pdf').read_bytes()
    response = await client.post(f'/api/declaration-imports/{job}/documents',
        files={'file': ('synthetic.pdf', data, 'application/pdf')}, data={'document_role': 'DECLARATION', 'sequence_no': '1'})
    assert response.status_code == 201, response.text
    return job


async def test_new_draft_same_known_pdf_is_eligible_without_provider_calls(client_a_admin, configured_ai, seed_data, recorded_store, recorded_gold, monkeypatch, migrator_session):
    import openai

    def forbidden_provider(*args, **kwargs):
        raise AssertionError('recorded capture must never construct a provider')

    monkeypatch.setattr(openai, 'AsyncOpenAI', forbidden_provider)
    first = await new_uploaded_draft(client_a_admin)
    started = await client_a_admin.post(f'/api/declaration-imports/{first}/start', headers={'Idempotency-Key': str(uuid.uuid4())})
    assert started.status_code == 200
    await migrator_session.execute(text("UPDATE declaration_import SET status='FAILED',attempt_count=1,error_code='PROVIDER_SCHEMA_FAILURE' WHERE id=:job"), {'job': first})
    migrator_session.add(DeclarationExtractionRun(organization_id=seed_data['org_a_id'], import_id=first, status='FAILED', provider='OpenAiDeclarationProvider',
        model_name='gpt-5.6-luna', prompt_version='declaration-v1', schema_version='2', harness_version='3', error_code='PROVIDER_SCHEMA_FAILURE'))
    await migrator_session.commit()
    second = await new_uploaded_draft(client_a_admin)
    assert first != second
    report = await harness.capture_recorded_import(
        organization_id=seed_data['org_a_id'], actor_id=seed_data['user_a_admin_id'],
        import_id=second, gold=recorded_gold, eligibility_only=True,
    )
    assert report['status'] == 'ELIGIBLE_NEW_DRAFT'
    assert report['input_digest'] == recorded_gold['input_digest']


@pytest_asyncio.fixture
async def recorded_run(client_a_admin, configured_ai, seed_data, recorded_store, recorded_queue_competitor, request):
    """Real queue/worker/pipeline/SDK serialization, with HTTP entirely mocked."""
    import httpx2
    from openai import AsyncOpenAI
    from app.declarations.openai_provider import OpenAiDeclarationProvider
    from app.declarations.pipeline import DeclarationPipeline
    from app.workers.declaration_import import DeclarationImportWorker
    from tests.bdd.steps.browser_declaration import fictional_provider
    from tests.test_declaration_openai_provider import FixedTokenEncoder, _sdk_response

    job = await new_uploaded_draft(client_a_admin)
    documents = (await client_a_admin.get(f'/api/declaration-imports/{job}/documents')).json()
    fake = fictional_provider(documents[0]['id'])
    outputs = {'DocumentInventoryOutput': fake._inventory_output, 'CoreExtractionOutput': fake._core_output,
               'RuleExtractionOutput': fake._rules_output, 'ReconciliationOutput': fake._reconciliation_output,
               'TargetedRecheckOutput': fake._recheck_output}
    requests = []

    def respond(http_request):
        wire = json.loads(http_request.content)
        requests.append(wire)
        payload = outputs[wire['text']['format']['name']].model_dump(mode='json')
        if getattr(request, 'param', None) == 'schema_failure' and wire['text']['format']['name'] == 'CoreExtractionOutput':
            payload['facts'][0]['field'] = 'synthetic_unsupported_core_field'
        return httpx2.Response(200, json=_sdk_response(payload, wire['model']))

    started = await client_a_admin.post(f'/api/declaration-imports/{job}/start', headers={'Idempotency-Key': str(uuid.uuid4())})
    assert started.status_code == 200, started.text
    async with AsyncOpenAI(api_key='synthetic-sdk-key', base_url='https://api.openai.com/v1', max_retries=0,
                           http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(respond))) as client:
        provider = OpenAiDeclarationProvider(client=client, token_encoder=FixedTokenEncoder(1000))
        worker = DeclarationImportWorker(DeclarationPipeline(provider, store=recorded_store))
        result = await worker.run_once(organization_id=seed_data['org_a_id'])
        assert result.import_id == job, 'worker claimed a different import in the shared test organization'
    state = await client_a_admin.get(f'/api/declaration-imports/{job}')
    if getattr(request, 'param', None) == 'schema_failure':
        assert state.json()['status'] == 'FAILED', state.text
        assert state.json()['error_code'] == 'PROVIDER_SCHEMA_FAILURE'
        assert len(requests) == 2
    else:
        assert state.json()['status'] == 'NEEDS_REVIEW', state.text
        assert state.json()['attempt_count'] >= 4
        assert len(requests) >= 4
    return job


async def capture(job, seed, gold, **kwargs):
    return await harness.capture_recorded_import(organization_id=seed['org_a_id'], actor_id=seed['user_a_admin_id'], import_id=job, gold=gold, **kwargs)


def outcome(report, stage, assertion):
    return next(row['status'] for row in report['assertions'] if row['stage'] == stage and row['assertion'] == assertion)


async def test_actual_multipass_pipeline_snapshot_scores_original_units_kinds_and_shares(recorded_run, seed_data, recorded_gold):
    report = await capture(recorded_run, seed_data, recorded_gold)
    assert report['source']['provider_attempt_count'] >= 4
    assert report['provenance']['provider'] == 'OpenAiDeclarationProvider'
    assert all(outcome(report, stage, 'schema') == 'PASS' for stage in ('inventory', 'extract_core', 'extract_rules', 'reconcile'))
    assert all(outcome(report, stage, 'semantics') == 'UNAVAILABLE' for stage in ('inventory', 'extract_core', 'extract_rules', 'reconcile', 'recheck'))
    required = {'terminal_outcome', 'candidate_shape', 'core_facts', 'unit_set', 'unit_kinds', 'mea_exact', 'evidence', 'rules_absent'}
    problems = [row for row in report['assertions'] if row['stage'] == 'final_output' and row['assertion'] in required and row['status'] != 'PASS']
    assert not problems, problems
    assert outcome(report, 'final_output', 'rule_effects') == 'NOT_APPLICABLE'
    assert report['accounting']['usage_status'] == 'REPORTED'
    assert 'Lindenallee' not in json.dumps(report)
    assert recorded_gold['unit_numbers'] == ['1', '2', 'G1']


@pytest.mark.parametrize('mutation,assertion', [('number', 'unit_set'), ('share', 'mea_exact'), ('kind', 'unit_kinds'), ('schema', 'candidate_shape'), ('evidence', 'evidence'), ('rule', 'rules_absent')])
async def test_bad_model_outputs_fail_localized_assertions_without_leaking_values(recorded_run, seed_data, recorded_gold, migrator_session, mutation, assertion):
    candidate = await migrator_session.scalar(select(DeclarationCandidate).where(DeclarationCandidate.import_id == recorded_run, DeclarationCandidate.candidate_type == 'UNIT_CREATE').order_by(DeclarationCandidate.stable_key))
    value = deepcopy(candidate.proposed_value_json)
    if mutation == 'number':
        value['number']['value'] = 'private-wrong-unit'
    elif mutation == 'share':
        value['mea']['numerator'] += 1
    elif mutation == 'kind':
        value['usage_type']['value'] = 'private-wrong-kind'
    elif mutation == 'schema':
        value['mea'] = {'kind': 'TEXT', 'value': 'private-bad-mea'}
    elif mutation == 'evidence':
        evidence = await migrator_session.scalar(select(DeclarationCandidateEvidence).where(DeclarationCandidateEvidence.candidate_id == candidate.id))
        evidence.quote = 'private-unsupported-citation'
    else:
        migrator_session.add(DeclarationCandidate(organization_id=seed_data['org_a_id'], import_id=recorded_run,
            stable_key='rule:hallucinated', candidate_type='RULE', subject_key='rule:hallucinated', operation='CREATE',
            proposed_value_json={'title': 'private-hallucinated-rule'}, confidence=1, risk_class='CRITICAL'))
    candidate.proposed_value_json = value
    await migrator_session.commit()
    report = await capture(recorded_run, seed_data, recorded_gold)
    assert outcome(report, 'final_output', assertion) == 'FAIL'
    assert 'private-' not in json.dumps(report)


@pytest.mark.parametrize('actor', ['missing', 'foreign', 'disabled'])
async def test_capture_refuses_nonexistent_foreign_and_disabled_actors(recorded_run, seed_data, recorded_gold, migrator_session, actor):
    user = seed_data['user_a_admin_id']
    if actor == 'missing':
        user = uuid.uuid4()
    elif actor == 'foreign':
        user = seed_data['user_b_admin_id']
    else:
        await migrator_session.execute(text('UPDATE "user" SET is_active=false WHERE id=:id'), {'id': user})
        await migrator_session.commit()
    try:
        with pytest.raises(RecordedCaptureUnavailable, match='IMPORT_NOT_VISIBLE'):
            await harness.capture_recorded_import(organization_id=seed_data['org_a_id'], actor_id=user, import_id=recorded_run, gold=recorded_gold)
    finally:
        if actor == 'disabled':
            async with async_sessionmaker(migrator_session.bind)() as cleanup:
                async with cleanup.begin():
                    await cleanup.execute(text('UPDATE "user" SET is_active=true WHERE id=:id'), {'id': user})


async def test_foreign_import_and_wrong_input_are_not_captured(recorded_run, seed_data, recorded_gold):
    with pytest.raises(RecordedCaptureUnavailable, match='IMPORT_NOT_VISIBLE'):
        await harness.capture_recorded_import(organization_id=seed_data['org_b_id'], actor_id=seed_data['user_b_admin_id'], import_id=recorded_run, gold=recorded_gold)
    with pytest.raises(RecordedCaptureUnavailable, match='SYNTHETIC_INPUT_MISMATCH'):
        await capture(recorded_run, seed_data, recorded_gold | {'input_digest': 'b' * 64})


async def test_frozen_report_remains_reusable_after_human_review_but_later_capture_is_refused(recorded_run, seed_data, recorded_gold, migrator_session, tmp_path):
    original = await capture(recorded_run, seed_data, recorded_gold)
    saved = tmp_path / 'baseline.json'
    saved.write_text(json.dumps(original))
    candidate = await migrator_session.scalar(select(DeclarationCandidate).where(DeclarationCandidate.import_id == recorded_run).limit(1))
    candidate.review_status, candidate.candidate_version = 'CORRECTED', 2
    candidate.corrected_value_json = deepcopy(candidate.proposed_value_json)
    candidate.reviewed_by, candidate.reviewed_at = seed_data['user_a_admin_id'], datetime.now(timezone.utc)
    await migrator_session.commit()
    with pytest.raises(RecordedCaptureUnavailable, match='UNTOUCHED_MODEL_PROPOSALS_REQUIRED'):
        await capture(recorded_run, seed_data, recorded_gold)
    comparison = compare_recorded_reports(load_recorded_report(saved), original)
    assert comparison['status'] == 'NO_OBSERVED_REGRESSION' and comparison['coverage'] == 'PARTIAL'


async def test_multiple_extraction_attempts_require_a_fresh_import(recorded_run, seed_data, recorded_gold, migrator_session):
    run = await migrator_session.scalar(select(DeclarationExtractionRun).where(DeclarationExtractionRun.import_id == recorded_run))
    migrator_session.add(DeclarationExtractionRun(organization_id=run.organization_id, import_id=run.import_id, status='FAILED', provider=run.provider,
        model_name=run.model_name, prompt_version=run.prompt_version, schema_version=run.schema_version, harness_version=run.harness_version))
    run.status = 'FAILED'
    await migrator_session.execute(text("UPDATE declaration_import SET status='FAILED' WHERE id=:job"), {'job': recorded_run})
    await migrator_session.commit()
    with pytest.raises(RecordedCaptureUnavailable, match='EXACTLY_ONE_EXTRACTION_RUN_REQUIRED'):
        await capture(recorded_run, seed_data, recorded_gold)
    with pytest.raises(RecordedCaptureUnavailable, match='NEW_UNUSED_DRAFT_REQUIRED'):
        await capture(recorded_run, seed_data, recorded_gold, eligibility_only=True)


async def test_saved_report_validation_refuses_raw_actual_missing_assertions_and_forged_stage_pass(recorded_run, seed_data, recorded_gold):
    report = await capture(recorded_run, seed_data, recorded_gold)
    invalid = [dict(actual={'units': []}), report | {'actual': {'private-raw-body': 'private-body'}}]
    missing = deepcopy(report)
    missing['assertions'].pop()
    invalid.append(missing)
    forged = deepcopy(report)
    next(row for row in forged['assertions'] if row['stage'] == 'recheck' and row['assertion'] == 'schema')['status'] = 'PASS'
    invalid.append(forged)
    for value in invalid:
        with pytest.raises(RecordedCaptureUnavailable):
            validate_recorded_report(value)


async def test_comparison_localizes_regressions_and_exposes_contract_identity_changes(recorded_run, seed_data, recorded_gold):
    baseline = await capture(recorded_run, seed_data, recorded_gold)
    candidate = deepcopy(baseline)
    next(row for row in candidate['assertions'] if row['stage'] == 'final_output' and row['assertion'] == 'mea_exact')['status'] = 'FAIL'
    candidate['provenance']['passes'][0]['returned_model_id'] = 'gpt-5.6-luna-snapshot'
    candidate['provenance']['passes'][0]['reasoning_effort'] = 'low'
    comparison = compare_recorded_reports(baseline, candidate)
    assert comparison['regressions'] == [{'stage': 'final_output', 'assertion': 'mea_exact'}]
    assert {'returned_model_id', 'reasoning_effort'} <= set(comparison['contract_changes'])
    with pytest.raises(RecordedCaptureUnavailable, match='INCOMPATIBLE_RECORDED_CASES'):
        compare_recorded_reports(baseline, candidate | {'gold_digest': 'b' * 64})


async def test_recorded_cli_collects_database_rows_and_reports_fixed_preflight_refusals(recorded_run, seed_data, recorded_gold, recorded_store, migrator_session, tmp_path):
    env = {k: v for k, v in os.environ.items() if 'API_KEY' not in k.upper() and not k.startswith(('OPENAI_', 'HV_OPENAI_')) and k != 'USER' and k not in {'HV_DECLARATION_LIVE_EVAL', 'HV_DECLARATION_REAL_PDF_MANIFEST', 'HV_DECLARATION_RUNTIME_IMAGE'}}
    env['HV_DOCUMENT_STORAGE_ROOT'] = str(recorded_store._root)
    command = [sys.executable, '-m', 'app.evals.declaration_knowledge', '--provider', 'recorded', '--fixtures', str(FIXTURES),
               '--organization-id', str(seed_data['org_a_id']), '--actor-id', str(seed_data['user_a_admin_id']), '--import-id', str(recorded_run), '--case-id', recorded_gold['id']]
    command[command.index(str(FIXTURES))] = '/dev/stdin'
    approved_gold = json.dumps({'recorded_import_cases': [recorded_gold]})
    result = subprocess.run(command, cwd=ROOT / 'backend', env=env, input=approved_gold, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)['capture_kind'] == 'recorded_product_import'
    baseline = tmp_path / 'baseline.json'
    baseline.write_text(result.stdout)
    compared = subprocess.run(command + ['--baseline-report', str(baseline)], cwd=ROOT / 'backend', env=env, input=approved_gold, capture_output=True, text=True, timeout=60)
    assert compared.returncode == 0, compared.stdout + compared.stderr
    assert json.loads(compared.stdout)['comparison']['coverage'] == 'PARTIAL'
    refused = subprocess.run(command + ['--check-eligibility'], cwd=ROOT / 'backend', env=env, input=approved_gold, capture_output=True, text=True, timeout=60)
    assert refused.returncode == 2
    assert json.loads(refused.stdout)['code'] == 'NEW_UNUSED_DRAFT_REQUIRED'
    await migrator_session.execute(text("UPDATE declaration_extraction_run SET status='FAILED' WHERE import_id=:job"), {'job': recorded_run})
    await migrator_session.execute(text("UPDATE declaration_import SET status='FAILED' WHERE id=:job"), {'job': recorded_run})
    await migrator_session.commit()
    failed = subprocess.run(command, cwd=ROOT / 'backend', env=env, input=approved_gold, capture_output=True, text=True, timeout=60)
    assert failed.returncode == 1, failed.stdout + failed.stderr
    assert outcome(json.loads(failed.stdout), 'final_output', 'terminal_outcome') == 'FAIL'


async def test_failed_terminal_run_cannot_pass_after_successful_provider_stages(recorded_run, seed_data, recorded_gold, migrator_session):
    await migrator_session.execute(text("UPDATE declaration_extraction_run SET status='FAILED',error_code='CANDIDATE_PUBLICATION_FAILED' WHERE import_id=:job"), {'job': recorded_run})
    await migrator_session.execute(text("UPDATE declaration_import SET status='FAILED',error_code='CANDIDATE_PUBLICATION_FAILED' WHERE id=:job"), {'job': recorded_run})
    await migrator_session.commit()
    report = await capture(recorded_run, seed_data, recorded_gold)
    assert outcome(report, 'reconcile', 'schema') == 'PASS'
    assert outcome(report, 'final_output', 'terminal_outcome') == 'FAIL'
    assert outcome(report, 'final_output', 'core_facts') == 'NOT_REACHED'
    assert report['accounting']['usage_status'] == 'UNKNOWN'


async def test_unknown_usage_reservations_are_separate_from_provider_reported_totals(recorded_run, seed_data, recorded_gold, migrator_session):
    await migrator_session.execute(text('UPDATE declaration_extraction_run SET input_tokens=input_tokens+99, output_tokens=output_tokens+12000, estimated_cost_usd=estimated_cost_usd+0.10 WHERE import_id=:job'), {'job': recorded_run})
    await migrator_session.commit()
    report = await capture(recorded_run, seed_data, recorded_gold)
    assert report['accounting']['usage_status'] == 'UNKNOWN'
    assert report['accounting']['job_accounting']['output_tokens'] == report['accounting']['provider_reported']['output_tokens'] + 12000
    assert outcome(report, 'accounting', 'usage_complete') == 'UNAVAILABLE'
    forged = deepcopy(report)
    forged['accounting']['usage_status'] = 'REPORTED'
    with pytest.raises(RecordedCaptureUnavailable):
        validate_recorded_report(forged)


async def test_run_pass_contract_mismatch_fails_provenance_without_relabeling_model_success(recorded_run, seed_data, recorded_gold, migrator_session):
    await migrator_session.execute(text("UPDATE declaration_extraction_run SET prompt_version='declaration-v999' WHERE import_id=:job"), {'job': recorded_run})
    await migrator_session.commit()
    report = await capture(recorded_run, seed_data, recorded_gold)
    assert outcome(report, 'provenance', 'contracts_consistent') == 'FAIL'


async def test_missing_pass_records_cannot_be_frozen_as_complete_stage_evidence(recorded_run, seed_data, recorded_gold):
    report = await capture(recorded_run, seed_data, recorded_gold)
    report['provenance']['passes'].pop(1)
    with pytest.raises(RecordedCaptureUnavailable):
        validate_recorded_report(report)


@pytest.mark.parametrize('recorded_run', ['schema_failure'], indirect=True)
async def test_sdk_schema_failure_preserves_stage_failure_and_unknown_usage(recorded_run, seed_data, recorded_gold):
    report = await capture(recorded_run, seed_data, recorded_gold)
    assert outcome(report, 'inventory', 'schema') == 'PASS'
    assert outcome(report, 'extract_core', 'execution') == 'FAIL'
    assert outcome(report, 'extract_core', 'schema') == 'FAIL'
    assert outcome(report, 'extract_rules', 'execution') == 'NOT_REACHED'
    assert outcome(report, 'final_output', 'terminal_outcome') == 'FAIL'
    assert outcome(report, 'final_output', 'core_facts') == 'NOT_REACHED'
    assert report['accounting']['usage_status'] == 'UNKNOWN'
    assert 'synthetic_unsupported_core_field' not in json.dumps(report)


@pytest.mark.parametrize('origin,needs_clarification,expected', [('rule', True, 'FAIL'), ('additional-rule', True, 'FAIL'), ('additional-rule', False, 'PASS')])
async def test_recorded_readiness_uses_rule_flags_without_treating_all_warnings_as_blockers(recorded_run, seed_data, recorded_gold, migrator_session, origin, needs_clarification, expected):
    candidate = DeclarationCandidate(organization_id=seed_data['org_a_id'], import_id=recorded_run,
        stable_key=origin + ':synthetic', candidate_type='RULE', subject_key='synthetic', operation='CREATE',
        proposed_value_json=_synthetic_note(needs_clarification=needs_clarification),
        confidence=1, risk_class='CRITICAL', review_status='PENDING', candidate_version=1)
    migrator_session.add(candidate)
    await migrator_session.flush()
    codes = ['PROVIDER_UNRESOLVED_ISSUE']
    if needs_clarification:
        codes.append('ADDITIONAL_FACT_NEEDS_CLARIFICATION' if origin == 'additional-rule' else 'RULE_NEEDS_CLARIFICATION')
    for code in codes:
        migrator_session.add(DeclarationCandidateIssue(organization_id=seed_data['org_a_id'], import_id=recorded_run,
            candidate_id=candidate.id, code=code, severity='WARNING', message='Synthetic clarification required'))
    await migrator_session.commit()
    report = await capture(recorded_run, seed_data, recorded_gold)
    assert outcome(report, 'final_output', 'blockers') == expected
    assert candidate.review_status == 'PENDING' and candidate.corrected_value_json is None


async def test_recorded_scorer_version_refuses_old_scores_in_either_comparison_direction(recorded_run, seed_data, recorded_gold, tmp_path):
    report = await capture(recorded_run, seed_data, recorded_gold)
    assert report['format_version'] == 2
    assert report['scorer_version'] == 'recorded-final-output-v2'
    previous = deepcopy(report)
    previous['format_version'] = 1
    previous.pop('scorer_version')
    saved = tmp_path / 'old-score.json'
    saved.write_text(json.dumps(previous))
    for first, second in ((report, previous), (previous, report)):
        with pytest.raises(RecordedCaptureUnavailable, match='INCOMPATIBLE_RECORDED_SCORER'):
            compare_recorded_reports(first, second)
    with pytest.raises(RecordedCaptureUnavailable, match='INCOMPATIBLE_RECORDED_SCORER'):
        load_recorded_report(saved)
    assert json.loads(saved.read_text()) == previous


@pytest.mark.parametrize('origin,expected', [('rule', 'FAIL'), ('additional-rule', 'PASS')])
async def test_rule_absence_scores_original_rule_origin_not_additional_note_storage(recorded_run, seed_data, recorded_gold, migrator_session, origin, expected):
    evidence = await migrator_session.scalar(select(DeclarationCandidateEvidence).where(DeclarationCandidateEvidence.import_id == recorded_run).limit(1))
    candidate = DeclarationCandidate(organization_id=seed_data['org_a_id'], import_id=recorded_run,
        stable_key=origin + ':synthetic', candidate_type='RULE', subject_key='synthetic', operation='CREATE',
        proposed_value_json=_synthetic_note(needs_clarification=False), confidence=1, risk_class='CRITICAL', review_status='PENDING', candidate_version=1)
    migrator_session.add(candidate)
    await migrator_session.flush()
    migrator_session.add(DeclarationCandidateEvidence(organization_id=seed_data['org_a_id'], import_id=recorded_run,
        candidate_id=candidate.id, document_id=evidence.document_id, pdf_page=evidence.pdf_page, quote=evidence.quote))
    await migrator_session.commit()
    report = await capture(recorded_run, seed_data, recorded_gold)
    assert outcome(report, 'final_output', 'rules_absent') == expected
    assert outcome(report, 'final_output', 'blockers') == 'PASS'
    assert outcome(report, 'final_output', 'evidence') == 'PASS'


def _synthetic_note(*, needs_clarification):
    from app.declarations.schemas import RulePayload
    return RulePayload(subject_key='synthetic', title='Synthetic note', topic='sonstige_relevante_information',
        summary='Synthetic source-backed note', scope={}, effect={'administrative_note': 'Synthetic source-backed note'},
        proposed_status='NEEDS_CLARIFICATION' if needs_clarification else 'EFFECTIVE',
        clarification_needed=needs_clarification, confidence=1,
        evidence=[{'document_key': 'synthetic', 'pdf_page': 1, 'quote': 'Synthetic note'}]).model_dump(
            mode='json', exclude={'subject_key', 'evidence', 'confidence'})


@pytest_asyncio.fixture
async def recorded_queue_competitor(request, seed_data, configured_ai, recorded_store):
    if not getattr(request, 'param', False):
        yield None
        return
    from types import SimpleNamespace
    from app.core.config import settings
    from tests.conftest import _api_test_client, _make_session_cookie
    org = seed_data.get('recorded_parent_org_id', seed_data['org_a_id'])
    actor = seed_data.get('recorded_parent_actor_id', seed_data['user_a_admin_id'])
    configured_ai(org, model='gpt-5.6-luna', import_enabled=True, qa_enabled=True, monthly_limit_usd=None)
    async with _api_test_client(cookies={settings.session_cookie_name: _make_session_cookie(actor, org)}) as client:
        job = await new_uploaded_draft(client)
        started = await client.post(f'/api/declaration-imports/{job}/start', headers={'Idempotency-Key': str(uuid.uuid4())})
        assert started.status_code == 200, started.text
        try:
            yield SimpleNamespace(id=job, client=client)
        finally:
            cancelled = await client.post(f'/api/declaration-imports/{job}/cancel')
            assert cancelled.status_code == 200 and cancelled.json()['status'] == 'CANCELLED', cancelled.text


@pytest.mark.parametrize('recorded_queue_competitor', [True], indirect=True)
async def test_recorded_fixture_processes_its_target_without_claiming_an_earlier_shared_org_job(recorded_run, recorded_queue_competitor):
    assert recorded_run != recorded_queue_competitor.id
    untouched = await recorded_queue_competitor.client.get(f'/api/declaration-imports/{recorded_queue_competitor.id}')
    assert untouched.status_code == 200
    assert untouched.json()['status'] == 'QUEUED'
    assert untouched.json()['attempt_count'] == 0
