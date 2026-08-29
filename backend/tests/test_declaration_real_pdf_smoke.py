"""The real-PDF evaluation path is intentionally opt-in and fail closed."""

from __future__ import annotations

import os
import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.evals.declaration_knowledge import (
    ComparisonConfiguration,
    BoundedProductionDeclarationPipelineAdapter,
    LivePipelineCaseMetrics,
    LivePipelineReport,
    LiveHarnessUnavailable,
    ProductionPipelineEvaluationAdapter,
    live_eval_missing_gates,
    run_real_pdf_smoke,
)


def _synthetic_pipeline_provider(document_key: str, *, invented_quote: bool = False):
    """Return an in-process provider; this never constructs an SDK client."""
    from app.declarations.fake_provider import FakeDeclarationProvider
    from app.declarations.schemas import (
        CoreExtractionOutput,
        DocumentInventoryOutput,
        FactPayload,
        IntegerValue,
        MeaFraction,
        ReconciliationOutput,
        RuleExtractionOutput,
        TargetedRecheckOutput,
        TextValue,
    )

    evidence = [
        {
            "document_key": document_key,
            "pdf_page": 1,
            "quote": "erfundene Formulierung" if invented_quote else "Teilungserklärung",
        }
    ]
    facts = [
        FactPayload(
            subject_key="property:main",
            field="street",
            raw_value="Hauptstraße 1",
            normalized_value=TextValue(kind="TEXT", value="Hauptstraße 1"),
            confidence=0.99,
            evidence=evidence,
        ),
        FactPayload(
            subject_key="property:main",
            field="mea_denominator",
            raw_value="1",
            normalized_value=IntegerValue(kind="INTEGER", value=1),
            confidence=0.99,
            evidence=evidence,
        ),
        FactPayload(
            subject_key="unit:one",
            field="number",
            raw_value="1",
            normalized_value=TextValue(kind="TEXT", value="1"),
            confidence=0.99,
            evidence=evidence,
        ),
        FactPayload(
            subject_key="unit:one",
            field="mea",
            raw_value="1/1",
            normalized_value=MeaFraction(kind="MEA", numerator=1, denominator=1),
            confidence=0.99,
            evidence=evidence,
        ),
    ]

    class _RecordingFakeProvider(FakeDeclarationProvider):
        def __init__(self) -> None:
            super().__init__(
                inventory_output=DocumentInventoryOutput(
                    documents=[
                        {
                            "document_key": document_key,
                            "document_type": "DECLARATION",
                            "title": "Synthetic declaration",
                        }
                    ]
                ),
                core_output=CoreExtractionOutput(facts=facts),
                rules_output=RuleExtractionOutput(rules=[]),
                reconciliation_output=ReconciliationOutput(
                    facts=facts, rules=[], mea_set_status="COMPLETE"
                ),
                recheck_output=TargetedRecheckOutput(),
            )
            self.recheck_calls = 0

        async def recheck(self, *args, **kwargs):
            self.recheck_calls += 1
            return await super().recheck(*args, **kwargs)

    return _RecordingFakeProvider()


def _synthetic_manifest_entry(tmp_path: Path, *, case_id: str):
    from app.evals.declaration_knowledge import RealPdfManifestEntry
    from tests.declaration_fixtures import make_text_pdf

    pdf_path = tmp_path / f"{case_id}.pdf"
    pdf_path.write_bytes(make_text_pdf("Teilungserklärung\nHauptstraße 1"))
    return RealPdfManifestEntry(
        case_id=case_id,
        path=pdf_path,
        sha256=hashlib.sha256(pdf_path.read_bytes()).hexdigest(),
    )


@pytest.mark.asyncio
async def test_direct_bounded_adapter_cannot_construct_provider_or_read_pdf(monkeypatch, tmp_path: Path) -> None:
    import app.declarations.openai_provider as openai_provider_module
    from app.evals.declaration_knowledge import RealPdfManifestEntry

    constructed = False

    def forbidden_provider(**_kwargs):
        nonlocal constructed
        constructed = True
        pytest.fail("provider construction must require a gated live capability")

    monkeypatch.setattr(openai_provider_module, "OpenAiDeclarationProvider", forbidden_provider)
    entry = RealPdfManifestEntry(case_id="case-live-01", path=tmp_path / "unread.pdf", sha256="0" * 64)

    with pytest.raises(LiveHarnessUnavailable, match="validated live capability"):
        await BoundedProductionDeclarationPipelineAdapter().evaluate((entry,))

    assert constructed is False


