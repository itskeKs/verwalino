"""Read-only product-import evaluations; never constructs an extraction provider."""
from collections import Counter
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import re
import uuid

from sqlalchemy import text

from app.core.db import get_session_factory, set_tenant_context
from app.core.storage import get_store, make_storage_key
from app.core.unit_numbers import normalize_unit_number
from app.declarations.evidence import EvidenceTextIndex, validate_evidence_quote
from app.declarations.observability import ProviderPassMetadata
from app.declarations.preflight import PdfPreflight
from app.declarations.provider import PageInput
from app.declarations.schemas import EvidencePayload, IntegerValue, MeaFraction, TextValue
from app.repositories.declaration_evaluation_repo import DeclarationEvaluationRepository, RecordedCaptureUnavailable

_FORMAT_VERSION = 2
_SCORER_VERSION = 'recorded-final-output-v2'

_STAGES = ('inventory', 'extract_core', 'extract_rules', 'reconcile', 'recheck')
_STATUSES = {'PASS', 'FAIL', 'UNAVAILABLE', 'NOT_REACHED', 'NOT_APPLICABLE'}
_ASSERTIONS = {'execution', 'schema', 'semantics', 'contracts_consistent', 'terminal_outcome', 'candidate_shape', 'core_facts', 'unit_set',
               'unit_kinds', 'mea_exact', 'rules_absent', 'rule_effects', 'evidence', 'blockers', 'usage_complete'}
_DIGEST = re.compile(r'^[a-f0-9]{64}$')
_REPORT_KEYS = {'format_version', 'scorer_version', 'capture_kind', 'case_id', 'input_digest', 'gold_digest', 'source', 'provenance', 'assertions', 'accounting'}
CAPTURE_REFUSAL_CODES = frozenset({'READ_ONLY_RUNTIME_REQUIRED', 'IMPORT_NOT_VISIBLE', 'CAPTURE_LIMIT_EXCEEDED',
    'SYNTHETIC_INPUT_MISMATCH', 'INVALID_RECORDED_GOLD', 'NEW_PROPERTY_IMPORT_REQUIRED', 'NEW_UNUSED_DRAFT_REQUIRED',
    'EXACTLY_ONE_EXTRACTION_RUN_REQUIRED', 'EXACTLY_ONE_START_REQUIRED', 'INCOMPATIBLE_RECORDED_SCORER', 'TERMINAL_PRE_REVIEW_IMPORT_REQUIRED',
    'RUN_OUTCOME_MISMATCH', 'UNTOUCHED_MODEL_PROPOSALS_REQUIRED', 'INVALID_RECORDED_REPORT', 'INCOMPATIBLE_RECORDED_CASES'})


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)


def _gold_identity(gold):
    from app.evals.declaration_knowledge import _reject_unsafe_fixture_content, _coerce_fact
    _reject_unsafe_fixture_content(gold)
    if set(gold) != {'id', 'input_digest', 'unit_numbers', 'core_facts', 'rules'} or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', gold['id']):
        raise RecordedCaptureUnavailable('INVALID_RECORDED_GOLD')
    if not _DIGEST.fullmatch(gold['input_digest']) or not 1 <= len(gold['unit_numbers']) <= 100 or len(gold['core_facts']) > 1000 or len(gold['rules']) > 100:
        raise RecordedCaptureUnavailable('INVALID_RECORDED_GOLD')
    for fact in gold['core_facts']:
        _coerce_fact(fact)
    return sha256(_canonical(gold).encode()).hexdigest()


def _safe_pass(value):
    data = dict(value)
    data['estimated_cost_usd'] = Decimal(data['estimated_cost_usd'])
    data['route_reason_codes'] = tuple(data['route_reason_codes'])
    return ProviderPassMetadata.from_values(**data).as_safe_dict()


def _assertion(stage, name, status):
    return {'stage': stage, 'assertion': name, 'status': status}


