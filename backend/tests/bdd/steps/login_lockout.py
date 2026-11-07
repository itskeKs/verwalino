"""Use the local deterministic clock or the external target's real deadline."""
import asyncio
from datetime import datetime, timedelta, timezone
from time import monotonic
from pytest_bdd import when
from app.core.deployment_policy import get_deployment_policy
from tests.bdd.steps.password import _sign_in, WRONG_PASSWORD
from tests.bdd.world import target_url

# Start after the lock-triggering response; allow two seconds at the expiry boundary.
EXPIRY_TOLERANCE_SECONDS = 2


@when("the user enters too many wrong passwords")
def too_many_wrong_passwords(world, monkeypatch):
    from app.core import rate_limit

    policy = get_deployment_policy()
    if target_url():
        metadata = world.look(world.anonymous, "/api/meta")
        assert metadata.status_code == 200, "Cannot verify the target deployment mode"
        assert metadata.json()["deployment_mode"] == policy.deployment_mode, "Target deployment mode differs from the scenario policy"
    else:
        world.data["login_clock"] = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
        monkeypatch.setattr(rate_limit, "utcnow", lambda: world.data["login_clock"])
    for _ in range(policy.rate_limit_rules.email_failures):
        assert _sign_in(world, WRONG_PASSWORD).status_code == 401
    if target_url():
        world.data["login_deadline"] = monotonic() + policy.rate_limit_rules.lockout_minutes * 60 + EXPIRY_TOLERANCE_SECONDS


@when("the login waiting period has elapsed")
def lockout_has_elapsed(world):
    if target_url():
        # No password polling: a refusal could alter hosted backoff. Short async
        # sleeps keep the scenario event loop and cancellation responsive.
        async def wait():
            while (remaining := world.data["login_deadline"] - monotonic()) > 0:
                await asyncio.sleep(min(remaining, 60))
        world.run(wait())
    else:
        world.data["login_clock"] += timedelta(minutes=get_deployment_policy().rate_limit_rules.lockout_minutes)
