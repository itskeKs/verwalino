"""Runtime wiring and bounded container-capability checks for imports."""

from __future__ import annotations

import os
import secrets
import subprocess
from collections.abc import Callable
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from sqlalchemy.engine import make_url

from app.models.organization import Organization
from app.workers import declaration_import


ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = ROOT / "docker-compose.prod.yml"
ENV_EXAMPLE_PATH = ROOT / ".env.example"
WORKER_ENTRYPOINT_PATH = ROOT / "docker/worker-entrypoint.sh"
README_PATH = ROOT / "README.md"
DECLARATION_RUNBOOK_PATH = ROOT / "docs" / "operations" / "declaration-imports.md"
ARCHITECTURE_PATH = ROOT / "docs" / "architecture.md"
DECLARATION_DESIGN_PATH = (
    ROOT / "docs" / "archive" / "superpowers" / "specs" / "2026-08-29-declaration-agentic-modernization-design.md"
)

_DECLARATION_EXTRACTION_ENVIRONMENT = {
    "HV_DECLARATION_IMPORT_ENABLED",
    "HV_DECLARATION_MAX_PDF_BYTES",
    "HV_DECLARATION_MAX_DOCUMENTS",
    "HV_DECLARATION_MAX_PAGES",
    "HV_DECLARATION_OCR_DPI",
    "HV_DECLARATION_OCR_TIMEOUT_SECONDS",
    "HV_DECLARATION_TEXT_MIN_CHARS",
    "HV_DECLARATION_OCR_MAX_PIXELS_PER_PAGE",
    "HV_DECLARATION_OCR_MAX_TEMP_BYTES",
    "HV_DECLARATION_OCR_MAX_PAGES_PER_IMPORT",
    "HV_DECLARATION_OCR_MAX_PIXELS_PER_IMPORT",
    "HV_DECLARATION_PREFLIGHT_DEADLINE_SECONDS",
    "HV_DECLARATION_PRIMARY_MODEL",
    "HV_DECLARATION_ESCALATION_MODEL",
    "HV_DECLARATION_PRIMARY_REASONING_EFFORT",
    "HV_DECLARATION_ESCALATION_REASONING_EFFORT",
    "HV_DECLARATION_PROMPT_VERSION",
    "HV_DECLARATION_SCHEMA_VERSION",
    "HV_DECLARATION_HARNESS_VERSION",
    "HV_DECLARATION_PROVIDER_TIMEOUT_SECONDS",
    "HV_DECLARATION_MAX_COST_USD",
    "HV_DECLARATION_MAX_RECHECK_PAGES",
    "HV_DECLARATION_MAX_RECHECK_CALLS",
    "HV_DECLARATION_PROVIDER_BATCH_PAGES",
    "HV_DECLARATION_PROVIDER_BATCH_CHARS",
    "HV_DECLARATION_MAX_INPUT_TOKENS",
    "HV_DECLARATION_MAX_OUTPUT_TOKENS",
    "HV_DECLARATION_PROVIDER_MAX_OUTPUT_TOKENS_PER_CALL",
    "HV_DECLARATION_MAX_CANDIDATES",
    "HV_DECLARATION_PRIMARY_INPUT_USD_PER_MILLION",
    "HV_DECLARATION_PRIMARY_OUTPUT_USD_PER_MILLION",
    "HV_DECLARATION_PRIMARY_CACHED_INPUT_USD_PER_MILLION",
    "HV_DECLARATION_PRIMARY_CACHE_WRITE_MULTIPLIER",
    "HV_DECLARATION_ESCALATION_INPUT_USD_PER_MILLION",
    "HV_DECLARATION_ESCALATION_OUTPUT_USD_PER_MILLION",
    "HV_DECLARATION_ESCALATION_CACHED_INPUT_USD_PER_MILLION",
    "HV_DECLARATION_WORKER_ID",
    "HV_DECLARATION_WORKER_POLL_SECONDS",
    "HV_DECLARATION_LEASE_SECONDS",
    "HV_DECLARATION_MAX_ATTEMPTS",
}
_DECLARATION_AGENTIC_DEFAULTS = {
    "HV_DECLARATION_PRIMARY_REASONING_EFFORT": "medium",
    "HV_DECLARATION_ESCALATION_REASONING_EFFORT": "none",
    "HV_DECLARATION_PRIMARY_CACHED_INPUT_USD_PER_MILLION": "0.02",
    "HV_DECLARATION_PRIMARY_CACHE_WRITE_MULTIPLIER": "1.25",
    "HV_DECLARATION_ESCALATION_CACHED_INPUT_USD_PER_MILLION": "0.075",
}
_DECLARATION_QA_ENVIRONMENT = {
    "HV_DECLARATION_QA_ENABLED",
    "HV_DECLARATION_QA_MODEL",
    "HV_DECLARATION_QA_TIMEOUT_SECONDS",
    "HV_DECLARATION_QA_MAX_HITS",
    "HV_DECLARATION_QA_MAX_INPUT_TOKENS",
    "HV_DECLARATION_QA_MAX_OUTPUT_TOKENS",
    "HV_DECLARATION_QA_MAX_COST_USD",
    "HV_DECLARATION_QA_BUDGET_WINDOW_MINUTES",
    "HV_DECLARATION_QA_RESERVATION_SECONDS",
    "HV_DECLARATION_QA_MAX_CONCURRENCY",
    "HV_DECLARATION_QA_INPUT_USD_PER_MILLION",
    "HV_DECLARATION_QA_OUTPUT_USD_PER_MILLION",
}
_DECLARATION_QA_DEFAULTS = {
    "HV_DECLARATION_QA_ENABLED": "false",
    "HV_DECLARATION_QA_MODEL": "gpt-5.6-luna",
    "HV_DECLARATION_QA_TIMEOUT_SECONDS": "30",
    "HV_DECLARATION_QA_MAX_HITS": "20",
    "HV_DECLARATION_QA_MAX_INPUT_TOKENS": "20000",
    "HV_DECLARATION_QA_MAX_OUTPUT_TOKENS": "1000",
    "HV_DECLARATION_QA_MAX_COST_USD": "0.05",
    "HV_DECLARATION_QA_BUDGET_WINDOW_MINUTES": "60",
    "HV_DECLARATION_QA_RESERVATION_SECONDS": "120",
    "HV_DECLARATION_QA_MAX_CONCURRENCY": "4",
    "HV_DECLARATION_QA_INPUT_USD_PER_MILLION": "0.20",
    "HV_DECLARATION_QA_OUTPUT_USD_PER_MILLION": "1.20",
}
_IMAGE_SMOKE_TIMEOUT_SECONDS = 60
_IMAGE_SMOKE_CLEANUP_TIMEOUT_SECONDS = 10