@pytest.mark.asyncio
async def test_bounded_adapter_preserves_actual_recheck_router_evidence(monkeypatch, tmp_path: Path) -> None:
    """A production-shaped fake flow must retain the product router decision."""
    import app.declarations.openai_provider as openai_provider_module
    import app.evals.declaration_knowledge as module

    entry = _synthetic_manifest_entry(tmp_path, case_id="case-live-01")
    provider = _synthetic_pipeline_provider(entry.case_id, invented_quote=True)
    monkeypatch.setattr(
        openai_provider_module,
        "OpenAiDeclarationProvider",
        lambda **_kwargs: provider,
    )
    adapter = BoundedProductionDeclarationPipelineAdapter(
        capability=module._LiveEvaluationCapability(module._LIVE_CAPABILITY_SEAL)
    )

    report = await adapter.evaluate((entry,))

    recheck = next(item for item in report.cases[0].passes if item.pass_id == "recheck")
    assert provider.recheck_calls == 1
    assert recheck.route_action == "VISION_RECHECK"
    assert recheck.route_reason_codes == ("EVIDENCE_QUOTE_NOT_FOUND",)


def test_real_pdf_smoke_requires_all_explicit_live_gates(tmp_path: Path) -> None:
    missing = live_eval_missing_gates(provider="live", live=True, compare_models=True)
    if missing:
        pytest.skip("real-PDF smoke evaluation is opt-in")

    manifest = os.environ["HV_DECLARATION_REAL_PDF_MANIFEST"]

    report_path = run_real_pdf_smoke(
        manifest_path=manifest,
        report_dir=tmp_path,
        provider="live",
        live=True,
        compare_models=True,
    )
    assert report_path.is_file()


class _RecordingProductionAdapter:
    def __init__(self) -> None:
        self.entries = ()

    async def evaluate(self, entries):
        self.entries = tuple(entries)
        return LivePipelineReport(
            cases=(
                LivePipelineCaseMetrics(
                    case_id=entries[0].case_id,
                    page_count=1,
                    fact_count=2,
                    rule_count=1,
                    blocker_count=0,
                    input_tokens=21,
                    output_tokens=8,
                    estimated_cost_usd=Decimal("0.000012"),
                    latency_ms=7,
                    retry_count=0,
                    failure_classes=(),
                ),
            )
        )


def _comparison_environ(manifest_path: Path) -> dict[str, str]:
    return {
        "HV_DECLARATION_REAL_PDF_MANIFEST": str(manifest_path),
        "HV_DECLARATION_LIVE_EVAL": "1",
        "HV_OPENAI_API_KEY": "caller-provided-only",
        "HV_DECLARATION_COMPARE_TERRA_INPUT_USD_PER_MILLION": "1.00",
        "HV_DECLARATION_COMPARE_TERRA_OUTPUT_USD_PER_MILLION": "5.00",
        "HV_DECLARATION_COMPARE_TERRA_CACHED_INPUT_USD_PER_MILLION": "0.10",
    }


def test_live_comparison_requires_compare_models_flag_authorization_external_manifest_and_hash(
    monkeypatch, tmp_path: Path
) -> None:
    import app.evals.declaration_knowledge as module

    manifest_path = tmp_path / "manifest.json"
    factory_calls: list[str] = []
    manifest_calls: list[Path] = []
    monkeypatch.setattr(module, "validate_real_pdf_manifest", lambda path, **kwargs: manifest_calls.append(Path(path)) or ())

    with pytest.raises(LiveHarnessUnavailable, match="explicit live gates"):
        run_real_pdf_smoke(
            manifest_path=manifest_path,
            report_dir=tmp_path,
            provider="live",
            live=True,
            compare_models=False,
            adapter_factory=lambda configuration: factory_calls.append(configuration.label),
            environ=_comparison_environ(manifest_path),
        )

    assert manifest_calls == []
    assert factory_calls == []


