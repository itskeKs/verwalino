"""Deterministic, synthetic-only checks for declaration knowledge evaluation."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.evals.declaration_knowledge import (
    CoreFact,
    EvaluationPass,
    SyntheticFakeEvaluationAdapter,
    actual_answer,
    build_report,
    evaluate_case,
    evaluate_core_facts,
    evaluate_fixture_file,
    gold_case,
    live_eval_missing_gates,
    main,
    mea_fact,
    report_as_json,
    validate_thresholds,
)


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "declaration_eval_cases.json"


def test_eval_metrics_penalize_unsupported_answer() -> None:
    report = evaluate_case(
        gold=gold_case(
            case_id="case-unsupported",
            allowed_source_ids={"source-1"},
            expected_status="NOT_DOCUMENTED",
        ),
        actual=actual_answer(
            status="ANSWER",
            source_ids=["invented-source"],
        ),
    )

    assert report.qa_citation_validity == Decimal("0")
    assert report.qa_unsupported_answer_count == 1
    assert report.qa_abstention_accuracy == Decimal("0")


@pytest.mark.parametrize("status", ["ANSWER", "NEEDS_CLARIFICATION"])
def test_answering_statuses_require_a_nonempty_unique_supported_citation(status: str) -> None:
    report = evaluate_case(
        gold=gold_case(
            case_id="case-citation-required",
            allowed_source_ids={"source-1"},
            expected_status=status,
        ),
        actual=actual_answer(status=status, source_ids=[]),
    )

    assert report.qa_citation_validity == Decimal("0")
    assert report.qa_source_support == Decimal("0")
    assert report.qa_unsupported_answer_count == (1 if status == "ANSWER" else 0)


@pytest.mark.parametrize(
    "status,source_ids",
    [("NOT_DOCUMENTED", ["invented-source"]), ("NOT_DOCUMENTED", ["source-1", "source-1"])],
)
def test_not_documented_rejects_invented_or_duplicate_citations(
    status: str, source_ids: list[str]
) -> None:
    report = evaluate_case(
        gold=gold_case(
            case_id="case-not-documented-citation",
            allowed_source_ids={"source-1"},
            expected_status="NOT_DOCUMENTED",
        ),
        actual=actual_answer(status=status, source_ids=source_ids),
    )

    assert report.qa_citation_validity == Decimal("0")


def test_invalid_citation_acceptance_counts_an_invalid_abstention_too() -> None:
    case = evaluate_case(
        gold=gold_case(
            case_id="case-invalid-abstention",
            allowed_source_ids={"source-1"},
            expected_status="NOT_DOCUMENTED",
        ),
        actual=actual_answer(status="NOT_DOCUMENTED", source_ids=["invented-source"]),
    )

    assert build_report([case]).invalid_citation_acceptance == 1


def test_core_metrics_treat_duplicate_actual_facts_as_precision_extras() -> None:
    expected = [mea_fact("unit-1", 1, 6)]
    actual = [mea_fact("unit-1", 1, 6), mea_fact("unit-1", 1, 6)]

    report = evaluate_core_facts(expected=expected, actual=actual)

    assert report.normalized_recall == Decimal("1")
    assert report.normalized_precision == Decimal("0.5")


def test_duplicate_rule_ids_are_rejected_as_invalid_scenario_output() -> None:
    rule = {"id": "rule-1", "topic": "repairs", "effect": {}, "evidence": []}
    with pytest.raises(ValueError, match="duplicate rule"):
        evaluate_case(
            gold=gold_case(
                case_id="case-duplicate-rule",
                allowed_source_ids=set(),
                expected_status="NOT_DOCUMENTED",
            ),
            actual={"rules": [rule, rule], "qa": actual_answer(status="NOT_DOCUMENTED", source_ids=[])},
        )


@pytest.mark.parametrize(
    "field,value,expected_message",
    [
        ("document_rule_relations", [["document-1", "rule-1"], ["document-1", "rule-1"]], "duplicate document_rule_relations"),
        ("rules", [{"id": "rule-1", "topic": "repairs", "effect": {}, "evidence": [{"source_id": "src-1", "page": 1}, {"source_id": "src-1", "page": 1}]}], "duplicate rule evidence"),
    ],
)
def test_duplicate_relations_and_evidence_are_rejected(
    field: str, value: object, expected_message: str
) -> None:
    actual = {"qa": actual_answer(status="NOT_DOCUMENTED", source_ids=[]), field: value}
    with pytest.raises(ValueError, match=expected_message):
        evaluate_case(
            gold=gold_case(
                case_id="case-duplicate-detail",
                allowed_source_ids=set(),
                expected_status="NOT_DOCUMENTED",
            ),
            actual=actual,
        )


def test_empty_expected_sets_score_as_vacuously_complete_and_are_documented() -> None:
    report = evaluate_case(
        gold=gold_case(case_id="case-empty-sets", allowed_source_ids=set(), expected_status="NOT_DOCUMENTED"),
        actual={"qa": actual_answer(status="NOT_DOCUMENTED", source_ids=[])},
    )

    assert report.rule_topic_coverage == Decimal("1")
    assert report.document_rule_relation_precision == Decimal("1")
    assert report.document_rule_relation_recall == Decimal("1")
    assert report.blocker_recall == Decimal("1")
    assert report.retrieval_recall_at_5 == Decimal("1")
    assert report.unit_assignment_coverage == Decimal("1")


def test_pass_ledger_is_sanitized_and_preserves_decimal_costs() -> None:
    report = evaluate_case(
        gold=gold_case(
            case_id="case-pass-ledger",
            allowed_source_ids=set(),
            expected_status="NOT_DOCUMENTED",
        ),
        actual={
            "qa": actual_answer(status="NOT_DOCUMENTED", source_ids=[]),
            "passes": [
                {
                    "pass_id": "rule-pass",
                    "input_tokens": 10,
                    "output_tokens": 4,
                    "estimated_cost_usd": "0.000012",
                    "latency_ms": 12,
                    "retry_count": 1,
                    "failure_class": "PROVIDER_TIMEOUT",
                }
            ],
        },
    )

    payload = report.as_dict()
    assert payload["passes"] == [
        {
            "pass_id": "rule-pass",
            "input_tokens": 10,
            "output_tokens": 4,
            "estimated_cost_usd": "0.000012",
            "latency_ms": 12,
            "retry_count": 1,
            "failure_class": "PROVIDER_TIMEOUT",
        }
    ]


def test_float_costs_and_structured_effects_are_rejected() -> None:
    with pytest.raises(ValueError, match="float"):
        evaluate_case(
            gold=gold_case(
                case_id="case-float-effect",
                allowed_source_ids=set(),
                expected_status="NOT_DOCUMENTED",
                rules=[{"id": "rule-float", "topic": "heating", "effect": {"share": 0.5}, "evidence": []}],
            ),
            actual={"qa": actual_answer(status="NOT_DOCUMENTED", source_ids=[])},
        )
    with pytest.raises(ValueError, match="float"):
        evaluate_case(
            gold=gold_case(case_id="case-float-cost", allowed_source_ids=set(), expected_status="NOT_DOCUMENTED"),
            actual={
                "qa": actual_answer(status="NOT_DOCUMENTED", source_ids=[]),
                "passes": [{"pass_id": "cost-pass", "input_tokens": 1, "output_tokens": 1, "estimated_cost_usd": 0.1, "latency_ms": 1, "retry_count": 0, "failure_class": None}],
            },
        )


def test_eval_metrics_compare_mea_as_exact_fraction() -> None:
    report = evaluate_core_facts(
        expected=[mea_fact("unit-034", 47_732, 1_000_000)],
        actual=[mea_fact("unit-034", 23_866, 500_000)],
    )

    assert report.normalized_exact_matches == 1
    assert report.mea_denominator_consistency == Decimal("0")
    assert report.normalized_precision == Decimal("1")


def test_core_metrics_include_identity_and_unit_count() -> None:
    report = evaluate_core_facts(
        expected=[
            CoreFact("property-a", "property_identity", value="building-a"),
            CoreFact("property-a", "unit_count", value="6"),
        ],
        actual=[
            CoreFact("property-a", "property_identity", value="building-a"),
            CoreFact("property-a", "unit_count", value="6"),
        ],
    )

    assert report.exact_property_identity is True
    assert report.exact_unit_count is True
    assert report.normalized_f1 == Decimal("1")


def test_case_metrics_cover_rules_relations_retrieval_qa_and_pass_ledger() -> None:
    gold = gold_case(
        case_id="case-complete",
        allowed_source_ids={"src-1"},
        expected_status="ANSWER",
        core_facts=[mea_fact("unit-1", 1, 6)],
        rules=[
            {
                "id": "rule-heat",
                "topic": "heating",
                "effect": {"area_share": "30/100", "usage_share": "70/100"},
                "evidence": [{"source_id": "src-1", "page": 4}],
            }
        ],
        document_rule_relations=[("document-1", "rule-heat")],
        blockers={"conflict-1"},
        retrieval_relevant_ids={"rule-heat"},
        expected_supported_source_ids={"src-1"},
        tenant_id="tenant-a",
    )
    actual = {
        "core_facts": [mea_fact("unit-1", 1, 6).as_dict()],
        "rules": [
            {
                "id": "rule-heat",
                "topic": "heating",
                "effect": {"usage_share": "70/100", "area_share": "30/100"},
                "evidence": [{"source_id": "src-1", "page": 4}],
            }
        ],
        "document_rule_relations": [["document-1", "rule-heat"]],
        "blockers": ["conflict-1"],
        "retrieved_ids": ["rule-heat"],
        "qa": actual_answer(status="ANSWER", source_ids=["src-1"]),
        "tenant_ids": ["tenant-a"],
        "passes": [
            EvaluationPass(
                pass_id="inventory",
                input_tokens=12,
                output_tokens=8,
                estimated_cost_usd=Decimal("0.000012"),
                latency_ms=41,
                retry_count=0,
                failure_class=None,
            ).as_dict()
        ],
    }

    report = evaluate_case(gold=gold, actual=actual)

    assert report.rule_topic_coverage == Decimal("1")
    assert report.rule_structured_effect_match == Decimal("1")
    assert report.rule_evidence_page_accuracy == Decimal("1")
    assert report.document_rule_relation_precision == Decimal("1")
    assert report.document_rule_relation_recall == Decimal("1")
    assert report.blocker_recall == Decimal("1")
    assert report.retrieval_recall_at_5 == Decimal("1")
    assert report.retrieval_mrr == Decimal("1")
    assert report.qa_status_accuracy == Decimal("1")
    assert report.qa_citation_validity == Decimal("1")
    assert report.qa_source_support == Decimal("1")
    assert report.total_input_tokens == 12
    assert report.total_output_tokens == 8
    assert report.total_estimated_cost_usd == Decimal("0.000012")
    assert report.total_latency_ms == 41
    assert report.total_retry_count == 0
    assert report.failure_classes == ()
    assert report.tenant_leakage_count == 0


def test_synthetic_fixture_has_required_reference_shapes_and_no_private_content() -> None:
    data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    cases = {item["id"]: item for item in data["cases"]}

    reference_a = cases["reference-a-commercial"]
    assert reference_a["gold"]["unit_breakdown"] == {"commercial": 6, "parking-special-use": 13}
    assert len(reference_a["gold"]["assignments"]["parking-special-use"]) == 13
    assert sum(
        item["numerator"] for item in reference_a["gold"]["core_facts"] if item["field_key"] == "mea"
    ) == 1_000
    assert {rule["topic"] for rule in reference_a["gold"]["rules"]} >= {
        "repairs",
        "heating_costs",
        "hot_water_costs",
    }

    base = cases["reference-b-base"]
    assert base["gold"]["unit_breakdown"] == {"apartments": 12, "parking": 21}
    assert len(base["gold"]["assignments"]["parking"]) == 21
    assert {rule["id"] for rule in base["gold"]["rules"]} >= {
        "rule-b-heating-30-70",
        "rule-b-hot-water-30-70",
        "rule-b-elevator-1-6",
        "rule-b-parking-1-21",
    }

    amendment = cases["reference-b-amendment"]
    apartment_mea = [
        fact
        for fact in amendment["gold"]["core_facts"]
        if fact["field_key"] == "mea" and fact["subject_id"].startswith("apartment-")
    ]
    assert len(apartment_mea) == 4
    assert all(item["numerator"] == 47_732 and item["denominator"] == 1_000_000 for item in apartment_mea)
    assert amendment["gold"]["unit_breakdown"] == {"apartments": 14, "parking": 21}
    assert amendment["gold"]["assignments"]["parking"] == base["gold"]["assignments"]["parking"]
    assert amendment["gold"]["amendment_lineage"] == {
        "split_from": ["apartment-13", "apartment-14"],
        "split_to": ["apartment-13a", "apartment-13b", "apartment-14a", "apartment-14b"],
    }

    serialized = json.dumps(data, ensure_ascii=False).lower()
    forbidden = ("wateler", "talstraße", "talstrasse", "library/cloudstorage", ".pdf", "signature")
    assert not any(value in serialized for value in forbidden)


def test_fixture_report_meets_fake_ci_thresholds_and_is_sanitized() -> None:
    report = evaluate_fixture_file(FIXTURE_PATH, adapter=SyntheticFakeEvaluationAdapter())
    failures = validate_thresholds(report)
    rendered = report_as_json(report)

    assert failures == ()
    assert '"case_id"' in rendered
    assert "source_text" not in rendered
    assert "quote" not in rendered
    assert "path" not in rendered.lower()
    assert "ignore all rules" not in rendered.lower()


def test_thresholds_report_each_failed_deterministic_gate() -> None:
    report = evaluate_fixture_file(FIXTURE_PATH)
    failed = report.with_overrides(
        candidate_schema_validity=Decimal("0.99"),
        invalid_citation_acceptance=1,
        unsupported_answer_count=1,
        blocker_recall=Decimal("0"),
        tenant_leakage_count=1,
        exact_fraction_normalization=Decimal("0"),
        retrieval_recall_at_5=Decimal("0.94"),
        unit_assignment_coverage=Decimal("0.99"),
    )

    assert len(validate_thresholds(failed)) == 8


def test_fake_cli_writes_only_sanitized_metric_report(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["--fixtures", str(FIXTURE_PATH), "--provider", "fake"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert '"threshold_metrics"' in output
    assert "src-a-01" not in output
    assert "document-a-01" not in output
    assert "quote" not in output


def test_live_cli_requires_explicit_flag_and_configuration(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["--fixtures", str(FIXTURE_PATH), "--provider", "live"])

    assert exit_code == 2
    assert capsys.readouterr().out == '{"status":"LIVE_FLAG_REQUIRED"}\n'


def test_fake_cli_exits_nonzero_when_a_deterministic_threshold_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    fixture["cases"][0]["fake_observed"]["candidate_schema_valid"] = False
    fixture_path = tmp_path / "threshold-failure.json"
    fixture_path.write_text(json.dumps(fixture), encoding="utf-8")

    exit_code = main(["--fixtures", str(fixture_path), "--provider", "fake"])

    assert exit_code == 1
    assert capsys.readouterr().out == '{"status":"THRESHOLD_FAILURE","failures":["candidate_schema_validity"]}\n'


def test_corrupted_typed_fake_adapter_fails_a_ci_gate() -> None:
    report = evaluate_fixture_file(FIXTURE_PATH, adapter=SyntheticFakeEvaluationAdapter(corrupt_output=True))

    assert "candidate_schema_validity" in validate_thresholds(report)


def test_synthetic_adapter_executes_product_fake_provider_contracts(monkeypatch) -> None:
    from app.declarations.fake_knowledge_provider import FakeKnowledgeProvider
    from app.declarations.fake_provider import FakeDeclarationProvider

    calls = {"declaration": 0, "knowledge": 0}
    original_reconcile = FakeDeclarationProvider.reconcile
    original_answer = FakeKnowledgeProvider.answer

    async def counted_reconcile(self, *args, **kwargs):
        calls["declaration"] += 1
        return await original_reconcile(self, *args, **kwargs)

    async def counted_answer(self, *args, **kwargs):
        calls["knowledge"] += 1
        return await original_answer(self, *args, **kwargs)

    monkeypatch.setattr(FakeDeclarationProvider, "reconcile", counted_reconcile)
    monkeypatch.setattr(FakeKnowledgeProvider, "answer", counted_answer)

    report = evaluate_fixture_file(FIXTURE_PATH, adapter=SyntheticFakeEvaluationAdapter())

    assert validate_thresholds(report) == ()
    assert calls == {"declaration": 7, "knowledge": 7}


def test_assignment_coverage_penalizes_a_missing_or_misclassified_assignment() -> None:
    gold = gold_case(
        case_id="case-assignments",
        allowed_source_ids=set(),
        expected_status="NOT_DOCUMENTED",
    )
    gold["unit_breakdown"] = {"apartments": 2, "parking": 2}
    gold["assignments"] = {"parking": ["parking-01", "parking-02"]}
    report = evaluate_case(
        gold=gold,
        actual={
            "qa": actual_answer(status="NOT_DOCUMENTED", source_ids=[]),
            "unit_breakdown": {"apartments": 2, "parking": 1},
            "assignments": {"parking": ["parking-01"]},
        },
    )

    assert report.unit_assignment_coverage < Decimal("1")


def test_assignment_coverage_penalizes_hallucinated_assignments_and_lineage() -> None:
    gold = gold_case(
        case_id="case-assignment-extras",
        allowed_source_ids=set(),
        expected_status="NOT_DOCUMENTED",
    )
    gold["unit_breakdown"] = {"apartments": 2, "parking": 1}
    gold["assignments"] = {"parking": ["parking-01"]}
    gold["amendment_lineage"] = {
        "split_from": ["apartment-01"],
        "split_to": ["apartment-01a", "apartment-01b"],
    }
    report = evaluate_case(
        gold=gold,
        actual={
            "qa": actual_answer(status="NOT_DOCUMENTED", source_ids=[]),
            "unit_breakdown": {"apartments": 2, "parking": 1, "storage": 1},
            "assignments": {"parking": ["parking-01", "parking-invented"]},
            "amendment_lineage": {
                "split_from": ["apartment-01"],
                "split_to": ["apartment-01a"],
            },
        },
    )

    assert report.unit_assignment_coverage < Decimal("1")


def test_direct_pass_instances_still_enforce_sanitized_decimal_fields() -> None:
    with pytest.raises(ValueError, match="pass_id"):
        EvaluationPass(
            pass_id="unsafe/pass",
            input_tokens=1,
            output_tokens=1,
            estimated_cost_usd=Decimal("0.1"),
            latency_ms=1,
            retry_count=0,
            failure_class=None,
        )
    with pytest.raises(ValueError, match="Decimal"):
        EvaluationPass(
            pass_id="safe-pass",
            input_tokens=1,
            output_tokens=1,
            estimated_cost_usd=0.1,  # type: ignore[arg-type]
            latency_ms=1,
            retry_count=0,
            failure_class=None,
        )


def test_live_gate_helper_requires_all_three_environment_values() -> None:
    assert live_eval_missing_gates({}) == (
        "HV_DECLARATION_LIVE_EVAL",
        "HV_DECLARATION_REAL_PDF_MANIFEST",
        "HV_OPENAI_API_KEY",
    )
    assert live_eval_missing_gates(
        {
            "HV_DECLARATION_LIVE_EVAL": "1",
            "HV_DECLARATION_REAL_PDF_MANIFEST": "manifest.json",
            "HV_OPENAI_API_KEY": "configured-by-caller",
        }
    ) == ()


@pytest.mark.parametrize(
    ("unsafe_key", "unsafe_value"),
    [
        ("source_text", "private sentence"),
        ("quote", "private sentence"),
        ("pdf_path", "/private/document.pdf"),
    ],
)
def test_fixture_loader_rejects_raw_sources_and_paths(tmp_path: Path, unsafe_key: str, unsafe_value: str) -> None:
    fixture = {
        "schema_version": "1",
        "cases": [{"id": "case-safe", "gold": {unsafe_key: unsafe_value}, "fake_observed": {}}],
    }
    fixture_path = tmp_path / "unsafe.json"
    fixture_path.write_text(json.dumps(fixture), encoding="utf-8")

    with pytest.raises(ValueError, match="sanitized"):
        evaluate_fixture_file(fixture_path)