def _input_bytes(snapshot, gold):
    if len(snapshot.documents) != 1 or snapshot.documents[0].content_sha256 != gold['input_digest']:
        raise RecordedCaptureUnavailable('SYNTHETIC_INPUT_MISMATCH')
    document = snapshot.documents[0]
    key = make_storage_key(str(snapshot.item.organization_id), gold['input_digest'])
    if document.storage_uri != key or not 0 < document.content_byte_size <= 5 * 1024 * 1024:
        raise RecordedCaptureUnavailable('SYNTHETIC_INPUT_MISMATCH')
    data = bytearray()
    for chunk in get_store().iter_chunks(key):
        data.extend(chunk)
        if len(data) > 5 * 1024 * 1024:
            raise RecordedCaptureUnavailable('CAPTURE_LIMIT_EXCEEDED')
    if len(data) != document.content_byte_size or sha256(data).hexdigest() != gold['input_digest']:
        raise RecordedCaptureUnavailable('SYNTHETIC_INPUT_MISMATCH')
    return bytes(data)


def _score_output(snapshot, gold, source):
    from app.evals.declaration_knowledge import CoreFact, evaluate_core_facts
    from app.services.declaration_review_service import _rule_needs_clarification
    # The caller has already rejected every reviewed/corrected proposal. Reuse
    # the preview predicate only on original PENDING values, never human edits.
    expected = [dict(fact) for fact in gold['core_facts']]
    for fact in expected:
        if fact['subject_id'].startswith('unit:'):
            fact['subject_id'] = 'unit:' + normalize_unit_number(fact['subject_id'].split(':', 1)[1])
    facts, numbers, kinds = [], [], []
    shape = True
    try:
        for candidate in snapshot.candidates:
            value = candidate.proposed_value_json
            if candidate.candidate_type == 'UNIT_CREATE':
                number = normalize_unit_number(TextValue.model_validate(value['number']).value)
                numbers.append(number)
                mea = MeaFraction.model_validate(value['mea'])
                kind = TextValue.model_validate(value['usage_type']).value
                kinds.append(CoreFact(f'unit:{number}', 'usage_type', value=kind))
                facts.append(CoreFact(f'unit:{number}', 'mea', numerator=mea.numerator, denominator=mea.denominator))
            elif candidate.candidate_type == 'PROPERTY_FIELD' and candidate.field_key == 'name':
                facts.append(CoreFact('property', 'property_identity', value=TextValue.model_validate(value).value))
            elif candidate.candidate_type == 'PROPERTY_FIELD' and candidate.field_key == 'mea_denominator':
                facts.append(CoreFact('property', 'mea_denominator', value=str(IntegerValue.model_validate(value).value)))
    except (ValueError, TypeError, KeyError):
        shape = False
    facts.extend(kinds)
    facts.append(CoreFact('property', 'unit_count', value=str(len(numbers))))
    metrics = evaluate_core_facts(expected=expected, actual=facts)
    kind_metrics = evaluate_core_facts(expected=[f for f in expected if f['field_key'] == 'usage_type'], actual=kinds)
    assertions = [
        _assertion('final_output', 'candidate_shape', 'PASS' if shape else 'FAIL'),
        _assertion('final_output', 'unit_set', 'PASS' if Counter(numbers) == Counter(normalize_unit_number(value) for value in gold['unit_numbers']) else 'FAIL'),
        _assertion('final_output', 'unit_kinds', 'PASS' if kind_metrics.normalized_f1 == 1 else 'FAIL'),
        _assertion('final_output', 'core_facts', 'PASS' if metrics.normalized_f1 == 1 else 'FAIL'),
        _assertion('final_output', 'mea_exact', 'PASS' if metrics.mea_exact_matches == metrics.mea_expected_count and metrics.mea_denominator_consistency == 1 else 'FAIL'),
        _assertion('final_output', 'blockers', 'FAIL' if any(issue.severity == 'BLOCKER' for issue in snapshot.issues)
                   or any(_rule_needs_clarification(candidate) for candidate in snapshot.candidates) else 'PASS'),
    ]
    rules = [candidate for candidate in snapshot.candidates
             if candidate.candidate_type == 'RULE' and not candidate.stable_key.startswith('additional-rule:')]
    # The known scan independently contains no governance/allocation clause.
    # This tests hallucinated rules, never rule recall on an empty expectation.
    assertions.append(_assertion('final_output', 'rules_absent', 'PASS' if not rules and not gold['rules'] else 'FAIL'))
    assertions.append(_assertion('final_output', 'rule_effects', 'NOT_APPLICABLE' if not gold['rules'] else 'UNAVAILABLE'))
    prepared = PdfPreflight(max_pages=1, max_ocr_pages_per_import=1, deadline_seconds=60).run_bytes(
        source, expected_sha256=gold['input_digest'], expected_size=len(source))
    evidence_status = 'PASS'
    if any(not page.text for page in prepared.pages):
        evidence_status = 'UNAVAILABLE'
    else:
        index = EvidenceTextIndex.from_pages([PageInput(gold['id'], p.page_number, p.text, 1) for p in prepared.pages])
        by_candidate = Counter()
        candidate_ids = {candidate.id for candidate in snapshot.candidates}
        document_id = snapshot.documents[0].id
        for evidence in snapshot.evidence:
            by_candidate[evidence.candidate_id] += 1
            if evidence.candidate_id not in candidate_ids or evidence.document_id != document_id:
                evidence_status = 'FAIL'
                continue
            try:
                citation = EvidencePayload(document_key=gold['id'], pdf_page=evidence.pdf_page, quote=evidence.quote)
                invalid = validate_evidence_quote(citation, index) is not None
            except (ValueError, TypeError):
                invalid = True
            if invalid:
                evidence_status = 'FAIL'
        if any(not by_candidate[candidate.id] for candidate in snapshot.candidates):
            evidence_status = 'FAIL'
    assertions.append(_assertion('final_output', 'evidence', evidence_status))
    return assertions