def test_readme_declaration_setup_includes_all_required_extras() -> None:
    readme = README_PATH.read_text(encoding="utf-8")

    assert "uv sync --extra dev --extra ocr --extra ops" in readme
    assert "docs/operations/declaration-imports.md" in readme


def test_runbook_states_no_live_call_without_all_gates() -> None:
    runbook = DECLARATION_RUNBOOK_PATH.read_text(encoding="utf-8")

    for required in (
        "PROVIDER_REFUSAL",
        "PROVIDER_INCOMPLETE",
        "PROVIDER_TIMEOUT",
        "PROVIDER_CONNECTION_ERROR",
        "PROVIDER_RATE_LIMIT",
        "PROVIDER_SERVER_ERROR",
        "OPENAI_REQUEST_FAILED",
        "PROVIDER_SCHEMA_FAILURE",
        "PROVIDER_USAGE_INVALID",
        "Evidence-Quote",
        "RouterDecision",
        "Human Approval",
        "--provider live --live --compare-models",
        "HV_DECLARATION_LIVE_EVAL=1",
        "HV_DECLARATION_REAL_PDF_MANIFEST",
        "HV_DECLARATION_COMPARE_TERRA_INPUT_USD_PER_MILLION",
        "HV_DECLARATION_COMPARE_TERRA_OUTPUT_USD_PER_MILLION",
        "HV_DECLARATION_COMPARE_TERRA_CACHED_INPUT_USD_PER_MILLION",
        "luna_medium",
        "luna_low",
        "terra_primary",
        "luna_with_mini_router",
        "descriptive_only",
        "not promotion-eligible",
        "SDK attempt",
        "23",
    ):
        assert required in runbook


