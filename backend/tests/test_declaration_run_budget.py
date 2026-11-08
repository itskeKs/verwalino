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
        completed_at=start+timedelta(seconds=123))
    result = _run_out(run)
    assert result.estimated_cost_usd is None
    assert (result.input_tokens, result.output_tokens, result.runtime_seconds) == (42,17,123)
    run.provider = "OpenAiDeclarationProvider"
    assert _run_out(run).estimated_cost_usd == Decimal("0")
    run.provider = "UnknownProvider"
    assert _run_out(run).estimated_cost_usd is None
