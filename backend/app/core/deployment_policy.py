"""The single table of deployment decisions, including explicitly deferred readers."""

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


_RATE_LIMIT_RULES = {
    "relaxed": RateLimitRules(20, 10, 15, 15, 1, 15),
    "strict": RateLimitRules(10, 5, 15, 30, 2, 1440),
}


@dataclass(frozen=True)
class DeploymentPolicy:
    # Public identity for /api/meta; never infer the mode from individual flags.
    deployment_mode: DeploymentMode
    # Browser setup is available only to the operator of an empty self-hosted DB.
    first_run_setup_enabled: bool
    # Public signup exists only in hosted mode.
    self_service_signup_enabled: bool
    # consumed by #16; error tracking is not present at this revision.
    error_tracking_default_environment: str
    # Operators may bootstrap an organization via the migrator CLI in either mode.
    admin_cli_bootstrap_allowed: bool
    # consumed by #11 (doctor organization-count warning); isolation always applies.
    multi_organization_expected: bool
    # Login and signup share the selected rules, with separate scopes.
    rate_limit_profile: Literal["relaxed", "strict"]
    # consumed by #22; this does not enforce quotas yet.
    quota_enforcement: bool
    # consumed by #17; use the issue tracker until a hosted support channel exists.
    support_url_default: str
    # consumed by #24/#26; legal pages are not part of this switch.
    show_hosted_legal_links: bool

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
        error_tracking_default_environment="selfhost",
        admin_cli_bootstrap_allowed=True,
        multi_organization_expected=False,
        rate_limit_profile="relaxed",
        quota_enforcement=False,
        support_url_default="https://github.com/itskeKs/verwalino/issues",
        show_hosted_legal_links=False,
    ),
    DeploymentMode.HOSTED: DeploymentPolicy(
        deployment_mode=DeploymentMode.HOSTED,
        first_run_setup_enabled=False,
        self_service_signup_enabled=True,
        error_tracking_default_environment="hosted",
        admin_cli_bootstrap_allowed=True,
        multi_organization_expected=True,
        rate_limit_profile="strict",
        quota_enforcement=True,
        support_url_default="https://github.com/itskeKs/verwalino/issues",
        show_hosted_legal_links=True,
    ),
}


def get_deployment_policy() -> DeploymentPolicy:
    return DeploymentPolicy.for_mode(settings.deployment_mode)