def test_live_comparison_does_not_open_pdf_when_any_gate_is_missing(monkeypatch, tmp_path: Path) -> None:
    import app.evals.declaration_knowledge as module

    manifest_path = tmp_path / "unopened-manifest.json"
    opened: list[Path] = []
    monkeypatch.setattr(module, "validate_real_pdf_manifest", lambda path, **kwargs: opened.append(Path(path)) or ())

    with pytest.raises(LiveHarnessUnavailable, match="explicit live gates"):
        run_real_pdf_smoke(
            manifest_path=manifest_path,
            report_dir=tmp_path,
            provider="live",
            live=False,
            compare_models=True,
            adapter_factory=lambda _configuration: pytest.fail("factory must not be constructed"),
            environ={},
        )

    assert opened == []


@pytest.mark.parametrize("invalid_rate", ["0", "nonsense"])
def test_live_comparison_rejects_invalid_terra_rates_before_any_path_access(
    monkeypatch, tmp_path: Path, invalid_rate: str
) -> None:
    manifest_path = tmp_path / "unopened-manifest.json"
    environ = _comparison_environ(manifest_path)
    environ["HV_DECLARATION_COMPARE_TERRA_INPUT_USD_PER_MILLION"] = invalid_rate

    monkeypatch.setattr(
        Path,
        "is_dir",
        lambda _path: pytest.fail("invalid rates must fail before report path access"),
    )
    monkeypatch.setattr(
        Path,
        "resolve",
        lambda _path: pytest.fail("invalid rates must fail before path resolution"),
    )

    with pytest.raises(LiveHarnessUnavailable, match="Terra rates are invalid"):
        run_real_pdf_smoke(
            manifest_path=manifest_path,
            report_dir=tmp_path,
            provider="live",
            live=True,
            compare_models=True,
            adapter_factory=lambda _configuration: pytest.fail(
                "adapter must not be constructed"
            ),
            environ=environ,
        )


def test_live_comparison_requires_exact_live_environment_gate_before_path_access(
    monkeypatch, tmp_path: Path
) -> None:
    manifest_path = tmp_path / "unopened-manifest.json"
    environ = _comparison_environ(manifest_path)
    environ["HV_DECLARATION_LIVE_EVAL"] = " 1 "
    monkeypatch.setattr(
        Path,
        "is_dir",
        lambda _path: pytest.fail("invalid live gate must fail before report path access"),
    )

    with pytest.raises(LiveHarnessUnavailable, match="explicit live gates"):
        run_real_pdf_smoke(
            manifest_path=manifest_path,
            report_dir=tmp_path,
            provider="live",
            live=True,
            compare_models=True,
            adapter_factory=lambda _configuration: pytest.fail(
                "adapter must not be constructed"
            ),
            environ=environ,
        )


def test_live_comparison_report_contains_model_comparison_and_safe_pass_metadata_only(
    monkeypatch, tmp_path: Path
) -> None:
    import app.evals.declaration_knowledge as module

    manifest_path = tmp_path / "manifest.json"
    input_path = tmp_path / "synthetic-input.pdf"
    input_path.write_bytes(b"synthetic-only")
    entry = module.RealPdfManifestEntry(
        case_id="case-live-01",
        path=input_path,
        sha256=hashlib.sha256(input_path.read_bytes()).hexdigest(),
    )
    monkeypatch.setattr(module, "validate_real_pdf_manifest", lambda _path, **_kwargs: (entry,))
    events: list[str] = []

    class _ClosableAdapter(_RecordingProductionAdapter):
        async def evaluate(self, entries):
            events.append(f"evaluate:{entries[0].case_id}")
            return await super().evaluate(entries)

        def close(self):
            events.append("close")

    def factory(configuration: ComparisonConfiguration):
        events.append(f"create:{configuration.label}")
        return _ClosableAdapter()

    report_path = run_real_pdf_smoke(
        manifest_path=manifest_path,
        report_dir=tmp_path,
        provider="live",
        live=True,
        compare_models=True,
        adapter_factory=factory,
        environ=_comparison_environ(manifest_path),
    )

    rendered = report_path.read_text(encoding="utf-8")
    assert events == [
        "create:luna_medium", "evaluate:case-live-01", "close",
        "create:luna_low", "evaluate:case-live-01", "close",
        "create:terra_primary", "evaluate:case-live-01", "close",
        "create:luna_with_mini_router", "evaluate:case-live-01", "close",
    ]
    assert '"model_comparison"' in rendered
    assert '"descriptive_only":true' in rendered
    assert "synthetic-input" not in rendered
    assert "request_id" not in rendered
    assert "source_text" not in rendered


