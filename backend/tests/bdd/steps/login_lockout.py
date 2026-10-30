"""Deterministic clock setup for the public login lockout journey."""
from datetime import datetime, timedelta, timezone
from pytest_bdd import when
from app.core.deployment_policy import get_deployment_policy
from tests.bdd.steps.password import _sign_in, WRONG_PASSWORD


@when("the user enters too many wrong passwords")
def too_many_wrong_passwords(world, monkeypatch):
    from app.core import rate_limit

    world.data["login_clock"] = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(rate_limit, "utcnow", lambda: world.data["login_clock"])
    for _ in range(get_deployment_policy().rate_limit_rules.email_failures):
        assert _sign_in(world, WRONG_PASSWORD).status_code == 401


@when("the login waiting period has elapsed")
def lockout_has_elapsed(world):
    world.data["login_clock"] += timedelta(minutes=get_deployment_policy().rate_limit_rules.lockout_minutes)
