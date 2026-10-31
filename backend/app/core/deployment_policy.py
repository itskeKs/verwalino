"""The single table of deployment decisions with implemented readers."""

from dataclasses import dataclass

from app.core.config import DeploymentMode, settings


@dataclass(frozen=True)
class DeploymentPolicy:
    # Scenario selection and reports use the application's validated mode.
    deployment_mode: DeploymentMode
    # Browser setup is available only to the operator of an empty self-hosted DB.
    first_run_setup_enabled: bool

    @classmethod
    def for_mode(cls, mode: DeploymentMode) -> "DeploymentPolicy":
        return _POLICIES[mode]


_POLICIES = {
    DeploymentMode.SELFHOST: DeploymentPolicy(
        deployment_mode=DeploymentMode.SELFHOST,
        first_run_setup_enabled=True,
    ),
    DeploymentMode.HOSTED: DeploymentPolicy(
        deployment_mode=DeploymentMode.HOSTED,
        first_run_setup_enabled=False,
    ),
}


def get_deployment_policy() -> DeploymentPolicy:
    return DeploymentPolicy.for_mode(settings.deployment_mode)