async def capture_recorded_import(*, organization_id, actor_id, import_id, gold, eligibility_only=False):
    """Collect actual rows once; no caller-supplied observed payload is accepted."""
    gold_digest = _gold_identity(gold)
    async with get_session_factory()() as session:
        async with session.begin():
            await session.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
            await set_tenant_context(session, organization_id)
            await session.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {'actor': str(actor_id)})
            snapshot = await DeclarationEvaluationRepository(session, organization_id).read(import_id, gold['input_digest'])
            source = _input_bytes(snapshot, gold)
            item = snapshot.item
            if item.mode != 'NEW_PROPERTY':
                raise RecordedCaptureUnavailable('NEW_PROPERTY_IMPORT_REQUIRED')
            if eligibility_only:
                if item.status != 'DRAFT' or item.attempt_count != 0 or snapshot.runs or snapshot.queues:
                    raise RecordedCaptureUnavailable('NEW_UNUSED_DRAFT_REQUIRED')
                return {'format_version': 1, 'capture_kind': 'recorded_import_eligibility', 'status': 'ELIGIBLE_NEW_DRAFT',
                        'case_id': gold['id'], 'input_digest': gold['input_digest'], 'gold_digest': gold_digest,
                        'organization_id': str(organization_id), 'import_id': str(import_id)}
            if len(snapshot.runs) != 1:
                raise RecordedCaptureUnavailable('EXACTLY_ONE_EXTRACTION_RUN_REQUIRED')
            if len(snapshot.queues) != 1 or snapshot.queues[0].operation != 'START' or snapshot.queues[0].previous_request_id is not None or snapshot.queues[0].id != item.queue_request_id:
                raise RecordedCaptureUnavailable('EXACTLY_ONE_START_REQUIRED')
            run, = snapshot.runs
            if run.status not in {'SUCCEEDED', 'FAILED'} or item.status not in {'NEEDS_REVIEW', 'FAILED'}:
                raise RecordedCaptureUnavailable('TERMINAL_PRE_REVIEW_IMPORT_REQUIRED')
            if (run.status, item.status) not in {('SUCCEEDED', 'NEEDS_REVIEW'), ('FAILED', 'FAILED')}:
                raise RecordedCaptureUnavailable('RUN_OUTCOME_MISMATCH')
            if snapshot.reviewed or any(c.review_status != 'PENDING' or c.candidate_version != 1 or c.reviewed_at is not None
                                       or c.reviewed_by is not None or c.corrected_value_json is not None for c in snapshot.candidates):
                raise RecordedCaptureUnavailable('UNTOUCHED_MODEL_PROPOSALS_REQUIRED')
            passes = [_safe_pass(row.safe_metadata) for row in snapshot.passes]
            consistent = run.provider in {'OpenAiDeclarationProvider', 'CompatibleDeclarationProvider', 'AnthropicDeclarationProvider'} and bool(passes) and all(
                entry['configured_model'] == run.model_name and entry['prompt_version'] == run.prompt_version
                and entry['schema_version'] == run.schema_version and entry['harness_version'] == run.harness_version for entry in passes)
            if run.status == 'SUCCEEDED':
                consistent = consistent and all(any(p['pass_id'] == stage and p['failure_class'] is None for p in passes) for stage in _STAGES[:4])
            assertions = [_assertion('provenance', 'contracts_consistent', 'PASS' if consistent else 'FAIL')]
            assertions.append(_assertion('final_output', 'terminal_outcome', 'PASS' if run.status == 'SUCCEEDED' else 'FAIL'))
            failed = False
            for stage in _STAGES:
                entries = [entry for entry in passes if entry['pass_id'] == stage]
                last = entries[-1] if entries else None
                execution = ('PASS' if last['failure_class'] is None else 'FAIL') if last else ('NOT_REACHED' if failed else 'UNAVAILABLE')
                schema = ('PASS' if last['failure_class'] is None else 'FAIL' if last['failure_class'] == 'PROVIDER_SCHEMA_FAILURE' else 'UNAVAILABLE') if last else execution
                assertions.extend([_assertion(stage, 'execution', execution), _assertion(stage, 'schema', schema), _assertion(stage, 'semantics', 'UNAVAILABLE')])
                failed = failed or execution == 'FAIL'
            if run.status == 'SUCCEEDED' and snapshot.candidates:
                assertions.extend(_score_output(snapshot, gold, source))
            else:
                assertions.extend(_assertion('final_output', name, 'NOT_REACHED') for name in
                    ('candidate_shape', 'core_facts', 'unit_set', 'unit_kinds', 'mea_exact', 'rules_absent', 'rule_effects', 'evidence', 'blockers'))
            provider_totals = {'input_tokens': sum(entry['input_tokens'] for entry in passes), 'output_tokens': sum(entry['output_tokens'] for entry in passes),
                               'estimated_cost_usd': str(sum((Decimal(entry['estimated_cost_usd']) for entry in passes), Decimal(0)))}
            accounted = {'input_tokens': run.input_tokens, 'output_tokens': run.output_tokens, 'estimated_cost_usd': str(run.estimated_cost_usd)}
            reported = run.status == 'SUCCEEDED' and all(entry['failure_class'] is None for entry in passes) and provider_totals['input_tokens'] == run.input_tokens and provider_totals['output_tokens'] == run.output_tokens and abs(Decimal(provider_totals['estimated_cost_usd']) - run.estimated_cost_usd) <= Decimal('0.000001')
            assertions.append(_assertion('accounting', 'usage_complete', 'PASS' if reported else 'UNAVAILABLE'))
            report = {'format_version': _FORMAT_VERSION, 'scorer_version': _SCORER_VERSION, 'capture_kind': 'recorded_product_import', 'case_id': gold['id'], 'input_digest': gold['input_digest'], 'gold_digest': gold_digest,
                      'source': {'organization_id': str(organization_id), 'import_id': str(import_id), 'run_id': str(run.id),
                                 'queue_request_id': str(item.queue_request_id), 'run_status': run.status, 'import_status': item.status,
                                 'provider_attempt_count': item.attempt_count, 'read_only': True, 'pre_review': True},
                      'provenance': {'provider': run.provider, 'model': run.model_name, 'prompt_version': run.prompt_version, 'schema_version': run.schema_version,
                                     'harness_version': run.harness_version, 'passes': passes},
                      'assertions': assertions, 'accounting': {'job_accounting': accounted, 'provider_reported': provider_totals,
                                                           'usage_status': 'REPORTED' if reported else 'UNKNOWN', 'cost_kind': 'ESTIMATE_NOT_BILLING'}}
            return validate_recorded_report(report)


