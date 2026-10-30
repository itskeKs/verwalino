"""Owned local servers and an async browser facade alongside the API World.

Playwright has a session loop of its own. Each synchronous BDD step drives that
loop only while using the browser, leaving World's per-test API loop intact.
Authentication stays in memory; no session state is written to disk.
"""
from __future__ import annotations

import asyncio
from contextlib import ExitStack
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError, async_playwright, expect

ROOT = Path(__file__).resolve().parents[3]
BASE_URL = "http://127.0.0.1:13000"
VIEWPORT = {"width": 1440, "height": 1000}


def _stop(process):
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=10)


@pytest.fixture(scope="session")
def browser_app(seed_data):
    """Never attach to an existing stack; it might belong to another session."""
    from app.core.config import settings
    from app.core.declaration_test_database import require_disposable_database_settings

    require_disposable_database_settings(settings)
    for port in (13000, 18000):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))
    # The selected journeys do not need workers, SMTP, or extraction providers.
    env = {key: value for key, value in os.environ.items() if not key.startswith("HV_") and key != "USER"}
    env.update({key: os.environ[key] for key in (
        "HV_DATABASE_URL", "HV_DATABASE_URL_MIGRATOR", "HV_DATABASE_URL_SYNC",
    )})
    env.update(HV_DECLARATION_IMPORT_ENABLED="false", HV_INVOICE_EXTRACTION_ENABLED="false")
    commands = [
        ([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "18000"], ROOT / "backend", "http://127.0.0.1:18000/api/health/live"),
        (["node", "node_modules/vite/bin/vite.js", "--host", "127.0.0.1", "--port", "13000", "--strictPort"], ROOT / "frontend", BASE_URL),
    ]
    with ExitStack() as stack:
        storage = stack.enter_context(tempfile.TemporaryDirectory(prefix="verwalino-browser-"))
        env["HV_DOCUMENT_STORAGE_ROOT"] = storage
        for command, directory, url in commands:
            log = stack.enter_context(tempfile.TemporaryFile(mode="w+"))
            process = subprocess.Popen(command, cwd=directory, env=env, stdout=log, stderr=log, start_new_session=True)
            stack.callback(_stop, process)
            deadline = time.monotonic() + 30
            while process.poll() is None and time.monotonic() < deadline:
                try:
                    with urlopen(url, timeout=1) as response:
                        if response.status == 200:
                            break
                except OSError:
                    time.sleep(0.2)
            else:
                log.seek(0)
                pytest.fail(f"Browser app did not start: {url}\n{log.read()[-4000:]}")
        yield


class BrowserSession:
    def __init__(self):
        self.loop = asyncio.new_event_loop()

    def run(self, coroutine):
        return self.loop.run_until_complete(coroutine)

    async def start(self, launch_args):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(**{"channel": "chromium", **launch_args})
        context = await self.browser.new_context(base_url=BASE_URL, viewport=VIEWPORT, service_workers="block")
        try:
            page = await context.new_page()
            await page.goto("/objekte")
            await page.get_by_label("E-Mail", exact=True).fill("admin@alpha.de")
            await page.get_by_label("Passwort", exact=True).fill("testpass1234")
            await page.get_by_role("button", name="Anmelden", exact=True).click()
            await expect(page.get_by_role("link", name="Objekte", exact=True)).to_be_visible()
            self.auth = await context.storage_state()
        finally:
            await context.close()

    async def close(self):
        if hasattr(self, "browser"):
            await self.browser.close()
        if hasattr(self, "playwright"):
            await self.playwright.stop()


@pytest.fixture(scope="session")
def browser_session(browser_app, browser_type_launch_args):
    session = BrowserSession()
    try:
        session.run(session.start(browser_type_launch_args))
        yield session
    finally:
        session.run(session.close())
        session.loop.close()


class BrowserWorld:
    def __init__(self, session, world):
        self.session = session
        self.world = world
        self.errors = []

    def run(self, coroutine):
        return self.session.run(coroutine)

    async def start(self):
        self.context = await self.session.browser.new_context(
            base_url=BASE_URL, viewport=VIEWPORT, storage_state=self.session.auth,
            service_workers="block", locale="de-DE", timezone_id="Europe/Berlin",
        )
        # Only the real local app is used; never call external services.
        async def local_only(route):
            from urllib.parse import urlsplit
            url = urlsplit(route.request.url)
            # Chromium's bundled PDF viewer loads internal assets, not network resources.
            internal_pdf_asset = (url.scheme, url.netloc) in {
                ("chrome", "resources"),
                ("chrome-extension", "mhjfbmdgcfjbbpaeojofohoefgiehjai"),
            }
            if (url.scheme == "http" and url.netloc == "127.0.0.1:13000") or internal_pdf_asset:
                await route.continue_()
            else:
                self.errors.append(f"External request: {route.request.url}")
                await route.abort()
        await self.context.route("**/*", local_only)
        self.page = await self.context.new_page()
        self.page.set_default_timeout(10000)
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))

    async def visit(self, path):
        await self.page.goto(path)

    async def select(self, label, value, scope=None):
        field = (scope or self.page).get_by_role("combobox", name=label, exact=True)
        try:
            await field.locator(f'option[value="{value}"]').wait_for(state="attached")
        except PlaywrightTimeoutError as error:
            error.add_note(f"Options for {label}: {await field.locator('option').all_text_contents()}\nAlerts: {await self.page.get_by_role('alert').all_text_contents()}")
            raise
        await field.select_option(value)

    async def scope(self, scope=None, scope_id=None):
        target = scope or self.page
        await target.get_by_label("Abrechnungskreis suchen", exact=True).fill(self.world.data["scope"]["label"])
        await self.select("Abrechnungskreis", scope_id or self.world.data["scope"]["id"], target)

    async def row(self, label, value):
        row = self.page.get_by_role("row").filter(has=self.page.get_by_role("cell", name=label, exact=True))
        await expect(row).to_contain_text(value)


@pytest.fixture
def browser_world(world, browser_session):
    browser = BrowserWorld(browser_session, world)
    browser.run(browser.start())
    try:
        yield browser
        assert not browser.errors, browser.errors
    finally:
        browser.run(browser.context.close())