def test_live_comparison_rejects_mutable_adapter_reuse(monkeypatch, tmp_path: Path) -> None:
    import app.evals.declaration_knowledge as module

    manifest_path = tmp_path / "manifest.json"
    input_path = tmp_path / "synthetic-input.pdf"
    input_path.write_bytes(b"synthetic-only")
    entry = module.RealPdfManifestEntry(
        case_id="case-live-01",
        path=input_path,
        sha256=hashlib.sha256(input_path.read_bytes()).hexdigest(),
    )
    monkeypatch.setattr(module, "validate_real_pdf_manifest", lambda _path, **_kwargs: (entry,))
    adapter = _RecordingProductionAdapter()

    with pytest.raises(LiveHarnessUnavailable, match="fresh adapter"):
        run_real_pdf_smoke(
            manifest_path=manifest_path,
            report_dir=tmp_path,
            provider="live",
            live=True,
            compare_models=True,
            adapter_factory=lambda _configuration: adapter,
            environ=_comparison_environ(manifest_path),
        )


def test_live_comparison_maps_adapter_cleanup_failure_safely(monkeypatch, tmp_path: Path) -> None:
    import app.evals.declaration_knowledge as module

    manifest_path = tmp_path / "manifest.json"
    input_path = tmp_path / "synthetic-input.pdf"
    input_path.write_bytes(b"synthetic-only")
    entry = module.RealPdfManifestEntry(case_id="case-live-01", path=input_path, sha256=hashlib.sha256(input_path.read_bytes()).hexdigest())
    monkeypatch.setattr(module, "validate_real_pdf_manifest", lambda _path, **_kwargs: (entry,))

    class _CloseFailAdapter(_RecordingProductionAdapter):
        def close(self) -> None:
            raise RuntimeError("private cleanup detail")

    with pytest.raises(LiveHarnessUnavailable, match="adapter cleanup failed"):
        run_real_pdf_smoke(
            manifest_path=manifest_path,
            report_dir=tmp_path,
            provider="live",
            live=True,
            compare_models=True,
            adapter_factory=lambda _configuration: _CloseFailAdapter(),
            environ=_comparison_environ(manifest_path),
        )


def test_injected_production_pipeline_adapter_writes_only_sanitized_metrics(tmp_path: Path) -> None:
    input_path = tmp_path / "synthetic-input.pdf"
    input_path.write_bytes(b"synthetic-only")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "case-live-01": {
                    "path": str(input_path),
                    "sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
                }
            }
        ),
        encoding="utf-8",
    )
    adapters: list[_RecordingProductionAdapter] = []

    def factory(_configuration: ComparisonConfiguration) -> _RecordingProductionAdapter:
        adapter = _RecordingProductionAdapter()
        adapters.append(adapter)
        return adapter

    report_path = run_real_pdf_smoke(
        manifest_path=manifest_path,
        report_dir=tmp_path,
        provider="live",
        live=True,
        compare_models=True,
        adapter_factory=factory,
        environ=_comparison_environ(manifest_path),
    )

    rendered = report_path.read_text(encoding="utf-8")
    assert len(adapters) == 4
    assert all(len(adapter.entries) == 1 for adapter in adapters)
    assert oct(report_path.stat().st_mode & 0o777) == "0o600"
    assert "synthetic-input" not in rendered
    assert str(input_path) not in rendered
    assert "source_text" not in rendered


def test_real_smoke_enforces_gates_even_when_an_adapter_is_injected(tmp_path: Path) -> None:
    with pytest.raises(LiveHarnessUnavailable, match="explicit live gates"):
        run_real_pdf_smoke(
            manifest_path=tmp_path / "no-manifest.json",
            report_dir=tmp_path,
            provider="live",
            live=True,
            compare_models=True,
            adapter_factory=lambda _configuration: _RecordingProductionAdapter(),
            environ={},
        )