def test_live_eval_documentation_describes_two_phase_gate_ordering() -> None:
    runbook = DECLARATION_RUNBOOK_PATH.read_text(encoding="utf-8")
    design = DECLARATION_DESIGN_PATH.read_text(encoding="utf-8")

    # The runbook is English; the archived design record stays German.
    for document, phrases, local in (
        (
            runbook,
            ("path method", "capability", "path/manifest/PDF/provider access", "transmission"),
            "local",
        ),
        (
            design,
            ("Path-Methode", "Capability", "Path-/Manifest-/PDF-/Providerzugriff", "Übertragung"),
            "lokal",
        ),
    ):
        for required in ("Phase A", "Phase B", "SHA-256", *phrases):
            assert required in document
        phase_a = document.split("**Phase A", maxsplit=1)[1].split(
            "**Phase B", maxsplit=1
        )[0]
        phase_b = document.split("**Phase B", maxsplit=1)[1]
        assert "HV_DECLARATION_REAL_PDF_MANIFEST" in phase_a
        assert "SHA-256" in phase_b
        assert local in phase_b

    assert 'model: Literal["gpt-5.4-mini"] | None' in design


def test_documentation_states_fixed_manager_and_retention_boundary() -> None:
    architecture = ARCHITECTURE_PATH.read_text(encoding="utf-8")
    runbook = DECLARATION_RUNBOOK_PATH.read_text(encoding="utf-8")

    assert "deterministic manager" in architecture
    assert "freely autonomous agent" in architecture
    assert "untrusted data" in architecture
    assert "store=false" in runbook
    assert "is not ZDR" in runbook


def test_declaration_runtime_defaults_use_explicit_medium_and_none_effort() -> None:
    from app.core.config import Settings

    configured = Settings()

    assert configured.declaration_primary_reasoning_effort == "medium"
    assert configured.declaration_escalation_reasoning_effort == "none"


def test_declaration_runtime_defaults_use_verified_luna_and_mini_cached_rates() -> None:
    from decimal import Decimal

    from app.core.config import Settings

    configured = Settings()

    assert configured.declaration_primary_cached_input_usd_per_million == Decimal("0.02")
    assert configured.declaration_primary_cache_write_multiplier == Decimal("1.25")
    assert configured.declaration_escalation_cached_input_usd_per_million == Decimal("0.075")


def test_invalid_effort_or_cache_rate_is_rejected() -> None:
    from decimal import Decimal

    from pydantic import ValidationError

    from app.core.config import Settings

    with pytest.raises(ValidationError):
        Settings(declaration_primary_reasoning_effort="unsupported")
    with pytest.raises(ValidationError):
        Settings(declaration_primary_cache_write_multiplier=Decimal("1.20"))
    with pytest.raises(ValidationError):
        Settings(declaration_escalation_cached_input_usd_per_million=Decimal("0"))


@pytest.mark.parametrize("effort", ["none", "low", "medium", "high", "xhigh", "max"])
def test_primary_reasoning_effort_accepts_exact_luna_allowlist(effort: str) -> None:
    from app.core.config import Settings

    assert Settings(declaration_primary_reasoning_effort=effort).declaration_primary_reasoning_effort == effort


@pytest.mark.parametrize("effort", ["none", "low", "medium", "high", "xhigh"])
def test_escalation_reasoning_effort_accepts_exact_mini_allowlist(effort: str) -> None:
    from app.core.config import Settings

    assert Settings(declaration_escalation_reasoning_effort=effort).declaration_escalation_reasoning_effort == effort


@pytest.mark.parametrize("field", ["declaration_primary_reasoning_effort", "declaration_escalation_reasoning_effort"])
def test_declaration_reasoning_effort_rejects_minimal(field: str) -> None:
    from pydantic import ValidationError

    from app.core.config import Settings

    with pytest.raises(ValidationError):
        Settings(**{field: "minimal"})


