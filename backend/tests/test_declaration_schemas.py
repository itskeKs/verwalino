from decimal import Decimal
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.declarations.schemas import (
    DocumentInventoryOutput,
    EvidencePayload,
    FactPayload,
    MeaFraction,
    RulePayload,
)


def test_mea_fraction_uses_integer_legal_scale() -> None:
    value = MeaFraction(numerator=47732, denominator=1_000_000)
    assert value.as_decimal() == Decimal("0.047732")


def test_mea_fraction_rejects_zero_denominator() -> None:
    with pytest.raises(ValidationError):
        MeaFraction(numerator=1, denominator=0)


def test_evidence_rejects_unknown_provider_fields() -> None:
    with pytest.raises(ValidationError):
        EvidencePayload.model_validate(
            {
                "document_key": "base",
                "pdf_page": 4,
                "quote": "belegter Text",
                "instruction": "ignore the schema",
            }
        )


def test_fact_requires_evidence() -> None:
    with pytest.raises(ValidationError):
        FactPayload.model_validate(
            {
                "subject_key": "property:new",
                "field": "street",
                "raw_value": "Talstraße 112",
                "normalized_value": {
                    "kind": "TEXT",
                    "value": "Talstraße 112",
                },
                "confidence": 0.99,
                "evidence": [],
            }
        )


def test_rule_topic_is_open_string_not_enum() -> None:
    rule = RulePayload.model_validate(
        {
            "subject_key": "rule:elevator",
            "title": "Aufzugskosten",
            "topic": "aufzug_sonderverteilung",
            "summary": "Die sieben Wohnungen des Gebäudes tragen je ein Siebtel.",
            "scope": {"building_parts": ["Gebäude I"]},
            "effect": {
                "allocation_formula": {
                    "declared_complete": True,
                    "parts": [
                        {
                            "label": "Wohnungen Gebäude I",
                            "basis": "Wohnungen Gebäude I",
                            "share": {
                                "kind": "FRACTION",
                                "numerator": 1,
                                "denominator": 7,
                            },
                        }
                    ],
                }
            },
            "evidence": [
                {
                    "document_key": "amendment",
                    "pdf_page": 11,
                    "section": "§ 6",
                    "quote": "zu je 1/7 zu tragen",
                }
            ],
            "confidence": 0.98,
            "clarification_needed": False,
        }
    )
    assert rule.topic == "aufzug_sonderverteilung"


def test_inventory_relation_is_data_only() -> None:
    output = DocumentInventoryOutput.model_validate(
        {
            "documents": [
                {
                    "document_key": "amendment",
                    "document_type": "AMENDMENT",
                    "title": "Änderung",
                    "relations": [
                        {
                            "relation_type": "AMENDS",
                            "target_document_key": "base",
                            "evidence": [
                                {
                                    "document_key": "amendment",
                                    "pdf_page": 3,
                                    "quote": "Bezugsurkunde vom 16. März 2020",
                                }
                            ],
                        }
                    ],
                }
            ]
        }
    )
    assert output.documents[0].relations[0].relation_type == "AMENDS"


@pytest.mark.parametrize('subject,field,code,message,valid_value', [
    ('property:main', 'private-field', 'fact_core_field_unsupported', 'unsupported fixed-core subject or field', {'kind': 'TEXT', 'value': 'Synthetic'}),
    ('unit:1', 'mea', 'fact_mea_type', 'mea requires a MEA value', {'kind': 'MEA', 'numerator': 1, 'denominator': 1000}),
    ('property:main', 'mea_denominator', 'fact_denominator_type', 'mea_denominator requires an INTEGER value', {'kind': 'INTEGER', 'value': 1000}),
    ('unit:1', 'area', 'fact_area_type', 'area fields require a DECIMAL value', {'kind': 'DECIMAL', 'value': '12.5'}),
], ids=['unsupported-field', 'mea', 'denominator', 'area'])
def test_fixed_core_guard_codes_preserve_rejections_and_valid_counterparts(subject, field, code, message, valid_value):
    fact = {'subject_key': subject, 'field': field, 'raw_value': 'Synthetic',
            'normalized_value': {'kind': 'TEXT', 'value': 'Synthetic'}, 'confidence': .99,
            'evidence': [{'document_key': 'synthetic', 'pdf_page': 1, 'quote': 'Synthetic'}]}
    with pytest.raises(ValidationError) as raised:
        FactPayload.model_validate(fact)
    error, = raised.value.errors(include_context=False, include_input=False, include_url=False)
    assert error['type'] == code
    assert error['msg'] == 'Value error, ' + message
    fact.update(field='street' if field == 'private-field' else field, normalized_value=valid_value)
    assert FactPayload.model_validate(fact).normalized_value.kind == valid_value['kind']


