"""Deployment decisions remain explicit and reviewable in both modes."""

from dataclasses import FrozenInstanceError, asdict

import pytest
from pydantic import ValidationError

from app.core.config import Settings


@pytest.mark.parametrize("mode, expected", [
    ("selfhost", {
        "deployment_mode": "selfhost",
        "first_run_setup_enabled": True,
        "self_service_signup_enabled": False,
        "error_tracking_default_environment": "selfhost",
        "admin_cli_bootstrap_allowed": True,
        "multi_organization_expected": False,
        "rate_limit_profile": "relaxed",
        "quota_enforcement": False,
        "support_url_default": "https://github.com/itskeKs/verwalino/issues",
        "show_hosted_legal_links": False,
    }),
    ("hosted", {
        "deployment_mode": "hosted",
        "first_run_setup_enabled": False,
        "self_service_signup_enabled": False,
        "error_tracking_default_environment": "hosted",
        "admin_cli_bootstrap_allowed": True,
        "multi_organization_expected": True,
        "rate_limit_profile": "strict",
        "quota_enforcement": True,
        "support_url_default": "https://github.com/itskeKs/verwalino/issues",
        "show_hosted_legal_links": True,
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