@pytest.mark.parametrize(
    "field,value",
    [
        ("declaration_primary_input_usd_per_million", Decimal("0")),
        ("declaration_primary_output_usd_per_million", Decimal("-1")),
        ("declaration_escalation_input_usd_per_million", Decimal("0")),
        ("declaration_escalation_output_usd_per_million", Decimal("-1")),
        ("declaration_primary_input_usd_per_million", Decimal("NaN")),
    ],
)
def test_legacy_declaration_rates_must_be_positive_and_finite(field: str, value: Decimal) -> None:
    from pydantic import ValidationError

    from app.core.config import Settings

    with pytest.raises(ValidationError):
        Settings(**{field: value})


def _compose() -> dict[str, object]:
    return yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))


def _environment(service: dict[str, object]) -> dict[str, str]:
    environment = service["environment"]
    assert isinstance(environment, dict)
    return environment


def test_compose_declares_least_privilege_worker_topology() -> None:
    services = _compose()["services"]
    assert isinstance(services, dict)
    app = services["app"]
    worker = services["worker"]
    assert isinstance(app, dict)
    assert isinstance(worker, dict)

    assert "ports" not in worker
    assert worker["image"] == app["image"]
    # Production promotion supplies one immutable, pre-built image to both
    # processes; neither runtime service may build from an unchecked checkout.
    assert "build" not in worker
    assert "build" not in app
    assert worker["restart"] == "unless-stopped"
    assert "documents:/data/documents" in worker["volumes"]
    assert worker["entrypoint"] == ["/usr/local/bin/worker-entrypoint.sh"]
    assert worker["read_only"] is True
    assert worker["user"] == "10001:10001"
    assert worker["cap_drop"] == ["ALL"]
    assert worker["security_opt"] == ["no-new-privileges:true"]
    assert worker["healthcheck"] == {
        "test": ["CMD", "python", "-m", "app.workers.declaration_import", "--healthcheck"],
        "interval": "30s",
        "timeout": "10s",
        "retries": 3,
    }
    assert worker["networks"] == {
        "backend": {"gw_priority": 0},
        "egress": {"gw_priority": 1},
    }
    networks = _compose()["networks"]
    assert networks["backend"] == {"internal": True}
    assert networks["egress"] == {"internal": False}

    environment = _environment(worker)
    assert make_url(environment["HV_DATABASE_URL_WORKER"]).username == "app_worker"
    assert environment["HV_OPENAI_API_KEY"] == "${HV_OPENAI_API_KEY:-}"
    assert environment["HV_DOCUMENT_STORAGE_ROOT"] == "/data/documents"
    assert environment["HV_DATA_REGION"] == "${HV_DATA_REGION:-EU}"
    assert _DECLARATION_EXTRACTION_ENVIRONMENT <= environment.keys()
    assert _DECLARATION_QA_ENVIRONMENT.isdisjoint(environment)
    assert {
        "HV_DATABASE_URL",
        "HV_DATABASE_URL_MIGRATOR",
        "HV_DATABASE_URL_SYNC",
        "HV_SESSION_SECRET",
    }.isdisjoint(environment)

    assert app["healthcheck"] == {
        "test": ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8000/api/health/live', headers={'Host': '${HV_PUBLIC_HOST:?set HV_PUBLIC_HOST in the protected env file or invoking environment}'}), timeout=2)\""],
        "interval": "10s",
        "timeout": "3s",
        "retries": 6,
        "start_period": "15s",
    }
    assert app["depends_on"]["db"]["condition"] == "service_healthy"
    assert worker["depends_on"]["db"]["condition"] == "service_healthy"
    assert worker["depends_on"]["app"]["condition"] == "service_healthy"

    app_environment = _environment(app)
    assert "app_rw" in app_environment["HV_DATABASE_URL"]
    assert {"HV_DATABASE_URL_MIGRATOR", "HV_DATABASE_URL_SYNC"}.isdisjoint(app_environment)
    assert app_environment["HV_OPENAI_API_KEY"] == "${HV_OPENAI_API_KEY:-}"
    assert _DECLARATION_QA_ENVIRONMENT == _DECLARATION_QA_DEFAULTS.keys()
    assert _DECLARATION_QA_ENVIRONMENT <= app_environment.keys()
    assert all(
        app_environment[name] == "${" + name + ":-" + default + "}"
        for name, default in _DECLARATION_AGENTIC_DEFAULTS.items()
    )
    assert all(
        environment[name] == "${" + name + ":-" + default + "}"
        for name, default in _DECLARATION_AGENTIC_DEFAULTS.items()
    )
    assert all(
        app_environment[name] == "${" + name + ":-" + default + "}"
        for name, default in _DECLARATION_QA_DEFAULTS.items()
    )


