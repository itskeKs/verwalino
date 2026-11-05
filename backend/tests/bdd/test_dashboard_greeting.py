"""The dashboard separates its greeting from organization context."""
from pathlib import Path
import re
import os
from uuid import uuid4

import pytest_asyncio
from playwright.async_api import expect
from pytest_bdd import given, parsers, scenarios, then, when

from app.core.security import hash_password
from app.models.membership import Membership
from app.models.organization import Organization
from app.models.user import User
from tests.conftest import _api_test_client
from tests.bdd.steps.browser_common import browser_step
from tests.bdd.report import artifact_directory, attach_artifact

ROOT = Path(__file__).resolve().parents[3]
scenarios(str(ROOT / "features/journeys/dashboard_greeting.feature"))


@pytest_asyncio.fixture
async def dashboard_identity(migrator_session):
    organization = Organization(id=uuid4(), name="Sabine Muster Hausverwaltung")
    user = User(id=uuid4(), organization_id=organization.id,
                email=f"dashboard-{uuid4()}@example.test", display_name="Sabine Muster",
                password_hash=hash_password("testpass1234"))
    migrator_session.add(organization)
    await migrator_session.flush()
    migrator_session.add(user)
    await migrator_session.flush()
    migrator_session.add(Membership(organization_id=organization.id, user_id=user.id, role="OWNER_ADMIN"))
    await migrator_session.commit()
    async with _api_test_client() as client:
        response = await client.post("/api/auth/login", json={"email": user.email, "password": "testpass1234"})
        assert response.status_code == 200
        yield client, user, response.cookies["hv_session"]


@given(parsers.parse('a fresh organization named "{organization}"'))
def fresh_dashboard_organization(world, dashboard_identity, organization):
    world.actor, user, session_token = dashboard_identity
    assert user.organization_id is not None and organization == "Sabine Muster Hausverwaltung"
    world.actor_organization = organization
    world.data["dashboard_email"] = user.email
    world.data["dashboard_session_token"] = session_token


@when(parsers.parse("the manager opens the dashboard at {width:d} pixels"))
@browser_step
async def open_dashboard(browser_world, width, request):
    page = browser_world.page
    await page.set_viewport_size({"width": width, "height": 900})
    await page.context.add_cookies([{"name": "hv_session", "value": browser_world.world.data["dashboard_session_token"], "url": "http://127.0.0.1:13000"}])
    await browser_world.visit("/")
    directory = artifact_directory(request.node)
    if directory:
        path = directory / f"dashboard-{width}.png"
        await page.screenshot(path=str(path), full_page=True)
        attach_artifact(request.node, path)


@then("the dashboard greets without a name and shows the organization above the heading")
@browser_step
async def check_dashboard_greeting(browser_world):
    page = browser_world.page
    heading = page.get_by_role("heading", name=re.compile(r"Guten (Morgen|Tag|Abend)"))
    await expect(heading).to_be_visible()
    await expect(heading).not_to_contain_text("Sabine")
    organization = page.get_by_text("Sabine Muster Hausverwaltung", exact=True)
    await expect(organization).to_be_visible()
    heading_handle = await heading.element_handle()
    assert await organization.evaluate("(e, h) => e.compareDocumentPosition(h) & Node.DOCUMENT_POSITION_FOLLOWING", heading_handle)
    geometry = await page.locator("main").evaluate("e => ({width:e.clientWidth, content:e.scrollWidth})")
    assert geometry["content"] <= geometry["width"] + 1, geometry
