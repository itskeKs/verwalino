"""The real-PDF evaluation path is intentionally opt-in and fail closed."""

from __future__ import annotations

import os
import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.evals.declaration_knowledge import (
    BoundedProductionDeclarationPipelineAdapter,
    LivePipelineCaseMetrics,
    LivePipelineReport,
    LiveHarnessUnavailable,
    ProductionPipelineEvaluationAdapter,
    live_eval_missing_gates,
    run_real_pdf_smoke,
)


def test_real_pdf_smoke_requires_all_explicit_live_gates(tmp_path: Path) -> None:
    missing = live_eval_missing_gates()
    if missing:
        pytest.skip("real-PDF smoke evaluation is opt-in")

    manifest = os.environ["HV_DECLARATION_REAL_PDF_MANIFEST"]

    report_path = run_real_pdf_smoke(manifest_path=manifest, report_dir=tmp_path)
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
    adapter = _RecordingProductionAdapter()
    assert isinstance(adapter, ProductionPipelineEvaluationAdapter)

    report_path = run_real_pdf_smoke(
        manifest_path=manifest_path,
        report_dir=tmp_path,
        adapter=adapter,
        environ={
            "HV_DECLARATION_REAL_PDF_MANIFEST": str(manifest_path),
            "HV_DECLARATION_LIVE_EVAL": "1",
            "HV_OPENAI_API_KEY": "caller-provided-only",
        },
    )

    rendered = report_path.read_text(encoding="utf-8")
    assert len(adapter.entries) == 1
    assert oct(report_path.stat().st_mode & 0o777) == "0o600"
    assert "synthetic-input" not in rendered
    assert str(input_path) not in rendered
    assert "source_text" not in rendered


def test_real_smoke_enforces_gates_even_when_an_adapter_is_injected(tmp_path: Path) -> None:
    with pytest.raises(LiveHarnessUnavailable, match="explicit live gates"):
        run_real_pdf_smoke(
            manifest_path=tmp_path / "no-manifest.json",
            report_dir=tmp_path,
            adapter=_RecordingProductionAdapter(),
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
            adapter=_WrongReportAdapter(),
            environ={
                "HV_DECLARATION_REAL_PDF_MANIFEST": str(manifest_path),
                "HV_DECLARATION_LIVE_EVAL": "1",
                "HV_OPENAI_API_KEY": "caller-provided-only",
            },
        )

    with pytest.raises(LiveHarnessUnavailable, match="manifest cases"):
        run_real_pdf_smoke(
            manifest_path=manifest_path,
            report_dir=tmp_path,
            adapter=_WrongCaseAdapter(),
            environ={
                "HV_DECLARATION_REAL_PDF_MANIFEST": str(manifest_path),
                "HV_DECLARATION_LIVE_EVAL": "1",
                "HV_OPENAI_API_KEY": "caller-provided-only",
            },
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
            adapter=_RecordingProductionAdapter(),
            environ={
                "HV_DECLARATION_REAL_PDF_MANIFEST": str(manifest_path),
                "HV_DECLARATION_LIVE_EVAL": "1",
                "HV_OPENAI_API_KEY": "caller-provided-only",
            },
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
            adapter=adapter,
            environ={
                "HV_DECLARATION_REAL_PDF_MANIFEST": str(manifest_path),
                "HV_DECLARATION_LIVE_EVAL": "1",
                "HV_OPENAI_API_KEY": "caller-provided-only",
            },
        )
    assert adapter.entries == ()