def test_compose_takes_the_public_host_from_the_env_file_and_labels_releases() -> None:
    host = "${HV_PUBLIC_HOST:?set HV_PUBLIC_HOST in the protected env file or invoking environment}"
    services = _compose()["services"]
    assert isinstance(services, dict)
    app = services["app"]

    assert _environment(app)["HV_ALLOWED_HOSTS"] == host
    assert app["labels"]["traefik.http.routers.hausverwaltung-alpha.rule"] == f"Host(`{host}`)"
    assert app["labels"]["traefik.http.routers.hausverwaltung-alpha-login.rule"] == (
        f"Host(`{host}`) && Path(`/api/auth/login`)"
    )
    for name in ("app", "worker", "invoice-worker"):
        labels = services[name]["labels"]
        assert labels["io.verwalino.release"].startswith("${HV_RELEASE_ID:?")
        assert not [key for key in labels if key.endswith(".hausverwaltung.release")]
    assert "HV_PUBLIC_HOST=" in ENV_EXAMPLE_PATH.read_text(encoding="utf-8").splitlines()


def test_runtime_example_and_entrypoint_keep_secrets_at_boundaries() -> None:
    example_values = {
        line.partition("=")[0]: line.partition("=")[2]
        for line in ENV_EXAMPLE_PATH.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#") and "=" in line
    }
    assert example_values["HV_OPENAI_API_KEY"] == ""
    assert _DECLARATION_EXTRACTION_ENVIRONMENT <= example_values.keys()
    assert all(
        example_values[name] == default
        for name, default in _DECLARATION_AGENTIC_DEFAULTS.items()
    )
    assert _DECLARATION_QA_ENVIRONMENT <= example_values.keys()
    assert all(
        example_values[name] == default
        for name, default in _DECLARATION_QA_DEFAULTS.items()
    )
    assert WORKER_ENTRYPOINT_PATH.read_text(encoding="utf-8") == (
        '#!/bin/sh\nset -eu\n\nexec python -m app.workers."${HV_WORKER_KIND:-declaration_import}" "$@"\n'
    )
    assert WORKER_ENTRYPOINT_PATH.stat().st_mode & 0o111


@pytest.mark.asyncio
async def test_healthcheck_reads_only_global_organization_id_and_cleans_tempfile(
    tmp_path: Path,
) -> None:
    seen: list[object] = []

    class Session:
        async def execute(self, statement):
            seen.append(statement)

    @asynccontextmanager
    async def unscoped():
        yield Session()

    configured = SimpleNamespace(document_storage_root=str(tmp_path))
    healthy = await declaration_import.run_healthcheck(
        configured=configured,
        unscoped_session_factory=unscoped,
        binary_finder=lambda _name: "/usr/bin/tool",
        command_runner=lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, "deu\n", ""),
        random_name=lambda: "fixed-healthcheck-name",
    )

    assert healthy
    assert len(seen) == 1
    assert tuple(seen[0].selected_columns) == (Organization.id,)
    healthcheck_dir = tmp_path / ".healthcheck"
    assert healthcheck_dir.is_dir()
    assert list(healthcheck_dir.iterdir()) == []


