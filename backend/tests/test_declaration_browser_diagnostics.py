"""Failure-only diagnostics must not expose declaration response payloads."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from tests.bdd.steps import browser_declaration


@pytest.mark.asyncio
async def test_ready_approval_does_not_request_or_print_diagnostics(monkeypatch):
    prepare = object()
    page = SimpleNamespace(get_by_role=Mock(return_value=prepare), request=SimpleNamespace(get=AsyncMock()))
    monkeypatch.setattr(browser_declaration, 'expect', Mock(return_value=SimpleNamespace(to_be_enabled=AsyncMock())))

    assert await browser_declaration._expect_approval_ready(page, 'synthetic-import') is prepare
    page.request.get.assert_not_called()


@pytest.mark.asyncio
async def test_blocked_approval_preserves_assertion_and_reports_only_safe_state(monkeypatch):
    failure = AssertionError('prepare is disabled')
    monkeypatch.setattr(browser_declaration, 'expect', Mock(return_value=SimpleNamespace(to_be_enabled=AsyncMock(side_effect=failure))))
    response = SimpleNamespace(status=200, json=AsyncMock(return_value={
        'draft_revision': 7, 'can_approve': False,
        'pending_candidate_ids': ['private-candidate-id'],
        'issues': [
            {'severity': 'BLOCKER', 'code': 'MEA_INCOMPLETE', 'message': 'private-source-quote'},
            {'severity': 'WARNING', 'code': 'UNREVIEWED_WARNING'},
        ],
        'api_key': 'private-credential', 'source': 'private-document-content',
    }))
    candidates = SimpleNamespace(status=200, json=AsyncMock(return_value=[
        {'review_status': status, 'risk_class': risk, 'evidence': [{'quote': 'private-evidence'}] * count,
         'id': 'private-candidate-id', 'proposed_value_json': {'value': 'private-value'}}
        for status, risk, count in [('ACCEPTED', 'CRITICAL', 0), ('CORRECTED', 'CRITICAL', 2),
                                    ('ACCEPTED', 'LOW', 0), ('PENDING', 'CRITICAL', 0)]
    ]))
    page = SimpleNamespace(
        get_by_role=Mock(), request=SimpleNamespace(get=AsyncMock(side_effect=[response, candidates])),
        locator=Mock(side_effect=[SimpleNamespace(count=AsyncMock(return_value=0)),
                                 SimpleNamespace(all_text_contents=AsyncMock(return_value=['Review-Daten konnten nicht geladen werden.']))]),
    )

    with pytest.raises(AssertionError) as raised:
        await browser_declaration._expect_approval_ready(page, 'synthetic-import')

    assert raised.value is failure
    note, = failure.__notes__
    assert 'private-' not in note
    assert json.loads(note.removeprefix('Declaration approval state: ')) == {
        'preview_status': 200, 'draft_revision': 7, 'can_approve': False, 'pending_count': 1,
        'blocker_codes': ['MEA_INCOMPLETE'], 'visible_pending_count': 0,
        'issues': [{'code': 'MEA_INCOMPLETE', 'severity': 'BLOCKER'}, {'code': 'UNREVIEWED_WARNING', 'severity': 'WARNING'}],
        'candidates_status': 200, 'accepted_critical_evidence_counts': [0, 2],
        'review_errors': ['Review-Daten konnten nicht geladen werden.'],
    }
    assert [(call.args, call.kwargs) for call in page.request.get.await_args_list] == [
        (('/api/declaration-imports/synthetic-import/approval-preview',), {'timeout': 5_000}),
        (('/api/declaration-imports/synthetic-import/candidates',), {'timeout': 5_000}),
    ]


@pytest.mark.asyncio
async def test_diagnostic_fetch_failure_does_not_replace_or_expose_the_original_error(monkeypatch):
    failure = AssertionError('prepare is disabled')
    monkeypatch.setattr(browser_declaration, 'expect', Mock(return_value=SimpleNamespace(to_be_enabled=AsyncMock(side_effect=failure))))
    page = SimpleNamespace(get_by_role=Mock(), request=SimpleNamespace(get=AsyncMock(side_effect=RuntimeError('private-request-data'))))

    with pytest.raises(AssertionError) as raised:
        await browser_declaration._expect_approval_ready(page, 'synthetic-import')

    assert raised.value is failure
    note, = failure.__notes__
    assert 'private-' not in note
    assert json.loads(note.removeprefix('Declaration approval state: ')) == {
        'preview_error': 'RuntimeError', 'candidates_error': 'RuntimeError', 'visible_review_error': 'AttributeError',
    }


@pytest.mark.asyncio
@pytest.mark.parametrize('candidate_result', [RuntimeError('private-request-data'), SimpleNamespace(status=503)])
async def test_supplemental_failure_retains_the_preview_predicates(monkeypatch, candidate_result):
    failure = AssertionError('prepare is disabled')
    monkeypatch.setattr(browser_declaration, 'expect', Mock(return_value=SimpleNamespace(to_be_enabled=AsyncMock(side_effect=failure))))
    preview = SimpleNamespace(status=200, json=AsyncMock(return_value={
        'draft_revision': 9, 'can_approve': False, 'pending_candidate_ids': [],
        'issues': [{'code': 'RULE_NEEDS_CLARIFICATION', 'severity': 'WARNING', 'message': 'private-source-quote'}],
    }))
    page = SimpleNamespace(get_by_role=Mock(), request=SimpleNamespace(get=AsyncMock(side_effect=[preview, candidate_result])))

    with pytest.raises(AssertionError) as raised:
        await browser_declaration._expect_approval_ready(page, 'synthetic-import')

    assert raised.value is failure
    note, = failure.__notes__
    assert 'private-' not in note
    state = json.loads(note.removeprefix('Declaration approval state: '))
    assert state['draft_revision'] == 9
    assert state['can_approve'] is False
    assert state['pending_count'] == 0
    assert state['issues'] == [{'code': 'RULE_NEEDS_CLARIFICATION', 'severity': 'WARNING'}]
    assert 'accepted_critical_evidence_counts' not in state
    if isinstance(candidate_result, Exception):
        assert state['candidates_error'] == 'RuntimeError'
    else:
        assert state['candidates_status'] == 503