def validate_recorded_report(report):
    """Accept only frozen sanitized captures, never raw actual/scoring input."""
    if isinstance(report, dict) and report.get('capture_kind') == 'recorded_product_import' and (
        report.get('format_version') != _FORMAT_VERSION or report.get('scorer_version') != _SCORER_VERSION
    ):
        raise RecordedCaptureUnavailable('INCOMPATIBLE_RECORDED_SCORER')
    if not isinstance(report, dict) or set(report) != _REPORT_KEYS or len(_canonical(report).encode()) > 256 * 1024:
        raise RecordedCaptureUnavailable('INVALID_RECORDED_REPORT')
    if report['format_version'] != _FORMAT_VERSION or report['capture_kind'] != 'recorded_product_import' or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', report['case_id']):
        raise RecordedCaptureUnavailable('INVALID_RECORDED_REPORT')
    if any(not _DIGEST.fullmatch(report[key]) for key in ('input_digest', 'gold_digest')):
        raise RecordedCaptureUnavailable('INVALID_RECORDED_REPORT')
    source = report['source']
    if set(source) != {'organization_id', 'import_id', 'run_id', 'queue_request_id', 'run_status', 'import_status', 'provider_attempt_count', 'read_only', 'pre_review'} or source['read_only'] is not True or source['pre_review'] is not True:
        raise RecordedCaptureUnavailable('INVALID_RECORDED_REPORT')
    for key in ('organization_id', 'import_id', 'run_id', 'queue_request_id'):
        uuid.UUID(source[key])
    if (source['run_status'], source['import_status']) not in {('SUCCEEDED', 'NEEDS_REVIEW'), ('FAILED', 'FAILED')} or type(source['provider_attempt_count']) is not int or not 0 <= source['provider_attempt_count'] <= 1_000_000:
        raise RecordedCaptureUnavailable('INVALID_RECORDED_REPORT')
    provenance = report['provenance']
    if set(provenance) != {'provider', 'model', 'prompt_version', 'schema_version', 'harness_version', 'passes'} or provenance['provider'] not in {'OpenAiDeclarationProvider', 'CompatibleDeclarationProvider', 'AnthropicDeclarationProvider'} or not 1 <= len(provenance['passes']) <= 64:
        raise RecordedCaptureUnavailable('INVALID_RECORDED_REPORT')
    for entry in provenance['passes']:
        _safe_pass(entry)
    if [entry['attempt_no'] for entry in provenance['passes']] != list(range(1, len(provenance['passes']) + 1)):
        raise RecordedCaptureUnavailable('INVALID_RECORDED_REPORT')
    first = provenance['passes'][0]
    # Header identities must themselves be validated safe provider metadata.
    _safe_pass(first | {'configured_model': provenance['model'], 'prompt_version': provenance['prompt_version'],
                        'schema_version': provenance['schema_version'], 'harness_version': provenance['harness_version']})
    if not 1 <= len(report['assertions']) <= 64:
        raise RecordedCaptureUnavailable('INVALID_RECORDED_REPORT')
    seen = set()
    for assertion in report['assertions']:
        if set(assertion) != {'stage', 'assertion', 'status'} or assertion['stage'] not in {*_STAGES, 'final_output', 'accounting', 'provenance'} or assertion['assertion'] not in _ASSERTIONS or assertion['status'] not in _STATUSES:
            raise RecordedCaptureUnavailable('INVALID_RECORDED_REPORT')
        identity = (assertion['stage'], assertion['assertion'])
        if identity in seen:
            raise RecordedCaptureUnavailable('INVALID_RECORDED_REPORT')
        seen.add(identity)
    expected = {(stage, assertion) for stage in _STAGES for assertion in ('execution', 'schema', 'semantics')}
    expected |= {('final_output', name) for name in ('terminal_outcome', 'candidate_shape', 'core_facts', 'unit_set', 'unit_kinds', 'mea_exact', 'rules_absent', 'rule_effects', 'evidence', 'blockers')}
    expected |= {('provenance', 'contracts_consistent'), ('accounting', 'usage_complete')}
    if seen != expected:
        raise RecordedCaptureUnavailable('INVALID_RECORDED_REPORT')
    status = {(row['stage'], row['assertion']): row['status'] for row in report['assertions']}
    if any(status[(stage, 'semantics')] != 'UNAVAILABLE' for stage in _STAGES) or status[('final_output', 'terminal_outcome')] != ('PASS' if source['run_status'] == 'SUCCEEDED' else 'FAIL'):
        raise RecordedCaptureUnavailable('INVALID_RECORDED_REPORT')
    consistent = all(p['configured_model'] == provenance['model'] and all(p[k] == provenance[k] for k in ('prompt_version', 'schema_version', 'harness_version')) for p in provenance['passes'])
    if source['run_status'] == 'SUCCEEDED':
        consistent = consistent and all(any(p['pass_id'] == stage and p['failure_class'] is None for p in provenance['passes']) for stage in _STAGES[:4])
    if status[('provenance', 'contracts_consistent')] != ('PASS' if consistent else 'FAIL'):
        raise RecordedCaptureUnavailable('INVALID_RECORDED_REPORT')
    failed = False
    for stage in _STAGES:
        entries = [p for p in provenance['passes'] if p['pass_id'] == stage]
        last = entries[-1] if entries else None
        execution = ('PASS' if last['failure_class'] is None else 'FAIL') if last else ('NOT_REACHED' if failed else 'UNAVAILABLE')
        schema = ('PASS' if last['failure_class'] is None else 'FAIL' if last['failure_class'] == 'PROVIDER_SCHEMA_FAILURE' else 'UNAVAILABLE') if last else execution
        if status[(stage, 'execution')] != execution or status[(stage, 'schema')] != schema:
            raise RecordedCaptureUnavailable('INVALID_RECORDED_REPORT')
        failed = failed or execution == 'FAIL'
    if source['run_status'] == 'FAILED' and any(status[('final_output', name)] != 'NOT_REACHED' for name in ('candidate_shape', 'core_facts', 'unit_set', 'unit_kinds', 'mea_exact', 'rules_absent', 'rule_effects', 'evidence', 'blockers')):
        raise RecordedCaptureUnavailable('INVALID_RECORDED_REPORT')
    accounting = report['accounting']
    if set(accounting) != {'job_accounting', 'provider_reported', 'usage_status', 'cost_kind'} or accounting['usage_status'] not in {'REPORTED', 'UNKNOWN'} or accounting['cost_kind'] != 'ESTIMATE_NOT_BILLING':
        raise RecordedCaptureUnavailable('INVALID_RECORDED_REPORT')
    for key in ('job_accounting', 'provider_reported'):
        row = accounting[key]
        if set(row) != {'input_tokens', 'output_tokens', 'estimated_cost_usd'} or any(type(row[field]) is not int or not 0 <= row[field] <= 1_000_000_000 for field in ('input_tokens', 'output_tokens')):
            raise RecordedCaptureUnavailable('INVALID_RECORDED_REPORT')
        cost = Decimal(row['estimated_cost_usd'])
        if not cost.is_finite() or cost < 0:
            raise RecordedCaptureUnavailable('INVALID_RECORDED_REPORT')
    passes = provenance['passes']
    reported_totals = accounting['provider_reported']
    if reported_totals['input_tokens'] != sum(p['input_tokens'] for p in passes) or reported_totals['output_tokens'] != sum(p['output_tokens'] for p in passes) or Decimal(reported_totals['estimated_cost_usd']) != sum((Decimal(p['estimated_cost_usd']) for p in passes), Decimal(0)):
        raise RecordedCaptureUnavailable('INVALID_RECORDED_REPORT')
    job = accounting['job_accounting']
    reported = source['run_status'] == 'SUCCEEDED' and all(p['failure_class'] is None for p in passes) and all(job[k] == reported_totals[k] for k in ('input_tokens', 'output_tokens')) and abs(Decimal(job['estimated_cost_usd']) - Decimal(reported_totals['estimated_cost_usd'])) <= Decimal('0.000001')
    if accounting['usage_status'] != ('REPORTED' if reported else 'UNKNOWN') or status[('accounting', 'usage_complete')] != ('PASS' if reported else 'UNAVAILABLE'):
        raise RecordedCaptureUnavailable('INVALID_RECORDED_REPORT')
    return report


