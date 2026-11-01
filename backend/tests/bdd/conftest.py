"""Fixtures and step library for the executable specifications in ``features/``.

Layout: one ``test_<feature>.py`` module per ``features/<feature>.feature`` (it only
binds the file), the steps live in ``steps/`` and are registered for every module
below by the imports at the end of this file. The database, the organizations and
the authenticated clients come from ``tests/conftest.py``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio

from tests.bdd.world import World, admin_credentials, target_url
from tests.bdd.browser import browser_app, browser_session, browser_world  # noqa: F401

# Reused as-is: a throw-away user that may change its password without
# touching the shared seed users (see tests/test_auth_password.py).
from tests.setup_support import fresh_setup  # noqa: F401
from app.core.deployment_policy import get_deployment_policy
from tests.test_auth_password import password_identity as local_password_identity  # noqa: F401

_SHARED_HOSTED_USAGE = pytest.StashKey[dict | None]()


@pytest.fixture(scope="session", autouse=True)
def run_migrations(request):
    if not target_url():
        return request.getfixturevalue("run_migrations")


@pytest.fixture(autouse=True)
def inject_test_engine(request):
    if not target_url():
        return request.getfixturevalue("inject_test_engine")


@pytest.fixture(scope="session")
def seed_data(request):
    if not target_url():
        return request.getfixturevalue("seed_data")
    email, password = admin_credentials()
    with httpx.Client(base_url=target_url(), timeout=60) as client:
        response = client.post("/api/auth/login", json={"email": email, "password": password})
        assert response.status_code == 200, f"External admin login answered {response.status_code}"
        identity = response.json()
    return {"user_a_admin_id": identity["user_id"], "org_a_id": identity["organization_id"]}


@pytest_asyncio.fixture
async def bdd_loop():
    """The event loop pytest-asyncio runs this test's async fixtures on."""
    return asyncio.get_running_loop()


@pytest.fixture(scope="session")
def shared_hosted_property_headroom(seed_data, request):
    """Diagnose shared fixture growth before a later scenario hits the free cap."""
    if target_url() or not get_deployment_policy().quota_enforcement:
        yield None
        return

    from app.core.quotas import FREE_PLAN

    usage = {"seed_data": seed_data, "peaks": {}}
    yield usage
    # Keep at least two slots free throughout the shared scenarios.
    minimum_headroom = 2
    problems = []
    for name, count in shared_property_counts(seed_data).items():
        peak, scenario = usage["peaks"].get(name, (count, "session teardown"))
        message = (f"Shared hosted BDD properties: {name}: peak {peak}/{FREE_PLAN.properties}, "
                   f"end {count}/{FREE_PLAN.properties}; peak scenario: {scenario}; "
                   f"requires at least {minimum_headroom} free slots.")
        request.config.pluginmanager.get_plugin("terminalreporter").write_line(message)
        if FREE_PLAN.properties - max(peak, count) < minimum_headroom:
            problems.append(message)
    assert not problems, "\n".join(problems) + (
        "\nReuse fixture records or isolate property-heavy scenarios in their own organization."
    )


def shared_property_counts(seed_data):
    import psycopg2
    from tests.conftest import MIGRATOR_SYNC_URL

    counts = {}
    with psycopg2.connect(MIGRATOR_SYNC_URL, options="-c statement_timeout=5000") as connection, connection.cursor() as cursor:
        for name, org_id in (("Verwaltung Alpha", seed_data["org_a_id"]),
                             ("Verwaltung Beta", seed_data["org_b_id"])):
            cursor.execute("SELECT count(*) FROM property WHERE organization_id = %s", (str(org_id),))
            counts[name] = cursor.fetchone()[0]
    return counts


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    yield
    usage = item.stash.get(_SHARED_HOSTED_USAGE, None)
    if usage is not None and "fresh_setup" not in item.funcargs["request"].fixturenames:
        # Sample before function-fixture cleanup can truncate retained evidence.
        # Fresh-installation scenarios hold a rollback-only TRUNCATE and do not
        # use the shared records; another connection would wait for teardown.
        for name, count in shared_property_counts(usage["seed_data"]).items():
            if count > usage["peaks"].get(name, (-1, ""))[0]:
                usage["peaks"][name] = count, item.name


@pytest.fixture
def world(bdd_loop, request, shared_hosted_property_headroom):
    request.node.stash[_SHARED_HOSTED_USAGE] = shared_hosted_property_headroom
    if target_url():
        admin = httpx.AsyncClient(base_url=target_url(), timeout=60)
        anonymous = httpx.AsyncClient(base_url=target_url(), timeout=60)
        state = World(bdd_loop, [admin], anonymous, primary_organization_alias="Verwaltung Alpha")
    else:
        admin = request.getfixturevalue("client_a_admin")
        anonymous = request.getfixturevalue("client_unauth")
        state = World(bdd_loop, [admin, request.getfixturevalue("client_b_admin")], anonymous)
    state.data["scenario"] = request.node.name
    try:
        if target_url():
            email, password = admin_credentials()
            response = state.send(admin, "POST", "/api/auth/login", json={"email": email, "password": password})
            assert response.status_code == 200, f"External admin login answered {response.status_code}"
        yield state
    finally:
        if target_url():
            state.run(admin.aclose())
            state.run(anonymous.aclose())


