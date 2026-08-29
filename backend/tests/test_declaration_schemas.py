from decimal import Decimal

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