class _WrongReportAdapter:
    async def evaluate(self, entries):
        del entries
        return {"source_text": "must never be serialized"}


class _WrongCaseAdapter:
    async def evaluate(self, entries):
        del entries
        return LivePipelineReport(
            cases=(
                LivePipelineCaseMetrics(
                    case_id="different-case",
                    page_count=1,
                    fact_count=0,
                    rule_count=0,
                    blocker_count=0,
                    input_tokens=0,
                    output_tokens=0,
                    estimated_cost_usd=Decimal("0"),
                    latency_ms=0,
                    retry_count=0,
                    failure_classes=(),
                ),
            )
        )


def test_real_smoke_rejects_non_typed_adapter_reports(tmp_path: Path) -> None:
    input_path = tmp_path / "synthetic-input.pdf"
    input_path.write_bytes(b"synthetic-only")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "case-live-01": {
                    "path": str(input_path),
                    "sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
                }
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(LiveHarnessUnavailable, match="sanitized live report"):
        run_real_pdf_smoke(
            manifest_path=manifest_path,
            report_dir=tmp_path,
            provider="live",
            live=True,
            compare_models=True,
            adapter_factory=lambda _configuration: _WrongReportAdapter(),
            environ=_comparison_environ(manifest_path),
        )

    with pytest.raises(LiveHarnessUnavailable, match="manifest cases"):
        run_real_pdf_smoke(
            manifest_path=manifest_path,
            report_dir=tmp_path,
            provider="live",
            live=True,
            compare_models=True,
            adapter_factory=lambda _configuration: _WrongCaseAdapter(),
            environ=_comparison_environ(manifest_path),
        )


def test_bounded_adapter_rejects_oversized_input_before_reading(tmp_path: Path) -> None:
    from app.evals.declaration_knowledge import RealPdfManifestEntry

    input_path = tmp_path / "oversized.pdf"
    input_path.write_bytes(b"1234")
    entry = RealPdfManifestEntry(case_id="case-live-01", path=input_path, sha256="0" * 64)
    adapter = BoundedProductionDeclarationPipelineAdapter()

    with pytest.raises(LiveHarnessUnavailable, match="configured PDF size limit"):
        adapter._read_input(entry, max_bytes=3)


def test_real_smoke_rejects_oversized_input_before_hashing(monkeypatch, tmp_path: Path) -> None:
    from app.core.config import settings

    input_path = tmp_path / "oversized.pdf"
    input_path.write_bytes(b"1234")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "case-live-01": {
                    "path": str(input_path),
                    "sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "declaration_max_pdf_bytes", 3)

    with pytest.raises(LiveHarnessUnavailable, match="configured PDF size limit"):
        run_real_pdf_smoke(
            manifest_path=manifest_path,
            report_dir=tmp_path,
            provider="live",
            live=True,
            compare_models=True,
            adapter_factory=lambda _configuration: _RecordingProductionAdapter(),
            environ=_comparison_environ(manifest_path),
        )


def test_real_smoke_caps_manifest_case_count_before_adapter_calls(monkeypatch, tmp_path: Path) -> None:
    from app.core.config import settings

    first = tmp_path / "first.pdf"
    second = tmp_path / "second.pdf"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "case-live-01": {
                    "path": str(first),
                    "sha256": hashlib.sha256(first.read_bytes()).hexdigest(),
                },
                "case-live-02": {
                    "path": str(second),
                    "sha256": hashlib.sha256(second.read_bytes()).hexdigest(),
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "declaration_max_documents", 1)
    adapter = _RecordingProductionAdapter()

    with pytest.raises(LiveHarnessUnavailable, match="configured case limit"):
        run_real_pdf_smoke(
            manifest_path=manifest_path,
            report_dir=tmp_path,
            provider="live",
            live=True,
            compare_models=True,
            adapter_factory=lambda _configuration: adapter,
            environ=_comparison_environ(manifest_path),
        )
    assert adapter.entries == ()