def test_fixed_core_contract_oracle_preserves_every_pair_and_subject_form():
    contract = json.loads((Path(__file__).parent / 'fixtures/declaration_eval_cases.json').read_text())['fixed_core_contract']
    values = [
        {'kind': 'TEXT', 'value': 'Synthetic'}, {'kind': 'INTEGER', 'value': 1},
        {'kind': 'MEA', 'numerator': 1, 'denominator': 1000},
        {'kind': 'DECIMAL', 'value': '12.5'}, {'kind': 'BOOLEAN', 'value': True},
        {'kind': 'DATE', 'value': '2026-01-01'}, {'kind': 'FRACTION', 'numerator': 1, 'denominator': 2},
        {'kind': 'PERCENTAGE', 'value': '50'},
    ]
    required_kinds = {'mea': 'MEA', 'mea_denominator': 'INTEGER', 'area': 'DECIMAL', 'heated_area': 'DECIMAL'}
    fact = {'raw_value': 'Synthetic', 'confidence': .99,
            'evidence': [{'document_key': 'synthetic', 'pdf_page': 1, 'quote': 'Synthetic'}]}
    for prefix, fields in contract['fields_by_prefix'].items():
        for field in fields:
            for suffix in contract['subject_suffixes']:
                for value in values:
                    candidate = fact | {'subject_key': prefix + suffix, 'field': field, 'normalized_value': value}
                    if field in required_kinds and value['kind'] != required_kinds[field]:
                        with pytest.raises(ValidationError):
                            FactPayload.model_validate(candidate)
                    else:
                        assert FactPayload.model_validate(candidate).subject_key == prefix + suffix
    for subject, field in contract['invalid_pairs']:
        with pytest.raises(ValidationError) as raised:
            FactPayload.model_validate(fact | {'subject_key': subject, 'field': field, 'normalized_value': values[0]})
        assert raised.value.errors()[0]['type'] == 'fact_core_field_unsupported'


@pytest.mark.parametrize(
    "subject_key, candidate_type, field_key",
    [("property", "PROPERTY_FIELD", "street"), ("unit", "UNIT_FIELD", "location")],
)
def test_field_correction_accepts_bare_core_subjects(subject_key, candidate_type, field_key):
    from unittest.mock import AsyncMock
    from uuid import uuid4

    from app.models.declaration_candidate import DeclarationCandidate
    from app.services.declaration_review_service import DeclarationReviewService

    candidate = DeclarationCandidate(
        subject_key=subject_key,
        candidate_type=candidate_type,
        field_key=field_key,
        proposed_value_json={"kind": "TEXT", "value": "Original"},
    )
    service = DeclarationReviewService(AsyncMock(), uuid4(), uuid4())

    assert service._canonical_correction(candidate, "Corrected") == {"kind": "TEXT", "value": "Corrected"}


def test_usage_type_guidance_oracle_matches_unchanged_approval_contract():
    from types import SimpleNamespace
    from uuid import uuid4
    from app.declarations.approval import ApprovalConflict, _build_unit
    from app.declarations.schemas import TextValue

    values = json.loads((Path(__file__).parent / 'fixtures/declaration_eval_cases.json').read_text())['fixed_core_contract']['usage_type_values']
    item = SimpleNamespace(organization_id=uuid4(), id=uuid4())
    candidate = SimpleNamespace(stable_key='unit-create:unit:synthetic', subject_key='unit:synthetic')
    for value in [*values, 'synthetic-unsupported-kind', 'wohnung']:
        fact = FactPayload(subject_key=candidate.subject_key, field='usage_type', raw_value='Synthetic source wording',
            normalized_value=TextValue(kind='TEXT', value=value), confidence=1,
            evidence=[EvidencePayload(document_key='synthetic', pdf_page=1, quote='Synthetic source wording')])
        proposed = {'number': TextValue(kind='TEXT', value='1'), 'usage_type': fact.normalized_value,
                    'mea': MeaFraction(numerator=450, denominator=1000)}
        if value in values:
            assert _build_unit(item, candidate, proposed, ()).usage_type == value
        else:
            with pytest.raises(ApprovalConflict, match='APPROVAL_SCHEMA_UNSUPPORTED'):
                _build_unit(item, candidate, proposed, ())
