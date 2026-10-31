"""Per-scenario state and a synchronous facade over the async test clients.

pytest-bdd runs steps synchronously and does not await ``async def`` steps, but
the shared fixtures in ``tests/conftest.py`` (the authenticated httpx clients,
the engine injected into the app) are async and bound to the event loop that
pytest-asyncio created for the current test. ``World`` receives that very loop
(see ``bdd_loop`` in ``conftest.py``) and drives every request on it with
``run_until_complete``. Steps therefore stay plain functions and still go
through the real FastAPI app, RLS and sessions.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx


def target_url() -> str | None:
    """An explicitly selected disposable installation, otherwise the local app."""
    return os.environ.get("VERWALINO_TARGET_URL", "").rstrip("/") or None


def admin_credentials() -> tuple[str, str]:
    if not target_url():
        return "admin@alpha.de", "testpass1234"
    email = os.environ.get("VERWALINO_TARGET_ADMIN_EMAIL")
    password = os.environ.get("VERWALINO_TARGET_ADMIN_PASSWORD")
    if not email or not password:
        raise ValueError("External scenarios require VERWALINO_TARGET_ADMIN_EMAIL and VERWALINO_TARGET_ADMIN_PASSWORD")
    return email, password


class World:
    """What one scenario knows: who is acting, the last answer, and what exists."""

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        admin_clients: list[httpx.AsyncClient],
        anonymous: httpx.AsyncClient,
        *,
        primary_organization_alias: str | None = None,
    ) -> None:
        self._loop = loop
        self._admin_clients = admin_clients
        self._organizations: dict[str, httpx.AsyncClient] | None = None
        self._primary_organization_alias = primary_organization_alias
        # An unauthenticated client; used to try a sign-in.
        self.anonymous = anonymous
        # The signed-in user the next When step acts as.
        self.actor: httpx.AsyncClient | None = None
        self.actor_organization: str | None = None
        # The most recent HTTP answer and a readable description of the call.
        self.response: httpx.Response | None = None
        self.call = "(no request was made yet)"
        # Records created by Given steps: (organization name, kind) -> Record.
        self.records: dict[tuple[str, str], Any] = {}
        # Scratch space for feature-specific steps (statement bytes, passwords ...).
        self.data: dict[str, Any] = {}

    # -- running async clients -------------------------------------------------
    def run(self, coroutine):
        return self._loop.run_until_complete(coroutine)

    def send(self, client: httpx.AsyncClient, method: str, url: str, **kwargs) -> httpx.Response:
        """Send one request on the scenario's event loop and remember the answer."""
        self.call = f"{method} {url}"
        self.response = self.run(client.request(method, url, **kwargs))
        return self.response

    def act(self, method: str, url: str, **kwargs) -> httpx.Response:
        """Send a request as the signed-in user."""
        assert self.actor is not None, "No user is signed in; add a 'Given ... is signed in' step."
        return self.send(self.actor, method, url, **kwargs)

    def look(self, client: httpx.AsyncClient, url: str) -> httpx.Response:
        """GET something to check the outcome, without replacing the remembered answer."""
        return self.run(client.get(url))

    # -- organizations ----------------------------------------------------------
    @property
    def organizations(self) -> dict[str, httpx.AsyncClient]:
        """Organization name -> administrator client, resolved through the API."""
        if self._organizations is None:
            resolved = {}
            for client in self._admin_clients:
                answer = self.run(client.get("/api/organizations/me"))
                assert answer.status_code == 200, f"GET /api/organizations/me answered {answer.status_code}"
                resolved[answer.json()["name"]] = client
            if self._primary_organization_alias:
                resolved[self._primary_organization_alias] = self._admin_clients[0]
            self._organizations = resolved
        return self._organizations

    def administrator_of(self, organization: str) -> httpx.AsyncClient:
        known = self.organizations
        assert organization in known, (
            f'Unknown organization "{organization}"; the test data has: ' + ", ".join(f'"{name}"' for name in known)
        )
        return known[organization]

    # -- failure messages ---------------------------------------------------------
    def describe_response(self) -> str:
        if self.response is None:
            return f"{self.call}: no answer"
        return f"{self.call} answered {self.response.status_code}: {self.response.text[:300]}"