@pytest.mark.asyncio
async def test_healthcheck_unlinks_tempfile_when_storage_check_fails(tmp_path: Path) -> None:
    @asynccontextmanager
    async def unscoped():
        yield SimpleNamespace(execute=lambda _statement: _completed_awaitable())

    def fail_fsync(_descriptor: int) -> None:
        raise OSError("test storage fault")

    configured = SimpleNamespace(document_storage_root=str(tmp_path))
    healthy = await declaration_import.run_healthcheck(
        configured=configured,
        unscoped_session_factory=unscoped,
        binary_finder=lambda _name: "/usr/bin/tool",
        command_runner=lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, "deu\n", ""),
        random_name=lambda: "failure-healthcheck-name",
        fsync=fail_fsync,
    )

    assert not healthy
    assert list((tmp_path / ".healthcheck").iterdir()) == []


def test_healthcheck_cli_never_constructs_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    async def healthy() -> bool:
        return True

    async def provider_construction_is_forbidden() -> None:
        raise AssertionError("healthcheck must not construct the provider")

    monkeypatch.setattr(declaration_import, "run_healthcheck", healthy)
    monkeypatch.setattr(declaration_import, "_main", provider_construction_is_forbidden)
    assert declaration_import.main(["--healthcheck"]) == 0


def test_worker_cli_supports_standard_help_without_constructing_provider(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def provider_construction_is_forbidden() -> None:
        raise AssertionError("help must not construct the provider")

    monkeypatch.setattr(declaration_import, "_main", provider_construction_is_forbidden)
    with pytest.raises(SystemExit) as exit_info:
        declaration_import.main(["--help"])

    assert exit_info.value.code == 0
    assert "--healthcheck" in capsys.readouterr().out


def test_image_smoke_timeout_removes_only_its_exact_container_name() -> None:
    commands: list[list[str]] = []

    def timeout_then_cleanup(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if command[1] == "run":
            raise subprocess.TimeoutExpired(command, 60)
        return subprocess.CompletedProcess(command, 0, "", "")

    smoke_name = "hv-declaration-runtime-smoke-test-name"
    with pytest.raises(subprocess.TimeoutExpired):
        _run_built_image_smoke(
            "hausverwaltung:declaration-foundation",
            command_runner=timeout_then_cleanup,
            container_name=smoke_name,
        )

    assert commands[0][0:5] == ["docker", "run", "--rm", "--name", smoke_name]
    assert commands[1] == ["docker", "rm", "-f", smoke_name]


def _run_built_image_smoke(
    image: str,
    *,
    command_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    container_name: str | None = None,
) -> subprocess.CompletedProcess[str]:
    smoke_name = container_name or f"hv-declaration-runtime-smoke-{secrets.token_hex(16)}"
    try:
        return command_runner(
            [
                "docker",
                "run",
                "--rm",
                "--name",
                smoke_name,
                "--entrypoint",
                "/bin/sh",
                image,
                "-c",
                "set -eu; test \"$(id -u)\" = 10001; "
                "command -v qpdf >/dev/null; command -v pdftoppm >/dev/null; "
                "command -v tesseract >/dev/null; "
                "tesseract --list-langs | grep -Fx deu >/dev/null; "
                "python -c 'import pikepdf, pdf2image, pytesseract'",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=_IMAGE_SMOKE_TIMEOUT_SECONDS,
        )
    finally:
        try:
            command_runner(
                ["docker", "rm", "-f", smoke_name],
                check=False,
                capture_output=True,
                text=True,
                timeout=_IMAGE_SMOKE_CLEANUP_TIMEOUT_SECONDS,
            )
        except subprocess.SubprocessError:
            pass
        except OSError:
            pass


@pytest.mark.skipif(
    not os.environ.get("HV_DECLARATION_RUNTIME_IMAGE"),
    reason="set HV_DECLARATION_RUNTIME_IMAGE after building the local runtime image",
)
def test_built_runtime_image_has_ocr_capabilities_and_nonroot_user() -> None:
    image = os.environ["HV_DECLARATION_RUNTIME_IMAGE"]
    result = _run_built_image_smoke(image)
    assert result.returncode == 0, result.stderr


async def _completed_awaitable() -> None:
    return None