@pytest.fixture
def password_identity(request, world):
    if not target_url():
        yield request.getfixturevalue("local_password_identity")
        return
    from tests.bdd.steps.password import CURRENT_PASSWORD
    email = f"scenario-password-{uuid4()}@example.test"
    response = world.send(world.administrator_of("Verwaltung Alpha"), "POST", "/api/users", json={
        "email": email, "password": CURRENT_PASSWORD, "display_name": "Scenario password user", "role": "CLERK",
    })
    assert response.status_code == 201, world.describe_response()
    client = httpx.AsyncClient(base_url=target_url(), timeout=60)
    try:
        response = world.send(client, "POST", "/api/auth/login", json={"email": email, "password": CURRENT_PASSWORD})
        assert response.status_code == 200, world.describe_response()
        yield client, email
    finally:
        world.run(client.aclose())


def pytest_addoption(parser):
    parser.addoption("--run-browser", action="store_true", help="Run the opt-in @browser features")


def pytest_collection_modifyitems(config, items):
    """Keep fixtures that control the local app out of HTTP-only target runs.

    Organization archives use migrator fixtures, ASGI clients and local storage;
    deletion also executes the operator CLI's service in-process. Its browser
    fixture patches hosted signup and SMTP locally and cleans up through the DB.
    Password recovery reads Mailpit and patches the local app's public host.
    These features remain active against the local stack and in PR CI.
    """
    mode = get_deployment_policy().deployment_mode
    for item in items:
        for required_mode in ("selfhost", "hosted"):
            if item.get_closest_marker(required_mode) and mode != required_mode:
                item.add_marker(pytest.mark.skip(reason=f"Requires {required_mode} deployment; running in {mode}"))
        if target_url():
            template = getattr(item.obj, "__scenario__", None)
            if template:
                reason = {
                    "organization_isolation.feature": "External isolation requires two organizations; only the bootstrap organization is configured",
                    "first_run_setup.feature": "Setup requires a fresh installation; the external target already has a bootstrap organization",
                    "inbox_review_chain.feature": "needs the in-process invoice worker and direct database fixtures",
                    "organization_deletion.feature": "needs direct database fixtures and the in-process deletion CLI",
                    "organization_export_import.feature": "needs direct database fixtures and in-process archive storage",
                    "password_reset.feature": "needs the Mailpit test mailbox",
                }.get(Path(template.feature.filename).name)
                if reason:
                    item.add_marker(pytest.mark.skip(reason=reason))
        if item.get_closest_marker("browser") and not config.getoption("--run-browser", default=False):
            item.add_marker(pytest.mark.skip(reason="Use make test-browser to run browser journeys"))


def pytest_bdd_step_error(feature, scenario, step, exception, **_):
    """Name the scenario and the step in the failure, so a CI log or a nightly
    issue points at the sentence in the .feature file, not at a Python frame."""
    exception.add_note(
        f"Scenario: {scenario.name}\n"
        f"Step: {step.keyword} {step.name}  (features/{Path(feature.filename).name}:{step.line_number})"
    )


# Step library. Pattern names must be unique across these modules because every
# feature sees all of them.
from tests.bdd.steps.common import *  # noqa: E402,F401,F403
from tests.bdd.steps.organization import *  # noqa: E402,F401,F403
from tests.bdd.steps.password import *  # noqa: E402,F401,F403
from tests.bdd.steps.bank_import import *  # noqa: E402,F401,F403
from tests.bdd.steps.reports import *  # noqa: E402,F401,F403
from tests.bdd.steps.inbox import *  # noqa: E402,F401,F403
from tests.bdd.steps.setup import *  # noqa: E402,F401,F403
from tests.bdd.steps.lease_documents import *  # noqa: E402,F401,F403
from tests.bdd.steps.diagnostics import *  # noqa: E402,F401,F403
from tests.bdd.steps.browser_setup import *  # noqa: E402,F401,F403
from tests.bdd.steps.browser_bank import *  # noqa: E402,F401,F403
from tests.bdd.steps.browser_rent import *  # noqa: E402,F401,F403
from tests.bdd.steps.browser_dunning import *  # noqa: E402,F401,F403
from tests.bdd.steps.settlement_period import *  # noqa: E402,F401,F403
from tests.bdd.steps.login_lockout import *  # noqa: E402,F401,F403
from tests.bdd.mailpit import mailpit  # noqa: E402,F401
from tests.bdd.steps.signup import signup_isolation  # noqa: E402,F401
from tests.bdd.steps.signup import *  # noqa: E402,F401,F403
from tests.bdd.steps.password_reset import *  # noqa: E402,F401,F403
from tests.bdd.steps.quotas import *  # noqa: E402,F401,F403
from tests.bdd.steps.organization_transfer import *  # noqa: E402,F401,F403
from tests.bdd.steps.source_access import *  # noqa: E402,F401,F403
