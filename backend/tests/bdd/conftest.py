"""Fixtures and step library for the executable specifications in ``features/``.

Layout: one ``test_<feature>.py`` module per ``features/<feature>.feature`` (it only
binds the file), the steps live in ``steps/`` and are registered for every module
below by the imports at the end of this file. The database, the organizations and
the authenticated clients come from ``tests/conftest.py``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import pytest_asyncio

from tests.bdd.world import World
from tests.bdd.browser import browser_app, browser_session, browser_world  # noqa: F401

# Reused as-is: a throw-away user that may change its password without
# touching the shared seed users (see tests/test_auth_password.py).
from tests.test_auth_password import password_identity  # noqa: F401
from tests.setup_support import fresh_setup  # noqa: F401


@pytest_asyncio.fixture
async def bdd_loop():
    """The event loop pytest-asyncio runs this test's async fixtures on."""
    return asyncio.get_running_loop()


@pytest.fixture
def world(bdd_loop, client_a_admin, client_b_admin, client_unauth, request) -> World:
    state = World(bdd_loop, [client_a_admin, client_b_admin], client_unauth)
    state.data["scenario"] = request.node.name
    return state


def pytest_addoption(parser):
    parser.addoption("--run-browser", action="store_true", help="Run the opt-in @browser features")


def pytest_collection_modifyitems(config, items):
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
from tests.bdd.steps.inbox import *  # noqa: E402,F401,F403
from tests.bdd.steps.setup import *  # noqa: E402,F401,F403
from tests.bdd.steps.browser_setup import *  # noqa: E402,F401,F403
from tests.bdd.steps.browser_bank import *  # noqa: E402,F401,F403
from tests.bdd.steps.browser_rent import *  # noqa: E402,F401,F403
from tests.bdd.steps.browser_dunning import *  # noqa: E402,F401,F403
