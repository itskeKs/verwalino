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
from tests.test_auth_password import password_identity as local_password_identity  # noqa: F401


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


@pytest.fixture
def world(bdd_loop, request):
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
    if target_url():
        for item in items:
            template = getattr(item.obj, "__scenario__", None)
            if template:
                reason = {
                    "organization_isolation.feature": "External isolation requires two organizations; only the bootstrap organization is configured",
                    "first_run_setup.feature": "Setup requires a fresh installation; the external target already has a bootstrap organization",
                }.get(Path(template.feature.filename).name)
                if reason:
                    item.add_marker(pytest.mark.skip(reason=reason))
    if not config.getoption("--run-browser", default=False):
        for item in items:
            if item.get_closest_marker("browser"):
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
from tests.bdd.steps.setup import *  # noqa: E402,F401,F403
from tests.bdd.steps.browser_setup import *  # noqa: E402,F401,F403
from tests.bdd.steps.browser_bank import *  # noqa: E402,F401,F403
from tests.bdd.steps.browser_rent import *  # noqa: E402,F401,F403
from tests.bdd.steps.browser_dunning import *  # noqa: E402,F401,F403
from tests.bdd.steps.source_access import *  # noqa: E402,F401,F403