def load_recorded_report(path):
    with Path(path).open('rb') as source:
        data = source.read(256 * 1024 + 1)
    if len(data) > 256 * 1024:
        raise RecordedCaptureUnavailable('INVALID_RECORDED_REPORT')
    report = json.loads(data)
    if isinstance(report, dict) and set(report) == {'capture', 'comparison'}:
        comparison = report['comparison']
        if set(comparison) != {'status', 'coverage', 'regressions', 'contract_changes', 'unavailable'} or comparison['status'] not in {'OBSERVED_REGRESSION', 'NO_OBSERVED_REGRESSION'} or comparison['coverage'] != 'PARTIAL':
            raise RecordedCaptureUnavailable('INVALID_RECORDED_REPORT')
        for key in ('regressions', 'unavailable'):
            if len(comparison[key]) > 64 or any(set(row) != {'stage', 'assertion'} or row['stage'] not in {*_STAGES, 'final_output', 'accounting', 'provenance'} or row['assertion'] not in _ASSERTIONS for row in comparison[key]):
                raise RecordedCaptureUnavailable('INVALID_RECORDED_REPORT')
        if not set(comparison['contract_changes']) <= {'model', 'prompt_version', 'schema_version', 'harness_version', 'pass_contract_hashes', 'returned_model_id', 'reasoning_effort'}:
            raise RecordedCaptureUnavailable('INVALID_RECORDED_REPORT')
        report = report['capture']
    return validate_recorded_report(report)


