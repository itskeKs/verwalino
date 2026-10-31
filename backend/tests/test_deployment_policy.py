"""Deployment decisions remain explicit and reviewable in both modes."""

from dataclasses import FrozenInstanceError, asdict
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings


@pytest.mark.parametrize("mode, expected", [
    ("selfhost", {
        "deployment_mode": "selfhost",
        "first_run_setup_enabled": True,
        "self_service_signup_enabled": False,
        "rate_limit_profile": "relaxed",
        "quota_enforcement": False,
    }),
    ("hosted", {
        "deployment_mode": "hosted",
        "first_run_setup_enabled": False,
        "self_service_signup_enabled": True,
        "rate_limit_profile": "strict",
        "quota_enforcement": True,
    }),
])
def test_policy_table_matches_the_mode_snapshot(mode, expected):
    from app.core.deployment_policy import DeploymentPolicy

    policy = DeploymentPolicy.for_mode(Settings(_env_file=None, deployment_mode=mode).deployment_mode)
    assert asdict(policy) == expected
    with pytest.raises(FrozenInstanceError):
        policy.first_run_setup_enabled = not policy.first_run_setup_enabled


def test_unconfigured_installations_default_to_selfhost(monkeypatch):
    monkeypatch.delenv("HV_DEPLOYMENT_MODE", raising=False)
    assert Settings(_env_file=None).deployment_mode == "selfhost"


@pytest.mark.parametrize("mode", ["hosted", "selfhost"])
def test_mode_is_read_from_the_environment(monkeypatch, mode):
    monkeypatch.setenv("HV_DEPLOYMENT_MODE", mode)
    assert Settings(_env_file=None).deployment_mode == mode


@pytest.mark.parametrize("mode", ["", "HOSTED", "self-host", "production", " hosted "])
def test_invalid_mode_is_refused_with_the_allowed_values(monkeypatch, mode):
    monkeypatch.setenv("HV_DEPLOYMENT_MODE", mode)
    with pytest.raises(ValidationError, match="'selfhost' or 'hosted'") as error:
        Settings(_env_file=None)
    assert error.value.errors()[0]["loc"] == ("deployment_mode",)


def test_architecture_documents_the_complete_policy_table():
    from dataclasses import fields
    from app.core.config import DeploymentMode
    from app.core.deployment_policy import DeploymentPolicy, RateLimitRules

    policies = [DeploymentPolicy.for_mode(mode) for mode in DeploymentMode]

    def display(value):
        return str(value).lower() if isinstance(value, bool) else str(value)

    expected = [
        "| Policy field | selfhost | hosted |",
        "| --- | --- | --- |",
        *(
            "| " + " | ".join([
                f"`{field.name}`",
                *(f"`{display(getattr(policy, field.name))}`" for policy in policies),
            ]) + " |"
            for field in fields(DeploymentPolicy)
        ),
        *(
            "| " + " | ".join([
                f"`rate_limit_rules.{field.name}`",
                *(f"`{display(getattr(policy.rate_limit_rules, field.name))}`" for policy in policies),
            ]) + " |"
            for field in fields(RateLimitRules)
        ),
    ]
    architecture = (Path(__file__).resolve().parents[2] / "docs/architecture.md").read_text()
    section = architecture.split("## Deployment modes\n", 1)[1].split("\n## ", 1)[0]
    actual = [line for line in section.splitlines() if line.startswith("|")]
    assert actual == expected


@pytest.mark.parametrize("mode,expected", [
    ("selfhost", (20, 10, 15, 15, 1, 15, 15, 100, 1000)),
    ("hosted", (10, 5, 15, 30, 2, 1440, 1440, 100, 1000)),
])
def test_rate_limit_rules_match_the_selected_profile(mode, expected):
    from dataclasses import astuple
    from app.core.deployment_policy import DeploymentPolicy

    rules = DeploymentPolicy.for_mode(Settings(_env_file=None, deployment_mode=mode).deployment_mode).rate_limit_rules
    assert astuple(rules) == expected
    with pytest.raises(FrozenInstanceError):
        rules.ip_failures = 1


@pytest.mark.parametrize('mode', ['selfhost', 'hosted'])
async def test_public_metadata_returns_only_mode_version_and_mail_capability_without_caching(client_unauth, monkeypatch, mode):
    from app.core.config import settings
    from app.main import app
    monkeypatch.setattr(settings, 'deployment_mode', mode)
    monkeypatch.setattr(settings, 'smtp_host', None)
    response = await client_unauth.get('/api/meta')
    assert response.status_code == 200
    assert response.json() == {'deployment_mode': mode, 'version': app.version, 'mail_available': False}
    assert response.headers['cache-control'] == 'no-store'
    assert 'set-cookie' not in response.headers


@pytest.mark.parametrize('host,available', [('signup.example.test', True), (None, False),
    ('user:password@example.test', False), ('http://example.test', False)])
async def test_meta_reports_only_usable_mail_configuration(client_unauth, signup_mail, monkeypatch, host, available):
    from app.core.config import settings
    monkeypatch.setattr(settings, 'public_host', host)
    response = await client_unauth.get('/api/meta')
    assert response.json()['mail_available'] is available
    assert set(response.json()) == {'deployment_mode', 'version', 'mail_available'}


from tests.signup_support import signup_mail  # noqa: E402,F401
