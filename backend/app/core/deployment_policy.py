"""The single table of deployment decisions with implemented readers."""

from dataclasses import dataclass
from typing import Literal

from app.core.config import DeploymentMode, settings


@dataclass(frozen=True)
class RateLimitRules:
    ip_failures: int
    email_failures: int
    window_minutes: int
    lockout_minutes: int
    lockout_multiplier: int
    max_lockout_minutes: int
    lockout_decay_minutes: int
    cleanup_every: int
    cleanup_batch_size: int


_RATE_LIMIT_RULES = {
    "relaxed": RateLimitRules(20, 10, 15, 15, 1, 15, 15, 100, 1000),
    "strict": RateLimitRules(10, 5, 15, 30, 2, 1440, 1440, 100, 1000),
}


@dataclass(frozen=True)
class DeploymentPolicy:
    # Scenario selection and reports use the application's validated mode.
    deployment_mode: DeploymentMode
    # Browser setup is available only to the operator of an empty self-hosted DB.
    first_run_setup_enabled: bool
    # Public signup exists only in hosted mode.
    self_service_signup_enabled: bool
    # Login consumes the mode-specific throttling rules.
    rate_limit_profile: Literal["relaxed", "strict"]
    # Selects the shared hosted free plan or unlimited self-hosted usage.
    quota_enforcement: bool

    @property
    def rate_limit_rules(self) -> RateLimitRules:
        return _RATE_LIMIT_RULES[self.rate_limit_profile]

    @classmethod
    def for_mode(cls, mode: DeploymentMode) -> "DeploymentPolicy":
        return _POLICIES[mode]


_POLICIES = {
    DeploymentMode.SELFHOST: DeploymentPolicy(
        deployment_mode=DeploymentMode.SELFHOST,
        first_run_setup_enabled=True,
        self_service_signup_enabled=False,
        rate_limit_profile="relaxed",
        quota_enforcement=False,
    ),
    DeploymentMode.HOSTED: DeploymentPolicy(
        deployment_mode=DeploymentMode.HOSTED,
        first_run_setup_enabled=False,
        self_service_signup_enabled=True,
        rate_limit_profile="strict",
        quota_enforcement=True,
    ),
}


def get_deployment_policy() -> DeploymentPolicy:
    return DeploymentPolicy.for_mode(settings.deployment_mode)