def compare_recorded_reports(baseline, candidate):
    baseline, candidate = validate_recorded_report(baseline), validate_recorded_report(candidate)
    if any(baseline[key] != candidate[key] for key in ('case_id', 'input_digest', 'gold_digest')):
        raise RecordedCaptureUnavailable('INCOMPATIBLE_RECORDED_CASES')
    previous = {(row['stage'], row['assertion']): row['status'] for row in baseline['assertions']}
    current = {(row['stage'], row['assertion']): row['status'] for row in candidate['assertions']}
    if previous.keys() != current.keys():
        raise RecordedCaptureUnavailable('INCOMPATIBLE_RECORDED_CASES')
    regressions = [{'stage': stage, 'assertion': name} for (stage, name), status in previous.items()
                   if status == 'PASS' and current[(stage, name)] != 'PASS']
    contract_changes = [key for key in ('model', 'prompt_version', 'schema_version', 'harness_version') if baseline['provenance'][key] != candidate['provenance'][key]]
    if [(p['pass_id'], p['prompt_sha256'], p['schema_sha256'], p['harness_sha256']) for p in baseline['provenance']['passes']] != [(p['pass_id'], p['prompt_sha256'], p['schema_sha256'], p['harness_sha256']) for p in candidate['provenance']['passes']]:
        contract_changes.append('pass_contract_hashes')
    for key in ('returned_model_id', 'reasoning_effort'):
        if [(p['pass_id'], p[key]) for p in baseline['provenance']['passes']] != [(p['pass_id'], p[key]) for p in candidate['provenance']['passes']]:
            contract_changes.append(key)
    return {'status': 'OBSERVED_REGRESSION' if regressions else 'NO_OBSERVED_REGRESSION', 'coverage': 'PARTIAL',
            'regressions': regressions, 'contract_changes': contract_changes,
            'unavailable': [{'stage': stage, 'assertion': name} for (stage, name), status in current.items() if status in {'UNAVAILABLE', 'NOT_REACHED'}]}
